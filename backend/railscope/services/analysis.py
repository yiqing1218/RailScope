"""Derived timetable/section views of the same canonical runs and physical paths."""

from collections import Counter
from html import escape
import math

from ..integrity import ordered_path_nodes
from .timetable import effective_run, route_distances
from .station_routing import effective_path


def run_projection(repo, run_id, scenario_id=""):
    effective = effective_run(repo, scenario_id, run_id)
    if effective.cancelled or not effective.corridor_id:
        return None
    refs = effective_path(repo, effective.corridor_id, effective.stops)
    return effective, refs, route_distances(repo, effective)


def distance_time(stops, distance, departure=False):
    for stop in stops:
        if math.isclose(stop.scheduled_distance_m or 0, distance, abs_tol=0.001):
            value = stop.departure_time_s if departure else stop.arrival_time_s
            return (
                value
                if value is not None
                else (stop.arrival_time_s if departure else stop.departure_time_s)
            )
    for left, right in zip(stops, stops[1:]):
        a, b = left.scheduled_distance_m, right.scheduled_distance_m
        if a is None or b is None or b <= a or not a <= distance <= b:
            continue
        start = (
            left.departure_time_s
            if left.departure_time_s is not None
            else left.arrival_time_s
        )
        end = (
            right.arrival_time_s
            if right.arrival_time_s is not None
            else right.departure_time_s
        )
        if start is not None and end is not None:
            return start + (distance - a) / (b - a) * (end - start)
    return None


def section_passages(repo, edge_refs, service_date, start_s=0, end_s=172800):
    """One row per physical section traversal across every Corridor, half-open entry window."""
    if not edge_refs or end_s <= start_s:
        raise ValueError("区间路径与时间窗口不能为空")
    target = [(r.edge_id, r.forward) for r in edge_refs]
    reverse = [(key, not forward) for key, forward in reversed(target)]
    rows, unresolved = [], []
    for run in repo.train_runs.values():
        if run.service_date != service_date:
            continue
        try:
            projection = run_projection(repo, run.id)
            if not projection:
                unresolved.append(run.id)
                continue
            _, refs, stops = projection
        except (KeyError, ValueError) as exc:
            unresolved.append(run.id)
            continue
        path = [(r.edge_id, r.forward) for r in refs]
        for index in range(len(path) - len(target) + 1):
            match = path[index : index + len(target)]
            if match not in (target, reverse):
                continue
            first, last = refs[index], refs[index + len(target) - 1]
            enter = distance_time(stops, first.start_distance_m, departure=True)
            leave = distance_time(stops, last.end_distance_m)
            if enter is None or leave is None or not start_s <= enter < end_s:
                continue
            length = last.end_distance_m - first.start_distance_m
            runtime = leave - enter
            rows.append(
                {
                    "train_run_id": run.id,
                    "train_number": run.train_number,
                    "corridor_id": run.corridor_id,
                    "direction": "forward" if match == target else "reverse",
                    "category": next(
                        (
                            prefix
                            for prefix in ("G", "D", "C", "Z", "T", "K")
                            if run.train_number.upper().startswith(prefix)
                        ),
                        "other",
                    ),
                    "traffic_type": run.traffic_type,
                    "enter_s": enter,
                    "leave_s": leave,
                    "distance_m": length,
                    "runtime_s": runtime,
                    "speed_kmh": length / runtime * 3.6 if runtime > 0 else None,
                }
            )
    return {
        "passages": sorted(rows, key=lambda r: (r["enter_s"], r["train_run_id"])),
        "unresolved_run_ids": unresolved,
    }


def section_statistics(repo, edge_refs, service_date, start_s=0, end_s=86400):
    result = section_passages(repo, edge_refs, service_date, start_s, end_s)
    rows = result["passages"]
    duration = sum(r["runtime_s"] for r in rows)
    result.update(
        count=len(rows),
        by_direction=dict(Counter(r["direction"] for r in rows)),
        by_category=dict(Counter(r["category"] for r in rows)),
        by_traffic_type=dict(Counter(r["traffic_type"] for r in rows)),
        average_runtime_s=duration / len(rows) if rows else None,
        average_speed_kmh=sum(r["distance_m"] for r in rows) / duration * 3.6
        if duration > 0
        else None,
        density_per_hour=len(rows) / ((end_s - start_s) / 3600),
        service_date=service_date,
        start_s=start_s,
        end_s=end_s,
    )
    return result


