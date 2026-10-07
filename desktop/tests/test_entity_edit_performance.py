"""Field invalidation, real owner updates and bounded durable edits."""
import json
from types import SimpleNamespace
import pytest

from desktop.catalog_workspace import CatalogWorkspace, read_overrides
from desktop.tests.test_catalog_workspace import catalog


def test_single_edit_does_not_rewrite_large_legacy_seed(tmp_path):
    path = tmp_path / 'workspace.json'
    path.write_text(json.dumps({f'RL-{i}': {'remarks': '原值'} for i in range(10000)}), encoding='utf-8')
    original = path.read_bytes()
    store = CatalogWorkspace(path)
    store.load()
    store.update({'RL-3': {'remarks': '新值'}})
    assert path.read_bytes() == original
    assert read_overrides(path)['RL-3']['remarks'] == '新值'
    reloaded = CatalogWorkspace(path)
    reloaded.load()
    assert reloaded.values == store.values
    store.undo()
    assert read_overrides(path)['RL-3']['remarks'] == '原值'
    store.redo()
    assert read_overrides(path)['RL-3']['remarks'] == '新值'


def test_query_snapshot_detects_external_edits_and_workspace_replacement(tmp_path):
    path = tmp_path / 'workspace.json'
    path.write_text('{"RL-1":{"remarks":"原值"}}', encoding='utf-8')
    store = CatalogWorkspace(path)
    store.load()
    assert store.cached_values() is store.values
    store.update({'RL-1': {'remarks': '本窗口编辑'}})
    assert store.cached_values()['RL-1']['remarks'] == '本窗口编辑'
    other = CatalogWorkspace(path)
    other.load()
    other.update({'RL-1': {'remarks': '另一窗口编辑'}})
    assert store.cached_values() is None
    store.load()
    assert store.cached_values()['RL-1']['remarks'] == '另一窗口编辑'
    replacement = CatalogWorkspace(tmp_path / 'replacement.json')
    replacement.load()
    from contextlib import closing
    import sqlite3
    from desktop.catalog_workspace import edit_database
    with closing(sqlite3.connect(edit_database(replacement.path))) as src, closing(sqlite3.connect(edit_database(path))) as dst:
        src.backup(dst)
    assert store.cached_values() is None


def test_first_line_library_uses_loaded_workspace_and_reload_after_external_edit(tmp_path, monkeypatch):
    from desktop.rail_ui import RailEditor
    import desktop.rail_ui as ui
    import desktop.rail_line_store as line_store
    from desktop.tests.test_query_sessions import install, edge
    install(tmp_path, [edge('a', 1, 2)])
    monkeypatch.setattr(line_store, 'index_ready', lambda *args: True)
    path = tmp_path / 'settings.json'
    store = CatalogWorkspace(path)
    store.load()
    owner = SimpleNamespace(directory=tmp_path, path=tmp_path / 'plan.json',
        catalog_metadata_path=path, catalog_editor=SimpleNamespace(workspace=store),
        graph={'edges': [], 'points': []})
    reads = []
    original = ui.read_overrides
    monkeypatch.setattr(ui, 'read_overrides', lambda p: reads.append(p) or original(p))
    first = RailEditor.line_library(owner)
    assert not reads and first.lines
    other = CatalogWorkspace(path)
    other.load()
    key = next(iter(first.lines))
    other.update({key: {'rail_semantics': {'track_role': 'maintenance_track'}}})
    second = RailEditor.line_library(owner)
    assert reads == [path] and second is not first
    assert second.line_semantics(key)['track_role'] == 'maintenance_track'


def test_station_without_legacy_tree_item_updates_authoritative_record(qtbot, tmp_path):
    widget = catalog(qtbot, tmp_path)
    record = {'id': 'way/11', 'name': '甲车辆段', 'station_type': '车辆段',
              'province': '甲省', 'city': '甲市', 'line_ids': [], 'line_names': []}
    widget.station_record_by_id[record['id']] = record
    del widget.station_items
    widget.save_station_override('way/11', display_name='乙车辆段')
    assert record['name'] == '乙车辆段'
    assert widget.station_record('way/11') is record
    widget.undo_catalog()
    assert record['name'] == '甲车辆段'
    widget.redo_catalog()
    assert record['name'] == '乙车辆段'


def test_remarks_only_does_not_refresh_map_directory_or_topology(qtbot, tmp_path):
    widget = catalog(qtbot, tmp_path)
    widget.map.calls.clear()
    changed = []
    widget.metadata_changed.connect(lambda: changed.append('topology'))
    widget.presentation_changed.connect(lambda: changed.append('viewport'))
    widget.save_overrides({'RL-1': {'technical_attributes': {'remarks': '备注'}}})
    assert changed == []
    assert widget.map.calls == []
    widget.undo_catalog()
    assert changed == []
    assert widget.map.calls == []


