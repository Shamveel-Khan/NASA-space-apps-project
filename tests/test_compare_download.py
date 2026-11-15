"""No network or inference: standalone NASA acquisition behavior."""
import importlib.util
import io
from pathlib import Path
import urllib.error

import pytest

spec = importlib.util.spec_from_file_location(
    'comparison', Path(__file__).resolve().parents[1] / 'navcam3d/compare_depth_models.py')
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


def test_cached_product_is_not_downloaded(tmp_path, monkeypatch):
    dest = tmp_path / 'a.IMG'
    dest.write_bytes(b'abcd')
    def unexpected(*args, **kwargs):
        raise AssertionError('network accessed for complete cache')
    monkeypatch.setattr(comparison.urllib.request, 'urlopen', unexpected)
    assert comparison.fetch_file('https://example.invalid', dest, 4) == dest


def test_truncated_cache_is_replaced_atomically(tmp_path, monkeypatch):
    dest = tmp_path / 'a.IMG'
    dest.write_bytes(b'a')
    monkeypatch.setattr(comparison.urllib.request, 'urlopen', lambda *a, **k: io.BytesIO(b'abcd'))
    comparison.fetch_file('https://example.invalid', dest, 4)
    assert dest.read_bytes() == b'abcd'
    assert not dest.with_name('a.IMG.part').exists()


def test_permanent_http_error_is_not_retried(tmp_path, monkeypatch):
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(1)
        raise urllib.error.HTTPError('https://example.invalid', 403, 'Forbidden', {}, None)
    monkeypatch.setattr(comparison.urllib.request, 'urlopen', forbidden)
    with pytest.raises(RuntimeError, match='HTTP 403'):
        comparison.fetch_file('https://example.invalid', tmp_path / 'a.IMG')
    assert len(calls) == 1


def test_listing_requires_all_four_products_and_propagates_failures(tmp_path, monkeypatch):
    xyz = 'NLB_486270860XYZLF0481570NCAM00323M1'
    ras = xyz.replace('XYZLF', 'RASLF')
    listing = tmp_path / 'listing.html'
    listing.write_text(' '.join(stem + ext for stem in (xyz, ras) for ext in ('.LBL', '.IMG')))
    def fake_download(url, dest, expected=None):
        if 'SOL01100/' == url[-9:]:
            return listing
        raise RuntimeError('worker download failed')
    monkeypatch.setattr(comparison, 'fetch_file', fake_download)
    with pytest.raises(RuntimeError, match='worker download failed'):
        comparison.download_sol(1100, tmp_path, 2, 0)