def station_timetable(repo, station_id, service_date):
    if station_id not in repo.stations:
        raise ValueError("未知车站")
    rows = []
    for run in repo.train_runs.values():
        if run.service_date != service_date:
            continue
        stops = repo.stops_for(run.id)
        selected = [s for s in stops if s.station_id == station_id]
        if selected:
            for stop in selected:
                arrival, departure = stop.arrival_time_s, stop.departure_time_s
                rows.append(
                    {
                        "train_run_id": run.id,
                        "train_number": run.train_number,
                        "station_id": station_id,
                        "arrival_s": arrival,
                        "departure_s": departure,
                        "stop_type": "停靠"
                        if arrival is not None
                        and departure is not None
                        and departure > arrival
                        else "始发"
                        if stop.sequence == 1
                        else "终到"
                        if stop.sequence == len(stops)
                        else "通过",
                        "platform_id": stop.platform_id,
                        "station_track_id": stop.station_track_id,
                        "station_route_id": stop.station_route_id,
                        "route_status": repo.station_routes[
                            stop.station_route_id
                        ].verification_status
                        if stop.station_route_id
                        else "未指定",
                        "vehicle_id": run.vehicle_id,
                    }
                )
        elif run.corridor_id:
            try:
                projection = run_projection(repo, run.id)
                if not projection:
                    continue
                _, refs, resolved = projection
                positions = [
                    distance
                    for node, distance in ordered_path_nodes(repo, refs)
                    if node == repo.stations[station_id].anchor_node_id
                ]
                for distance in positions:
                    time = distance_time(resolved, distance)
                    if time is not None:
                        rows.append(
                            {
                                "train_run_id": run.id,
                                "train_number": run.train_number,
                                "station_id": station_id,
                                "arrival_s": time,
                                "departure_s": time,
                                "stop_type": "通过",
                                "platform_id": None,
                                "station_track_id": None,
                                "station_route_id": None,
                                "route_status": "通道正线（插值）",
                                "vehicle_id": run.vehicle_id,
                            }
                        )
            except (ValueError, KeyError):
                continue
    return sorted(
        rows,
        key=lambda r: (
            (r["arrival_s"] if r["arrival_s"] is not None else r["departure_s"]) or 0,
            r["train_number"],
        ),
    )


def station_track_intervals(repo, station_id, service_date):
    """Head-position occupancy from the real path, including approach, dwell and exit.

    These schedule intervals are not interlocking clearance or train-tail detection.
    """
    tracks = {
        t.id: {r.edge_id for r in t.edge_refs}
        for t in repo.station_tracks.values()
        if t.station_id == station_id
    }
    result = []
    for run in repo.train_runs.values():
        if run.service_date != service_date:
            continue
        projection = run_projection(repo, run.id)
        if not projection:
            continue
        _, refs, stops = projection
        for track_id, edge_ids in tracks.items():
            intervals = []
            for ref in refs:
                if ref.edge_id not in edge_ids:
                    continue
                start = distance_time(stops, ref.start_distance_m)
                end = distance_time(stops, ref.end_distance_m, departure=True)
                if start is None or end is None or end < start:
                    continue
                if intervals and start <= intervals[-1][1]:
                    intervals[-1][1] = max(end, intervals[-1][1])
                else:
                    intervals.append([start, end])
            result.extend(
                {
                    "train_run_id": run.id,
                    "train_number": run.train_number,
                    "station_track_id": track_id,
                    "arrival_s": start,
                    "departure_s": end,
                }
                for start, end in intervals
            )
    return sorted(
        result, key=lambda r: (r["arrival_s"], r["station_track_id"], r["train_run_id"])
    )


def station_state(repo, station_id, service_date, time_s):
    rows = station_timetable(repo, station_id, service_date)
    tracks = {
        t.id: {"state": "free", "train_run_ids": []}
        for t in repo.station_tracks.values()
        if t.station_id == station_id
    }
    from .simulation import TimetableLinearInterpolationModel

    active = []
    seen = set()
    for row in rows:
        if row["train_run_id"] in seen:
            continue
        seen.add(row["train_run_id"])
        position = TimetableLinearInterpolationModel().position_at_time(
            repo, "", row["train_run_id"], time_s, allow_reference=True
        )
        if position["state"] not in {"running", "dwelling"}:
            continue
        projection = run_projection(repo, row["train_run_id"])
        if not projection:
            continue
        _, refs, _ = projection
        distance = position["route_distance_m"]
        edge = next(
            (
                r.edge_id
                for r in refs
                if r.start_distance_m <= distance < r.end_distance_m
            ),
            None,
        )
        if edge is None and refs and distance == refs[-1].end_distance_m:
            edge = refs[-1].edge_id
        on_station = False
        for ident, track in repo.station_tracks.items():
            if ident in tracks and edge in {r.edge_id for r in track.edge_refs}:
                tracks[ident]["train_run_ids"].append(row["train_run_id"])
                tracks[ident]["state"] = "occupied"
                on_station = True
        route = repo.station_routes.get(row["station_route_id"])
        if (
            on_station
            or (route and edge in {r.edge_id for r in route.edge_refs})
            or (edge in repo.edges and repo.edges[edge].facility_id == station_id)
        ):
            active.append({**row, **position, "edge_id": edge})
    return {
        "station_id": station_id,
        "time_s": time_s,
        "tracks": tracks,
        "trains": active,
    }


