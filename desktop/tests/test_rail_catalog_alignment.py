import json
import sqlite3
from copy import deepcopy

import pytest

from desktop.rail_categories import catalog_parents
from desktop.rail_semantics import semantic_record
from desktop.transport_modes import metro_source_ids, other_transport


def test_directory_uses_the_same_facts_as_style_and_editor():
    from desktop.rail_style_resolver import STYLE_SELECTIONS, STYLE_LABELS, style_key
    for key, (group, category, function, band) in STYLE_SELECTIONS.items():
        facts = {'railway_class': category, 'line_role': function if group == 'track' else 'unknown',
                 'track_role': 'main_track' if group == 'track' else function,
                 'facility_only': group == 'station', 'speed_band': band}
        assert catalog_parents(facts, 0) == tuple(STYLE_LABELS[key].split(' / '))
        assert style_key(facts) == key
    # A name and a province cannot invent a trunk-line property.
    facts = {'name': '京沪高速线', 'province': '上海市', 'railway_class': 'high_speed',
             'line_role': 'connecting_line', 'track_role': 'main_track', 'speed_band': '250-300'}
    assert catalog_parents(facts, 0) == ('轨道线', '高速铁路', '联络线', '250–300 km/h')


def test_legacy_status_edit_changes_the_canonical_status_and_directory():
    source = {'way_tags': {'railway': 'rail', 'highspeed': 'yes', 'usage': 'main'}}
    facts = semantic_record(source, {'technical_attributes': {'operating_status': '在建'}})
    assert facts['construction_status'] == 'construction'
    assert facts['construction'] is True
    assert facts['provenance']['construction_status']['source'] == 'workspace_override'
    assert catalog_parents({**source, **facts}, 0)[0] == '在建'


def test_status_editor_has_one_control_and_saves_semantics(qtbot):
    from desktop.line_metadata_ui import LineMetadataDialog
    dialog = LineMetadataDialog('rail', '甲线', ['轨道线', '高速铁路', '正线'],
                                {'operating_status': '运营中'}, rail_semantics={
                                    'railway_class': 'high_speed', 'line_role': 'main_line',
                                    'track_role': 'main_track', 'construction_status': 'operating'})
    qtbot.addWidget(dialog)
    assert 'operating_status' not in dialog.attribute_controls
    control = dialog.rail_semantics['construction_status']
    control.setCurrentIndex(control.findData('construction'))
    values = dialog.values()
    assert values['rail_semantics']['construction_status'] == 'construction'
    assert values['technical_attributes']['operating_status'] == '在建'


@pytest.mark.parametrize('tags', [
    {'railway': 'depot', 'operator': '南京地铁'},
    {'landuse': 'railway', 'name': '港鐵大圍車廠 MTR Tai Wai Depot'},
    {'railway': 'depot', 'depot': 'subway'},
])
def test_explicit_metro_facility_evidence_is_excluded(tags):
    assert other_transport(tags)
    assert not other_transport({'railway': 'depot', 'name': '北京动车段'})


def test_unresolved_metro_polygon_is_not_an_authoritative_metro_id(tmp_path):
    path = tmp_path / 'metro.sqlite'
    candidate = {'properties': {'osm_way_id': 7, 'station_area_tags': {'railway': 'platform'},
                 'associated_station_ids': [], 'member_station_ids': [],
                 'association_verification_status': 'unresolved'}}
    verified = {'properties': {'osm_way_id': 8, 'station_area_tags': {'subway': 'yes'}}}
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE features(kind,data)')
        db.executemany('INSERT INTO features VALUES(?,?)',
                       [('areas', json.dumps(f)) for f in (candidate, verified)])
    assert ('way', '7') not in metro_source_ids(path)
    assert ('way', '8') in metro_source_ids(path)


