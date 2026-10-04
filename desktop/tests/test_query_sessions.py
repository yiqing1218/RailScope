from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import sqlite3
from threading import Barrier

import pytest

from desktop.tests.test_line_membership import edge, install
from desktop.rail_line_store import DiskRailLineLibrary


def test_repeated_line_queries_install_membership_once(tmp_path, monkeypatch):
    from desktop.rail_line_workspace import LineWorkspace
    library = DiskRailLineLibrary(install(tmp_path, [edge('a', 1, 2), edge('b', 2, 3, highspeed='yes')]))
    calls = []
    original = LineWorkspace.install
    monkeypatch.setattr(LineWorkspace, 'install', lambda self, db: (calls.append(db), original(self, db))[1])
    for _ in range(4):
        assert library.search_lines('仙宁')
    assert len(calls) == 1


def test_station_directory_does_not_eagerly_decode_country(tmp_path, monkeypatch):
    import desktop.rail_line_store as store
    index = install(tmp_path, [edge('a', 1, 2)])
    with sqlite3.connect(index) as db:
        db.execute('CREATE TABLE IF NOT EXISTS station_directory(source_id TEXT PRIMARY KEY,name TEXT,data TEXT)')
        db.execute("INSERT INTO station_directory VALUES('node/1','甲站','{\"properties\":{\"name\":\"甲\"}}')")
    monkeypatch.setattr(store, 'load_directory', lambda *args: pytest.fail('decoded full country'))
    library = store.DiskRailLineLibrary(index)
    assert 'node/1' in library.station_directory
    assert library.endpoint_label('station:node/1') == '甲站'


def test_query_scratch_tables_do_not_leak_between_resolutions(tmp_path):
    library = DiskRailLineLibrary(install(tmp_path, [edge('a', 1, 2)]))
    ids = list(library.lines)
    for _ in range(3):
        graph = library.selected_library(ids, ['a'])
        assert set(graph.edges) == {'a'}
    with library.connect() as db:
        assert not db.execute("SELECT name FROM sqlite_temp_master WHERE name='selected_edges'").fetchall()


def test_read_sessions_are_thread_confined_and_nested(tmp_path):
    from desktop.sqlite_read_sessions import ReadSessions
    index = install(tmp_path, [edge('a', 1, 2)])
    pool = ReadSessions(index)
    barrier = Barrier(2)

    def worker():
        with pool.connect() as outer:
            barrier.wait(timeout=5)
            with pool.connect() as inner:
                assert outer is not inner
                assert inner.execute('SELECT count(*) FROM lines').fetchone()[0]
            with pool.connect() as inner_again:
                assert inner_again is inner
        with pool.connect() as again:
            assert again is outer
        return outer

    with ThreadPoolExecutor(max_workers=2) as executor:
        a, b = list(executor.map(lambda _: worker(), range(2)))
    assert a is not b


def test_read_session_refreshes_after_replacement_and_rejects_writes(tmp_path):
    from desktop.sqlite_read_sessions import ReadSessions
    index = install(tmp_path, [edge('a', 1, 2)])
    pool = ReadSessions(index)
    with pool.connect() as db:
        original = db
        with pytest.raises(sqlite3.OperationalError):
            db.execute("UPDATE lines SET source_name='不许写入'")
    replacement = tmp_path / 'replacement.sqlite'
    with closing(sqlite3.connect(index)) as src, closing(sqlite3.connect(replacement)) as dst:
        src.backup(dst)
        with dst:
            dst.execute("UPDATE lines SET source_name='新的快照'")
    pool.close_current_thread()
    replacement.replace(index)
    with pool.connect() as db:
        assert db is not original
        assert db.execute('SELECT source_name FROM lines').fetchone()[0] == '新的快照'


def test_search_indexes_rename_undo_and_group_names_without_country_scan(tmp_path, monkeypatch):
    library = DiskRailLineLibrary(install(tmp_path, [edge('a', 1, 2)]))
    key = next(iter(library.lines))
    assert not library.search_lines('自定义')
    library.metadata[key] = {'display_name': '自定义线路'}
    assert library.search_lines('自定义')[0]['id'] == key
    # Warm searches must not iterate all metadata again, including station and
    # object display overrides that are not line identities.
    monkeypatch.setattr(library.metadata, 'items', lambda: pytest.fail('full metadata scan'))
    assert library.search_lines('自定义')[0]['id'] == key
    library.metadata.pop(key)
    assert not library.search_lines('自定义')


