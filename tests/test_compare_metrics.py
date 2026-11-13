"""Synthetic metrics/report tests and a mocked DA3 adapter; no downloads or AI inference."""
import csv
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

spec = importlib.util.spec_from_file_location(
    'comparison_metrics', Path(__file__).resolve().parents[1] / 'navcam3d/compare_depth_models.py')
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


def metric(prediction, truth):
    prediction, truth = np.asarray(prediction, float), np.asarray(truth, float)
    return comparison.metrics(prediction, truth, np.ones(truth.shape, bool))


def test_metrics_match_hand_calculation_and_exclude_invalid_predictions():
    m = metric([2, 8, np.nan, -1], [2, 4, 8, 10])
    assert m['count'] == 2 and m['eligible_count'] == 4
    assert m['coverage'] == .5
    assert m['absrel'] == .5
    assert m['mae_m'] == 2
    assert m['rmse_m'] == pytest.approx(np.sqrt(8))
    assert m['bias_m'] == 2
    assert m['delta1'] == .5
    assert m['delta2'] == .5
    assert m['delta3'] == .5
    assert m['rmse_log'] == pytest.approx(np.log(2)/np.sqrt(2))


def test_pooled_rmse_uses_pixel_weights_and_squared_errors():
    stats = comparison.aggregate([metric([2], [1]), metric([4, 4, 4], [1, 1, 1])])
    assert stats['pixels'] == 4
    assert stats['frame_statistics']['absrel']['mean'] == 2
    assert stats['pooled']['absrel'] == 2.5
    assert stats['pooled']['rmse_m'] == pytest.approx(np.sqrt(7))
    assert 'p90_rel' not in stats['pooled']  # frame percentiles are not pooled pixel percentiles


def test_empty_metric_is_json_serializable_and_depth_bands_do_not_overlap():
    m = metric([np.nan], [2])
    assert m['count'] == 0 and m['absrel'] is None
    json.dumps(m, allow_nan=False)
    assert comparison.depth_ranges(7) == [(0., 5.), (5., 7.)]


def test_splits_are_disjoint_reproducible_and_cover_image():
    first = comparison.splits((128, 128), seed=4, block=16)
    again = comparison.splits((128, 128), seed=4, block=16)
    assert np.all(np.stack(first).sum(0) == 1)
    assert all(np.array_equal(a, b) for a, b in zip(first, again))
    for mask in first:
        assert np.unique(mask[:16, :16]).size == 1


def test_da3_adapter_uses_local_checkpoint_single_rgb_view_and_direct_depth(tmp_path, monkeypatch):
    calls = {}
    class FakeDA3:
        @classmethod
        def from_pretrained(cls, path, **kwargs):
            calls['load'] = (path, kwargs)
            return cls()
        def to(self, device):
            calls['device'] = device
            return self
        def eval(self):
            return self
        def inference(self, images, **kwargs):
            calls['images'], calls['inference'] = images, kwargs
            return SimpleNamespace(depth=np.full((1, 2, 2), 4., np.float32), is_metric=0)
    monkeypatch.setitem(sys.modules, 'depth_anything_3', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'depth_anything_3.api', SimpleNamespace(DepthAnything3=FakeDA3))
    _, predict = comparison.load_model('da3', tmp_path, 'cpu', da3_process_res=280)
    result = predict(np.full((4, 4), 127, np.uint8))
    assert np.all(result == 4)  # not inverse-depth or rescaled to fabricated metres
    assert result.shape == (4, 4)
    assert calls['load'] == (str(tmp_path.resolve()), {'local_files_only': True})
    assert len(calls['images']) == 1 and calls['images'][0].shape == (4, 4, 3)
    assert calls['inference']['process_res_method'] == 'upper_bound_resize'
    assert calls['inference']['process_res'] == 280
    assert calls['inference']['export_dir'] is None
    assert predict.is_metric is False


def test_resize_preserves_invalid_prediction_mask():
    depth = np.array([[4, np.nan], [4, np.nan]], np.float32)
    result = comparison.resize_depth(depth, (4, 4))
    assert np.all(result[:, 0] == 4)
    assert np.all(np.isnan(result[:, -1]))


