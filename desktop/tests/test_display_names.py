import json

from desktop.rail_display import display_manifest, feature_name_key


def test_names_include_lines_nodes_stations_and_only_identified_yards():
    catalog = {
        'line': {'line_id': 'IL-1', 'name': '原线名'},
        'yard': {'station_name': '南京南', 'track_type': '高速铁路站场股道'},
        'unknown': {'station_name': '未关联站场', 'track_type': '站场股道'},
    }
    overrides = {'line': {'display_name': '新线名'}, 'station:node/1': {'display_name': '新站名'},
                 'switch:node/2': {'display_name': '西咽喉道岔'}, 'feature:node/3': {'display_name': '分界点'}}
    before = json.dumps(catalog)
    names = display_manifest(catalog, overrides)
    assert names['lines'] == {'line': '新线名', 'IL-1': '新线名'}
    assert names['stations']['node/1'] == '新站名'
    assert names['features']['node/2'] == '西咽喉道岔'
    assert names['features']['node/3'] == '分界点'
    assert names['yards'] == {'yard': '南京南 · 站场股道'}
    assert json.dumps(catalog) == before
    assert feature_name_key({'osm_node_id': 3}) == 'node/3'


def test_generic_node_name_persists_and_emits_map_update(qtbot, tmp_path):
    from desktop.rail_catalog_ui import RailCatalog
    from desktop.tests.test_operating_ui import MapStub
    widget = RailCatalog(tmp_path, tmp_path/'settings.json', MapStub())
    qtbot.addWidget(widget)
    updates = []
    widget.display_names_changed.connect(lambda: updates.append(True))
    widget.save_feature_name({'osm_node_id': 3}, '边界点')
    assert updates
    restored = RailCatalog(tmp_path, tmp_path/'settings.json', MapStub())
    qtbot.addWidget(restored)
    assert restored.overrides['feature:node/3']['display_name'] == '边界点'
    widget.undo_catalog()
    assert 'feature:node/3' not in widget.overrides
