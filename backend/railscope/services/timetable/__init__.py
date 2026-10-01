from __future__ import annotations
from dataclasses import replace
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
            matches = [i for i, s in enumerate(stops) if s.station_id == station_id]
            if len(matches) != 1 or stops[matches[0]].departure_time_s is None:
                raise ValueError("hold requires a unique scheduled departure")
            index = matches[0]
            # Hold moves this departure and all later times; it never changes scheduled facts.
            stops[index] = replace(stops[index], departure_time_s=stops[index].departure_time_s + int(seconds))
            stops[index + 1:] = [shifted_stop(s, int(seconds)) for s in stops[index + 1:]]
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
    from ..station_routing import effective_path, positioned_stops
    refs = effective_path(repo, effective.corridor_id, effective.stops)
    stops = positioned_stops(repo, refs, effective.stops)
    distances = stop_distances(repo, refs, stops)
    return tuple(StopTime(**{**s.__dict__, "scheduled_distance_m": distance})
                 for s, distance in zip(stops, distances))
