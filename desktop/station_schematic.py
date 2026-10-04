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


ACCESSORY_NAME = re.compile(r'走行|动走|动车|机务|车辆段|检修|出入段|牵出|疏解|联络|机走|折返|立折|存车|客整')
TRACK_NUMBER = re.compile(r'(?:第?\s*[\d一二三四五六七八九十ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*(?:站台|道|股道)|站台\s*\d+)')
LINE_COLORS = ('#2774b5', '#ba4141', '#278557', '#8052a4', '#a46622', '#17878b', '#a34b83', '#59649d')


def mainline_label(line):
    name = line.name.replace('（参考）','').replace('(参考)','').strip() if line else ''
    return bool(line and not name.startswith('未命名')
                and not ACCESSORY_NAME.search(name) and not TRACK_NUMBER.search(name)
                and line.line_role != 'connecting_line'
                and not name.endswith(('站场股道','站台线','到发线','安全线')))


def mainline_edge(repo, edge):
    return (mainline_label(repo.lines.get(edge.infrastructure_line_id))
            and edge.track_role in ('main_track','unknown')
            and edge.service not in ('yard','siding','spur','crossover'))


def outgoing_ports(repo, geometries, bounds, terminal_exclusion=None):
    """Actual frame crossings, grouped by railway and parallel physical tracks.

    A pair is two existing tracks, never a generated duplicate of a single line.
    Endpoints inside the view are included only when they are real open ends of
    this loaded graph, outside the platform core. No business topology changes.
    """
    from collections import Counter
    from shapely.geometry import Point, box
    frame = box(*bounds)
    west,south,east,north = bounds
    degree = Counter(node for key in geometries for node in
                     (repo.edges[key].from_node_id,repo.edges[key].to_node_id))
    candidates = []
    def points(value):
        if value.geom_type == 'Point':
            yield value
        elif hasattr(value, 'geoms'):
            for part in value.geoms:
                yield from points(part)
    for key, geometry in geometries.items():
        edge = repo.edges[key]
        if not mainline_edge(repo,edge):
            continue
        crossings = list(points(geometry.intersection(frame.boundary)))
        ends = [(edge.from_node_id,Point(geometry.coords[0]),-1),
                (edge.to_node_id,Point(geometry.coords[-1]),1)]
        entries = [(point,None) for point in crossings]
        for node,point,direction in ends:
            if degree[node] == 1 and frame.contains(point) and not (
                    terminal_exclusion is not None and terminal_exclusion.contains(point)):
                entries.append((point,direction))
        for point,end_direction in entries:
            distance = geometry.project(point)
            step = min(50,geometry.length/4)
            a = geometry.interpolate(max(0,distance-step))
            b = geometry.interpolate(min(geometry.length,distance+step))
            if end_direction is None and frame.contains(a) == frame.contains(b):
                continue  # A tangential touch is not an outgoing railway.
            if end_direction == -1 or (end_direction is None and not frame.covers(a)):
                a,b = b,a
            vector = (b.x-a.x,b.y-a.y)
            length = math.hypot(*vector)
            if not length:
                continue
            vector = tuple(v/length for v in vector)
            side = min(('left','right','top','bottom'),key=lambda side: {
                'left':abs(point.x-west), 'right':abs(point.x-east),
                'top':abs(point.y-north), 'bottom':abs(point.y-south)}[side])
            line = repo.lines[edge.infrastructure_line_id]
            candidates.append({'point':(point.x,point.y),'vector':vector,'side':side,'line':line,'edge_ids':{key}})
    groups = []
    for item in sorted(candidates,key=lambda item:(item['line'].name,item['side'],item['point'])):
        match = next((group for group in groups if group['line'].name == item['line'].name
            and group['side']==item['side'] and math.dist(group['point'],item['point']) <= 160
            and sum(a*b for a,b in zip(group['vector'],item['vector'])) > .85),None)
        if match is None:
            groups.append({**item,'points':[item['point']]})
        else:
            # One physical boundary point can be shared by two source fragments.
            if not any(math.dist(p,item['point']) < .05 for p in match['points']):
                match['points'].append(item['point'])
            match['edge_ids'].update(item['edge_ids'])
            match['point'] = tuple(sum(p[i] for p in match['points'])/len(match['points']) for i in (0,1))
    return groups


