"""Directory commands must preserve unrelated pages and never visit upstream work."""
import json
import sqlite3

import pytest
from desktop.rail_catalog_ui import RailCatalog
from desktop.rail_station_catalog_model import StationCatalogModel, sync_station_catalog
from desktop.tests.test_operating_ui import MapStub


def widget_with_stations(qtbot, tmp_path, monkeypatch):
    records = {f'RL-{i}': {'name': f'Line {i:04}', 'way_ids': [i], 'track_role': 'main_track'} for i in range(700)}
    records['ST-yard'] = {'name': 'Depot track', 'way_ids': [701], 'track_role': 'shunting_track', 'station_id': 'node/1'}
    (tmp_path / 'rail_catalog.json').write_text(json.dumps(records), encoding='utf-8')
    widget = RailCatalog(tmp_path, tmp_path / 'settings.json', MapStub(), shared_path=tmp_path / 'absent.json')
    qtbot.addWidget(widget)
    stations = [{'id': f'node/{i}', 'name': name, 'province': 'Province', 'city': 'City',
                 'station_type': '车站', 'line_ids': [], 'line_names': []}
                for i, name in ((1, 'Station A'), (2, 'Station B'))]
    monkeypatch.setattr('desktop.rail_station_catalog_model.rail_station_records', lambda *a, **k: (stations, 2))
    with sqlite3.connect(tmp_path / 'rail_lines.sqlite') as db:
        db.executescript('CREATE TABLE features(id INTEGER PRIMARY KEY,data TEXT); CREATE TABLE rail_feature_groups(feature_id,group_id);')
    sync_station_catalog(tmp_path, widget.catalog.path, [], widget.overrides)
    widget.station_model = StationCatalogModel(widget.catalog.path)
    widget.station_browser.setModel(widget.station_model)
    widget.station_model.fetchMore()
    for record in stations:
        record['_source_name'] = record['name']
        record['_source_station_type'] = '车站'
        widget.station_record_by_id[record['id']] = record
    widget.map.calls.clear()
    return widget


def forbid_full_work(monkeypatch, widget):
    def reject(*a, **k):
        raise AssertionError('Directory command entered a global rebuild/hash/reset')
    for method in ('populate', '_populate_paged_directory', '_prepare_station_catalog'):
        monkeypatch.setattr(widget, method, reject)
    for model in (widget.line_model, widget.facility_model, widget.station_model):
        monkeypatch.setattr(model, 'reset_from_disk', reject)
    monkeypatch.setattr('desktop.rail_catalog_model._directory_signature', reject)
    monkeypatch.setattr('desktop.rail_catalog_ui.station_catalog_signature', reject)
    monkeypatch.setattr('desktop.rail_catalog_ui.sync_station_catalog', reject)


@pytest.mark.parametrize('scope', ['catalog', 'object', 'assembly'])
def test_line_function_edit_and_history_do_not_prepare_station_directory(qtbot, tmp_path, monkeypatch, scope):
    widget = widget_with_stations(qtbot, tmp_path, monkeypatch)
    key = 'RL-0'
    if scope == 'assembly':
        widget.merge_line_segments({'RL-0', 'RL-1'}, 'Shared')
    elif scope == 'object':
        key = 'object:network_edge_id:NE-one'
    widget._save_local_overrides({key: {'rail_semantics': {'line_role': 'branch_line'}}})
    before = widget.workspace.revisions.copy()
    with sqlite3.connect(widget.catalog.path) as db:
        station_rows = db.execute('SELECT * FROM rail_station_nodes ORDER BY id').fetchall()
    events = []
    widget.entities_changed.connect(events.append)
    forbid_full_work(monkeypatch, widget)
    delta = {key: {'rail_semantics': {'line_role': 'connecting_line',
                                   'source': 'workspace_override', 'verification_status': 'user_verified'}}}
    widget.save_overrides(delta if scope != 'object' else {}, delta if scope == 'object' else None)
    widget.undo_catalog()
    widget.redo_catalog()
    assert len(events) == 3
    assert widget.overrides[key]['rail_semantics']['line_role'] == 'connecting_line'
    assert widget.workspace.revisions['semantic'] == before['semantic'] + 3
    assert widget.workspace.revisions['topology'] == before['topology']
    with sqlite3.connect(widget.catalog.path) as db:
        assert db.execute('SELECT * FROM rail_station_nodes ORDER BY id').fetchall() == station_rows
    # Reopening with the current revision must keep the existing station cache.
    monkeypatch.undo()
    assert widget._prepare_station_catalog() is False


