"""Per-stop, bounded station paths; shared Corridors remain immutable."""

from dataclasses import replace
import heapq

from ..integrity import path_refs


def effective_path(repo, corridor_id, stops):
    corridor = repo.corridors[corridor_id]
    legs = [(r.edge_id, r.forward) for r in corridor.edge_refs]
    replacements = []
    for stop in stops:
        if not stop.station_route_id:
            continue
        route = repo.station_routes[stop.station_route_id]
        if route.station_id != stop.station_id:
            raise ValueError("StationRoute belongs to another station")
        refs = path_refs(repo, [(r.edge_id, r.forward) for r in route.edge_refs])
        if route.verification_status not in {
            "official",
            "official_confirmed",
            "user_verified",
            "manual_override",
            "automatic_reference",
        }:
            raise ValueError("StationRoute is unresolved")
        nodes = [corridor.origin_node_id]
        nodes.extend(
            repo.edges[key].to_node_id if forward else repo.edges[key].from_node_id
            for key, forward in legs
        )
        candidates = [
            (a, b)
            for a, n in enumerate(nodes)
            if n == route.entry_node_id
            for b in range(a + 1, len(nodes))
            if nodes[b] == route.exit_node_id
        ]
        if len(candidates) != 1:
            raise ValueError(
                "StationRoute boundaries are not unique ordered Corridor endpoints"
            )
        a, b = candidates[0]
        if any(a < end and start < b for start, end, _ in replacements):
            raise ValueError("Overlapping station routes")
        selected = [(r.edge_id, r.forward) for r in refs]
        # Cross-line changes need a separate full Corridor. A station detour uses
        # explicitly station-owned tracks/edges, or an already verified manual route.
        if route.verification_status == "automatic_reference":
            owned = {
                e.id for e in repo.edges.values() if e.facility_id == stop.station_id
            }
            owned.update(
                r.edge_id
                for t in repo.station_tracks.values()
                if t.station_id == stop.station_id
                for r in t.edge_refs
            )
            if any(
                key not in owned
                and key not in {r.edge_id for r in corridor.edge_refs[a:b]}
                for key, _ in selected
            ):
                raise ValueError(
                    "Automatic station route leaves known station topology"
                )
        if stop.station_track_id:
            track = repo.station_tracks[stop.station_track_id]
            if not {r.edge_id for r in track.edge_refs} & {key for key, _ in selected}:
                raise ValueError(
                    "StationRoute does not traverse selected platform track"
                )
        replacements.append((a, b, selected))
    for start, end, selected in sorted(replacements, reverse=True):
        legs[start:end] = selected
    return path_refs(repo, legs)


def positioned_stops(repo, refs, stops):
    """Resolve a selected platform track to its real midpoint, never station centroid."""
    result = []
    for stop in stops:
        if stop.station_track_id and stop.stop_edge_id is None:
            ids = {
                r.edge_id for r in repo.station_tracks[stop.station_track_id].edge_refs
            }
            matches = [r for r in refs if r.edge_id in ids]
            if not matches:
                raise ValueError("Selected station track is absent from effective path")
            ref = matches[len(matches) // 2]
            stop = replace(
                stop,
                stop_edge_id=ref.edge_id,
                stop_offset_m=repo.edges[ref.edge_id].length_m / 2,
                stop_edge_sequence=ref.sequence,
            )
        elif stop.stop_edge_id and stop.station_route_id:
            matches = [r for r in refs if r.edge_id == stop.stop_edge_id]
            if len(matches) == 1:
                stop = replace(stop, stop_edge_sequence=matches[0].sequence)
        result.append(stop)
    return tuple(result)


def resolve_station_route(repo, station_id, entry_node_id, exit_node_id, track_id):
    """Find a real directed reference path via a station track. No guessed crossover."""
    track = repo.station_tracks[track_id]
    if track.station_id != station_id or not track.edge_refs:
        return {"status": "unresolved", "reason": "站台股道尚未建立真实轨道关联"}
    owned = {e.id for e in repo.edges.values() if e.facility_id == station_id}
    owned.update(
        r.edge_id
        for t in repo.station_tracks.values()
        if t.station_id == station_id
        for r in t.edge_refs
    )
    target = {r.edge_id for r in track.edge_refs}
    adjacency = {}
    for key in owned:
        edge = repo.edges[key]
        if edge.construction_status != "operating":
            continue
        for forward in (True, False):
            if edge.direction not in {"both", "forward" if forward else "reverse"}:
                continue
            a, b = (
                (edge.from_node_id, edge.to_node_id)
                if forward
                else (edge.to_node_id, edge.from_node_id)
            )
            adjacency.setdefault(a, []).append((b, key, forward, edge.length_m))
    queue = [(0, entry_node_id, False, ())]
    best = {}
    while queue:
        cost, node, visited, legs = heapq.heappop(queue)
        if cost >= best.get((node, visited), float("inf")):
            continue
        best[node, visited] = cost
        if node == exit_node_id and visited:
            return {
                "status": "automatic_reference",
                "edge_refs": path_refs(repo, legs),
                "source": "station_topology",
                "verification_status": "automatic_reference",
            }
        for end, key, forward, length in adjacency.get(node, ()):
            if key not in {leg[0] for leg in legs}:
                heapq.heappush(
                    queue,
                    (
                        cost + length,
                        end,
                        visited or key in target,
                        legs + ((key, forward),),
                    ),
                )
    return {"status": "unresolved", "reason": "已载入站区拓扑缺少连续可运营进出站路径"}
