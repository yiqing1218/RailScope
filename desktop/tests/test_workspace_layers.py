"""Workspace layer authority, migration fidelity and typed invalidation."""
import json
import sqlite3
from contextlib import closing

from desktop.catalog_workspace import CatalogWorkspace, edit_database, read_overrides


def test_legacy_migration_preserves_tombstones_and_extension_data(tmp_path):
    path = tmp_path / 'rail_catalog.json'
    path.write_text(json.dumps({'RL-1': {'display_name': 'seed', 'folder_path': ['Old']},
                               'RL-2': {'display_name': 'deleted'}}), encoding='utf-8')
    legacy = path.with_suffix('.edits.sqlite')
    with sqlite3.connect(legacy) as db:
        db.execute('PRAGMA user_version=1')
        db.execute('CREATE TABLE overrides(entity_id TEXT PRIMARY KEY,data TEXT)')
        db.executemany('INSERT INTO overrides VALUES(?,?)', [
            ('RL-1', json.dumps({'display_name': 'local', 'folder_path': ['Province', 'City'],
                                'color': '#123456', 'extensions': {'test': [1, 2]}})), ('RL-2', None)])
    original = (path.read_bytes(), legacy.read_bytes())
    store = CatalogWorkspace(path)
    store.load()
    assert store.values == {'RL-1': {'display_name': 'local', 'folder_path': ['Province', 'City'],
                                   'color': '#123456', 'extensions': {'test': [1, 2]}}}
    with closing(sqlite3.connect(edit_database(path))) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {'directory_folders', 'directory_membership', 'presentation_overrides',
                'station_assignments', 'line_assemblies', 'assembly_members', 'revisions'} <= tables
    assert (path.read_bytes(), legacy.read_bytes()) == original
    path.write_text(json.dumps({'RL-2': {'display_name': 'stale'}}), encoding='utf-8')
    assert read_overrides(path) == store.values
    reloaded = CatalogWorkspace(path)
    reloaded.load()
    assert reloaded.values == store.values


def test_directory_edit_and_history_only_advance_directory_revision(tmp_path):
    store = CatalogWorkspace(tmp_path / 'rail_catalog.json')
    store.load()
    store.update({'RL-1': {'folder_path': ['A']}})
    assert getattr(store, 'revisions', {}).get('directory') == 1
    assert all(store.revisions[name] == 0 for name in ('geometry', 'topology', 'semantic', 'presentation'))
    store.undo()
    assert store.revisions['directory'] == 2
    store.redo()
    assert store.revisions['directory'] == 3
    restored = CatalogWorkspace(store.path)
    restored.load()
    assert restored.revisions == store.revisions


def test_workspace_splits_field_ownership_without_losing_effective_values(tmp_path):
    store = CatalogWorkspace(tmp_path / 'rail_catalog.json')
    store.load()
    value = {'display_name': '甲线', 'color': '#123456', 'folder_path': ['自定义'],
             'station_id': 'STN-1', 'station_assignment': 'manual',
             'source': 'manual', 'verification_status': 'user_verified', 'confidence': .9,
             'extensions': {'unknown': 'kept'}}
    store.update({'RL-1': value})
    assert read_overrides(store.path) == {'RL-1': value}
    with closing(sqlite3.connect(edit_database(store.path))) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert 'directory_membership' in tables
        assert db.execute('SELECT count(*) FROM directory_membership').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM station_assignments').fetchone()[0] == 1
    assert store.revisions['topology'] == 0
    assert store.revisions['assignment'] == 1
    assert store.revisions['presentation'] == 1
