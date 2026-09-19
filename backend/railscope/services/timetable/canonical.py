"""Source adapters and conservative timetable-to-corridor matching.

These staging records carry no geometry. Import is atomic and never guesses a
station or chooses a shortest path when more than one corridor fits.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from hashlib import sha256
from math import isfinite
from typing import Iterable, Mapping

from ...domain import StopTime, TrainRun, TrainService
from ...repository import RailRepository
from ...integrity import ordered_path_nodes


@dataclass(frozen=True)
class CanonicalStop:
    station_code: str
    arrival_s: int | None
    departure_s: int | None


@dataclass(frozen=True)
class CanonicalTimetable:
    service_date: str
    train_code: str
    internal_train_no: str
    source_id: str
    source_version: str
    stops: tuple[CanonicalStop, ...]


def _id(prefix: str, *parts: str) -> str:
    # Length prefixes avoid collisions caused by separators in upstream IDs.
    value = ''.join(f'{len(part)}:{part}' for part in parts)
    return prefix + sha256(value.encode('utf-8')).hexdigest()[:24]


def parse_clock(value: object) -> int | None:
    if value in (None, '', '--', '----', '--:--', '始发站', '终到站'):
        return None
    parts = str(value).split(':')
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
        raise ValueError(f'invalid timetable clock: {value!r}')
    h, m, s = int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) == 3 else 0
    if h >= 168 or m >= 60 or s >= 60:
        raise ValueError(f'invalid timetable clock: {value!r}')
    return h * 3600 + m * 60 + s


def _normalize(rows: Iterable[tuple[str, object, object]], *, rollover: bool) -> tuple[CanonicalStop, ...]:
    stops, previous, day = [], None, 0
    for station, arrival, departure in rows:
        if not isinstance(station, str) or not station.strip():
            raise ValueError('station code is required')
        times = []
        for value in (arrival, departure):
            seconds = parse_clock(value)
            if seconds is not None:
                absolute = seconds + (day * 86400 if seconds < 86400 else 0)
                if previous is not None and absolute < previous:
                    # 12306 uses wall clocks; only a clear evening-to-morning
                    # transition is inferred. Other reversals need explicit days.
                    if rollover and seconds < 86400 and previous % 86400 >= 18 * 3600 and seconds <= 6 * 3600:
                        day = previous // 86400 + 1
                        absolute = seconds + day * 86400
                    else:
                        raise ValueError('non-monotonic timetable; use explicit hours beyond 24 for next day')
                previous = absolute
                seconds = absolute
            times.append(seconds)
        if times == [None, None]:
            raise ValueError(f'{station}: arrival or departure is required')
        stops.append(CanonicalStop(station.strip(), *times))
    if len(stops) < 2:
        raise ValueError('a timetable needs at least two stops')
    return tuple(stops)


def from_gtfs(trip: Mapping, stop_times: Iterable[Mapping], *, service_date: str,
              source_id: str, source_version: str) -> CanonicalTimetable:
    """Convert one selected active GTFS trip; caller resolves calendar exceptions."""
    rows = [row for row in stop_times if str(row['trip_id']) == str(trip['trip_id'])]
    rows.sort(key=lambda row: int(row['stop_sequence']))
    if len({int(row['stop_sequence']) for row in rows}) != len(rows):
        raise ValueError('duplicate GTFS stop_sequence')
    result = CanonicalTimetable(service_date, str(trip.get('trip_short_name') or trip['trip_id']),
        str(trip['trip_id']), source_id, source_version,
        _normalize(((str(row['stop_id']), row.get('arrival_time'), row.get('departure_time')) for row in rows), rollover=False))
    _validate(result)
    return result


def from_12306(rows: Iterable[Mapping], *, service_date: str, train_code: str,
               internal_train_no: str, source_id: str, source_version: str) -> CanonicalTimetable:
    """Convert a supplied 12306 stop table, without network access or credentials.

    station_telecode is preferred; station_name requires an explicit alias mapping
    at import. The adapter does not silently equate same-named stations.
    """
    rows = sorted(rows, key=lambda row: int(row['station_no']))
    if len({int(row['station_no']) for row in rows}) != len(rows):
        raise ValueError('duplicate 12306 station_no')
    result = CanonicalTimetable(service_date, train_code, internal_train_no, source_id, source_version,
        _normalize(((row.get('station_telecode') or row.get('station_name'), row.get('arrive_time'),
                     row.get('start_time')) for row in rows), rollover=True))
    _validate(result)
    return result


def _validate(value: CanonicalTimetable) -> None:
    if date.fromisoformat(value.service_date).isoformat() != value.service_date:
        raise ValueError('service_date must be YYYY-MM-DD')
    if any(not isinstance(v, str) or not v.strip() for v in
           (value.train_code, value.internal_train_no, value.source_id, value.source_version)):
        raise ValueError('train code, internal number and source provenance are required')
    if len(value.stops) < 2:
        raise ValueError('a timetable needs at least two stops')
    previous = -1
    for stop in value.stops:
        if not stop.station_code or (stop.arrival_s is None and stop.departure_s is None):
            raise ValueError('each stop needs a station and time')
        for seconds in (stop.arrival_s, stop.departure_s):
            if seconds is not None:
                if type(seconds) is not int or not previous <= seconds < 7 * 86400:
                    raise ValueError('invalid or non-monotonic canonical time')
                previous = seconds


def import_timetables(repo: RailRepository, values: Iterable[CanonicalTimetable],
                      station_mapping: Mapping[tuple[str, str], str] | None = None) -> tuple[TrainRun, ...]:
    """Validate the entire batch before writing; duplicate instances are errors."""
    pending, stops, services, ids = [], [], {}, set(repo.train_runs)
    mapping = station_mapping or {}
    for value in values:
        _validate(value)
        resolved = []
        for stop in value.stops:
            target = mapping.get((value.source_id, stop.station_code))
            candidates = [s.id for s in repo.stations.values() if s.code == stop.station_code] if target is None else [target]
            if len(candidates) != 1 or candidates[0] not in repo.stations:
                raise ValueError(f'unresolved station {value.source_id}:{stop.station_code}')
            resolved.append(candidates[0])
        service_id = _id('SVC-', value.source_id, value.train_code, value.internal_train_no)
        run_id = _id('RUN-', value.service_date, value.source_id, value.train_code, value.internal_train_no)
        if run_id in ids:
            raise ValueError(f'duplicate train instance: {value.train_code}@{value.service_date}')
        ids.add(run_id)
        services[service_id] = TrainService(service_id, value.train_code, value.internal_train_no,
                                           value.source_id, value.source_version)
        run = TrainRun(run_id, value.service_date, value.train_code, resolved[0], resolved[-1],
            service_id=service_id, internal_train_no=value.internal_train_no,
            source_id=value.source_id, source_version=value.source_version, verification_status='unresolved')
        pending.append(run)
        stops.extend(StopTime(run_id, station_id, i, stop.arrival_s, stop.departure_s)
                     for i, (station_id, stop) in enumerate(zip(resolved, value.stops), 1))
    repo.train_services.update(services)
    repo.train_runs.update((run.id, run) for run in pending)
    repo.stops.extend(stops)
    return tuple(pending)


def station_distances(repo: RailRepository, edge_refs, station_ids: Iterable[str]) -> tuple[float, ...]:
    """Resolve stops against actual ordered topology; no equal-spacing fallback."""
    positions, previous_node, previous_distance = [], None, 0.0
    for i, ref in enumerate(edge_refs):
        edge = repo.edges[ref.edge_id]
        if (ref.sequence != i + 1 or type(ref.forward) is not bool
                or not all(isfinite(v) for v in (ref.start_distance_m, ref.end_distance_m, edge.length_m))
                or edge.length_m <= 0):
            raise ValueError('invalid physical path sequence or distance')
        start, end = (edge.from_node_id, edge.to_node_id) if ref.forward else (edge.to_node_id, edge.from_node_id)
        if start not in repo.nodes or end not in repo.nodes:
            raise ValueError('physical edge references missing node')
        if edge.direction not in {'both', 'forward' if ref.forward else 'reverse'}:
            raise ValueError('physical path violates edge direction')
        if previous_node is not None and previous_node != start:
            raise ValueError('disconnected physical path')
        if abs(ref.start_distance_m - previous_distance) > .01 or abs(ref.end_distance_m - ref.start_distance_m - edge.length_m) > .01:
            raise ValueError('invalid path cumulative distance')
        previous_node, previous_distance = end, ref.end_distance_m
    positions = list(ordered_path_nodes(repo, edge_refs))
    result, cursor = [], 0
    for station_id in station_ids:
        anchor = repo.stations[station_id].anchor_node_id
        found = next((i for i in range(cursor, len(positions)) if positions[i][0] == anchor), None)
        if found is None:
            raise ValueError(f'station {station_id} is absent or out of order on corridor')
        result.append(positions[found][1])
        cursor = found + 1
    return tuple(result)


def match_corridors(repo: RailRepository, train_id: str) -> dict:
    run = repo.train_runs[train_id]
    stops = repo.stops_for(train_id)
    candidates = []
    for corridor in repo.corridors.values():
        try:
            distances = station_distances(repo, corridor.edge_refs, (s.station_id for s in stops))
            if not distances or distances[0] != 0 or distances[-1] != corridor.edge_refs[-1].end_distance_m:
                continue
            if any(getattr(repo.edges[r.edge_id], 'construction_status', 'operating') != 'operating' for r in corridor.edge_refs):
                continue
            candidates.append(corridor.id)
        except (KeyError, ValueError):
            continue
    # Even a unique inferred match needs explicit verification before simulation.
    status = 'automatic_match' if len(candidates) == 1 else 'unresolved'
    repo.train_runs[train_id] = replace(run, corridor_id=candidates[0] if len(candidates) == 1 else None,
                                      verification_status=status)
    return {'status': status, 'candidates': sorted(candidates)}


def verify_corridor(repo: RailRepository, train_id: str, corridor_id: str) -> TrainRun:
    corridor = repo.corridors[corridor_id]
    distances = station_distances(repo, corridor.edge_refs, (s.station_id for s in repo.stops_for(train_id)))
    if not distances or distances[0] != 0 or distances[-1] != corridor.edge_refs[-1].end_distance_m:
        raise ValueError('train endpoints do not match complete corridor')
    if any(getattr(repo.edges[r.edge_id], 'construction_status', 'operating') != 'operating' for r in corridor.edge_refs):
        raise ValueError('non-operating infrastructure cannot be verified for formal simulation')
    run = replace(repo.train_runs[train_id], corridor_id=corridor_id, verification_status='user_verified')
    repo.train_runs[train_id] = run
    return run
