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
    names = sorted({l.name for l in repo.lines.values() if business_line(l)} | {system})
    return PALETTE[names.index(system) % len(PALETTE)]


def metadata(repo, layout, options, info):
    return {
        "schema": "railscope.station-diagram.v3",
        "station_id": next(iter(repo.stations)),
        "source": "shared_repository_topology",
        "attribution": "数据 © OpenStreetMap contributors",
        "verification_status": "automatic_reference_not_dispatch_verified",
        "confidence": None,
        "layout_algorithm": "source_shape_shared_transform_v2",
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
        "lanes": [asdict(l) | {"edge_ids": sorted(l.edge_ids)} for l in layout.lanes],
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
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">',
        "<metadata>"
        + escape(json.dumps(metadata(repo, layout, options, info), ensure_ascii=False))
        + "</metadata>",
        "<style>text{font-family:Microsoft YaHei,Arial;fill:#25364a}</style>",
        '<rect width="100%" height="100%" fill="white"/>',
        f'<rect x="10" y="10" width="{w - 20}" height="{h - 20}" fill="none" stroke="#b9c3cd"/>',
    ]

    def text(
        value, point, size=None, color="#25364a", anchor="middle", attrs="", weight=400
    ):
        out.append(
            f'<text {attrs} x="{point[0]:.2f}" y="{point[1]:.2f}" text-anchor="{anchor}" font-size="{size or options.label_size:.2f}" font-weight="{weight}" style="fill:{color};stroke:white;stroke-width:2;paint-order:stroke fill">{escape(str(value))}</text>'
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
    for mark in layout.crossings:
        key = mark["upper_edge"]
        x, y = mark["point"]
        dx, dy = mark["tangent"]
        a = (x - dx * 7, y - dy * 7)
        b = (x + dx * 7, y + dy * 7)
        width = (
            options.line_overrides.get(repo.edges[key].infrastructure_line_id, {}).get(
                "width"
            )
            or options.main_width
        )
        out.append(
            f'<g data-crossing="unconnected" data-upper-edge="{escape(key)}" data-lower-edge="{escape(mark["lower_edge"])}"><path d="M {a[0]:.2f},{a[1]:.2f} L {b[0]:.2f},{b[1]:.2f}" fill="none" stroke="white" stroke-width="{width + 5:.2f}"/><path d="M {a[0]:.2f},{a[1]:.2f} L {b[0]:.2f},{b[1]:.2f}" fill="none" stroke="{colors[key]}" stroke-width="{width:.2f}"/></g>'
        )
    # Topology supplied these real degree-three-or-more nodes before rendering.
    for node in layout.switch_nodes:
        x, y = layout.nodes[node]
        out.append(
            f'<line data-switch-node="{escape(node)}" x1="{x - 3:.2f}" y1="{y - 6:.2f}" x2="{x + 3:.2f}" y2="{y + 6:.2f}" stroke="#25364a" stroke-width="1.2"/>'
        )
    for lane in layout.lanes:
        if lane.track_number:
            e = min(lane.edge_ids)
            d = layout.edges[e]
            mid = lane.label_point or tuple((d.points[0][i] + d.points[-1][i]) / 2 for i in (0, 1))
            spacing = min((math.dist(other.label_point,mid) for other in layout.lanes
                           if other is not lane and other.track_number and other.label_point),default=options.label_size)
            font_size = min(options.label_size*.65,max(7,spacing*.72))
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
                if side in ("left", "right"):
                    sign = -1 if side == "left" else 1
                    triangle = f"{ax + sign * 10:.2f},{ay:.2f} {ax - sign * 5:.2f},{ay - 4:.2f} {ax - sign * 5:.2f},{ay + 4:.2f}"
                else:
                    sign = -1 if side == "top" else 1
                    triangle = f"{ax:.2f},{ay + sign * 10:.2f} {ax - 4:.2f},{ay - sign * 5:.2f} {ax + 4:.2f},{ay - sign * 5:.2f}"
                out.append(
                    f'<polygon data-port-arrow="{escape(p["key"])}" points="{triangle}" fill="{color}"/>'
                )
            x = px - 16 if side == "left" else px + 16 if side == "right" else px
            y = py - 18 if side != "bottom" else py + 32
            x,y = p.get('label_point', (x,y))
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
            {(layout.systems[k], colors[k]) for k in layout.edges if layout.systems[k]}
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
    text("站场示意 · 保留站内相对形状，距离可压缩 · 自动参考", (w / 2, h - 14), 14, "#768390")
    out.append("</svg>")
    return "\n".join(out)
