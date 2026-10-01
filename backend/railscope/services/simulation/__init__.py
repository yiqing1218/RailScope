from typing import Protocol
from ...repository import RailRepository
from ..timetable import effective_run, route_distances
from .geometry import route_coordinate


class MotionModel(Protocol):
    def position_at_time(self, repo: RailRepository, scenario_id: str, train_id: str, time_s: int) -> dict: ...


class TimetableLinearInterpolationModel:
    """Future V2/V3 interface. The state is a route distance, never a frame-mutated coordinate."""
    def position_at_time(self, repo: RailRepository, scenario_id: str, train_id: str, time_s: int, *, allow_reference=False) -> dict:
        effective = effective_run(repo, scenario_id, train_id)
        run = effective.train_run
        if effective.cancelled:
            return {"state": "cancelled", "train_run_id": train_id}
        if (run.service_id or run.corridor_id) and not allow_reference and (
            not effective.corridor_id
            or run.verification_status
            not in {"official", "user_verified", "manual_override"}
        ):
            return {"state": "unresolved", "train_run_id": train_id}
        try:
            corridor = repo.corridors[effective.corridor_id]
            if any(
                repo.edges[ref.edge_id].construction_status != "operating"
                for ref in corridor.edge_refs
            ):
                return {"state": "unresolved", "train_run_id": train_id}
            stops = route_distances(repo, effective)
        except (KeyError, ValueError):
            return {"state": "unresolved", "train_run_id": train_id}
        if effective.cancelled or not stops:
            return {"state": "cancelled", "train_run_id": train_id}
        first, last = stops[0], stops[-1]
        first_departure = first.departure_time_s if first.departure_time_s is not None else first.arrival_time_s
        last_arrival = last.arrival_time_s if last.arrival_time_s is not None else last.departure_time_s
        first_present = first.arrival_time_s if first.arrival_time_s is not None else first_departure
        last_present = last.departure_time_s if last.departure_time_s is not None else last_arrival
        if time_s < first_present:
            return {"state": "not_started", "train_run_id": train_id}
        if time_s > last_present or (last_present > last_arrival and time_s == last_present):
            return {"state": "finished", "train_run_id": train_id}
        for stop in stops:
            if stop.arrival_time_s is not None and stop.departure_time_s is not None and stop.arrival_time_s <= time_s < stop.departure_time_s:
                return self._position(repo, effective, "dwelling", stop.scheduled_distance_m)
        for left, right in zip(stops, stops[1:]):
            start = left.departure_time_s if left.departure_time_s is not None else left.arrival_time_s
            end = right.arrival_time_s if right.arrival_time_s is not None else right.departure_time_s
            if start is not None and end is not None and start <= time_s <= end:
                ratio = 0 if end == start else (time_s - start) / (end - start)
                distance = (left.scheduled_distance_m or 0) + ratio * ((right.scheduled_distance_m or 0) - (left.scheduled_distance_m or 0))
                return self._position(repo, effective, "running", distance)
        return {"state": "unknown", "train_run_id": train_id}

    @staticmethod
    def _position(repo, effective, state, distance):
        result = {"state": state, "train_run_id": effective.train_run.id, "route_distance_m": distance}
        path = repo.corridors.get(effective.corridor_id)
        if path is not None:
            from ..station_routing import effective_path
            result['coordinate'] = route_coordinate(repo, effective_path(repo, effective.corridor_id, effective.stops), distance)
            result['route_status'] = 'automatic_reference' if any(repo.station_routes[s.station_route_id].verification_status == 'automatic_reference'
                for s in effective.stops if s.station_route_id) else effective.train_run.verification_status
        return result


class TrainStateProvider(Protocol):
    def state(self, train_id: str, time_s: int) -> dict: ...


class ScheduledTrainStateProvider:
    pass
