"""Preview/apply an offline MoGe-2 production migration, or restore its backups.

Does not install dependencies, download weights, run inference or rebuild artifacts.
--apply requires an installed MoGe source checkout at the specified commit.
"""
import argparse
import ast
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import uuid

ROOT = Path(__file__).resolve().parent.parent

DEPTH_CLASS = '''class Depth:
    """Offline Ruicheng/moge-2-vitl. Output is axial metric depth, not range."""
    def __init__(self, weights=None, device="cpu"):
        import os
        os.environ["HF_HUB_OFFLINE"] = "1"
        if not weights or not Path(weights).is_file():
            raise FileNotFoundError("--model must name a local Ruicheng/moge-2-vitl model.pt")
        from moge.model.v2 import MoGeModel
        self.device = device
        self.model = MoGeModel.from_pretrained(str(Path(weights).resolve())).to(device).eval()
        self.name = "MoGe-2 ViT-L (Ruicheng/moge-2-vitl), stereo-scaled"
        import hashlib
        self.checkpoint_sha256 = hashlib.file_digest(open(weights, "rb"), "sha256").hexdigest()

    def __call__(self, img8):
        tensor = torch.from_numpy(np.repeat(img8[None], 3, 0)).to(device=self.device, dtype=torch.float32)/255
        with torch.inference_mode():
            out = self.model.infer(tensor, resolution_level=9, use_fp16=False)
        depth = out["depth"].float().cpu().numpy()
        mask = out["mask"].cpu().numpy().astype(bool)
        return np.where(mask & np.isfinite(depth) & (depth > 0), depth, np.nan)


'''

CALIBRATION = '''        # Metric depth needs only a robust multiplicative correction.
        # The held-out stereo pixels below never participate in this fit.
        usable = fit & np.isfinite(rel) & (rel > 0)
        if usable.sum() < 100:
            raise ValueError("Insufficient valid MoGe predictions for stereo calibration")
        scale = float(np.median(stereo[usable] / rel[usable]))
        da = rel * scale
        test &= np.isfinite(da) & (da > 0)
        if test.sum() < 100:
            raise ValueError("Insufficient held-out MoGe predictions")
        p, g = da[test], stereo[test]
        m["da_absrel"] = float(np.mean(np.abs(p - g) / g))
        m["da_within25"] = float(np.mean(np.maximum(p / g, g / p) < 1.25))
        m["da_absrel_plain"] = float(np.mean(np.abs(rel[test] - g) / g))
        m["align"] = "median_metric_scale"
        m["metric_scale"] = scale
'''


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'Repository structure changed: expected one occurrence of {old[:80]!r}')
    return text.replace(old, new, 1)


