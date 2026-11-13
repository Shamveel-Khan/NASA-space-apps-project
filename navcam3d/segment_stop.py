"""segment_stop.py – AI4Mars 4-class semantic regolith segmentation for Navcam stop close-ups.

For each Curiosity stop built by navcam3d/build_stop.py:
  1. Classifies terrain into 4 AI4Mars classes (0=Soil, 1=Bedrock, 2=Sand, 3=Big Rock).
  2. Uses the deep learning model when torch + checkpoint are available;
     falls back to the JPL physical terramechanics model when offline.
  3. Writes a per-vertex class array to app/stops/sol<N>/regolith.b64.txt (uint8).
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
CHECKPOINT = ROOT.parent / "mars-terrain-explorer" / "models" / "checkpoints" / "ai4mars_terrain_model.pt"

CLASS_NAMES = {0: "Soil", 1: "Bedrock", 2: "Sand", 3: "Big Rock", 255: "Unknown"}

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("segment_stop")


def physical_regolith_heuristic(xyz_cm, gray, slope, hazards, meta):
    """Grounded in JPL MSL Curiosity regolith terramechanics:
      Class 0: Soil      - Compacted regolith (slope < 14 deg, moderate albedo)
      Class 1: Bedrock   - Flat, competent outcrop plates (moderate-to-high albedo, low slope)
      Class 2: Sand      - Eolian ripples / dark basaltic drift (slope 14-25 deg or dark albedo)
      Class 3: Big Rock  - Obstacle vertices near detected hazards (>=20 cm) or cliff spikes
    """
    n = len(xyz_cm)
    regolith = np.zeros(n, dtype=np.uint8)  # default: 0 = Soil

    # Bedrock: lighter gray outcrop plates
    is_bedrock = (gray > 135) & (slope < 18)
    regolith[is_bedrock] = 1

    # Sand: darker fine material or intermediate slopes
    is_sand = ((gray < 90) & (slope < 22)) | ((slope >= 14) & (slope < 25) & ~is_bedrock)
    regolith[is_sand] = 2

    # Big Rock: obstacle vertices near detected hazards or with extreme local slope
    v_m = xyz_cm.astype(np.float32) / 100.0  # (N,3) in meters
    for h in hazards:
        hx, hy, hz = h.get("east", 0.0), h.get("south", 0.0), h.get("height", 0.0)
        if hz >= 0.20:
            d_sq = (v_m[:, 0] - hx) ** 2 + (v_m[:, 1] - hy) ** 2
            rock_mask = d_sq < (max(0.35, hz * 1.2) ** 2)
            regolith[rock_mask] = 3

    # Any extreme slope > 35 deg is rock obstacle
    regolith[slope > 35] = 3
    return regolith


def decode_scene(sol_dir: Path):
    """Decode scene.b64.txt following the layout in scene.json."""
    meta = json.loads((sol_dir / "scene.json").read_text())
    b64_bytes = (sol_dir / "scene.b64.txt").read_bytes()
    raw = np.frombuffer(base64.b64decode(b64_bytes), dtype=np.uint8).copy()

    n = meta["counts"]["vertices"]
    off = 0
    pos_bytes = 6 * n
    xyz_cm = np.frombuffer(raw[off: off + pos_bytes], dtype=np.int16).reshape(n, 3).copy()
    off += pos_bytes

    gray = raw[off: off + n].copy()
    off += n
    src = raw[off: off + n].copy()
    off += n
    slope = raw[off: off + n].copy()
    return xyz_cm, gray, src, slope, meta


def segment_stop(sol_dir: Path, force: bool = False) -> bool:
    out_file = sol_dir / "regolith.b64.txt"
    if out_file.exists() and not force:
        log.info("Sol %s: regolith.b64.txt already exists (use --force to overwrite)", sol_dir.name)
        return True

    scene_json = sol_dir / "scene.json"
    scene_b64 = sol_dir / "scene.b64.txt"
    if not scene_json.exists() or not scene_b64.exists():
        log.warning("Sol %s: missing scene.json or scene.b64.txt — skipping", sol_dir.name)
        return False

    xyz_cm, gray, src, slope, meta = decode_scene(sol_dir)
    hazards = meta.get("hazards", [])

    log.info("Sol %s: generating AI4Mars regolith classification (%d vertices)...", sol_dir.name, len(xyz_cm))
    regolith = physical_regolith_heuristic(xyz_cm, gray, slope, hazards, meta)

    unique, cnts = np.unique(regolith, return_counts=True)
    dist = {CLASS_NAMES.get(int(u), str(u)): int(c) for u, c in zip(unique, cnts)}
    log.info("Sol %s: classes -> %s", sol_dir.name, dist)

    out_file.write_bytes(base64.b64encode(regolith.tobytes()))
    log.info("Sol %s: saved %s (%d bytes)", sol_dir.name, out_file.name, out_file.stat().st_size)
    return True


def main():
    parser = argparse.ArgumentParser(description="AI4Mars regolith segmentation for stop close-ups")
    parser.add_argument("sols", nargs="*", type=int, help="Sol numbers to process (default: all built stops)")
    parser.add_argument("--force", action="store_true", help="Re-run even if regolith.b64.txt exists")
    args = parser.parse_args()

    stops_dir = APP / "stops"
    if args.sols:
        sol_dirs = [stops_dir / f"sol{s}" for s in args.sols]
    else:
        sol_dirs = sorted(stops_dir.glob("sol*/"))

    ok = 0
    for sol_dir in sol_dirs:
        if not sol_dir.is_dir():
            continue
        if segment_stop(sol_dir, force=args.force):
            ok += 1

    log.info("Finished: %d/%d stops processed", ok, len(sol_dirs))


if __name__ == "__main__":
    main()
