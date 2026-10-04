from contextlib import closing
import sqlite3

from desktop.rail_station_catalog_model import StationCatalogModel


def test_searches_are_read_only_per_window_and_refresh_after_rename(qtbot, tmp_path):
    path = tmp_path / 'directory.sqlite'
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE rail_station_nodes(id TEXT PRIMARY KEY,parent_id TEXT,label TEXT,kind TEXT,object_id TEXT,path TEXT,child_count INT,total INT,archived INT,searchable TEXT,station_total INT,facility_total INT,track_total INT)')
        db.executemany('INSERT INTO rail_station_nodes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', [
            ('folder:p', '', '省', 'folder', None, '["省"]', 2, 2, 0, '省', 2, 0, 0),
            ('station:node/1', 'folder:p', '甲站', 'station', 'node/1', '["省","node/1"]', 0, 1, 0, '甲站', 1, 0, 0),
            ('station:node/2', 'folder:p', '乙站', 'station', 'node/2', '["省","node/2"]', 0, 1, 0, '乙站', 1, 0, 0)])
    before = path.read_bytes()
    a, b = StationCatalogModel(path), StationCatalogModel(path)
    a.set_search('甲')
    b.set_search('乙')
    for model, expected in ((a, 'node/1'), (b, 'node/2'), (a, 'node/1')):
        model.fetchMore()
        parent = model.index(0, 0)
        model.fetchMore(parent)
        assert model._node(model.index(0, 0, parent)).object_id == expected
        assert model._count_children('folder:p') == 1
    assert path.read_bytes() == before
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE rail_station_nodes SET label='丙站',searchable='丙站' WHERE id='station:node/1'")
    a.refresh_labels({'station:node/1'})
    assert a._count_children('folder:p') == 0
    assert b._count_children('folder:p') == 1
    a._sessions.close_current_thread()
    b._sessions.close_current_thread()
