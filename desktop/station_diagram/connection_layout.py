"""One cubic per same-ownership degree-two chain, split without losing edges."""

import math
from dataclasses import replace
from types import SimpleNamespace
from shapely.geometry import LineString
from .topology import chains


def cubic(a, b, start_outward=None, end_outward=None):
    dx, dy = b[0] - a[0], b[1] - a[1]
    lead = min(180, abs(dx) * 0.45)
    sign = 1 if dx >= 0 else -1
    c1 = (a[0] + sign * lead, a[1])
    c2 = (b[0] - sign * lead, b[1])
    if abs(dx) < 0.01:
        lead = min(180, abs(dy) * 0.42)
        sign_y = 1 if dy >= 0 else -1
        c1 = (a[0], a[1] + sign_y * lead)
        c2 = (b[0], b[1] - sign_y * lead)
    if start_outward:
        c1 = tuple(a[i] - start_outward[i] * lead for i in (0, 1))
    if end_outward:
        c2 = tuple(b[i] - end_outward[i] * lead for i in (0, 1))
    # A port tangent may point against the chord. Never let that create an
    # artificial return loop or overshoot between two real connection nodes.
    c1 = tuple(max(min(a[i], b[i]), min(max(a[i], b[i]), c1[i])) for i in (0, 1))
    c2 = tuple(max(min(a[i], b[i]), min(max(a[i], b[i]), c2[i])) for i in (0, 1))
    c1, c2 = tuple(zip(*(sorted((c1[i], c2[i]), reverse=b[i] < a[i]) for i in (0, 1))))
    return (a, c1, c2, b)


def axial_cubic(a, b, axis):
    """Use the station axis as the straight tangent in either page orientation."""
    if axis == 0:
        return cubic(a, b)
    return tuple((p[1], p[0]) for p in cubic((a[1], a[0]), (b[1], b[0])))


def point(curve, t):
    u = 1 - t
    return tuple(
        u**3 * curve[0][i]
        + 3 * u * u * t * curve[1][i]
        + 3 * u * t * t * curve[2][i]
        + t**3 * curve[3][i]
        for i in (0, 1)
    )


def derivative(curve, t):
    u = 1 - t
    return tuple(
        3 * u * u * (curve[1][i] - curve[0][i])
        + 6 * u * t * (curve[2][i] - curve[1][i])
        + 3 * t * t * (curve[3][i] - curve[2][i])
        for i in (0, 1)
    )


def subcurve(curve, t0, t1):
    a, b = point(curve, t0), point(curve, t1)
    da, db = derivative(curve, t0), derivative(curve, t1)
    dt = (t1 - t0) / 3
    return (
        a,
        tuple(a[i] + da[i] * dt for i in (0, 1)),
        tuple(b[i] - db[i] * dt for i in (0, 1)),
        b,
    )


def path(curves, orient=lambda p: p):
    if not curves:
        return ""
    a = orient(curves[0][0])
    out = [f"M {a[0]:.4f},{a[1]:.4f}"]
    for curve in curves:
        out.append(
            "C " + " ".join(f"{p[0]:.4f},{p[1]:.4f}" for p in map(orient, curve[1:]))
        )
    return " ".join(out)


def connection_curves(
    repo,
    graph,
    keys,
    ownership,
    interior,
    ports=(),
    preserve_ownership=True,
    chain_signature=None,
):
    result = {}

    def signature(k):
        if chain_signature:
            return chain_signature(k)
        return (
            (ownership[k].line_id, ownership[k].yard_id, repo.edges[k].track_role)
            if preserve_ownership
            else None
        )

    vectors = {"left": (-1, 0), "right": (1, 0), "top": (0, -1), "bottom": (0, 1)}
    port_vectors = {node: vectors[p["side"]] for p in ports for node in p["nodes"]}
    for legs in chains(graph, keys, signature):
        if not legs:
            continue
        first, forward = legs[0]
        last, last_forward = legs[-1]
        start = graph.endpoints[first][0 if forward else 1]
        end = graph.endpoints[last][1 if last_forward else 0]
        a, b = interior.nodes[start], interior.nodes[end]
        if start == end or math.dist(a, b) < 0.01:
            if not preserve_ownership:
                continue
            # A source loop is drawn as a loop, never collapsed to a zero path.
            for key, fwd in legs:
                n0, n1 = graph.endpoints[key]
                p0, p1 = interior.nodes[n0], interior.nodes[n1]
                result[key] = (cubic(p0, p1),)
            continue
        curve = cubic(a, b, port_vectors.get(start), port_vectors.get(end))
        weights = [max(repo.edges[k].length_m, 0.001) for k, _ in legs]
        total = sum(weights)
        travelled = 0.0
        for (key, fwd), weight in zip(legs, weights):
            t0, t1 = travelled / total, (travelled + weight) / total
            part = subcurve(curve, t0, t1)
            n0, n1 = graph.endpoints[key]
            oriented = part if fwd else part[::-1]
            interior.nodes[n0] = oriented[0]
            interior.nodes[n1] = oriented[-1]
            result[key] = (oriented,)
            travelled += weight
    return result


