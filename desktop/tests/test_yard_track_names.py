import json
import sqlite3
from types import SimpleNamespace

from desktop.display_names import apply_names
from desktop.yard_track_names import automatic_track_names, yard_track_key


def test_track_numbering_is_per_track_persistent_and_preserves_source_names(tmp_path):
    database = tmp_path/'rail.sqlite'
    features = []
    for section, edge, tags in [('RS-a', 'a1', {}), ('RS-a', 'a2', {}),
                                ('RS-b', 'b', {'railway:track_ref': '1'}), ('RS-c', 'c', {'name': '检修股道'})]:
        features.append({'geometry': {'type': 'LineString'}, 'properties': {
            'section_id': section, 'network_edge_id': edge, 'way_tags': tags,
            'catalog_group_id': 'ST-test', 'line_name': '未命名轨道', 'line_id': 'RL-'+edge}})
    with sqlite3.connect(database) as db:
        db.executescript('CREATE TABLE features(id,data); CREATE TABLE rail_feature_groups(feature_id,group_id);')
        for i, feature in enumerate(features):
            db.execute('INSERT INTO features VALUES(?,?)', (i, json.dumps(feature)))
            db.execute('INSERT INTO rail_feature_groups VALUES(?,?)', (i, 'ST-test'))
    groups = [('ST-test', {'station_name': '测试站', 'provinces': ['测试省']})]
    names = automatic_track_names(database, groups, {})
    assert names['object:section_id:RS-a']['display_name'] == '测试站 · 站线（用途待核实）'
    assert names['object:section_id:RS-a']['track_number'] is None
    assert names['object:section_id:RS-b']['display_name'] == '测试站 · 1股道'
    assert 'object:section_id:RS-c' not in names
    assert automatic_track_names(database, groups, names) == {}
    names['object:section_id:RS-a'].update(display_name='到发3道', source='manual')
    apply_names({'features': features}, names)
    assert [f['properties'].get('display_name') for f in features] == ['到发3道', '到发3道', '测试站 · 1股道', None]
    assert features[0]['properties']['network_edge_id'] == 'a1'

    old = {'system:yard_track_names': {'snapshot': str(database.stat().st_mtime_ns), 'version': 1},
           'object:section_id:RS-a': {'display_name': '测试站 · 第98股道（暂编）',
                'track_number': 98, 'source': 'automatic_yard_track_number', 'color': '#123abc'}}
    migrated = automatic_track_names(database, groups, old)
    track = migrated['object:section_id:RS-a']
    assert track['track_number'] is None and '98' not in track['display_name']
    assert track['color'] == '#123abc'
    assert track['legacy_metadata']['automatic_numbering']['track_number'] == 98


def test_map_rename_opens_track_object_editor_without_station_link():
    from desktop.launcher import Desk
    feature = {'layer': 'rail', 'properties': {'catalog_group_id': 'ST-test',
                'section_id': 'RS-one', 'network_edge_id': 'edge-one'}}
    edits = []
    desk = SimpleNamespace(selected_features=[feature], edit_selected_metadata=lambda value: edits.append(value))
    Desk.rename_map_selection(desk)
    assert edits == [feature]
    assert yard_track_key({'catalog_group_id': 'RL-main', 'network_edge_id': 'e'}) is None


def test_unassociated_yard_track_with_line_id_opens_line_editor():
    from desktop.launcher import Desk
    feature = {'layer': 'rail', 'properties': {'catalog_group_id': 'RL-yard',
                'section_id': 'RS-one', 'network_edge_id': 'edge-one', 'service': 'yard'}}
    calls = []
    desk = SimpleNamespace(selected_data=feature, route_lookup={},
        edit_station_tracks=lambda: calls.append('station'),
        edit_line_metadata=lambda selected, kind, **kwargs: calls.append((selected, kind, kwargs)))
    Desk.edit_selected_metadata(desk)
    assert calls == [(feature, 'rail', {'rail_groups': ['RL-yard']})]
