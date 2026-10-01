"""One cubic per same-ownership degree-two chain, split without losing edges."""

import math
from .topology import chains


def cubic(a, b, start_outward=None, end_outward=None):
    dx, dy = b[0] - a[0], b[1] - a[1]
    lead = max(30, min(180, math.hypot(dx, dy) * 0.42))
    sign = 1 if dx >= 0 else -1
    c1 = (a[0] + sign * lead, a[1])
    c2 = (b[0] - sign * lead, b[1])
    if start_outward:
        c1 = tuple(a[i] - start_outward[i] * lead for i in (0, 1))
    if end_outward:
        c2 = tuple(b[i] - end_outward[i] * lead for i in (0, 1))
    return (a, c1, c2, b)


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


def connection_curves(repo, graph, keys, ownership, interior, ports=()):
    result = {}
    signature = lambda k: (
        ownership[k].line_id,
        ownership[k].yard_id,
        repo.edges[k].track_role,
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
