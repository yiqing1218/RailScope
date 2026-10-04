"""A yard's own main-track broad trend, with a stable platform reference."""

from statistics import mean
import math

try:
    from ..station_diagram_geometry import (
        common_baseline,
        smooth_baseline,
        baseline_value,
        densify,
    )
except ImportError:
    from station_diagram_geometry import (
        common_baseline,
        smooth_baseline,
        baseline_value,
        densify,
    )


def yard_baseline(paths, seed_paths, core, interval):
    lo, hi = interval
    a, b = core
    seeds = [
        tuple(p for p in densify(path, [a, b]) if a <= p[0] <= b) for path in seed_paths
    ]
    seeds = [p for p in seeds if len(p) >= 2]
    central = smooth_baseline(common_baseline(seeds, core)) if seeds else ()
    if not central:
        central = common_baseline(seeds, core) if seeds else ()
    broad = common_baseline(paths, interval)
    if not broad:
        return central
    # Sample a broad window instead of following median jumps when a fragmented
    # reference pair briefly contains one rail or a secondary branch.
    window = max(50, (b - a) * 0.2)
    knots = sorted(
        {lo + (hi - lo) * i / 160 for i in range(161)}
        | {a, b}
        | {p[0] for p in central}
    )

    def filtered(x):
        return mean(baseline_value(broad, x + window * n / 4) for n in range(-4, 5))

    result = []
    for x in knots:
        if central and a <= x <= b:
            y = baseline_value(central, x)
        elif central:
            boundary = a if x < a else b
            y = filtered(x) - filtered(boundary) + baseline_value(central, boundary)
        else:
            y = filtered(x)
        result.append((x, y))
    return tuple(result)


def central_reference_paths(
    repo, raw, graph, seed_paths, seed_keys, core, interval=None
):
    """Follow source tangents solely to select a drawing reference, not a route.

    All other edges and their connections remain in the exported graph. This
    prevents an unrelated branch at a distant junction becoming the yard axis.
    """
    result, keys = [], set(seed_keys)
    endpoints = {
        raw[k][i]: n
        for k, e in repo.edges.items()
        if k in raw
        for i, n in ((0, e.from_node_id), (-1, e.to_node_id))
    }
    limit = max(1800, (core[1] - core[0]) * 2)
    if interval:
        limit = max(limit, core[0] - interval[0], interval[1] - core[1])
    for initial in seed_paths:
        path = list(initial)
        for reverse in (False, True):
            if reverse:
                path.reverse()
            used = set(seed_keys)
            sign = 1 if path[-1][0] > path[0][0] else -1
            for _ in range(100):
                current = path[-1]
                if (current[0] - (core[1] if sign > 0 else core[0])) * sign > limit:
                    break
                node = endpoints.get(current)
                if node is None:
                    break
                dx, dy = current[0] - path[-2][0], current[1] - path[-2][1]
                length = math.hypot(dx, dy)
                choices = []
                for k in graph.adjacency[node]:
                    if k in used:
                        continue
                    e = repo.edges[k]
                    if e.track_role in (
                        "storage_track",
                        "safety_track",
                        "depot_track",
                    ) or e.service in ("spur", "yard"):
                        continue
                    points = raw[k] if e.from_node_id == node else raw[k][::-1]
                    vx, vy = points[-1][0] - current[0], points[-1][1] - current[1]
                    mag = math.hypot(vx, vy)
                    if vx * sign <= 0.01 or mag < 0.01 or length < 0.01:
                        continue
                    choices.append(((vx * dx + vy * dy) / (mag * length), k, points))
                if not choices:
                    break
                score, k, points = max(choices)
                if score < 0.25:
                    break
                used.add(k)
                keys.add(k)
                path.extend(points[1:])
            if reverse:
                path.reverse()
        result.append(tuple(path))
    return result, keys
