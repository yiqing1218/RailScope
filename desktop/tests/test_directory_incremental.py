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
        assert json.loads(db.execute("SELECT path FROM rail_station_nodes WHERE id='station:node/1'").fetchone()[0]) == ['stations', 'Custom', 'Station', 'node/1']
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
