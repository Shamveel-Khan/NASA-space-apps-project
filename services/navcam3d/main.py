"""Internal, read-only API for real precomputed rover metadata; no inference jobs."""
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
DATASET = Path(__file__).resolve().parent / "dataset"


def read_record(name: str):
    path = DATASET / name
    if not DATASET.is_dir():
        raise HTTPException(503, "The Navcam dataset has not been staged.")
    if not path.is_file():
        raise HTTPException(404, "Unknown rover stop.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(503, "The deployed Navcam metadata could not be read.") from None


@app.get("/health")
def health():
    ready = (DATASET / "catalog.json").is_file()
    if not ready:
        raise HTTPException(503, "The Navcam dataset has not been staged.")
    return {"service": "navcam3d", "status": "ok", "mode": "precomputed-metadata"}


@app.get("/stops")
def stops():
    return read_record("catalog.json")


@app.get("/stops/{sol}")
def stop(sol: int):
    if sol < 1:
        raise HTTPException(404, "Unknown rover stop.")
    return read_record(f"sol{sol}.json")
