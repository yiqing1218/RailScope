import json
import sqlite3
from dataclasses import replace

from railscope.demo import load_demo
from railscope.workspace import EditSession, SQLiteWorkspace


def test_workspace_persists_inputs_and_rebuilds_legacy_derived_cache(tmp_path):
    store = SQLiteWorkspace(tmp_path / 'workspace.sqlite')
    repo = load_demo()
    store.seed(repo)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM workspace_source WHERE kind IN ('occupancies','conflicts')").fetchone()[0] == 0
        # Simulate an older workspace's invalid cached projection.
        db.execute("INSERT INTO workspace_source VALUES('occupancies','@list',?)", (json.dumps([{
            'id':'stale', 'train_run_id':'deleted', 'resource_type':'block', 'resource_id':'deleted',
            'start_time_s':0, 'end_time_s':100, 'direction':'forward', 'scenario_id':'missing'}]),))
    loaded, _ = store.load()
    assert loaded.occupancies == repo.occupancies
    assert loaded.conflicts == repo.conflicts


def test_edit_and_undo_keep_schedule_and_occupancy_in_same_transaction(tmp_path):
    store = SQLiteWorkspace(tmp_path / 'workspace.sqlite')
    store.seed(load_demo())
    session = EditSession(store)
    before = list(session.repo.occupancies)
    def delay(repo):
        repo.stops = [replace(stop, arrival_time_s=None if stop.arrival_time_s is None else stop.arrival_time_s+600,
                             departure_time_s=None if stop.departure_time_s is None else stop.departure_time_s+600)
                      if stop.train_run_id == 'run-101' else stop for stop in repo.stops]
    session.change(delay)
    assert session.repo.occupancies != before
    session.undo()
    assert session.repo.occupancies == before
    session.redo()
    assert session.repo.occupancies != before
