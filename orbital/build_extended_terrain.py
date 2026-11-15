"""Fetch matching 5 km USGS elevation/color windows for the terrain page only."""
import base64
import json
import io
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from PIL import Image
import rasterio
from rasterio.enums import Resampling
from rasterio.windows import from_bounds
from scipy.ndimage import uniform_filter
from fetch_orbital import read_ifd, tag_values, get_range, make_tiff


def read_dem(src, bounds):
    """Batch contiguous TIFF strips to avoid thousands of serial HTTP requests."""
    url = src.name
    win = from_bounds(*bounds, src.transform).round_offsets().round_lengths()
    tags = read_ifd(url)
    first, count = int(win.row_off), int(win.height)
    offsets = tag_values(url, tags, 273, first, count)
    sizes = tag_values(url, tags, 279, first, count)
    output = np.empty((count, int(win.width)), dtype=np.float32)

    def block(start):
        end = min(count, start+64)
        lo, hi = offsets[start], offsets[end-1]+sizes[end-1]
        blob = get_range(url, lo, hi-1)
        chunks = [blob[offsets[i]-lo:offsets[i]-lo+sizes[i]] for i in range(start, end)]
        tif = make_tiff(src.width, end-start, chunks, {
            258: (3, [32]), 259: (3, [5]), 262: (3, [1]), 277: (3, [1]),
            278: (4, [1]), 284: (3, [1]), 339: (3, [3]),
        })
        image = Image.open(io.BytesIO(tif))
        return start, np.array(image.crop((int(win.col_off), 0, int(win.col_off+win.width), end-start)))

    starts = list(range(0, count, 64))
    print(f'Elevation transfer: {sum(sizes)/1e6:.1f} MB in {len(starts)} batches', flush=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(block, start) for start in starts]
        for done, future in enumerate(as_completed(futures), 1):
            start, rows = future.result()
            output[start:start+len(rows)] = rows
            if done % 8 == 0 or done == len(starts):
                print(f'Elevation batches: {done}/{len(starts)}', flush=True)
    output[output < -30000] = np.nan
    return np.asarray(Image.fromarray(output).resize((2500, 2500), Image.Resampling.BILINEAR))[None]

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'app/terrain-5km'
BASE = 'https://asc-pds-services.s3.us-west-2.amazonaws.com/mosaic/Mars/MSL/'


def main():
    OUT.mkdir(exist_ok=True)
    old = json.loads((ROOT / 'data/cache/window.json').read_text())
    left, top = old['dem']['x0'] - 1500, old['dem']['y0'] + 1500
    bounds = (left, top - 5000, left + 5000, top)
    sources = {
        'dem': 'MSL_Gale_DEM_Mosaic_1m_v3.tif',
        'color': 'MSL_Gale_HiRISE-LRGB_78quads_sharp_cog.tif',
    }
    arrays = {}
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR', GDAL_HTTP_TIMEOUT=120,
                      GDAL_HTTP_MAX_RETRY=4, GDAL_HTTP_MULTIRANGE='YES'):
        for kind, name in sources.items():
            cache = ROOT / 'data/cache' / f'terrain_5km_{kind}.npy'
            if cache.exists():
                arrays[kind] = np.load(cache)
                print(f'Using cached {kind}', flush=True)
                continue
            print(f'Reading {kind} source metadata…', flush=True)
            with rasterio.open(BASE + name) as src:
                window = from_bounds(*bounds, src.transform)
                if '--inspect' in sys.argv:
                    print(kind, src.shape, src.transform, window, flush=True)
                    if kind == 'dem':
                        tags = read_ifd(src.name)
                        first, count = int(window.row_off), round(window.height)
                        offsets = tag_values(src.name, tags, 273, first, count)
                        sizes = tag_values(src.name, tags, 279, first, count)
                        for start in range(0, count, 64):
                            end = min(count, start+64)
                            span = offsets[end-1]+sizes[end-1]-offsets[start]
                            if span > 10_000_000 or span <= 0:
                                print('Unusual batch', start, end, span, flush=True)
                    continue
                if window.col_off < 0 or window.row_off < 0 or window.col_off + window.width > src.width or window.row_off + window.height > src.height:
                    raise RuntimeError(f'{kind} source does not cover the requested bounds')
                size = 2500 if kind == 'dem' else 4096
                bands = [1] if kind == 'dem' else [1, 2, 3]
                print(f'Downloading 5 km {kind} window → {size}×{size}…', flush=True)
                arrays[kind] = read_dem(src, bounds) if kind == 'dem' else src.read(
                    bands, window=window, out_shape=(len(bands), size, size),
                    resampling=Resampling.bilinear, masked=True).filled(0)
                np.save(cache, arrays[kind])
                print(f'{kind} downloaded', flush=True)
    if '--inspect' in sys.argv:
        return
    dem = arrays['dem'][0].astype(np.float32)
    dem[dem < -30000] = np.nan
    low = float(np.nanmin(dem))
    valid = np.isfinite(dem)
    if np.nanmax(dem) - low >= 655.35:
        raise RuntimeError('Elevation span exceeds centimetre encoding')
    cm = np.where(valid, np.round((dem-low)*100), 65535).astype('<u2')
    (OUT / 'terrain.b64.txt').write_bytes(base64.b64encode(cm.tobytes()))
    safe = np.where(valid, dem, low)
    dy, dx = np.gradient(safe, 2)
    rough = np.sqrt(np.maximum(uniform_filter(safe**2, 3)-uniform_filter(safe, 3)**2, 0))
    hazard = np.clip(0.6*np.hypot(dx, dy)/np.tan(np.radians(30)) + 0.3*np.clip(rough/1.5, 0, 1), 0, 1)
    hazard[~valid] = 1
    (OUT / 'cost.b64.txt').write_bytes(base64.b64encode((hazard*255).astype(np.uint8).tobytes()))
    Image.fromarray(arrays['color'].transpose(1, 2, 0)).save(OUT / 'map-color.jpg', quality=90)
    (OUT / 'window.json').write_text(json.dumps({
        'size_m': 5000, 'grid_m': 2, 'cols': 2500, 'rows': 2500,
        'height_min': low, 'map_px_m': 5000/4096,
        'offset_x': 1500, 'offset_y': 1500,
        'bounds': bounds, 'sources': sources,
        'hazard_source': 'Slope and roughness approximation from USGS DEM',
    }, indent=2))
    print(f'Complete: {valid.mean():.1%} elevation coverage', flush=True)


if __name__ == '__main__':
    main()
