"""One collision registry for yards, physical platforms, faces, tracks and trunks."""

import math
import re
from statistics import median
import unicodedata

from shapely.geometry import LineString

from .helpers import platform_parts, mainline_label


def text_width(value, size):
    return max(
        max(
            sum(1 if unicodedata.east_asian_width(c) in "WF" else 0.62 for c in line)
            for line in str(value).splitlines()
        )
        * size,
        size,
    )


class LabelPlacer:
    def __init__(self, bounds):
        self.bounds, self.boxes = bounds, []

    def place(self, value, size, candidates, *, anchor="middle"):
        width = text_width(value, size) + 6
        left_bound, t, r, b = self.bounds
        for point in candidates:
            x, y = point
            x0 = (
                x - width
                if anchor == "end"
                else x
                if anchor == "start"
                else x - width / 2
            )
            rect = (
                x0,
                y - size - 3,
                x0 + width,
                y
                + size * 0.2
                + 3
                + max(0, len(str(value).splitlines()) - 1) * size * 1.4,
            )
            if rect[0] < left_bound or rect[1] < t or rect[2] > r or rect[3] > b:
                continue
            if any(
                rect[0] < q[2] and rect[2] > q[0] and rect[1] < q[3] and rect[3] > q[1]
                for q in self.boxes
            ):
                continue
            self.boxes.append(rect)
            return point, rect
        return None


