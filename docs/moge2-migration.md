# Local MoGe-2 migration

Two scripts are provided. Neither has been executed as part of implementation.
Production remains on Depth Anything until the migration is explicitly applied.

## Preparation

Use the existing environment with numpy, scipy, Pillow, matplotlib, torch and
transformers. Separately install Microsoft/MoGe from a reviewed, immutable commit
compatible with your Python/PyTorch/CUDA versions. The migration pins that exact
commit in requirements.txt; it does not install packages. Latest upstream can
include MoGe-3 dependencies, so do not blindly select main.

For an offline run, provision these files locally (the comparison downloads them automatically by default):

- Depth Anything V2 Small Hugging Face snapshot directory, including processor configuration.
- A Depth Anything 3 snapshot directory (default `depth-anything/DA3-LARGE-1.1`).
- The `model.pt` checkpoint from `Ruicheng/moge-2-vitl` (not the normal variant or MoGe-1).
- Complete Navcam RAS/XYZ `.LBL` and `.IMG` pairs under `data/raw/sol<N>/`.

## Compare in Google Colab (standalone file)

Upload only `compare_depth_models.py` to Colab; no repository clone or local NASA
data is needed. Select a GPU runtime. Install dependencies in a notebook cell
(the MoGe package is Microsoft's implementation):

```python
%pip install "numpy<2" pillow matplotlib "transformers==4.57.6" huggingface_hub "opencv-python<4.12" git+https://github.com/microsoft/MoGe.git git+https://github.com/ByteDance-Seed/Depth-Anything-3.git
```

Restart the runtime if pip changes already imported packages. Keep Colab's
GPU-compatible torch build; upstream dependency compatibility must be checked if
installation requests a conflicting torch/CUDA build. DA3 declares xformers and
other dependencies; let pip resolve compatible wheels and restart the runtime
after any torch/torchvision/xformers change. Installation has not been tested here.

Quick comparison on two frames per sol:

```python
!python compare_depth_models.py --sols 1094 1098 1100 --max-frames-per-sol 2 --device cuda --output /content/comparison-quick
```

For all data matching the eight sol folders in this repository:

```python
!python compare_depth_models.py --device cuda --output /content/comparison-all
```

Default sols: 520, 521, 1037, 1083, 1094, 1098, 1100, 1400. This list is embedded
so the standalone file works without the repository. Override it with `--sols`.
NASA products download to `data/raw/sol<N>` and weights to `data/model_cache`.
Use `--raw-root` and `--model-cache` to put caches on a mounted Drive if desired.
Complete products are reused; incomplete products are retried and byte-checked.
All paired left-camera stereo frames in a sol are included, across all site/drive
positions; this is broader than the production fetcher's last-stop selection.
No rover waypoint feed is required. Permanent NASA HTTP errors fail clearly.

The script downloads three checkpoints, runs them sequentially on the local
Colab runtime, and compares them on the same held-out NASA stereo pixels:

| Model | Default checkpoint | Depth treatment |
|---|---|---|
| Depth Anything V2 Small | `depth-anything/Depth-Anything-V2-Small-hf` | Existing inverse-depth fit selection |
| MoGe-2 ViT-L | `Ruicheng/moge-2-vitl` | Raw metric depth and median stereo scale |
| Depth Anything 3 | `depth-anything/DA3-LARGE-1.1` | Direct relative depth with median stereo scale |

The DA3 default follows the [official refreshed model recommendation](https://github.com/ByteDance-Seed/Depth-Anything-3#-model-cards).
To evaluate the dedicated metric DA3 model instead:

```python
!python compare_depth_models.py --sols 1100 --da3-model depth-anything/DA3METRIC-LARGE --device cuda --output /content/comparison-metric
```

Other selectable DA3 variants: `DA3-LARGE`, `DA3MONO-LARGE`, `DA3-BASE`, `DA3-SMALL`
(use the `depth-anything/` prefix). Only one DA3 variant is loaded per run.
`--da3-process-res` controls the longest-side input resolution (default 504);
`--moge-resolution-level` controls MoGe's resolution level (default 9).
DA3 uses its [official single-view inference API](https://github.com/ByteDance-Seed/Depth-Anything-3/blob/main/docs/API.md)
with resize-only preprocessing. Depth is restored to the original image shape.
DA3's output flag determines whether raw metric results are also reported.
Raw relative DA3 outputs are saved as native arrays and are never called metres.

The full report is printed and saved in `summary.md`. It includes:

- Median per-frame accuracy, frame means/P90/worst cases, and pooled-pixel metrics.
- Per-sol, per-frame, and true-depth-band accuracy (0–5, 5–10, 10–20, 20–30, 30–45, 45–60 m by default).
- Calibrated pairwise wins, losses, ties, and mean relative-error differences.
- AbsRel, MAE, RMSE, bias, delta1/2/3, plus log errors and error percentiles in JSON/CSV.
- Individual model coverage, common-valid stereo coverage, and hole prediction availability.
- Model loading, warm-up, mean/median/P90 inference latency, throughput, and peak allocated GPU memory.
- Split counts, skipped-frame reasons, package/device versions, and checkpoint SHA-256 hashes.

`metrics.json` contains all results. `metrics.csv` contains per-frame common/own
and depth-band metrics. `aggregate_metrics.csv` contains grouped distributions
and pooled scores. `frame_*.png` shows shared-scale metric depth/error panels;
`predictions/` stores native model outputs. The results directory must be new.
Inference timing excludes PDS reading, disk writes, loading, and warm-up. DA3
uses upstream automatic mixed precision; DA2/MoGe use float32. This is not an
equal-parameter or equal-compute benchmark.

Calibration, method selection and evaluation use disjoint spatial blocks. DA2
uses inverse-depth fitting; MoGe/DA3 use direct-depth scaling. Stereo-hole
coverage measures availability, not accuracy. No fine-tuning is performed. Use
`--offline` with cached weights/data to prevent downloads, or supply
`--da-weights`, `--moge-weights`, and `--da3-weights` for explicit local checkpoints.
When supplying local DA3 weights, set `--da3-model` to the corresponding variant
so the report's requested checkpoint identity is accurate; the report also
records the actual loaded model name and file hashes.

## Preview and apply

```bash
python navcam3d/migrate_to_moge2.py --moge-revision FULL_40_CHARACTER_COMMIT_SHA
python navcam3d/migrate_to_moge2.py --moge-revision FULL_40_CHARACTER_COMMIT_SHA \
  --moge-source /sources/MoGe --apply
```

Preview prints a unified diff without writing. Apply checks the clean upstream
checkout revision, backs up all affected files, and records before/after hashes.
It modifies the production builder, report template, requirements and README.
It does not download, install dependencies, rebuild stops, or deploy anything.
Legacy JSON keys and raw-image filenames are retained for viewer compatibility;
new metadata identifies schema 2 and records checkpoint SHA-256. In schema 2,
`da_absrel_plain` means raw MoGe metric-depth error. Existing generated stops and
historical DA fine-tuning notebooks/results retain their original provenance.

Review the diff and benchmark before applying. Rebuild stops separately with the
local MoGe checkpoint and `--no-fetch`; the existing builder replaces output for
requested sols, so preserve old artifacts before that separate operation.

```bash
python navcam3d/migrate_to_moge2.py --rollback data/migration_backups/BACKUP_ID
```

Rollback refuses to overwrite files edited after migration. Backup hashes are
verified before restoring. The migration rejects an unexpected builder structure
rather than guessing how to patch it.

## Verification still required before rollout

Syntax compilation has passed. Neither script, inference nor end-to-end rebuilding
has been run. Check dependency compatibility, checkpoint identity, masked invalid
predictions, preservation of measured stereo, CAHV axial depth/unprojection,
metadata/report compatibility, and representative local stop builds. Mars imagery
is a domain shift; no improvement or performance claim is made before measuring.
