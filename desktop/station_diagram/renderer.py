"""Draw only; topology, membership and placement are already resolved."""

from dataclasses import asdict
from html import escape
import json
import math
from .helpers import station_projection, port_destination
from .pipeline import build_layout
from .yard_classifier import CLASS_COLORS
from .yard_classifier import business_line


NEUTRAL = "#85919b"
PALETTE = (
    "#2463a3",
    "#b8453e",
    "#33846e",
    "#75609a",
    "#a87731",
    "#17878b",
    "#a13cad",
    "#465992",
)


def destination_warnings(repo, layout, info, options):
    local, *_ = station_projection(repo)
    missing = [
        p
        for p in layout.ports
        if p["visible"]
        and not options.port_overrides.get(p["key"], {}).get("text")
        and port_destination(p, info, local) is None
    ]
    return (
        [
            f"{len(missing)} 组端口缺少已确定的线路去向，标为“去向待核对”，可在边缘端口设置中填写。"
        ]
        if missing
        else []
    )


def edge_color(
    repo, edge, options, role=None, system=None, ownership=None, styles=None
):
    own = options.line_overrides.get(edge.infrastructure_line_id, {})
    if own.get("color"):
        return own["color"]
    line_id = ownership.line_id if ownership else edge.infrastructure_line_id
    if line_id in options.color_overrides:
        return options.color_overrides[line_id]
    if system in options.color_overrides:
        return options.color_overrides[system]
    if ownership and ownership.status == "unresolved":
        return NEUTRAL
    if options.color_scheme == "mono":
        return "#343d46"
    if not system:
        return NEUTRAL
    if ownership and ownership.yard_type == "intercity":
        return CLASS_COLORS["intercity"]
    # Inherit an explicitly assigned business line's diagram color, not the
    # geometrically nearest railway's color.
    assigned = options.line_overrides.get(line_id, {})
    if assigned.get("color"):
        return assigned["color"]
    line = repo.lines.get(line_id)
    cls = ownership.railway_class if ownership else edge.railway_class
    if styles and line:
        try:
            from ..rail_style_resolver import style_key
        except ImportError:
            from rail_style_resolver import style_key
        facts = {
            "railway_class": cls,
            "line_role": line.line_role,
            "track_role": "main_track",
            "design_speed_kmh": line.design_speed_kmh,
            "technical_attributes": line.provenance.get(
                "workspace_presentation", {}
            ).get("technical_attributes", {}),
        }
        color = styles.get(style_key(facts), {}).get("color")
        if color:
            return color
    if cls in CLASS_COLORS:
        return CLASS_COLORS[cls]
    names = sorted(
        {line.name for line in repo.lines.values() if business_line(line)} | {system}
    )
    return PALETTE[names.index(system) % len(PALETTE)]


def metadata(repo, layout, options, info):
    return {
        "schema": "railscope.station-diagram.v3",
        "station_id": next(iter(repo.stations)),
        "source": "shared_repository_topology",
        "attribution": "数据 © OpenStreetMap contributors",
        "verification_status": "automatic_reference_not_dispatch_verified",
        "confidence": None,
        "layout_algorithm": layout.algorithm,
        "drawing_groups": {
            k: {**g, "edge_ids": sorted(g["edge_ids"])}
            for k, g in layout.groups.items()
        },
        "group_baselines": layout.group_baselines,
        "annotations": layout.annotations,
        "schematic_extensions": layout.extensions,
        "boundary_source": layout.boundary_source,
        "axis_angle_degrees": math.degrees(layout.angle),
        "options": asdict(options),
        "warnings": layout.warnings,
        "selected_edge_ids": sorted(layout.edges),
        "nodes": layout.nodes,
        "edges": {
            key: {
                "from_node_id": repo.edges[key].from_node_id,
                "to_node_id": repo.edges[key].to_node_id,
                "source_id": repo.edges[key].source_id,
                "snapshot": repo.edges[key].snapshot_id,
                "verification_status": repo.edges[key].verification_status,
                "confidence": repo.edges[key].confidence,
                "role": d.role,
                "zone": d.zone,
                "ownership": asdict(layout.ownership[key]),
            }
            for key, d in layout.edges.items()
        },
        "lanes": [
            asdict(lane) | {"edge_ids": sorted(lane.edge_ids)} for lane in layout.lanes
        ],
        "ports": [
            {
                k: p[k]
                for k in (
                    "key",
                    "side",
                    "point",
                    "points",
                    "angle_degrees",
                    "vector",
                    "visible",
                    "direction_source",
                )
            }
            | {
                "line_id": p["line"].id,
                "edge_ids": sorted(p["edge_ids"]),
                "node_ids": p["nodes"],
            }
            for p in layout.ports
        ],
        "line_destinations": info.get("line_destinations", {}),
        "drawing_crossings": layout.crossings,
        "switch_nodes": layout.switch_nodes,
    }


