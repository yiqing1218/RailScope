"""Per-yard relative geometry with a normalized, editable station composition."""

import math
from statistics import median

from shapely.geometry import LineString, MultiPoint, Point, box

from .helpers import (
    DiagramLayout,
    DiagramEdge,
    PlatformSymbol,
    edge_role,
    platform_parts,
    principal_axis,
    reliable_platform_axes,
    station_projection,
)
from .extractor import extract
from .topology import build_graph
from .station_extent import functional_interval
from .yard_groups import group_tracks
from .crossings import crossing_symbols
from .shape_paths import rounded_path
from .types import Lane
from .outlet_layout import arrange_outlets
from .label_layout import annotate
from .yard_selection import platform_membership, select_yards
from .yard_reference import yard_baseline, central_reference_paths
from .connection_layout import arrange_yard_connections
from .track_layout import straighten_platform_tracks
from .reference_layout import smooth_main_references

try:
    from ..station_diagram_layout import DiagramOptions
    from ..station_diagram_geometry import (
        common_baseline,
        smooth_baseline,
        baseline_value,
        densify,
        line_parts,
        reference_paths,
    )
except ImportError:
    from station_diagram_layout import DiagramOptions
    from station_diagram_geometry import (
        common_baseline,
        smooth_baseline,
        baseline_value,
        densify,
        line_parts,
        reference_paths,
    )