def test_read_sessions_cleanup_after_exception_and_observe_writes(tmp_path):
    from desktop.sqlite_read_sessions import ReadSessions
    index = install(tmp_path, [edge('a', 1, 2)])
    pool = ReadSessions(index, scratch_tables=('selected_nodes',))
    with pytest.raises(ValueError), pool.connect() as db:
        db.execute('CREATE TEMP TABLE selected_nodes(id)')
        raise ValueError('cancelled')
    with closing(sqlite3.connect(index)) as writer, writer:
        writer.execute("UPDATE lines SET source_name='新名称'")
    with pool.connect() as db:
        assert not db.execute("SELECT name FROM sqlite_temp_master WHERE name='selected_nodes'").fetchall()
        assert db.execute('SELECT source_name FROM lines').fetchone()[0] == '新名称'
    pool.close_current_thread()


def test_catalog_readers_are_thread_confined_and_invalidate_cached_records(tmp_path):
    import json
    from desktop.rail_catalog_index import RailCatalogIndex
    index = tmp_path / 'catalog.sqlite'
    with closing(sqlite3.connect(index)) as db, db:
        db.execute('CREATE TABLE catalog(id TEXT PRIMARY KEY,data TEXT)')
        db.execute('INSERT INTO catalog VALUES(?,?)', ('RL-1', json.dumps({'name': '旧名'})))
    catalog = RailCatalogIndex(index)
    assert catalog['RL-1']['name'] == '旧名'
    barrier = Barrier(2)
    def worker():
        with catalog._connect() as db:
            barrier.wait(timeout=5)
            value = catalog['RL-1']
            connection = db
        catalog.close()
        return value, connection
    with ThreadPoolExecutor(max_workers=2) as executor:
        a, b = list(executor.map(lambda _: worker(), range(2)))
    assert a[0] == b[0] and a[1] is not b[1]
    with closing(sqlite3.connect(index)) as db, db:
        db.execute('UPDATE catalog SET data=?', (json.dumps({'name': '新名'}),))
    assert catalog['RL-1']['name'] == '新名'
    catalog.close()


def test_line_cache_is_thread_confined_and_save_invalidates_workers(tmp_path):
    library = DiskRailLineLibrary(install(tmp_path, [edge('a', 1, 2)]))
    key = next(iter(library.lines))
    library.line_semantics(key)
    main_cache = library._semantic_cache
    barrier = Barrier(2)
    def worker():
        old = library.line_semantics(key)['track_role']
        cache = library._semantic_cache
        barrier.wait(timeout=5)
        barrier.wait(timeout=5)
        new = library.line_semantics(key)['track_role']
        assert library._semantic_cache is not cache
        return old, new, cache
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(worker)
        barrier.wait(timeout=5)
        library.metadata[key] = {'rail_semantics': {'track_role': 'maintenance_track'}}
        barrier.wait(timeout=5)
        old, new, worker_cache = pending.result(timeout=5)
    assert old != new and new == 'maintenance_track'
    assert worker_cache is not main_cache
    assert library.line_semantics(key)['track_role'] == 'maintenance_track'


def test_signal_box_lookup_uses_sparse_station_keys_after_save_and_undo(monkeypatch):
    from desktop.rail_query_index import QueryOverrides
    from desktop.catalog_metadata import custom_signal_box_records
    overrides = QueryOverrides({'RL-1': {'display_name': '普通线路'}})
    monkeypatch.setattr(overrides, 'items', lambda: pytest.fail('scanned every railway override'))
    assert custom_signal_box_records(overrides, []) == []
    overrides['station:signalbox/manual'] = {'display_name': '人工线路所',
        'coordinates': [121, 31], 'member_switch_ids': ['SW-a', 'SW-b']}
    assert custom_signal_box_records(overrides, [])[0]['name'] == '人工线路所'
    overrides.pop('station:signalbox/manual')
    assert custom_signal_box_records(overrides, []) == []
