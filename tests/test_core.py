"""Fast checks of the maths, no downloads and no AI model needed:  pytest -q"""
import io
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "navcam3d"), str(ROOT / "orbital")]
import pds  # noqa: E402
from align import choose_align, find_hazards, fit_align  # noqa: E402
from fetch_orbital import make_tiff  # noqa: E402
from finetune_depth import validate_splits  # noqa: E402
import urllib.error
import fetch_stop  # noqa: E402
from build_stop import Depth  # noqa: E402


# ---------- depth scaling ----------
def test_plain_fit_recovers_inverse_depth_relation():
    z = np.linspace(2, 20, 500)
    r = 3.0 / z + 0.1                       # model output = s * (1/z) + t
    assert np.allclose(fit_align("plain", r, z)(r), z, rtol=1e-6)


def test_power_fit_wins_when_relation_is_a_power_law():
    z = np.linspace(2, 60, 2000)
    r = 5.0 * z ** -0.7
    method, f = choose_align(r, z)
    assert method == "power"
    assert np.allclose(f(r), z, rtol=1e-6)


# ---------- hazards ----------
def test_finds_a_40cm_rock_and_ignores_a_10cm_bump():
    cell = 0.15
    h = np.zeros((120, 120))
    h[40:44, 40:44] = 0.40                  # rock, 60 cm across
    h[80:84, 80:84] = 0.10                  # pebble
    found = find_hazards(h, np.ones_like(h, bool), cell)
    assert len(found) == 1
    r, c, height, area = found[0]
    assert 40 <= r <= 44 and 40 <= c <= 44 and height > 0.3 and area == pytest.approx(16 * cell * cell)


# ---------- camera model and frames ----------
class _Cam(pds.CAHV):
    def __init__(self):
        self.C = np.array([0.5, 0.2, -1.9])
        self.A = np.array([0.9, 0.1, 0.42]); self.A /= np.linalg.norm(self.A)
        self.H = np.array([200.0, 1300.0, 50.0])
        self.V = np.array([100.0, 10.0, 1300.0])


def test_ray_through_projected_pixel_hits_the_point_again():
    cam = _Cam()
    P = np.array([6.0, 1.5, 0.3])
    u, v, depth = cam.project(P)
    ray = cam.rays(int(u) + 2, int(v) + 2)[int(round(v)), int(round(u))]
    back = cam.C + ray * depth / (ray @ cam.A)
    assert np.linalg.norm(back - P) < 0.02  # within 2 cm after rounding to the nearest pixel


def test_quaternion_gives_a_proper_rotation():
    q = np.array([0.332161, -0.0754662, -0.0622262, 0.938137]); q /= np.linalg.norm(q)
    Rm = pds.quat_to_matrix(q)
    assert np.allclose(Rm @ Rm.T, np.eye(3), atol=1e-9) and np.linalg.det(Rm) == pytest.approx(1)


# ---------- NASA PDS3 file reading ----------
def test_reads_a_pds3_label_and_big_endian_image(tmp_path):
    data = np.arange(6, dtype=">i2").reshape(2, 3)
    label = ("PDS_VERSION_ID = PDS3\nRECORD_BYTES = 64\n^IMAGE = (\"T.IMG\",2)\n/* comment */\n"
             "OBJECT = IMAGE\n  LINES = 2\n  LINE_SAMPLES = 3\n  BANDS = 1\n  SAMPLE_TYPE = MSB_INTEGER\n"
             "  SAMPLE_BITS = 16\nEND_OBJECT = IMAGE\nEND\n")
    (tmp_path / "T.LBL").write_text(label)
    (tmp_path / "T.IMG").write_bytes(b"\0" * 64 + data.tobytes())
    arr, entries = pds.read_product(tmp_path / "T.LBL")
    assert np.array_equal(arr, data.astype(np.float32))
    assert pds.get(entries, "LINES", "IMAGE") == "2"


# ---------- range-read TIFF rebuilding ----------
def test_synthetic_tiff_round_trip_uncompressed_strips():
    rows = np.random.default_rng(0).random((4, 7)).astype("<f4")
    tif = make_tiff(7, 4, [r.tobytes() for r in rows], {258: (3, [32]), 259: (3, [1]), 262: (3, [1]),
                                                        277: (3, [1]), 278: (4, [1]), 284: (3, [1]), 339: (3, [3])})
    assert np.array_equal(np.array(Image.open(io.BytesIO(tif))), rows)


# ---------- sol split leakage prevention ----------
def test_valid_disjoint_splits_pass():
    assert validate_splits(train_sols=[520, 700], test_sols=[1094, 1098], val_sols=[1100]) is True


def test_train_test_overlap_raises_error():
    with pytest.raises(ValueError, match="PROHIBITED SPLIT LEAKAGE.*both --train and --test"):
        validate_splits(train_sols=[520, 1400], test_sols=[1400, 1037])


def test_train_val_overlap_raises_error():
    with pytest.raises(ValueError, match="PROHIBITED SPLIT LEAKAGE.*both --train and validation"):
        validate_splits(train_sols=[520, 700], test_sols=[1037], val_sols=[700])


def test_val_test_overlap_raises_error():
    with pytest.raises(ValueError, match="PROHIBITED SPLIT LEAKAGE.*both validation and --test"):
        validate_splits(train_sols=[520], test_sols=[1037], val_sols=[1037])


def test_empty_splits_raise_error():
    with pytest.raises(ValueError, match="cannot be empty"):
        validate_splits(train_sols=[], test_sols=[1037])
    with pytest.raises(ValueError, match="cannot be empty"):
        validate_splits(train_sols=[520], test_sols=[])


# ---------- NASA ingestion and HTTP error handling ----------
def test_cached_bypasses_retries_on_http_403(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch_stop, "CACHE", tmp_path)

    def mock_urlopen(*args, **kwargs):
        raise urllib.error.HTTPError("http://dummy.url", 403, "Forbidden", {}, None)

    monkeypatch.setattr(fetch_stop.urllib.request, "urlopen", mock_urlopen)

    with pytest.raises(RuntimeError, match="NASA HTTP 403 Forbidden"):
        fetch_stop.cached("http://dummy.url/waypoints.json", "test_403.json", tries=3)


def test_cached_uses_local_file_if_available(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch_stop, "CACHE", tmp_path)
    (tmp_path / "offline.json").write_text('{"mock": true}')

    # Should not call urlopen at all because cached file is valid
    def mock_urlopen(*args, **kwargs):
        raise AssertionError("urlopen should not be called when cache exists")

    monkeypatch.setattr(fetch_stop.urllib.request, "urlopen", mock_urlopen)
    res = fetch_stop.cached("http://dummy.url/offline.json", "offline.json", max_age_days=10)
    assert res == '{"mock": true}'


def test_download_fails_immediately_on_404(monkeypatch, tmp_path):
    dest = tmp_path / "missing.IMG"

    def mock_urlopen(*args, **kwargs):
        raise urllib.error.HTTPError("http://dummy.url", 404, "Not Found", {}, None)

    monkeypatch.setattr(fetch_stop.urllib.request, "urlopen", mock_urlopen)

    with pytest.raises(RuntimeError, match="HTTP 404 on http://dummy.url"):
        fetch_stop.download("http://dummy.url", dest, tries=3)


# ---------- checkpoint safety and validation ----------
def test_depth_loader_raises_on_missing_weights():
    with pytest.raises(FileNotFoundError, match="Specified fine-tuned checkpoint does not exist"):
        Depth(weights="data/models/non_existent_model.pt")
