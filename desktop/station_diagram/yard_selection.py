"""Select drawing yards and their physically connected approaches, read only."""

from collections import deque

from shapely.geometry import LineString

from .topology import build_graph


def platform_membership(platforms, rotate, raw, edge_groups, track_keys):
    result = {}
    for ident, _, points in platforms:
        body = LineString([rotate(p) for p in points])
        candidates = sorted(
            (body.distance(LineString(raw[k])), g)
            for k in track_keys
            if k in raw
            for g in edge_groups.get(k, ())
        )
        result[ident] = (
            {g for distance, g in candidates if distance <= candidates[0][0] + 2}
            if candidates
            else set()
        )
    return result


def select_yards(repo, raw, groups, requested, options, platform_core):
    if not requested:
        return set(raw)
    if not set(requested) <= groups.keys():
        raise ValueError("选择的分场已不存在，请重新选择导出范围")
    seeds = {k for g in requested for k in groups[g]["edge_ids"]} & raw.keys()
    blocked = {
        k
        for g, v in groups.items()
        if g not in requested
        for tid in v["track_ids"]
        for ref in repo.station_tracks[tid].edge_refs
        for k in (ref.edge_id,)
        if k in raw
        and min(p[0] for p in raw[k]) < platform_core[1]
        and max(p[0] for p in raw[k]) > platform_core[0]
    } - seeds
    # A StationTrack may describe a short approach without a platform. Only
    # suppress another yard's platform band, never its connected outer pieces.
    graph = build_graph(repo, set(raw), validate=False)
    retained, queue = set(seeds), deque((k, 0) for k in sorted(seeds))
    while queue:
        k, depth = queue.popleft()
        if depth >= options.topology_depth:
            continue
        for node in graph.endpoints[k]:
            for nxt in graph.adjacency[node]:
                if nxt not in retained and nxt not in blocked:
                    retained.add(nxt)
                    queue.append((nxt, depth + 1))
    return retained