def test_unchanged_connections_are_not_revalidated_or_overwritten(qtbot):
    from desktop.rail_connection_ui import StationConnectionSelector
    calls = []
    library = SimpleNamespace(connected_lines=lambda _: [{'id': 'RL-1', 'name': '甲线'}],
        station_connection_override=lambda *args: calls.append(args) or [{'line_id': ident} for ident in args[1]])
    editor = StationConnectionSelector(library, 'station:way/11')
    qtbot.addWidget(editor)
    assert editor.connections() is None
    assert calls == []
    editor._append('RL-2', '乙线')
    assert editor.connections() == [{'line_id': 'RL-1'}, {'line_id': 'RL-2'}]
    assert len(calls) == 1


def test_changed_connection_preserves_existing_fixed_anchor(qtbot):
    from desktop.rail_connection_ui import StationConnectionSelector
    fixed = {'line_id': 'RL-1', 'anchor_node': 'NN-1', 'anchor_policy': 'fixed',
             'distance_m': 0, 'source': 'manual', 'verification_status': 'user_verified'}
    library = SimpleNamespace(connected_lines=lambda _: [{'id': 'RL-1', 'name': '甲线'}],
        _station_sources=lambda _: ['way/11'], _station_connection_override=lambda _: [fixed],
        station_connection_override=lambda _, ids: [{'line_id': ident, 'anchor_node': 'NN-2'} for ident in ids])
    editor = StationConnectionSelector(library, 'station:way/11')
    qtbot.addWidget(editor)
    editor._append('RL-2', '乙线')
    assert editor.connections()[0] == fixed


def test_failed_incremental_transaction_keeps_state_and_history(tmp_path, monkeypatch):
    store = CatalogWorkspace(tmp_path / 'workspace.json')
    store.load()
    store.update({'RL-1': {'display_name': '甲'}})
    before = dict(store.values)
    def fail(*_):
        raise OSError('disk failure')
    monkeypatch.setattr(store, '_write_entries', fail)
    with pytest.raises(OSError, match='disk failure'):
        store.update({'RL-1': {'display_name': '乙'}})
    assert store.values == before and len(store.undo_stack) == 1
    assert read_overrides(store.path) == before


def test_related_line_station_semantics_save_as_one_undoable_command(qtbot, tmp_path):
    widget = catalog(qtbot, tmp_path)
    record = {'id': 'way/11', 'name': '甲站', 'station_type': '客运站',
              'line_ids': [], 'line_names': [], 'province': '', 'city': ''}
    widget.station_record_by_id['way/11'] = record
    events = []
    widget.topology_changed.connect(lambda delta: events.append(delta))
    links = [{'line_id': 'RL-1', 'anchor_node': 'NN-1', 'anchor_policy': 'fixed'}]
    widget.save_overrides({'RL-1': {'display_name': '新线名'}},
        {'station:way/11': {'connected_lines': links}, 'IL-1': {'connected_line_ids': ['IL-2']}})
    assert len(widget.workspace.undo_stack) == 1 and len(events) == 1
    assert record['line_ids'] == ['RL-1']
    assert read_overrides(widget.workspace.path)['station:way/11']['connected_lines'] == links
    widget.undo_catalog()
    assert widget.display_name('RL-1') == '源线路'
    assert 'station:way/11' not in widget.local_overrides and 'IL-1' not in widget.local_overrides
    widget.redo_catalog()
    assert widget.display_name('RL-1') == '新线名'
    assert widget.local_overrides['station:way/11']['connected_lines'] == links


def test_failed_related_edit_changes_no_owner_or_views(qtbot, tmp_path, monkeypatch):
    widget = catalog(qtbot, tmp_path)
    before = dict(widget.overrides)
    events = []
    widget.topology_changed.connect(events.append)
    monkeypatch.setattr(widget.workspace, '_write_entries', lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError, match='disk full'):
        widget.save_overrides({'RL-1': {'display_name': '新线名'}},
                             {'station:node/1': {'connected_lines': [{'line_id': 'RL-1'}]}})
    assert widget.overrides == before and not widget.workspace.undo_stack and not events


def test_tombstone_does_not_resurrect_an_exchange_seed(tmp_path):
    seed = tmp_path / 'exchange.json'
    seed.write_text(json.dumps({'RL-1': {'display_name': '种子'}}), encoding='utf-8')
    store = CatalogWorkspace(tmp_path / 'workspace.json')
    store.load((seed,))
    store.update({'RL-2': {'display_name': '临时'}})
    store.undo()
    store.load((seed,))
    assert store.values == {'RL-1': {'display_name': '种子'}}


