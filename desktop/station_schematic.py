"""Station schematic from shared physical edges; symbols never change GIS data."""
from html import escape
import math
import re


def ensure_export_font():
    """Qt's offscreen export can start without a system font collection."""
    import os
    from pathlib import Path
    from PySide6.QtGui import QFontDatabase
    if 'Microsoft YaHei' not in QFontDatabase.families():
        font = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/msyh.ttc'
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))


def station_projection(repo):
    """North-up Web Mercator, matching the map with no axis compression."""
    station = next(iter(repo.stations.values()))
    cx, cy = station.lon, station.lat
    cosine = math.cos(math.radians(cy))
    radius = 6378137
    origin_y = radius*math.log(math.tan(math.pi/4+math.radians(cy)/2))
    def local(point):
        return (radius*math.radians(point[0]-cx),
                radius*math.log(math.tan(math.pi/4+math.radians(point[1])/2))-origin_y)
    return local, cx, cy, cosine


ACCESSORY_NAME = re.compile(r'走行|动车|机务|车辆段|检修|出入段|牵出|疏解|联络|机走')
TRACK_NUMBER = re.compile(r'(?:第?\s*[\d一二三四五六七八九十ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*(?:站台|道|股道)|站台\s*\d+)')


def mainline_label(line):
    return bool(line and not line.name.startswith('未命名')
                and not ACCESSORY_NAME.search(line.name) and not TRACK_NUMBER.search(line.name)
                and line.line_role != 'connecting_line'
                and not line.name.endswith(('站场股道','站台线','到发线','安全线')))


def platform_parts(context):
    """Real source platforms become rectangular symbols only in this export."""
    for feature in context:
        props = feature.get('properties', {})
        tags = props.get('way_tags', props)
        if (tags.get('railway') != 'platform' and props.get('kind') != 'platform'
                and 'platform' not in str(props.get('asset_kind', ''))):
            continue
        geometry = feature.get('geometry') or {}
        kind = geometry.get('type')
        coords = geometry.get('coordinates', [])
        pieces = coords if kind == 'MultiPolygon' else [coords] if kind in ('Polygon','LineString') else []
        for index, part in enumerate(pieces):
            points = part[0] if kind in ('Polygon','MultiPolygon') else part
            if len(points) >= 2:
                ident = props.get('infrastructure_id') or props.get('osm_way_id') or props.get('osm_relation_id') or 'platform'
                yield str(ident)+':'+str(index), kind, points