def arrange_yard_connections(
    repo,
    graph,
    raw,
    edges,
    nodes,
    ownership,
    core,
    frame,
    ports,
    extensions,
    platform_rails=(),
    core_frame=None,
    portrait=False,
    reference_keys=(),
):
    """Standardize post-platform connections after all yard anchors are placed."""
    try:
        from ..station_diagram_geometry import line_parts
    except ImportError:
        from station_diagram_geometry import line_parts
    from .shape_paths import rounded_path

    def is_branch(key):
        return edges[key].role == "connector" or repo.edges[key].track_role in (
            "connecting_line",
            "crossover",
            "crossover_track",
        )

    candidates = set()
    for k, d in edges.items():
        if k in reference_keys:
            continue
        if not (
            max(p[0] for p in raw[k]) < core[0] or min(p[0] for p in raw[k]) > core[1]
        ):
            continue
        a, b = d.points[0], d.points[-1]
        if math.dist(a, b) < 0.01:
            continue
        candidates.add(k)
    curves = connection_curves(
        repo,
        graph,
        candidates,
        ownership,
        SimpleNamespace(nodes=nodes),
        ports,
        preserve_ownership=False,
        chain_signature=is_branch,
    )
    platform_paths = {}
    if core_frame is not None:
        axis = 1 if portrait else 0
        cross = 1 - axis
        band = (core_frame[axis], core_frame[axis + 2])
        rail_keys = {k for k in edges if not is_branch(k) and k not in reference_keys}
        for legs in chains(graph, rail_keys):
            if not any(k in platform_rails for k, _ in legs):
                continue
            first, fwd = legs[0]
            last, last_fwd = legs[-1]
            start = graph.endpoints[first][0 if fwd else 1]
            end = graph.endpoints[last][1 if last_fwd else 0]
            a, b = nodes[start], nodes[end]
            if start == end or abs(b[axis] - a[axis]) < 1:
                continue
            central = [
                p
                for k, _ in legs
                for p in edges[k].points
                if band[0] - 0.01 <= p[axis] <= band[1] + 0.01
            ]
            if not central:
                continue
            level = sum(p[cross] for p in central) / len(central)
            forward = b[axis] > a[axis]
            limits = sorted((a[axis], b[axis]))
            anchors = [a]
            for value in sorted(band, reverse=not forward):
                if limits[0] < value < limits[1]:
                    anchors.append((level, value) if portrait else (value, level))
            anchors.append(b)
            if len(anchors) == 2 and (limits[1] < band[0] or limits[0] > band[1]):
                continue
            segments = [axial_cubic(p, q, axis) for p, q in zip(anchors, anchors[1:])]

            def evaluate(value):
                segment = next(
                    (
                        s
                        for s in segments
                        if min(s[0][axis], s[-1][axis]) - 0.01
                        <= value
                        <= max(s[0][axis], s[-1][axis]) + 0.01
                    ),
                    None,
                )
                if segment is None:
                    return a if abs(value - a[axis]) < abs(value - b[axis]) else b
                # Invert the monotone axial cubic, keeping every source node's
                # ordering along the rail, including edge provenance splits.
                low, high = 0.0, 1.0
                for _ in range(30):
                    t = (low + high) / 2
                    if (point(segment, t)[axis] < value) == forward:
                        low = t
                    else:
                        high = t
                return point(segment, (low + high) / 2)

            for k, _ in legs:
                old = edges[k].points
                aa, bb = graph.endpoints[k]
                x0, x1 = old[0][axis], old[-1][axis]
                samples = [x0 + (x1 - x0) * i / 24 for i in range(25)]
                samples += [v for v in band if min(x0, x1) < v < max(x0, x1)]
                pts = tuple(evaluate(v) for v in sorted(set(samples), reverse=x1 < x0))
                if aa not in (start, end):
                    nodes[aa] = pts[0]
                if bb not in (start, end):
                    nodes[bb] = pts[-1]
                platform_paths[k] = pts
    for k, d in list(edges.items()):
        e = repo.edges[k]
        if k in platform_paths:
            pts = (nodes[e.from_node_id], *platform_paths[k][1:-1], nodes[e.to_node_id])
        elif k in curves:
            pts = (
                nodes[e.from_node_id],
                *(point(curves[k][0], i / 24) for i in range(1, 24)),
                nodes[e.to_node_id],
            )
        else:
            pts = (nodes[e.from_node_id], *d.points[1:-1], nodes[e.to_node_id])
        parts = tuple(line_parts(LineString(pts).intersection(frame)))
        edges[k] = replace(
            d,
            points=pts,
            parts=parts,
            path=" ".join(
                rounded_path(
                    p,
                    8,
                    (core_frame[1 if portrait else 0], core_frame[3 if portrait else 2])
                    if core_frame
                    else None,
                    1 if portrait else 0,
                )
                for p in parts
            ),
        )
    # Extension attachments are real node positions in this drawing, too.
    for ext in extensions:
        e = repo.edges[ext["edge_id"]]
        old, end = ext["points"]
        node = min(
            (e.from_node_id, e.to_node_id), key=lambda n: math.dist(nodes[n], old)
        )
        ext["points"] = (nodes[node], end)
    return sorted(set(curves) | set(reference_keys))
