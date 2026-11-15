#!/usr/bin/env python3
"""Build the bundled Street View sky from NASA's PIA16919 raw-color panorama."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import tempfile
import urllib.request

import numpy as np
from PIL import Image


SOURCE_URL = (
    "https://assets.science.nasa.gov/content/dam/science/psd/mars/resources/"
    "deepzooms/5367_PIA16919raw.jpg"
)
SOURCE_SHA256 = "097a431c60fd92f72ddcfe66d93eaf9b492f44ec179b5d2b9ff2b328708694f9"
SOURCE_SIZE = (10_000, 2_616)
OUTPUT_SIZE = (4_096, 2_048)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_source(destination: Path) -> None:
    request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "marswalk-sky-builder/1.0"})
    with urllib.request.urlopen(request, timeout=180) as response, destination.open("wb") as stream:
        while chunk := response.read(1024 * 1024):
            stream.write(chunk)


def fill_missing_sky(source: np.ndarray) -> np.ndarray:
    """Replace neutral placeholder tiles in the upper mosaic with measured sky colors."""
    result = source.copy()
    upper = result[:700].astype(np.float32)
    spread = np.ptp(upper, axis=2)
    brightness = upper.mean(axis=2)
    warm = ((upper[..., 0] > upper[..., 1] + 5)
            & (upper[..., 1] > upper[..., 2] + 3)
            & (brightness > 120))

    row_colors = np.full((700, 3), np.nan, np.float32)
    for row in range(700):
        pixels = upper[row][warm[row]]
        if len(pixels) >= 32:
            row_colors[row] = np.median(pixels, axis=0)
    valid_rows = np.flatnonzero(np.isfinite(row_colors[:, 0]))
    if not len(valid_rows):
        raise RuntimeError("No valid warm sky pixels found in NASA source")
    for channel in range(3):
        row_colors[:, channel] = np.interp(np.arange(700), valid_rows, row_colors[valid_rows, channel])

    placeholder = ((spread < 20) & (brightness > 55) & (brightness < 248)) | (brightness <= 8)
    replacement = np.broadcast_to(row_colors[:, None, :], upper.shape)
    upper[placeholder] = replacement[placeholder]
    result[:700] = np.clip(upper, 0, 255).astype(np.uint8)
    return result


def make_equirectangular(source: Image.Image) -> Image.Image:
    raw = fill_missing_sky(np.asarray(source.convert("RGB")))
    # The upper 950 source rows contain the measured sky, distant Gale horizon,
    # and terrain, while excluding the large rover-deck mosaic in the foreground.
    band = Image.fromarray(raw[:950]).resize((4096, 760), Image.Resampling.LANCZOS)
    band_array = np.asarray(band).astype(np.float32)
    # Remove the remaining mast/deck sliver from the distant background by
    # interpolating the immediately adjacent measured terrain columns.
    left, right = 420, 500
    original = band_array[500:, left:right].copy()
    replacement = band_array[500:, right + 150:right + 150 + (right - left)].copy()
    horizontal = np.sin(np.linspace(0, np.pi, right - left, dtype=np.float32))[None, :, None]
    vertical = np.minimum(1, np.arange(len(original), dtype=np.float32)[:, None, None] / 50)
    blend = horizontal * vertical
    band_array[500:, left:right] = original * (1 - blend) + replacement * blend
    output = np.empty((2048, 4096, 3), np.float32)

    band_top, band_bottom = 624, 1384
    pole = np.median(band_array[0], axis=0)
    for row in range(band_top):
        mix = row / max(1, band_top - 1)
        output[row] = pole * (1 - mix) + band_array[0] * mix
    output[band_top:band_bottom] = band_array
    ground = np.median(band_array[-80:], axis=(0, 1))
    for row in range(band_bottom, 2048):
        mix = (row - band_bottom) / max(1, 2047 - band_bottom)
        output[row] = ground * (1 - 0.22 * mix)

    # Component Mastcam frames have different exposure edges in the uncaptured
    # upper sky. Derive one continuous sky profile from the brightest measured
    # pixels in each row, then blend the real horizon back in below it.
    sky_profile = np.empty((1200, 3), np.float32)
    for row in range(1200):
        pixels = output[row]
        brightness = pixels.mean(axis=1)
        sky_profile[row] = np.median(pixels[brightness >= np.percentile(brightness, 65)], axis=0)
    padded = np.pad(sky_profile, ((20, 20), (0, 0)), mode="edge")
    sky_profile = np.stack([padded[row:row + 41].mean(axis=0) for row in range(1200)])
    output[:1080] = sky_profile[:1080, None, :]
    for row in range(1080, 1200):
        mix = (row - 1080) / 119
        output[row] = sky_profile[row] * (1 - mix) + output[row] * mix

    # Blend the wrap boundary so the inward-facing sphere has no vertical seam.
    seam = 96
    for column in range(seam):
        weight = 1 - column / seam
        opposite = 4095 - column
        average = (output[:, column] + output[:, opposite]) * 0.5
        output[:, column] = output[:, column] * (1 - weight) + average * weight
        output[:, opposite] = output[:, opposite] * (1 - weight) + average * weight
    return Image.fromarray(np.clip(output, 0, 255).astype(np.uint8), "RGB")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="use an already downloaded PIA16919 JPEG")
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parents[1] / "app/world/gale_sky_pia16919.jpg")
    args = parser.parse_args()

    temporary = None
    source = args.source
    if source is None:
        temporary = tempfile.TemporaryDirectory(prefix="marswalk-sky-")
        source = Path(temporary.name) / "PIA16919raw.jpg"
        print(f"Downloading {SOURCE_URL}", flush=True)
        fetch_source(source)
    if sha256(source) != SOURCE_SHA256:
        raise RuntimeError("PIA16919 source checksum does not match the reviewed NASA asset")
    with Image.open(source) as image:
        if image.size != SOURCE_SIZE:
            raise RuntimeError(f"Expected NASA source {SOURCE_SIZE}, got {image.size}")
        sky = make_equirectangular(image)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    sky.save(args.output, "JPEG", quality=85, optimize=True, progressive=True, subsampling=1)
    print(f"Wrote {args.output} ({args.output.stat().st_size / 1_000_000:.2f} MB, {sky.size[0]}x{sky.size[1]})")
    if temporary:
        temporary.cleanup()


if __name__ == "__main__":
    main()