def test_corrupt_incremental_database_blocks_edits_and_preserves_bytes(tmp_path):
    from desktop.catalog_workspace import edit_database
    path = tmp_path / 'workspace.json'
    database = edit_database(path)
    database.write_bytes(b'corrupt database')
    store = CatalogWorkspace(path)
    with pytest.raises(ValueError, match='未载入'):
        store.load()
    with pytest.raises(ValueError, match='载入'):
        store.update({'RL-1': {'display_name': '乙'}})
    assert database.read_bytes() == b'corrupt database'


def test_incremental_map_commands_coalesce_every_owner_and_deletion():
    from desktop.map_commands import MapCommands
    queue = MapCommands()
    queue.put('patchRailEntities', ({'station:node/1': {'display_name': '甲'}},), '')
    queue.put('patchRailEntities', ({'station:node/2': None},), '')
    queue.put('patchRailEntities', ({'station:node/1': {'display_name': '乙'}},), '')
    result = queue.pop()
    assert 'node/1' in result and 'node/2' in result and '乙' in result and '甲' not in result
    assert queue.pop() is None and queue.entity_patches == {}


def test_station_placement_moves_only_owner_subtree_and_keeps_totals(tmp_path):
    import sqlite3
    from desktop.rail_station_catalog_model import update_station_placement
    index = tmp_path / 'directory.sqlite'
    with sqlite3.connect(index) as db:
        db.execute('CREATE TABLE rail_station_nodes(id TEXT PRIMARY KEY,parent_id TEXT,label TEXT,kind TEXT,object_id TEXT,path TEXT,child_count INT DEFAULT 0,total INT DEFAULT 0,archived INT DEFAULT 0,searchable TEXT,station_total INT DEFAULT 0,facility_total INT DEFAULT 0,track_total INT DEFAULT 0)')
        db.executemany('INSERT INTO rail_station_nodes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', [
            ('folder:["stations","旧省"]', '', '旧省', 'folder', None, '["stations","旧省"]', 1, 2, 0, '旧省', 1, 1, 0),
            ('station:way/1', 'folder:["stations","旧省"]', '甲车辆段', 'station', 'way/1', '["stations","旧省","way/1"]', 1, 2, 0, '甲车辆段', 1, 1, 0),
            ('facility:ST-1', 'station:way/1', '股道', 'facility', 'ST-1', '["stations","旧省","way/1","ST-1"]', 0, 1, 0, '股道', 0, 1, 0),
            ('station:way/2', '', '另一站', 'station', 'way/2', '["stations","way/2"]', 0, 1, 0, '另一站', 1, 0, 0)])
    record = {'id': 'way/1', 'name': '甲车辆段', 'station_type': '车辆段'}
    assert update_station_placement(index, record, {'folder_path': ['新省'], 'archived': True})
    with sqlite3.connect(index) as db:
        # Descendants inherit placement through stable parents; moving a station
        # must not rewrite every physical track's redundant source-time path.
        assert db.execute('SELECT parent_id,path FROM rail_station_nodes WHERE id=?', ('facility:ST-1',)).fetchone() == ('station:way/1', '["stations","旧省","way/1","ST-1"]')
        assert db.execute('SELECT parent_id FROM rail_station_nodes WHERE id=?', ('station:way/1',)).fetchone()[0] == 'folder:["stations","已归档","新省","其他","车辆段"]'
        assert db.execute('SELECT total,station_total,facility_total FROM rail_station_nodes WHERE id=?', ('folder:["stations","已归档","新省"]',)).fetchone() == (2, 1, 1)
        assert db.execute('SELECT path FROM rail_station_nodes WHERE id=?', ('station:way/2',)).fetchone()[0] == '["stations","way/2"]'


