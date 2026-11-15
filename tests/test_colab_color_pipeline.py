"""Offline checks for the self-contained Colab color pipeline."""
import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("colab_color_pipeline", ROOT / "tools/colab_color_pipeline.py")
PIPELINE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PIPELINE)


def test_defaults_target_colab_and_rank_fourteen_sols():
    args = PIPELINE.parse_args([])
    assert args.repo_dir == Path("/content/gale-marswalk-planner")
    assert args.output_dir == Path("/content/color_coverage")
    assert (args.top, args.shortlist_multiplier, args.max_images, args.workers) == (14, 4, 20, 2)


def test_embedded_payload_checksum_and_required_files(tmp_path):
    compressed = base64.b64decode(PIPELINE.EMBEDDED_PIPELINE_B64, validate=True)
    assert hashlib.sha256(compressed).hexdigest() == PIPELINE.PIPELINE_PAYLOAD_SHA256
    PIPELINE.install_embedded_pipeline(tmp_path)
    required = {
        "align.py", "audit_color_coverage.py", "build_stop.py", "colorize_all_stops.py",
        "colorize_stop.py", "fetch_stop.py", "pds.py", "stop_report.html",
    }
    assert required == {path.name for path in (tmp_path / "navcam3d").iterdir()}


def test_safe_extract_rejects_parent_traversal(tmp_path):
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as archive:
        info = tarfile.TarInfo("../outside.txt")
        info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(RuntimeError, match="Unsafe embedded path"):
        PIPELINE.safe_extract(payload.getvalue(), tmp_path)


def test_stereo_progress_is_visible_and_result_order_is_stable(monkeypatch, tmp_path, capsys):
    rows = [{"sol": 12}, {"sol": 7}, {"sol": 20}]
    monkeypatch.setattr(PIPELINE, "stereo_count", lambda row, cache: int(row["sol"]) % 5)
    assert PIPELINE.check_stereo_counts(rows, tmp_path, workers=2) == [2, 2, 0]
    output = capsys.readouterr().out
    assert "Navcam [1/3]" in output
    assert "stereo frame(s)" in output


def test_variant_validation_preserves_geometry_and_gray_fallback(tmp_path):
    original, variant = tmp_path / "original", tmp_path / "variant"
    original.mkdir(); variant.mkdir()
    vertices, faces = 30, 0
    gray = bytes(range(vertices))
    blob = bytes(vertices * 6) + gray + bytes(vertices) + bytes(vertices)
    original_meta = {"sol": 1, "counts": {"vertices": vertices, "faces": faces}}
    colorization = {
        "asset": "color.b64.txt",
        "mask_asset": "color-mask.b64.txt",
        "geometry_sha256": hashlib.sha256(blob).hexdigest(),
        "colored_vertices": 25,
        "vertex_coverage": 25 / vertices,
        "images": ["Mastcam"],
    }
    color_meta = {**original_meta, "variant": "mastcam-color", "colorization": colorization}
    for folder, meta in ((original, original_meta), (variant, color_meta)):
        (folder / "scene.json").write_text(json.dumps(meta))
        (folder / "scene.b64.txt").write_bytes(base64.b64encode(blob))
    rgb = bytes([200, 100, 50]) * 25 + b"".join(bytes([value]) * 3 for value in gray[25:])
    (variant / "color.b64.txt").write_bytes(base64.b64encode(rgb))
    (variant / "color-mask.b64.txt").write_bytes(base64.b64encode(bytes([1]) * 25 + bytes(5)))
    assert PIPELINE.validate_variant(original, variant)["colorization"]["colored_vertices"] == 25


def test_result_totals_keep_finetune_evidence():
    data = {
        "closeups": [
            {"photos": 2, "area_m2": 10, "error": 0.1, "hazards": [[0, 0, 0.6, 1]]},
            {"photos": 3, "area_m2": 21, "error": 0.2, "hazards": [[0, 0, 0.2, 1]]},
        ],
        "results": {"finetune": {"better_on": 25}},
    }
    PIPELINE.refresh_result_totals(data)
    assert data["results"] == {
        "stops": 2, "photos": 5, "area_m2": 31, "rocks": 2, "tall_rocks": 1,
        "median_error": pytest.approx(0.15), "finetune": {"better_on": 25},
    }


def test_package_is_a_direct_repo_overlay(tmp_path):
    repo, reports = tmp_path / "repo", tmp_path / "repo/reports"
    for name in ("sol9", "sol9-color"):
        folder = repo / "app/stops" / name
        folder.mkdir(parents=True)
        (folder / "asset.txt").write_text(name)
    (repo / "app/data.json").write_text("{}")
    reports.mkdir(parents=True)
    (reports / "ranking.json").write_text("[]")
    output = tmp_path / "color_coverage"
    args = SimpleNamespace(output_dir=output, overwrite_output=False, repo_url="example.invalid/repo",
                           revision=None, top=1, sol_min=None, sol_max=None, max_images=20,
                           sol_window=5, workers=2)
    row = {"sol": 9, "rank": 1, "score": 99, "mastcam_same_station": 20,
           "actual_color_coverage": 0.5, "actual_color_images": 10}
    archive = PIPELINE.package(repo, [row], [row], args, reports)
    assert archive == output.with_suffix(".zip")
    assert (output / "app/data.json").is_file()
    assert (output / "app/stops/sol9-color/asset.txt").read_text() == "sol9-color"
    assert json.loads((output / "manifest.json").read_text())["sols"][0]["sol"] == 9
