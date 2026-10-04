"""Straight platform rails in drawing space; source geometry stays untouched."""

from statistics import median

from .topology import chains

try:
    from ..station_diagram_geometry import densify
except ImportError:
    from station_diagram_geometry import densify


def straighten_platform_tracks(graph, paths, nodes, keys, core):
    """Use the chord of each physical through rail, not its GIS wiggles.

    Real junctions stop a rail chain. Short crossing/turnout branches do not
    become platform rails just because they touch the platform band.
    """
    lo, hi = core
    span = max(hi - lo, 1)
    rail_levels, node_levels = {}, {}
    for legs in chains(graph, keys):
        points = []
        for key, forward in legs:
            part = paths[key] if forward else paths[key][::-1]
            points.extend(part if not points else part[1:])
        if not points:
            continue
        band = [p for p in densify(points, (lo, hi)) if lo <= p[0] <= hi]
        if len(band) < 2:
            continue
        a, b = min(band), max(band)
        if b[0] - a[0] < span * 0.35:
            continue
        if abs(b[1] - a[1]) > max(25, (b[0] - a[0]) * 0.2):
            continue
        # A closed cycle or a real reversal is not a through platform track.
        first, fwd = legs[0]
        last, last_fwd = legs[-1]
        start = graph.endpoints[first][0 if fwd else 1]
        end = graph.endpoints[last][1 if last_fwd else 0]
        if start == end:
            continue
        deltas = [q[0] - p[0] for p, q in zip(band, band[1:])]
        if any(d > 1 for d in deltas) and any(d < -1 for d in deltas):
            continue
        level = (a[1] + b[1]) / 2
        for key, _ in legs:
            rail_levels[key] = level
            for node in graph.endpoints[key]:
                if lo - 1e-5 <= nodes[node][0] <= hi + 1e-5:
                    node_levels.setdefault(node, []).append(level)
    normalized_nodes = dict(nodes)
    for node, levels in node_levels.items():
        x, _ = nodes[node]
        normalized_nodes[node] = (x, median(levels))
    normalized = {}
    for key, points in paths.items():
        level = rail_levels.get(key)
        drawn = [
            (p[0], level) if level is not None and lo <= p[0] <= hi else p
            for p in densify(points, (lo, hi))
        ]
        a, b = graph.endpoints[key]
        drawn[0], drawn[-1] = normalized_nodes[a], normalized_nodes[b]
        normalized[key] = tuple(drawn)
    return normalized, normalized_nodes, rail_levels


def core_rail_count(layout, keys, portrait=False):
    """Count physical drawn rails at the core section, not imported fragments."""
    axis = 1 if portrait else 0
    cross = 1 - axis
    mid = (layout.core_bounds[axis] + layout.core_bounds[axis + 2]) / 2
    levels = []
    for key in set(keys) & set(layout.platform_rail_ids):
        for part in layout.edges[key].parts:
            for a, b in zip(part, part[1:]):
                if (
                    min(a[axis], b[axis]) <= mid <= max(a[axis], b[axis])
                    and abs(b[axis] - a[axis]) > 0.01
                ):
                    t = (mid - a[axis]) / (b[axis] - a[axis])
                    levels.append(a[cross] + t * (b[cross] - a[cross]))
                    break
    distinct = []
    for value in sorted(levels):
        if not distinct or value - distinct[-1] > 0.1:
            distinct.append(value)
    return len(distinct)
