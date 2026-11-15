"""Download every left-Navcam stereo photo Curiosity took at one stop, with NASA's 3D (XYZ) file for each.

    python3 fetch_stop.py 1100                 # the stop where Curiosity parked at the end of sol 1100
    python3 fetch_stop.py 1100 1104 1108       # several stops
    python3 fetch_stop.py 1100 --extra-sols 2  # also photos taken there on the next 2 sols, before it drove on

How it finds them:
  1. NASA's rover location feed gives the stop's site/drive numbers for that sol (e.g. sol 1100 -> 49/2902).
  2. The sol's folder in the PDS Navcam archive is listed; every file named NLB_*XYZLF<site><drive>* is a left
     Navcam stereo frame taken at that stop, and the matching *RASLF* file is the photo itself.
  3. Each .LBL/.IMG pair is downloaded in parallel. Interrupted or stalled downloads resume where they stopped.

About 15 MB per frame, usually 5 to 20 frames per stop. Files land in data/raw/sol<N>/.
"""
import argparse
import concurrent.futures as cf
import json
import sys
import re
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
CACHE = ROOT / "data" / "cache"
PDS = "https://planetarydata.jpl.nasa.gov/img/data/msl/MSLNAV_1XXX/"
WAYPOINTS = "https://mars.nasa.gov/mmgis-maps/MSL/Layers/json/MSL_waypoints.json"


def cached(url, name, max_age_days=7, tries=4):
    """Fetch once and keep a local copy. NASA's server sometimes cuts long listings short, so retry."""
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / name
    if p.exists() and time.time() - p.stat().st_mtime < max_age_days * 86400:
        return p.read_text(errors="ignore")
    print(f"  [NASA] downloading {name} ...", end="", flush=True)
    t_start = time.time()
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "gale-marswalk-planner/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                chunks = []
                total = 0
                while True:
                    chunk = resp.read(131072)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total % 1048576 < 131072:
                        print(f" {total/1e6:.1f}MB", end="", flush=True)
                data = b"".join(chunks)
                elapsed = time.time() - t_start
                print(f" (total {len(data)/1e6:.1f} MB in {elapsed:.0f}s)", flush=True)
                tmp = p.with_name(p.name + ".tmp")
                tmp.write_bytes(data)
                tmp.rename(p)
                return data.decode(errors="ignore")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404):
                print(f"\n  [NASA] permanent HTTP {e.code} on {url}. Bypassing retries.", flush=True)
                if p.exists():
                    print(f"  [cache] falling back to existing cached copy of {name}", flush=True)
                    return p.read_text(errors="ignore")
                raise RuntimeError(f"NASA HTTP {e.code} Forbidden/Not Found for {url}") from e
            wait = 2 + 2 * k
            print(f"\n  [NASA] HTTP error {e.code} on {name}. Retrying in {wait}s...", flush=True)
            time.sleep(wait)
        except Exception as e:  # IncompleteRead, timeouts, resets
            wait = 2 + 2 * k
            print(f"\n  [NASA] stalled/failed on {name} ({type(e).__name__}). Retrying in {wait}s...", flush=True)
            time.sleep(wait)
    if p.exists():
        print(f"  [cache] falling back to existing cached copy of {name}", flush=True)
        return p.read_text(errors="ignore")
    raise RuntimeError(f"could not download {url}")


def stop_for_sol(sol):
    """Site/drive of the last place Curiosity parked on this sol, from NASA's rover location feed."""
    feats = json.loads(cached(WAYPOINTS, "waypoints.json"))["features"]
    rows = [f["properties"] for f in feats if f["properties"]["sol"] == sol]
    if not rows:
        raise SystemExit(f"NASA's feed has no stop recorded for sol {sol}. Curiosity may not have driven that sol; "
                         f"try the sol before.")
    p = max(rows, key=lambda r: (r["site"], r["drive"]))
    return p


def sol_folder(sol):
    return PDS + ("DATA_V1/" if sol <= 1869 else "DATA/") + f"SOL{sol:05d}/"


