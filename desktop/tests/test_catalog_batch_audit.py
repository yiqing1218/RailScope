"""A mixed facility/track drag is one persistent operation and one undo."""
from types import SimpleNamespace
import sqlite3

from desktop.catalog_workspace import CatalogWorkspace
from desktop.rail_catalog_ui import RailCatalog


def test_mixed_station_drag_is_one_command(tmp_path, monkeypatch):
    store = CatalogWorkspace(tmp_path / 'workspace.json')
    store.load()
    index = tmp_path / 'directory.sqlite'
    with sqlite3.connect(index) as db:
        db.execute('CREATE TABLE rail_station_nodes(id TEXT, kind TEXT, object_id TEXT, parent_id TEXT, label TEXT)')
        db.execute("INSERT INTO rail_station_nodes VALUES('target','station','node/1',NULL,'甲站')")
    host = SimpleNamespace(catalog=SimpleNamespace(path=index),
        station_model=SimpleNamespace(ids_below=lambda _: (set(), {'ST-facility'}),
            track_ids_below=lambda _: {'object:network_edge_id:NE-1'}),
        station_record=lambda _: {'name': '甲站'},
        save_overrides=lambda changes, object_changes=None: store.update({**changes, **(object_changes or {})}))
    host._assign_station_facilities = lambda *args: RailCatalog._assign_station_facilities(host, *args)
    host._assign_station_assets = lambda *args: RailCatalog._assign_station_assets(host, *args)
    monkeypatch.setattr('desktop.rail_catalog_ui.QTimer.singleShot', lambda _, callback: callback())
    RailCatalog._move_paged_station_assets(host, ['mixed-selection'], 'target')
    assert len(store.undo_stack) == 1
    assert {value['station_id'] for value in store.values.values()} == {'node/1'}
    assert len(store.values) == 2
    store.undo()
    assert store.values == {}
    store.redo()
    assert len(store.values) == 2
    assert {value['station_id'] for value in store.values.values()} == {'node/1'}
