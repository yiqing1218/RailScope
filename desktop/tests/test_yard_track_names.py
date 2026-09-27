import json
import sqlite3
from types import SimpleNamespace

from desktop.display_names import apply_names
from desktop.yard_track_names import automatic_track_names, yard_track_key


def test_track_numbering_is_per_track_persistent_and_preserves_source_names(tmp_path):
    database = tmp_path/'rail.sqlite'
    features = []
    for section, edge, tags in [('RS-a', 'a1', {}), ('RS-a', 'a2', {}),
                                ('RS-b', 'b', {'ref': '1'}), ('RS-c', 'c', {'name': '检修股道'})]:
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
    assert names['object:section_id:RS-a']['display_name'] == '测试站 · 第2股道（暂编）'
    assert names['object:section_id:RS-b']['display_name'] == '测试站 · 1股道'
    assert 'object:section_id:RS-c' not in names
    assert automatic_track_names(database, groups, names) == {}
    names['object:section_id:RS-a'].update(display_name='到发3道', source='manual')
    apply_names({'features': features}, names)
    assert [f['properties'].get('display_name') for f in features] == ['到发3道', '到发3道', '测试站 · 1股道', None]
    assert features[0]['properties']['network_edge_id'] == 'a1'


def test_map_rename_opens_track_entity_editor_not_line_metadata():
    from desktop.launcher import Desk
    feature = {'layer': 'rail', 'properties': {'catalog_group_id': 'ST-test',
                'section_id': 'RS-one', 'network_edge_id': 'edge-one'}}
    edits = []
    desk = SimpleNamespace(selected_features=[feature], edit_station_tracks=lambda: edits.append(feature))
    Desk.rename_map_selection(desk)
    assert edits == [feature]
    assert yard_track_key({'catalog_group_id': 'RL-main', 'network_edge_id': 'e'}) is None
