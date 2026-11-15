"""Batch validation cases. Written without executing them during implementation."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('batch', Path(__file__).resolve().parents[1]/'navcam3d/colorize_all_stops.py')
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


def fixture(tmp_path):
    original, variant = tmp_path/'original', tmp_path/'variant'
    original.mkdir(); variant.mkdir(); (variant/'img').mkdir()
    blob = bytes(180)+bytes([80])*30+bytes(60)
    meta = {'counts': {'vertices': 30}}
    color = {'geometry_sha256': hashlib.sha256(blob).hexdigest(), 'colored_vertices': 25,
             'vertex_coverage': 25/30}
    (original/'scene.json').write_text(json.dumps(meta))
    (variant/'scene.json').write_text(json.dumps({**meta,'variant':'mastcam-color','colorization':color}))
    for directory in (original,variant): (directory/'scene.b64.txt').write_bytes(base64.b64encode(blob))
    (variant/'color.b64.txt').write_bytes(base64.b64encode(bytes([200,150,100])*25+bytes([80])*15))
    (variant/'color-mask.b64.txt').write_bytes(base64.b64encode(bytes([1])*25+bytes(5)))
    (variant/'index.html').write_text('report')
    (variant/'img/step1_photo.jpg').write_bytes(b'preview')
    return original, variant


def test_valid_variant_preserves_original_geometry_and_fallback(tmp_path):
    original, variant = fixture(tmp_path)
    assert batch.validate(original,variant)['colorization']['colored_vertices']==25


def test_changed_geometry_is_rejected(tmp_path):
    original, variant = fixture(tmp_path)
    (variant/'scene.b64.txt').write_bytes(base64.b64encode(b'changed'))
    with pytest.raises(ValueError,match='geometry differs'): batch.validate(original,variant)


def test_fabricated_color_in_uncovered_vertices_is_rejected(tmp_path):
    original, variant = fixture(tmp_path)
    rgb=bytearray(base64.b64decode((variant/'color.b64.txt').read_bytes())); rgb[-1]=100
    (variant/'color.b64.txt').write_bytes(base64.b64encode(rgb))
    with pytest.raises(ValueError,match='grayscale'): batch.validate(original,variant)


def test_corrupt_mask_is_rejected(tmp_path):
    original, variant = fixture(tmp_path)
    (variant/'color-mask.b64.txt').write_bytes(base64.b64encode(bytes(2)))
    with pytest.raises(ValueError,match='dimensions'): batch.validate(original,variant)
