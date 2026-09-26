from __future__ import annotations
from ...domain import EffectiveRun, StopTime, shifted_stop
from ...repository import RailRepository


def effective_run(repo: RailRepository, scenario_id: str, train_id: str) -> EffectiveRun:
    run = repo.train_runs[train_id]
    stops = list(repo.stops_for(train_id))
    corridor_id = run.corridor_id
    cancelled = False
    offset = 0
    for event in repo.events_for(scenario_id, train_id):
        if event.event_type == "cancel_train":
            cancelled = True
        elif event.event_type == "delay_train":
            offset += int(event.new_value)
        elif event.event_type == "hold_train":
            station_id, seconds = event.new_value
            index = next((i for i, s in enumerate(stops) if s.station_id == station_id), None)
            if index is None:
                raise ValueError("hold station is not in train timetable")
            # Hold moves this departure and all later times; it never changes scheduled facts.
            stops[index:] = [shifted_stop(s, int(seconds)) for s in stops[index:]]
        elif event.event_type == "change_route":
            corridor_id = str(event.new_value)
        elif event.event_type == "assign_track":
            station_id, track_id = event.new_value
            stops = [StopTime(**{**s.__dict__, "station_track_id": track_id}) if s.station_id == station_id else s for s in stops]
    if offset:
        stops = [shifted_stop(s, offset) for s in stops]
    return EffectiveRun(run, tuple(stops), corridor_id, cancelled)


def route_distances(repo: RailRepository, effective: EffectiveRun) -> tuple[StopTime, ...]:
    from .canonical import stop_distances
    if not effective.corridor_id:
        return effective.stops
    path = repo.corridors[effective.corridor_id]
    distances = stop_distances(repo, path.edge_refs, effective.stops)
    return tuple(StopTime(**{**s.__dict__, "scheduled_distance_m": distance})
                 for s, distance in zip(effective.stops, distances))
