"""Read-only diagram layout over the shared RailScope repository.

Coordinates here are drawing units, never infrastructure or operational IDs.
The independent station_diagram package preserves shape with a common transform;
shared nodes remain actual connections. Legacy option names survive migration.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
import math


@dataclass(frozen=True)
class DiagramOptions:
    layout_mode: str = 'yard_relative'
    auto_rotate: bool = True
    orientation: str = 'landscape'
    show_north: bool = True
    station_compression: float = 2.5
    outside_compression: float = 8.0
    remove_common_bend: bool = True
    include_construction: bool = False
    platform_width: float = 1.0
    margin: float = 50
    topology_depth: int = 8
    show_main: bool = True
    show_station: bool = True
    show_connectors: bool = True
    show_outer_main: bool = True
    show_outer_connectors: bool = True
    show_platforms: bool = True
    show_legend: bool = True
    show_title: bool = True
    show_endpoints: bool = True
    main_width: float = 3.0
    station_width: float = 3.0
    connector_width: float = 3.0
    direction_radius_m: float = 2000
    title_size: float = 48
    label_size: float = 30
    platform_fill: str = 'gray'
    color_scheme: str = 'systems'
    color_overrides: dict[str, str] = field(default_factory=dict)
    line_overrides: dict[str, dict] = field(default_factory=dict)
    port_overrides: dict[str, dict] = field(default_factory=dict)
    yard_overrides: dict[str, dict] = field(default_factory=dict)
    selected_yards: tuple[str, ...] = ()
    track_overrides: dict[str, dict] = field(default_factory=dict)
    platform_overrides: dict[str, dict] = field(default_factory=dict)
    show_track_labels: bool = True
    show_platform_labels: bool = True
    align_main_outlets: bool = True
    width: int = 2400
    aspect_ratio: float = 1.85
    output_format: str = 'svg'
    dpi: int = 300

    def __post_init__(self):
        import re
        for name in ('station_compression', 'platform_width', 'direction_radius_m',
                     'outside_compression', 'main_width', 'station_width',
                     'connector_width', 'title_size', 'label_size', 'aspect_ratio'):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(name + ' 必须是正数')
        if not 800 <= self.width <= 12000 or not 72 <= self.dpi <= 1200:
            raise ValueError('输出宽度须为 800–12000，分辨率须为 72–1200 DPI')
        if not 0 <= self.topology_depth <= 64 or not isinstance(self.topology_depth, int):
            raise ValueError('拓扑追踪层数须为 0–64 的整数')
        if not 10 <= self.margin <= self.width/5:
            raise ValueError('留白须为 10 到输出宽度的 1/5')
        if not 1.1 <= self.aspect_ratio <= 3:
            raise ValueError('横纵比须为 1.1–3（纵向时取倒数）')
        for name, values in {
            'layout_mode': ('yard_relative', 'source_shape'),
            'orientation': ('landscape', 'portrait'), 'output_format': ('svg', 'png', 'pdf'),
            'color_scheme': ('systems', 'mono'),
            'platform_fill': ('gray', 'tint', 'outline'),
        }.items():
            if getattr(self, name) not in values:
                raise ValueError('无效选项：' + name)
        if any(not re.fullmatch(r'#[0-9a-fA-F]{6}', color) for color in self.color_overrides.values()):
            raise ValueError('手动颜色必须为 #RRGGBB')
        for value in self.line_overrides.values():
            if value.get('role','auto') not in ('auto','main','station','connector','auxiliary'):
                raise ValueError('无效的图面线路角色')
            if value.get('color') and not re.fullmatch(r'#[0-9a-fA-F]{6}',value['color']):
                raise ValueError('线路颜色必须为 #RRGGBB')
            if value.get('width') is not None and not .3 <= float(value['width']) <= 20:
                raise ValueError('单线路线宽须为 0.3–20')
        for value in self.port_overrides.values():
            for key in ('dx','dy'):
                if not math.isfinite(float(value.get(key,0))) or abs(float(value.get(key,0))) > 1000:
                    raise ValueError('标注位置偏移须在 -1000 到 1000 之间')
        for value in self.yard_overrides.values():
            if value.get('color') and not re.fullmatch(r'#[0-9a-fA-F]{6}', value['color']):
                raise ValueError('分场颜色必须为 #RRGGBB')

    @property
    def canvas_size(self):
        long_side, short_side = self.width, round(self.width/self.aspect_ratio)
        return (long_side, short_side) if self.orientation == 'landscape' else (short_side, long_side)


@dataclass(frozen=True)
class DiagramEdge:
    id: str
    points: tuple[tuple[float, float], ...]
    role: str
    external: bool
    zone: str
    parts: tuple = ()
    path: str = ''


@dataclass(frozen=True)
class PlatformSymbol:
    id: str
    source_kind: str
    x: float
    y: float
    length: float
    width: float
    angle: float = 0


@dataclass
class DiagramLayout:
    width: int
    height: int
    angle: float
    axis_source: str
    edges: dict[str, DiagramEdge]
    platforms: list[PlatformSymbol]
    nodes: dict[str, tuple[float, float]]
    ports: list[dict]
    core_bounds: tuple[float, float, float, float]
    plot_bounds: tuple[float, float, float, float]
    project: object
    warnings: list[str]
    systems: dict[str, str | None]
    geometry_scale: float
    station_interval: tuple[float, float]
    visible_source_interval: tuple[float, float]
    baseline: tuple
    lanes: list = field(default_factory=list)
    ownership: dict = field(default_factory=dict)
    yard_labels: list = field(default_factory=list)
    line_labels: list = field(default_factory=list)
    boundary_source: str = ''
    crossings: list = field(default_factory=list)
    switch_nodes: list[str] = field(default_factory=list)
    algorithm: str = 'source_shape_shared_transform_v2'
    groups: dict = field(default_factory=dict)
    annotations: list = field(default_factory=list)
    group_baselines: dict = field(default_factory=dict)
    extensions: list = field(default_factory=list)


def edge_role(repo, edge, options=None):
    """Prefer domain semantics; named-line inference stays a display reference."""
    try:
        from .station_schematic import ACCESSORY_NAME, mainline_edge
    except ImportError:
        from station_schematic import ACCESSORY_NAME, mainline_edge
    line = repo.lines.get(edge.infrastructure_line_id)
    override = options.line_overrides.get(edge.infrastructure_line_id,{}) if options else {}
    if override.get('role','auto') != 'auto':
        return override['role']
    if (edge.track_role == 'crossover' or edge.service == 'crossover'
            or edge.line_role == 'connecting_line' or (line and line.line_role == 'connecting_line')
            or (line and ACCESSORY_NAME.search(line.name))):
        return 'connector'
    if edge.track_role == 'main_track' or mainline_edge(repo, edge):
        return 'main'
    if edge.track_role in ('safety_track', 'storage_track', 'depot_track') or edge.service in ('spur', 'yard'):
        return 'auxiliary'
    return 'station'


def station_inner_edges(repo):
    """Station references plus connected short throat fragments, without edits."""
    station_projection = diagram_helpers()[0]
    local, *_ = station_projection(repo)
    inner = {ref.edge_id for track in repo.station_tracks.values() for ref in track.edge_refs}
    if not inner or not inner.issubset(repo.edges):
        raise ValueError('该站缺少完整的真实轨道引用')
    points = [local(p) for key in inner for p in repo.edges[key].coordinates]
    bounds = (min(p[0] for p in points)-180, min(p[1] for p in points)-180,
              max(p[0] for p in points)+180, max(p[1] for p in points)+180)
    adjacent = defaultdict(list)
    for key, edge in repo.edges.items():
        adjacent[edge.from_node_id].append(key)
        adjacent[edge.to_node_id].append(key)
    queue = deque(sorted(inner))
    while queue:
        edge = repo.edges[queue.popleft()]
        for node in (edge.from_node_id, edge.to_node_id):
            for key in adjacent[node]:
                if key in inner:
                    continue
                if all(bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]
                       for x,y in map(local, repo.edges[key].coordinates)):
                    inner.add(key)
                    queue.append(key)
    return inner


def diagram_helpers():
    try:
        from .station_schematic import station_projection, platform_parts, mainline_label
    except ImportError:
        from station_schematic import station_projection, platform_parts, mainline_label
    return station_projection, platform_parts, mainline_label


def system_name(line):
    import re
    return re.sub(r'(?:上行|下行)(?:线)?$', '', line.name.replace('（参考）','').replace('(参考)','')).strip()


def connected_systems(repo, keys, options=None):
    """Compatibility query: only explicit domain memberships, never diffusion."""
    try:
        from .station_diagram.yard_classifier import classify
    except ImportError:
        from station_diagram.yard_classifier import classify
    decisions, _ = classify(repo, keys, options or DiagramOptions())
    return {key: decision.system for key, decision in decisions.items()}


def select_edges(repo, options):
    try:
        from .station_diagram.extractor import extract
    except ImportError:
        from station_diagram.extractor import extract
    result = extract(repo, (), options)
    return result.inner, result.selected


def principal_axis(paths):
    """Length-weighted, unoriented segment direction; no station-name rules."""
    xx = yy = xy = 0.0
    for points in paths:
        for a, b in zip(points, points[1:]):
            dx, dy = b[0]-a[0], b[1]-a[1]
            length = math.hypot(dx, dy)
            if length:
                xx += dx*dx/length
                yy += dy*dy/length
                xy += dx*dy/length
    return .5*math.atan2(2*xy, xx-yy)


def platform_axis(points):
    """Use the long side of a source outline, not its closed ring direction."""
    from shapely.geometry import MultiPoint
    rectangle = MultiPoint(points).minimum_rotated_rectangle
    if rectangle.geom_type != 'Polygon':
        return points
    corners = list(rectangle.exterior.coords)
    a, b = max(zip(corners, corners[1:]), key=lambda pair: math.dist(*pair))
    return [a, b]


def reliable_platform_axes(paths):
    from shapely.geometry import MultiPoint
    result = []
    for points in paths:
        rectangle = MultiPoint(points).minimum_rotated_rectangle
        if rectangle.geom_type == 'Polygon':
            corners = list(rectangle.exterior.coords)
            sides = [math.dist(a,b) for a,b in zip(corners,corners[1:])]
            if max(sides)/max(min(sides), .01) < 2:
                continue  # A broad area is not evidence for a platform axis.
        result.append(platform_axis(points))
    return result


def build_layout(repo, context=(), options=None):
    try:
        from .station_diagram.pipeline import build_layout as build
    except ImportError:
        from station_diagram.pipeline import build_layout as build
    return build(repo, context, options)


def smooth_path(points):
    """C1, shape-preserving cubic interpolation, not a tiny S at every vertex."""
    from shapely.geometry import LineString
    # Subpixel source noise should not become a chain of visible waves.
    points = tuple(LineString(points).simplify(1.2, preserve_topology=True).coords)
    steps = [max(math.dist(a,b),.001) for a,b in zip(points,points[1:])]
    if not steps:
        return ''
    def slopes(axis):
        secants = [(b[axis]-a[axis])/h for a,b,h in zip(points,points[1:],steps)]
        values = [secants[0]]
        for i in range(1,len(points)-1):
            a,b = secants[i-1:i+1]
            if a*b <= 0:
                values.append(0.0)
            else:
                w1,w2 = 2*steps[i]+steps[i-1],steps[i]+2*steps[i-1]
                values.append((w1+w2)/(w1/a+w2/b))
        values.append(secants[-1])
        return values
    dx,dy = slopes(0),slopes(1)
    out = [f'M{points[0][0]:.2f},{points[0][1]:.2f}']
    for i,(a,b,h) in enumerate(zip(points,points[1:],steps)):
        if abs(a[0]-b[0]) < .5 or abs(a[1]-b[1]) < .5:
            out.append(f'L{b[0]:.2f},{b[1]:.2f}')
        else:
            out.append(f'C{a[0]+dx[i]*h/3:.2f},{a[1]+dy[i]*h/3:.2f} '
                       f'{b[0]-dx[i+1]*h/3:.2f},{b[1]-dy[i+1]*h/3:.2f} {b[0]:.2f},{b[1]:.2f}')
    return ' '.join(out)
