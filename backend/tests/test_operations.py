from railscope.demo import load_demo
from railscope.domain import DispatchEvent
from railscope.services.dispatch import add_event, reset
from railscope.services.timetable import effective_run
from railscope.services.simulation import TimetableLinearInterpolationModel

SCENARIO = "base-2026-09-15"

def test_same_block_overlap_and_opposite_direction_are_detected():
    repo = load_demo()
    types = {c.conflict_type for c in repo.conflicts}
    assert "same_block_overlap" in types
    assert "opposite_direction_conflict" in types

def test_delay_recalculates_occupancy_without_mutating_scheduled_stops():
    repo = load_demo(); before = repo.stops_for("run-102")
    add_event(repo, DispatchEvent("e1", SCENARIO, "run-102", "delay_train", new_value=600))
    after = effective_run(repo, SCENARIO, "run-102").stops
    assert after[0].departure_time_s == before[0].departure_time_s + 600
    assert repo.stops_for("run-102") == before

def test_hold_cancel_assign_track_and_reset_are_scenario_scoped():
    repo = load_demo()
    add_event(repo, DispatchEvent("track-a", SCENARIO, "run-101", "assign_track", new_value=("station-b", "track-b-1")))
    add_event(repo, DispatchEvent("track-b", SCENARIO, "run-102", "assign_track", new_value=("station-b", "track-b-1")))
    # The regular demo does not overlap at B; move the following run five seconds earlier
    # through an effective delay event to exercise the independent track detector.
    add_event(repo, DispatchEvent("track-delay", SCENARIO, "run-102", "delay_train", new_value=-65))
    assert any(c.conflict_type == "platform_overlap" for c in repo.conflicts)
    add_event(repo, DispatchEvent("hold", SCENARIO, "run-102", "hold_train", new_value=("station-a", 300)))
    assert repo.events
    add_event(repo, DispatchEvent("cancel", SCENARIO, "run-102", "cancel_train", new_value=True))
    assert not any(o.train_run_id == "run-102" for o in repo.occupancies)
    reset(repo, SCENARIO)
    assert not repo.events and any(c.conflict_type == "same_block_overlap" for c in repo.conflicts)

def test_position_is_a_deterministic_function_of_time_and_route():
    repo = load_demo(); model = TimetableLinearInterpolationModel()
    a = model.position_at_time(repo, SCENARIO, "run-101", 7*3600+5*60)
    b = model.position_at_time(repo, SCENARIO, "run-101", 7*3600+5*60)
    assert a == b and a["state"] == "running"
