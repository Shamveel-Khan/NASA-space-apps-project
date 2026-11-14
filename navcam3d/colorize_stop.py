"""Project real, same-station Mastcam RGB onto an existing Navcam mesh.

Keeps original scene geometry and source classification unchanged. The separate
variant uses grayscale for uncovered vertices. No depth inference or training.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
from html import escape
import json
from pathlib import Path
import shutil
import urllib.request
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image
from scipy.ndimage import map_coordinates

import audit_color_coverage as audit
import pds

ROOT = Path(__file__).resolve().parent.parent


def decode_scene(folder):
    meta = json.loads((folder/'scene.json').read_text())
    blob = base64.b64decode((folder/'scene.b64.txt').read_bytes())
    n = meta['counts']['vertices']
    vertices = np.frombuffer(blob, dtype='<i2', count=n*3).reshape(n, 3).astype(float)/100
    gray = np.frombuffer(blob, dtype='u1', count=n, offset=n*6).copy()
    return meta, vertices, gray, blob


def camera_from_xml(text):
    root = ET.fromstring(text)
    model = next(iter(audit.elements(root, 'Camera_Model_Parameters')))
    def vector(name, keys):
        return np.array(audit.vector(next(iter(audit.elements(model, name))), keys), dtype=float)
    C = vector('Vector_Center', ('x_position', 'y_position', 'z_position'))
    A = vector('Vector_Axis', ('x_unit', 'y_unit', 'z_unit'))
    H = vector('Vector_Horizontal', ('x_pixel', 'y_pixel', 'z_pixel'))
    V = vector('Vector_Vertical', ('x_pixel', 'y_pixel', 'z_pixel'))
    definition = next(e for e in audit.elements(root, 'Coordinate_Space_Definition')
                      if audit.first(e, 'local_identifier', '').startswith('ROVER_NAV_FRAME'))
    offset = np.array(audit.vector(next(iter(audit.elements(definition, 'Vector_Origin_Offset'))),
                                  ('x_position', 'y_position', 'z_position')))
    q = np.array(audit.vector(next(iter(audit.elements(definition, 'Quaternion_Plus_Direction'))),
                             ('qcos', 'qsin1', 'qsin2', 'qsin3')))
    R = pds.quat_to_matrix(q/np.linalg.norm(q))
    return root, C, A, H, V, R, offset


def project(vertices_site, camera, width, height):
    _, C, A, H, V, R, offset = camera
    P = (vertices_site-offset)@R-C
    depth = P@A
    u, v = P@H/np.maximum(depth, 1e-9), P@V/np.maximum(depth, 1e-9)
    valid = (depth > .1) & (u >= 2) & (v >= 2) & (u < width-3) & (v < height-3)
    return u, v, depth, valid


def visible_on_heightfield(vertices, camera_position, heightfield, radius, cell):
    """Conservative sampled line-of-sight screen against the existing terrain."""
    visible = np.ones(len(vertices), bool)
    for fraction in np.linspace(.02, .97, 64):
        ray = camera_position+(vertices-camera_position)*fraction
        coords = [(ray[:, 2]+radius)/cell-.5, (ray[:, 0]+radius)/cell-.5]
        terrain = map_coordinates(heightfield, coords, order=1, mode='constant', cval=np.nan)
        visible &= ~np.isfinite(terrain) | (terrain < ray[:, 1]+.15)
    return visible


def read_rgb(path, root):
    array = next(iter(audit.elements(root, 'Array_3D_Image')))
    axes = {audit.first(e, 'axis_name'): (int(audit.first(e, 'sequence_number')), int(audit.first(e, 'elements')))
            for e in audit.elements(array, 'Axis_Array')}
    order = sorted(axes, key=lambda key: axes[key][0])
    dtype = {'SignedMSB2': '>i2', 'UnsignedMSB2': '>u2', 'UnsignedByte': 'u1'}[audit.first(array, 'data_type')]
    shape = [axes[k][1] for k in order]
    raw = np.fromfile(path, dtype=dtype, offset=int(audit.first(array, 'offset')),
                      count=int(np.prod(shape))).reshape(shape)
    rgb = np.transpose(raw, [order.index(k) for k in ('Line', 'Sample', 'Band')]).astype(float)
    bad = (rgb < 0).any(-1) | (rgb >= 32767).any(-1)
    rgb[bad] = np.nan
    return rgb


def download_image(record, destination, offline=False):
    path = destination/Path(record['image_url']).name
    if path.exists() and path.stat().st_size == record['expected_bytes']: return path
    if offline: raise RuntimeError(f'Image not cached: {path}')
    last = None
    for _ in range(3):
        try:
            request = urllib.request.Request(record['image_url'], headers={'User-Agent': 'marswalk-color-projection/1.0'})
            part = path.with_suffix('.part')
            have = part.stat().st_size if part.exists() else 0
            if have: request.add_header('Range', f'bytes={have}-')
            with urllib.request.urlopen(request, timeout=45) as response, part.open('ab' if have else 'wb') as stream:
                if have and response.status != 206: stream.seek(0); stream.truncate()
                while chunk := response.read(1024*1024): stream.write(chunk)
            if path.with_suffix('.part').stat().st_size != record['expected_bytes']:
                raise ValueError('Incomplete image download')
            path.with_suffix('.part').replace(path)
            return path
        except (OSError, ValueError) as error: last = error
    raise RuntimeError(f"Unable to download {record['image_url']}: {last}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--sol', type=int, default=1100)
    ap.add_argument('--max-images', type=int, default=20)
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--output-folder', type=Path)
    ap.add_argument('--raw-root', type=Path, default=ROOT/'data/raw')
    ap.add_argument('--no-register', action='store_true')
    ap.add_argument('--offline', action='store_true')
    ap.add_argument('--audit', type=Path, default=ROOT/'data/color_coverage/live-audit-network/coverage.json')
    args = ap.parse_args(argv)
    if args.max_images < 1 or args.workers < 1: ap.error('max-images and workers must be positive')
    source = ROOT/f'app/stops/sol{args.sol}'
    output = args.output_folder or ROOT/f'app/stops/sol{args.sol}-color'
    if output.exists(): ap.error(f'Output already exists: {output}')
    meta, vertices, gray, blob = decode_scene(source)
    raw_dir = args.raw_root/f'sol{args.sol}'
    labels = sorted(raw_dir.glob('NLB_*.LBL'))
    if not labels: raise ValueError(f'Missing camera labels for sol {args.sol}')
    origin = pds.rover_to_site(pds.read_label(labels[0]))[1]
    site = origin+np.stack([-vertices[:, 2], vertices[:, 0], -vertices[:, 1]], axis=1)
    radius, cell = meta['map']['radius'], .15
    n = int(round(2*radius/cell))
    field = np.full((n, n), np.nan)
    rows = np.clip(np.round((vertices[:, 2]+radius)/cell-.5).astype(int), 0, n-1)
    cols = np.clip(np.round((vertices[:, 0]+radius)/cell-.5).astype(int), 0, n-1)
    field[rows, cols] = vertices[:, 1]
    report = json.loads(args.audit.read_text())
    candidates = [r for r in report['matches'] if r['target_sol'] == args.sol and r['relation'] == 'same_site_drive']
    cache = audit.MetadataCache(ROOT/'data/cache/color_audit', offline=True)
    options = []
    for record in candidates:
        text = cache.get(record['label_url'])
        camera = camera_from_xml(text)
        root, C, A, H, V, R, offset = camera
        if audit.first(root, 'model_type') != 'CAHV': continue
        u, v, depth, mask = project(site, camera, record['width'], record['height'])
        if mask.sum() < 25: continue
        cam_site = offset+R@C
        cam_local = np.array([cam_site[1]-origin[1], -(cam_site[2]-origin[2]), -(cam_site[0]-origin[0])])
        ids = np.flatnonzero(mask)
        mask[ids] &= visible_on_heightfield(vertices[ids], cam_local, field, radius, cell)
        if mask.sum() < 25: continue
        file = next(iter(audit.elements(root, 'File_Area_Observational')))
        record = {**record, 'expected_bytes': int(audit.first(file, 'file_size'))}
        focal = np.linalg.norm(np.cross(H, A))
        edge = np.minimum.reduce([u/record['width'], 1-u/record['width'], v/record['height'], 1-v/record['height']])
        score = np.where(mask, focal/np.maximum(depth, .1)*np.minimum(edge*5, 1), 0)
        options.append({'record': record, 'camera': camera, 'mask': mask, 'score': score, 'u': u, 'v': v})
    selected = []
    covered = np.zeros(len(vertices), bool)
    while options and len(selected) < args.max_images:
        index = max(range(len(options)), key=lambda i: int((options[i]['mask'] & ~covered).sum()))
        choice = options.pop(index)
        gain = int((choice['mask'] & ~covered).sum())
        if gain < 25: break
        selected.append(choice); covered |= choice['mask']
        print(f"Select {choice['record']['product_id']}: +{gain} vertices ({covered.mean():.1%} potential coverage)", flush=True)
    if not selected: raise SystemExit('No projected visible RGB coverage; nothing written')
    downloads = ROOT/'data/color_coverage/texture_cache'; downloads.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        paths = list(pool.map(lambda o: download_image(o['record'], downloads, args.offline), selected))
    # One shared display transform preserves channel ratios; no synthetic color is introduced.
    samples = []
    for path, option in zip(paths, selected):
        array = read_rgb(path, option['camera'][0])
        samples.append(array.reshape(-1, 3)[::max(1, array.shape[0]*array.shape[1]//5000)].copy())
        del array
    samples = np.concatenate(samples)
    finite = samples[np.isfinite(samples).all(1)]
    if not len(finite): raise ValueError('Selected images contain no valid RGB samples')
    lo, hi = np.percentile(finite, [1, 99])
    rgb = np.repeat(gray[:, None], 3, axis=1)
    best = np.zeros(len(vertices))
    provenance = []
    for path, option in zip(paths, selected):
        array = read_rgb(path, option['camera'][0])
        ids = np.flatnonzero(option['mask'])
        pixels = array[np.round(option['v'][ids]).astype(int), np.round(option['u'][ids]).astype(int)]
        valid = np.isfinite(pixels).all(1) & (option['score'][ids] > best[ids])
        ids, pixels = ids[valid], pixels[valid]
        display = (255*np.clip((pixels-lo)/max(hi-lo, 1), 0, 1)**(1/2.2)).astype(np.uint8)
        rgb[ids] = display; best[ids] = option['score'][ids]
        provenance.append({k: option['record'][k] for k in ('product_id', 'sol', 'site', 'drive', 'image_url', 'label_url')})
    colored = best > 0
    if colored.sum() < 25: raise SystemExit('No valid color samples; nothing written')
    shutil.copytree(source, output)
    (output/'color.b64.txt').write_bytes(base64.b64encode(rgb.tobytes()))
    (output/'color-mask.b64.txt').write_bytes(base64.b64encode(colored.astype(np.uint8).tobytes()))
    focus = np.median(vertices[colored], axis=0).round(2).tolist()
    color = {'asset': 'color.b64.txt', 'mask_asset': 'color-mask.b64.txt', 'colored_vertices': int(colored.sum()),
             'total_vertices': len(vertices), 'vertex_coverage': float(colored.mean()), 'images': provenance,
             'focus': focus, 'display_transform': {'low': float(lo), 'high': float(hi), 'gamma': 1/2.2},
             'method': 'CAHV projection using same-station Mastcam and rover/site poses; sampled terrain line-of-sight check',
             'limits': 'Preview registration from telemetry, not hand-refined. Uncovered vertices keep original grayscale. Geometry and depth-source labels unchanged.',
             'geometry_sha256': hashlib.sha256(blob).hexdigest(),
             'anchor_label': labels[0].name, 'configuration': {'max_images': args.max_images}}
    meta['colorization'] = color
    meta['variant'] = 'mastcam-color'
    (output/'scene.json').write_text(json.dumps(meta, indent=1))
    # Top-down surface preview preserves the spatial relation to the mesh.
    preview = np.full((n, n, 3), 24, np.uint8); preview[rows, cols] = rgb
    Image.fromarray(preview).resize((640, 640), Image.Resampling.NEAREST).save(output/'img/step1_photo.jpg', quality=95)
    if not args.no_register:
        data_path = ROOT/'app/data.json'; data = json.loads(data_path.read_text())
        original = next(c for c in data['closeups'] if c['sol'] == args.sol)
        variant = {**original, 'id': f'{args.sol}-color', 'label': f'Sol {args.sol} · Mastcam color',
                   'variant': 'mastcam-color', 'folder': f'stops/sol{args.sol}-color',
                   'color_coverage': color['vertex_coverage'], 'color_photos': len(selected)}
        variants = [v for v in data.get('closeup_variants', []) if v.get('id') != variant['id']]
        data['closeup_variants'] = variants+[variant]
        data_path.write_text(json.dumps(data, separators=(',', ':')))
    pct = f'{colored.mean():.1%}'
    (output/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>Sol '+str(args.sol)+' · Mastcam color</title>'
        '<style>body{font:16px system-ui;background:#171917;color:#eee;max-width:900px;margin:3rem auto;padding:1rem}a{color:#e5b27c}img{max-width:100%}</style>'
        f'<h1>Sol {args.sol} · Mastcam color</h1><p>Original Navcam geometry with real Mastcam RGB. {pct} of vertices receive color from {len(selected)} images; the rest remain grayscale.</p>'
        f'<p>{escape(color["limits"])}</p><a href="../../../ground-closeups/?sol={args.sol}-color">Open color reconstruction</a> · '
        f'<a href="../sol{args.sol}/index.html">Original scientific report</a><p><img src="img/step1_photo.jpg" alt="Top-down view of the color-projected surface"></p>'
        '<h2>NASA color observations</h2><ul>'+''.join(f'<li><a href="{escape(r["label_url"])}">{escape(r["product_id"])}</a> (sol {r["sol"]})</li>' for r in provenance)+'</ul>')
    print(f'Created {output}: {pct} actual color coverage, {len(selected)} source images', flush=True)


if __name__ == '__main__': main()
