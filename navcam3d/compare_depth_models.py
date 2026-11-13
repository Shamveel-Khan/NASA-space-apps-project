"""Standalone Colab comparison: Depth Anything V2 Small, MoGe-2, and Depth Anything 3.

Requires numpy, torch, transformers, Pillow, matplotlib, microsoft/MoGe,
and ByteDance-Seed/Depth-Anything-3.
Colab setup (notebook cell, then restart runtime if packages changed):
  %pip install "numpy<2" pillow matplotlib "transformers==4.57.6" huggingface_hub "opencv-python<4.12" git+https://github.com/microsoft/MoGe.git git+https://github.com/ByteDance-Seed/Depth-Anything-3.git
Select a GPU runtime. All three models use single-image inference. Default DA3:
depth-anything/DA3-LARGE-1.1; use --da3-model to select mono/metric/smaller variants.
The full report prints to stdout and is saved as summary.md alongside JSON/CSV.
No production files are modified. See --help for data/cache/resolution options.
Downloads NASA stereo data and model weights; all inference runs locally.
Example: python compare_depth_models.py --sols 520 1094 1100 --device cuda
"""
import argparse
import csv
import gc
import hashlib
import json
import os
import platform
import importlib.metadata
import importlib.util
from itertools import combinations
from pathlib import Path
import re
import time

import concurrent.futures as cf
import urllib.request
import urllib.error
from urllib.parse import urljoin

DEFAULT_SOLS = [520, 521, 1037, 1083, 1094, 1098, 1100, 1400]
DA3_MODELS = ['depth-anything/DA3-LARGE-1.1', 'depth-anything/DA3-LARGE',
              'depth-anything/DA3MONO-LARGE', 'depth-anything/DA3METRIC-LARGE',
              'depth-anything/DA3-BASE', 'depth-anything/DA3-SMALL']
PDS = 'https://planetarydata.jpl.nasa.gov/img/data/msl/MSLNAV_1XXX/'


