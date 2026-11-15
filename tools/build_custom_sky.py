"""Blend the user's shows images into a seamless decorative sky panorama."""
from pathlib import Path
import re
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT = 4096, 2048


def main():
    paths = sorted(
        (p for p in (ROOT / 'shows').iterdir()
         if p.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp'}),
        key=lambda p: [int(part) if part.isdigit() else part.lower()
                       for part in re.split(r'(\d+)', p.name)],
    )
    if not paths:
        raise SystemExit('No sky images found in shows/')
    # Cover the full circle using every image, with soft overlapping edges.
    count = max(6, len(paths))
    strip_height, step = 1024, WIDTH / count
    tile_width = round(step * 1.54)
    total = np.zeros((strip_height, WIDTH, 3), dtype=np.float32)
    weights = np.zeros((1, WIDTH, 1), dtype=np.float32)
    for index in range(count):
        path = paths[index % len(paths)]
        with Image.open(path) as source:
            tile = np.asarray(source.convert('RGB').resize((tile_width, strip_height), Image.Resampling.LANCZOS), dtype=np.float32)
        x = (np.arange(tile_width) + round(index * step)) % WIDTH
        weight = np.sin(np.pi * (np.arange(tile_width) + 0.5) / tile_width) ** 2
        total[:, x] += tile * weight[None, :, None]
        weights[:, x] += weight[None, :, None]
    strip = total / weights
    result = np.empty((HEIGHT, WIDTH, 3), dtype=np.float32)
    result[:strip_height] = strip
    # Fade to uniform colors at the poles to avoid a pinched star pattern.
    for row in range(160):
        t = row / 160
        result[row] = strip[row] * t + strip[0].mean(axis=0) * (1-t)
    ground = strip[-1].mean(axis=0)
    for row in range(strip_height, HEIGHT):
        t = min(1, (row-strip_height) / 200)
        result[row] = strip[-1] * (1-t) + ground * t
    output = ROOT / 'app/world/custom_stars_sky.jpg'
    Image.fromarray(np.uint8(np.clip(result, 0, 255))).save(output, quality=88, optimize=True, progressive=True)
    print(f'{output}: {output.stat().st_size:,} bytes ({WIDTH}x{HEIGHT})')
    print('Included: ' + ', '.join(p.name for p in paths))


if __name__ == '__main__':
    main()