def test_platform_display_keeps_source_lines_and_deduplicates_old_faces():
    from desktop.rail_platforms import platform_display
    polygon = {'properties': {'infrastructure_id': 'way/1', 'way_tags': {'ref': '2;3'},
                'associated_station_ids': [5]}, 'geometry': {'type': 'Polygon',
                'coordinates': [[[120, 30], [120.005, 30], [120.005, 30.0001], [120, 30.0001], [120, 30]]]}}
    old = deepcopy(polygon)
    old['properties'].update(infrastructure_id='way/2', retained_previous_snapshot=True)
    line = {'properties': {'infrastructure_id': 'way/3', 'way_tags': {'ref': '4'}},
            'geometry': {'type': 'LineString', 'coordinates': [[120, 30], [120.005, 30]]}}
    originals = deepcopy([polygon, old, line])
    visible = platform_display([polygon, old, line])
    assert len(visible) == 2
    assert visible[0]['properties']['display_duplicate_source_ids'] == ['way/2']
    assert visible[1]['geometry']['type'] == 'LineString'
    assert [polygon, old, line] == originals


def test_line_only_platforms_keep_missing_face_notice():
    from desktop.rail_platforms import platform_display
    feature = {'properties': {'infrastructure_id': 'way/1'},
               'geometry': {'type': 'LineString', 'coordinates': [[120, 30], [120.01, 30]]}}
    visible = platform_display([feature])
    assert visible[0]['properties']['geometry_status'] == '来源仅有站台线，缺少真实站台面'
    assert 'geometry_status' not in feature['properties']


def test_station_assets_include_new_owner_from_association_sidecar(tmp_path):
    from desktop.rail_platform_associations import source_fingerprint
    from desktop.station_tracks import station_assets
    feature = {'properties': {'infrastructure_id': 'way/1', 'associated_station_ids': [5]},
               'geometry': {'type': 'LineString', 'coordinates': [[120, 30], [120.01, 30]]}}
    edit = {'properties': {'associated_station_ids': [7]}, 'source_fingerprint': source_fingerprint(feature)}
    (tmp_path/'rail_platform_associations.json').write_text(
        json.dumps({'associations': {'way/1': edit}}), encoding='utf-8')
    with sqlite3.connect(tmp_path/'rail.sqlite') as db:
        db.execute('CREATE TABLE features(kind,data)')
        db.execute('INSERT INTO features VALUES(?,?)', ('railPlatforms', json.dumps(feature)))
        assert not station_assets(db, 'node/5')
        assert station_assets(db, 'node/7')[0]['properties']['associated_station_ids'] == [7]


def test_status_change_updates_viewport_filter_without_changing_source(tmp_path):
    from desktop.rail_store import viewport
    feature = {'properties': {'network_edge_id': 'NE-1', 'catalog_group_id': 'RL-1',
                              'construction_status': 'operating'},
               'geometry': {'type': 'LineString', 'coordinates': [[120, 30], [120.01, 30]]}}
    with sqlite3.connect(tmp_path / 'rail.sqlite') as db:
        db.execute('CREATE TABLE features(id PRIMARY KEY,kind,service,data)')
        db.execute('CREATE VIRTUAL TABLE bounds USING rtree(id,minx,maxx,miny,maxy)')
        db.execute('INSERT INTO features VALUES(1,?,?,?)', ('rail', 'main', json.dumps(feature)))
        db.execute('INSERT INTO bounds VALUES(1,120,120.01,30,30)')
    edits = {'RL-1': {'rail_semantics': {'construction_status': 'construction'}}}
    bbox = [119.9, 29.9, 120.1, 30.1]
    result = viewport(tmp_path, 'rail', bbox, 14, {'states': ['construction']}, overrides=edits)
    assert len(result['features']) == 1
    assert not viewport(tmp_path, 'rail', bbox, 14, {'states': ['operating']}, overrides=edits)['features']
    with sqlite3.connect(tmp_path / 'rail.sqlite') as db:
        assert json.loads(db.execute('SELECT data FROM features').fetchone()[0]) == feature


