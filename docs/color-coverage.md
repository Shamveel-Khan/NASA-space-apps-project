# Audit color images around the Navcam stops

`navcam3d/audit_color_coverage.py` is a standard-library-only metadata audit. It
finds Mastcam RGB images for the sols represented in `data/raw`, at the same
rover stations and nearby stations. It does not run any depth model, download
image pixels, or alter existing data products.

```bash
python navcam3d/audit_color_coverage.py
```

An explicit search with adjustable definitions of nearby and ample:

```bash
python navcam3d/audit_color_coverage.py \
  --sols 520 521 1037 1083 1094 1098 1100 1400 \
  --sol-window 5 --radius-m 100 \
  --min-color-images 10 --min-azimuth-bins 3 \
  --output data/color_coverage/first-audit
```

Default sols are discovered from `data/raw/sol*`. For a standalone copy with a
different data location, pass `--raw-root /path/to/data/raw`. At least one local
Navcam label or `stop.json` anchor is required; unlike the depth comparison,
this audit needs your actual stations to answer the location question.

## NASA sources checked

The [current NASA PDS4 Mastcam collection](https://planetarydata.jpl.nasa.gov/img/data/msl/msl_mmm/data_MSLMST/)
contains calibrated volumes with `SURFACE/<sol>/` directories. The script
gets the actual volume and sol links from directory listings rather than
assuming a sol-to-release mapping. All calibrated volumes are checked because
old observations can be redelivered in later releases. The large collection
inventory is not downloaded.

[NASA's Mastcam RDR description](https://pds.nasa.gov/ds-view/pds/viewProfile.jsp?dsid=MSL-M-MASTCAM-4-RDR-IMG-V1.0)
describes calibrated, color-corrected and geometrically linearized products.
The script uses each product's metadata to confirm RGB, rather than assuming all
Mastcam images or all processing suffixes mean the same thing.

A representative label inspected during implementation is
[1100ML0048710040500488C00_DRCL](https://planetarydata.jpl.nasa.gov/img/data/msl/msl_mmm/data_MSLMST/volume_0011_calibrated/SURFACE/1100/1100ml0048710040500488c00_drcl.xml).
It explicitly declares RGB display channels, a decoded Bayer array, the clear
filter, three 1208×1208 bands, and 16-bit samples. It contains the actual rover
motion counter, camera model, and coordinate transforms. Its rover station is
49/2626, whereas the repo's `sol1100/stop.json` is 49/2902: sharing a sol does
not establish that the images were taken at the same rover location.

## What is counted

- One observation per capture ID, with full-size lossless products preferred to
  JPEG transmissions and only one processing variant counted. Thumbnail modes
  and grayscale transmissions are excluded.
- XML must confirm three bands, distinct red/green/blue display channels, decoded
  Bayer color, and a clear filter. Raw Bayer and multispectral filter images are
  excluded from this landscape RGB audit.
- The default minimum dimensions are 512×512, configurable with `--min-width`
  and `--min-height`. Explicit sky, Sun and calibration-target descriptions are
  excluded. Other irrelevant scenes may remain and require visual review.
- Separate counts cover the same station on the target sol, the same station
  within the sol window, and nearby stations within the window.

The same station means the same **site and drive** counter, taken from local
Navcam labels. All stations represented in a local sol are audited separately.
Missing labels can fall back to `stop.json`. Nearby distances use horizontal
telemetry offsets only within a common site frame. Cross-site matching uses
NASA's waypoint latitude/longitude, when available, with the Mars radius.
Differences between drive counter numbers are never treated as distances.

Pointing vectors are transformed from the rover frame to the site frame.
Direction candidates are Mastcam centers within `--pointing-tolerance-deg` of
at least one local Navcam center (default 30°). This is a screening hint, not
validated image overlap. Azimuth bins count different headings rather than
panorama coverage. The default ample heuristic requires ten qualifying captures
and three occupied 30° heading bins; change these thresholds for your purpose.

Counts measure **available RGB products**, not visual color richness, focus,
exposure, complete scene coverage, or registration quality. The script does not
look at image pixels. Same station and similar pointing are useful evidence but
do not establish that a color image covers the exact same terrain patch.

## Output and failure behavior

Reports are written to a new timestamped directory under `data/color_coverage`:

- `summary.md`: per-station counts, threshold verdicts, screening totals and errors.
- `coverage.json`: full metadata, positions, matching decisions and configuration.
- `images.csv`: exact IMG/XML URLs and each capture's relation to each target.
- `gallery.html`: a table of same-station/nearby candidates with NASA links. It
  has no automatically fetched images, so opening it does not download the data.

The metadata cache is under `data/cache/color_audit`. `--offline` reuses cached
metadata without network access; `--refresh` reloads cached metadata. An optional
`--waypoints /path/MSL_waypoints.json` bypasses the live waypoint feed.

Requests have retries and timeouts (`--timeout`, default 30 seconds), with four
workers by default. A failed directory or label request is reported as an
incomplete search; an incomplete result is not proof of missing imagery.
Unknown distances stay unknown. The script writes its diagnostic reports and
returns exit code 2 when the audit is incomplete. It refuses an existing output
directory. A live audit has now completed for all eight repo targets with no remaining metadata errors. See [the generated findings](../data/color_coverage/live-audit-network/feasibility.md), [the complete report](../data/color_coverage/live-audit-network/summary.md), and [decoded samples](../data/color_coverage/samples/contact_sheet.png). Generated data is gitignored.

## Getting the actual color images

Use the exact NASA `image_url` and `label_url` in the report. Download both the
IMG and its XML label. These are scientific PDS images, not JPEGs. A PDS4-capable
reader can interpret the label's array offsets, dimensions, numeric type, and
band mapping. Preserve those labels when saving local data. For 16-bit products,
choose and document a display stretch when creating PNG/JPEG previews; do not
rename an IMG file to JPG or assume it contains display-ready 8-bit RGB.

Colorizing the existing Navcam meshes is a subsequent operation: use both
camera models and poses to project into Mastcam, check visibility/occlusion,
and compare overlapping terrain. Row/column copying between the cameras is
not geometrically valid.

## Sol 1100 color reconstruction

The ground-closeups collection now has two independently selectable sol-1100
entries: the original Navcam grayscale version and a Mastcam-color variant.
Open `/ground-closeups/?sol=1100-color` to select the color version directly.
The variant keeps the original geometry and depth-source labels byte-for-byte;
17,681 of 79,466 vertices (22.2%) receive actual RGB from 20 same-station Mastcam
observations. Uncovered vertices retain their original grayscale. This is a
preview registration from camera telemetry, with a sampled heightfield
line-of-sight screen; it has not been manually refined or validated for complete
photogrammetric alignment. Its report lists all NASA source labels.

The separate assets live under `app/stops/sol1100-color/`. `color.b64.txt` is an
RGB uint8 array with three values per existing vertex; `color-mask.b64.txt`
identifies the vertices receiving RGB. Geometry remains in the unchanged scene
binary. The public `closeup_variants` registry keeps this visual alternative
out of planning-stop, hazard, and scientific-result counts. The app-data rebuild
recognizes variants and retains that separation.

To reproduce from the audit and raw Navcam labels in a fresh checkout without
an existing color-output folder:

```bash
OPENBLAS_NUM_THREADS=1 python navcam3d/colorize_stop.py --sol 1100 --max-images 20
```

This downloads selected NASA IMG files into the gitignored texture cache and
creates a separate variant. It refuses to overwrite an existing variant. It
runs no depth model. RGB previews use a shared percentile stretch and gamma;
they should not be interpreted as a newly calibrated true-color observation.