def test_presentation_http_replays_current_names_and_undo(qtbot, monkeypatch):
    from pathlib import Path
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.request import urlopen, Request
    from urllib.error import HTTPError
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    import launcher
    server = ThreadingHTTPServer(('127.0.0.1', 0), launcher.LocalHandler)
    server.config = {'railDisplayOverrides': {'station:way/1': {'display_name': '乙车辆段'}}}
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    feature = {'geometry': {'type': 'Point'}, 'properties': {'station_source_id': 'way/1', 'name': '甲车辆段'}}
    try:
        def request(body):
            req = Request(f'http://127.0.0.1:{server.server_port}/api/entity-presentation',
                          data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
            with urlopen(req, timeout=3) as response:
                return json.load(response)
        values = request([feature])
        assert values[0]['display_name'] == '乙车辆段'
        server.config['railDisplayOverrides'].clear()
        values = request([{'geometry': {'type': 'Point'}, 'properties': values[0]}])
        assert values[0]['name'] == '甲车辆段' and not values[0].get('display_name')
        with pytest.raises(HTTPError) as error:
            request({'invalid': True})
        assert error.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_line_renderer_overrides_keep_geometry_and_restore_on_undo():
    from desktop.display_names import apply_rail_presentation, restore_presentation
    feature = {'geometry': {'type': 'LineString', 'coordinates': [[120, 30], [121, 31]]},
               'properties': {'catalog_group_id': 'RL-1', 'line_id': 'RL-1'}}
    original = list(feature['geometry']['coordinates'])
    apply_rail_presentation({'features': [feature]}, {}, {'RL-1': {'color': '#8b2535', 'width': 3.25}})
    assert feature['properties']['rail_display_color'] == '#8b2535'
    assert feature['properties']['rail_display_width'] == 3.25
    assert feature['geometry']['coordinates'] == original
    restore_presentation(feature['properties'])
    apply_rail_presentation({'features': [feature]}, {}, {})
    assert 'rail_display_color' not in feature['properties'] and 'rail_display_width' not in feature['properties']


def test_saved_edit_and_undo_repair_an_old_directory_cache_after_restart(tmp_path):
    import sqlite3
    from desktop.rail_catalog_ui import RailCatalog
    source, cache = tmp_path / 'rail_lines.sqlite', tmp_path / 'cache.sqlite'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE station_directory(source_id TEXT PRIMARY KEY,name TEXT)')
        db.execute("INSERT INTO station_directory VALUES('way/1','源站')")
    with sqlite3.connect(cache) as db:
        db.execute('CREATE TABLE rail_station_nodes(id TEXT PRIMARY KEY,label TEXT,path TEXT,searchable TEXT)')
        db.execute('INSERT INTO rail_station_nodes VALUES(?,?,?,?)', ('station:way/1','旧缓存','["stations","省","way/1"]','旧缓存'))
    workspace = CatalogWorkspace(tmp_path / 'workspace.json')
    workspace.load()
    workspace.update({'station:way/1': {'display_name': '持久新名'}})
    model = SimpleNamespace(refresh_labels=lambda keys: None)
    class Catalog(dict):
        path = cache
    host = SimpleNamespace(workspace=workspace, catalog=Catalog(), directory=tmp_path,
        station_model=model, overrides=workspace.values, _refresh_line_labels=lambda keys: None)
    for name in ('持久新名', '源站'):
        reopened = CatalogWorkspace(workspace.path)
        reopened.load()
        host.workspace, host.overrides = reopened, reopened.values
        RailCatalog._replay_workspace_labels(host)
        with sqlite3.connect(cache) as db:
            assert db.execute('SELECT label FROM rail_station_nodes').fetchone()[0] == name
        workspace.undo()


@pytest.mark.parametrize('value', [{'color': 'red'}, {'width': -1}, {'width': float('nan')}])
def test_invalid_renderer_fields_do_not_create_an_edit_or_history(tmp_path, value):
    workspace = CatalogWorkspace(tmp_path / 'workspace.json')
    workspace.load()
    with pytest.raises(ValueError):
        workspace.update({'RL-1': value})
    assert not workspace.values and not workspace.undo_stack


def test_canonical_repository_does_not_silently_drop_a_corrupt_workspace(tmp_path, monkeypatch):
    from desktop import rail_ui
    from desktop.catalog_workspace import edit_database
    path = tmp_path / 'workspace.json'
    edit_database(path).write_bytes(b'corrupt')
    actual = rail_ui.read_overrides
    monkeypatch.setattr(rail_ui, 'read_overrides', lambda candidate: actual(candidate) if candidate == path else {})
    host = SimpleNamespace(catalog_metadata_path=path)
    with pytest.raises(ValueError, match='未载入'):
        rail_ui.RailEditor.canonical_repository(host, tmp_path / 'identity.sqlite')


def test_renderer_overrides_survive_directory_exchange(tmp_path):
    from desktop.rail_catalog_ui import save_line_directory
    source = CatalogWorkspace(tmp_path / 'source.json')
    source.load()
    source.update({'RL-1': {'display_name': '线路', 'color': '#8b2535', 'width': 3.25}})
    exchange = tmp_path / 'exchange.json'
    save_line_directory(exchange, source.values)
    restored = CatalogWorkspace(tmp_path / 'restored.json')
    restored.load((exchange,))
    assert restored.values == source.values


def test_picked_feature_restores_encoded_base_before_undo_replay():
    from desktop.display_names import restore_presentation
    props = {'station_source_id': 'way/1', 'name': '修改名', 'display_name': '修改名',
             '_presentation_base': json.dumps({'name': '来源名'})}
    restore_presentation(props)
    assert props['name'] == '来源名' and 'display_name' not in props
    assert props['station_source_id'] == 'way/1'
