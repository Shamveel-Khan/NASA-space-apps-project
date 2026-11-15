"""Public metadata facade. Website files have an explicit static mount and may be promoted to the CDN."""
import json
import logging
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
logger = logging.getLogger(__name__)


def navcam_response(path: str) -> Response:
    # Read the binding for each request, never at build time or in browser code.
    base = os.environ.get("NAVCAM3D_URL", "")
    parts = urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise HTTPException(503, "The Navcam service binding is unavailable.")
    request = Request(urljoin(base.rstrip("/") + "/", path), headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=8) as upstream:
            payload = upstream.read(2 * 1024 * 1024 + 1)
        if len(payload) > 2 * 1024 * 1024:
            raise ValueError("Metadata response exceeds the allowed size")
        json.loads(payload)  # Return only valid JSON from the metadata service.
    except HTTPError as error:
        if error.code == 404:
            raise HTTPException(404, "This rover stop is not in the deployed dataset.") from None
        logger.warning("Navcam metadata request failed with HTTP %s", error.code)
        raise HTTPException(502, "The Navcam metadata service could not complete the request.") from None
    except (URLError, TimeoutError, OSError, ValueError):
        # Do not log the private binding URL or any credentials it may contain.
        logger.warning("Navcam metadata service is unavailable")
        raise HTTPException(502, "Navcam metadata is temporarily unavailable.") from None
    return Response(payload, media_type="application/json", headers={"Cache-Control": "no-store"})


@app.get("/api/navcam/stops")
def stops():
    return navcam_response("stops")


@app.get("/api/navcam/stops/{sol}")
def stop(sol: int):
    if sol < 1:
        raise HTTPException(404, "Unknown rover stop.")
    return navcam_response(f"stops/{sol}")


@app.get("/api/health")
def health():
    return {"service": "app", "status": "ok", "navcam_binding_configured": bool(os.environ.get("NAVCAM3D_URL"))}


# Keep API routes above this mount. Directory HTML resolution serves / and all
# physical exploration routes, including when CDN promotion is unavailable.
SITE = Path(__file__).resolve().parent / "site"
if not SITE.is_dir():
    SITE = Path(__file__).resolve().parents[2]  # Local checkout, before deployment staging.
app.mount(
    "/",
    StaticFiles(directory=str(SITE), html=True),
    name="website",
)