def frames_at(sol, site, drive):
    url = sol_folder(sol)
    cache_name = f"listing_SOL{sol:05d}.html"
    print(f"  [PDS] listing sol {sol} folder ({url}) ...", end="", flush=True)
    html = cached(url, cache_name, max_age_days=365)
    sd = f"{site:03d}{drive:04d}"
    names = sorted(set(re.findall(rf"NLB_\d{{9}}XYZLF{sd}NCAM\d{{5}}M1\.IMG", html)))
    matched = [(sol, n[:-4]) for n in names if n.replace("XYZLF", "RASLF") in html]
    if not matched:
        print(f" 0 stereo frames at site {site} drive {drive}", flush=True)
    else:
        print(f" {len(matched)} stereo frames found", flush=True)
    return matched


def download(url, dest, tries=4):
    """Resumable download: keeps a .part file and continues with an HTTP Range request after any stall."""
    if dest.exists():
        return dest
    part = dest.with_name(dest.name + ".part")
    for k in range(tries):
        have = part.stat().st_size if part.exists() else 0
        req = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
        try:
            with urllib.request.urlopen(req, timeout=20) as r, open(part, "ab" if have else "wb") as f:
                if have and r.status != 206:      # server ignored the range: start over
                    f.truncate(0)
                while chunk := r.read(1 << 16):
                    f.write(chunk)
            part.rename(dest)
            return dest
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404):
                print(f"  [warn] permanent HTTP {e.code} on {dest.name}, aborting retries.", flush=True)
                raise RuntimeError(f"HTTP {e.code} on {url}") from e
            wait = 1 + 2 * k
            print(f"  [warn] HTTP {e.code} on {dest.name}, retry {k+1}/{tries}", flush=True)
            time.sleep(wait)
        except Exception as e:
            print(f"  [warn] stall on {dest.name}, retry {k+1}/{tries} ({type(e).__name__})", flush=True)
            time.sleep(1 + 2 * k)
    raise RuntimeError(f"gave up on {url}")


def fetch(sol, extra_sols=0, workers=6):
    stop = stop_for_sol(sol)
    print(f"\n{'='*60}", flush=True)
    print(f"  SOL {sol}  site {stop['site']} drive {stop['drive']}", flush=True)
    print(f"  location: ({stop['lat']:.6f}, {stop['lon']:.6f})  "
          f"driven: {stop['dist_km']} km  tilt: {stop.get('tilt', '?')}°", flush=True)
    print(f"{'='*60}", flush=True)
    frames = []
    for s in range(sol, sol + 1 + extra_sols):
        frames += frames_at(s, stop["site"], stop["drive"])
    if not frames:
        raise SystemExit(f"sol {sol}: no left-Navcam stereo frames with 3D files at this stop. "
                         f"Try --extra-sols 2 or another sol.")
    out = RAW / f"sol{sol}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "stop.json").write_text(json.dumps(stop, indent=1))
    jobs = [(sol_folder(s) + stem.replace("XYZLF", k) + ext, out / (stem.replace("XYZLF", k) + ext))
            for s, stem in frames for k in ("XYZLF", "RASLF") for ext in (".LBL", ".IMG")]
    total_mb = len(frames) * 15
    print(f"  downloading {len(frames)} stereo frames ({len(jobs)} files, ~{total_mb} MB) "
          f"into {out.relative_to(ROOT)} ...", flush=True)
    t0, done = time.time(), 0
    with cf.ThreadPoolExecutor(workers) as ex:
        for _ in cf.as_completed([ex.submit(download, u, d) for u, d in jobs]):
            done += 1
            if done % 8 == 0 or done == len(jobs):
                elapsed = time.time() - t0
                rate_mb = (done / len(jobs)) * total_mb / max(elapsed, 1)
                eta = (len(jobs) - done) / max(done, 1) * elapsed
                print(f"  {done}/{len(jobs)} files  {elapsed:.0f}s elapsed  "
                      f"~{rate_mb:.1f} MB/s  ETA {eta:.0f}s", flush=True)
    print(f"  saved to {out}", flush=True)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sols", type=int, nargs="+")
    ap.add_argument("--extra-sols", type=int, default=0, help="also look in the next N sols' folders")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    failed = []
    for sol in a.sols:
        try:
            fetch(sol, a.extra_sols, a.workers)
        except (SystemExit, Exception) as e:   # one bad stop must not stop the batch
            print(f"sol {sol}: skipped ({e})", flush=True)
            failed.append(sol)
    if failed:
        print("skipped sols:", " ".join(map(str, failed)))


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)  # progress shows up live in logs
    main()
