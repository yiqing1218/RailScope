import json
import sqlite3
from desktop.catalog_workspace import CatalogWorkspace, routing_revision
from desktop.artifact_manifest import manifest, install_manifest, read_manifest


def test_routing_revision_ignores_directory_presentation_and_assignment(tmp_path):
    path = tmp_path / 'rail_catalog.json'
    workspace = CatalogWorkspace(path)
    workspace.load()
    original = routing_revision(path)
    workspace.update({'RL-a': {'folder_path': ['Custom'], 'display_name': 'New', 'color': '#123456'}})
    workspace.update({'object:network_edge_id:NE-a': {'station_id': 'station:RS-a', 'station_assignment': 'manual'}})
    assert routing_revision(path) == original
    workspace.update({'RL-a': {'rail_semantics': {'track_role': 'main_track'}}})
    assert routing_revision(path) != original


def test_manifest_is_complete_and_tracks_its_actual_inputs(tmp_path):
    path = tmp_path / 'cache.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)')
        record = manifest('line-index', 18, {'source': 'immutable-snapshot'}, {'topology': 2})
        install_manifest(db, record)
        db.commit()
        assert read_manifest(db) == record
    assert record['complete'] and record['created_at'] and record['input_fingerprint']
    assert record != manifest('line-index', 18, {'source': 'another-snapshot'}, {'topology': 2})
