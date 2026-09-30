"""Selection and directory queries must follow entity IDs and literal paths."""
import json
import sqlite3
from types import SimpleNamespace

from desktop.lazy_directory import SqliteDirectoryModel
from desktop.metro_store import StationLookup
from launcher import Desk


def test_right_click_on_selected_row_keeps_multiselection(qtbot):
    from PySide6.QtCore import QItemSelectionModel, Qt
    from PySide6.QtGui import QStandardItem, QStandardItemModel
    from PySide6.QtTest import QTest
    from desktop.rail_catalog_model import RailDirectoryView
    model = QStandardItemModel()
    model.appendRow(QStandardItem('第一条'))
    model.appendRow(QStandardItem('第二条'))
    view = RailDirectoryView()
    qtbot.addWidget(view)
    view.setModel(model)
    view.show()
    flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
    for row in range(2):
        view.selectionModel().select(model.index(row, 0), flags)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.RightButton,
                     pos=view.visualRect(model.index(1, 0)).center())
    assert len(view.selectionModel().selectedRows()) == 2


def test_map_multiselect_uses_indexed_physical_station_lookup(tmp_path, monkeypatch):
    path = tmp_path / 'metro.sqlite'
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE station_aliases(id TEXT,physical_id TEXT);'
                         'CREATE INDEX physical ON station_aliases(physical_id);')
        db.executemany('INSERT INTO station_aliases VALUES(?,?)',
                       [('alias-A', 'ST-1'), ('alias-B', 'ST-1'), ('alias-C', 'ST-2')])
    lookup = StationLookup(path, {})
    def full_scan():
        raise AssertionError('Map selection must not scan the national catalog')
    monkeypatch.setattr(lookup, 'items', full_scan)
    window = SimpleNamespace(selected_features=[
        {'layer': 'metro-stations', 'properties': {'infrastructure_id': 'ST-1'}},
        {'layer': 'metro-stations', 'properties': {'infrastructure_id': 'ST-2'}},
        {'layer': 'metro-stations', 'properties': {'infrastructure_id': 'ST-1'}}],
        rail_catalog_widget=SimpleNamespace(catalog={}), route_lookup={}, station_lookup=lookup)
    assert Desk._selected_catalog_objects(window)[3] == {'alias-A', 'alias-B', 'alias-C'}


def test_folder_wildcards_do_not_select_or_search_other_paths(qtbot, tmp_path):
    path = tmp_path / 'directory.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE directory_nodes(id TEXT,parent_id TEXT,label TEXT,kind TEXT,'
                   'object_id TEXT,path TEXT,child_count INTEGER,total INTEGER,archived INTEGER)')
        for folder, name in [('A_%', '本目录站'), ('AXxx', '另一目录站')]:
            key = 'folder:' + json.dumps([folder], ensure_ascii=False)
            db.execute('INSERT INTO directory_nodes VALUES(?,?,?,?,?,?,?,?,?)',
                       (key, '', folder, 'folder', None, json.dumps([folder]), 1, 1, 0))
            db.execute('INSERT INTO directory_nodes VALUES(?,?,?,?,?,?,?,?,?)',
                       (name, key, name, 'object', name, json.dumps([folder, '二级']), 0, 1, 0))
    model = SqliteDirectoryModel(path)
    key = 'folder:' + json.dumps(['A_%'], ensure_ascii=False)
    assert model.ids_below(key) == {'本目录站'}
    model.set_search('另一目录站')
    model.fetchMore()
    assert model.rowCount() == 1
    assert model.data(model.index(0, 0)).startswith('AXxx')
