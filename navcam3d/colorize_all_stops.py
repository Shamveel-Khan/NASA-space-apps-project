"""Batch Mastcam color variants for registered original close-ups. See docs/batch-color.md."""
import argparse
import base64
import csv
from datetime import datetime
import hashlib
import json
import logging
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
LOG = logging.getLogger('mastcam.batch')


def atomic_json(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, indent=2))
    temporary.replace(path)


def validate(original, variant):
    LOG.debug('Validating original=%s variant=%s', original, variant)
    source = base64.b64decode((original / 'scene.b64.txt').read_bytes(), validate=True)
    other = base64.b64decode((variant / 'scene.b64.txt').read_bytes(), validate=True)
    if source != other:
        raise ValueError('Variant geometry differs from original')
    meta = json.loads((variant / 'scene.json').read_text())
    original_meta = json.loads((original / 'scene.json').read_text())
    if {k: v for k, v in meta.items() if k not in ('variant', 'colorization')} != original_meta:
        raise ValueError('Variant changed scientific metadata')
    color = meta['colorization']
    n = meta['counts']['vertices']
    if hashlib.sha256(source).hexdigest() != color['geometry_sha256']:
        raise ValueError('Source hash mismatch')
    rgb = base64.b64decode((variant / 'color.b64.txt').read_bytes(), validate=True)
    mask = base64.b64decode((variant / 'color-mask.b64.txt').read_bytes(), validate=True)
    if len(rgb) != 3 * n or len(mask) != n or any(v not in (0, 1) for v in mask):
        raise ValueError('Invalid RGB/mask dimensions')
    if sum(mask) < 25 or sum(mask) != color['colored_vertices']:
        raise ValueError('Missing or inconsistent color coverage')
    if abs(sum(mask) / n - color['vertex_coverage']) > 1e-10:
        raise ValueError('Coverage fraction mismatch')
    for i, colored in enumerate(mask):
        if not colored and rgb[3 * i:3 * i + 3] != bytes([source[6 * n + i]]) * 3:
            raise ValueError('Uncovered vertex lost original grayscale')
    for name in ('index.html', 'img/step1_photo.jpg'):
        if not (variant / name).is_file():
            raise ValueError(f'Missing {name}')
    LOG.debug('Validation passed: %s vertices, %s colored, %.2f%% coverage',
              n, color['colored_vertices'], 100 * color['vertex_coverage'])
    return meta


def register(entry, meta):
    path = ROOT / 'app/data.json'
    data = json.loads(path.read_text())
    variant = {**entry, 'id': f"{entry['sol']}-color", 'variant': 'mastcam-color',
               'label': f"Sol {entry['sol']} · Mastcam color",
               'folder': f"stops/sol{entry['sol']}-color",
               'color_coverage': meta['colorization']['vertex_coverage'],
               'color_photos': len(meta['colorization']['images'])}
    data['closeup_variants'] = [v for v in data.get('closeup_variants', [])
                                if v.get('id') != variant['id']] + [variant]
    atomic_json(path, data)
    LOG.info('Registered Sol %s in %s (%d photos)', entry['sol'], path, variant['color_photos'])


