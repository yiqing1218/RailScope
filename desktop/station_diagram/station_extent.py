"""Functional drawing limits: after the last interaction, only trunks remain.

This interval is a display partition, never a fabricated station boundary.
"""

from shapely.geometry import LineString
from shapely.strtree import STRtree
from collections import defaultdict


def functional_interval(repo, raw, graph, inner, body, roles, ownership):
    points = list(body) or [p for key in inner for p in raw[key]]
    lo, hi = min(p[0] for p in points), max(p[0] for p in points)
    events = []
    line_events = defaultdict(list)
    for node, keys in graph.adjacency.items():
        # Actual switches, connector joins and multiple named trunks belong to
        # the station/connection drawing region, even outside its GIS site.
        if any(roles[k] == "main" for k in keys) and (
            len(keys) >= 3 or any(roles[k] == "connector" for k in keys)
        ):
            key = keys[0]
            index = 0 if repo.edges[key].from_node_id == node else -1
            events.append(raw[key][index][0])
            for line_id in {ownership[k].line_id for k in keys if ownership[k].line_id}:
                line_events[line_id].append(raw[key][index][0])
    trunks = [k for k in raw if roles[k] == "main" and ownership[k].line_id]
    shapes = [LineString(raw[k]) for k in trunks]
    if shapes:
        tree = STRtree(shapes)
        for i, geometry in enumerate(shapes):
            for j in tree.query(geometry, predicate="intersects"):
                if (
                    j <= i
                    or ownership[trunks[i]].line_id == ownership[trunks[j]].line_id
                ):
                    continue
                intersection = geometry.intersection(shapes[j])
                if not intersection.is_empty:
                    events.extend((intersection.bounds[0], intersection.bounds[2]))
                    for line_id in (
                        ownership[trunks[i]].line_id,
                        ownership[trunks[j]].line_id,
                    ):
                        line_events[line_id].extend(
                            (intersection.bounds[0], intersection.bounds[2])
                        )
    if events:
        lo, hi = min(lo, min(events)), max(hi, max(events))
    pad = max(15, (hi - lo) * 0.015)
    # Each trunk can be free of interactions before another trunk is. A single
    # rectangular global limit must not swallow its valid outlet.
    core_lo, core_hi = min(p[0] for p in points), max(p[0] for p in points)
    line_ids = {ownership[k].line_id for k in trunks}
    intervals = {
        ident: (
            min([core_lo, *line_events[ident]]) - 1,
            max([core_hi, *line_events[ident]]) + 1,
        )
        for ident in line_ids
    }
    return (lo - pad, hi + pad), intervals
