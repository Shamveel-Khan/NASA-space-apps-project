#!/usr/bin/env python3
"""Rank Mars Curiosity sols by data richness, 3D resolution, and walk connectivity.

Usage:
    python3 tools/rank_sols.py 1 100
    python3 tools/rank_sols.py 1050 1150 --top 10
    python3 tools/rank_sols.py 1080-1115 --unbuilt-only
    python3 tools/rank_sols.py --top 10        # defaults to the active 2 km planning window
"""
import argparse
import concurrent.futures as cf
import json
import math
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
APP = ROOT / "app"
CACHE.mkdir(parents=True, exist_ok=True)

PDS_BASE = "https://planetarydata.jpl.nasa.gov/img/data/msl/MSLNAV_1XXX/"
WAYPOINTS_URL = "https://mars.nasa.gov/mmgis-maps/MSL/Layers/json/MSL_waypoints.json"
WEATHER_URL = "https://mars.nasa.gov/rss/api/?feed=weather&category=msl&feedtype=json"
R_MARS = 3396190.0


def fetch_cached_json(url, filename, max_age_days=14):
    p = CACHE / filename
    if p.exists() and time.time() - p.stat().st_mtime < max_age_days * 86400:
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "gale-marswalk-planner/1.0"})
        data = urllib.request.urlopen(req, timeout=30).read()
        p.write_bytes(data)
        return json.loads(data)
    except Exception as e:
        if p.exists():
            return json.loads(p.read_text())
        raise RuntimeError(f"Could not load {url}: {e}")


def get_pds_listing(sol, timeout=12):
    """Retrieve and cache the HTML listing for a sol from NASA PDS."""
    cache_file = CACHE / f"listing_SOL{sol:05d}.html"
    if cache_file.exists():
        return cache_file.read_text(errors="ignore")

    subfolder = "DATA_V1/" if sol <= 1869 else "DATA/"
    url = f"{PDS_BASE}{subfolder}SOL{sol:05d}/"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "gale-marswalk-planner/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode(errors="ignore")
            cache_file.write_text(content)
            return content
    except Exception:
        return ""


def count_stereo_frames(sol, site, drive, timeout=10):
    html = get_pds_listing(sol, timeout=timeout)
    if not html:
        return 0
    sd = f"{site:03d}{drive:04d}"
    names = set(re.findall(rf"NLB_\d{{9}}XYZLF{sd}NCAM\d{{5}}M1\.IMG", html))
    valid = [n for n in names if n.replace("XYZLF", "RASLF") in html]
    return len(valid)