def run_command(command):
    LOG.info('Executing: %s', ' '.join(map(str, command)))
    start = time.monotonic()
    # Unbuffered Python makes subprocess progress visible immediately.
    cmd = list(command)
    if Path(cmd[0]).resolve() == Path(sys.executable).resolve() and '-u' not in cmd:
        cmd.insert(1, '-u')
    result = subprocess.run(cmd, check=False)
    LOG.info('Command finished in %.1fs (exit code %d)', time.monotonic() - start, result.returncode)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--sols', type=int, nargs='+')
    ap.add_argument('--max-images', type=int, default=20)
    ap.add_argument('--sol-window', type=int, default=5)
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--plan', action='store_true', help='print targets only, with no writes or requests')
    ap.add_argument('--offline', action='store_true')
    ap.add_argument('--resume', action='store_true', default=True, help='reuse validated variants (default)')
    ap.add_argument('--rebuild', action='store_true', help='replace selected variants only after validation')
    ap.add_argument('--verbose', '-v', action='count', default=0,
                    help='show detailed progress; use -vv for debug messages')
    ap.add_argument('--output', type=Path, default=ROOT / 'data/color_coverage' /
                    ('batch-' + datetime.now().strftime('%Y%m%d-%H%M%S')))
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose >= 2 else logging.INFO,
                        format='%(asctime)s | %(levelname)-7s | %(message)s',
                        datefmt='%H:%M:%S', stream=sys.stdout, force=True)
    if args.max_images < 1 or args.workers < 1 or args.sol_window < 0:
        ap.error('image/worker counts must be positive and window nonnegative')
    LOG.info('Repository: %s', ROOT)
    LOG.info('Options: sols=%s max_images=%d sol_window=%d workers=%d offline=%s rebuild=%s',
             args.sols or 'all', args.max_images, args.sol_window, args.workers, args.offline, args.rebuild)
    data = json.loads((ROOT / 'app/data.json').read_text())
    entries = [e for e in data['closeups'] if not e.get('variant')]
    known = {e['sol'] for e in entries}
    if args.sols and set(args.sols) - known:
        ap.error('Requested sol has no registered original reconstruction')
    entries = [e for e in entries if args.sols is None or e['sol'] in args.sols]
    LOG.info('Discovered %d original close-up(s): %s', len(entries), [e['sol'] for e in entries])
    if args.plan:
        for e in entries:
            folder = ROOT / 'app' / e['folder']
            print(json.dumps({'sol': e['sol'], 'source': str(folder),
                'source_ready': all((folder / name).is_file() for name in ('scene.json', 'scene.b64.txt')),
                'color_exists': (folder.with_name(folder.name + '-color') / 'scene.json').is_file(),
                'action': 'rebuild' if args.rebuild else 'validate/resume or generate'}))
        return
    args.output.mkdir(parents=True, exist_ok=False)
    LOG.info('Report directory: %s', args.output)
    rows = []
    cache_path = ROOT / 'data/cache/color_audit'
    import audit_color_coverage as audit
    batch_start = time.monotonic()
    for index, entry in enumerate(entries, 1):
        sol = entry['sol']
        source = ROOT / 'app' / entry['folder']
        destination = ROOT / f'app/stops/sol{sol}-color'
        temporary = destination.with_name(destination.name + '.building-' + uuid.uuid4().hex)
        row = {'sol': sol, 'status': 'failed', 'coverage': None,
               'url': f'/ground-closeups/?sol={sol}-color'}
        backup = None
        started = time.monotonic()
        LOG.info('========== [%d/%d] SOL %s ==========', index, len(entries), sol)
        LOG.info('Source: %s | Destination: %s', source, destination)
        try:
            if destination.exists() and not args.rebuild:
                LOG.info('Existing variant found; validating and re-registering')
                meta = validate(source, destination)
                register(entry, meta)
                row.update(status='reused', coverage=meta['colorization']['vertex_coverage'])
            else:
                LOG.info('Preparing camera-pose anchors and metadata cache')
                cache = audit.MetadataCache(cache_path, offline=args.offline)
                anchors = audit.prepare_scene_anchors(ROOT / 'app/stops', [sol], cache)
                LOG.info('Anchor directory: %s', anchors)
                audit_dir = args.output / f'audit-sol{sol}'
                command = [sys.executable, str(ROOT / 'navcam3d/audit_color_coverage.py'),
                           '--raw-root', str(anchors), '--sols', str(sol),
                           '--sol-window', str(args.sol_window), '--workers', str(args.workers),
                           '--output', str(audit_dir)]
                if args.offline:
                    command.append('--offline')
                LOG.info('Starting Mastcam coverage audit')
                result = run_command(command)
                report_path = audit_dir / 'coverage.json'
                if not report_path.exists():
                    raise RuntimeError(f'Audit produced no inventory (exit code {result.returncode})')
                report = json.loads(report_path.read_text())
                row['audit_incomplete'] = report['incomplete']
                matches = report['matches']
                same_station = [r for r in matches if r.get('relation') == 'same_site_drive']
                LOG.info('Audit: %d matches, %d confirmed same-station, incomplete=%s',
                         len(matches), len(same_station), report['incomplete'])
                if result.returncode:
                    LOG.warning('Audit exited with code %d; inspecting available report', result.returncode)
                if not same_station:
                    raise RuntimeError('No confirmed same-station RGB candidates; consult per-sol audit')
                command = [sys.executable, str(ROOT / 'navcam3d/colorize_stop.py'),
                           '--sol', str(sol), '--max-images', str(args.max_images),
                           '--workers', str(args.workers), '--raw-root', str(anchors),
                           '--audit', str(report_path), '--output-folder', str(temporary),
                           '--no-register']
                if args.offline:
                    command.append('--offline')
                LOG.info('Colorizing Sol %s with up to %d images', sol, args.max_images)
                result = run_command(command)
                if result.returncode:
                    raise subprocess.CalledProcessError(result.returncode, command)
                LOG.info('Validating generated color data and original geometry')
                meta = validate(source, temporary)
                if destination.exists():
                    backup = destination.with_name(destination.name + '.backup-' + uuid.uuid4().hex)
                    destination.rename(backup)
                    LOG.info('Backed up old variant: %s', backup)
                temporary.rename(destination)
                try:
                    register(entry, meta)
                except BaseException:
                    destination.rename(temporary)
                    if backup:
                        backup.rename(destination)
                    raise
                row.update(status='built', coverage=meta['colorization']['vertex_coverage'])
                if backup:
                    row['previous_variant_backup'] = str(backup)
        except (Exception, SystemExit) as error:
            row['error'] = str(error)
            LOG.exception('Sol %s failed: %s', sol, error)
        finally:
            if backup and backup.exists() and not destination.exists():
                backup.rename(destination)
            if temporary.exists():
                LOG.debug('Removing temporary folder: %s', temporary)
                shutil.rmtree(temporary)
            row['elapsed_seconds'] = round(time.monotonic() - started, 2)
            rows.append(row)
            atomic_json(args.output / 'batch.json', {'targets': rows, 'configuration': {
                'max_images': args.max_images, 'sol_window': args.sol_window, 'offline': args.offline}})
            LOG.info('SOL %s: %s | coverage=%s | elapsed=%.1fs', sol, row['status'],
                     f"{row['coverage']:.2%}" if row['coverage'] is not None else 'N/A',
                     row['elapsed_seconds'])
            LOG.info('Progress: %d/%d processed; %d built, %d reused, %d failed',
                     index, len(entries),
                     sum(r['status'] == 'built' for r in rows),
                     sum(r['status'] == 'reused' for r in rows),
                     sum(r['status'] == 'failed' for r in rows))
            print(json.dumps(row), flush=True)
    with (args.output / 'batch.csv').open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['sol', 'status', 'coverage', 'url', 'error'], extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)
    lines = ['# Batch Mastcam variants', '', '| Sol | Status | Colored vertices | Viewer |',
             '|---:|---|---:|---|']
    for row in rows:
        coverage = f"{row['coverage']:.1%}" if row['coverage'] is not None else 'N/A'
        lines.append(f"| {row['sol']} | {row['status']} | {coverage} | [Open]({row['url']}) |")
        if row.get('error'):
            lines.append(f"\nSol {row['sol']}: {row['error']}\n")
    (args.output / 'summary.md').write_text('\n'.join(lines))
    LOG.info('========== BATCH FINISHED in %.1fs ==========', time.monotonic() - batch_start)
    LOG.info('Reports: %s', args.output)
    if any(r['status'] == 'failed' or r.get('audit_incomplete') for r in rows):
        LOG.warning('At least one sol failed or had incomplete metadata; exiting with code 2')
        raise SystemExit(2)
    LOG.info('All selected sols completed successfully')


if __name__ == '__main__':
    main()