def changes(a):
    path = ROOT/'navcam3d/build_stop.py'
    s = path.read_text()
    s = replace_once(s, 'from transformers import AutoImageProcessor, AutoModelForDepthEstimation\n', '')
    s = replace_once(s, 'MODEL = "depth-anything/Depth-Anything-V2-Small-hf"  # 25M parameters, Apache-2.0', 'MODEL = "Ruicheng/moge-2-vitl"')
    start, end = s.index('class Depth:'), s.index('# ---------- one photo ----------')
    s = s[:start] + DEPTH_CLASS + s[end:]
    start, end = s.index('        method, align = choose_align'), s.index('        cap = min(MAX_DEPTH')
    s = s[:start] + CALIBRATION + s[end:]
    s = replace_once(s, '(stereo < MAX_DEPTH)', '(stereo > 0) & np.isfinite(stereo) & (stereo < MAX_DEPTH)')
    s = replace_once(s, '    ap.add_argument("--model", help="fine-tuned weights from navcam3d/finetune_depth.py (default: pretrained)")',
                     '    ap.add_argument("--model", required=True, help="local Ruicheng/moge-2-vitl model.pt")\n    ap.add_argument("--device", choices=["cpu", "cuda"], default="cpu")')
    s = replace_once(s, 'depth_model = Depth(a.model)', 'depth_model = Depth(a.model, a.device)')
    s = replace_once(s, '"depth_model": depth_model.name,', '"depth_model": depth_model.name, "checkpoint_sha256": depth_model.checkpoint_sha256, "schema_version": 2,')
    s = replace_once(s, 'colorize(rel, "turbo_r", rel.min(), rel.max())', 'colorize(np.nan_to_num(rel), "turbo", 0, vmax, np.isfinite(rel))')
    s = s.replace('Depth Anything V2 Small (pretrained, no training) -> depth for every pixel, unitless.', 'MoGe-2 ViT-L -> metric axial depth plus a validity mask.')
    s = s.replace('Three fits are tried and the one that does best\n     on stereo pixels held back for checking is kept.', 'A robust median metric scale is fitted.')
    s = s.replace('Depth Anything', 'MoGe-2')
    ast.parse(s)
    result = {'navcam3d/build_stop.py': s}
    report = (ROOT/'navcam3d/stop_report.html').read_text().replace('Depth Anything V2 Small', 'MoGe-2 ViT-L').replace('Depth Anything V2', 'MoGe-2').replace('Depth Anything', 'MoGe-2')
    report = report.replace('Every pixel, but no units: near and far only.', 'Metric axial depth; invalid predictions remain masked.')
    report = report.replace('Each photo tries three ways of turning MoGe-2\'s output into metres and keeps the one that does best on a few stereo pixels it set aside.', 'Each photo uses a robust stereo-derived scale to calibrate MoGe-2 metric depth.')
    report = report.replace('lands within about 5% of stereo', 'is evaluated against held-out stereo')
    report = report.replace('One straight-line fit fails on photos that see both nearby ground and distant hills; a power-law fit, chosen automatically, fixes most of that.', 'Raw metric scale and stereo-calibrated scale are reported separately; Mars accuracy must be measured locally.')
    report = report.replace('Depth model: MoGe-2 ViT-L (Apache-2.0).', 'Depth model: Ruicheng/moge-2-vitl (see upstream license).')
    # Existing binary layout, filenames and da_* keys are retained for deployed consumers.
    result['navcam3d/stop_report.html'] = report
    req = (ROOT/'requirements.txt').read_text()
    result['requirements.txt'] = req + f'\n# Microsoft MoGe-2 inference; immutable source revision\nmoge @ git+https://github.com/microsoft/MoGe.git@{a.moge_revision}\n'
    readme = (ROOT/'README.md').read_text()
    result['README.md'] = readme + '''\n\n## MoGe-2 production migration\n\nProduction now requires a local `Ruicheng/moge-2-vitl` model.pt with\n`build_stop.py --model /absolute/path/model.pt --no-fetch SOL`. Inference is local.\nThe CPU default uses float32; CUDA can be selected explicitly. Metric axial depth\nis calibrated with a robust stereo-derived scale, and only valid predictions fill\nstereo holes. NASA camera geometry and measured stereo remain authoritative.\nEarlier Depth Anything results and notebooks above are historical experiments.\nThe legacy `da_*` JSON keys and image filenames remain compatibility aliases;\n`da_absrel_plain` now denotes raw metric-depth error. New artifacts use schema 2.\nRebuild existing stops explicitly after benchmarking; this migration does not\nrelabel or overwrite previously generated artifacts. Old DA fine-tuned weights\nare incompatible with the new builder; finetune_depth.py remains historical.\n'''
    return result


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--moge-revision', help='40-character Microsoft/MoGe commit SHA')
    ap.add_argument('--moge-source', type=Path, help='installed local Microsoft/MoGe source checkout')
    ap.add_argument('--rollback', type=Path, help='backup directory from a previous apply')
    a = ap.parse_args()
    if a.rollback:
        manifest = json.loads((a.rollback/'manifest.json').read_text())
        for name, item in manifest.items():
            path = ROOT/name
            if path.resolve().is_relative_to(ROOT) is False:
                ap.error('Invalid backup path')
            if digest(path.read_bytes()) != item['after']:
                ap.error(f'{name} changed after migration; refusing rollback')
            if digest((a.rollback/name).read_bytes()) != item['before']:
                ap.error('Backup checksum mismatch')
        for name in manifest:
            (ROOT/name).write_bytes((a.rollback/name).read_bytes())
        print('Restored migration backups'); return
    if not a.moge_revision or not __import__('re').fullmatch('[0-9a-f]{40}', a.moge_revision):
        ap.error('--moge-revision must be a full immutable commit SHA')
    try:
        planned = changes(a)
    except (ValueError, IndexError) as e:
        ap.error(str(e))
    for name, content in planned.items():
        print(''.join(difflib.unified_diff((ROOT/name).read_text().splitlines(True), content.splitlines(True), fromfile=name, tofile=name)), end='')
    if not a.apply:
        print('\nPreview only; no files changed.'); return
    if not a.moge_source:
        ap.error('--apply requires --moge-source pointing to the installed source checkout')
    revision = subprocess.check_output(['git', '-C', str(a.moge_source), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != a.moge_revision:
        ap.error('MoGe source HEAD differs from requested revision')
    if subprocess.check_output(['git', '-C', str(a.moge_source), 'status', '--porcelain'], text=True).strip():
        ap.error('MoGe source checkout has uncommitted changes')
    backup = ROOT/'data/migration_backups'/uuid.uuid4().hex
    manifest = {}
    for name, content in planned.items():
        original = (ROOT/name).read_bytes()
        target = backup/name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(original)
        manifest[name] = {'before': digest(original), 'after': digest(content.encode())}
    (backup/'manifest.json').write_text(json.dumps(manifest, indent=2))
    try:
        for name, content in planned.items(): (ROOT/name).write_text(content)
    except BaseException:
        for name in manifest: (ROOT/name).write_bytes((backup/name).read_bytes())
        raise
    print(f'\nApplied. Rollback backup: {backup}')


if __name__ == '__main__':
    main()