def render_svg(
    repo, context=(), options=None, station_info=None, template="professional"
):
    if options is None:
        try:
            from ..station_diagram_layout import DiagramOptions
        except ImportError:
            from station_diagram_layout import DiagramOptions
        options = DiagramOptions()
    info = station_info or {}
    layout = build_layout(repo, context, options)
    layout.warnings.extend(destination_warnings(repo, layout, info, options))
    station = next(iter(repo.stations.values()))
    w, h = layout.width, layout.height
    styles = info.get("rail_styles")
    colors = {
        k: edge_color(
            repo,
            repo.edges[k],
            options,
            d.role,
            layout.systems[k],
            layout.ownership[k],
            styles,
        )
        for k, d in layout.edges.items()
    }
    if layout.groups:
        for group in layout.groups.values():
            for key in group["edge_ids"]:
                if key in layout.edges and layout.ownership[key].status != "unresolved":
                    colors[key] = (
                        "#343d46" if options.color_scheme == "mono" else group["color"]
                    )
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">',
        "<metadata>"
        + escape(json.dumps(metadata(repo, layout, options, info), ensure_ascii=False))
        + "</metadata>",
        "<style>text{font-family:Microsoft YaHei,Arial}</style>",
        '<rect width="100%" height="100%" fill="white"/>',
        f'<rect x="10" y="10" width="{w - 20}" height="{h - 20}" fill="none" stroke="#b9c3cd"/>',
    ]

    def text(
        value, point, size=None, color="#25364a", anchor="middle", attrs="", weight=400
    ):
        chunks = str(value).splitlines()
        if len(chunks) > 1:
            # Qt SVG ignores several tspan positioning attributes. Separate
            # text objects give SVG, PNG and PDF the same actual line breaks.
            out.append(f"<g {attrs}>")
            for i, chunk in enumerate(chunks):
                text(
                    chunk,
                    (point[0], point[1] + i * (size or options.label_size) * 1.4),
                    size,
                    color,
                    anchor,
                    weight=weight,
                )
            out.append("</g>")
            return
        position = (
            f'x="{point[0]:.2f}" y="{point[1]:.2f}" text-anchor="{anchor}" '
            f'font-size="{size or options.label_size:.2f}" font-weight="{weight}"'
        )
        caption = escape(str(value))
        # Qt SVG does not implement paint-order. Draw the halo first and the
        # solid glyphs separately so SVG, PNG and PDF share readable text.
        out.append(
            f'<g aria-hidden="true"><text {position} fill="white" stroke="white" '
            f'stroke-width="2">{caption}</text></g>'
        )
        out.append(
            f'<text {attrs} {position} fill="{color}" stroke="none">{caption}</text>'
        )

    if options.show_title:
        text(
            station.name + "平面布置示意图",
            (w / 2, options.margin + options.title_size),
            options.title_size,
            attrs='data-title="true"',
            weight=700,
        )
    if options.show_platforms:
        for p in layout.platforms:
            fill = {"gray": "#e4e8ec", "tint": "#e0ede5", "outline": "white"}[
                options.platform_fill
            ]
            out.append(
                f'<rect data-platform-id="{escape(p.id)}" data-source-geometry="{p.source_kind}" data-symbol="diagram-only" x="{p.x - p.length / 2:.2f}" y="{p.y - p.width / 2:.2f}" width="{p.length:.2f}" height="{p.width:.2f}" transform="rotate({p.angle:.2f} {p.x:.2f} {p.y:.2f})" fill="{fill}" stroke="#a5afb9" stroke-width="1"/>'
            )
    ranked = {"auxiliary": 0, "connector": 1, "station": 2, "main": 3}
    for key, d in sorted(
        layout.edges.items(), key=lambda item: (ranked[item[1].role], item[0])
    ):
        e = repo.edges[key]
        width = (
            options.line_overrides.get(e.infrastructure_line_id, {}).get("width")
            or options.main_width
        )
        dash = (
            ' stroke-dasharray="10 7"'
            if e.construction_status == "construction"
            else ""
        )
        out.append(
            f'<path data-edge-id="{escape(key)}" data-role="{d.role}" data-zone="{d.zone}" data-from-node="{escape(e.from_node_id)}" data-to-node="{escape(e.to_node_id)}" d="{d.path}" fill="none" stroke="{colors[key]}" stroke-width="{width:.2f}" stroke-linecap="round" stroke-linejoin="round"{dash}/>'
        )
    # Independent geometry crossings remain continuous solid railway strokes.
    # Connectivity is retained in metadata rather than implied by gap symbols.
    for extension in layout.extensions:
        key = extension["edge_id"]
        if key not in colors:
            continue
        a, b = extension["points"]
        out.append(
            f'<path data-schematic-extension="{escape(key)}" d="M {a[0]:.2f},{a[1]:.2f} L {b[0]:.2f},{b[1]:.2f}" fill="none" stroke="{colors[key]}" stroke-width="{options.main_width:.2f}"/>'
        )
    for label in layout.annotations:
        if label["leader"]:
            a, b = label["leader"]
            out.append(
                f'<path data-label-leader="{escape(label["id"])}" d="M {a[0]:.2f},{a[1]:.2f} L {b[0]:.2f},{b[1]:.2f}" fill="none" stroke="#a5afb9" stroke-width=".7"/>'
            )
        color = label["color"]
        if label["kind"] == "main-line":
            keys = [
                k for k in layout.edges if layout.ownership[k].line_id == label["id"]
            ]
            if keys:
                color = colors[min(keys)]
        text(
            label["text"],
            label["point"],
            label["font_size"],
            color,
            label["anchor"],
            attrs=f'data-label-kind="{label["kind"]}" data-label-id="{escape(label["id"])}"',
        )
    for lane in (
        layout.lanes if not layout.annotations and options.show_track_labels else ()
    ):
        if lane.track_number:
            e = min(lane.edge_ids)
            d = layout.edges[e]
            mid = lane.label_point or tuple(
                (d.points[0][i] + d.points[-1][i]) / 2 for i in (0, 1)
            )
            spacing = min(
                (
                    math.dist(other.label_point, mid)
                    for other in layout.lanes
                    if other is not lane and other.track_number and other.label_point
                ),
                default=options.label_size,
            )
            font_size = min(options.label_size * 0.65, max(7, spacing * 0.72))
            text(
                lane.track_number,
                (mid[0], mid[1] - 7),
                font_size,
                colors[e],
                attrs='data-track-number="true"',
            )
    for label in layout.yard_labels:
        text(
            label["text"],
            label["point"],
            options.label_size * 0.9,
            colors[label["edge_id"]],
            attrs=f'data-yard-id="{escape(label["yard_id"])}"',
            weight=700,
        )
    if options.show_endpoints:
        for label in layout.line_labels:
            text(
                label["text"],
                label["point"],
                label["font_size"],
                colors[label["edge_id"]],
                attrs=f'data-convergence-line="{escape(label["line_id"])}" data-convergence-node="{escape(label["convergence_node"])}"',
            )
        local, *_ = station_projection(repo)
        for p in layout.ports:
            if not p["visible"]:
                continue
            key = min(p["edge_ids"])
            color = colors[key]
            rule = options.port_overrides.get(p["key"], {})
            destination = port_destination(p, info, local)
            destination_text = "往" + destination if destination else "去向待核对"
            px, py = p["point"]
            side = p["side"]
            # Arrowheads are placed per existing track, never fake paired rails.
            for ax, ay in p["points"]:
                vx, vy = p["screen_vector"]
                extension = next(
                    (
                        v
                        for v in layout.extensions
                        if math.dist(v["points"][-1], (ax, ay)) < 0.1
                    ),
                    None,
                )
                if extension:
                    a, b = extension["points"]
                    vx, vy = b[0] - a[0], b[1] - a[1]
                length = math.hypot(vx, vy)
                vx, vy = vx / length, vy / length
                triangle = " ".join(
                    f"{x:.2f},{y:.2f}"
                    for x, y in (
                        (ax + vx * 10, ay + vy * 10),
                        (ax - vx * 5 - vy * 4, ay - vy * 5 + vx * 4),
                        (ax - vx * 5 + vy * 4, ay - vy * 5 - vx * 4),
                    )
                )
                out.append(
                    f'<polygon data-port-arrow="{escape(p["key"])}" points="{triangle}" fill="{color}"/>'
                )
            x = px - 16 if side == "left" else px + 16 if side == "right" else px
            y = py - 18 if side != "bottom" else py + 32
            x, y = p.get("label_point", (x, y))
            anchor = (
                "end" if side == "left" else "start" if side == "right" else "middle"
            )
            x += float(rule.get("dx", 0))
            y += float(rule.get("dy", 0))
            vx, vy = p["screen_vector"]
            arrow = ("→", "↘", "↓", "↙", "←", "↖", "↑", "↗")[
                round(math.atan2(vy, vx) / (math.pi / 4)) % 8
            ]
            value = rule.get("text") or (
                arrow + " " + destination_text
                if side in ("left", "top")
                else destination_text + " " + arrow
            )
            if hasattr(layout, "label_placer"):
                candidates = (
                    [(x, y)]
                    if any(k in rule for k in ("dx", "dy"))
                    else [
                        (x, y + i * options.label_size * 1.4)
                        for i in (0, 1, -1, 2, -2, 3, -3, 4, -4)
                    ]
                )
                placed = layout.label_placer.place(
                    value, options.label_size * 0.8, candidates, anchor=anchor
                )
                if not placed and not any(k in rule for k in ("dx", "dy")):
                    # Dense real-data exits can meet at a corner. Search the
                    # adjacent border band before failing an automatic export.
                    left, top, right, bottom = layout.label_placer.bounds
                    if side in ("left", "right"):
                        band = [
                            (x + dx, yy)
                            for yy in range(math.ceil(top + 30), int(bottom), 30)
                            for dx in (0, -90 if side == "right" else 90)
                        ]
                    else:
                        band = [
                            (xx, y + dy)
                            for xx in range(int(left + 180), int(right - 180), 60)
                            for dy in (0, 40, -40, 80, -80)
                        ]
                    band.sort(key=lambda q: math.dist(q, (x, y)))
                    placed = layout.label_placer.place(
                        value, options.label_size * 0.8, band, anchor=anchor
                    )
                    if placed:
                        a = (px, py)
                        z = placed[0]
                        out.append(
                            f'<path data-label-leader="{escape(p["key"])}" d="M {a[0]:.2f},{a[1]:.2f} L {z[0]:.2f},{z[1]:.2f}" fill="none" stroke="#a5afb9" stroke-width=".7"/>'
                        )
                if placed:
                    (x, y), rect = placed
                    layout.annotations.append(
                        {
                            "kind": "direction",
                            "id": p["key"],
                            "text": value,
                            "point": (x, y),
                            "bounds": rect,
                            "font_size": options.label_size * 0.8,
                            "color": color,
                        }
                    )
                else:
                    raise ValueError(
                        "端口标签重叠或超出画布，请调整文字偏移、输出尺寸或字号"
                    )
            out.append(
                f'<g data-line-id="{escape(p["line"].id)}" data-end="{side}" data-port-x="{px:.2f}" data-port-y="{py:.2f}" data-track-count="{len(p["points"])}" data-port-edges="{escape(" ".join(sorted(p["edge_ids"])))}" x="{x:.2f}" y="{y:.2f}" style="fill:{color}">'
            )
            for i, chunk in enumerate(value.splitlines()):
                text(
                    chunk,
                    (x, y + i * options.label_size * 1.1),
                    options.label_size * 0.8,
                    color,
                    anchor,
                    weight=600,
                )
            out.append("</g>")
    if options.show_north:
        degrees = math.degrees(layout.angle)
        nx, ny = w - options.margin - 55, options.margin + 70
        out.append(
            f'<g data-north-angle="{degrees:.4f}" transform="translate({nx:.2f} {ny:.2f}) rotate({degrees:.4f})"><line x1="0" y1="25" x2="0" y2="-20" stroke="#25364a"/><polygon points="0,-32 -7,-14 7,-14" fill="#25364a"/></g>'
        )
        text(
            "N",
            (nx + math.sin(layout.angle) * 46, ny - math.cos(layout.angle) * 46),
            options.label_size * 0.8,
        )
    if options.show_legend:
        entries = sorted(
            {
                (g["name"], g["color"] if options.color_scheme != "mono" else "#343d46")
                for g in layout.groups.values()
                if g["legend"]
            }
            if layout.groups
            else {
                (layout.systems[k], colors[k])
                for k in layout.edges
                if layout.systems[k]
            }
        )
        if any(c == NEUTRAL for c in colors.values()):
            entries.append(("归属待核对", NEUTRAL))
        x, y = options.margin, h - options.margin - 35
        out.append('<g data-legend="true">')
        fs = options.label_size * 0.65
        for name, color in entries:
            needed = len(name) * fs + 80
            if x + needed > w - options.margin:
                x = options.margin
                y += fs * 1.5
            if y > h - 22:
                raise ValueError("图例过密，请增大画布或减少外围层数")
            out.append(
                f'<line x1="{x}" y1="{y}" x2="{x + 40}" y2="{y}" stroke="{color}" stroke-width="{options.main_width}"/>'
            )
            text(name, (x + 50, y + fs * 0.3), fs, color, "start")
            x += needed
        out.append("</g>")
    footer = (
        "站场示意 · 台体序号仅用于本图 · 股道与乘降面使用来源编号 · 自动参考"
        if layout.groups
        else "站场示意 · 保留站内相对形状，距离可压缩 · 自动参考"
    )
    text(footer, (w / 2, h - 14), 14, "#768390")
    out[1] = (
        "<metadata>"
        + escape(json.dumps(metadata(repo, layout, options, info), ensure_ascii=False))
        + "</metadata>"
    )
    out.append("</svg>")
    return "\n".join(out)