def build_layout(repo, context=(), options=None):
    options = options or DiagramOptions()
    selected = extract(repo, context, options)
    if not selected.selected:
        raise ValueError("当前内容设置未保留可导出的真实轨道")
    graph = build_graph(repo, selected.selected)
    local, *_ = station_projection(repo)
    platforms = list(platform_parts(context))
    tracks = {
        r.edge_id for t in repo.station_tracks.values() for r in t.edge_refs
    } & selected.selected
    reference_keys = tracks or selected.inner & selected.selected
    axes = reliable_platform_axes(
        [[local(p) for p in points] for _, _, points in platforms]
    )
    theta = principal_axis(
        axes
        or [
            [local(p) for p in repo.edges[k].coordinates]
            for k in sorted(reference_keys)
        ]
    )
    if not options.auto_rotate:
        theta = 0
    ca, sa = math.cos(theta), math.sin(theta)

    def rotate(p):
        x, y = local(p)
        return x * ca + y * sa, -x * sa + y * ca

    raw = {
        k: tuple(map(rotate, repo.edges[k].coordinates))
        for k in sorted(selected.selected)
    }
    groups, edge_groups, ownership, warnings = group_tracks(repo, raw, options)
    platform_groups = platform_membership(platforms, rotate, raw, edge_groups, tracks)
    if options.selected_yards:
        platform_axis = [
            rotate(p)[0] for _, _, points in platforms for p in points
        ] or [p[0] for k in tracks for p in raw[k]]
        retained = select_yards(
            repo,
            raw,
            groups,
            options.selected_yards,
            options,
            (min(platform_axis), max(platform_axis)),
        )
        raw = {k: p for k, p in raw.items() if k in retained}
        tracks &= retained
        reference_keys = tracks or selected.inner & retained
        groups = {g: v for g, v in groups.items() if g in options.selected_yards}
        edge_groups = {
            k: values & groups.keys()
            for k, values in edge_groups.items()
            if k in retained
        }
        platforms = [p for p in platforms if platform_groups[p[0]] & groups.keys()]
        graph = build_graph(repo, retained)
    body = [rotate(p) for _, _, points in platforms for p in points]
    core_lo, core_hi = (
        (min(p[0] for p in body), max(p[0] for p in body))
        if body
        else (
            min(p[0] for k in reference_keys for p in raw[k]),
            max(p[0] for k in reference_keys for p in raw[k]),
        )
    )
    if not body:
        # A display band near the source station point, not a claimed physical
        # platform/site boundary. Approach tracks must not become the core just
        # because the import lacks platform outlines.
        center = min(core_hi, max(core_lo, 0.0))
        core_lo, core_hi = max(core_lo, center - 250), min(core_hi, center + 250)
        if core_hi - core_lo < 80:
            core_lo, core_hi = center - 40, center + 40
    roles = {k: edge_role(repo, repo.edges[k], options) for k in raw}
    (lo, hi), line_intervals = functional_interval(
        repo, raw, graph, selected.inner & raw.keys(), body, roles, ownership
    )
    mid = (core_lo + core_hi) / 2
    baselines = {}
    main_references = []
    track_by_id = {track.id: track for track in repo.station_tracks.values()}
    for ident, group in groups.items():
        seed_keys = {
            r.edge_id for tid in group["track_ids"] for r in track_by_id[tid].edge_refs
        } & raw.keys()
        main_keys = {k for k in seed_keys if roles[k] == "main"}
        central_keys = {
            ref.edge_id
            for tid in group["track_ids"]
            if any(r.edge_id in main_keys for r in track_by_id[tid].edge_refs)
            for ref in track_by_id[tid].edge_refs
        } & raw.keys()
        # The yard's own central main-track pair establishes its horizontal
        # reference through both throats, independently of other yards.
        seed_paths = reference_paths(repo, central_keys or seed_keys, raw)
        paths = seed_paths
        if main_keys:
            paths, reference_keys_group = central_reference_paths(
                repo, raw, graph, seed_paths, central_keys, (core_lo, core_hi), (lo, hi)
            )
            group["reference_edges"] = sorted(reference_keys_group)
            main_references.append(paths)
            for k in reference_keys_group:
                if not edge_groups.get(k):
                    edge_groups[k] = {ident}
                    group["edge_ids"].add(k)
        bounds = (lo, hi) if main_keys else (core_lo, core_hi)
        paths = [
            tuple(
                p
                for p in densify(path, sorted(bounds))
                if bounds[0] <= p[0] <= bounds[1]
            )
            for path in paths
        ]
        paths = [p for p in paths if len(p) >= 2]
        baseline = (
            ()
            if ident == "unassigned"
            else (
                yard_baseline(paths, seed_paths, (core_lo, core_hi), bounds)
                if main_keys and options.remove_common_bend
                else smooth_baseline(common_baseline(paths, bounds))
                if options.remove_common_bend and paths
                else ()
            )
        )
        baselines[ident] = baseline

    def offset(p, identities):
        values = [
            baseline_value(baselines[g], p[0]) - baseline_value(baselines[g], mid)
            for g in identities
            if g in baselines
        ]
        return sum(values) / len(values) if values else 0

    def adjusted(p, identities):
        return p[0], p[1] - offset(p, identities)

    # Only actual shared nodes reconcile two group transforms. Independent
    # geometry crossings never become graph connections.
    node_source = {
        n: rotate((repo.nodes[n].lon, repo.nodes[n].lat)) for n in graph.adjacency
    }
    node_adjusted = {
        n: adjusted(
            p, set().union(*(edge_groups.get(k, set()) for k in graph.adjacency[n]))
        )
        for n, p in node_source.items()
    }
    drawing_raw, node_adjusted, platform_rails = straighten_platform_tracks(
        graph,
        {
            k: tuple(adjusted(p, edge_groups.get(k, ())) for p in points)
            for k, points in raw.items()
        },
        node_adjusted,
        tracks | {k for k in raw if roles[k] == "main"},
        (core_lo, core_hi),
    )
    core = [p for k in tracks for p in drawing_raw[k] if core_lo <= p[0] <= core_hi]
    core += [
        adjusted(rotate(p), platform_groups.get(ident, ()))
        for ident, _, points in platforms
        for p in points
    ]
    if not core:
        core = [p for k in selected.inner & raw.keys() for p in raw[k]]
    y0, y1 = min(p[1] for p in core), max(p[1] for p in core)
    width, height = options.canvas_size
    gutter = (
        max(160, options.label_size * 6) if options.show_endpoints else options.margin
    )
    left, right = options.margin + gutter, width - options.margin - gutter
    top = options.margin + (options.title_size * 1.8 if options.show_title else 10)
    bottom = height - options.margin - (120 if options.show_legend else 30)
    if right - left < 120 or bottom - top < 120:
        raise ValueError("画布过小，无法容纳站场与标注")
    portrait = options.orientation == "portrait"
    axis_room = bottom - top if portrait else right - left
    cross_room = right - left if portrait else bottom - top
    # Independent axis scales are intentional diagram composition. Each yard
    # retains its relative offsets; no equal-lane topology layout is used.
    sx = axis_room * 0.28 * 2.5 / max(core_hi - core_lo, 80)
    # Compact empty gaps between yards without equalizing actual track spacing.
    # This monotone mapping retains transverse order and each local bend.
    levels = []
    for track in repo.station_tracks.values():
        for ref in track.edge_refs:
            if ref.edge_id in raw:
                levels.extend(
                    adjusted(p, edge_groups.get(ref.edge_id, ()))[1]
                    for p in densify(raw[ref.edge_id], [mid])
                    if abs(p[0] - mid) < 1e-5
                )
    levels = sorted(set(round(value, 4) for value in levels))
    gaps = [b - a for a, b in zip(levels, levels[1:]) if b - a > 1]
    gap_limit = max(20, median(gaps) * 4) if gaps else 20
    cross_knots = []
    for value in levels:
        mapped = (
            value
            if not cross_knots
            else cross_knots[-1][1] + min(value - cross_knots[-1][0], gap_limit)
        )
        cross_knots.append((value, mapped))

    def compact_cross(value):
        if len(cross_knots) < 2:
            return value
        if value < cross_knots[0][0]:
            return cross_knots[0][1] + value - cross_knots[0][0]
        if value > cross_knots[-1][0]:
            return cross_knots[-1][1] + value - cross_knots[-1][0]
        return baseline_value(cross_knots, value)

    cy0, cy1 = compact_cross(y0), compact_cross(y1)
    sy = cross_room * 0.76 / max(cy1 - cy0, 20)
    if gaps:
        # A four-track selected yard must not acquire hundreds of pixels of
        # track/platform width just because it is stretched to fill the page.
        sy = min(sy, cross_room * 0.04 / median(gaps))
    cx, cy, my = (left + right) / 2, (top + bottom) / 2, (cy0 + cy1) / 2
    throat_span = max(250, (core_hi - core_lo) * 0.65)
    throat_room = axis_room * 0.29

    def axial(x):
        if x < lo:
            return axial(lo) + (x - lo) / options.outside_compression
        if x > hi:
            return axial(hi) + (x - hi) / options.outside_compression
        if x < core_lo:
            distance = core_lo - x
            return (
                core_lo - mid
            ) / options.station_compression - throat_room / sx * distance / (
                distance + throat_span
            )
        if x > core_hi:
            distance = x - core_hi
            return (
                core_hi - mid
            ) / options.station_compression + throat_room / sx * distance / (
                distance + throat_span
            )
        return (x - mid) / options.station_compression

    def page(p):
        x = axial(p[0]) * sx
        y = (compact_cross(p[1]) - my) * sy
        # Compress peripheral transverse excursions after the platform band.
        # This monotone transform keeps crossings/order while fitting real
        # distant junctions into the connection region instead of clipping them.
        limit = cross_room * 0.105
        if p[1] < y0:
            extra = (p[1] - y0) * sy
            y = (cy0 - my) * sy + extra / (1 + abs(extra) / limit)
        elif p[1] > y1:
            extra = (p[1] - y1) * sy
            y = (cy1 - my) * sy + extra / (1 + abs(extra) / limit)
        return (cx + y, cy + x) if portrait else (cx + x, cy - y)

    nodes = {n: page(p) for n, p in node_adjusted.items()}
    frame = box(left, top, right, bottom)
    edges = {}
    knots = sorted(
        {lo, hi, core_lo, core_hi} | {p[0] for b in baselines.values() for p in b}
    )
    for k, path in raw.items():
        screen = [page(p) for p in densify(drawing_raw[k], knots)]
        e = repo.edges[k]
        screen[0], screen[-1] = nodes[e.from_node_id], nodes[e.to_node_id]
        parts = tuple(line_parts(LineString(screen).intersection(frame)))
        zone = "external" if all(p[0] < lo or p[0] > hi for p in path) else "station"
        edges[k] = DiagramEdge(
            k,
            tuple(screen),
            roles[k],
            k not in selected.inner,
            zone,
            parts,
            " ".join(rounded_path(part) for part in parts),
        )
    symbols = []
    for ident, kind, points in platforms:
        source = [rotate(p) for p in points]
        identities = platform_groups[ident] & groups.keys()
        transformed = [page(adjusted(p, identities)) for p in source]
        rectangle = MultiPoint(transformed).minimum_rotated_rectangle
        corners = (
            list(rectangle.exterior.coords)
            if rectangle.geom_type == "Polygon"
            else list(rectangle.coords)
        )
        if len(corners) < 2:
            continue
        sides = [(math.dist(a, b), a, b) for a, b in zip(corners, corners[1:])]
        # The physical platform's long source axis remains the horizontal
        # symbol axis after deliberate anisotropic compression.
        center = rectangle.centroid
        xs, ys = [p[0] for p in transformed], [p[1] for p in transformed]
        length = max(ys) - min(ys) if portrait else max(xs) - min(xs)
        thickness = max(xs) - min(xs) if portrait else max(ys) - min(ys)
        if not sides or not frame.covers(center):
            continue
        symbols.append(
            PlatformSymbol(
                ident,
                kind,
                center.x,
                center.y,
                max(length, 1),
                max(thickness, 3) * options.platform_width,
                90 if portrait else 0,
            )
        )
    line_spacing = {}
    for group in groups.values():
        central_ids = [
            tid
            for tid in group["track_ids"]
            if any(
                ref.edge_id in tracks and roles[ref.edge_id] == "main"
                for ref in track_by_id[tid].edge_refs
            )
        ]
        levels_group = sorted(
            {
                round(
                    page(adjusted(p, edge_groups.get(k, ())))[0 if portrait else 1], 4
                )
                for tid in central_ids
                for ref in track_by_id[tid].edge_refs
                if (k := ref.edge_id) in raw
                for p in densify(raw[k], [mid])
                if abs(p[0] - mid) < 1e-5
            }
        )
        gaps_group = [
            b - a for a, b in zip(levels_group, levels_group[1:]) if b - a > 1
        ]
        if gaps_group:
            spacing = median(gaps_group)
            for k in group["edge_ids"] & raw.keys():
                if ownership[k].line_id:
                    line_spacing[ownership[k].line_id] = spacing
    ports, extensions = arrange_outlets(
        repo,
        raw,
        edges,
        nodes,
        graph,
        ownership,
        (lo, hi),
        page,
        adjusted,
        edge_groups,
        frame,
        theta,
        options,
        line_intervals,
        knots,
        (core_lo, core_hi, y0, y1),
        line_spacing,
        drawing_raw,
    )
    core_a, core_b = page((core_lo, y0)), page((core_hi, y1))
    core_bounds = (
        min(core_a[0], core_b[0]),
        min(core_a[1], core_b[1]),
        max(core_a[0], core_b[0]),
        max(core_a[1], core_b[1]),
    )
    smoothed_references = smooth_main_references(
        graph,
        raw,
        edges,
        nodes,
        main_references,
        (core_lo, core_hi),
        (lo, hi),
        portrait,
        core_bounds,
    )
    regular_connections = arrange_yard_connections(
        repo,
        graph,
        raw,
        edges,
        nodes,
        ownership,
        (core_lo, core_hi),
        frame,
        ports,
        extensions,
        platform_rails,
        core_bounds,
        portrait,
        smoothed_references,
    )
    # Prune only invisible drawing fragments; never alter the source repository.
    edges = {k: d for k, d in edges.items() if d.parts}
    for port in ports:
        port["edge_ids"].intersection_update(edges)
    ports = [p for p in ports if p["edge_ids"]]
    bounds = box(*frame.bounds).bounds
    center_a, center_b = page((core_lo, y0)), page((core_hi, y1))
    core_bounds = (
        min(center_a[0], center_b[0]),
        min(center_a[1], center_b[1]),
        max(center_a[0], center_b[0]),
        max(center_a[1], center_b[1]),
    )
    lanes = []
    for track in repo.station_tracks.values():
        keys = {r.edge_id for r in track.edge_refs} & edges.keys()
        if not keys:
            continue
        samples = [p for k in keys for part in edges[k].parts for p in part]
        sy_source = median(p[1] for k in keys for p in raw[k])
        label = (
            options.track_overrides.get(track.id, {}).get("label") or track.track_number
        )
        lanes.append(
            Lane(
                track.id,
                keys,
                sy_source,
                median(p[1] for p in samples),
                track.yard_id,
                ownership[min(keys)].yard_name,
                ownership[min(keys)].system,
                label,
            )
        )
    layout = DiagramLayout(
        width,
        height,
        theta - (math.pi / 2 if portrait else 0),
        "source_platform_axis" if axes else "station_tracks",
        edges,
        symbols,
        nodes,
        ports,
        core_bounds,
        bounds,
        lambda p: page(rotate(p)),
        selected.warnings + warnings,
        {k: ownership[k].system for k in edges},
        sx,
        (lo, hi),
        (
            lo - (hi - lo) * options.outside_compression,
            hi + (hi - lo) * options.outside_compression,
        ),
        (),
        lanes=lanes,
        ownership=ownership,
        boundary_source="last_mainline_interaction_display_interval",
        crossings=crossing_symbols(edges, graph, nodes),
        switch_nodes=[
            n
            for n, ks in graph.adjacency.items()
            if len(ks) >= 3 and frame.covers(Point(nodes[n]))
        ],
        algorithm="straight_platform_smooth_throats_v5",
        groups=groups,
        group_baselines=baselines,
        extensions=extensions,
    )
    layout.platform_rail_ids = sorted(platform_rails.keys() & edges.keys())
    layout.regular_connection_ids = sorted(set(regular_connections) & edges.keys())
    annotate(repo, context, layout, platform_groups, options)
    if options.selected_yards:
        layout.warnings.append(
            "仅显示所选分场的站台区域；相连外围轨道保留，其他分场的站台区连接在本图中截断。"
        )
    if not symbols:
        layout.warnings.append(
            "来源缺少当前范围的站台轮廓；本图仅显示已加载股道，不表示站台资料齐全。"
        )
    return layout
