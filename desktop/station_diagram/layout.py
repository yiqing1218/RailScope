"""Regular station lanes and topology-ordered throat anchors in drawing units."""

from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
import math
from statistics import median
from .helpers import (
    platform_parts,
    principal_axis,
    reliable_platform_axes,
    station_projection,
    edge_role,
)
from .types import Lane


def section_y(points, x):
    samples = []
    for a, b in zip(points, points[1:]):
        if min(a[0], b[0]) <= x <= max(a[0], b[0]) and abs(a[0] - b[0]) > 0.001:
            t = (x - a[0]) / (b[0] - a[0])
            samples.append(a[1] + t * (b[1] - a[1]))
    return median(samples) if samples else None


@dataclass
class Interior:
    raw: dict
    source_nodes: dict
    nodes: dict
    lanes: list
    lane_for_edge: dict
    source_core: tuple
    core: tuple
    frame: tuple
    throat_limit: float
    angle: float
    local: object
    axis: object
    project: object
    orient: object
    platforms: list
    warnings: list


def build_interior(repo, context, extraction, graph, ownership, options):
    local, *_ = station_projection(repo)
    platforms = list(platform_parts(context))
    axes = reliable_platform_axes([[local(p) for p in pts] for _, _, pts in platforms])
    candidates = [
        k
        for k in extraction.inner
        if k in graph.endpoints
        and edge_role(repo, repo.edges[k], options) not in ("connector", "auxiliary")
    ]
    track_refs = {r.edge_id for t in repo.station_tracks.values() for r in t.edge_refs}
    axis_keys = [k for k in candidates if k in track_refs] or candidates
    theta = principal_axis(
        axes or [[local(p) for p in repo.edges[k].coordinates] for k in axis_keys]
    )
    ca, sa = math.cos(theta), math.sin(theta)

    def axis(point):
        x, y = local(point)
        return (ca * x + sa * y, -sa * x + ca * y)

    raw = {k: tuple(map(axis, repo.edges[k].coordinates)) for k in graph.endpoints}
    source_nodes = {
        n: axis((repo.nodes[n].lon, repo.nodes[n].lat)) for n in graph.adjacency
    }
    pp = [(ident, kind, [axis(p) for p in pts]) for ident, kind, pts in platforms]
    if pp:
        # Median platform extent avoids station buildings and an unrelated outlier.
        lo = median(min(p[0] for p in pts) for _, _, pts in pp)
        hi = median(max(p[0] for p in pts) for _, _, pts in pp)
    else:
        points = [p for k in axis_keys or extraction.inner if k in raw for p in raw[k]]
        lo, hi = min(p[0] for p in points), max(p[0] for p in points)
    if hi - lo < 2:
        lo -= 50
        hi += 50
    middle = (lo + hi) / 2
    # Only display lanes are inferred here. All original edges remain in graph.
    seeds = {k: section_y(raw[k], middle) for k in candidates}

    def axial(key):
        step = min(30, (hi - lo) / 10)
        a, b = section_y(raw[key], middle - step), section_y(raw[key], middle + step)
        if a is not None and b is not None:
            return abs(a - b) < step
        pts = raw[key]
        return abs(pts[-1][0] - pts[0][0]) > 2 * abs(pts[-1][1] - pts[0][1])

    seeds = {k: y for k, y in seeds.items() if y is not None and axial(k)}
    if not seeds:
        raise ValueError("站台核心未找到可规则化的真实股道，请检查站台与车站关联")
    lanes = []
    lane_for_edge = {}
    for key, y in sorted(seeds.items(), key=lambda item: (-item[1], item[0])):
        # Duplicate edge fragments at the same cut are one lane only through a
        # real shared node, never just because their coordinates are close.
        shared = next(
            (
                l
                for l in lanes
                if abs(l.source_y - y) < 0.05
                and any(
                    set(graph.endpoints[key]) & set(graph.endpoints[e])
                    for e in l.edge_ids
                )
            ),
            None,
        )
        if shared:
            shared.edge_ids.add(key)
            lane_for_edge[key] = shared
            continue
        own = ownership[key]
        numbers = {
            t.track_number
            for t in repo.station_tracks.values()
            if any(r.edge_id == key for r in t.edge_refs) and t.track_number
        }
        lane = Lane(
            key,
            {key},
            y,
            yard_id=own.yard_id,
            yard_name=own.yard_name,
            system=own.system,
            track_number=next(iter(numbers)) if len(numbers) == 1 else None,
        )
        lanes.append(lane)
        lane_for_edge[key] = lane
    # Follow core fragments through true nodes and straight continuations.
    for lane in lanes:
        queue = list(lane.edge_ids)
        while queue:
            key = queue.pop()
            for node in graph.endpoints[key]:
                x, _ = source_nodes[node]
                if not lo - 0.01 <= x <= hi + 0.01:
                    continue
                for nxt in graph.adjacency[node]:
                    if nxt in lane_for_edge or nxt not in extraction.inner:
                        continue
                    pts = raw[nxt]
                    dx = abs(pts[-1][0] - pts[0][0])
                    dy = abs(pts[-1][1] - pts[0][1])
                    if (
                        edge_role(repo, repo.edges[nxt], options) == "connector"
                        or dx <= 2 * dy
                    ):
                        continue
                    if min(p[0] for p in pts) > hi or max(p[0] for p in pts) < lo:
                        continue
                    lane.edge_ids.add(nxt)
                    lane_for_edge[nxt] = lane
                    queue.append(nxt)
    long = options.width
    short = round(long / options.aspect_ratio)
    padding = max(options.margin + 30, options.label_size * 9.5)
    left, right = padding, long - padding
    top = options.margin + (options.title_size + 60 if options.show_title else 40)
    bottom = short - options.margin - (100 if options.show_legend else 45)
    corewidth = (right - left) * max(
        0.16, min(0.42, 0.28 * 4 / options.station_compression)
    )
    coreleft, coreright = (long - corewidth) / 2, (long + corewidth) / 2
    spacing = min(42, max(12, (bottom - top) / (len(lanes) + 5)))
    if spacing < options.main_width * 3:
        raise ValueError("股道过密，请增大画布或减小统一线宽")
    cy = (top + bottom) / 2
    for i, lane in enumerate(lanes):
        lane.y = cy + (i - (len(lanes) - 1) / 2) * spacing
    sy = [l.source_y for l in lanes]
    ys = [l.y for l in lanes]

    def ymap(y):
        if len(lanes) == 1:
            return cy - (y - sy[0]) * min(2, spacing / 6)
        for i in range(len(sy) - 1):
            if sy[i] >= y >= sy[i + 1]:
                t = (sy[i] - y) / max(sy[i] - sy[i + 1], 0.01)
                return ys[i] + t * (ys[i + 1] - ys[i])
        i = 0 if y > sy[0] else len(sy) - 1
        return ys[i] - (y - sy[i]) * min(spacing / 6, 2)

    # Decision-node stations are ordered, without retaining real throat length.
    extent = max(hi - lo, 150)
    throat = extent * 0.8
    xleft = sorted(
        {
            x
            for n, (x, y) in source_nodes.items()
            if lo - throat <= x < lo and len(graph.adjacency[n]) != 2
        }
    )
    xright = sorted(
        {
            x
            for n, (x, y) in source_nodes.items()
            if hi < x <= hi + throat and len(graph.adjacency[n]) != 2
        }
    )
    throatwidth = min((coreleft - left) * 0.58, corewidth * 0.65)

    def xmap(x):
        if lo <= x <= hi:
            return coreleft + (x - lo) / (hi - lo) * corewidth
        side = -1 if x < lo else 1
        knots = xleft if side < 0 else xright
        boundary = lo if side < 0 else hi
        inside = coreleft if side < 0 else coreright
        if abs(x - boundary) <= throat:
            # Smoothly interpolate at all source knots, including degree-two
            # vertices; no two distinct decision stages collapse to one x.
            ordered = (
                [boundary - throat, *knots, boundary]
                if side < 0
                else [boundary, *knots, boundary + throat]
            )
            target = (
                [
                    inside - throatwidth + i * throatwidth / (len(ordered) - 1)
                    for i in range(len(ordered))
                ]
                if side < 0
                else [
                    inside + i * throatwidth / (len(ordered) - 1)
                    for i in range(len(ordered))
                ]
            )
            idx = max(0, min(len(ordered) - 2, bisect_left(ordered, x) - 1))
            a, b = ordered[idx : idx + 2]
            t = (x - a) / max(b - a, 0.001)
            return target[idx] + t * (target[idx + 1] - target[idx])
        outer = left if side < 0 else right
        fraction = 1 - math.exp(
            -(abs(x - boundary) - throat)
            / max(extent * options.outside_compression / 3, 500)
        )
        return (
            inside
            + side * throatwidth
            + (outer - inside - side * throatwidth) * fraction
        )

    def project(point):
        x, y = axis(point)
        return xmap(x), max(top + spacing, min(bottom - spacing, ymap(y)))

    def orient(point):
        x, y = point
        return (y, long - x) if options.orientation == "portrait" else (x, y)

    nodes = {
        n: project((repo.nodes[n].lon, repo.nodes[n].lat)) for n in graph.adjacency
    }
    # Reserve a true outside connection zone. Branch nodes never crowd the
    # perimeter merely because a complete source edge extends far away.
    reserve = min(150, (right - left) * 0.12)
    nodes = {
        n: (max(left + reserve, min(right - reserve, p[0])), p[1])
        for n, p in nodes.items()
    }
    warnings = []
    if not pp:
        warnings.append("缺少真实站台几何，未生成推测的站台轮廓。")
    anchors = defaultdict(set)
    for key, lane in lane_for_edge.items():
        for node in graph.endpoints[key]:
            x, _ = source_nodes[node]
            if lo - 0.01 <= x <= hi + 0.01:
                anchors[node].add(lane.id)
    lane_by_id = {l.id: l for l in lanes}
    for node, values in anchors.items():
        if len(values) == 1:
            nodes[node] = (nodes[node][0], lane_by_id[next(iter(values))].y)
        else:
            warnings.append("站台区含真实共用道岔节点，保留共用连接：" + node)
    # Degree-two core fragments stay on the same lane through their first
    # real throat decision. Source bends are not copied into those fragments.
    for lane in lanes:
        queue = list(lane.edge_ids)
        while queue:
            key = queue.pop()
            for node in graph.endpoints[key]:
                if len(graph.adjacency[node]) != 2:
                    continue
                for nxt in graph.adjacency[node]:
                    if nxt in lane_for_edge:
                        continue
                    if edge_role(repo, repo.edges[nxt], options) == "connector":
                        continue
                    other = graph.other(nxt, node)
                    # Endpoints are direction-layout anchors, not platform lanes.
                    if len(graph.adjacency[other]) == 1:
                        continue
                    lane_for_edge[nxt] = lane
                    lane.edge_ids.add(nxt)
                    queue.append(nxt)
                    if node not in anchors:
                        nodes[node] = (nodes[node][0], lane.y)
    # A weak reference to source order keeps unrelated parallel tracks apart;
    # shared-node relaxation creates regular fan-in instead of y-clamp piles.
    fixed = set(anchors)
    # A disconnected component with no platform anchor must not relax into
    # a dot or a false parallel lane. Keep its source-relative reference.
    for component in graph.components:
        component_nodes = {n for k in component for n in graph.endpoints[k]}
        if not component_nodes & fixed:
            fixed.update(component_nodes)
    source_y = {n: p[1] for n, p in nodes.items()}
    for _ in range(80):
        updates = {}
        for node, incident in graph.adjacency.items():
            if node in fixed:
                continue
            neighbours = [graph.other(k, node) for k in incident]
            values = [nodes[n][1] for n in neighbours]
            updates[node] = (0.12 * source_y[node] + sum(values)) / (len(values) + 0.12)
        for node, y in updates.items():
            nodes[node] = (nodes[node][0], y)
    return Interior(
        raw,
        source_nodes,
        nodes,
        lanes,
        lane_for_edge,
        (lo, hi),
        (coreleft, ys[0] - spacing, coreright, ys[-1] + spacing),
        (left, top, right, bottom),
        throat,
        theta,
        local,
        axis,
        project,
        orient,
        pp,
        warnings,
    )


def throat_path(a, b):
    """Horizontal lead + standard diagonal + horizontal lead."""
    if abs(a[1] - b[1]) < 0.01:
        return (a, b)
    dx = b[0] - a[0]
    lead = min(abs(dx) * 0.22, 28)
    sign = 1 if dx >= 0 else -1
    return (a, (a[0] + sign * lead, a[1]), (b[0] - sign * lead, b[1]), b)