def station_svg(repo, context=(), width=2400, station_info=None):
    station = next(iter(repo.stations.values()))
    station_info = station_info or {}
    yard_ids = {ref.edge_id for track in repo.station_tracks.values() for ref in track.edge_refs}
    all_yard_ids = set(yard_ids)
    if not yard_ids:
        raise ValueError('该站缺少可导出的真实轨道')
    local, *_ = station_projection(repo)
    platforms = list(platform_parts(context))
    if platforms:
        from shapely.geometry import LineString
        from shapely.ops import unary_union
        # Select the platform yard, excluding neighboring depot sidings grouped
        # by the same station name. This is only a selection distance, never a
        # generated platform outline. Keep each selected track's complete path.
        platform_geometry = unary_union([LineString([local(p) for p in points]) for _,_,points in platforms])
        selected = set()
        for track in repo.station_tracks.values():
            members = {ref.edge_id for ref in track.edge_refs}
            if any(LineString([local(p) for p in repo.edges[key].coordinates]).distance(platform_geometry) <= 150
                   for key in members):
                selected.update(members)
        if selected:
            # Follow adjacent station fragments to their true ends. Platform
            # proximity chooses a yard component, never cuts a connected track.
            from collections import defaultdict, deque
            adjacent = defaultdict(list)
            for key in yard_ids:
                edge = repo.edges[key]
                for node in (edge.from_node_id,edge.to_node_id):
                    adjacent[node].append(key)
            queue = deque(node for key in selected for node in
                          (repo.edges[key].from_node_id,repo.edges[key].to_node_id))
            visited = set()
            while queue:
                node = queue.popleft()
                if node in visited:
                    continue
                visited.add(node)
                for key in adjacent[node]:
                    if key not in selected:
                        selected.add(key)
                        queue.extend((repo.edges[key].from_node_id,repo.edges[key].to_node_id))
            yard_ids = selected
    # Keep station throats even when their source way belongs to an access
    # railway. Hide its outside extension/name, never sever station tracks.
    visible = {key:edge for key,edge in repo.edges.items() if key not in all_yard_ids or key in yard_ids}
    frame = [local(p) for key in yard_ids for p in visible[key].coordinates]
    frame += [local(p) for _,_,points in platforms for p in points]
    west, east = min(p[0] for p in frame), max(p[0] for p in frame)
    south, north = min(p[1] for p in frame), max(p[1] for p in frame)
    xpad, ypad = max((east-west)*.06, 25), max((north-south)*.06, 25)
    west -= xpad; east += xpad; south -= ypad; north += ypad
    # Text occupies dedicated columns with no rails underneath, no leaders.
    margin = max(280, min(440, width*.19))
    left, right, top = margin, width-margin, 160
    scale = min((right-left)/(east-west),6000/(north-south))
    plot_height = max(500, (north-south)*scale)
    height = math.ceil(plot_height+290)
    bottom = top+plot_height
    # Additional vertical space centers short, wide yards without stretching.
    yoffset = (plot_height-(north-south)*scale)/2
    xoffset = ((right-left)-(east-west)*scale)/2
    def project(point):
        x, y = local(point)
        return left+xoffset+(x-west)*scale, top+yoffset+(north-y)*scale
    def path(edge):
        from shapely.geometry import LineString, box
        geometry = LineString(list(map(project,edge.coordinates)))
        # Only main-line context outside the selected yard is clipped. Frame
        # padding guarantees that every station track and its ends stay visible.
        if edge.id not in yard_ids:
            geometry = geometry.intersection(box(left,top,right,bottom))
        def serialize(value):
            if value.is_empty:
                return ''
            if value.geom_type in ('MultiLineString','GeometryCollection'):
                return ' '.join(serialize(part) for part in value.geoms)
            if value.geom_type == 'LineString':
                return 'M'+' L'.join(f'{x:.2f},{y:.2f}' for x,y in value.coords)
            return ''
        return serialize(geometry)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<style>text{font-family:Microsoft YaHei;fill:#213c49}</style>',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="65" y="58" font-size="30" font-weight="700">{escape(station.name)}平面布置图</text>',
        f'<text x="65" y="91" font-size="16">{escape(station_info.get("summary") or "类型与站场规模待核实")}</text>',
        f'<g data-track-bounds="{left},{top},{right},{bottom}">']
    for ident, kind, points in platforms:
        values = list(map(project, points))
        from shapely.geometry import LineString, Polygon
        rectangle = (Polygon(values) if kind in ('Polygon','MultiPolygon') else LineString(values)).minimum_rotated_rectangle
        corners = list(rectangle.exterior.coords)[:4] if rectangle.geom_type=='Polygon' else list(rectangle.coords)
        a,b = max(zip(corners,corners[1:]+corners[:1]),key=lambda pair:math.dist(*pair))
        angle = math.degrees(math.atan2(b[1]-a[1],b[0]-a[0]))
        center_x = sum(x for x,y in corners)/len(corners)
        center_y = sum(y for x,y in corners)/len(corners)
        length = math.dist(a,b)
        # A narrow OSM platform way is a rectangular diagram symbol, not an
        # inferred geographic platform boundary. Original geometry is retained.
        thickness = max(4,rectangle.area/max(length,1)) if rectangle.geom_type=='Polygon' else 8
        out.append(f'<rect data-platform-id="{escape(ident)}" data-source-geometry="{kind}" '
                   f'x="{center_x-length/2:.2f}" y="{center_y-thickness/2:.2f}" width="{length:.2f}" height="{thickness:.2f}" '
                   f'transform="rotate({angle:.4f} {center_x:.2f} {center_y:.2f})" '
                   'fill="#d7e4df" stroke="#85a69a" stroke-width="1"/>')
    node_degree = {}
    by_line = {}
    for key, edge in visible.items():
        d = path(edge)
        if not d:
            continue
        for node in (edge.from_node_id, edge.to_node_id):
            node_degree[node] = node_degree.get(node,0)+1
        out.append(f'<path data-edge-id="{escape(key)}" d="{d}" fill="none" stroke="#426574" stroke-width="2.2"/>')
        line = repo.lines.get(edge.infrastructure_line_id)
        if mainline_label(line):
            by_line.setdefault(line.name, (line, []))[1].append(edge)
    for node, degree in node_degree.items():
        if degree >= 3:
            value = repo.nodes[node]; x,y = project((value.lon,value.lat))
            if left <= x <= right and top <= y <= bottom:
                out.append(f'<circle data-node-id="{escape(node)}" cx="{x:.2f}" cy="{y:.2f}" r="3.3" fill="#426574"/>')
    out.append('</g>')
    for side in ('left','right'):
        labels = []
        for line, edges in by_line.values():
            points = [p for edge in edges for p in (edge.coordinates[0],edge.coordinates[-1])]
            point = (min if side == 'left' else max)(points, key=lambda p:local(p)[0])
            labels.append((project(point)[1],line))
        labels.sort(key=lambda item:(item[0],item[1].name))
        # Forward/backward passes keep every label separated and in the margins.
        gap = 54
        positions = []
        for preferred, line in labels:
            positions.append(max(top+20, min(bottom-20, preferred), positions[-1]+gap if positions else top+20))
        if positions and positions[-1] > bottom-20:
            positions[-1] = bottom-20
            for i in range(len(positions)-2,-1,-1):
                positions[i] = min(positions[i],positions[i+1]-gap)
        x = 65 if side == 'left' else width-65
        anchor = 'start' if side == 'left' else 'end'
        for (_, line), y in zip(labels,positions):
            direction = station_info.get('line_destinations',{}).get(line.id,{}).get(side)
            text = line.name+' · '+('往'+direction if direction else '全线端点待补充')
            size = min(19, (margin-105)/max(len(text),1))
            out.append(f'<text data-line-id="{escape(line.id)}" data-end="{side}" x="{x}" y="{y:.2f}" '
                       f'text-anchor="{anchor}" font-size="{size:.2f}">{escape(text)}</text>')
    out.extend([f'<text x="65" y="{height-62}" font-size="14">站台用矩形图示 · 方向为全线端点 · 轨道按地图原形等比绘制</text>',
        f'<text x="65" y="{height-32}" font-size="12">自动参考，非联锁进路图 · 数据 © OpenStreetMap contributors</text>', '</svg>'])
    return '\n'.join(out)
