from typing import Protocol
from ...repository import RailRepository
from ..timetable import effective_run, route_distances


class MotionModel(Protocol):
    def position_at_time(self, repo: RailRepository, scenario_id: str, train_id: str, time_s: int) -> dict: ...


class TimetableLinearInterpolationModel:
    """Future V2/V3 interface. The state is a route distance, never a frame-mutated coordinate."""
    def position_at_time(self, repo: RailRepository, scenario_id: str, train_id: str, time_s: int) -> dict:
        effective = effective_run(repo, scenario_id, train_id)
        stops = route_distances(repo, effective)
        if effective.cancelled or not stops:
            return {"state": "cancelled", "train_run_id": train_id}
        first, last = stops[0], stops[-1]
        first_departure = first.departure_time_s or first.arrival_time_s or 0
        last_arrival = last.arrival_time_s or last.departure_time_s or 0
        if time_s < first_departure:
            return {"state": "not_started", "train_run_id": train_id}
        if time_s > last_arrival:
            return {"state": "finished", "train_run_id": train_id}
        for left, right in zip(stops, stops[1:]):
            if left.arrival_time_s is not None and left.departure_time_s is not None and left.arrival_time_s <= time_s < left.departure_time_s:
                return {"state": "dwelling", "train_run_id": train_id, "route_distance_m": left.scheduled_distance_m}
            start = left.departure_time_s if left.departure_time_s is not None else left.arrival_time_s
            end = right.arrival_time_s if right.arrival_time_s is not None else right.departure_time_s
            if start is not None and end is not None and start <= time_s <= end:
                ratio = 0 if end == start else (time_s - start) / (end - start)
                return {"state": "running", "train_run_id": train_id,
                        "route_distance_m": (left.scheduled_distance_m or 0) + ratio * ((right.scheduled_distance_m or 0) - (left.scheduled_distance_m or 0))}
        return {"state": "unknown", "train_run_id": train_id}


class TrainStateProvider(Protocol):
    def state(self, train_id: str, time_s: int) -> dict: ...


class ScheduledTrainStateProvider:
    pass
