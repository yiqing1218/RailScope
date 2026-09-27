from copy import deepcopy

from desktop.display_names import apply_names, object_key
from desktop.rail_line_store import DiskRailLineLibrary
from desktop.tests.test_line_membership import aliases, edge, install


def test_map_names_do_not_change_source_tags_or_ids():
    features = [
        {'type': 'Feature', 'geometry': {'type': 'LineString'}, 'properties': {
            'network_edge_id': 'NE-a', 'line_id': 'IL-a', 'catalog_group_id': 'RC-a',
            'line_name': '原线路', 'way_tags': {'name': '原线路'}}},
        {'type': 'Feature', 'geometry': {'type': 'Point'}, 'properties': {
            'osm_node_id': 2, 'kind': 'switch', 'name': '原道岔'}},
        {'type': 'Feature', 'geometry': {'type': 'Point'}, 'properties': {
            'osm_node_id': 3, 'kind': 'station', 'name': '原站'}},
    ]
    original = deepcopy(features)
    apply_names({'features': features}, {'RC-a': {'display_name': '新线路'},
        'switch:node/2': {'display_name': '新道岔'}, 'station:node/3': {'display_name': '新站'}})
    assert [f['properties']['display_name'] for f in features] == ['新线路', '新道岔', '新站']
    for old, new in zip(original, features):
        assert all(new['properties'][k] == v for k, v in old['properties'].items())
    key = object_key(features[0]['properties'])
    apply_names({'features': features}, {key: {'display_name': '独立物理段'}})
    assert features[0]['properties']['line_display_name'] == '独立物理段'


def test_renamed_station_search_accepts_original_and_display_name(tmp_path):
    lib = DiskRailLineLibrary(install(tmp_path, [edge('a', 1, 2)]),
        metadata={'station:node/a': {'display_name': '合肥西站·京港场'}})
    aliases(lib.path, [('node/a', '合肥西', 100, 1, 0, 'source', 1, 118, 32)])
    for query in ('合肥西', '合肥西站', '京港场', '合肥西站·京港场'):
        values = lib.search_endpoints(query)
        assert values and values[0][1].startswith('合肥西站·京港场')


def test_renamed_physical_node_search_and_inferred_yard_names(tmp_path):
    lib = DiskRailLineLibrary(install(tmp_path, [edge('a', 1, 2)]),
        metadata={'object:osm_node_id:2': {'display_name': '东咽喉'}})
    assert 2 in dict(lib.search_endpoints('东咽喉', physical=True))
    assert lib.endpoint_label(2) == '东咽喉'
    features = [{'geometry': {'type': 'LineString'}, 'properties': {'line_name': name, 'catalog_group_id': 'ST-1'}}
                for name in ('未命名轨道 1', '京沪线')]
    apply_names({'features': features}, {'ST-1': {'display_name': '合肥站 · 站场股道（参考）',
        'source': 'automatic_station_group', 'snapshot': 'test', 'verification_status': 'automatic_reference'}})
    assert features[0]['properties']['line_display_name'].startswith('合肥站')
    assert 'line_display_name' not in features[1]['properties']


def test_signal_box_rename_uses_index_and_reuses_coordinates(tmp_path, qtbot, monkeypatch):
    from desktop.rail_catalog_ui import RailCatalog
    from desktop.tests.test_operating_ui import MapStub
    import desktop.rail_catalog_ui as catalog_module
    install(tmp_path, [edge('a', 1, 2)])
    widget = RailCatalog(tmp_path, tmp_path/'settings.json', MapStub())
    qtbot.addWidget(widget)
    widget.overrides['station:signalbox/test'] = {'display_name': '甲线路所', 'member_switch_ids': [1, 2]}
    original_connect = catalog_module.sqlite3.connect
    calls = []
    def connect(path, *args, **kwargs):
        calls.append(str(path))
        assert str(path).endswith('rail_lines.sqlite')  # No full rail.sqlite scan.
        return original_connect(path, *args, **kwargs)
    monkeypatch.setattr(catalog_module.sqlite3, 'connect', connect)
    first = widget.signal_box_geojson()
    assert len(first['features']) == 2 and len(calls) == 1
    widget.overrides['station:signalbox/test']['display_name'] = '乙线路所'
    renamed = widget.signal_box_geojson()
    assert len(calls) == 1
    assert renamed['features'][0]['geometry'] == first['features'][0]['geometry']
    assert renamed['features'][0]['properties']['name'] == '乙线路所'