def parse_sol_range(raw_args, default_min=0, default_max=5000):
    if not raw_args:
        return default_min, default_max
    if len(raw_args) == 1:
        arg = raw_args[0]
        if "-" in arg:
            parts = arg.split("-")
            return int(parts[0]), int(parts[1])
        return int(arg), int(arg)
    return int(raw_args[0]), int(raw_args[1])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("range", nargs="*", help="Sol range, e.g. '1 100' or '1080-1115'")
    parser.add_argument("--top", type=int, default=10, help="Number of top sols to show (default: 10)")
    parser.add_argument("--unbuilt-only", action="store_true", help="Only show sols not yet built in app/stops/")
    parser.add_argument("--fast", action="store_true", help="Skip live PDS network checks; use telemetry metrics")
    parser.add_argument("--in-window-only", action="store_true", help="Only show stops inside the 2 km planning area")
    args = parser.parse_args()

    # Load project references
    waypoints_data = fetch_cached_json(WAYPOINTS_URL, "waypoints.json")
    weather_data = fetch_cached_json(WEATHER_URL, "weather.json")

    # Load app window if exists
    app_data_path = APP / "data.json"
    win = None
    existing_closeups = {}
    if app_data_path.exists():
        app_data = json.loads(app_data_path.read_text())
        win = app_data.get("window")
        for c in app_data.get("closeups", []):
            existing_closeups[c["sol"]] = c

    # Determine sol range
    has_range_arg = len(args.range) > 0
    if has_range_arg:
        sol_min, sol_max = parse_sol_range(args.range)
    elif win:
        # Default to the range of stops inside the window
        window_sols = [s["sol"] for s in app_data.get("stops", [])]
        sol_min, sol_max = min(window_sols), max(window_sols)
    else:
        sol_min, sol_max = 1, 100

    # Index weather by sol
    weather_by_sol = {}
    for entry in weather_data.get("soles", []):
        try:
            weather_by_sol[int(entry["sol"])] = entry
        except (ValueError, KeyError):
            pass

    # Extract waypoints for the parking spot on each sol
    raw_feats = [f["properties"] for f in waypoints_data["features"] if sol_min <= f["properties"]["sol"] <= sol_max]
    if not raw_feats:
        print(f"No Curiosity stops found in waypoints feed between Sol {sol_min} and {sol_max}.")
        return

    # Group by sol and choose final parking position
    sols_dict = {}
    for p in raw_feats:
        s = p["sol"]
        if s not in sols_dict or (p["site"], p["drive"]) > (sols_dict[s]["site"], sols_dict[s]["drive"]):
            sols_dict[s] = p

    sorted_sols = sorted(sols_dict.keys())

    # Helper: calculate distance (metres) on Mars sphere
    def calc_dist_m(p1, p2):
        dlat = math.radians(p2["lat"] - p1["lat"])
        dlon = math.radians(p2["lon"] - p1["lon"])
        a = (math.sin(dlat / 2) ** 2 +
             math.cos(math.radians(p1["lat"])) * math.cos(math.radians(p2["lat"])) * math.sin(dlon / 2) ** 2)
        return 2 * R_MARS * math.asin(math.sqrt(max(0, a)))

    # Compute spatial metrics
    candidates = []
    xc = R_MARS * math.radians(win["lon"]) if win else 0
    yc = R_MARS * math.radians(win["lat"]) if win else 0
    half_size = win["size_m"] / 2 if win else 1000

    for i, sol in enumerate(sorted_sols):
        p = sols_dict[sol]
        is_built = sol in existing_closeups

        if args.unbuilt_only and is_built:
            continue

        # Check planning window
        in_window = False
        if win:
            px = R_MARS * math.radians(p["lon"])
            py = R_MARS * math.radians(p["lat"])
            in_window = (abs(px - xc) <= half_size) and (abs(py - yc) <= half_size)

        if args.in_window_only and not in_window:
            continue

        # Neighbors & Connectivity
        dist_prev = calc_dist_m(sols_dict[sorted_sols[i - 1]], p) if i > 0 else 999.0
        dist_next = calc_dist_m(p, sols_dict[sorted_sols[i + 1]]) if i + 1 < len(sorted_sols) else 999.0
        nearest_neighbor_m = min(dist_prev, dist_next)

        # Count how many other stops are within 100 m (EVA walking cluster)
        cluster_stops = sum(1 for other_sol, other_p in sols_dict.items()
                            if other_sol != sol and calc_dist_m(p, other_p) <= 100.0)

        # Terrain / tilt
        tilt = p.get("tilt") or math.hypot(p.get("roll", 0), p.get("pitch", 0))

        # Weather
        has_weather = sol in weather_by_sol and weather_by_sol[sol].get("max_temp") not in (None, "--")

        candidates.append({
            "sol": sol,
            "site": p["site"],
            "drive": p["drive"],
            "lat": p["lat"],
            "lon": p["lon"],
            "elev": p.get("elev_geoid", 0),
            "drive_m": p.get("dist_m", 0),
            "nearest_neighbor_m": nearest_neighbor_m,
            "cluster_stops": cluster_stops,
            "tilt": tilt,
            "is_built": is_built,
            "in_window": in_window,
            "has_weather": has_weather,
            "frames": existing_closeups[sol]["photos"] if is_built else None,
        })

    if not candidates:
        print("No candidates match the specified filter criteria.")
        return

    # Pre-score candidates based on spatial telemetry, connectivity, window & weather
    for c in candidates:
        nn = c["nearest_neighbor_m"]
        score_conn = 25.0 if nn <= 50 else (18.0 if nn <= 100 else (10.0 if nn <= 250 else 3.0))
        score_conn = min(25.0, score_conn + min(5.0, c["cluster_stops"] * 1.5))
        score_win = 20.0 if c["in_window"] else 5.0
        tilt = c["tilt"]
        score_terrain = 10.0 if (3.0 <= tilt <= 15.0) else (6.0 if tilt < 3.0 else 4.0)
        score_weather = 10.0 if c["has_weather"] else 3.0
        # Estimated stereo score if unbuilt
        est_frames = c["frames"] if c["frames"] is not None else 10
        score_stereo = min(35.0, (est_frames / 18.0) * 35.0)
        c["score"] = round(score_stereo + score_conn + score_win + score_terrain + score_weather, 1)

    # Sort initially to find the most promising candidates
    candidates.sort(key=lambda x: -x["score"])

    # If live checking PDS, probe only the top shortlisted candidates to keep execution fast (<10s)
    if not args.fast:
        probe_limit = min(len(candidates), max(args.top * 2, 20))
        shortlist = candidates[:probe_limit]
        unbuilt_probe = [c for c in shortlist if c["frames"] is None]
        if unbuilt_probe:
            print(f"Checking NASA PDS stereo availability for top {len(unbuilt_probe)} candidate sols...", end="", flush=True)
            with cf.ThreadPoolExecutor(max_workers=8) as ex:
                future_to_c = {
                    ex.submit(count_stereo_frames, c["sol"], c["site"], c["drive"]): c
                    for c in unbuilt_probe
                }
                for f in cf.as_completed(future_to_c):
                    c = future_to_c[f]
                    try:
                        c["frames"] = f.result()
                    except Exception:
                        c["frames"] = 0
            print(" done.\n")

            # Re-score shortlisted candidates with confirmed stereo frames
            for c in shortlist:
                frames = c["frames"] if c["frames"] is not None else 0
                score_stereo = min(35.0, (frames / 18.0) * 35.0)
                nn = c["nearest_neighbor_m"]
                score_conn = 25.0 if nn <= 50 else (18.0 if nn <= 100 else (10.0 if nn <= 250 else 3.0))
                score_conn = min(25.0, score_conn + min(5.0, c["cluster_stops"] * 1.5))
                score_win = 20.0 if c["in_window"] else 5.0
                tilt = c["tilt"]
                score_terrain = 10.0 if (3.0 <= tilt <= 15.0) else (6.0 if tilt < 3.0 else 4.0)
                score_weather = 10.0 if c["has_weather"] else 3.0
                c["score"] = round(score_stereo + score_conn + score_win + score_terrain + score_weather, 1)

            candidates.sort(key=lambda x: -x["score"])

    # Sort descending by score
    candidates.sort(key=lambda x: -x["score"])
    top_candidates = candidates[:args.top]

    # Print Formatted Results
    print("=" * 105)
    print(f" TOP {len(top_candidates)} CURIOSITY SOLS RANKED FOR GALE MARSWALK PLANNER (Sols {sol_min}–{sol_max})")
    print("=" * 105)
    header = (f"{'Rank':<5} {'Sol':<6} {'Status':<10} {'In 2km?':<8} "
              f"{'Stereo Frames / Res':<22} {'Walk Neighbor':<15} {'Tilt':<7} {'Weather':<8} {'Score':<6}")
    print(header)
    print("-" * 105)

    for idx, c in enumerate(top_candidates, 1):
        status = "Built" if c["is_built"] else "Ready"
        win_str = "YES" if c["in_window"] else "outside"
        if c["frames"] is not None:
            if c["frames"] >= 15:
                res_desc = f"{c['frames']} (360° Pano)"
            elif c["frames"] >= 8:
                res_desc = f"{c['frames']} (180° Wedge)"
            elif c["frames"] > 0:
                res_desc = f"{c['frames']} (Front View)"
            else:
                res_desc = "0 frames"
        else:
            res_desc = "Est. ~10"

        neigh_desc = f"{c['nearest_neighbor_m']:.0f} m" if c["nearest_neighbor_m"] < 900 else "Isolated"
        tilt_desc = f"{c['tilt']:.1f}°"
        weather_desc = "REMS OK" if c["has_weather"] else "--"

        row = (f"#{idx:<4} Sol {c['sol']:<4} {status:<10} {win_str:<8} "
               f"{res_desc:<22} {neigh_desc:<15} {tilt_desc:<7} {weather_desc:<8} {c['score']:<6.1f}")
        print(row)

    print("=" * 105)

    # Recommendations & copy-paste commands
    unbuilt_top = [c["sol"] for c in top_candidates if not c["is_built"] and (c["frames"] is None or c["frames"] > 0)]
    if unbuilt_top:
        print("\n Recommended command to download & build the top unbuilt stops:")
        sols_str = " ".join(map(str, unbuilt_top[:6]))
        print(f"  .venv/bin/python navcam3d/build_stop.py {sols_str}")
        print(f"  .venv/bin/python orbital/build_app_data.py\n")
    else:
        print("\nAll top recommended stops in this query are already built in the project!\n")


if __name__ == "__main__":
    main()
