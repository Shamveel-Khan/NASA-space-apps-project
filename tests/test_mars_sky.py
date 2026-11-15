"""Integrity checks for the NASA-derived Street View sky asset."""
from pathlib import Path

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SKY = ROOT / "app/world/gale_sky_pia16919.jpg"


def test_sky_asset_is_web_sized_equirectangular_image():
    with Image.open(SKY) as image:
        assert image.size == (4096, 2048)
        pixels = np.asarray(image).astype(np.int16)
    assert SKY.stat().st_size < 3_000_000
    assert np.abs(pixels[:, 0] - pixels[:, -1]).mean() < 2


def test_zenith_has_no_mosaic_placeholder_blocks():
    with Image.open(SKY) as image:
        sky = np.asarray(image)[:800]
    # The extrapolated zenith comes from measured warm sky rows and should be
    # continuous rather than retaining the source mosaic's gray missing tiles.
    assert sky.std(axis=1).mean() < 1
    assert sky[..., 0].mean() > sky[..., 2].mean()