def port_destination(port, station_info, local):
    """Orient full railway termini by the actual outward direction, not columns."""
    record = station_info.get('line_destinations',{}).get(port['line'].id,{})
    terminals = record.get('terminals',[])
    located = [terminal for terminal in terminals if terminal.get('coordinates')]
    far = [terminal for terminal in located if math.hypot(*local(terminal['coordinates'])) > 2500]
    if len(far)==1 and len(located)==2:
        return far[0]['name']
    if len(located) == 2:
        # Compare both terminal vectors at the station origin. A local curve
        # changes the drawing side, but must not turn a northern terminus into
        # the southern one simply because it exits the right-hand border.
        def alignment(terminal):
            vector = local(terminal['coordinates'])
            length = math.hypot(*vector)
            return sum(a*b for a,b in zip(vector,port['vector']))/length if length > 2500 else 0
        return max(located,key=alignment)['name']
    side = port['side']
    if side in ('top','bottom'):
        side = 'right' if port['vector'][0] >= 0 else 'left'
    return record.get(side)


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


def map_station_svg(repo, context=(), width=2400, station_info=None):
    """Legacy map-proportional baseline, retained for comparison tooling only."""
    station = next(iter(repo.stations.values()))
    station_info = station_info or {}
    yard_ids = {ref.edge_id for track in repo.station_tracks.values() for ref in track.edge_refs}
    all_yard_ids = set(yard_ids)
    if not yard_ids:
        raise ValueError('该站缺少可导出的真实轨道')
    local, _, _, cosine = station_projection(repo)
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
    from shapely.geometry import LineString, box
    geometries = {key:LineString([local(p) for p in edge.coordinates]) for key,edge in visible.items()}
    yard_box = box(west,south,east,north).buffer(150/cosine)
    # Keep full station tracks; outside the yard, show the trunk approaches.
    # Auxiliary/depot tracks cannot create additional apparent main-line exits.
    geometries = {key:geometry if key in yard_ids or mainline_edge(repo,visible[key])
                  else geometry.intersection(yard_box) for key,geometry in geometries.items()}
    geometries = {key:geometry for key,geometry in geometries.items() if not geometry.is_empty}
    port_geometries = {key:LineString([local(p) for p in visible[key].coordinates]) for key in geometries}
    terminal_exclusion = platform_geometry.envelope.buffer(350/cosine) if platforms else None
    xpad, ypad = max((east-west)*.06, 350/cosine), max((north-south)*.06, 350/cosine)
    west -= xpad; east += xpad; south -= ypad; north += ypad
    # Move the frame beyond an unfinished fan while keeping the map projection.
    # Real four-track corridors stay four-track; a bounded search never deletes
    # source tracks merely to force every mouth to contain two.
    for _ in range(5):
        ports = outgoing_ports(repo,port_geometries,(west,south,east,north),terminal_exclusion)
        crowded = {port['side'] for port in ports if len(port['points']) > 2}
        if not crowded:
            break
        step = 400/cosine
        west -= step if 'left' in crowded else 0
        east += step if 'right' in crowded else 0
        north += step if 'top' in crowded else 0
        south -= step if 'bottom' in crowded else 0
    ports = outgoing_ports(repo,port_geometries,(west,south,east,north),terminal_exclusion)
    line_names = sorted({port['line'].name for port in ports})
    colors = {name:LINE_COLORS[i%len(LINE_COLORS)] for i,name in enumerate(line_names)}
    # Text occupies dedicated columns with no rails underneath, no leaders.
    margin = max(280, min(440, width*.19))
    left, right, top = margin, width-margin, 230
    scale = min((right-left)/(east-west),6000/(north-south))
    plot_height = max(500, (north-south)*scale)
    height = math.ceil(plot_height+390)
    bottom = top+plot_height
    # Additional vertical space centers short, wide yards without stretching.
    yoffset = (plot_height-(north-south)*scale)/2
    xoffset = ((right-left)-(east-west)*scale)/2
    def project(point):
        x, y = local(point)
        return left+xoffset+(x-west)*scale, top+yoffset+(north-y)*scale
    def project_local(point):
        x,y = point
        return left+xoffset+(x-west)*scale, top+yoffset+(north-y)*scale
    def path(edge):
        from shapely.ops import transform
        geometry = transform(lambda x,y,z=None:(left+xoffset+(x-west)*scale,
                             top+yoffset+(north-y)*scale), geometries[edge.id])
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
    for key, edge in visible.items():
        if key not in geometries:
            continue
        d = path(edge)
        if not d:
            continue
        for node in (edge.from_node_id, edge.to_node_id):
            node_degree[node] = node_degree.get(node,0)+1
        line = repo.lines.get(edge.infrastructure_line_id)
        color = colors.get(line.name,'#617078') if line and mainline_label(line) else '#617078'
        out.append(f'<path data-edge-id="{escape(key)}" d="{d}" fill="none" stroke="{color}" stroke-width="2.2"/>')
    for node, degree in node_degree.items():
        if degree >= 3:
            value = repo.nodes[node]; x,y = project((value.lon,value.lat))
            if left <= x <= right and top <= y <= bottom:
                out.append(f'<circle data-node-id="{escape(node)}" cx="{x:.2f}" cy="{y:.2f}" r="3.3" fill="#426574"/>')
    out.append('</g>')
    for side in ('left','right','top','bottom'):
        vertical = side in ('left','right')
        labels = [(project_local(port['point'])[1 if vertical else 0],port) for port in ports if port['side']==side]
        labels.sort(key=lambda item:(item[0],item[1]['line'].name))
        gap = 60 if vertical else 260
        low, high = (top+30,bottom-30) if vertical else (left+120,right-120)
        positions = []
        for preferred, port in labels:
            positions.append(max(low, min(high, preferred), positions[-1]+gap if positions else low))
        if positions and positions[-1] > high:
            positions[-1] = high
            for i in range(len(positions)-2,-1,-1):
                positions[i] = min(positions[i],positions[i+1]-gap)
        for (_, port), position in zip(labels,positions):
            line = port['line']
            direction = port_destination(port,station_info,local)
            text = line.name+' · '+('往'+direction if direction else '全线端点待补充')
            color = colors[line.name]
            px,py = project_local(port['point'])
            # A matching dot on each physical exit track connects the margin
            # label to its pair without drawing leader lines over the station.
            for point in port['points']:
                x,y = project_local(point)
                out.append(f'<circle data-port-line="{escape(line.id)}" cx="{x:.2f}" cy="{y:.2f}" r="3.3" fill="{color}"/>')
            x = (left-25 if side=='left' else right+25) if vertical else position
            y = position if vertical else (top-65 if side=='top' else bottom+50)
            anchor = ('end' if side=='left' else 'start') if vertical else 'middle'
            size = min(19, (margin-65 if vertical else 245)/max(len(text),1))
            out.append(f'<text data-line-id="{escape(line.id)}" data-end="{side}" '
                       f'data-port-x="{px:.2f}" data-port-y="{py:.2f}" data-track-count="{len(port["points"])}" '
                       f'data-port-edges="{escape(" ".join(sorted(port["edge_ids"])))}" '
                       f'x="{x:.2f}" y="{y:.2f}" text-anchor="{anchor}" font-size="{size:.2f}" '
                       f'style="fill:{color}">{escape(text)}</text>')
    out.extend([f'<text x="65" y="{height-62}" font-size="14">同色轨道与文字对应一个线路出口 · 站台用矩形图示 · 轨道按地图原形等比绘制</text>',
        f'<text x="65" y="{height-32}" font-size="12">自动参考，非联锁进路图 · 数据 © OpenStreetMap contributors</text>', '</svg>'])
    return '\n'.join(out)


def station_svg(repo, context=(), width=2400, station_info=None, options=None, *, layout=None):
    """Public export API now builds a topology-based, partitioned diagram."""
    try:
        from .station_diagram_layout import DiagramOptions
        from .station_diagram_render import render_svg
    except ImportError:
        from station_diagram_layout import DiagramOptions
        from station_diagram_render import render_svg
    return render_svg(repo, context, options or DiagramOptions(width=width), station_info, layout=layout)
