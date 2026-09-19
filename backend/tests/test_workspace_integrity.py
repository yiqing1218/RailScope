from dataclasses import replace
import json
import sqlite3

import pytest

from railscope.domain import (
    Corridor, InfrastructureLine, LineMembership, NetworkEdge, NetworkNode,
    Station, StopTime, TrainRun, TrainService,
)
from railscope.integrity import path_refs, references, validate_repository
from railscope.repository import RailRepository
from railscope.workspace import EditSession, SQLiteWorkspace


def repository():
    repo = RailRepository()
    repo.lines["IL-1"] = InfrastructureLine("IL-1", "原线路名", "rail", construction_status="operating")
    for key, lon in (("a", 120.0), ("b", 120.1), ("c", 120.2)):
        repo.nodes[key] = NetworkNode(key, lon, 30.0)
        repo.stations[key] = Station(key, key.upper(), lon, 30.0, key)
    repo.edges["e1"] = NetworkEdge("e1", "a", "b", ((120, 30), (120.1, 30)), 100,
                                           infrastructure_line_id="IL-1")
    repo.edges["e2"] = NetworkEdge("e2", "b", "c", ((120.1, 30), (120.2, 30)), 100,
                                           infrastructure_line_id="IL-1")
    repo.memberships.extend((LineMembership("e1", "IL-1"), LineMembership("e2", "IL-1")))
    refs = path_refs(repo, [("e1", True), ("e2", True)])
    repo.corridors["COR-1"] = Corridor("COR-1", "完整通道", refs, "a", "c")
    repo.train_services["SVC-1"] = TrainService("SVC-1", "G1")
    repo.train_runs["RUN-1"] = TrainRun("RUN-1", "2026-09-18", "G1", "a", "c",
                                                service_id="SVC-1", corridor_id="COR-1",
                                                verification_status="user_verified")
    repo.stops.extend((StopTime("RUN-1", "a", 1, 0, 10), StopTime("RUN-1", "c", 2, 100, 110)))
    validate_repository(repo)
    return repo


def test_transactional_override_undo_redo_and_reimport(tmp_path):
    store = SQLiteWorkspace(tmp_path / "workspace.sqlite")
    source = repository()
    store.seed(source)
    session = EditSession(store)
    session.edit_line("IL-1", name="京沪高速铁路", railway_type="high_speed")
    assert session.repo.lines["IL-1"].name == "京沪高速铁路"
    session.undo()
    assert session.repo.lines["IL-1"].name == "原线路名"
    session.redo()
    session.save()
    refreshed = repository()
    refreshed.lines["IL-1"] = replace(refreshed.lines["IL-1"], name="OSM 新名称")
    store.seed(refreshed)
    loaded, _ = store.load()
    assert loaded.lines["IL-1"].name == "京沪高速铁路", "人工覆盖须在重新导入后继续生效"


def test_legacy_route_path_workspace_migrates_to_corridor(tmp_path):
    store = SQLiteWorkspace(tmp_path / "workspace.sqlite")
    store.seed(repository())
    with sqlite3.connect(store.path) as db, db:
        raw = db.execute(
            "SELECT data FROM workspace_source WHERE kind='corridors' AND id='COR-1'"
        ).fetchone()[0]
        db.execute(
            "INSERT INTO workspace_source VALUES('routes','COR-1',?)", (raw,)
        )
        db.execute(
            "DELETE FROM workspace_source WHERE kind='corridors' AND id='COR-1'"
        )
        run = json.loads(
            db.execute(
                "SELECT data FROM workspace_source WHERE kind='train_runs' AND id='RUN-1'"
            ).fetchone()[0]
        )
        run["route_path_id"] = run.pop("corridor_id")
        db.execute(
            "UPDATE workspace_source SET data=? WHERE kind='train_runs' AND id='RUN-1'",
            (json.dumps(run),),
        )
    loaded, _ = store.load()
    assert loaded.train_runs["RUN-1"].corridor_id == "COR-1"
    assert loaded.corridors["COR-1"].verification_status == "unverified"


def test_failed_candidate_and_stale_save_leave_workspace_unchanged(tmp_path):
    store = SQLiteWorkspace(tmp_path / "workspace.sqlite")
    store.seed(repository())
    first, second = EditSession(store), EditSession(store)
    before = first.repo
    with pytest.raises(ValueError, match="引用"):
        first.delete_edge("e1")
    assert first.repo == before and not first.dirty
    first.edit_line("IL-1", name="名称 A")
    first.save()
    second.edit_line("IL-1", name="名称 B")
    with pytest.raises(ValueError, match="其他窗口"):
        second.save()
    loaded, _ = store.load()
    assert loaded.lines["IL-1"].name == "名称 A"


def test_reference_inspection_is_transitive_to_train_run():
    repo = repository()
    impact = references(repo, "edge", "e1")
    assert impact["corridors"] == ["COR-1"]
    assert impact["train_runs"] == ["RUN-1"]


def test_nonoperating_and_wrong_direction_corridors_are_rejected():
    repo = repository()
    repo.edges["e2"] = replace(repo.edges["e2"], construction_status="planned")
    with pytest.raises(ValueError, match="非运营"):
        path_refs(repo, [("e1", True), ("e2", True)])
    repo.edges["e2"] = replace(repo.edges["e2"], construction_status="operating", direction="forward")
    with pytest.raises(ValueError, match="方向"):
        path_refs(repo, [("e2", False)])


def test_line_split_and_station_merge_keep_references_valid(tmp_path):
    store = SQLiteWorkspace(tmp_path / "workspace.sqlite")
    store.seed(repository())
    session = EditSession(store)
    new_line = session.split_line("IL-1", "b", "测试支线", ["e2"])
    assert {m.edge_id for m in session.repo.memberships if m.line_id == new_line} == {"e2"}
    session.merge_stations("b", "c")
    assert "c" not in session.repo.stations
    assert session.repo.train_runs["RUN-1"].destination_station_id == "b"
    assert [stop.station_id for stop in session.repo.stops_for("RUN-1")] == ["a", "b"]
    validate_repository(session.repo)