def corridor_archive(repo, corridor_id):
    corridor = repo.corridors[corridor_id]
    positions = dict(ordered_path_nodes(repo, corridor.edge_refs))
    stations = [
        {"id": s.id, "name": s.name, "distance_m": positions[s.anchor_node_id]}
        for s in repo.stations.values()
        if s.anchor_node_id in positions
    ]
    points = [
        {
            "id": p.id,
            "name": p.name,
            "point_type": p.point_type,
            "distance_m": min(positions[key] for key in p.node_ids if key in positions),
        }
        for p in repo.operational_points.values()
        if any(key in positions for key in p.node_ids)
    ]
    intent = repo.route_intents.get(corridor.route_intent_id)
    itinerary = []
    if intent:
        collections = {
            "station": repo.stations,
            "node": repo.nodes,
            "operational_point": repo.operational_points,
            "infrastructure_line": repo.lines,
        }
        for step in intent.steps:
            obj = collections[step.kind][step.reference_id]
            itinerary.append(
                {
                    "kind": step.kind,
                    "id": obj.id,
                    "name": getattr(obj, "name", obj.id),
                    "direction": step.direction,
                }
            )
    return {
        "id": corridor.id,
        "name": corridor.name,
        "length_m": corridor.edge_refs[-1].end_distance_m,
        "verification_status": corridor.verification_status,
        "source_id": corridor.source_id,
        "itinerary": itinerary,
        "stations": sorted(stations, key=lambda s: s["distance_m"]),
        "operational_points": sorted(points, key=lambda p: p["distance_m"]),
        "edges": [
            {
                "id": r.edge_id,
                "direction": "forward" if r.forward else "reverse",
                "start_m": r.start_distance_m,
                "end_m": r.end_distance_m,
                "line_id": repo.edges[r.edge_id].infrastructure_line_id,
            }
            for r in corridor.edge_refs
        ],
    }


def corridor_diagram_svg(
    repo, corridor_id, service_date, linewidth=1.5, font_size=12, width=1400, height=800
):
    if (
        not 0.1 <= linewidth <= 20
        or not 6 <= font_size <= 72
        or not 200 <= width <= 12000
        or not 200 <= height <= 12000
    ):
        raise ValueError("图表尺寸、线宽或字号无效")
    corridor = repo.corridors[corridor_id]
    axis = dict(ordered_path_nodes(repo, corridor.edge_refs))
    nodes = {
        s.id: axis[s.anchor_node_id]
        for s in repo.stations.values()
        if s.anchor_node_id in axis
    }
    target = [(r.edge_id, r.forward) for r in corridor.edge_refs]
    rev = [(key, not f) for key, f in reversed(target)]
    trains = []
    for run in repo.train_runs.values():
        if run.service_date != service_date or not run.corridor_id:
            continue
        path = repo.corridors[run.corridor_id]
        legs = [(r.edge_id, r.forward) for r in path.edge_refs]
        if legs not in (target, rev):
            continue
        points = []
        for stop in repo.stops_for(run.id):
            if stop.station_id not in nodes:
                continue
            for time in (stop.arrival_time_s, stop.departure_time_s):
                if time is not None:
                    points.append((time, nodes[stop.station_id]))
        if points:
            trains.append((run, points, legs == target))
    start = min((p[0] for _, pts, _ in trains for p in pts), default=0) // 3600 * 3600
    end = max((p[0] for _, pts, _ in trains for p in pts), default=start + 3600)
    end = max(end, start + 3600)
    left, top, right, bottom = 130, 60, width - 40, height - 60
    x = lambda t: left + (t - start) / (end - start) * (right - left)
    y = lambda d: (
        top + d / max(corridor.edge_refs[-1].end_distance_m, 1) * (bottom - top)
    )
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<g font-family="Microsoft YaHei,sans-serif" font-size="{font_size}" fill="#20313d">',
        f'<text x="{left}" y="30">{escape(corridor.name)} · {escape(service_date)} · 正向红 / 反向蓝</text>',
    ]
    for sid, distance in sorted(nodes.items(), key=lambda item: item[1]):
        pos = y(distance)
        parts.extend(
            [
                f'<line x1="{left}" x2="{right}" y1="{pos}" y2="{pos}" stroke="#d8e0e5"/>',
                f'<text x="8" y="{pos + 4}">{escape(repo.stations[sid].name)}</text>',
            ]
        )
    for t in range(start, int(end) + 1, 3600):
        pos = x(t)
        parts.extend(
            [
                f'<line x1="{pos}" x2="{pos}" y1="{top}" y2="{bottom}" stroke="#d8e0e5"/>',
                f'<text x="{pos - 18}" y="{height - 20}">{t // 3600:02d}:00</text>',
            ]
        )
    for run, points, forward in trains:
        color = "#a52432" if forward else "#2766a1"
        coords = " ".join(f"{x(t):.2f},{y(d):.2f}" for t, d in points)
        parts.append(
            f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="{linewidth}"/>'
        )
        t, d = points[0]
        parts.append(
            f'<text x="{x(t) + 3}" y="{y(d) - 5}" fill="{color}">{escape(run.train_number)}</text>'
        )
    parts.append("</g></svg>")
    return "".join(parts)
