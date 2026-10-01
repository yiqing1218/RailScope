"""Circular source bearing -> perimeter port; real open endpoints only."""

from collections import defaultdict
import math
from .helpers import edge_role
from .yard_classifier import business_line


def bearing(points, local, radius):
    samples = [local(p) for p in points]
    far = [p for p in samples if math.hypot(*p) >= radius * 0.65]
    # Source sampling density cannot bias the result: measure the outward
    # continuation at evenly spaced distances on its original polyline.
    from shapely.geometry import LineString

    line = LineString(samples)
    uniform = [
        tuple(line.interpolate(line.length * i / 16).coords[0]) for i in range(17)
    ]
    near = [p for p in uniform if radius * 0.65 <= math.hypot(*p) <= radius * 1.5]
    choices = near or (far[-3:] if far else samples[-1:])
    vectors = [
        (x / max(math.hypot(x, y), 0.001), y / max(math.hypot(x, y), 0.001))
        for x, y in choices
    ]
    x, y = sum(p[0] for p in vectors), sum(p[1] for p in vectors)
    length = math.hypot(x, y)
    return (x / length, y / length) if length else (1.0, 0.0)


def direction_ports(repo, graph, ownership, interior, options):
    groups = []
    warnings = []
    lo, hi = interior.source_core
    ca, sa = math.cos(interior.angle), math.sin(interior.angle)
    sy = sum(l.source_y for l in interior.lanes) / len(interior.lanes)
    mx = (lo + hi) / 2
    origin = (ca * mx - sa * sy, sa * mx + ca * sy)

    def centred(point):
        x, y = interior.local(point)
        return x - origin[0], y - origin[1]

    for node, keys in sorted(graph.adjacency.items()):
        if len(keys) != 1:
            continue
        key = keys[0]
        edge = repo.edges[key]
        own = ownership[key]
        # Platform ends, safety tracks and termini in the station are closed
        # ends, never fictitious through-line outlets.
        sx, sy = interior.source_nodes[node]
        if (
            lo - 0.01 <= sx <= hi + 0.01
            and min(l.source_y for l in interior.lanes) - 50
            <= sy
            <= max(l.source_y for l in interior.lanes) + 50
        ):
            continue
        if edge_role(repo, edge, options) != "main" or not business_line(
            repo.lines.get(own.line_id)
        ):
            continue
        coords = edge.coordinates if node == edge.to_node_id else edge.coordinates[::-1]
        vector = bearing(coords, centred, options.direction_radius_m)
        ca, sa = math.cos(interior.angle), math.sin(interior.angle)
        dx, dy = ca * vector[0] + sa * vector[1], -sa * vector[0] + ca * vector[1]
        screen = (dx, -dy)
        side = (
            ("left" if dx < 0 else "right")
            if abs(dx) >= abs(dy) * 0.65
            else ("top" if dy > 0 else "bottom")
        )
        angle = math.degrees(math.atan2(vector[1], vector[0])) % 360
        point = interior.local((repo.nodes[node].lon, repo.nodes[node].lat))
        match = next(
            (
                g
                for g in groups
                if g["line"].id == own.line_id
                and g["side"] == side
                and sum(a * b for a, b in zip(g["vector"], vector)) > 0.95
            ),
            None,
        )
        if match:
            match["nodes"].append(node)
            match["edge_ids"].add(key)
            match["source_points"].append(point)
            match["vectors"].append(vector)
        else:
            groups.append(
                {
                    "line": repo.lines[own.line_id],
                    "nodes": [node],
                    "edge_ids": {key},
                    "vector": vector,
                    "vectors": [vector],
                    "screen_vector": screen,
                    "side": side,
                    "source_points": [point],
                    "angle_degrees": angle,
                    "role": "main",
                }
            )
    left, top, right, bottom = interior.frame
    cx = (left + right) / 2
    cy = (top + bottom) / 2
    occurrences = defaultdict(int)
    for g in groups:
        vx = sum(v[0] for v in g["vectors"])
        vy = sum(v[1] for v in g["vectors"])
        mag = math.hypot(vx, vy)
        g["vector"] = (vx / mag, vy / mag)
        ca, sa = math.cos(interior.angle), math.sin(interior.angle)
        dx, dy = ca * vx + sa * vy, sa * vx - ca * vy
        g["screen_vector"] = (dx / mag, dy / mag)
        if g["side"] in ("left", "right"):
            px = left if dx < 0 else right
            py = cy + dy / abs(dx) * (right - left) / 2
            # Near-horizontal ports retain the source angular order rather
            # than perpetuating the station's lane order.
            py = max(top + 35, min(bottom - 35, py))
        else:
            py = top if dy < 0 else bottom
            px = cx + dx / abs(dy) * (bottom - top) / 2
            px = max(left + 35, min(right - 35, px))
        g["point"] = (px, py)
        base = g["line"].id + ":" + g["side"]
        index = occurrences[base]
        occurrences[base] += 1
        g["key"] = base if index == 0 else base + ":" + min(g["nodes"])
    for side in ("left", "right", "top", "bottom"):
        axis = 1 if side in ("left", "right") else 0
        ports = sorted(
            (g for g in groups if g["side"] == side),
            key=lambda g: (g["point"][axis], g["source_points"][0][axis], g["key"]),
        )
        lower, upper = (top + 30, bottom - 30) if axis == 1 else (left + 30, right - 30)
        gap = max(options.label_size * 2.4, 55)
        if len(ports) > 1:
            gap = min(gap, (upper - lower) / (len(ports) - 1))
        positions = [max(lower, g["point"][axis]) for g in ports]
        for i in range(1, len(positions)):
            positions[i] = max(positions[i], positions[i - 1] + gap)
        if positions and positions[-1] > upper:
            positions[-1] = upper
            for i in range(len(positions) - 2, -1, -1):
                positions[i] = min(positions[i], positions[i + 1] - gap)
        if positions and positions[0] < lower:
            warnings.append("边缘线路端口过密，请增大画布。")
        for g, p in zip(ports, positions):
            center = list(g["point"])
            center[axis] = p
            points = []
            sorted_nodes = sorted(
                g["nodes"],
                key=lambda n: (interior.source_nodes[n][1 if axis == 1 else 0], n),
                reverse=axis == 1,
            )
            for i, node in enumerate(sorted_nodes):
                point = list(center)
                point[axis] += (i - (len(sorted_nodes) - 1) / 2) * 10
                interior.nodes[node] = tuple(point)
                points.append(tuple(point))
            g["nodes"] = sorted_nodes
            g["point"] = tuple(center)
            g["points"] = points
            g["original_points"] = list(points)
            g["extended"] = False
            g["visible"] = options.line_overrides.get(g["line"].id, {}).get(
                "label", True
            )
            g["visible"] = options.port_overrides.get(g["key"], {}).get(
                "visible", g["visible"]
            )
            g["direction_source"] = "source_geojson_circular_bearing"
    return groups, warnings
