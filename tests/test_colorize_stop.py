"""Camera projection and occlusion checks, using synthetic data only."""
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'navcam3d'))
from colorize_stop import project, read_rgb, visible_on_heightfield


def test_projection_uses_camera_pose_and_rejects_behind_camera():
    camera = (None, np.zeros(3), np.array([0, 0, 1]), np.array([100, 0, 50]),
              np.array([0, 100, 50]), np.eye(3), np.array([1, 2, 3]))
    points = np.array([[1, 2, 13], [1, 2, 2], [21, 2, 13]])
    u, v, depth, valid = project(points, camera, 100, 100)
    assert valid.tolist() == [True, False, False]
    assert u[0] == v[0] == 50
    assert depth[0] == 10


def test_heightfield_hides_ground_behind_a_ridge():
    radius, cell = 10, .25
    surface = np.zeros((80, 80))
    camera = np.array([0, 2, 0])
    targets = np.array([[5, 0, 0], [-5, 0, 0]])
    surface[:, 48:53] = 2  # ridge at east 2–3 m, between camera and first target
    visible = visible_on_heightfield(targets, camera, surface, radius, cell)
    assert visible.tolist() == [False, True]


def test_rgb_reader_respects_offset_band_order_and_invalid_constant(tmp_path):
    values = np.arange(18, dtype='>i2').reshape(3, 2, 3)
    values[1, 0, 0] = 32767
    path = tmp_path/'color.IMG'
    path.write_bytes(b'header00'+values.tobytes())
    root = ET.fromstring('''<Product><Array_3D_Image><offset>8</offset><data_type>SignedMSB2</data_type>
    <Axis_Array><axis_name>Band</axis_name><sequence_number>1</sequence_number><elements>3</elements></Axis_Array>
    <Axis_Array><axis_name>Line</axis_name><sequence_number>2</sequence_number><elements>2</elements></Axis_Array>
    <Axis_Array><axis_name>Sample</axis_name><sequence_number>3</sequence_number><elements>3</elements></Axis_Array>
    </Array_3D_Image></Product>''')
    rgb = read_rgb(path, root)
    assert rgb.shape == (2, 3, 3)
    assert np.isnan(rgb[0, 0]).all()
    assert np.array_equal(rgb[1, 2], values[:, 1, 2])
