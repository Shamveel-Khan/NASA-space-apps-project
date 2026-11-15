"""Audit NASA Mastcam RGB coverage around the repository's Navcam stops.

Standard library only. Downloads directory listings and labels, not image pixels.
  python navcam3d/audit_color_coverage.py
  python navcam3d/audit_color_coverage.py --sols 1094 1098 1100 --sol-window 5 --radius-m 100

Outputs JSON, CSV, Markdown and an HTML gallery of image/label links. 'Ample' is
an adjustable count heuristic, not proof of image overlap or usable color texture.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import gzip
from datetime import datetime, timezone
import hashlib
from html import escape
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
from urllib.parse import unquote, urljoin, urlparse
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = 'https://planetarydata.jpl.nasa.gov/img/data/msl/msl_mmm/data_MSLMST/'
WAYPOINTS = 'https://mars.nasa.gov/mmgis-maps/MSL/Layers/json/MSL_waypoints.json'
DEFAULT_SOLS = [520, 521, 1037, 1083, 1094, 1098, 1100, 1400]


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = set()

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.hrefs.update(v for k, v in attrs if k == 'href' and v)


def children(text, base):
    """Only direct descendants of a listing, with no query strings or parent links."""
    parser = Links(); parser.feed(text)
    parent = urlparse(base)
    links = set()
    for href in parser.hrefs:
        url = urlparse(urljoin(base, href))
        if url.scheme != parent.scheme or url.netloc != parent.netloc or url.query or url.fragment:
            continue
        if not url.path.startswith(parent.path): continue
        tail = url.path[len(parent.path):].rstrip('/')
        if tail and '/' not in tail:
            links.add(url.geturl())
    return sorted(links)


class MetadataCache:
    def __init__(self, root, offline=False, refresh=False, timeout=30):
        self.root, self.offline, self.refresh, self.timeout = root, offline, refresh, timeout

    def get(self, url):
        path = self.root / (hashlib.sha256(url.encode()).hexdigest()+'.txt')
        if path.exists() and path.stat().st_size > 0 and (self.offline or not self.refresh):
            return path.read_text()
        if self.offline: raise RuntimeError(f'Uncached metadata in --offline mode: {url}')
        for attempt in range(3):
            try:
                request = urllib.request.Request(url, headers={'User-Agent': 'marswalk-color-audit/1.0', 'Accept-Encoding': 'gzip'})
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = response.read()
                    if response.headers.get('Content-Encoding', '').lower() == 'gzip':
                        payload = gzip.decompress(payload)
                    data = payload.decode('utf-8', errors='replace')
                    if not data.strip():
                        raise ValueError(f'NASA returned an empty metadata response: {url}')
                self.root.mkdir(parents=True, exist_ok=True)
                temp = path.with_suffix('.part'); temp.write_text(data); temp.replace(path)
                return data
            except urllib.error.HTTPError as error:
                if error.code in (401, 403, 404) or attempt == 2:
                    raise RuntimeError(f'HTTP {error.code}: {url}') from error
            except (OSError, ValueError) as error:
                if attempt == 2: raise RuntimeError(f'Metadata request failed: {url}: {error}') from error
            time.sleep(2**attempt)


def odl_entries(text):
    """Small group-aware reader for local Navcam PDS3 labels."""
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    stack, rows, pending = [], [], ''
    for line in text.splitlines():
        pending = (pending+' '+line.strip()).strip()
        if pending.count('(') > pending.count(')') or pending.count('"') % 2:
            continue
        match = re.match(r'([\w^:]+)\s*=\s*(.*)', pending)
        pending = ''
        if not match: continue
        key, value = match.groups()
        if key in ('GROUP', 'OBJECT'): stack.append(value.strip('"'))
        elif key in ('END_GROUP', 'END_OBJECT'):
            if stack: stack.pop()
        else: rows.append((tuple(stack), key, value))
    return rows


def odl_value(rows, key, group=None):
    return next((value for path, name, value in rows if name == key and (group is None or group in path)), None)


def numeric(value):
    return [float(x) for x in re.findall(r'[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?', (value or '').split('<')[0])]


def quaternion_rotate(q, vector):
    s, x, y, z = q
    norm = math.sqrt(sum(v*v for v in q)); s, x, y, z = (v/norm for v in q)
    matrix = [[1-2*(y*y+z*z), 2*(x*y-s*z), 2*(x*z+s*y)],
              [2*(x*y+s*z), 1-2*(x*x+z*z), 2*(y*z-s*x)],
              [2*(x*z-s*y), 2*(y*z+s*x), 1-2*(x*x+y*y)]]
    return [sum(a*b for a, b in zip(row, vector)) for row in matrix]


def load_targets(raw_root, sols):
    targets = {}
    for sol in sols:
        directory = raw_root/f'sol{sol}'
        anchors = {}
        # RAS/XYZ representations of a single observation must not inflate counts.
        for path in sorted(directory.glob('N*_*[RX]*LF*.LBL')):
            rows = odl_entries(path.read_text(errors='replace'))
            rmc = numeric(odl_value(rows, 'ROVER_MOTION_COUNTER'))
            if len(rmc) < 2: continue
            key = (int(rmc[0]), int(rmc[1]))
            anchor = anchors.setdefault(key, {'site': key[0], 'drive': key[1], 'navcam_frames': set(),
                                             'pointings': [], 'position': None, 'site_position': None})
            identity = re.sub(r'(RASLF|XYZLF)', 'FRAME', path.stem)
            if identity in anchor['navcam_frames']: continue
            anchor['navcam_frames'].add(identity)
            offset = numeric(odl_value(rows, 'ORIGIN_OFFSET_VECTOR', 'ROVER_COORD_SYSTEM_PARMS'))
            if len(offset) == 3: anchor['site_position'] = offset
            axis = numeric(odl_value(rows, 'MODEL_COMPONENT_2', 'GEOMETRIC_CAMERA_MODEL_PARMS'))
            quat = numeric(odl_value(rows, 'ORIGIN_ROTATION_QUATERNION', 'ROVER_COORD_SYSTEM_PARMS'))
            if len(axis) == 3 and len(quat) == 4:
                v = quaternion_rotate(quat, axis)
                anchor['pointings'].append({'azimuth': math.degrees(math.atan2(v[1], v[0])) % 360,
                                            'elevation': math.degrees(math.asin(max(-1, min(1, -v[2]))))})
        stop_path = directory/'stop.json'
        if stop_path.exists():
            stop = json.loads(stop_path.read_text())
            key = (int(stop['site']), int(stop['drive']))
            if not anchors:
                anchors[key] = {'site': key[0], 'drive': key[1], 'navcam_frames': set(), 'pointings': [], 'position': None, 'site_position': None}
            if key in anchors and 'lat' in stop and 'lon' in stop:
                anchors[key]['position'] = [float(stop['lat']), float(stop['lon'])]
        for anchor in anchors.values(): anchor['navcam_frames'] = sorted(anchor['navcam_frames'])
        targets[sol] = list(anchors.values())
    return targets


def prepare_scene_anchors(scene_root, sols, cache):
    """Fetch only exact source labels, keeping pose provenance tied to the mesh."""
    destination = cache.root/'scene_anchors'
    for sol in sols:
        meta = json.loads((scene_root/f'sol{sol}'/'scene.json').read_text())
        frame = meta['frames'][0]['id']
        if not re.fullmatch(r'N[A-Z0-9_]+', frame):
            raise ValueError(f'Invalid source frame identifier for sol {sol}')
        folder = destination/f'sol{sol}'; folder.mkdir(parents=True, exist_ok=True)
        url = ('https://planetarydata.jpl.nasa.gov/img/data/msl/MSLNAV_1XXX/' +
               ('DATA_V1/' if sol <= 1869 else 'DATA/') + f'SOL{sol:05d}/' + frame+'.LBL')
        text = cache.get(url)
        rmc = numeric(odl_value(odl_entries(text), 'ROVER_MOTION_COUNTER'))
        stop = meta['stop']
        if len(rmc) < 2 or tuple(map(int, rmc[:2])) != (int(stop['site']), int(stop['drive'])):
            raise ValueError(f'Camera label station differs from mesh station for sol {sol}')
        (folder/(frame+'.LBL')).write_text(text)
        (folder/'stop.json').write_text(json.dumps(stop))
    return destination


def waypoint_positions(text):
    rows = json.loads(text)['features']
    positions = {}
    for row in rows:
        p = row.get('properties', {})
        try:
            key = (int(p['site']), int(p['drive']))
            lat, lon = float(p['lat']), float(p['lon'])
            if math.isfinite(lat) and math.isfinite(lon): positions[key] = [lat, lon]
        except (KeyError, TypeError, ValueError): continue
    return positions


def distance_m(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 2*3389500*math.asin(min(1, math.sqrt(h)))


def angular_separation(a, b):
    az1, el1, az2, el2 = map(math.radians, (a['azimuth'], a['elevation'], b['azimuth'], b['elevation']))
    c = math.sin(el1)*math.sin(el2)+math.cos(el1)*math.cos(el2)*math.cos(az1-az2)
    return math.degrees(math.acos(max(-1, min(1, c))))


def tag(node):
    return node.tag.rsplit('}', 1)[-1]


def elements(node, name):
    return [] if node is None else [e for e in node.iter() if tag(e) == name]


def first(node, name, default=None):
    return next(((e.text or '').strip() for e in elements(node, name) if (e.text or '').strip()), default)


def indices(node):
    result = {}
    for e in elements(node, 'Motion_Counter_Index') + elements(node, 'Coordinate_Space_Index'):
        name, value = first(e, 'index_id'), first(e, 'index_value_number')
        if name and value: result[name] = int(float(value))
    return result


def vector(node, names):
    if node is None: return None
    values = [first(node, name) for name in names]
    return [float(v) for v in values] if all(v is not None for v in values) else None


def parse_mastcam(text, label_url, image_url):
    root = ET.fromstring(text)
    id_ = first(root, 'alternate_id', Path(urlparse(label_url).path).stem).upper()
    motion = next((e for e in elements(root, 'Motion_Counter') if first(e, 'name') == 'RMC'), None)
    rmc = indices(motion)
    geometry = next(iter(elements(root, 'Camera_Model_Parameters')), None)
    if not rmc: rmc = indices(geometry)
    axes = {first(e, 'axis_name'): int(first(e, 'elements', '0')) for e in elements(root, 'Axis_Array')}
    display = next(iter(elements(root, 'Color_Display_Settings')), None)
    channels = [first(display, name) for name in ('red_channel_band', 'green_channel_band', 'blue_channel_band')]
    decoded = first(root, 'color_filter_array_state', '').lower() == 'decoded'
    filter_name = first(root, 'filter_name', '')
    filter_number = first(root, 'filter_number')
    clear = filter_number == '0' or filter_name.endswith('_CLEAR')
    # Require explicit RGB metadata; a Bayer sensor or a JPEG extension alone is not proof of RGB.
    rgb = axes.get('Band') == 3 and len(set(channels)) == 3 and all(channels) and decoded and clear
    site_position, pointing = None, None
    camera_frame = first(geometry, 'coordinate_space_frame_type')
    axis = vector(next(iter(elements(geometry, 'Vector_Axis')), None), ('x_unit', 'y_unit', 'z_unit'))
    for definition in elements(root, 'Coordinate_Space_Definition'):
        present = next(iter(elements(definition, 'Coordinate_Space_Present')), None)
        reference = next(iter(elements(definition, 'Coordinate_Space_Reference')), None)
        local_id = first(definition, 'local_identifier', '')
        if local_id.startswith('ROVER_NAV_FRAME') and first(reference, 'coordinate_space_frame_type') == 'SITE_FRAME':
            if indices(present).get('SITE') != rmc.get('SITE') or indices(present).get('DRIVE') != rmc.get('DRIVE'):
                continue
            site_position = vector(next(iter(elements(definition, 'Vector_Origin_Offset')), None),
                                   ('x_position', 'y_position', 'z_position'))
            quat = vector(next(iter(elements(definition, 'Quaternion_Plus_Direction')), None),
                          ('qcos', 'qsin1', 'qsin2', 'qsin3'))
            if axis and quat and camera_frame == 'ROVER_NAV_FRAME':
                v = quaternion_rotate(quat, axis)
                pointing = {'azimuth': math.degrees(math.atan2(v[1], v[0])) % 360,
                            'elevation': math.degrees(math.asin(max(-1, min(1, -v[2]))))}
            break
    summary = next(iter(elements(root, 'Primary_Result_Summary')), None)
    description = first(summary, 'description', '')
    nonterrain = bool(re.search(r'\b(sky|sun|solar|calibration target|flat field)\b', description, re.I))
    capture = re.sub(r'[A-Z]\d{2}_DR[A-Z]{2}$', '', id_)
    return {'product_id': id_, 'capture_id': capture, 'sol': int(id_[:4]),
            'camera': 'MAST_LEFT' if id_[4:6] == 'ML' else 'MAST_RIGHT',
            'site': rmc.get('SITE'), 'drive': rmc.get('DRIVE'),
            'site_position': site_position, 'position': None, 'pointing': pointing,
            'bands': axes.get('Band', 1), 'width': axes.get('Sample', 0), 'height': axes.get('Line', 0),
            'filter': filter_name, 'confirmed_rgb': rgb, 'nonterrain_hint': nonterrain,
            'description': description, 'start_time': first(root, 'start_date_time'),
            'image_url': image_url, 'label_url': label_url, 'archive_folder': urljoin(label_url, './'),
            'sample_bits': int(first(root, 'sample_bits', '0'))}


def candidates(urls):
    """One full-size transmission/processing variant per capture; exclude thumbnail modes G/H/I."""
    mapping = {unquote(Path(urlparse(u).path).name).upper(): u for u in urls}
    captures = {}
    for name, url in mapping.items():
        match = re.fullmatch(r'(\d{4}M[LR]\d+)([ABCEF])(\d{2})_(DRCL|DRCX|DRLX|DRXX)\.XML', name)
        if not match: continue
        capture, mode, version, suffix = match.groups()
        image = mapping.get(name[:-4]+'.IMG')
        if not image: continue
        rank = (('C', 'A', 'B', 'F', 'E').index(mode), ('DRCL', 'DRCX', 'DRLX', 'DRXX').index(suffix), -int(version))
        if capture not in captures or rank < captures[capture][0]:
            captures[capture] = (rank, url, image)
    return [(entry[1], entry[2]) for _, entry in sorted(captures.items())]


def discover(cache, archive, wanted_sols, workers):
    """Discover sol directories from all calibrated volumes, including late re-deliveries."""
    errors, folders = [], []
    volumes = [url for url in children(cache.get(archive), archive)
               if re.fullmatch(r'volume_\d{4}_calibrated', Path(urlparse(url).path.rstrip('/')).name)]
    if not volumes: raise RuntimeError('No calibrated volumes found; check --archive-root and the NASA listing')
    def scan(volume):
        surface = volume.rstrip('/')+'/SURFACE/'
        try:
            sols = children(cache.get(surface), surface)
            return [(int(Path(urlparse(u).path.rstrip('/')).name), u) for u in sols
                    if Path(urlparse(u).path.rstrip('/')).name.isdigit()
                    and int(Path(urlparse(u).path.rstrip('/')).name) in wanted_sols], None
        except (OSError, ValueError, RuntimeError) as error:
            return [], {'scope': 'volume', 'url': surface, 'error': str(error)}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for found, error in pool.map(scan, volumes):
            folders.extend(found)
            if error: errors.append(error)
    return sorted(set(folders)), errors, len(volumes)


def relation(image, anchor, radius):
    if image['site'] is None or image['drive'] is None:
        return 'unknown_location', None, None
    if (image['site'], image['drive']) == (anchor['site'], anchor['drive']):
        return 'same_site_drive', 0., 'site_drive_identity'
    if image['site'] == anchor['site'] and image['site_position'] and anchor['site_position']:
        d = math.hypot(image['site_position'][0]-anchor['site_position'][0],
                       image['site_position'][1]-anchor['site_position'][1])
        return ('nearby' if d <= radius else 'outside_radius'), d, 'same_site_telemetry_xy'
    if image['position'] and anchor['position']:
        d = distance_m(image['position'], anchor['position'])
        return ('nearby' if d <= radius else 'outside_radius'), d, 'waypoint_mars_haversine'
    return 'unknown_distance', None, None


def assess(images, anchors, sol, args, incomplete):
    matches, summaries = [], []
    for anchor in anchors:
        groups = {key: [] for key in ('same_site_drive', 'nearby', 'outside_radius', 'unknown_location', 'unknown_distance')}
        for image in images:
            if abs(image['sol']-sol) > args.sol_window: continue
            rel, distance, method = relation(image, anchor, args.radius_m)
            direction = None
            if image['site'] == anchor['site'] and image['pointing'] and anchor['pointings']:
                direction = min(angular_separation(image['pointing'], p) for p in anchor['pointings'])
            record = {**image, 'target_sol': sol, 'target_site': anchor['site'], 'target_drive': anchor['drive'],
                      'relation': rel, 'distance_m': distance, 'distance_method': method,
                      'navcam_center_separation_deg': direction,
                      'direction_candidate': direction is not None and direction <= args.pointing_tolerance_deg}
            groups[rel].append(record); matches.append(record)
        same, nearby = groups['same_site_drive'], groups['nearby']
        same_bins = sorted({int(r['pointing']['azimuth']//args.azimuth_bin_deg) for r in same if r['pointing']})
        combined_bins = sorted({int(r['pointing']['azimuth']//args.azimuth_bin_deg) for r in same+nearby
                                if r['pointing'] and r['site'] == anchor['site']})
        def verdict(count, bins, uncertain):
            if count >= args.min_color_images and len(bins) >= args.min_azimuth_bins:
                return 'ample_by_metadata_heuristic' if not uncertain else 'ample_lower_bound_incomplete_search'
            if uncertain: return 'unknown_incomplete_search'
            if count >= args.min_color_images: return 'count_met_directional_diversity_unconfirmed'
            return 'limited' if count else 'none_in_search_window'
        summaries.append({'target_sol': sol, 'site': anchor['site'], 'drive': anchor['drive'],
                          'navcam_frames': len(anchor['navcam_frames']),
                          'same_day_same_spot': sum(r['sol'] == sol for r in same),
                          'same_spot_window': len(same), 'nearby_window': len(nearby),
                          'same_spot_azimuth_bins': same_bins, 'same_or_nearby_azimuth_bins': combined_bins,
                          'same_spot_direction_candidates': sum(r['direction_candidate'] for r in same),
                          'unknown_distance': len(groups['unknown_distance'])+len(groups['unknown_location']),
                          'outside_radius': len(groups['outside_radius']),
                          'same_spot_verdict': verdict(len(same), same_bins, incomplete or bool(groups['unknown_location'])),
                          'same_or_nearby_verdict': verdict(len(same)+len(nearby), combined_bins,
                              incomplete or bool(groups['unknown_location'] or groups['unknown_distance']))})
    return matches, summaries


def write_reports(output, report):
    (output/'coverage.json').write_text(json.dumps(report, indent=2, allow_nan=False))
    columns = ['target_sol', 'target_site', 'target_drive', 'sol', 'product_id', 'capture_id', 'camera',
               'site', 'drive', 'relation', 'distance_m', 'distance_method', 'width', 'height', 'bands',
               'filter', 'sample_bits', 'start_time', 'navcam_center_separation_deg', 'direction_candidate',
               'description', 'image_url', 'label_url', 'archive_folder']
    with (output/'images.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction='ignore')
        writer.writeheader(); writer.writerows(report['matches'])
    a = report['config']
    lines = ['# NASA Mastcam color coverage audit', '',
             f"Target sols: {a['sols']}; search: ±{a['sol_window']} sols; nearby radius: {a['radius_m']} m.",
             f"Archive: {a['archive_root']}",
             f"Status: {'INCOMPLETE — counts are lower bounds' if report['incomplete'] else 'metadata search completed'}.",
             f"Calibrated volumes inspected: {report['volumes']}; selected sol folders: {report['folders']}; "
             f"unique labels checked: {report['labels_checked']}; RGB terrain candidates: {len(report['images'])}.", '',
             f"Ample heuristic: at least {a['min_color_images']} unique qualifying captures and "
             f"{a['min_azimuth_bins']} occupied {a['azimuth_bin_deg']}° azimuth bins. These are adjustable planning thresholds.", '',
             '| Target sol | Site/drive | Navcam frames | Same spot, same sol | Same spot, window | Nearby, window | Same-spot azimuth bins | Direction candidates | Same-spot verdict | Same+nearby verdict | Unknown distance |',
             '|---:|---|---:|---:|---:|---:|---:|---:|---|---|---:|']
    for r in report['summary']:
        lines.append(f"| {r['target_sol']} | {r['site']}/{r['drive']} | {r['navcam_frames']} | "
                     f"{r['same_day_same_spot']} | {r['same_spot_window']} | {r['nearby_window']} | "
                     f"{len(r['same_spot_azimuth_bins'])} | {r['same_spot_direction_candidates']} | "
                     f"{r['same_spot_verdict']} | {r['same_or_nearby_verdict']} | {r['unknown_distance']} |")
    lines += ['', '## How the images are found and screened', '',
              '1. Read local Navcam RAS/XYZ labels to identify every site/drive represented in each sol, falling back to stop.json.',
              '2. Discover all calibrated Mastcam volumes in the current NASA PDS4 collection; inspect matching sol directories.',
              '3. Select one full-size transmission/processing variant per capture, excluding grayscale and thumbnail transmission modes.',
              f"4. Check XML for three bands, explicit RGB display channels, decoded Bayer color, clear filter, and at least {a['min_width']}×{a['min_height']} pixels.",
              '5. Exclude descriptions explicitly identifying sky, Sun, or calibration-target observations. Other scene types still need visual review.',
              '6. Match exact site/drive; otherwise calculate horizontal distance from telemetry offsets within the same site, or Mars waypoint latitude/longitude across sites.', '',
              '## Interpretation and limits', '',
              '- Same sol is a time filter, not a location match. Drive counters are identifiers, not metres.',
              '- RGB confirms a color product, not visual richness, focus, exposure quality, or a complete panorama. No image pixels were downloaded or analyzed.',
              '- Same site/drive confirms rover station only; it does not prove the same ground is visible. Mastcam and Navcam have different viewpoints and fields of view.',
              f"- Direction candidates have center directions within {a['pointing_tolerance_deg']}° in the same site frame; this is only a screening hint, not geometric registration.",
              '- Azimuth bins describe heading diversity, not angular coverage or overlap. Camera elevations and scene content must also be reviewed.',
              '- Nearby captures are within the specified rover-position radius, not necessarily images of the target patch. The default search is limited to the stated sol window.',
              '- Missing waypoints or geometry remain unknown; cross-site coordinates are not compared directly. Failed requests make the audit incomplete, not proof of absence.',
              '- Multiple target anchors can reference the same capture; do not sum anchor counts to obtain a global unique-image count.',
              '- This audit focuses on Mastcam landscape RGB. MAHLI close-ups, multispectral filter stacks, grayscale Navcam, and raw Bayer mosaics are not counted.', '',
              '## Downloading the color data', '',
              'Open gallery.html or images.csv. Each record includes an exact NASA IMG URL and its PDS4 XML label; processing/thumbnail variants are not counted twice.',
              'Download the IMG together with its XML. These are scientific PDS images, not JPEG files: decode using a PDS4-capable reader and preserve the label metadata.',
              'Use the XML array offset/type/dimensions and RGB band mapping when converting to PNG/JPEG. A 16-bit calibrated RGB product requires an explicit display stretch; do not rename IMG to JPG.',
              'For colorizing Navcam geometry, project with both camera models and poses and check occlusion. Mastcam RGB cannot be assigned to Navcam pixels by equal row/column alone.', '',
              '## Screening totals', '', json.dumps(report['screening'], indent=2), '',
              '## Missing targets and errors', '']
    lines += [f'- No local Navcam labels or stop.json anchor for sol {sol}.' for sol in report['missing_targets']]
    lines += [f"- {e['scope']}: {e.get('url', '')}: {e['error']}" for e in report['errors']]
    if not report['missing_targets'] and not report['errors']: lines.append('None.')
    text = '\n'.join(lines)+'\n'
    (output/'summary.md').write_text(text)
    cards = []
    for r in report['matches']:
        if r['relation'] not in ('same_site_drive', 'nearby'): continue
        d = f"{r['distance_m']:.1f} m" if r['distance_m'] is not None else 'unknown'
        cards.append('<tr>'+''.join(f'<td>{escape(str(v))}</td>' for v in
                     (r['target_sol'], f"{r['target_site']}/{r['target_drive']}", r['sol'], r['product_id'],
                      r['relation'], d, f"{r['width']}×{r['height']}"))+
                     f'<td><a href="{escape(r["image_url"], quote=True)}">NASA IMG</a> · '
                     f'<a href="{escape(r["label_url"], quote=True)}">XML label</a> · '
                     f'<a href="{escape(r["archive_folder"], quote=True)}">Archive folder</a></td></tr>')
    (output/'gallery.html').write_text('<!doctype html><meta charset="utf-8"><title>Mastcam candidates</title>'
        '<style>body{font:15px system-ui;margin:2rem}td,th{padding:.5rem;border-bottom:1px solid #ccc;text-align:left}table{border-collapse:collapse}</style>'
        '<h1>Mastcam color candidates</h1><p>Metadata only; links download scientific images. '
        'Same rover station or nearby position does not prove matching scene coverage. See summary.md.</p>'
        '<table><tr><th>Target sol</th><th>Target site/drive</th><th>Image sol</th><th>Product</th><th>Relation</th><th>Distance</th><th>Size</th><th>Links</th></tr>'+
        ''.join(cards)+'</table>')
    print(text, flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--scene-root', type=Path, help='use existing scene metadata and fetch only source camera labels')
    ap.add_argument('--raw-root', type=Path, default=ROOT/'data/raw')
    ap.add_argument('--sols', type=int, nargs='+', help='default: sol folders in raw-root, or the repo eight-sol list')
    ap.add_argument('--sol-window', type=int, default=5)
    ap.add_argument('--radius-m', type=float, default=100)
    ap.add_argument('--min-color-images', type=int, default=10)
    ap.add_argument('--min-azimuth-bins', type=int, default=3)
    ap.add_argument('--azimuth-bin-deg', type=int, default=30)
    ap.add_argument('--pointing-tolerance-deg', type=float, default=30)
    ap.add_argument('--min-width', type=int, default=512)
    ap.add_argument('--min-height', type=int, default=512)
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--timeout', type=int, default=30)
    ap.add_argument('--archive-root', default=ARCHIVE)
    ap.add_argument('--cache', type=Path, default=ROOT/'data/cache/color_audit')
    ap.add_argument('--waypoints', type=Path, help='optional local NASA waypoint GeoJSON')
    ap.add_argument('--offline', action='store_true', help='cached metadata only; never contact NASA')
    ap.add_argument('--refresh', action='store_true', help='refresh cached metadata')
    ap.add_argument('--output', type=Path, default=ROOT/'data/color_coverage'/datetime.now().strftime('%Y%m%d-%H%M%S'))
    a = ap.parse_args()
    if a.sols is None:
        a.sols = sorted(int(p.name[3:]) for p in a.raw_root.glob('sol*') if p.is_dir() and p.name[3:].isdigit()) or DEFAULT_SOLS
    a.sols = sorted(set(a.sols))
    if min(a.sols) < 0 or a.sol_window < 0 or a.radius_m < 0 or a.workers < 1 or a.timeout < 1:
        ap.error('sols/window/radius must be nonnegative; workers and timeout positive')
    if a.min_color_images < 1 or a.min_azimuth_bins < 0 or not 1 <= a.azimuth_bin_deg <= 360 or 360 % a.azimuth_bin_deg:
        ap.error('min-color-images must be positive; min-azimuth-bins nonnegative; bin size must divide 360')
    if min(a.min_width, a.min_height) < 1 or not 0 <= a.pointing_tolerance_deg <= 180:
        ap.error('dimensions must be positive; pointing tolerance must be between 0 and 180 degrees')
    if a.output.exists(): ap.error('Output directory already exists; choose a new directory')
    a.archive_root = a.archive_root.rstrip('/')+'/'
    if a.scene_root:
        a.raw_root = prepare_scene_anchors(a.scene_root, a.sols, MetadataCache(a.cache, a.offline, a.refresh, a.timeout))
    targets = load_targets(a.raw_root, a.sols)
    missing = [sol for sol, anchors in targets.items() if not anchors]
    if len(missing) == len(a.sols):
        ap.error('No local Navcam labels or stop.json anchors found. Supply --raw-root pointing to your data/raw folder.')
    cache = MetadataCache(a.cache, a.offline, a.refresh, a.timeout)
    errors, positions = [], {}
    # Telemetry positions allow same-site matching even if the live waypoint feed is unavailable.
    try:
        positions = waypoint_positions(a.waypoints.read_text() if a.waypoints else cache.get(WAYPOINTS))
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        errors.append({'scope': 'waypoints', 'url': str(a.waypoints or WAYPOINTS), 'error': str(error)})
    for anchors in targets.values():
        for anchor in anchors:
            anchor['position'] = anchor['position'] or positions.get((anchor['site'], anchor['drive']))
    wanted = {s for sol in a.sols for s in range(max(0, sol-a.sol_window), sol+a.sol_window+1)}
    print(f'[NASA] Discovering calibrated Mastcam metadata for {len(wanted)} sols', flush=True)
    try:
        folders, discovery_errors, volume_count = discover(cache, a.archive_root, wanted, a.workers)
    except (OSError, ValueError, RuntimeError) as error:
        folders, discovery_errors, volume_count = [], [{'scope': 'archive', 'url': a.archive_root, 'error': str(error)}], 0
    errors.extend(discovery_errors)
    jobs = []
    def list_folder(item):
        sol, folder = item
        try:
            return candidates(children(cache.get(folder), folder)), None
        except (OSError, ValueError, RuntimeError) as error:
            return [], {'scope': 'sol_folder', 'url': folder, 'error': str(error)}
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        futures = [pool.submit(list_folder, item) for item in folders]
        for i, future in enumerate(as_completed(futures), 1):
            found, error = future.result()
            jobs.extend(found)
            if error: errors.append(error)
            print(f'[NASA] Listed {i}/{len(folders)} sol folders; {len(jobs)} candidate labels', flush=True)
    jobs = sorted(set(jobs))
    print(f'[NASA] Checking {len(jobs)} labels; no image pixels will be downloaded', flush=True)
    images, screening = {}, {'not_confirmed_rgb': 0, 'below_dimensions': 0, 'nonterrain_hint': 0, 'duplicates': 0}
    def inspect(job):
        label_url, image_url = job
        try:
            return parse_mastcam(cache.get(label_url), label_url, image_url), None
        except (ET.ParseError, OSError, ValueError, RuntimeError) as error:
            return None, {'scope': 'label', 'url': label_url, 'error': str(error)}
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        futures = [pool.submit(inspect, job) for job in jobs]
        for i, future in enumerate(as_completed(futures), 1):
            image, error = future.result()
            if i % 50 == 0 or i == len(jobs):
                print(f'[NASA] Checked {i}/{len(jobs)} labels; {len(images)} RGB candidates; {len(errors)} errors', flush=True)
            if error: errors.append(error); continue
            if not image['confirmed_rgb']: screening['not_confirmed_rgb'] += 1; continue
            if image['width'] < a.min_width or image['height'] < a.min_height:
                screening['below_dimensions'] += 1; continue
            if image['nonterrain_hint']: screening['nonterrain_hint'] += 1; continue
            image['position'] = positions.get((image['site'], image['drive']))
            if image['capture_id'] in images: screening['duplicates'] += 1
            previous = images.get(image['capture_id'])
            if previous is None or image['label_url'] > previous['label_url']:
                images[image['capture_id']] = image

    images = sorted(images.values(), key=lambda r: (r['sol'], r['capture_id']))
    incomplete = bool(errors or missing)
    matches, summary = [], []
    for sol, anchors in targets.items():
        records, stats = assess(images, anchors, sol, a, incomplete)
        matches.extend(records); summary.extend(stats)
    report = {'created_utc': datetime.now(timezone.utc).isoformat(),
              'config': {k: str(v) if isinstance(v, Path) else v for k, v in vars(a).items()},
              'incomplete': incomplete, 'volumes': volume_count, 'folders': len(folders),
              'labels_checked': len(jobs), 'screening': screening, 'targets': targets,
              'missing_targets': missing, 'errors': errors, 'images': images, 'matches': matches, 'summary': summary}
    a.output.mkdir(parents=True)
    write_reports(a.output, report)
    print(f'Reports: {a.output.resolve()}', flush=True)
    if incomplete: raise SystemExit(2)  # preserves report while making uncertainty machine-readable


if __name__ == '__main__':
    main()
