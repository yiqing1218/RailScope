"""Smooth the yard's continuous main-rail reference across real switches."""

from dataclasses import replace
from statistics import median

from .connection_layout import axial_cubic, point


def curve_at_axis(curve, value, axis):
    low, high = 0.0, 1.0
    forward = curve[-1][axis] > curve[0][axis]
    for _ in range(32):
        t = (low + high) / 2
        if (point(curve, t)[axis] < value) == forward:
            low = t
        else:
            high = t
    return point(curve, (low + high) / 2)


def smooth_main_references(
    graph,
    raw,
    edges,
    nodes,
    references,
    core,
    interval,
    portrait=False,
    core_frame=None,
):
    """Reposition switch anchors on a broad curve without adding connections.

    Reference paths were selected using source tangents solely for drawing.
    A switch keeps its stable ID and all incident edges follow its new position.
    """
    axis, cross = (1, 0) if portrait else (0, 1)
    source_nodes = {}
    for key in raw:
        a, b = graph.endpoints[key]
        source_nodes[raw[key][0]] = a
        source_nodes[raw[key][-1]] = b
    original_nodes = dict(nodes)
    curves, proposals = {}, {}
    for paths in references:
        for path in paths:
            rail_nodes = [(p[0], source_nodes[p]) for p in path if p in source_nodes]
            central = [(x, n) for x, n in rail_nodes if core[0] <= x <= core[1]]
            pairs = {
                frozenset((a[1], b[1])) for a, b in zip(rail_nodes, rail_nodes[1:])
            }
            mid = sum(core) / 2
            core_points = [
                p
                for k in raw
                if frozenset(graph.endpoints[k]) in pairs
                and min(q[0] for q in raw[k]) <= mid <= max(q[0] for q in raw[k])
                for p in edges[k].points
                if core_frame is not None
                and core_frame[axis] <= p[axis] <= core_frame[axis + 2]
            ]
            if not central and not core_points:
                continue
            level = (
                median(p[cross] for p in core_points)
                if core_points
                else median(original_nodes[n][cross] for _, n in central)
            )
            for sign, boundary in ((-1, core[0]), (1, core[1])):
                side = [
                    (x, n)
                    for x, n in rail_nodes
                    if (x - boundary) * sign >= 0 and interval[0] <= x <= interval[1]
                ]
                if len(side) < 2:
                    continue
                side.sort(reverse=sign < 0)
                x0, n0 = side[0]
                x1, n1 = side[-1]
                if abs(x1 - x0) < 30 or n0 == n1:
                    continue
                a, b = original_nodes[n0], original_nodes[n1]
                # The near anchor continues the platform's horizontal rail.
                a = (level, a[1]) if portrait else (a[0], level)
                if abs(b[axis] - a[axis]) < 1:
                    continue
                curve = axial_cubic(a, b, axis)
                leg_nodes = {n for _, n in side}
                # The source path can share a switch with another reference.
                # Shared proposals reconcile once; no duplicate drawing node.
                for _, node in side:
                    proposals.setdefault(node, []).append(
                        curve_at_axis(curve, original_nodes[node][axis], axis)
                    )
                for key in raw:
                    aa, bb = graph.endpoints[key]
                    if (
                        aa in leg_nodes
                        and bb in leg_nodes
                        and frozenset((aa, bb)) in pairs
                        and key not in curves
                    ):
                        curves[key] = curve
    for node, values in proposals.items():
        nodes[node] = tuple(median(v[i] for v in values) for i in (0, 1))
    for key, curve in curves.items():
        old = edges[key]
        a, b = graph.endpoints[key]
        x0, x1 = original_nodes[a][axis], original_nodes[b][axis]
        pts = (
            nodes[a],
            *(
                curve_at_axis(curve, x0 + (x1 - x0) * i / 24, axis)
                for i in range(1, 24)
            ),
            nodes[b],
        )
        edges[key] = replace(old, points=pts)
    return sorted(curves)