def make_rows():
    rows = []
    for sol in (520, 1100):
        for name, factor in [('da_aligned', 1.2), ('moge_scaled', 1.1), ('da3_scaled', 1.1)]:
            m = metric(np.array([2., 4.])*factor, [2, 4])
            rows.append({'sol': sol, 'frame': f'photo-{sol}', 'model': name,
                         'alignment': 'median_scale', 'scale': factor,
                         'calibration_pixels': 30, 'test_pixels': 2,
                         'stereo_coverage': .8, 'common_stereo_coverage': .9,
                         'full_image_prediction_coverage': 1., 'hole_prediction_coverage': .7,
                         'common': m, 'own': m, 'depth_ranges': {'0.0-5.0': m}})
    return rows


def test_detailed_reports_include_all_models_groups_and_paired_ties(tmp_path):
    rows = make_rows()
    summary = comparison.summarize(rows)
    pairwise = comparison.pairwise_comparisons(rows)
    tie = next(x for x in pairwise if x['a'] == 'moge_scaled' and x['b'] == 'da3_scaled')
    assert tie['frames'] == 2 and tie['ties'] == 2
    assert summary['overall']['da3_scaled']['pixels'] == 4
    report = {'frames': rows, 'summary': summary, 'pairwise': pairwise,
              'config': {'sols': [520, 1100], 'device': 'cpu', 'seed': 0, 'block_size': 32, 'max_depth': 60},
              'models': {'da3': comparison.DA3_MODELS[0]}, 'inputs': ['a', 'b'],
              'environment': {'gpu': None, 'timing_scope': 'inference'}, 'timings': {}}
    text = comparison.detailed_report(report)
    for expected in ('Sol 520', 'Sol 1100', 'Pooled RMSE', 'da3_scaled', 'Per-frame accuracy',
                     'Mean A−B', 'Metric definitions', '0.0-5.0 m'):
        assert expected in text
    comparison.write_csv_reports(tmp_path, rows, summary)
    with (tmp_path/'metrics.csv').open() as source:
        output = list(csv.DictReader(source))
    assert len(output) == 18
    assert {r['scope'] for r in output} == {'common', 'own', 'common_depth_range'}
    assert (tmp_path/'aggregate_metrics.csv').is_file()
    json.dumps(report, allow_nan=False)


def test_all_skipped_report_explains_no_valid_comparison():
    rows = [{'sol': 520, 'frame': 'empty', 'status': 'insufficient common stereo pixels',
             'fit_pixels': 0, 'selection_pixels': 0, 'test_pixels': 0}]
    report = {'frames': rows, 'summary': comparison.summarize(rows),
              'pairwise': comparison.pairwise_comparisons(rows),
              'config': {'sols': [520], 'device': 'cpu', 'seed': 0, 'block_size': 32, 'max_depth': 60},
              'models': {'da3': comparison.DA3_MODELS[0]}, 'inputs': ['a'],
              'environment': {'gpu': None, 'timing_scope': 'inference'}, 'timings': {}}
    text = comparison.detailed_report(report)
    assert 'No valid calibrated comparison' in text
    assert 'insufficient common stereo pixels' in text


def test_da3_weights_resolve_selected_repository_from_offline_cache(tmp_path, monkeypatch):
    calls = []
    def snapshot(repo, **kwargs):
        calls.append((repo, kwargs))
        path = tmp_path / repo.rsplit('/', 1)[-1]
        path.mkdir(exist_ok=True)
        return str(path)
    def checkpoint(repo, filename, **kwargs):
        calls.append((repo, kwargs))
        path = tmp_path / filename
        path.write_bytes(b'fake checkpoint')
        return str(path)
    monkeypatch.setitem(sys.modules, 'huggingface_hub', SimpleNamespace(
        snapshot_download=snapshot, hf_hub_download=checkpoint))
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')
    monkeypatch.setenv('TRANSFORMERS_OFFLINE', '1')
    args = SimpleNamespace(offline=True, da_weights=None, moge_weights=None,
                           da3_weights=None, da3_model='depth-anything/DA3METRIC-LARGE',
                           model_cache=tmp_path/'cache')
    comparison.resolve_weights(args)
    assert args.da3_weights.name == 'DA3METRIC-LARGE'
    assert [repo for repo, _ in calls] == ['depth-anything/Depth-Anything-V2-Small-hf',
                                          'Ruicheng/moge-2-vitl', 'depth-anything/DA3METRIC-LARGE']
    assert all(kwargs['local_files_only'] for _, kwargs in calls)
