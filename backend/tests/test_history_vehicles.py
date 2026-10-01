from dataclasses import replace

import pytest

from railscope.domain import InfrastructureLifecycle, Vehicle, TrainRun, StopTime
from railscope.repository import RailRepository
from railscope.services.history import (
    effective_lifecycle,
    lifecycle_state,
    validate_lifecycle,
)
from railscope.services.vehicles import vehicle_duties, validate_vehicle_assignments
from railscope.workspace import WorkspaceObjects, SQLiteWorkspace


def test_date_boundaries_and_open_ended_future():
    life = InfrastructureLifecycle(
        "LINE-1",
        construction_started="2008-01-01",
        opened="2015-06-01",
        closed="2026-10-01",
    )
    assert lifecycle_state(life, "2007-12-31") == "absent"
    assert lifecycle_state(life, "2008-01-01") == "construction"
    assert lifecycle_state(life, "2015-06-01") == "operating"
    assert lifecycle_state(life, "2026-10-01") == "disused"
    assert lifecycle_state(replace(life, closed=None), "2099-01-01") == "operating"
    with pytest.raises(ValueError):
        validate_lifecycle(replace(life, opened="2007-01-01"))


def test_inherit_unknown_override_and_cycle():
    lives = {
        "line": InfrastructureLifecycle("line", opened="2015-01-01"),
        "station": InfrastructureLifecycle("station", parent_id="line"),
    }
    assert effective_lifecycle(lives, "station").opened == "2015-01-01"
    lives["station"] = replace(lives["station"], opened="2016-01-01")
    assert effective_lifecycle(lives, "station").opened == "2016-01-01"
    assert lifecycle_state(None, "2008-01-01") == "unknown"
    lives["line"] = replace(lives["line"], parent_id="station")
    with pytest.raises(ValueError, match="cycle"):
        effective_lifecycle(lives, "station")


def test_typed_objects_share_workspace_and_roundtrip(tmp_path):
    path = tmp_path / "workspace.sqlite"
    store = WorkspaceObjects(path)
    life = InfrastructureLifecycle(
        "INF-station", opened="2026-10-01", source_aliases=("station:node/1",)
    )
    store.put("lifecycles", life)
    store.put("vehicles", Vehicle("VEH-1", "一号车", model="CR400AF", mode="rail"))
    assert store.collection("lifecycles")[life.id] == life
    repo, _ = SQLiteWorkspace(path).load()
    assert repo.lifecycles[life.id] == life
    assert repo.vehicles["VEH-1"].model == "CR400AF"
    with pytest.raises(ValueError):
        store.put("lifecycles", replace(life, closed="2020-01-01"))
    assert store.collection("lifecycles")[life.id] == life


def test_vehicle_duties_use_absolute_dates_and_reject_overlap():
    repo = RailRepository()
    repo.vehicles["v"] = Vehicle("v", "EMU")
    repo.train_runs["a"] = TrainRun("a", "2026-09-30", "G1", "x", "y", vehicle_id="v")
    repo.train_runs["b"] = TrainRun("b", "2026-10-01", "G14", "y", "x", vehicle_id="v")
    repo.stops = [
        StopTime("a", "x", 1, 85000, 85000),
        StopTime("a", "y", 2, 87000, 87000),
        StopTime("b", "y", 1, 1200, 1200),
        StopTime("b", "x", 2, 3600, 3600),
    ]
    validate_vehicle_assignments(repo)
    assert [d["train_run_id"] for d in vehicle_duties(repo, "v")] == ["a", "b"]
    repo.stops[2] = replace(repo.stops[2], arrival_time_s=0, departure_time_s=0)
    with pytest.raises(ValueError, match="overlap"):
        validate_vehicle_assignments(repo)
    empty = RailRepository()
    empty.train_runs["bad"] = TrainRun(
        "bad", "2026-09-30", "BAD", "x", "y", traffic_type="invalid"
    )
    with pytest.raises(ValueError, match="客货类型"):
        validate_vehicle_assignments(empty)
