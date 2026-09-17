from __future__ import annotations
from ...domain import TrackOccupancy
from ...repository import RailRepository
from ..timetable import effective_run, route_distances


def calculate_occupancies(repo: RailRepository, scenario_id: str) -> list[TrackOccupancy]:
    result: list[TrackOccupancy] = []
    edge_blocks: dict[str, list[str]] = {}
    for member in repo.block_edges:
        edge_blocks.setdefault(member.edge_id, []).append(member.block_id)
    for train in repo.train_runs.values():
        effective = effective_run(repo, scenario_id, train.id)
        if effective.cancelled or not effective.route_path_id:
            continue
        stops = route_distances(repo, effective)
        if len(stops) < 2:
            continue
        path = repo.routes[effective.route_path_id]
        for left, right in zip(stops, stops[1:]):
            start = left.departure_time_s if left.departure_time_s is not None else left.arrival_time_s
            end = right.arrival_time_s if right.arrival_time_s is not None else right.departure_time_s
            if start is None or end is None or end < start:
                continue
            a = left.scheduled_distance_m or 0.0
            b = right.scheduled_distance_m or a
            span = max(1.0, b - a)
            for ref in path.edge_refs:
                overlap_start = max(a, ref.start_distance_m)
                overlap_end = min(b, ref.end_distance_m)
                if overlap_end <= overlap_start:
                    continue
                enter = round(start + (overlap_start - a) / span * (end - start))
                leave = round(start + (overlap_end - a) / span * (end - start))
                for block_id in edge_blocks.get(ref.edge_id, []):
                    result.append(TrackOccupancy(f"occ-{scenario_id}-{train.id}-{block_id}-{enter}", train.id, "block", block_id,
                                                 enter, max(enter, leave), "forward" if ref.forward else "reverse", scenario_id))
        for stop in stops:
            if stop.station_track_id and stop.arrival_time_s is not None and stop.departure_time_s is not None:
                result.append(TrackOccupancy(f"occ-{scenario_id}-{train.id}-{stop.station_track_id}-{stop.arrival_time_s}", train.id,
                                             "station_track", stop.station_track_id, stop.arrival_time_s, stop.departure_time_s,
                                             "both", scenario_id))
    repo.occupancies = [o for o in repo.occupancies if o.scenario_id != scenario_id] + result
    return result
