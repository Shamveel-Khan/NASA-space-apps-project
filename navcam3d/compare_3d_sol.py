"""Side-by-side 3D Sol terrain comparison using authentic NASA PDS CAHVOR geometry.

Builds 15 cm ground-surface .glb meshes for one Sol from:
  1. Depth Anything V2 Small (current production model in build_stop.py)
  2. MoGe-2 ViT-L
  3. Depth Anything 3 (DA3-LARGE-1.1)
  4. NASA Stereo Ground Truth (XYZLF)

Reuses precomputed predictions from compare_depth_models.py when available, and
generates a synchronized multi-pane Three.js GLB viewer (compare_3d_viewer.html).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_stop  # noqa: E402
import compare_depth_models as cdm  # noqa: E402
import pds  # noqa: E402
from align import choose_align  # noqa: E402


def _project_model_cloud(
    depth_map: np.ndarray,
    img8: np.ndarray,
    cam: pds.CAHV,
    rays: np.ndarray,
    R: np.ndarray,
    t: np.ndarray,
    max_depth: float = 45.0,
) -> np.ndarray:
    """Project a single-frame metric depth map into site-frame 3D points [east, north, up, gray]."""
    d_safe = np.nan_to_num(depth_map, nan=0.0, posinf=0.0, neginf=0.0)
    P_rov = cam.C + rays * (d_safe / np.maximum(rays @ cam.A, 1e-6))[..., None]
    valid = (
        np.isfinite(depth_map)
        & (depth_map > 1.0)
        & (depth_map < max_depth)
        & ~build_stop.rover_body(P_rov)
    )
    P_site = P_rov @ R.T + t
    east = P_site[..., 1] - t[1]
    north = P_site[..., 0] - t[0]
    up = -(P_site[..., 2] - t[2])
    have = valid & (np.hypot(east, north) < build_stop.RADIUS + 2)
    sub = np.flatnonzero(have.ravel())[::3]
    gv = img8.ravel()[sub].astype(float)
    gnorm = np.clip(128 + 42 * (gv - gv.mean()) / max(gv.std(), 1e-6), 0, 255)
    return np.stack([east.ravel()[sub], north.ravel()[sub], up.ravel()[sub], gnorm], 1).astype(np.float32)


def _points_to_glb(pts: np.ndarray, out_glb: Path, mesh_name: str) -> dict[str, int]:
    """Grid site-frame points at 15 cm and export a glTF 2.0 binary (.glb) mesh."""
    grid = build_stop.GRID
    radius = build_stop.RADIUS
    ng = int(2 * radius / grid)
    ix = np.floor((pts[:, 0] + radius) / grid).astype(int)
    iy = np.floor((radius - pts[:, 1]) / grid).astype(int)
    ok = (ix >= 0) & (ix < ng) & (iy >= 0) & (iy < ng)
    cid = iy[ok] * ng + ix[ok]
    cnt = np.bincount(cid, minlength=ng * ng).reshape(ng, ng)
    gh = np.where(cnt > 0, np.bincount(cid, weights=pts[ok, 2], minlength=ng * ng).reshape(ng, ng) / np.maximum(cnt, 1), np.nan)
    gray_g = np.where(cnt > 0, np.bincount(cid, weights=pts[ok, 3], minlength=ng * ng).reshape(ng, ng) / np.maximum(cnt, 1), 128.0)

    data = np.isfinite(gh)
    _, (ny0, nx0) = ndimage.distance_transform_edt(~data, return_indices=True)
    spike = data & (np.abs(gh - ndimage.median_filter(gh[ny0, nx0], size=9)) > 0.6)
    gh[spike], data = np.nan, data & ~spike
    dist, (ny_, nx_) = ndimage.distance_transform_edt(~data, return_indices=True)
    fill = ~data & (dist * grid <= 0.6)
    gh = np.where(fill, gh[ny_, nx_], gh)
    gray_g = np.where(fill, gray_g[ny_, nx_], gray_g)

    yy, xx = np.mgrid[0:ng, 0:ng]
    ce, cn = (xx + 0.5) * grid - radius, radius - (yy + 0.5) * grid
    ok_g = (data | fill) & (np.hypot(ce, cn) <= radius)
    w_ = ndimage.gaussian_filter(ok_g.astype(float), 0.8)
    gh = ndimage.gaussian_filter(np.where(ok_g, gh, 0.0), 0.8) / np.maximum(w_, 1e-6)

    gidx = -np.ones((ng, ng), np.int64)
    gidx[ok_g] = np.arange(ok_g.sum())
    V = np.stack([ce[ok_g], gh[ok_g], -cn[ok_g]], 1)
    a, b, c, d = gidx[:-1, :-1], gidx[1:, :-1], gidx[:-1, 1:], gidx[1:, 1:]
    tris = np.concatenate([
        np.stack([a, b, c], -1)[(a >= 0) & (b >= 0) & (c >= 0)],
        np.stack([b, d, c], -1)[(b >= 0) & (d >= 0) & (c >= 0)],
    ]).astype(np.uint32)
    vgray = np.clip(np.nan_to_num(gray_g[ok_g], nan=128), 0, 255).astype(np.uint8)
    build_stop.write_glb(out_glb, V, np.repeat(vgray[:, None], 3, 1), tris, mesh_name)
    return {"vertices": int(len(V)), "triangles": int(len(tris))}


def _write_viewer(out_dir: Path, sol: int, models_meta: list[dict]) -> Path:
    """Write a synchronized 4-pane Three.js GLB comparison HTML page."""
    cards_json = json.dumps(models_meta, indent=2)
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Sol {sol} — 3D Depth Model Comparison (DA2 vs MoGe-2 vs DA3)</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #0b0f14; color: #e6ebf0; font-family: system-ui, -apple-system, sans-serif; overflow: hidden; }}
  #topbar {{ height: 44px; display: flex; align-items: center; justify-content: space-between; padding: 0 16px; border-bottom: 1px solid #243040; background: #111720; font-size: 13px; }}
  #topbar b {{ color: #e3a254; }}
  #grid {{ display: grid; grid-template-columns: 1fr 1fr; grid-template-rows: 1fr 1fr; width: 100vw; height: calc(100vh - 44px); gap: 2px; background: #243040; }}
  .pane {{ position: relative; background: #0b0f14; overflow: hidden; }}
  .hud {{ position: absolute; top: 10px; left: 12px; z-index: 10; background: rgba(11,15,20,0.86); border: 1px solid #2e3b4e; border-radius: 8px; padding: 8px 12px; pointer-events: none; max-width: 92%; }}
  .hud .title {{ font-weight: 700; font-size: 13px; color: #f3f4f6; margin-bottom: 2px; }}
  .hud .sub {{ font-family: ui-monospace, Consolas, monospace; font-size: 11px; color: #9ca3af; }}
  .hud .badge {{ display: inline-block; margin-left: 6px; padding: 1px 6px; border-radius: 4px; font-size: 10px; font-weight: 700; background: #2f9e62; color: #fff; }}
</style>
</head>
<body>
<div id="topbar">
  <div><b>Sol {sol} 3D Terrain Comparison</b> — 15 cm CAHVOR Ground Meshes (All Panes Camera-Synced)</div>
  <div>Drag: Orbit &nbsp;|&nbsp; Scroll: Zoom &nbsp;|&nbsp; Right-Drag: Pan</div>
</div>
<div id="grid"></div>
<script type="importmap">{{"imports":{{"three":"https://unpkg.com/three@0.170.0/build/three.module.js","three/addons/":"https://unpkg.com/three@0.170.0/examples/jsm/"}}}}</script>
<script type="module">
import * as THREE from 'three';
import {{ OrbitControls }} from 'three/addons/controls/OrbitControls.js';
import {{ GLTFLoader }} from 'three/addons/loaders/GLTFLoader.js';

const MODELS = {cards_json};
const grid = document.getElementById('grid');
const loader = new GLTFLoader();
const views = [];

for (const m of MODELS) {{
  const pane = document.createElement('div');
  pane.className = 'pane';
  const hud = document.createElement('div');
  hud.className = 'hud';
  const badge = m.winner ? '<span class="badge">BEST</span>' : '';
  hud.innerHTML = `<div class="title">${{m.label}}${{badge}}</div><div class="sub">${{m.subtitle}}</div>`;
  pane.appendChild(hud);
  grid.appendChild(pane);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0b0f14);
  const cam = new THREE.PerspectiveCamera(48, pane.clientWidth / pane.clientHeight, 0.1, 200);
  cam.position.set(14, 10, 14);

  const renderer = new THREE.WebGLRenderer({{ antialias: true }});
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(pane.clientWidth, pane.clientHeight);
  pane.appendChild(renderer.domElement);

  const ctrl = new OrbitControls(cam, renderer.domElement);
  ctrl.target.set(0, -1.5, 0);
  ctrl.enableDamping = true;
  ctrl.update();

  scene.add(new THREE.AmbientLight(0xffffff, 0.95));
  const dir = new THREE.DirectionalLight(0xffe7c7, 0.85);
  dir.position.set(15, 25, 10);
  scene.add(dir);
  scene.add(new THREE.GridHelper(48, 24, 0x334155, 0x1e293b));

  loader.load(m.glb, (gltf) => {{
    scene.add(gltf.scene);
  }});
  views.push({{ pane, scene, cam, renderer, ctrl }});
}}

let syncing = false;
for (const v of views) {{
  v.ctrl.addEventListener('change', () => {{
    if (syncing) return;
    syncing = true;
    for (const o of views) {{
      if (o === v) continue;
      o.cam.position.copy(v.cam.position);
      o.cam.quaternion.copy(v.cam.quaternion);
      o.ctrl.target.copy(v.ctrl.target);
      o.ctrl.update();
    }}
    syncing = false;
  }});
}}

window.addEventListener('resize', () => {{
  for (const v of views) {{
    v.cam.aspect = v.pane.clientWidth / v.pane.clientHeight;
    v.cam.updateProjectionMatrix();
    v.renderer.setSize(v.pane.clientWidth, v.pane.clientHeight);
  }}
}});

(function animate() {{
  requestAnimationFrame(animate);
  for (const v of views) {{
    v.ctrl.update();
    v.renderer.render(v.scene, v.cam);
  }}
}})();
</script>
</body>
</html>
"""
    html_path = out_dir / "compare_3d_viewer.html"
    html_path.write_text(html, encoding="utf-8")
    return html_path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sol", type=int, default=1100)
    ap.add_argument("--comparison-dir", type=Path, required=True, help="Output dir from compare_depth_models.py")
    ap.add_argument("--out-dir", type=Path, default=None)
    args = ap.parse_args()

    out_dir = args.out_dir or (args.comparison_dir / f"3d_sol{args.sol}")
    out_dir.mkdir(parents=True, exist_ok=True)
    report = json.loads((args.comparison_dir / "metrics.json").read_text())
    pred_dir = args.comparison_dir / "predictions"

    sol_tag = f"sol{args.sol}"
    sol_indices = [i for i, p in enumerate(report["inputs"]) if sol_tag in Path(p).parts]
    if not sol_indices:
        raise SystemExit(f"No frames for Sol {args.sol} found in {args.comparison_dir}")

    clouds = {"da": [], "moge": [], "da3": [], "gt": []}
    for i in sol_indices:
        ras_lbl = Path(report["inputs"][i])
        xyz_lbl = ras_lbl.with_name(ras_lbl.name.replace("RASLF", "XYZLF"))
        img, _ = pds.read_product(ras_lbl)
        xyz, e = pds.read_product(xyz_lbl)
        cam, (R, t) = pds.CAHV(e), pds.rover_to_site(e)
        P_rov = (np.moveaxis(xyz, 0, -1).astype(np.float64) - t) @ R
        _, _, stereo = cam.project(P_rov)
        valid = (np.abs(xyz).sum(0) > 0) & ~build_stop.rover_body(P_rov) & (stereo < build_stop.MAX_DEPTH)
        gt = np.where(valid, stereo, np.nan)
        img8 = build_stop.to8(img)
        rays = cam.rays(*img.shape[::-1])

        da_raw = np.load(pred_dir / f"da_{i}.npy")
        moge_raw = np.load(pred_dir / f"moge_{i}.npy")
        da3_raw = np.load(pred_dir / f"da3_{i}.npy")

        train, sel, _ = cdm.splits(gt.shape, report["config"]["seed"] + i, report["config"]["block_size"])
        common = valid & np.isfinite(da_raw) & (da_raw > 0) & np.isfinite(moge_raw) & (moge_raw > 0) & np.isfinite(da3_raw) & (da3_raw > 0)
        calib = common & (train | sel)
        if calib.sum() < 20:
            continue

        _, align_fn = choose_align(da_raw[calib], gt[calib])
        da_m = align_fn(da_raw)
        moge_m = moge_raw * float(np.median(gt[calib] / moge_raw[calib]))
        da3_m = da3_raw * float(np.median(gt[calib] / da3_raw[calib]))

        clouds["da"].append(_project_model_cloud(da_m, img8, cam, rays, R, t))
        clouds["moge"].append(_project_model_cloud(moge_m, img8, cam, rays, R, t))
        clouds["da3"].append(_project_model_cloud(da3_m, img8, cam, rays, R, t))
        clouds["gt"].append(_project_model_cloud(gt, img8, cam, rays, R, t))

    sol_summary = report["summary"]["per_sol"].get(str(args.sol), {})
    def _stat_str(key, mesh_info):
        fs = sol_summary.get(key, {}).get("frame_statistics", {})
        if not fs:
            return f"{mesh_info['triangles']:,} tris"
        return (
            f"AbsRel {fs['absrel']['median']:.2%} | "
            f"RMSE {fs['rmse_m']['median']:.2f}m | "
            f"δ1 {fs['delta1']['median']:.1%} | "
            f"{mesh_info['triangles']:,} tris"
        )

    specs = [
        ("da", "da_aligned", "Depth Anything V2 Small (Current)", True),
        ("moge", "moge_scaled", "MoGe-2 ViT-L (Scaled)", False),
        ("da3", "da3_scaled", "Depth Anything 3 Large 1.1 (Scaled)", False),
        ("gt", None, "NASA Stereo Ground Truth (XYZLF)", False),
    ]
    models_meta = []
    for key, metric_key, label, winner in specs:
        pts = np.concatenate(clouds[key], axis=0)
        glb_name = f"sol{args.sol}_{key}.glb"
        mesh_info = _points_to_glb(pts, out_dir / glb_name, f"sol_{args.sol}_{key}")
        subtitle = _stat_str(metric_key, mesh_info) if metric_key else f"Sparse stereo reference | {mesh_info['triangles']:,} tris"
        models_meta.append({"label": label, "glb": glb_name, "subtitle": subtitle, "winner": winner})
        print(f"[3d] {glb_name}: {mesh_info['vertices']:,} verts, {mesh_info['triangles']:,} tris")

    html_path = _write_viewer(out_dir, args.sol, models_meta)
    print(f"[viewer] {html_path}")


if __name__ == "__main__":
    main()
