"""Metadata-only tests: no NASA requests or image downloads."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location('color_audit', Path(__file__).resolve().parents[1]/'navcam3d/audit_color_coverage.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

# Compact fixture follows the structure of NASA's sol-1100 Mastcam PDS4 label.
XML = '''<Product xmlns="pds" xmlns:g="geometry" xmlns:d="display" xmlns:i="imaging">
<alternate_id>1100ML0048710040500488C00_DRCL</alternate_id>
<start_date_time>2015-09-10T04:19:55.713Z</start_date_time>
<Primary_Result_Summary><description>Periodic documentation of the terrain along the traverse</description></Primary_Result_Summary>
<d:Color_Display_Settings><d:red_channel_band>1</d:red_channel_band><d:green_channel_band>2</d:green_channel_band><d:blue_channel_band>3</d:blue_channel_band></d:Color_Display_Settings>
<g:Motion_Counter><g:name>RMC</g:name><g:Motion_Counter_Index><g:index_id>SITE</g:index_id><g:index_value_number>49</g:index_value_number></g:Motion_Counter_Index><g:Motion_Counter_Index><g:index_id>DRIVE</g:index_id><g:index_value_number>2626</g:index_value_number></g:Motion_Counter_Index></g:Motion_Counter>
<g:Camera_Model_Parameters><g:Vector_Axis><g:x_unit>1</g:x_unit><g:y_unit>0</g:y_unit><g:z_unit>0</g:z_unit></g:Vector_Axis><g:coordinate_space_frame_type>ROVER_NAV_FRAME</g:coordinate_space_frame_type></g:Camera_Model_Parameters>
<g:Coordinate_Space_Definition><local_identifier>ROVER_NAV_FRAME_TELEMETRY</local_identifier>
<g:Coordinate_Space_Present><g:Coordinate_Space_Index><g:index_id>SITE</g:index_id><g:index_value_number>49</g:index_value_number></g:Coordinate_Space_Index><g:Coordinate_Space_Index><g:index_id>DRIVE</g:index_id><g:index_value_number>2626</g:index_value_number></g:Coordinate_Space_Index></g:Coordinate_Space_Present>
<g:Vector_Origin_Offset><g:x_position>30</g:x_position><g:y_position>40</g:y_position><g:z_position>2</g:z_position></g:Vector_Origin_Offset>
<g:Quaternion_Plus_Direction><g:qcos>1</g:qcos><g:qsin1>0</g:qsin1><g:qsin2>0</g:qsin2><g:qsin3>0</g:qsin3></g:Quaternion_Plus_Direction>
<g:Coordinate_Space_Reference><g:coordinate_space_frame_type>SITE_FRAME</g:coordinate_space_frame_type></g:Coordinate_Space_Reference>
</g:Coordinate_Space_Definition>
<i:color_filter_array_state>Decoded</i:color_filter_array_state><i:filter_number>0</i:filter_number><i:filter_name>MASTCAM_L0_CLEAR</i:filter_name><i:sample_bits>16</i:sample_bits>
<Axis_Array><axis_name>Band</axis_name><elements>3</elements></Axis_Array><Axis_Array><axis_name>Line</axis_name><elements>1208</elements></Axis_Array><Axis_Array><axis_name>Sample</axis_name><elements>1208</elements></Axis_Array>
</Product>'''


def image():
    return audit.parse_mastcam(XML, 'https://nasa.test/a.xml', 'https://nasa.test/a.IMG')


def anchor():
    return {'site': 49, 'drive': 2902, 'site_position': [0, 0, 0], 'position': None,
            'navcam_frames': ['n1'], 'pointings': [{'azimuth': 0, 'elevation': 0}]}


def test_real_structure_confirms_rgb_and_location_and_transforms_axis():
    r = image()
    assert r['confirmed_rgb'] is True
    assert (r['site'], r['drive']) == (49, 2626)
    assert r['capture_id'] == '1100ML0048710040500488'
    assert r['width'] == r['height'] == 1208
    assert r['site_position'] == [30, 40, 2]
    assert r['pointing'] == {'azimuth': 0, 'elevation': 0}


@pytest.mark.parametrize('text', [XML.replace('<elements>3</elements>', '<elements>1</elements>'),
    XML.replace('>Decoded<', '>Encoded<'), XML.replace('<i:filter_number>0</i:filter_number>', '<i:filter_number>4</i:filter_number>').replace('MASTCAM_L0_CLEAR', 'MASTCAM_L4'),
    XML.replace('<d:blue_channel_band>3', '<d:blue_channel_band>2')])
def test_bayer_single_band_and_multispectral_are_not_confirmed_rgb(text):
    assert audit.parse_mastcam(text, 'https://nasa.test/a.xml', 'https://nasa.test/a.IMG')['confirmed_rgb'] is False


def test_sol_match_does_not_imply_same_station_and_drive_is_not_distance():
    rel, distance, method = audit.relation(image(), anchor(), 60)
    assert rel == 'nearby' and distance == 50
    assert method == 'same_site_telemetry_xy'
    assert audit.relation(image(), anchor(), 40)[0] == 'outside_radius'
    a = anchor(); a['drive'] = 2626
    assert audit.relation(image(), a, 0)[0] == 'same_site_drive'


def test_offsets_from_different_sites_are_not_compared():
    r = image(); r['site'] = 50
    assert audit.relation(r, anchor(), 100)[0] == 'unknown_distance'
    r['position'] = [-4, 137]
    a = anchor(); a['position'] = [-4, 137]
    assert audit.relation(r, a, 100)[0] == 'nearby'


def test_capture_selection_deduplicates_processing_and_thumbnail_variants():
    base = 'https://nasa.test/sol/'
    names = ['1100ML0048710040500488C00_DRCL', '1100ML0048710040500488C00_DRCX',
             '1100ML0048710040500488E01_DRCL', '1100ML0048710040500488I01_DRCL']
    urls = [base+n.lower()+'.xml' for n in names]+[base+n+'.IMG' for n in names]
    assert audit.candidates(urls) == [(base+names[0].lower()+'.xml', base+names[0]+'.IMG')]


def test_unknown_distance_prevents_false_absence_verdict():
    r = image(); r['site'] = 50
    a = SimpleNamespace(sol_window=5, radius_m=100, azimuth_bin_deg=30, min_color_images=10,
                        min_azimuth_bins=3, pointing_tolerance_deg=30)
    _, rows = audit.assess([r], [anchor()], 1100, a, incomplete=False)
    assert rows[0]['same_spot_verdict'] == 'none_in_search_window'
    assert rows[0]['same_or_nearby_verdict'] == 'unknown_incomplete_search'
    assert rows[0]['unknown_distance'] == 1


def test_safe_listing_links_and_offline_failure(tmp_path):
    base = 'https://nasa.test/archive/'
    links = audit.children('<a href="../">parent</a><a href="volume_0001_calibrated/">v</a><a href="?C=N">sort</a><a href="https://other.test/a">other</a>', base)
    assert links == [base+'volume_0001_calibrated/']
    with pytest.raises(RuntimeError, match='Uncached metadata'):
        audit.MetadataCache(tmp_path, offline=True).get(base)


def test_discovery_does_not_treat_failed_volume_as_empty_sol():
    root = 'https://nasa.test/archive/'
    class FakeCache:
        def get(self, url):
            if url == root:
                return '<a href="volume_0001_calibrated/">v1</a><a href="volume_0002_calibrated/">v2</a>'
            if '0002' in url: raise RuntimeError('NASA unavailable')
            return '<a href="1100/">sol</a><a href="1101/">next</a>'
    folders, errors, count = audit.discover(FakeCache(), root, {1100}, 2)
    assert count == 2 and len(errors) == 1
    assert folders == [(1100, root+'volume_0001_calibrated/SURFACE/1100/')]


def test_local_anchor_keeps_multiline_rmc_and_site_position(tmp_path):
    directory = tmp_path/'sol1100'; directory.mkdir()
    text = '''ROVER_MOTION_COUNTER = (49,2902,8,0,0,0,2,0,0,0)
GROUP = ROVER_COORD_SYSTEM_PARMS
ORIGIN_OFFSET_VECTOR = (1,2,3)
ORIGIN_ROTATION_QUATERNION = (1,0,0,0)
END_GROUP = ROVER_COORD_SYSTEM_PARMS
GROUP = GEOMETRIC_CAMERA_MODEL_PARMS
MODEL_COMPONENT_2 = (1,0,0)
END_GROUP = GEOMETRIC_CAMERA_MODEL_PARMS
'''
    for kind in ('RASLF', 'XYZLF'):
        (directory/f'NLB_495148600{kind}0492902NCAM00353M1.LBL').write_text(text)
    targets = audit.load_targets(tmp_path, [1100])[1100]
    assert len(targets) == 1
    assert len(targets[0]['navcam_frames']) == 1
    assert targets[0]['site_position'] == [1, 2, 3]
    json.dumps(targets, allow_nan=False)


def test_empty_cache_entry_is_refetched_and_gzip_decoded(tmp_path, monkeypatch):
    import gzip
    import hashlib
    import io
    url = 'https://nasa.test/label.xml'
    path = tmp_path/(hashlib.sha256(url.encode()).hexdigest()+'.txt')
    path.write_text('')
    class Response(io.BytesIO):
        headers = {'Content-Encoding': 'gzip'}
    monkeypatch.setattr(audit.urllib.request, 'urlopen', lambda *a, **k: Response(gzip.compress(b'<label/>')))
    assert audit.MetadataCache(tmp_path).get(url) == '<label/>'
    assert path.read_text() == '<label/>'


def test_empty_remote_metadata_retries_without_caching(tmp_path, monkeypatch):
    import io
    class Response(io.BytesIO):
        headers = {}
    calls = []
    def empty(*args, **kwargs):
        calls.append(1)
        return Response(b'')
    monkeypatch.setattr(audit.urllib.request, 'urlopen', empty)
    monkeypatch.setattr(audit.time, 'sleep', lambda _: None)
    with pytest.raises(RuntimeError, match='empty metadata response'):
        audit.MetadataCache(tmp_path).get('https://nasa.test/label.xml')
    assert len(calls) == 3
    assert not list(tmp_path.glob('*.txt'))
