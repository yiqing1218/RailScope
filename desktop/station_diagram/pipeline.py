"""Topology -> ownership -> station lanes -> connections -> direction ports."""

from collections import defaultdict
import math
from statistics import median
from .extractor import extract
from .topology import build_graph
from .yard_classifier import classify
from .layout import build_interior, throat_path
from .connection_layout import (
    connection_curves,
    path as curve_path,
    point as curve_point,
)
from .crossings import crossing_symbols
from .labels import convergence_labels
from .direction_layout import direction_ports
from .helpers import DiagramEdge, DiagramLayout, PlatformSymbol, edge_role


def poly_path(points):
    return "M " + " L ".join(f"{p[0]:.4f},{p[1]:.4f}" for p in points)


def build_layout(repo, context=(), options=None):
    if options is None:
        try:
            from ..station_diagram_layout import DiagramOptions
        except ImportError:
            from station_diagram_layout import DiagramOptions
        options = DiagramOptions()
    extraction = extract(repo, context, options)
    graph = build_graph(repo, extraction.selected)
    ownership, warnings = classify(repo, extraction.selected, options)
    interior = build_interior(repo, context, extraction, graph, ownership, options)
    ports, direction_warnings = direction_ports(
        repo, graph, ownership, interior, options
    )
    lo, hi = interior.source_core
    cl, ct, cr, cb = interior.core
    far = set()
    for key, (a, b) in graph.endpoints.items():
        xs = [p[0] for p in interior.raw[key]]
        if key not in interior.lane_for_edge and (
            min(xs) < lo - interior.throat_limit or max(xs) > hi + interior.throat_limit
        ):
            far.add(key)
    curves = connection_curves(repo, graph, far, ownership, interior, ports)
    drawings = {}
    for key, (a, b) in graph.endpoints.items():
        edge = repo.edges[key]
        pa, pb = interior.nodes[a], interior.nodes[b]
        role = edge_role(repo, edge, options)
        if key in curves:
            points = tuple(
                curve_point(curve, t / 32) for curve in curves[key] for t in range(33)
            )
            d = curve_path(curves[key], interior.orient)
            zone = "external_connection"
        elif key in interior.lane_for_edge:
            lane = interior.lane_for_edge[key]
            x0, x1 = interior.raw[key][0][0], interior.raw[key][-1][0]
            sign = 1 if x1 >= x0 else -1
            cuts = [
                (x, cl if x == lo else cr)
                for x in (lo, hi)
                if min(x0, x1) < x < max(x0, x1)
            ]
            cuts.sort(reverse=sign < 0)
            anchors = [pa, *[(x, lane.y) for _, x in cuts], pb]
            points = []
            for start, end in zip(anchors, anchors[1:]):
                segment = throat_path(start, end)
                points.extend(segment if not points else segment[1:])
            if not points:
                points = [pa, pb]
            points = tuple(points)
            d = poly_path(tuple(map(interior.orient, points)))
            zone = "platform_core"
        else:
            points = throat_path(pa, pb)
            d = poly_path(tuple(map(interior.orient, points)))
            zone = "throat"
        oriented = tuple(map(interior.orient, points))
        drawings[key] = DiagramEdge(
            key, oriented, role, key not in extraction.inner, zone, (oriented,), d
        )
    # Symbols come only from actual source platform features.
    symbols = []
    for ident, kind, points in interior.platforms:
        x = median(p[0] for p in points)
        y = median(p[1] for p in points)
        above = [l for l in interior.lanes if l.source_y >= y]
        below = [l for l in interior.lanes if l.source_y < y]
        near = min(interior.lanes, key=lambda l: abs(l.source_y - y))
        spacing = (
            abs(interior.lanes[1].y - interior.lanes[0].y)
            if len(interior.lanes) > 1
            else 35
        )
        py = (
            (above[-1].y + below[0].y) / 2
            if above and below
            else near.y + (-1 if y > near.source_y else 1) * spacing * 0.55
        )
        px = cl + (x - lo) / (hi - lo) * (cr - cl)
        source_length = max(p[0] for p in points) - min(p[0] for p in points)
        length = min(
            cr - cl, max((cr - cl) * 0.25, source_length / (hi - lo) * (cr - cl))
        )
        center = interior.orient((px, py))
        symbols.append(
            PlatformSymbol(
                ident,
                kind,
                *center,
                length,
                spacing * 0.42 * options.platform_width,
                90 if options.orientation == "portrait" else 0,
            )
        )
    yard_lanes = defaultdict(list)
    for lane in interior.lanes:
        if lane.yard_id:
            yard_lanes[lane.yard_id].append(lane)
    yard_labels = []
    for yard, lanes in sorted(yard_lanes.items()):
        names = {l.yard_name for l in lanes if l.yard_name}
        yard_labels.append(
            {
                "yard_id": yard,
                "text": next(iter(names)) if len(names) == 1 else yard,
                "point": interior.orient(((cl + cr) / 2, min(l.y for l in lanes) - 20)),
                "edge_id": min(lanes[0].edge_ids),
            }
        )
    line_labels = convergence_labels(repo, graph, drawings, ports, interior, options)
    for port in ports:
        port["point"] = interior.orient(port["point"])
        port["points"] = list(map(interior.orient, port["points"]))
        port["original_points"] = list(port["points"])
        if options.orientation == "portrait":
            port["side"] = {
                "left": "bottom",
                "right": "top",
                "top": "left",
                "bottom": "right",
            }[port["side"]]
            vx, vy = port["screen_vector"]
            port["screen_vector"] = (vy, -vx)
    nodes = {n: interior.orient(p) for n, p in interior.nodes.items()}

    def bounds(value):
        points = [interior.orient((value[i], value[j])) for i in (0, 2) for j in (1, 3)]
        return (
            min(p[0] for p in points),
            min(p[1] for p in points),
            max(p[0] for p in points),
            max(p[1] for p in points),
        )

    return DiagramLayout(
        *options.canvas_size,
        interior.angle - (math.pi / 2 if options.orientation == "portrait" else 0),
        "source_platform_axis" if interior.platforms else "station_track_axis",
        drawings,
        symbols,
        nodes,
        ports,
        bounds(interior.core),
        bounds(interior.frame),
        lambda p: interior.orient(interior.project(p)),
        extraction.warnings + warnings + interior.warnings + direction_warnings,
        {key: own.system for key, own in ownership.items()},
        1.0,
        (lo, hi),
        (
            min(p[0] for pts in interior.raw.values() for p in pts),
            max(p[0] for pts in interior.raw.values() for p in pts),
        ),
        (),
        interior.lanes,
        ownership,
        yard_labels,
        line_labels,
        extraction.boundary_source,
        crossing_symbols(drawings, graph, nodes),
        [node for node, keys in sorted(graph.adjacency.items()) if len(keys) >= 3],
    )
