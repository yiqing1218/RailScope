"""Drawing-only bridges distinguish intersections from shared graph nodes."""

from collections import defaultdict
import math


def crossing_symbols(drawings, graph, nodes):
    # A spatial hash keeps this bounded for hundreds of source edges. Curves
    # have already been sampled by connection_layout, never by the renderer.
    cells = defaultdict(list)
    segments = []
    size = 70
    for key, drawing in drawings.items():
        for a, b in zip(drawing.points, drawing.points[1:]):
            if math.dist(a, b) < 0.01:
                continue
            index = len(segments)
            segments.append((key, a, b))
            for x in range(
                math.floor(min(a[0], b[0]) / size),
                math.floor(max(a[0], b[0]) / size) + 1,
            ):
                for y in range(
                    math.floor(min(a[1], b[1]) / size),
                    math.floor(max(a[1], b[1]) / size) + 1,
                ):
                    cells[x, y].append(index)
    pairs = set()
    rank = {"auxiliary": 0, "connector": 1, "station": 2, "main": 3}
    order = {
        key: i
        for i, (key, d) in enumerate(
            sorted(drawings.items(), key=lambda item: (rank[item[1].role], item[0]))
        )
    }
    found = []
    for indexes in cells.values():
        for offset, i in enumerate(indexes):
            key, a, b = segments[i]
            for j in indexes[offset + 1 :]:
                pair = (min(i, j), max(i, j))
                if pair in pairs:
                    continue
                pairs.add(pair)
                other, c, d = segments[j]
                if key == other:
                    continue
                u = (b[0] - a[0], b[1] - a[1])
                v = (d[0] - c[0], d[1] - c[1])
                det = u[0] * v[1] - u[1] * v[0]
                if abs(det) < 0.0001:
                    continue
                w = (c[0] - a[0], c[1] - a[1])
                t = (w[0] * v[1] - w[1] * v[0]) / det
                s = (w[0] * u[1] - w[1] * u[0]) / det
                if not -0.00001 <= t <= 1.00001 or not -0.00001 <= s <= 1.00001:
                    continue
                point = (a[0] + t * u[0], a[1] + t * u[1])
                shared = set(graph.endpoints[key]) & set(graph.endpoints[other])
                if any(math.dist(point, nodes[n]) < 14 for n in shared):
                    continue
                upper, lower, vector = (
                    (key, other, u) if order[key] > order[other] else (other, key, v)
                )
                # Consecutive sampled pieces may report the same intersection.
                if any(
                    m["upper_edge"] == upper
                    and m["lower_edge"] == lower
                    and math.dist(m["point"], point) < 8
                    for m in found
                ):
                    continue
                length = math.hypot(*vector)
                found.append(
                    {
                        "upper_edge": upper,
                        "lower_edge": lower,
                        "point": point,
                        "tangent": tuple(value / length for value in vector),
                    }
                )
    return found