def annotate(repo, context, layout, platform_groups, options):
    margin = 12
    placer = LabelPlacer(
        (
            margin,
            options.margin + options.title_size * 1.8,
            layout.width - margin,
            layout.height - 160,
        )
    )
    annotations = []
    missing = []

    def add(
        kind, ident, value, point, size, color="#25364a", candidates=(), anchor="middle"
    ):
        choices = [point, *candidates]
        choices += [
            (point[0] + dx, point[1] + dy)
            for dy in (-size * 1.3, size * 1.5, -size * 2.8, size * 3)
            for dx in (0, -size * 3, size * 3, -size * 6, size * 6)
        ]
        placed = placer.place(value, size, choices, anchor=anchor)
        if not placed:
            # A last resort uses free nearby drawing space and a short leader,
            # rather than silently omitting a track or overlapping labels.
            left_bound, t, r, b = placer.bounds
            grid = [
                (x, y)
                for y in range(math.ceil(t + size), int(b), max(18, int(size * 1.6)))
                for x in range(
                    math.ceil(left_bound + text_width(value, size) / 2),
                    int(r - text_width(value, size) / 2),
                    max(50, int(text_width(value, size) + 16)),
                )
            ]
            grid.sort(key=lambda p: math.dist(p, point))
            placed = placer.place(value, size, grid, anchor=anchor)
        if placed:
            position, rect = placed
            annotations.append(
                {
                    "kind": kind,
                    "id": ident,
                    "text": value,
                    "point": position,
                    "font_size": size,
                    "color": color,
                    "anchor": anchor,
                    "bounds": rect,
                    "leader": (point, position)
                    if math.dist(position, point) > size * 2
                    else None,
                }
            )
        else:
            missing.append(ident)

    # Number physical source objects in stable spatial order; this is a drawing
    # index, never a passenger boarding face or official track number.
    props = {
        ident: f.get("properties", {})
        for f in context
        for ident, _, _ in platform_parts([f])
    }
    ordered = sorted(
        layout.platforms,
        key=lambda p: (p.x if options.orientation == "portrait" else p.y, p.id),
    )
    unknown_faces = 0
    if options.show_platforms and options.show_platform_labels:
        for index, p in enumerate(ordered, 1):
            property_ = props.get(p.id, {})
            tags = property_.get("way_tags", property_)
            rule = options.platform_overrides.get(p.id, {})
            values = [
                v
                for v in re.split(
                    r"[;/,&\s]+", str(rule.get("faces") or tags.get("ref") or "")
                )
                if v
            ]
            physical = rule.get("physical_number") or property_.get(
                "physical_platform_number"
            )
            value = f"台体 {physical}" if physical else f"台体 {index}"
            point = (
                (p.x, p.y - p.length * 0.32 + 5)
                if options.orientation == "portrait"
                else (p.x - p.length * 0.32, p.y + 5)
            )
            add("physical-platform", p.id, value, point, options.label_size * 0.42)
            # Polygon shape cannot establish the number of boarding faces.
            count = max(1, len(values))
            unknown_faces += not bool(values)
            for n in range(count):
                face = "站台 " + values[n] if values else "面号待核对"
                # One row inside each body, with separate cells for body and faces.
                # Source ref order is not assumed to establish a physical side.
                offset = p.length * (
                    0.08 + 0.24 * n / max(count - 1, 1) if count > 1 else 0.32
                )
                point = (
                    (p.x, p.y + offset + 5)
                    if options.orientation == "portrait"
                    else (p.x + offset, p.y + 5)
                )
                add(
                    "platform-face",
                    p.id + ":" + str(n),
                    face,
                    point,
                    options.label_size * 0.4,
                )
    lane_order = sorted(
        layout.lanes, key=lambda lane: (lane.source_y, lane.id), reverse=True
    )
    track_by_id = {track.id: track for track in repo.station_tracks.values()}
    if options.show_track_labels:
        for index, lane in enumerate(lane_order, 1):
            if not lane.track_number and not options.track_overrides.get(
                lane.id, {}
            ).get("label"):
                continue
            track = track_by_id[lane.id]
            longest = max(
                (
                    LineString(part)
                    for k in lane.edge_ids
                    for part in layout.edges[k].parts
                ),
                key=lambda p: p.length,
            )
            points = [
                tuple(longest.interpolate(longest.length * f).coords[0])
                for f in (0.32, 0.68, 0.18, 0.82, 0.5, 0.08, 0.92)
            ]
            point = points[0]
            value = (str(lane.track_number) + "道") if lane.track_number else ""
            if options.track_overrides.get(lane.id, {}).get("label"):
                value = options.track_overrides[lane.id]["label"]
            size = options.label_size * 0.42
            candidates = [(p[0], p[1] - 5) for p in points]
            identities = [
                g for g in layout.groups.values() if lane.id in g["track_ids"]
            ]
            color = identities[0]["color"] if identities else "#85919b"
            add(
                "track",
                track.id,
                value,
                (point[0], point[1] - 5),
                size,
                color,
                candidates,
            )
    for group in sorted(layout.groups.values(), key=lambda g: g["id"]):
        if not group["caption"]:
            continue
        samples = [
            p
            for k in group["edge_ids"]
            if k in layout.edges
            for part in layout.edges[k].parts
            for p in part
        ]
        if not samples:
            continue
        core_samples = [
            p
            for p in samples
            if layout.core_bounds[0] <= p[0] <= layout.core_bounds[2]
            and layout.core_bounds[1] <= p[1] <= layout.core_bounds[3]
        ] or samples
        x, y = median(p[0] for p in core_samples), median(p[1] for p in core_samples)
        bodies = [
            p for p in layout.platforms if group["id"] in platform_groups.get(p.id, ())
        ]
        faces = {
            v
            for p in bodies
            for v in re.split(
                r"[;/,&\s]+",
                str(
                    options.platform_overrides.get(p.id, {}).get("faces")
                    or props.get(p.id, {})
                    .get("way_tags", props.get(p.id, {}))
                    .get("ref")
                    or ""
                ),
            )
            if v
        }
        track_count = sum(lane.id in group["track_ids"] for lane in layout.lanes)
        group["display_scale"] = {
            "physical_bodies": len(bodies),
            "known_faces": len(faces),
            "tracks": track_count,
            "source": "drawn_source_objects",
            "verification_status": "diagram_count_not_official_station_scale",
        }
        scale = f"图示 {len(bodies)} 台体 / {track_count} 股道"
        if faces:
            scale += f" / {len(faces)} 站台面"
        # Yard names have reserved candidates alongside their whole core group.
        add(
            "yard",
            group["id"],
            group["name"] + "\n" + scale,
            (layout.core_bounds[2] + 28, y),
            options.label_size * 0.6,
            group["color"],
            [(x, y - options.label_size * 2), (x, y + options.label_size * 2)],
        )
    # Only named main lines, once per business line. Connector destinations
    # are represented by their actual target trunks, not connector captions.
    seen = set()
    for port in sorted(layout.ports, key=lambda p: (p["line"].id, p["side"])):
        if (
            not options.show_endpoints
            or not port["visible"]
            or port["line"].name in seen
        ):
            continue
        seen.add(port["line"].name)
        px, py = port["point"]
        side = port["side"]
        point = (px + 14 if side == "left" else px - 14, py - options.label_size * 0.9)
        add(
            "main-line",
            port["line"].id,
            port["line"].name,
            point,
            options.label_size * 0.7,
            "#25364a",
            anchor="start" if side == "left" else "end",
        )
    if options.show_endpoints:
        for key, drawing in sorted(layout.edges.items()):
            line = repo.lines.get(layout.ownership[key].line_id)
            if (
                drawing.role != "main"
                or not mainline_label(line)
                or line.name in seen
                or options.line_overrides.get(line.id, {}).get("label") is False
            ):
                continue
            seen.add(line.name)
            add(
                "main-line",
                line.id,
                line.name,
                (layout.core_bounds[2] + 35, median(p[1] for p in drawing.points)),
                options.label_size * 0.7,
                anchor="start",
            )
    layout.annotations = annotations
    # Port directions will join the same registry in the renderer, when the
    # caller's sourced terminal names are available.
    layout.label_placer = placer
    if missing:
        raise ValueError(
            f"{len(missing)} 个标签无法无重叠放置，请增大输出尺寸或减小字号"
        )
    if any(not lane.track_number for lane in layout.lanes):
        layout.warnings.append(
            "缺少来源股道编号的轨道保留图形，不编造 T 序号；台体序号仅用于本图。"
        )
    if unknown_faces:
        layout.warnings.append(
            f"{unknown_faces} 个实体台体缺少来源站台面编号；未推定面数。可在“站台编号”中填写本图编号。"
        )
