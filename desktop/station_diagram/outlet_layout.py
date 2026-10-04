"""First-order exterior trends, separated trunk bundles and schematic extensions."""

from collections import defaultdict
from dataclasses import replace
import math
from statistics import median

from shapely.geometry import LineString, Point, box

from .helpers import mainline_label
from .shape_paths import rounded_path

try:
    from ..station_diagram_geometry import densify, line_parts
except ImportError:
    from station_diagram_geometry import densify, line_parts


def linear_direction(paths, sign, radius=None, origin=(0, 0)):
    """Exact arc-length moments: neither vertex density nor edge splits bias PCA."""
    if radius:
        circle = Point(origin).buffer(radius)
        nearby = [
            part
            for path in paths
            for part in line_parts(LineString(path).intersection(circle))
        ]
        if nearby:
            paths = nearby
    weight, mx, my, exx, eyy, exy = 0, 0, 0, 0, 0, 0
    for path in paths:
        for a, b in zip(path, path[1:]):
            length = math.dist(a, b)
            ax, ay = a
            bx, by = b
            weight += length
            mx += length * (ax + bx) / 2
            my += length * (ay + by) / 2
            exx += length * (ax * ax + ax * bx + bx * bx) / 3
            eyy += length * (ay * ay + ay * by + by * by) / 3
            exy += length * (2 * ax * ay + ax * by + bx * ay + 2 * bx * by) / 6
    if weight <= 1e-8:
        raise ValueError("正线方向参考几何长度为零，需核对源数据")
    mx, my = mx / weight, my / weight
    xx, yy, xy = exx / weight - mx * mx, eyy / weight - my * my, exy / weight - mx * my
    angle = 0.5 * math.atan2(2 * xy, xx - yy)
    dx, dy = math.cos(angle), math.sin(angle)
    if dx * sign < 0:
        dx, dy = -dx, -dy
    return dx, dy


def components(repo, keys):
    adjacent = defaultdict(set)
    for k in keys:
        e = repo.edges[k]
        adjacent[e.from_node_id].add(k)
        adjacent[e.to_node_id].add(k)
    remaining = set(keys)
    while remaining:
        todo, result = [min(remaining)], set()
        while todo:
            k = todo.pop()
            if k not in remaining:
                continue
            remaining.remove(k)
            result.add(k)
            e = repo.edges[k]
            todo.extend((adjacent[e.from_node_id] | adjacent[e.to_node_id]) & remaining)
        yield result


def ray_endpoint(origin, vector, bounds):
    left, top, right, bottom = bounds
    vx, vy = vector
    choices = [
        ((bound - start) / v, side)
        for bound, start, v, side in (
            (left, origin[0], vx, "left"),
            (right, origin[0], vx, "right"),
            (top, origin[1], vy, "top"),
            (bottom, origin[1], vy, "bottom"),
        )
        if abs(v) > 1e-8 and (bound - start) / v > 0
    ]
    distance, side = min(choices)
    point = list((origin[0] + distance * vx, origin[1] + distance * vy))
    point[0 if side in ("left", "right") else 1] = {
        "left": left,
        "right": right,
        "top": top,
        "bottom": bottom,
    }[side]
    return tuple(point), side


