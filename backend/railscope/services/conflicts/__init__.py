from __future__ import annotations
from ...domain import Conflict, TrackOccupancy
from ...repository import RailRepository


def _overlap(a: TrackOccupancy, b: TrackOccupancy) -> tuple[int, int] | None:
    start, end = max(a.start_time_s, b.start_time_s), min(a.end_time_s, b.end_time_s)
    return (start, end) if start < end else None


def detect_conflicts(repo: RailRepository, scenario_id: str) -> list[Conflict]:
    occupancies = [o for o in repo.occupancies if o.scenario_id == scenario_id]
    conflicts: list[Conflict] = []
    for index, a in enumerate(occupancies):
        for b in occupancies[index + 1:]:
            if a.train_run_id == b.train_run_id or a.resource_type != b.resource_type or a.resource_id != b.resource_id:
                continue
            shared = _overlap(a, b)
            if shared:
                if a.resource_type == "station_track":
                    kind, severity = "platform_overlap", "high"
                elif a.direction != b.direction:
                    kind, severity = "opposite_direction_conflict", "critical"
                else:
                    kind, severity = "same_block_overlap", "high"
                conflicts.append(Conflict(f"conf-{scenario_id}-{kind}-{a.id}-{b.id}", scenario_id, kind, a.train_run_id, b.train_run_id,
                                          a.resource_type, a.resource_id, *shared, severity))
                continue
            if a.resource_type != "block" or a.direction != b.direction:
                continue
            rule = next((r for r in repo.headway_rules if r.block_id == a.resource_id), None)
            min_s = rule.same_direction_min_s if rule else 180
            first, second = sorted((a, b), key=lambda o: o.start_time_s)
            if second.start_time_s - first.start_time_s < min_s:
                conflicts.append(Conflict(f"conf-{scenario_id}-headway-{a.id}-{b.id}", scenario_id, "headway_violation",
                                          first.train_run_id, second.train_run_id, "block", a.resource_id,
                                          first.start_time_s, second.start_time_s, "medium"))
    repo.conflicts = [c for c in repo.conflicts if c.scenario_id != scenario_id] + conflicts
    return conflicts
