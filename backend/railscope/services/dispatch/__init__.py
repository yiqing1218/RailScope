from __future__ import annotations
from typing import Protocol
from ...domain import DispatchEvent, DispatchScenario
from ...repository import RailRepository
from ..occupancy import calculate_occupancies
from ..conflicts import detect_conflicts


class DispatchSolver(Protocol):
    def solve(self, scenario: DispatchScenario) -> None: ...


class ManualDispatchSolver:
    def solve(self, scenario: DispatchScenario) -> None:
        return None


def add_event(repo: RailRepository, event: DispatchEvent) -> DispatchEvent:
    if event.scenario_id not in repo.scenarios:
        raise ValueError("unknown scenario")
    if event.train_run_id not in repo.train_runs:
        raise ValueError("unknown train run")
    if any(existing.id == event.id for existing in repo.events):
        raise ValueError("duplicate dispatch event id")
    run = repo.train_runs[event.train_run_id]
    scenario = repo.scenarios[event.scenario_id]
    if run.service_date != scenario.service_date:
        raise ValueError("train run and scenario service dates differ")
    stops = repo.stops_for(run.id)
    station_ids = {stop.station_id for stop in stops}
    if event.event_type == "delay_train":
        if type(event.new_value) is not int:
            raise ValueError("delay requires integer seconds")
    elif event.event_type == "hold_train":
        if (
            not isinstance(event.new_value, (tuple, list))
            or len(event.new_value) != 2
            or event.new_value[0] not in station_ids
            or type(event.new_value[1]) is not int
            or event.new_value[1] < 0
        ):
            raise ValueError("hold requires a scheduled station and non-negative seconds")
    elif event.event_type == "cancel_train":
        if event.new_value is not True:
            raise ValueError("cancel requires true")
    elif event.event_type == "change_route":
        corridor = repo.corridors.get(str(event.new_value))
        if corridor is None:
            raise ValueError("change route requires an existing complete corridor")
        from ..timetable.canonical import station_distances

        station_distances(repo, corridor.edge_refs, (stop.station_id for stop in stops))
    elif event.event_type == "assign_track":
        if not isinstance(event.new_value, (tuple, list)) or len(event.new_value) != 2:
            raise ValueError("assign track requires station and track ids")
        station_id, track_id = event.new_value
        track = repo.station_tracks.get(track_id)
        if station_id not in station_ids or track is None or track.station_id != station_id:
            raise ValueError("station track does not belong to the scheduled station")
    else:
        raise ValueError("unsupported dispatch event type")
    previous_occupancies = repo.occupancies
    previous_conflicts = repo.conflicts
    repo.events.append(event)
    try:
        recalculate(repo, event.scenario_id)
    except Exception:
        repo.events.pop()
        repo.occupancies = previous_occupancies
        repo.conflicts = previous_conflicts
        raise
    return event


def recalculate(repo: RailRepository, scenario_id: str) -> dict[str, int]:
    occupancy = calculate_occupancies(repo, scenario_id)
    conflicts = detect_conflicts(repo, scenario_id)
    return {"occupancies": len(occupancy), "conflicts": len(conflicts)}


def reset(repo: RailRepository, scenario_id: str) -> dict[str, int]:
    repo.reset_scenario(scenario_id)
    return recalculate(repo, scenario_id)