def test_untagged_depot_classification_uses_contained_track_modes():
    from desktop.rail_transport_context import classify_facility_tracks
    feature = {'properties': {'infrastructure_id': 'way/1'}, 'geometry': {'type': 'Polygon',
                'coordinates': [[[120, 30], [120.01, 30], [120.01, 30.01], [120, 30.01], [120, 30]]]}}
    urban = {'properties': {'osm_way_id': 7, 'way_tags': {'railway': 'subway'}},
             'geometry': {'type': 'LineString', 'coordinates': [[120.001, 30.005], [120.009, 30.005]]}}
    rail = deepcopy(urban)
    rail['properties'] = {'osm_way_id': 8, 'way_tags': {'railway': 'rail'}}
    assert classify_facility_tracks([feature], [urban])['way/1']['mode'] == 'urban'
    assert classify_facility_tracks([feature], [urban, rail])['way/1']['mode'] == 'mixed'
    # Outside tracks cannot establish a facility's mode by proximity.
    urban['geometry']['coordinates'] = [[120.02, 30.005], [120.03, 30.005]]
    assert classify_facility_tracks([feature], [urban])['way/1']['mode'] == 'unknown'


def test_platform_inside_another_named_building_cannot_take_nearest_station():
    from desktop.rail_boundaries import associate
    station = {'properties': {'osm_node_id': 1, 'name': '甲站'},
               'geometry': {'type': 'Point', 'coordinates': [120, 30]}}
    building = {'properties': {'infrastructure_id': 'way/2', 'source_name': '乙站',
                              'boundary_kind': 'station_building', 'way_tags': {}},
                'geometry': {'type': 'Polygon', 'coordinates': [
                    [[120.001, 30], [120.004, 30], [120.004, 30.004], [120.001, 30.004], [120.001, 30]]]}}
    platform = deepcopy(building)
    platform['properties'] = {'infrastructure_id': 'way/3', 'source_name': '1',
                              'boundary_kind': 'platform', 'way_tags': {'ref': '1'}}
    platform['geometry']['coordinates'] = [[
        [120.002, 30.001], [120.003, 30.001], [120.003, 30.003], [120.002, 30.003], [120.002, 30.001]]]
    associate([building, platform], [station])
    assert not platform['properties']['associated_station_ids']
    assert platform['properties']['association_verification_status'] == 'unresolved'
    assert platform['properties']['association_candidate_station_ids'] == ['way/2']


def test_metro_area_representative_point_is_excluded_by_source_id(tmp_path):
    from desktop.rail_store import viewport
    feature = {'properties': {'station_source_id': 'way/2', 'kind': 'yard',
                              'name': '甲停车场', 'node_tags': {'landuse': 'railway'}},
               'geometry': {'type': 'Point', 'coordinates': [120, 30]}}
    with sqlite3.connect(tmp_path/'rail.sqlite') as db:
        db.execute('CREATE TABLE features(id PRIMARY KEY,kind,service,data)')
        db.execute('CREATE VIRTUAL TABLE bounds USING rtree(id,minx,maxx,miny,maxy)')
        db.execute('INSERT INTO features VALUES(1,?,?,?)',('railPoints','main',json.dumps(feature)))
        db.execute('INSERT INTO bounds VALUES(1,120,120,30,30)')
    (tmp_path/'rail_transport_context.json').write_text(json.dumps({'facilities':{
        'way/2': {'mode':'urban','verification_status':'automatic_reference'}}}),encoding='utf-8')
    assert not viewport(tmp_path,'railPoints',[119.9,29.9,120.1,30.1],16)['features']


def test_association_sidecar_cannot_be_replayed_onto_changed_geometry(tmp_path):
    from desktop.rail_platform_associations import apply_associations, source_fingerprint
    feature = {'properties': {'infrastructure_id':'way/1'},
               'geometry':{'type':'LineString','coordinates':[[120,30],[120.01,30]]}}
    edit = {'properties': {'associated_station_ids':[7]},'source_fingerprint':source_fingerprint(feature)}
    (tmp_path/'rail_platform_associations.json').write_text(json.dumps({'associations':{'way/1':edit}}),encoding='utf-8')
    assert apply_associations([feature],tmp_path)[0]['properties']['associated_station_ids'] == [7]
    altered = deepcopy(feature)
    altered['geometry']['coordinates'][1] = [121,31]
    assert 'associated_station_ids' not in apply_associations([altered],tmp_path)[0]['properties']