def test_line_move_and_history_keep_unrelated_loaded_branch(qtbot, tmp_path, monkeypatch):
    widget = widget_with_stations(qtbot, tmp_path, monkeypatch)
    index = widget.line_model.reveal_catalog_id('RL-699')
    unrelated = widget.line_model._node(index)
    ancestors = index.parent().internalPointer()
    forbid_full_work(monkeypatch, widget)
    events = []
    widget.metadata_changed.connect(lambda: events.append('broad'))
    widget.move_items({'RL-0'}, ['Custom', 'New'])
    widget.undo_catalog()
    widget.redo_catalog()
    assert unrelated in ancestors.children
    assert widget.parents('RL-0') == ('Custom', 'New')
    assert widget.line_model.reveal_catalog_id('RL-0').isValid()
    assert events == [] and widget.map.calls == []


def test_station_move_is_local_and_sends_no_map_selection(qtbot, tmp_path, monkeypatch):
    widget = widget_with_stations(qtbot, tmp_path, monkeypatch)
    with sqlite3.connect(widget.catalog.path) as db:
        untouched = db.execute("SELECT * FROM rail_station_nodes WHERE object_id='node/2'").fetchone()
    forbid_full_work(monkeypatch, widget)
    widget.save_station_changes({'node/1'}, folder_path=['Custom', 'Station'])
    widget.undo_catalog()
    widget.redo_catalog()
    with sqlite3.connect(widget.catalog.path) as db:
        assert db.execute("SELECT * FROM rail_station_nodes WHERE object_id='node/2'").fetchone() == untouched
        assert json.loads(db.execute("SELECT path FROM rail_station_nodes WHERE id='station:node/1'").fetchone()[0]) == ['stations', 'Custom', 'Station', '技术作业待核实', '业务性质待核实', 'node/1']
    assert widget.map.calls == []