def arrange_outlets(
    repo,
    raw,
    edges,
    nodes,
    graph,
    ownership,
    interval,
    page,
    adjusted,
    edge_groups,
    frame,
    theta,
    options,
    line_intervals,
    knots,
    source_core,
    line_spacing=None,
):
    lo, hi = interval
    grouped = defaultdict(dict)
    for k, d in edges.items():
        line_id = ownership[k].line_id
        line = repo.lines.get(line_id)
        if (
            d.role != "main"
            or not line
            or not mainline_label(line)
            or line.line_role == "connecting_line"
        ):
            continue
        line_lo, line_hi = line_intervals.get(line_id, interval)
        for sign, cut in ((-1, line_lo), (1, line_hi)):
            region = (
                box(-1e12, -1e12, cut, 1e12)
                if sign < 0
                else box(cut, -1e12, 1e12, 1e12)
            )
            parts = tuple(line_parts(LineString(raw[k]).intersection(region)))
            if parts:
                grouped[line_id, sign][k] = parts
    ports = []
    left_bound, t, r, b = frame.bounds
    for (line_id, sign), paths in sorted(grouped.items()):
        flat = [part for pieces in paths.values() for part in pieces]
        line_lo, line_hi = line_intervals.get(line_id, interval)
        cut = line_lo if sign < 0 else line_hi
        anchors = [min(part, key=lambda p: abs(p[0] - cut)) for part in flat]
        source_anchor = (cut, median(p[1] for p in anchors))
        first_key = min(paths)
        start = page(adjusted(source_anchor, edge_groups.get(first_key, ())))
        direction = linear_direction(
            flat, sign, options.direction_radius_m, source_anchor
        )
        if options.orientation == "portrait":
            vx, vy = direction[1], direction[0]
        else:
            vx, vy = direction[0], -direction[1]
        candidates = []
        for bound, origin, v, side in (
            (left_bound, start[0], vx, "left"),
            (r, start[0], vx, "right"),
            (t, start[1], vy, "top"),
            (b, start[1], vy, "bottom"),
        ):
            distance = (bound - origin) / v if abs(v) > 1e-8 else -1
            if distance > 0:
                candidates.append((distance, side))
        distance, side = min(candidates)
        endpoint = (start[0] + distance * vx, start[1] + distance * vy)
        endpoint = tuple(
            min(upper, max(lower, v))
            for v, lower, upper in zip(endpoint, (left_bound, t), (r, b))
        )
        ca, sa = math.cos(theta), math.sin(theta)
        vector = (
            direction[0] * ca - direction[1] * sa,
            direction[0] * sa + direction[1] * ca,
        )
        key = line_id + ":" + side
        if any(p["key"] == key for p in ports):
            key += ":positive" if sign > 0 else ":negative"
        ports.append(
            {
                "key": key,
                "line": repo.lines[line_id],
                "side": side,
                "point": endpoint,
                "points": [],
                "original_points": [],
                "edge_ids": set(paths),
                "nodes": [],
                "vector": vector,
                "screen_vector": (vx, vy),
                "direction_source": "length_uniform_linear_fit",
                "angle_degrees": math.degrees(math.atan2(vector[1], vector[0])) % 360,
                "visible": options.port_overrides.get(key, {}).get(
                    "visible",
                    options.line_overrides.get(line_id, {}).get("label") is not False,
                ),
                "extended": options.port_overrides.get(key, {}).get(
                    "extend", options.align_main_outlets
                ),
                "start": start,
                "source_anchor": source_anchor,
                "paths": paths,
                "source_direction": direction,
            }
        )
        ports[-1]["cut"] = cut
        ports[-1]["sign"] = sign
    # Reserve distinct border positions. Keep the post-interaction trunk order
    # on a common border, so the simplification itself adds no bundle crossing.
    for side in ("left", "right", "top", "bottom"):
        values = sorted(
            (p for p in ports if p["side"] == side),
            key=lambda p: p["start"][1 if side in ("left", "right") else 0],
        )
        index = 1 if side in ("left", "right") else 0
        lower, upper = (t + 30, b - 30) if index == 1 else (left_bound + 30, r - 30)
        gap = min(options.label_size * 2.3, (upper - lower) / max(len(values), 1))
        positions = [min(upper, max(lower, p["point"][index])) for p in values]
        for i in range(1, len(positions)):
            positions[i] = max(positions[i], positions[i - 1] + gap)
        if positions and positions[-1] > upper:
            positions[-1] = upper
            for i in range(len(positions) - 2, -1, -1):
                positions[i] = min(positions[i], positions[i + 1] - gap)
        for p, value in zip(values, positions):
            endpoint = list(p["point"])
            endpoint[index] = value
            p["point"] = tuple(endpoint)
    extensions = []
    mapped = {}
    for port in ports:
        direction, source_anchor = port["source_direction"], port["source_anchor"]
        start, end = port["start"], port["point"]
        bundles = list(components(repo, port["paths"]))
        border_index = 1 if port["side"] in ("left", "right") else 0

        def bundle_anchor(keys):
            points = [p for k in keys for part in port["paths"][k] for p in part]
            anchor = min(points, key=lambda p: abs(p[0] - source_anchor[0]))
            return anchor, page(adjusted(anchor, edge_groups.get(min(keys), ())))

        bundles.sort(key=lambda ks: bundle_anchor(ks)[1][border_index])
        near_center = sum(bundle_anchor(ks)[1][border_index] for ks in bundles) / len(
            bundles
        )
        for number, keys in enumerate(bundles):
            source_points = [p for k in keys for part in port["paths"][k] for p in part]
            anchor, near = bundle_anchor(keys)
            # Preserve physical screen order and the yard's drawn pair spacing.
            # Source north-up y is reversed on the page; sorting raw y inverted
            # every outbound pair and produced artificial scissors crossings.
            spacing = (line_spacing or {}).get(port["line"].id)
            if (
                not spacing
                and len(bundles) > 1
                and all(
                    abs(bundle_anchor(ks)[1][border_index] - near_center) < 1
                    for ks in bundles
                )
            ):
                spacing = median((line_spacing or {}).values()) if line_spacing else 16
            final_offset = (
                (number - (len(bundles) - 1) / 2) * spacing
                if spacing
                else near[border_index] - near_center
            )
            qmax = max(
                sum((p[i] - anchor[i]) * direction[i] for i in (0, 1))
                for p in source_points
            )
            qmax = max(qmax, 1)
            fraction = min(0.85, max(0.3, 6.24 / options.outside_compression))
            target = tuple(start[i] + (end[i] - start[i]) * fraction for i in (0, 1))
            border_index = 1 if port["side"] in ("left", "right") else 0
            far = list(target)
            far[border_index] += final_offset
            actual_end = tuple(far)

            def transform(
                p,
                anchor=anchor,
                direction=direction,
                qmax=qmax,
                near=near,
                actual_end=actual_end,
            ):
                q = min(
                    1,
                    max(
                        0, sum((p[i] - anchor[i]) * direction[i] for i in (0, 1)) / qmax
                    ),
                )
                return tuple(near[i] + q * (actual_end[i] - near[i]) for i in (0, 1))

            for k in keys:
                mapped[k, port["sign"]] = (port["cut"], transform)
                e = repo.edges[k]
                for node, index in ((e.from_node_id, 0), (e.to_node_id, -1)):
                    if (raw[k][index][0] - port["cut"]) * port["sign"] > 0:
                        nodes[node] = transform(raw[k][index])
            last = max(
                (
                    (sum((p[i] - anchor[i]) * direction[i] for i in (0, 1)), k, p)
                    for k in keys
                    for p in (raw[k][0], raw[k][-1])
                    if (p[0] - port["cut"]) * port["sign"] > 0
                ),
                default=None,
            )
            if not last:
                continue
            _, last_key, p = last
            border = list(end)
            border[border_index] += final_offset
            actual = transform(p)
            port["original_points"].append(actual)
            port["points"].append(tuple(border) if port["extended"] else actual)
            e = repo.edges[last_key]
            port["nodes"].extend(
                n
                for n in (e.from_node_id, e.to_node_id)
                if len(graph.adjacency[n]) == 1
            )
            if port["extended"]:
                extensions.append(
                    {"edge_id": last_key, "points": (actual, tuple(border))}
                )
        if port["points"]:
            port["point"] = tuple(
                sum(p[i] for p in port["points"]) / len(port["points"]) for i in (0, 1)
            )
            if port["extended"]:
                point = list(port["point"])
                point[0 if port["side"] in ("left", "right") else 1] = {
                    "left": left_bound,
                    "right": r,
                    "top": t,
                    "bottom": b,
                }[port["side"]]
                port["point"] = tuple(point)
        for k in ("paths", "source_direction", "source_anchor", "start"):
            port.pop(k, None)
    for k, edge in list(edges.items()):
        pts = []
        cuts = [v[0] for (key, _), v in mapped.items() if key == k]
        for p in densify(raw[k], sorted(set(knots) | set(cuts))):
            transform = next(
                (
                    value[1]
                    for sign in (-1, 1)
                    if (value := mapped.get((k, sign))) and (p[0] - value[0]) * sign > 0
                ),
                None,
            )
            pts.append(
                transform(p) if transform else page(adjusted(p, edge_groups.get(k, ())))
            )
        source = repo.edges[k]
        pts[0], pts[-1] = nodes[source.from_node_id], nodes[source.to_node_id]
        parts = tuple(line_parts(LineString(pts).intersection(frame)))
        edges[k] = replace(
            edge,
            points=tuple(pts),
            parts=parts,
            path=" ".join(rounded_path(p) for p in parts),
        )
    # A north/south branch can leave the station without crossing an axial
    # left/right cut. Its last real decision node is the anchor instead.
    claimed = set().union(*(p["edge_ids"] for p in ports)) if ports else set()
    station_keys = {
        r.edge_id for track in repo.station_tracks.values() for r in track.edge_refs
    }
    full_degree = defaultdict(int)
    for e in repo.edges.values():
        for n in (e.from_node_id, e.to_node_id):
            full_degree[n] += 1
    x0, x1, y0, y1 = source_core
    for k, d in list(edges.items()):
        e = repo.edges[k]
        line = repo.lines.get(ownership[k].line_id)
        if (
            k in claimed
            or k in station_keys
            or d.role != "main"
            or not line
            or not mainline_label(line)
            or line.line_role == "connecting_line"
        ):
            continue
        for node, index in ((e.from_node_id, 0), (e.to_node_id, -1)):
            p = raw[k][index]
            other = raw[k][-1 if index == 0 else 0]
            if (
                len(graph.adjacency[node]) != 1
                or (x0 <= p[0] <= x1 and y0 <= p[1] <= y1)
                or (full_degree[node] > 1 and math.hypot(*p) <= math.hypot(*other))
            ):
                # An inward-facing cut caused by selection/depth is not an
                # outgoing railway. Never extrapolate it across the whole yard.
                continue
            anchor = d.points[-1 if index == 0 else 0]
            if not frame.contains(Point(anchor)):
                continue
            source = raw[k][::-1] if index == 0 else raw[k]
            dx, dy = linear_direction([source], 1)
            if (
                dx * (source[-1][0] - source[0][0])
                + dy * (source[-1][1] - source[0][1])
                < 0
            ):
                dx, dy = -dx, -dy
            vx, vy = (dy, dx) if options.orientation == "portrait" else (dx, -dy)
            border, side = ray_endpoint(anchor, (vx, vy), frame.bounds)
            free_anchor = anchor
            prefix = [anchor]
            a, z = page((x0, y0)), page((x1, y1))
            core = box(
                min(a[0], z[0]), min(a[1], z[1]), max(a[0], z[0]), max(a[1], z[1])
            )
            # A terminal spur whose anchor is already outside the platforms
            # must not acquire a shortcut through them when straightened.
            # Keep its attachment, use a rounded outer connection, then fit
            # only the free segment to the first-order direction.
            if (
                not core.contains(Point(anchor))
                and LineString((anchor, border)).intersection(core).length > 1
            ):
                cl, ct, cr, cb = core.bounds
                if abs(vx) >= abs(vy):
                    route_y = max(t + 10, ct - 30) if vy <= 0 else min(b - 10, cb + 30)
                    free_anchor = (
                        max(left_bound + 10, cl - 40)
                        if vx < 0
                        else min(r - 10, cr + 40),
                        route_y,
                    )
                    prefix.extend(((anchor[0], route_y), free_anchor))
                else:
                    route_x = (
                        max(left_bound + 10, cl - 30)
                        if vx <= 0
                        else min(r - 10, cr + 30)
                    )
                    free_anchor = (
                        route_x,
                        max(t + 10, ct - 40) if vy < 0 else min(b - 10, cb + 40),
                    )
                    prefix.extend(((route_x, anchor[1]), free_anchor))
                border, side = ray_endpoint(free_anchor, (vx, vy), frame.bounds)
            fraction = min(0.85, max(0.3, 6.24 / options.outside_compression))
            end = tuple(
                free_anchor[i] + (border[i] - free_anchor[i]) * fraction for i in (0, 1)
            )
            nodes[node] = end
            oriented = prefix + [end]
            points = tuple(oriented[::-1] if index == 0 else oriented)
            edges[k] = replace(
                d, points=points, parts=(points,), path=rounded_path(points)
            )
            ca, sa = math.cos(theta), math.sin(theta)
            vector = (dx * ca - dy * sa, dx * sa + dy * ca)
            key = line.id + ":" + side
            extended = options.port_overrides.get(key, {}).get(
                "extend", options.align_main_outlets
            )
            matching_port = next(
                (
                    v
                    for v in ports
                    if v["line"].id == line.id
                    and v["side"] == side
                    and sum(a * b for a, b in zip(v["vector"], vector)) > 0.9
                ),
                None,
            )
            if not matching_port and any(v["key"] == key for v in ports):
                key += ":" + node
            if matching_port:
                matching_port["edge_ids"].add(k)
                matching_port["points"].append(border if extended else end)
                matching_port["nodes"].append(node)
            else:
                ports.append(
                    {
                        "key": key,
                        "line": line,
                        "side": side,
                        "point": border if extended else end,
                        "points": [border if extended else end],
                        "original_points": (end,),
                        "edge_ids": {k},
                        "nodes": [node],
                        "vector": vector,
                        "screen_vector": (vx, vy),
                        "angle_degrees": math.degrees(math.atan2(vector[1], vector[0]))
                        % 360,
                        "direction_source": "length_uniform_linear_fit",
                        "visible": options.port_overrides.get(key, {}).get(
                            "visible",
                            options.line_overrides.get(line.id, {}).get("label")
                            is not False,
                        ),
                        "extended": extended,
                    }
                )
            if extended:
                extensions.append({"edge_id": k, "points": (end, border)})
    # The terminal fallback and axial cut may discover the two members of a
    # physical pair in different passes. Reconcile their final free segments
    # together, preserving order and the central main-track spacing.
    for port in ports:
        paired = [
            v
            for v in extensions
            if v["edge_id"] in port["edge_ids"]
            and any(math.dist(v["points"][-1], p) < 0.01 for p in port["points"])
        ]
        if len(paired) < 2:
            continue
        index = 1 if port["side"] in ("left", "right") else 0
        paired.sort(key=lambda v: (v["points"][0][index], v["edge_id"]))
        spacing = (line_spacing or {}).get(port["line"].id)
        if not spacing:
            values = sorted(v["points"][0][index] for v in paired)
            gaps = [b - a for a, b in zip(values, values[1:]) if b - a > 1]
            spacing = median(gaps) if gaps else 16
        center = tuple(
            sum(v["points"][-1][i] for v in paired) / len(paired) for i in (0, 1)
        )
        actual_center = tuple(
            sum(v["points"][0][i] for v in paired) / len(paired) for i in (0, 1)
        )
        points, originals = [], []
        for number, ext in enumerate(paired):
            shift = (number - (len(paired) - 1) / 2) * spacing
            border, actual = list(center), list(actual_center)
            border[index] += shift
            actual[index] += shift
            k = ext["edge_id"]
            e = repo.edges[k]
            drawing = edges[k]
            old = ext["points"][0]
            start = math.dist(drawing.points[0], old) < math.dist(
                drawing.points[-1], old
            )
            node = e.from_node_id if start else e.to_node_id
            nodes[node] = tuple(actual)
            pts = (
                (tuple(actual), drawing.points[-1])
                if start
                else (drawing.points[0], tuple(actual))
            )
            parts = tuple(line_parts(LineString(pts).intersection(frame)))
            edges[k] = replace(
                drawing,
                points=pts,
                parts=parts,
                path=" ".join(rounded_path(p) for p in parts),
            )
            ext["points"] = (tuple(actual), tuple(border))
            points.append(tuple(border))
            originals.append(tuple(actual))
        port["point"] = center
        port["points"] = points
        port["original_points"] = originals
    # Reconcile shared endpoints after terminal positions have been arranged.
    for k, d in list(edges.items()):
        e = repo.edges[k]
        pts = list(d.points)
        pts[0] = nodes[e.from_node_id]
        pts[-1] = nodes[e.to_node_id]
        parts = tuple(line_parts(LineString(pts).intersection(frame)))
        edges[k] = replace(
            d,
            points=tuple(pts),
            parts=parts,
            path=" ".join(rounded_path(p) for p in parts),
        )
    # Returned separately: schematic extensions are not NetworkEdges.
    return ports, extensions
