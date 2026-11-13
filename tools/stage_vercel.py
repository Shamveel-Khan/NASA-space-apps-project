"""Stage existing assets for a Vercel service; never download or reconstruct terrain.

Run from any directory: python tools/stage_vercel.py app|navcam3d
Only the selected service's generated site/ or dataset/ directory is replaced.
"""
import argparse
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
PAGE_DIRS = ("mars", "gale-crater", "planning-area", "3d-terrain", "ground-closeups", "mars-street-view")


def reset_generated(path: Path):
    # Callers supply only hardcoded generated directories under services/.
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def stage_app():
    target = ROOT / "services" / "app" / "site"
    reset_generated(target)
    shutil.copy2(ROOT / "index.html", target / "index.html")
    shutil.copy2(ROOT / "LICENSE", target / "LICENSE")
    shutil.copytree(ROOT / "app", target / "app", ignore=shutil.ignore_patterns("tests", "__pycache__", "*.pyc"))
    for name in PAGE_DIRS:
        shutil.copytree(ROOT / name, target / name)
    # Public, same-origin facade path only. The private binding is read at runtime
    # by services/app/main.py and is never bundled into browser assets.
    boot = target / "app" / "ui" / "boot.js"
    boot.write_text("window.MARS_NAVCAM_API = new URL('../api/navcam/', document.baseURI).href;\n" + boot.read_text(encoding="utf-8"), encoding="utf-8")
    print("Staged the website, shipped NASA assets and routes for the app service.")


def stage_navcam():
    target = ROOT / "services" / "navcam3d" / "dataset"
    source = json.loads((ROOT / "app" / "data.json").read_text(encoding="utf-8"))
    records = []
    for stop in source["closeups"]:
        path = (ROOT / "app" / stop["folder"] / "scene.json").resolve()
        path.relative_to(ROOT / "app" / "stops")
        record = json.loads(path.read_text(encoding="utf-8"))
        if record["sol"] != stop["sol"]:
            raise ValueError(f"Stop metadata mismatch for sol {stop['sol']}")
        records.append((stop["sol"], path))
    reset_generated(target)
    (target / "catalog.json").write_text(json.dumps({"stops": source["closeups"], "results": source.get("results"), "source": "Shipped NASA-derived precomputed Navcam dataset"}), encoding="utf-8")
    for sol, path in records:
        shutil.copy2(path, target / f"sol{sol}.json")
    print(f"Staged {len(records)} original scene metadata files for the internal navcam3d service.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=("app", "navcam3d"))
    args = parser.parse_args()
    {"app": stage_app, "navcam3d": stage_navcam}[args.service]()
