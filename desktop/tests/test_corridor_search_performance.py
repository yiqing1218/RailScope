from contextlib import contextmanager
import sqlite3

from desktop.rail_line_store import DiskRailLineLibrary
from desktop.tests.test_line_membership import aliases, edge, install


def test_connected_station_lookup_is_bounded_with_large_unrelated_directory(tmp_path, monkeypatch):
    index = install(tmp_path, [edge('a',1,2), edge('b',2,3,highspeed='yes')])
    aliases(index,[('node/a','始发站',100,1,1,'source',1,118,32)])
    with sqlite3.connect(index) as db:
        db.executemany('INSERT INTO lines VALUES(?,?,?,?,?)',
                       ((f'unrelated-{i}', f'其他线路{i}', 0, '正线', 'test') for i in range(12000)))
    library = DiskRailLineLibrary(index)
    original = library.connect
    steps = [0]

    @contextmanager
    def measured():
        with original() as db:
            def progress():
                steps[0] += 1000
                return steps[0] > 15000
            db.set_progress_handler(progress,1000)
            yield db

    monkeypatch.setattr(library,'connect',measured)
    assert len(library.connected_lines('station:node/a')) == 1
    assert steps[0] < 15000  # SQLite's old UNION subquery scanned all 12,000 lines.


def test_repeated_endpoint_choices_reuse_bounded_cache(tmp_path, monkeypatch):
    index = install(tmp_path,[edge('a',1,2),edge('b',2,3)])
    library = DiskRailLineLibrary(index)
    line = library.search_lines()[0]['id']
    first = library.reachable_nodes(1,line,physical=True)
    monkeypatch.setattr(library,'selected_library',lambda *a,**k: (_ for _ in ()).throw(AssertionError('graph reloaded')))
    second = library.reachable_nodes(1,line,physical=True)
    assert second == first
    second.clear()
    assert library.reachable_nodes(1,line,physical=True) == first
