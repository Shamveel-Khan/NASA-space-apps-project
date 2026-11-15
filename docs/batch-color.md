# Mastcam color for all existing close-ups

Implementation only: the new batch workflow, modified scripts, and new tests
have not been executed or syntax-checked during this change. No imagery was
downloaded and no generated scene or viewer registry was modified by this change.

## Run from the repository root

Use the existing Python environment with numpy, scipy, and Pillow. No GPU or
model weights are needed. Preview the registered targets first:

```bash
.venv/bin/python navcam3d/colorize_all_stops.py --plan
```

Generate alternatives for every registered original close-up:

```bash
OPENBLAS_NUM_THREADS=1 .venv/bin/python navcam3d/colorize_all_stops.py
```

Currently this means 14 sols: 1037, 1083, 1085, 1087, 1093, 1094, 1098, 1100,
1104, 1106, 1107, 1108, 1110, 1112. It discovers the registry at runtime, so new
original close-ups are included automatically. Valid existing variants, including
sol 1100, are preserved and re-registered without rebuilding.

The runner downloads only source Navcam labels needed for camera poses, then
Mastcam metadata and selected color images. It does not rebuild Navcam geometry
or run depth estimation. Each sol gets its own audit directory. Original meshes
and scientific metadata must match before a variant can be published. Outputs
are built in temporary folders. Existing variants are replaced only with
`--rebuild`; prior variants are retained in backup folders when replaced.

After an interruption, repeat the command. Completed variants are validated and
reused; completed image downloads are reused and partial downloads resume where
NASA supports HTTP Range. Batch reports checkpoint after each sol. Temporary
folders from a forcefully killed process may remain, but are not registered as
variants. Inspect/remove them manually if disk space is needed.

To limit a run:

```bash
OPENBLAS_NUM_THREADS=1 .venv/bin/python navcam3d/colorize_all_stops.py --sols 1094 1100 --max-images 20 --workers 4
```

For a completely cached run add `--offline`. It fails a sol when required labels
or images are absent; it will not silently access the network.

Reports live in a new timestamped directory under `data/color_coverage`, or the
new directory supplied with `--output`. `batch.json` is updated after each sol;
`batch.csv` and `summary.md` are produced at completion. Exit code 2 means at
least one sol failed or used an incomplete metadata audit; consult the per-sol
reports. Successful variants remain available. Unknown coverage is not treated
as evidence that imagery does not exist.

Serve the repo root:

```bash
python3 -m http.server 8000
```

Open `/ground-closeups/`, choose a “Mastcam color” entry, or use
`http://localhost:8000/ground-closeups/?sol=1100-color`. Original grayscale entries
remain separately selectable. Color alternatives do not duplicate planning stops
or hazards. Do not run multiple batch/single-sol writers concurrently: registry
updates are atomic, but concurrent independent writers are not coordinated.

## Increase real color coverage

Start by increasing the source-image limit and search window, then explicitly
rebuild selected variants:

```bash
OPENBLAS_NUM_THREADS=1 .venv/bin/python navcam3d/colorize_all_stops.py --sols 1100 --max-images 60 --sol-window 15 --rebuild
```

- `--max-images 60` permits more views beyond the default 20. Selection stops
  when no remaining image adds at least 25 projected visible vertices, so a
  higher cap is not a guarantee of increased coverage.
- `--sol-window 15` searches additional days at the exact same rover station.
  Images from another position are not automatically used merely because the
  rover was nearby. Larger windows cost metadata requests, disk space and time.
- Check each audit: more images help only if they point at previously uncovered
  terrain. Broad Mastcam-left views and additional useful pointing directions
  can help more than many narrow views of one rock.
- The same-station restriction and occlusion screen protect geometric validity.
  Do not disable them to make the percentage look larger. Nearby-station texture
  projection requires a separately validated camera-pose/registration workflow.
- Telemetry projection and a sampled heightfield screen remain approximate.
  Improving camera registration and visibility testing may improve usable
  coverage, but requires additional implementation and validation.
- No genuine image can supply color to terrain it never observed. Such vertices
  intentionally remain grayscale. Artificial tinting or colorization is not
  measured Mastcam coverage.

Compare actual `vertex_coverage` in scene metadata and batch reports before and
after rebuilding. Sol 1100's existing 22.2% result is a baseline, not a maximum
or a guarantee for other stops. Display stretch changes brightness/appearance,
not the fraction of vertices with real color measurements.

## Optional verification, to run yourself

```bash
.venv/bin/python -m pytest -q tests/test_color_batch.py tests/test_colorize_stop.py tests/test_color_coverage.py
node --test app/tests/*.test.js
```

These commands were documented, not executed during implementation.
