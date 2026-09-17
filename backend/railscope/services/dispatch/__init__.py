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
    repo.events.append(event)
    recalculate(repo, event.scenario_id)
    return event


def recalculate(repo: RailRepository, scenario_id: str) -> dict[str, int]:
    occupancy = calculate_occupancies(repo, scenario_id)
    conflicts = detect_conflicts(repo, scenario_id)
    return {"occupancies": len(occupancy), "conflicts": len(conflicts)}


def reset(repo: RailRepository, scenario_id: str) -> dict[str, int]:
    repo.reset_scenario(scenario_id)
    return recalculate(repo, scenario_id)