def test_station_move_keeps_expanded_descendants_selected(qtbot, tmp_path, monkeypatch):
    from PySide6.QtCore import QItemSelectionModel
    widget = widget_with_stations(qtbot, tmp_path, monkeypatch)
    model, view = widget.station_model, widget.station_browser
    with sqlite3.connect(widget.catalog.path) as db:
        db.execute("INSERT INTO rail_station_nodes(id,parent_id,label,kind,object_id,path,searchable) VALUES('test-track','facility:ST-yard','Track','facility_track','NE-focus','[]','track')")
        db.execute("UPDATE rail_station_nodes SET child_count=child_count+1 WHERE id='facility:ST-yard'")
    station = model.index_for_key('station:node/1')
    facility = model.index_for_key('facility:ST-yard')
    track = model.index_for_key('test-track')
    view.setExpanded(station, True)
    view.setExpanded(facility, True)
    selection = view.selectionModel()
    for index in (station, track):
        selection.select(index, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    selection.setCurrentIndex(track, QItemSelectionModel.SelectionFlag.NoUpdate)
    widget.map.calls.clear()
    forbid_full_work(monkeypatch, widget)
    widget.save_station_changes({'node/1'}, folder_path=['Custom','Expanded'])
    assert {model._node(index).object_id for index in selection.selectedRows()} == {'node/1','NE-focus'}
    assert model._node(selection.currentIndex()).object_id == 'NE-focus'
    assert view.isExpanded(model.index_for_key('station:node/1'))
    assert view.isExpanded(model.index_for_key('facility:ST-yard'))
    assert widget.map.calls == []


def test_facility_folder_does_not_rebuild_ownership(qtbot, tmp_path, monkeypatch):
    widget = widget_with_stations(qtbot, tmp_path, monkeypatch)
    with sqlite3.connect(widget.catalog.path) as db:
        before = db.execute("SELECT parent_id FROM rail_station_nodes WHERE id='facility:ST-yard'").fetchone()
    forbid_full_work(monkeypatch, widget)
    widget.move_items({'ST-yard'}, ['Custom', 'Depot'])
    widget.undo_catalog()
    widget.redo_catalog()
    with sqlite3.connect(widget.catalog.path) as db:
        assert db.execute("SELECT parent_id FROM rail_station_nodes WHERE id='facility:ST-yard'").fetchone() == before
    assert widget.map.calls == []


def test_assembly_move_undo_and_shared_style_are_local(qtbot, tmp_path, monkeypatch):
    widget = widget_with_stations(qtbot, tmp_path, monkeypatch)
    assembly = widget.merge_line_segments({'RL-0', 'RL-1'}, 'Shared')
    widget.map.calls.clear()
    forbid_full_work(monkeypatch, widget)
    widget.move_items({'RL-0'}, ['Custom', 'Assembly'])
    assert widget.parents('RL-1') == ('Custom', 'Assembly')
    assert widget._last_saved_keys == {'line-assembly:' + assembly}
    widget.undo_catalog()
    widget.redo_catalog()
    widget.save_overrides({'RL-0': {'color': '#123456', 'display_name': 'Renamed'}})
    assert widget.overrides['RL-1']['display_name'] == 'Renamed'
    assert widget.overrides['RL-1']['color'] == '#123456'
    assert widget.line_model.reveal_catalog_id('RL-1').isValid()


def test_explicit_assignment_and_history_update_only_related_stations(qtbot, tmp_path, monkeypatch):
    widget = widget_with_stations(qtbot, tmp_path, monkeypatch)
    forbid_full_work(monkeypatch, widget)
    widget._assign_station_assets({'ST-yard'}, set(), 'node/2')
    with sqlite3.connect(widget.catalog.path) as db:
        parent = db.execute("SELECT parent_id FROM rail_station_nodes WHERE id='facility:ST-yard'").fetchone()[0]
        assert 'node/2' in parent
    widget.undo_catalog()
    widget.redo_catalog()
    widget._assign_station_assets({'ST-yard'}, set(), None)
    with sqlite3.connect(widget.catalog.path) as db:
        assert '待核对' in db.execute("SELECT parent_id FROM rail_station_nodes WHERE id='facility:ST-yard'").fetchone()[0]
    assert widget.map.calls == []


def test_moving_one_same_name_member_does_not_resolve_all_siblings(qtbot, tmp_path, monkeypatch):
    widget = widget_with_stations(qtbot, tmp_path, monkeypatch)
    with sqlite3.connect(widget.catalog.path) as db:
        db.execute("UPDATE catalog SET data=json_set(data,'$.name','Shared source label') WHERE id LIKE 'RL-%'")
        db.execute("DELETE FROM metadata WHERE key IN ('paged_directory_signature','directory_cache_revision')")
    widget._populate_paged_directory()
    forbid_full_work(monkeypatch, widget)
    resolved = []
    original = widget._resolve_directory_record
    def local(key, record):
        resolved.append(key)
        return original(key, record)
    monkeypatch.setattr(widget, '_resolve_directory_record', local)
    widget.move_items({'RL-0'}, ['Custom', 'Single member'])
    assert set(resolved) == {'RL-0'}
    with sqlite3.connect(widget.catalog.path) as db:
        assert db.execute("SELECT d.total FROM rail_directory_nodes d JOIN rail_directory_members m ON m.node_id=d.id WHERE m.catalog_id='RL-1'").fetchone()[0] == 699


def test_assignment_preserves_domain_id_and_resolves_source_alias(qtbot, tmp_path, monkeypatch):
    widget = widget_with_stations(qtbot, tmp_path, monkeypatch)
    forbid_full_work(monkeypatch, widget)
    widget.save_overrides({'ST-yard': {'station_id': 'STN-stable-domain', 'station_source': 'node/2', 'station_assignment': 'manual'}})
    assert widget.local_overrides['ST-yard']['station_id'] == 'STN-stable-domain'
    with sqlite3.connect(widget.catalog.path) as db:
        assert 'node/2' in db.execute("SELECT parent_id FROM rail_station_nodes WHERE id='facility:ST-yard'").fetchone()[0]


def test_group_assignment_keeps_more_specific_track_owner(qtbot, tmp_path, monkeypatch):
    widget = widget_with_stations(qtbot, tmp_path, monkeypatch)
    edge = 'object:network_edge_id:NE-local'
    with sqlite3.connect(widget.catalog.path) as db:
        parent = db.execute("SELECT parent_id FROM rail_station_nodes WHERE id='facility:ST-yard'").fetchone()[0]
        db.execute("INSERT INTO rail_station_nodes(id,parent_id,label,kind,object_id,path,searchable) VALUES(?,?,?,'facility_track',?,?,?)",
            ('segment:900', parent, 'Private track', edge, '["stations","node/1","track"]', 'private track'))
        db.execute('INSERT INTO rail_facility_track_owners VALUES(?,?,?,?)', (edge,'node/1','{}','ST-yard'))
        db.execute('INSERT INTO rail_facility_track_baseline VALUES(?,?,?,?)', (edge,'node/1','{}','ST-yard'))
    widget._save_local_overrides({edge: {'station_id': 'node/1', 'station_assignment': 'manual'}})
    forbid_full_work(monkeypatch, widget)
    widget._assign_station_assets({'ST-yard'}, set(), 'node/2')
    with sqlite3.connect(widget.catalog.path) as db:
        assert 'node/1' in db.execute("SELECT parent_id FROM rail_station_nodes WHERE id='segment:900'").fetchone()[0]
        assert db.execute('SELECT station_id FROM rail_facility_track_owners WHERE object_id=?',(edge,)).fetchone()[0] == 'node/1'


def test_assembly_visible_counts_survive_single_owner_undo(qtbot, tmp_path, monkeypatch):
    widget = widget_with_stations(qtbot, tmp_path, monkeypatch)
    widget.merge_line_segments({'RL-0','RL-1'}, 'Shared')
    widget.line_model.set_visibility({'RL-0','RL-1'})
    widget.move_items({'RL-0','RL-1'}, ['Custom','Assembly'])
    new_folder = 'folder:' + json.dumps(['lines','Custom','Assembly'], ensure_ascii=False)
    widget.undo_catalog()
    assert widget.line_model.visible_counts.get(new_folder, 0) == 0
    widget.redo_catalog()
    with sqlite3.connect(widget.catalog.path) as db:
        node,path = db.execute("SELECT d.id,d.path FROM rail_directory_nodes d JOIN rail_directory_members m ON m.node_id=d.id WHERE m.catalog_id='RL-0'").fetchone()
    folder = 'folder:' + json.dumps(json.loads(path), ensure_ascii=False)
    assert widget.line_model.visible_counts[node] == 2
    assert widget.line_model.visible_counts[folder] == 2


def test_cross_parent_moves_keep_multi_selection_and_focus(qtbot, tmp_path, monkeypatch):
    from PySide6.QtCore import QItemSelectionModel
    widget = widget_with_stations(qtbot, tmp_path, monkeypatch)
    view,model = widget.line_browser,widget.line_model
    indexes = [model.reveal_catalog_id(key) for key in ('RL-0','RL-699')]
    selection = view.selectionModel()
    for index in indexes:
        selection.select(index, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    selection.setCurrentIndex(indexes[0], QItemSelectionModel.SelectionFlag.NoUpdate)
    widget.map.calls.clear()
    forbid_full_work(monkeypatch,widget)
    widget.move_items({'RL-0'}, ['Custom','Selected'])
    assert {model._node(index).object_id for index in selection.selectedRows()} == {'RL-0','RL-699'}
    assert model._node(selection.currentIndex()).object_id == 'RL-0'
    assert widget.map.calls == []
