"""Timetable references are valid even while corridor resolution is pending."""
from collections import defaultdict
from datetime import date
import math


def validate_timetables(repo):
    errors, grouped = [], defaultdict(list)
    for stop in repo.stops:
        grouped[stop.train_run_id].append(stop)
        if stop.train_run_id not in repo.train_runs:
            errors.append(f'stop {stop.train_run_id}: train run missing')
        if stop.station_id not in repo.stations:
            errors.append(f'stop {stop.train_run_id}: station missing ({stop.station_id})')
        for ident, collection in ((stop.platform_id, 'platforms'),
                (stop.station_track_id, 'station_tracks'), (stop.station_route_id, 'station_routes')):
            if ident is not None:
                item = getattr(repo, collection).get(ident)
                if item is None or item.station_id != stop.station_id:
                    errors.append(f'stop {stop.train_run_id}: invalid {collection} reference {ident}')
                elif collection == 'station_routes' and item.verification_status not in {
                        'official', 'official_confirmed', 'user_verified', 'manual_override', 'automatic_reference'}:
                    errors.append(f'stop {stop.train_run_id}: station route is unverified')
        if stop.stop_edge_id is not None or stop.stop_offset_m is not None:
            edge = repo.edges.get(stop.stop_edge_id)
            offset = stop.stop_offset_m
            if edge is None or type(offset) not in (int, float) or not math.isfinite(offset) or not 0 <= offset <= edge.length_m:
                errors.append(f'stop {stop.train_run_id}: invalid stop edge/offset')
        if stop.stop_edge_sequence is not None and (stop.stop_edge_id is None or
                type(stop.stop_edge_sequence) is not int or stop.stop_edge_sequence < 1):
            errors.append(f'stop {stop.train_run_id}: invalid stop edge sequence')
    for run in repo.train_runs.values():
        try:
            if date.fromisoformat(run.service_date).isoformat() != run.service_date:
                raise ValueError()
        except (ValueError, TypeError):
            errors.append(f'run {run.id}: invalid service date')
        if not isinstance(run.train_number, str) or not run.train_number.strip():
            errors.append(f'run {run.id}: train number missing')
        for field in ('origin_station_id', 'destination_station_id'):
            if getattr(run, field) not in repo.stations:
                errors.append(f'run {run.id}: {field} missing')
        stops = grouped[run.id]
        if any(type(stop.sequence) is not int for stop in stops):
            errors.append(f'run {run.id}: invalid stop sequence')
            continue
        stops = sorted(stops, key=lambda stop: stop.sequence)
        if [stop.sequence for stop in stops] != list(range(1, len(stops) + 1)):
            errors.append(f'run {run.id}: invalid stop sequence')
        if len(stops) < 2:
            errors.append(f'run {run.id}: timetable needs at least two stops')
        if stops:
            if stops[0].station_id != run.origin_station_id:
                errors.append(f'run {run.id}: origin disagrees with timetable')
            if stops[-1].station_id != run.destination_station_id:
                errors.append(f'run {run.id}: destination disagrees with timetable')
        previous = 0
        for stop in stops:
            times = [value for value in (stop.arrival_time_s, stop.departure_time_s) if value is not None]
            valid = bool(times) and all(type(value) is int and value >= previous for value in times)
            if not valid or times != sorted(times):
                errors.append(f'run {run.id}: stop times not monotonic')
            if valid:
                previous = times[-1]
    if errors:
        raise ValueError('\n'.join(errors))
