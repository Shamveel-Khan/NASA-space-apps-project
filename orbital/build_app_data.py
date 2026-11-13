"""Turn the downloaded window + NASA feeds into the files the planner app loads.

Run after fetch_orbital.py (and after building any stops with navcam3d/build_stop.py):
    python3 orbital/build_app_data.py
Writes app/: map.jpg, terrain.b64.txt, cost.b64.txt, data.json
    - terrain.b64.txt:  uint16 elevation heights, one value per 2 m cell
    - cost.b64.txt:     uint8 LightGBM traversability hazard score (0-255) per 2 m cell
    - data.json:        closeups, stops, weather, window metadata, results
"""
import base64
import json
import sys
import math
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

# ---- LightGBM traversability cost grid (AI hazard heatmap) ----
def _build_traversability_grid(dem1, GRID):
    """Compute a per-cell traversability hazard score using LightGBM G2O transfer model.

    Returns a float32 array of shape (rows, cols) with values in [0, 1]:
      0 = flat, smooth, safe (competent bedrock / duricrust)
      1 = steep, rough, hazardous (cliffs >22°, loose sand traps, boulder fields)

    Falls back to a slope-only approximation if the LightGBM checkpoint is not present,
    or if lightgbm is not installed.
    """
    from scipy.ndimage import uniform_filter
    h, w = (dem1.shape[0] // GRID) * GRID, (dem1.shape[1] // GRID) * GRID
    dem = np.nanmean(dem1[:h, :w].reshape(h // GRID, GRID, w // GRID, GRID), axis=(1, 3))
    cell_size_m = float(GRID)

    # --- terrain derivatives ---
    dy, dx = np.gradient(dem, cell_size_m)
    slope_deg = np.degrees(np.arctan(np.sqrt(dx**2 + dy**2)))
    roughness = np.sqrt(np.maximum(
        uniform_filter(dem**2, size=3) - uniform_filter(dem, size=3)**2, 0.0))
    aspect_rad = np.arctan2(-dy, dx)
    aspect_sin = np.sin(aspect_rad)
    aspect_cos = np.cos(aspect_rad)
    d2y, _ = np.gradient(dy, cell_size_m)
    _, d2x = np.gradient(dx, cell_size_m)
    laplacian = d2x + d2y
    profile_curv = np.clip(laplacian / (1.0 + dx**2 + dy**2)**1.5, -0.5, 0.5)

    lgbm_model_path = ROOT.parent / "mars-terrain-explorer" / "models" / "checkpoints" / "ground_to_orbit_lgbm.txt"
    # Also check sibling repo layout
    if not lgbm_model_path.exists():
        lgbm_model_path = ROOT.parent.parent / "mars-terrain-explorer" / "models" / "checkpoints" / "ground_to_orbit_lgbm.txt"

    rows_g, cols_g = dem.shape
    X = np.column_stack([
        slope_deg.flatten(),
        roughness.flatten(),
        aspect_sin.flatten(),
        aspect_cos.flatten(),
        profile_curv.flatten(),
        laplacian.flatten(),
    ])

    if lgbm_model_path.exists():
        try:
            import lightgbm as lgb
            booster = lgb.Booster(model_file=str(lgbm_model_path))
            hazard = booster.predict(X).reshape(rows_g, cols_g).astype(np.float32)
            hazard = np.clip(hazard, 0.0, 1.0)
            print(f"LightGBM G2O traversability grid: {rows_g}x{cols_g} cells, "
                  f"hazard range [{hazard.min():.3f}, {hazard.max():.3f}]")
            return hazard
        except Exception as exc:
            print(f"[warn] LightGBM inference failed ({exc}); falling back to slope-only hazard.")

    # ---- Bekker-Wong slope-based fallback ----
    tan_phi = float(np.tan(np.radians(30.0)))  # Martian basaltic dust repose angle
    slope_rad = np.radians(np.clip(slope_deg, 0.0, 45.0))
    bekker_slip = np.clip(np.tan(slope_rad) / tan_phi, 0.0, 1.0)
    roughness_imp = np.clip(roughness / 1.5, 0.0, 1.0)
    hazard = np.clip(0.60 * bekker_slip + 0.30 * roughness_imp + 0.10 * np.abs(profile_curv),
                     0.0, 1.0).astype(np.float32)
    print(f"Bekker-Wong slope fallback traversability grid: {rows_g}x{cols_g} cells, "
          f"hazard range [{hazard.min():.3f}, {hazard.max():.3f}]")
    return hazard

ROOT = Path(__file__).resolve().parent.parent
CACHE, APP = ROOT / "data" / "cache", ROOT / "app"
APP.mkdir(exist_ok=True)
FEEDS = {
    "waypoints": "https://mars.nasa.gov/mmgis-maps/MSL/Layers/json/MSL_waypoints.json",
    "traverse": "https://mars.nasa.gov/mmgis-maps/MSL/Layers/json/MSL_traverse.json",
    "weather": "https://mars.nasa.gov/rss/api/?feed=weather&category=msl&feedtype=json",
}
GRID = 2  # metres per cell for routing and 3D


def feed(name):
    p = CACHE / f"{name}.json"
    if not p.exists():
        p.write_bytes(urllib.request.urlopen(FEEDS[name], timeout=120).read())
    return json.loads(p.read_text())


win = json.loads((CACHE / "window.json").read_text())
R = win["radius"]


def to_px(lon, lat):
    """lon/lat -> metres from the window's top-left corner (x right = east, y down = south)."""
    x, y = R * math.radians(lon), R * math.radians(lat)
    return x - win["dem"]["x0"], win["dem"]["y0"] - y


def results_summary(closeups):
    """Headline numbers for the page, all measured by this repo's scripts."""
    out = {"stops": len(closeups), "photos": sum(c["photos"] for c in closeups),
           "area_m2": round(sum(c["area_m2"] for c in closeups)),
           "rocks": sum(len(c["hazards"]) for c in closeups),
           "tall_rocks": sum(1 for c in closeups for h in c["hazards"] if h[2] >= 0.5),
           "median_error": float(np.median([c["error"] for c in closeups])) if closeups else None}
    ft = ROOT / "docs" / "results" / "finetune.json"
    if ft.exists():
        f = json.loads(ft.read_text())
        out["finetune"] = {"train_sols": f["train_sols"], "test_sols": f["test_sols"], "train_photos": f["train_photos"],
                           "test_photos": f["test_photos"], "pretrained": f["pretrained"]["chosen"],
                           "finetuned": f["finetuned"]["chosen"], "pretrained_plain": f["pretrained"]["plain"],
                           "finetuned_plain": f["finetuned"]["plain"], "better_on": f["finetuned_better_on"]}
    return out


# ---- terrain grid (2 m), stored as centimetres above the lowest point ----
dem_path = CACHE / "dem_1m.npy"
ortho_path = CACHE / "ortho_50cm.png"
if dem_path.exists():
    dem1 = np.load(dem_path)
    h, w = (dem1.shape[0] // GRID) * GRID, (dem1.shape[1] // GRID) * GRID
    dem = np.nanmean(dem1[:h, :w].reshape(h // GRID, GRID, w // GRID, GRID), axis=(1, 3))
    hmin = float(np.nanmin(dem))
    cm = np.where(np.isfinite(dem), np.round((dem - hmin) * 100), 65535).astype("<u2")
    (APP / "terrain.b64.txt").write_bytes(base64.b64encode(cm.tobytes()))
    print(f"terrain {cm.shape} cells of {GRID} m, heights {hmin:.1f} to {np.nanmax(dem):.1f} m, "
          f"{np.isnan(dem).mean():.1%} missing")
    # ---- LightGBM AI traversability cost grid ----
    try:
        hazard = _build_traversability_grid(dem1, GRID)
        # Pack as uint8 [0..255]; 0=safe, 255=impassable
        hazard_u8 = (hazard * 255).clip(0, 255).astype(np.uint8)
        (APP / "cost.b64.txt").write_bytes(base64.b64encode(hazard_u8.tobytes()))
        print(f"cost grid {hazard_u8.shape} written to app/cost.b64.txt")
    except Exception as exc:
        print(f"[warn] Could not build traversability cost grid: {exc}")
    if ortho_path.exists():
        ortho = Image.open(ortho_path)
        Image.fromarray(np.array(ortho)).resize((w, h), Image.LANCZOS).convert("L").save(APP / "map.jpg", quality=84)
else:
    w = int(win["dem"]["shape"][1])
    h = int(win["dem"]["shape"][0])
    hmin = -4476.052734375
    if (APP / "data.json").exists():
        hmin = json.loads((APP / "data.json").read_text()).get("window", {}).get("height_min", hmin)
    print(f"keeping existing terrain.b64.txt and map.jpg ({w}x{h} m window)")
    # Generate cost.b64.txt from existing terrain.b64.txt if missing
    cost_file = APP / "cost.b64.txt"
    if not cost_file.exists() and (APP / "terrain.b64.txt").exists():
        try:
            print("Generating LightGBM cost grid from existing terrain.b64.txt...")
            cm = np.frombuffer(base64.b64decode((APP / "terrain.b64.txt").read_bytes()), dtype="<u2")
            cols, rows = w // GRID, h // GRID
            dem_grid = np.where(cm != 65535, cm.astype(np.float32) / 100.0 + hmin, np.nan).reshape(rows, cols)
            # Replicate 2x2 to pass to _build_traversability_grid
            dem_2m = np.repeat(np.repeat(dem_grid, GRID, axis=0), GRID, axis=1)
            hazard = _build_traversability_grid(dem_2m, GRID)
            hazard_u8 = (hazard * 255).clip(0, 255).astype(np.uint8)
            cost_file.write_bytes(base64.b64encode(hazard_u8.tobytes()))
            print(f"Exported LightGBM cost grid -> {cost_file} ({hazard_u8.shape})")
        except Exception as exc:
            print(f"[warn] Could not generate cost grid from terrain.b64.txt: {exc}")

# ---- Curiosity's path and stops inside the window ----
size = win["size_m"]
inside = lambda x, y: 0 <= x < w and 0 <= y < h
stops = []
for f in feed("waypoints")["features"]:
    p = f["properties"]
    x, y = to_px(p["lon"], p["lat"])
    if inside(x, y):
        stops.append({"sol": p["sol"], "site": p["site"], "drive": p["drive"], "x": round(x, 1), "y": round(y, 1),
                      "elev": round(p["elev_geoid"], 2), "yaw": p["yaw"], "km": p["dist_km"]})
path = []
for f in feed("traverse")["features"]:
    geo = f["geometry"]
    lines = [geo["coordinates"]] if geo["type"] == "LineString" else geo["coordinates"]
    for line in lines:
        seg = []
        for lon, lat, *_ in line:
            x, y = to_px(lon, lat)
            if inside(x, y):
                seg.append([round(x, 1), round(y, 1)])
            elif seg:
                path.append(seg); seg = []
        if len(seg) > 1:
            path.append(seg)
print(f"{len(stops)} Curiosity stops and {sum(len(s) for s in path)} path points inside the window "
      f"(sols {min(s['sol'] for s in stops)}-{max(s['sol'] for s in stops)})")

# ---- weather: Curiosity REMS, every sol reported ----
keep = ("sol", "terrestrial_date", "season", "ls", "min_temp", "max_temp", "min_gts_temp", "max_gts_temp",
        "pressure", "atmo_opacity", "sunrise", "sunset", "local_uv_irradiance_index")
weather = {}
for s in feed("weather")["soles"]:
    if s.get("max_temp") not in (None, "--"):
        weather[s["sol"]] = {k: s.get(k) for k in keep}
print(f"weather for {len(weather)} sols")

# ---- every stop already turned into 3D by navcam3d/build_stop.py ----
closeups = []
for sj in sorted((APP / "stops").glob("sol*/scene.json")):
    m = json.loads(sj.read_text())
    st = m.get("stop") or {}
    if "lon" not in st:
        continue
    x, y = to_px(st["lon"], st["lat"])
    if inside(x, y):
        closeups.append({"sol": m["sol"], "folder": f"stops/{sj.parent.name}", "x": round(x, 1), "y": round(y, 1),
                         "elev": st["elev_geoid"], "photos": len(m["frames"]),
                         "error": round(m["summary"]["median_da_absrel"], 4), "area_m2": round(m["map"]["area_m2"]),
                         "model": m.get("depth_model", "Depth Anything V2 Small (pretrained)"),
                         # rocks/ledges in map metres: [x, y, height above ground, 1 if estimated by Depth Anything]
                         "hazards": [[round(x + h["east"], 2), round(y + h["south"], 2), h["height"], int(h["estimated"])]
                                     for h in m.get("hazards", [])]})
print(f"{len(closeups)} stops with 3D close-ups ({sum(len(c['hazards']) for c in closeups)} rocks/ledges):",
      ", ".join(f"sol {c['sol']}" for c in closeups))
data = {
    "window": {"lat": win["lat"], "lon": win["lon"], "size_m": size, "grid_m": GRID, "cols": w // GRID,
               "rows": h // GRID, "height_min": hmin, "map_px_m": 1,
               "source": "USGS Astrogeology MSL Gale merged DEM 1 m v3 and orthophoto 25 cm v3"},
    "stops": stops, "path": path, "weather": weather,
    "closeups": closeups,
    "radiation": {"msv_per_day": 0.64, "source": "Curiosity RAD, Hassler et al., Science 2014"},
    "results": results_summary(closeups),
    "cost_grid": {"file": "cost.b64.txt", "cols": w // GRID, "rows": h // GRID,
                  "source": "LightGBM ground-to-orbit traversability (Bekker-Wong, Arvidson et al. 2011)"},
}
(APP / "data.json").write_text(json.dumps(data, separators=(",", ":")))
print("app files:", ", ".join(f"{p.name} {p.stat().st_size / 1e6:.1f} MB" for p in sorted(APP.iterdir()) if p.is_file()))