def fetch_file(url, destination, expected=None):
    """Atomic cached downloads with retries and optional PDS byte-count checks."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size > 0:
        if expected is None or destination.stat().st_size == expected:
            return destination
    temporary = destination.with_name(destination.name + '.part')
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'marswalk-depth-comparison/1.0'})
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open('wb') as out:
                while chunk := response.read(1024*1024):
                    out.write(chunk)
            if temporary.stat().st_size == 0 or (expected is not None and temporary.stat().st_size != expected):
                raise ValueError(f'Incomplete NASA product: {destination.name}')
            temporary.replace(destination)
            return destination
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 404):
                raise RuntimeError(f'NASA HTTP {exc.code}: {url}') from exc
            if attempt == 3: raise
        except (OSError, ValueError) as exc:
            if attempt == 3: raise RuntimeError(f'Download failed: {url}: {exc}') from exc
        time.sleep(2**attempt)


def download_sol(sol, root, workers, max_frames):
    """Fetch all paired linearized left-camera stereo products for a sol.

    Uses the PDS listing directly: no rover waypoint feed or repo imports needed.
    Includes every site/drive represented in the sol rather than only its last stop.
    """
    folder = PDS + ('DATA_V1/' if sol <= 1869 else 'DATA/') + f'SOL{sol:05d}/'
    listing = fetch_file(folder, root/'_listings'/f'SOL{sol:05d}.html').read_text()
    names = set(re.findall(r'NLB_[A-Z0-9_]+\.(?:LBL|IMG)', listing, re.I))
    stems = sorted(n[:-4] for n in names if n.endswith('.LBL') and 'XYZLF' in n
                   and n[:-4]+'.IMG' in names
                   and n.replace('XYZLF', 'RASLF') in names
                   and n.replace('XYZLF', 'RASLF')[:-4]+'.IMG' in names)
    if max_frames: stems = stems[:max_frames]
    if not stems: raise RuntimeError(f'Sol {sol}: no paired linearized left Navcam RAS/XYZ products at {folder}')
    out = root/f'sol{sol}'
    print(f'[NASA] Sol {sol}: {len(stems)} stereo pairs (~{len(stems)*15} MB)', flush=True)
    def pair(stem):
        for kind in ('RASLF', 'XYZLF'):
            name = stem.replace('XYZLF', kind)
            lbl = fetch_file(urljoin(folder, name+'.LBL'), out/(name+'.LBL'))
            e = label(lbl)
            expected = int(e['FILE_RECORDS'])*int(e['RECORD_BYTES'])
            fetch_file(urljoin(folder, name+'.IMG'), out/(name+'.IMG'), expected)
        return out/(stem.replace('XYZLF', 'RASLF')+'.LBL'), out/(stem+'.LBL')
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        # Consuming map propagates worker failures; incomplete datasets are never silently scored.
        pairs = list(pool.map(pair, stems))
    return pairs


def resolve_weights(a):
    if a.offline:
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['TRANSFORMERS_OFFLINE'] = '1'
    if a.da_weights is None or a.moge_weights is None or a.da3_weights is None:
        from huggingface_hub import snapshot_download, hf_hub_download
        a.model_cache.mkdir(parents=True, exist_ok=True)
        if a.da_weights is None:
            a.da_weights = Path(snapshot_download('depth-anything/Depth-Anything-V2-Small-hf',
                cache_dir=str(a.model_cache), local_files_only=a.offline,
                allow_patterns=['*.json', '*.safetensors', '*.bin']))
        if a.moge_weights is None:
            a.moge_weights = Path(hf_hub_download('Ruicheng/moge-2-vitl', 'model.pt',
                cache_dir=str(a.model_cache), local_files_only=a.offline))
        if a.da3_weights is None:
            a.da3_weights = Path(snapshot_download(a.da3_model,
                cache_dir=str(a.model_cache), local_files_only=a.offline,
                allow_patterns=['*.json', '*.safetensors', '*.bin']))
    if not a.da_weights.is_dir() or not a.moge_weights.is_file() or not a.da3_weights.is_dir():
        raise ValueError('Model paths must be existing DA2/DA3 directories and a MoGe model.pt')


import numpy as np


def label(path):
    text = re.sub(r'/\*.*?\*/', '', path.read_text(), flags=re.S)
    return {k: v.strip() for k, v in re.findall(
        r'^\s*([\^A-Z0-9_]+)\s*=\s*(\([^)]*\)|[^\n]+)', text, re.M)}


def numbers(value):
    return np.array([float(x) for x in re.findall(r'-?\d+\.?\d*(?:[eE][-+]?\d+)?', value.split('<')[0])])


def read_product(path):
    e = label(path)
    dtype = {('MSB_INTEGER', 16): '>i2', ('IEEE_REAL', 32): '>f4'}[
        e['SAMPLE_TYPE'], int(e['SAMPLE_BITS'])]
    shape = tuple(int(e[k]) for k in ('BANDS', 'LINES', 'LINE_SAMPLES'))
    offset = (int(numbers(e['^IMAGE'].split(',')[-1])[0]) - 1) * int(e['RECORD_BYTES'])
    a = np.fromfile(path.with_suffix('.IMG'), dtype=dtype, offset=offset,
                    count=int(np.prod(shape))).reshape(shape).astype(np.float32)
    return (a[0] if shape[0] == 1 else a), e


def prepare(ras, xyz_path, max_depth):
    img, _ = read_product(ras)
    xyz, e = read_product(xyz_path)
    q = numbers(e['ORIGIN_ROTATION_QUATERNION']); q /= np.linalg.norm(q)
    s, x, y, z = q
    R = np.array([[1-2*(y*y+z*z), 2*(x*y-s*z), 2*(x*z+s*y)],
                  [2*(x*y+s*z), 1-2*(x*x+z*z), 2*(y*z-s*x)],
                  [2*(x*z-s*y), 2*(y*z+s*x), 1-2*(x*x+y*y)]])
    P = (np.moveaxis(xyz, 0, -1) - numbers(e['ORIGIN_OFFSET_VECTOR'])) @ R
    C, A = (numbers(e[f'MODEL_COMPONENT_{i}']) for i in (1, 2))
    depth = (P-C) @ A
    body = (P[..., 0] > -1.6) & (P[..., 0] < 1.9) & (abs(P[..., 1]) < 1.5) & (P[..., 2] < -.25)
    valid = np.isfinite(xyz).all(0) & (abs(xyz).sum(0) > 0) & ~body & (depth > 0) & (depth < max_depth)
    lo, hi = np.percentile(img[img > 0], [.5, 99.5]) if (img > 0).any() else (0, 1)
    img8 = (np.clip((img-lo)/max(hi-lo, 1), 0, 1)*255).astype(np.uint8)
    return img8, depth, valid


def splits(shape, seed, block):
    y, x = np.indices(shape)
    ids = (y//block)*(int(np.ceil(shape[1]/block))) + x//block
    assignment = np.random.default_rng(seed).choice(3, int(ids.max())+1, p=[.4, .2, .4])
    return tuple(assignment[ids] == k for k in range(3))


def fit_inverse(method, r, z):
    if method == 'power':
        k, c = np.polyfit(np.log(np.maximum(r, 1e-3)), np.log(z), 1)
        return lambda a: np.exp(c)*np.maximum(a, 1e-3)**k
    X = np.stack([r, np.ones_like(r)], 1)
    target = 1/z
    if method == 'weighted':
        X, target = X*z[:, None], np.ones_like(z)
    s, t = np.linalg.lstsq(X, target, rcond=None)[0]
    return lambda a: 1/np.maximum(s*a+t, 1e-3)


METRIC_KEYS = ('absrel', 'sqrel', 'mae_m', 'rmse_m', 'rmse_log', 'log10',
               'bias_m', 'median_ae_m', 'p90_rel', 'delta1', 'delta2', 'delta3')


def metrics(p, g, mask):
    """All errors are measured on finite positive predictions and truth, without clipping."""
    total = int(mask.sum())
    use = mask & np.isfinite(p) & (p > 0) & np.isfinite(g) & (g > 0)
    result = {'count': int(use.sum()), 'eligible_count': total,
              'coverage': float(use.sum()/max(total, 1))}
    if not use.any():
        return {**result, **dict.fromkeys(METRIC_KEYS)}
    a, b = p[use].astype(np.float64), g[use].astype(np.float64)
    error = a-b
    rel = abs(error)/b
    log_error = np.log(a)-np.log(b)
    ratio = np.maximum(a/b, b/a)
    return {**result, 'absrel': float(rel.mean()), 'sqrel': float(np.mean(error**2/b)),
            'mae_m': float(np.mean(abs(error))), 'rmse_m': float(np.sqrt(np.mean(error**2))),
            'rmse_log': float(np.sqrt(np.mean(log_error**2))),
            'log10': float(np.mean(abs(np.log10(a)-np.log10(b)))),
            'bias_m': float(error.mean()), 'median_ae_m': float(np.median(abs(error))),
            'p90_rel': float(np.percentile(rel, 90)),
            **{f'delta{k}': float(np.mean(ratio < 1.25**k)) for k in (1, 2, 3)}}


def resize_depth(depth, shape):
    """DA3 resize-only preprocessing preserves the full field of view; undo its resize."""
    import torch
    depth = np.asarray(depth, dtype=np.float32)
    if depth.ndim != 2:
        raise ValueError(f'Expected HxW depth, received {depth.shape}')
    if depth.shape == shape:
        return np.where(np.isfinite(depth) & (depth > 0), depth, np.nan)
    valid = np.isfinite(depth) & (depth > 0)
    values = torch.from_numpy(np.where(valid, depth, 0))[None, None]
    support = torch.from_numpy(valid.astype(np.float32))[None, None]
    kwargs = dict(size=shape, mode='bilinear', align_corners=False)
    d = torch.nn.functional.interpolate(values, **kwargs)[0, 0].numpy()
    w = torch.nn.functional.interpolate(support, **kwargs)[0, 0].numpy()
    return np.where(w > .999, d, np.nan)



def load_model(kind, path, device, da3_process_res=504, moge_resolution_level=9):
    import torch
    if kind == 'da':
        from transformers import AutoImageProcessor, AutoModelForDepthEstimation
        proc = AutoImageProcessor.from_pretrained(str(path), local_files_only=True)
        model = AutoModelForDepthEstimation.from_pretrained(str(path), local_files_only=True).to(device).eval()
        def predict(img):
            from PIL import Image
            rgb = Image.fromarray(np.repeat(img[..., None], 3, -1))
            out = model(**proc(images=rgb, return_tensors='pt').to(device)).predicted_depth
            return torch.nn.functional.interpolate(out[:, None], size=img.shape,
                mode='bicubic', align_corners=False)[0, 0].float().cpu().numpy()
    elif kind == 'da3':
        from depth_anything_3.api import DepthAnything3
        model = DepthAnything3.from_pretrained(str(path.resolve()), local_files_only=True).to(device).eval()
        def predict(img):
            rgb = np.repeat(img[..., None], 3, -1)
            output = model.inference([rgb], process_res=da3_process_res,
                process_res_method='upper_bound_resize', export_dir=None)
            # DA3 is direct depth, not inverse depth. Do not invert or metric-scale here.
            predict.is_metric = bool(output.is_metric)
            return resize_depth(output.depth[0], img.shape)
    elif kind == 'moge':
        from moge.model.v2 import MoGeModel
        model = MoGeModel.from_pretrained(str(path.resolve())).to(device).eval()
        def predict(img):
            tensor = torch.from_numpy(np.repeat(img[None], 3, 0)).to(device=device, dtype=torch.float32)/255
            out = model.infer(tensor, resolution_level=moge_resolution_level, use_fp16=False)
            d = out['depth'].float().cpu().numpy()
            mask = out['mask'].cpu().numpy().astype(bool)
            return np.where(mask & np.isfinite(d) & (d > 0), d, np.nan)
    else:
        raise ValueError(f'Unknown model: {kind}')
    return model, predict


def depth_ranges(max_depth):
    edges = sorted(set([0., max_depth] + [x for x in (5., 10., 20., 30., 45.) if x < max_depth]))
    return list(zip(edges[:-1], edges[1:]))


def aggregate(scored):
    """Frame statistics plus exact pooled means/RMSE from per-frame sufficient statistics."""
    scored = [m for m in scored if m['count'] and m['absrel'] is not None]
    if not scored:
        return {'frames': 0, 'pixels': 0, 'frame_statistics': {}, 'pooled': {}}
    frame_stats = {}
    for key in METRIC_KEYS:
        values = np.array([m[key] for m in scored], dtype=float)
        frame_stats[key] = {'mean': float(values.mean()), 'median': float(np.median(values)),
                            'p90': float(np.percentile(values, 90)), 'min': float(values.min()),
                            'max': float(values.max()), 'std': float(values.std())}
    counts = np.array([m['count'] for m in scored], dtype=float)
    pooled = {}
    for key in ('absrel', 'sqrel', 'mae_m', 'rmse_m', 'rmse_log', 'log10', 'bias_m', 'delta1', 'delta2', 'delta3'):
        values = np.array([m[key] for m in scored], dtype=float)
        pooled[key] = float(np.sqrt(np.average(values**2, weights=counts)) if key in ('rmse_m', 'rmse_log')
                            else np.average(values, weights=counts))
    # Per-frame percentiles cannot reconstruct global pixel percentiles, so do not label them as pooled.
    return {'frames': len(scored), 'pixels': int(counts.sum()),
            'eligible_pixels': sum(m['eligible_count'] for m in scored),
            'frame_statistics': frame_stats, 'pooled': pooled}


def summarize(rows):
    scored = [r for r in rows if 'model' in r]
    names = sorted({r['model'] for r in scored})
    def group(subset):
        return {name: aggregate([r['common'] for r in subset if r['model'] == name]) for name in names}
    return {'overall': group(scored),
            'per_sol': {str(sol): group([r for r in scored if r['sol'] == sol])
                        for sol in sorted({r['sol'] for r in rows})},
            'per_depth_range': {band: {name: aggregate([r['depth_ranges'][band] for r in scored if r['model'] == name])
                                      for name in names}
                                for band in sorted({b for r in scored for b in r['depth_ranges']},
                                                   key=lambda b: float(b.split('-')[0]))}}


def pairwise_comparisons(rows):
    calibrated = ('da_aligned', 'moge_scaled', 'da3_scaled')
    by_model = {name: {(r['sol'], r['frame']): r['common']['absrel'] for r in rows
                       if r.get('model') == name and r['common']['absrel'] is not None}
                for name in calibrated}
    results = []
    for a, b in combinations(calibrated, 2):
        shared = sorted(set(by_model[a]) & set(by_model[b]))
        differences = np.array([by_model[a][key]-by_model[b][key] for key in shared])
        results.append({'a': a, 'b': b, 'frames': len(shared),
                        'a_wins': int((differences < -1e-10).sum()),
                        'b_wins': int((differences > 1e-10).sum()),
                        'ties': int((abs(differences) <= 1e-10).sum()),
                        'mean_absrel_a_minus_b': float(differences.mean()) if shared else None})
    return results


def environment_info(torch, device):
    packages = {}
    for name in ('numpy', 'torch', 'torchvision', 'transformers', 'huggingface-hub', 'moge', 'depth-anything-3'):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = 'unknown / source checkout'
    return {'python': platform.python_version(), 'platform': platform.platform(),
            'packages': packages, 'device': device, 'cuda_version': torch.version.cuda,
            'gpu': torch.cuda.get_device_name() if device == 'cuda' else None,
            'precision': {'da': 'float32', 'moge': 'float32, use_fp16=False',
                          'da3': 'upstream automatic mixed precision; weights float32'},
            'timing_scope': 'preprocess + inference + original-size depth conversion; excludes PDS reads, disk writes, loading and warm-up'}


def weight_fingerprints(path):
    files = [path] if path.is_file() else sorted(p for p in path.rglob('*')
             if p.is_file() and p.suffix in ('.json', '.safetensors', '.bin'))
    result = []
    for file in files:
        h = hashlib.sha256()
        with file.open('rb') as source:
            for chunk in iter(lambda: source.read(1024*1024), b''):
                h.update(chunk)
        result.append({'file': str(file), 'bytes': file.stat().st_size, 'sha256': h.hexdigest()})
    return result


def write_csv_reports(output, rows, summary):
    fields = ['sol', 'frame', 'model', 'scope', 'depth_range', 'alignment', 'scale',
              'calibration_pixels', 'test_pixels', 'stereo_coverage', 'common_stereo_coverage',
              'full_image_prediction_coverage', 'hole_prediction_coverage',
              'count', 'eligible_count', 'coverage', *METRIC_KEYS]
    with (output/'metrics.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader()
        for row in rows:
            if 'model' not in row: continue
            metadata = {k: row[k] for k in fields if k in row}
            for scope in ('common', 'own'):
                writer.writerow({**metadata, 'scope': scope, **row[scope]})
            for band, m in row['depth_ranges'].items():
                writer.writerow({**metadata, 'scope': 'common_depth_range', 'depth_range': band, **m})
    fields = ['scope', 'group', 'model', 'frames', 'pixels', 'metric',
              'frame_mean', 'frame_median', 'frame_p90', 'frame_min', 'frame_max', 'frame_std', 'pooled']
    with (output/'aggregate_metrics.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader()
        groups = [('overall', 'all', summary['overall'])]
        groups += [(scope, group, models) for scope in ('per_sol', 'per_depth_range')
                   for group, models in summary[scope].items()]
        for scope, group, models in groups:
            for name, stats in models.items():
                for key, values in stats['frame_statistics'].items():
                    writer.writerow({'scope': scope, 'group': group, 'model': name, 'frames': stats['frames'],
                                     'pixels': stats['pixels'], 'metric': key,
                                     **{f'frame_{k}': v for k, v in values.items()},
                                     'pooled': stats['pooled'].get(key)})


def accuracy_table(models):
    lines = ['| Model / variant | Frames | Test pixels | AbsRel % ↓ | MAE m ↓ | RMSE m ↓ | δ1 % ↑ | δ2 % ↑ | δ3 % ↑ | Bias m |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for name, group in models.items():
        if not group['frames']:
            lines.append(f'| {name} | 0 | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |'); continue
        m = {k: v['median'] for k, v in group['frame_statistics'].items()}
        lines.append(f"| {name} | {group['frames']} | {group['pixels']:,} | {m['absrel']*100:.2f} | "
                     f"{m['mae_m']:.3f} | {m['rmse_m']:.3f} | {m['delta1']*100:.2f} | "
                     f"{m['delta2']*100:.2f} | {m['delta3']*100:.2f} | {m['bias_m']:+.3f} |")
    return lines


def detailed_report(report):
    rows, summary, config = report['frames'], report['summary'], report['config']
    scored = [r for r in rows if 'model' in r]
    skipped = [r for r in rows if 'status' in r]
    frames = {(r['sol'], r['frame']) for r in scored}
    lines = ['# NASA Navcam: DA2 vs MoGe-2 vs DA3', '',
             f"Requested sols: {config['sols']}. Downloaded/selected frames: {len(report['inputs'])}; "
             f'evaluated: {len(frames)}; skipped: {len(skipped)}.',
             f"Device: {config['device']}; GPU: {report['environment']['gpu'] or 'none'}. "
             f"DA3 checkpoint: {report['models']['da3']}.", '',
             '## Evaluation protocol', '',
             '- Each model receives one grayscale-derived RGB photo at a time; no multi-view conditioning, poses, or training.',
             f"- Axial stereo depth: finite, positive, below {config['max_depth']} m, with the rover body excluded.",
             f"- Seed {config['seed']}; {config['block_size']}×{config['block_size']} pixel spatial blocks: "
             '40% fit, 20% method selection, 40% held-out test (expected proportions).',
             '- All three models share common-valid calibration and test pixels; never fit or choose alignment on the test split.',
             '- DA2: choose plain/weighted inverse-depth or power-law fitting using the selection split, then refit on fit+selection.',
             '- MoGe and DA3: median(truth / native depth) on fit+selection; preserves direct-depth semantics.',
             '- Default DA3 relative outputs require calibration; raw relative values are saved but never called metres or ranked as metric depth.',
             '- Raw metric variants are diagnostics; calibrated variants form the three-way ranking.',
             '- Errors use original image resolution, no prediction clipping. DA3 uses resize-only preprocessing and bilinear restoration.',
             '- Model defaults differ in inference resolution and parameter count; this is a deployment comparison, not equal-compute benchmarking.', '',
             '## Overall accuracy', '',
             'Tables use the median of per-frame metrics (each frame has equal weight). Lower errors and higher δ scores are better.', '']
    lines += accuracy_table(summary['overall'])
    lines += ['', '## Variation across frames and pooled-pixel accuracy', '',
              'Pooled scores weight every tested pixel equally. RMSE is recomputed from weighted squared RMSE; '
              'frame percentiles are never presented as pooled pixel percentiles.', '',
              '| Variant | Mean AbsRel % | Median % | P90 frame % | Worst frame % | Std dev (pp) | Pooled AbsRel % | Pooled RMSE m | Pooled δ1 % |',
              '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for name, stats in summary['overall'].items():
        if not stats['frames']: continue
        m, pooled = stats['frame_statistics']['absrel'], stats['pooled']
        lines.append(f"| {name} | {m['mean']*100:.2f} | {m['median']*100:.2f} | {m['p90']*100:.2f} | "
                     f"{m['max']*100:.2f} | {m['std']*100:.2f} | {pooled['absrel']*100:.2f} | "
                     f"{pooled['rmse_m']:.3f} | {pooled['delta1']*100:.2f} |")
    lines += ['', '## Calibrated head-to-head comparison', '',
              'Wins use AbsRel on matching frame IDs and the same test pixels. This is descriptive, not a significance test.', '',
              '| A | B | Paired frames | A wins | B wins | Ties | Mean A−B error (pp) |',
              '|---|---|---:|---:|---:|---:|---:|']
    for result in report['pairwise']:
        difference = result['mean_absrel_a_minus_b']
        difference = f'{difference*100:+.3f}' if difference is not None else 'N/A'
        lines.append(f"| {result['a']} | {result['b']} | {result['frames']} | {result['a_wins']} | "
                     f"{result['b_wins']} | {result['ties']} | {difference} |")
    calibrated = {k: v for k, v in summary['overall'].items()
                  if k in ('da_aligned', 'moge_scaled', 'da3_scaled') and v['frames']}
    if calibrated:
        best = min(v['frame_statistics']['absrel']['median'] for v in calibrated.values())
        winners = [k for k, v in calibrated.items() if abs(v['frame_statistics']['absrel']['median']-best) <= 1e-10]
        lines += ['', f"Lowest median calibrated AbsRel: **{', '.join(winners)}**, {best:.2%}."]
    else:
        lines += ['', 'No valid calibrated comparison; inspect skipped frames below.']
    lines += ['', '## Per-sol accuracy', '']
    for sol, groups in summary['per_sol'].items():
        lines += [f'### Sol {sol}', '', *accuracy_table(groups), '']
    lines += ['## Accuracy by true stereo depth range', '', 'Bands use true depth, not predicted depth. Empty bands are shown as N/A.', '']
    for band, groups in summary['per_depth_range'].items():
        lines += [f'### {band} m', '', *accuracy_table(groups), '']
    lines += ['## Coverage', '', 'Median per-frame coverage; accuracy cannot be established in stereo holes.', '',
              '| Variant | Stereo / image % | Common / stereo % | Prediction / image % | Own test coverage % | Hole availability % |',
              '|---|---:|---:|---:|---:|---:|']
    for name in summary['overall']:
        group = [r for r in scored if r['model'] == name]
        med = lambda key: 100*float(np.median([r[key] for r in group]))
        own = 100*float(np.median([r['own']['coverage'] for r in group]))
        lines.append(f"| {name} | {med('stereo_coverage'):.2f} | {med('common_stereo_coverage'):.2f} | "
                     f"{med('full_image_prediction_coverage'):.2f} | {own:.2f} | {med('hole_prediction_coverage'):.2f} |")
    lines += ['', 'Hole availability counts positive predictions between 1 m and the maximum depth in non-stereo regions. '
              'It is measured before production rover/range filters; it is not final usable map area.', '',
              '## Inference performance', '', report['environment']['timing_scope']+'.',
              'Models are run sequentially, with one untimed warm-up each. DA3 uses upstream automatic mixed precision; '
              'DA2 and MoGe use float32. Memory is peak allocated CUDA memory during timed inference, not total GPU reservation.', '',
              '| Model | Load s | Warm-up s | Mean s/frame | Median s/frame | P90 s/frame | Images/s | Peak GPU GiB |',
              '|---|---:|---:|---:|---:|---:|---:|---:|']
    for name, timing in report['timings'].items():
        memory = timing['peak_gpu_bytes']
        memory = f'{memory/2**30:.2f}' if memory is not None else 'N/A (CPU)'
        lines.append(f"| {name} | {timing['load_seconds']:.2f} | {timing['warmup_seconds']:.2f} | "
                     f"{timing['mean_seconds']:.3f} | {timing['median_seconds']:.3f} | "
                     f"{timing['p90_seconds']:.3f} | {timing['images_per_second']:.2f} | {memory} |")
    lines += ['', '## Per-frame accuracy and calibration', '',
              '| Sol | Frame | Variant | Calibration | Scale | Fit pixels | Test pixels | AbsRel % | RMSE m | δ1 % |',
              '|---:|---|---|---|---:|---:|---:|---:|---:|---:|']
    for row in scored:
        m = row['common']; scale = f"{row['scale']:.5g}" if row['scale'] is not None else 'N/A'
        lines.append(f"| {row['sol']} | {row['frame']} | {row['model']} | {row['alignment']} | {scale} | "
                     f"{row['calibration_pixels']:,} | {m['count']:,} | {m['absrel']*100:.2f} | "
                     f"{m['rmse_m']:.3f} | {m['delta1']*100:.2f} |")
    lines += ['', '## Skipped frames', '']
    lines += [f"- Sol {r['sol']}, {r['frame']}: {r['status']}; fit={r['fit_pixels']}, "
              f"selection={r['selection_pixels']}, test={r['test_pixels']}." for r in skipped] or ['None.']
    lines += ['', '## Metric definitions and interpretation', '',
              '- AbsRel: mean |prediction−truth| / truth. A value of 10% is relative depth error, not 90% classification accuracy.',
              '- MAE and RMSE: absolute depth error in metres; RMSE penalizes large errors more strongly.',
              '- δ1 / δ2 / δ3: fraction with max(prediction/truth, truth/prediction) < 1.25, 1.25², 1.25³.',
              '- Bias: mean prediction−truth in metres; positive means overestimated depth.',
              '- JSON/CSV also include SqRel, RMSE(log), mean |log10 error|, median absolute error and P90 relative error.',
              '- Sparse NASA stereo is a reference measurement with its own errors, especially at range. '
              'Common-valid evaluation can omit hard pixels; inspect individual coverage as well as accuracy.',
              '- Spatial blocks reduce leakage but neighboring blocks can remain correlated. Results describe these sols, not all Mars terrain.', '',
              '## Output files', '',
              '- metrics.json: all frame metrics, split sizes, aggregates, pairwise scores, configuration, environment and checkpoint SHA-256 hashes.',
              '- metrics.csv: per-frame common/own test metrics and true-depth-band metrics.',
              '- aggregate_metrics.csv: overall, per-sol and depth-band frame distributions and pooled metrics.',
              '- frame_*.png: shared-scale metric depth and held-out error panels; predictions/*.npy: native raw arrays.',
              '- summary.md: this complete report. No production artifacts are modified.', '']
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--raw-root', type=Path, default=Path('data/raw'))
    ap.add_argument('--sols', nargs='+', type=int, default=DEFAULT_SOLS, help='defaults to the eight sols in this repo data/raw')
    ap.add_argument('--da-weights', type=Path, help='optional existing local HF model directory')
    ap.add_argument('--moge-weights', type=Path, help='optional local MoGe model.pt')
    ap.add_argument('--da3-weights', type=Path, help='optional local DA3 model directory')
    ap.add_argument('--da3-model', choices=DA3_MODELS, default=DA3_MODELS[0])
    ap.add_argument('--da3-process-res', type=int, default=504, help='DA3 longest-side inference resolution')
    ap.add_argument('--moge-resolution-level', type=int, default=9, choices=range(10))
    ap.add_argument('--device', default='auto', choices=['auto', 'cpu', 'cuda'])
    ap.add_argument('--output', type=Path, default=Path('data/comparisons')/time.strftime('%Y%m%d-%H%M%S'), help='new results directory')
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--block-size', type=int, default=32)
    ap.add_argument('--max-depth', type=float, default=60)
    ap.add_argument('--offline', action='store_true', help='reuse local data and cached weights without downloads')
    ap.add_argument('--model-cache', type=Path, default=Path('data/model_cache'))
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--max-frames-per-sol', type=int, default=0, help='0 = all pairs; positive limit for a quick run')
    a = ap.parse_args()
    if a.workers < 1 or a.max_frames_per_sol < 0 or any(s < 0 for s in a.sols):
        ap.error('workers must be positive; frame limit and sols must be nonnegative')
    if a.block_size < 1 or a.max_depth <= 1 or a.da3_process_res < 14:
        ap.error('block-size must be positive, max-depth > 1 and da3-process-res >= 14')
    if a.output.exists():
        ap.error('Output directory already exists')
    import torch
    if a.device == 'auto': a.device = 'cuda' if torch.cuda.is_available() else 'cpu'
    if a.device == 'cuda' and not torch.cuda.is_available():
        ap.error('CUDA requested but unavailable; enable a GPU runtime in Colab')
    missing = [name for name in ('transformers', 'moge', 'depth_anything_3', 'matplotlib')
               if importlib.util.find_spec(name) is None]
    if missing:
        ap.error(f'Missing packages: {missing}. Install the Colab dependencies documented in docs/moge2-migration.md before downloading data.')
    print(f'[run] device={a.device}; sols={sorted(set(a.sols))}; '
          f'DA3={a.da3_model}; process_res={a.da3_process_res}; seed={a.seed}', flush=True)
    pairs = []
    for sol in sorted(set(a.sols)):
        if not a.offline:
            pairs.extend(download_sol(sol, a.raw_root, a.workers, a.max_frames_per_sol))
        else:
            found = []
            for xyz in sorted((a.raw_root/f'sol{sol}').glob('NLB_*XYZLF*.LBL')):
                ras = xyz.with_name(xyz.name.replace('XYZLF', 'RASLF'))
                if all(p.exists() for p in (ras, ras.with_suffix('.IMG'), xyz.with_suffix('.IMG'))):
                    found.append((ras, xyz))
            if not found: ap.error(f'No complete offline pairs for sol {sol}')
            pairs.extend(found[:a.max_frames_per_sol] if a.max_frames_per_sol else found)
    resolve_weights(a)
    torch.manual_seed(a.seed)
    a.output.mkdir(parents=True)
    cache = a.output/'predictions'; cache.mkdir()
    timings = {}
    for kind, path in [('da', a.da_weights), ('moge', a.moge_weights), ('da3', a.da3_weights)]:
        print(f'[model] Loading {kind} on {a.device}', flush=True)
        load_start = time.perf_counter()
        model, predict = load_model(kind, path, a.device, a.da3_process_res, a.moge_resolution_level)
        if a.device == 'cuda': torch.cuda.synchronize()
        load_seconds = time.perf_counter()-load_start
        warmup_start = time.perf_counter()
        times = []
        with torch.inference_mode():
            predict(prepare(*pairs[0], a.max_depth)[0])  # untimed warm-up
            if a.device == 'cuda':
                torch.cuda.synchronize(); torch.cuda.reset_peak_memory_stats()
            warmup_seconds = time.perf_counter()-warmup_start
            for i, pair in enumerate(pairs):
                print(f'[{kind}] {i+1}/{len(pairs)} {pair[0].stem}', flush=True)
                img = prepare(*pair, a.max_depth)[0]
                if a.device == 'cuda': torch.cuda.synchronize()
                start = time.perf_counter(); pred = predict(img)
                if a.device == 'cuda': torch.cuda.synchronize()
                times.append(time.perf_counter()-start)
                np.save(cache/f'{kind}_{i}.npy', pred)
        timings[kind] = {'seconds_per_frame': times, 'load_seconds': load_seconds, 'warmup_seconds': warmup_seconds,
                         'native_metric': getattr(predict, 'is_metric', kind == 'moge'),
                         'loaded_model_name': getattr(model, 'model_name', None),
                         'median_seconds': float(np.median(times)), 'mean_seconds': float(np.mean(times)),
                         'p90_seconds': float(np.percentile(times, 90)),
                         'images_per_second': float(len(times)/sum(times)),
                         'peak_gpu_bytes': torch.cuda.max_memory_allocated() if a.device == 'cuda' else None}
        del predict, model
        gc.collect()
        if a.device == 'cuda': torch.cuda.empty_cache()
    rows = []
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    for i, pair in enumerate(pairs):
        img, gt, valid = prepare(*pair, a.max_depth)
        da, moge, da3 = (np.load(cache/f'{k}_{i}.npy') for k in ('da', 'moge', 'da3'))
        sol = int(pair[0].parent.name.removeprefix('sol'))
        train, selection, test = splits(gt.shape, a.seed+i, a.block_size)
        common = valid & np.isfinite(da) & (da > 0) & np.isfinite(moge) & (moge > 0) & np.isfinite(da3) & (da3 > 0)
        fit, select, held = (common & s for s in (train, selection, test))
        if min(fit.sum(), select.sum(), held.sum()) < 20:
            rows.append({'frame': pair[0].stem, 'sol': sol, 'status': 'insufficient common stereo pixels',
                         'fit_pixels': int(fit.sum()), 'selection_pixels': int(select.sum()), 'test_pixels': int(held.sum())})
            print(f'[skip] sol {sol} {pair[0].stem}: fewer than 20 common pixels in a split', flush=True)
            continue
        methods = {m: fit_inverse(m, da[fit], gt[fit]) for m in ('plain', 'weighted', 'power')}
        chosen = min(methods, key=lambda m: metrics(methods[m](da), gt, select)['absrel'])
        calibration = fit | select
        da_metric = fit_inverse(chosen, da[calibration], gt[calibration])(da)
        scale = float(np.median(gt[calibration]/moge[calibration]))
        da3_scale = float(np.median(gt[calibration]/da3[calibration]))
        predictions = {'da_aligned': da_metric, 'moge_raw': moge,
                       'moge_scaled': moge*scale, 'da3_scaled': da3*da3_scale}
        if timings['da3']['native_metric']:
            predictions['da3_raw_metric'] = da3
        fig, axes = plt.subplots(2, len(predictions)+1, figsize=(4*(len(predictions)+1), 8))
        axes[0, 0].set_title(f'Sol {sol}: input RGB'); axes[1, 0].set_title('NASA stereo depth (m)')
        axes[0, 0].imshow(img, cmap='gray'); axes[1, 0].imshow(np.where(valid, gt, np.nan), vmin=0, vmax=a.max_depth)
        for j, (name, pred) in enumerate(predictions.items(), 1):
            row = {'frame': pair[0].stem, 'sol': sol, 'model': name,
                   'alignment': chosen if name == 'da_aligned' else 'median_scale' if name.endswith('_scaled') else 'none',
                   'scale': scale if name == 'moge_scaled' else da3_scale if name == 'da3_scaled' else None,
                   'calibration_pixels': int(calibration.sum()), 'test_pixels': int(held.sum()),
                   'stereo_coverage': float(valid.mean()),
                   'common_stereo_coverage': float(common.sum()/max(valid.sum(), 1)),
                   'full_image_prediction_coverage': float(np.mean(np.isfinite(pred) & (pred > 0))),
                   'common': metrics(pred, gt, held), 'own': metrics(pred, gt, valid & test),
                   'depth_ranges': {f'{lo}-{hi}': metrics(pred, gt, held & (gt >= lo) & (gt < hi))
                                    for lo, hi in depth_ranges(a.max_depth)},
                   'hole_prediction_coverage': float(np.mean((~valid) & np.isfinite(pred) & (pred > 1) & (pred < a.max_depth)) / max(np.mean(~valid), 1e-12))}
            rows.append(row)
            m = row['common']
            print(f"[accuracy] sol {sol} {i+1}/{len(pairs)} {name}: "
                  f"AbsRel={m['absrel']:.2%}, RMSE={m['rmse_m']:.3f}m, "
                  f"delta1={m['delta1']:.2%}, n={m['count']:,}", flush=True)
            axes[0, j].imshow(pred, vmin=0, vmax=a.max_depth); axes[0, j].set_title(name)
            im = axes[1, j].imshow(np.where(held, abs(pred-gt)/np.maximum(gt, 1e-6), np.nan), vmin=0, vmax=.5, cmap='magma')
            axes[1, j].set_title(f"AbsRel {m['absrel']:.1%}; delta1 {m['delta1']:.1%}")
            fig.colorbar(im, ax=axes[1, j], fraction=.046, label='Relative error (display capped at 0.5)')
        for ax in axes.flat: ax.axis('off')
        fig.tight_layout(); fig.savefig(a.output/f'frame_{i:04d}.png', dpi=130); plt.close(fig)
    summary = summarize(rows)
    report = {'config': {k: str(v) if isinstance(v, Path) else v for k, v in vars(a).items()},
              'models': {'da': 'depth-anything/Depth-Anything-V2-Small-hf',
                         'moge': 'Ruicheng/moge-2-vitl', 'da3': a.da3_model},
              'environment': environment_info(torch, a.device), 'timings': timings, 'frames': rows,
              'summary': summary, 'pairwise': pairwise_comparisons(rows),
              'inputs': [str(ras) for ras, _ in pairs],
              'weights': {k: weight_fingerprints(path) for k, path in
                          [('da', a.da_weights), ('moge', a.moge_weights), ('da3', a.da3_weights)]},
              'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (a.output/'metrics.json').write_text(json.dumps(report, indent=2, allow_nan=False))
    write_csv_reports(a.output, rows, summary)
    text = detailed_report(report)
    (a.output/'summary.md').write_text(text)
    print('\n'+text, flush=True)
    print(f'Results saved in {a.output.resolve()}', flush=True)
    if not summary['overall']:
        raise SystemExit('No frames could be evaluated; see the saved skipped-frame diagnostics.')


if __name__ == '__main__':
    main()
