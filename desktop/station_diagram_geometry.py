"""A common-bend shear plus axis-only piecewise compression in a fixed frame."""
from bisect import bisect_right
from collections import defaultdict, Counter
import math
from statistics import median

from shapely.geometry import LineString, MultiPoint, box

try:
    from .station_diagram_layout import (DiagramOptions, DiagramEdge, DiagramLayout, PlatformSymbol,
        select_edges, edge_role, connected_systems, system_name, principal_axis,
        reliable_platform_axes, diagram_helpers)
except ImportError:
    from station_diagram_layout import (DiagramOptions, DiagramEdge, DiagramLayout, PlatformSymbol,
        select_edges, edge_role, connected_systems, system_name, principal_axis,
        reliable_platform_axes, diagram_helpers)


def common_baseline(paths, interval):
    """Median of the dominant parallel trunk at source vertices and core samples.

    Subtracting the same b(x) from all objects removes collective bending but
    retains differences y1(x)-y2(x). There is no per-track straightening.
    """
    lo,hi = interval
    knots = sorted({p[0] for path in paths for p in path} | {lo+(hi-lo)*i/40 for i in range(41)})
    result = []
    for x in knots:
        values = []
        for path in paths:
            samples = []
            for a,b in zip(path,path[1:]):
                if min(a[0],b[0]) <= x <= max(a[0],b[0]) and abs(b[0]-a[0]) > .001:
                    t = (x-a[0])/(b[0]-a[0])
                    samples.append(a[1]+t*(b[1]-a[1]))
            if samples:
                values.append(median(samples))
        if values:
            result.append((x,median(values)))
    return tuple(result)


def reference_paths(repo, keys, raw):
    """Join physical degree-two fragments before measuring a parallel pair.

    Otherwise one side of a pair can momentarily be counted twice at an OSM
    fragment endpoint, shifting the median onto a single rail by one spacing.
    """
    adjacency = defaultdict(list)
    for key in keys:
        edge = repo.edges[key]
        adjacency[edge.from_node_id].append(key)
        adjacency[edge.to_node_id].append(key)
    remaining, paths = set(keys), []
    while remaining:
        endpoints = sorted(node for node,edges in adjacency.items() if len(edges) != 2
                           and any(k in remaining for k in edges))
        first = min(remaining)
        node = endpoints[0] if endpoints else repo.edges[first].from_node_id
        path = []
        while True:
            key = next((k for k in sorted(adjacency[node]) if k in remaining),None)
            if key is None:
                break
            remaining.remove(key)
            edge = repo.edges[key]
            forward = node == edge.from_node_id
            points = raw[key] if forward else raw[key][::-1]
            path.extend(points if not path else points[1:])
            node = edge.to_node_id if forward else edge.from_node_id
            if len(adjacency[node]) != 2:
                break
        if path:
            paths.append(path)
    return paths


def baseline_value(knots, x):
    if not knots:
        return 0.0
    index = bisect_right(knots, (x,math.inf))-1
    if index < 0:
        return knots[0][1]
    if index >= len(knots)-1:
        return knots[-1][1]
    a,b = knots[index:index+2]
    return a[1]+(x-a[0])/(b[0]-a[0])*(b[1]-a[1])


def smooth_baseline(knots):
    """Fit a cubic broad trend; never reproduce the raw median's short steps."""
    if len(knots) < 4 or knots[-1][0]-knots[0][0] < 100:
        return ()
    lo,hi = knots[0][0],knots[-1][0]
    samples = [(lo+(hi-lo)*i/80, -1+2*i/80) for i in range(81)]
    matrix = [[sum(t**(i+j) for _,t in samples) for j in range(4)]
              + [sum(t**i*baseline_value(knots,x) for x,t in samples)] for i in range(4)]
    for column in range(4):
        pivot = max(range(column,4),key=lambda row:abs(matrix[row][column]))
        matrix[column],matrix[pivot] = matrix[pivot],matrix[column]
        denominator = matrix[column][column]
        if abs(denominator) < 1e-10:
            return ()
        matrix[column] = [v/denominator for v in matrix[column]]
        for row in range(4):
            if row != column:
                factor = matrix[row][column]
                matrix[row] = [a-factor*b for a,b in zip(matrix[row],matrix[column])]
    coefficients = [matrix[i][-1] for i in range(4)]
    return tuple((x,sum(c*t**i for i,c in enumerate(coefficients))) for x,t in samples)


def densify(points, x_knots):
    """Split display geometry at warp knots, without splitting NetworkEdges."""
    result = [points[0]]
    for a,b in zip(points,points[1:]):
        if a[0] != b[0]:
            lo,hi = sorted((a[0],b[0]))
            start,stop = bisect_right(x_knots,lo), bisect_right(x_knots,hi)
            middle = x_knots[start:stop]
            if b[0] < a[0]:
                middle = middle[::-1]
            for x in middle:
                t = (x-a[0])/(b[0]-a[0])
                if 0 < t < 1:
                    result.append((x,a[1]+t*(b[1]-a[1])))
        result.append(b)
    return result


def line_parts(value):
    if value.geom_type == 'LineString' and value.length > .1:
        yield tuple(value.coords)
    elif hasattr(value,'geoms'):
        for part in value.geoms:
            yield from line_parts(part)


def build_layout(repo, context=(), options=None):
    options = options or DiagramOptions()
    station_projection, platform_parts, _ = diagram_helpers()
    inner, chosen = select_edges(repo,options)
    local, *_ = station_projection(repo)
    platforms = list(platform_parts(context))
    axes = reliable_platform_axes([[local(p) for p in points] for _,_,points in platforms])
    main = [key for key in sorted(inner) if edge_role(repo,repo.edges[key],options) == 'main']
    theta = principal_axis(axes or [[local(p) for p in repo.edges[key].coordinates] for key in main or sorted(inner)])
    axis_source = 'source_platform_axis' if axes else 'main_track_axis' if main else 'station_tracks'
    angle = theta-(math.pi/2 if options.orientation == 'portrait' else 0) if options.auto_rotate else 0
    ca,sa = math.cos(theta), math.sin(theta)
    def rotate(point):
        x,y = local(point)
        return x*ca+y*sa, -x*sa+y*ca
    raw = {key: tuple(map(rotate,repo.edges[key].coordinates)) for key in sorted(chosen | inner)}
    platform_values = [[rotate(p) for p in points] for _,_,points in platforms]
    body = [p for path in platform_values for p in path]
    station_points = [p for key in inner for p in raw[key]]
    if body:
        lo,hi = min(p[0] for p in body), max(p[0] for p in body)
        span = max(hi-lo,80)
        # Include both throats in the station region. They are not treated as
        # arbitrary third compression zones.
        lo = min(lo, max(min(p[0] for p in station_points),lo-span*.65))
        hi = max(hi, min(max(p[0] for p in station_points),hi+span*.65))
    else:
        lo,hi = min(p[0] for p in station_points), max(p[0] for p in station_points)
    if hi-lo < 80:
        mid = (lo+hi)/2
        lo,hi = mid-40,mid+40
    mid = (lo+hi)/2
    systems = connected_systems(repo,chosen | inner,options)
    groups = defaultdict(list)
    body_hull = MultiPoint(body).convex_hull if body else None
    for key in main:
        if systems.get(key) and (body_hull is None or LineString(raw[key]).distance(body_hull) <= 200):
            groups[systems[key]].append(key)
    # One continuous trunk supplies a shared reference; competing route bends
    # remain relative geometry, not separately flattened paths.
    dominant = max(groups, key=lambda name: sum(max(0,min(max(p[0] for p in raw[k]),hi)-max(min(p[0] for p in raw[k]),lo)) for k in groups[name]), default=None)
    reference = reference_paths(repo,groups[dominant] if dominant else main,raw)
    baseline = smooth_baseline(common_baseline(reference,(lo,hi))) if options.remove_common_bend and reference else ()
    def longitudinal(x):
        if lo <= x <= hi:
            return (x-mid)/options.station_compression
        if x < lo:
            return (lo-mid)/options.station_compression+(x-lo)/options.outside_compression
        return (hi-mid)/options.station_compression+(x-hi)/options.outside_compression
    def inverse_x(x):
        left,right = longitudinal(lo),longitudinal(hi)
        if x < left:
            return lo+(x-left)*options.outside_compression
        if x > right:
            return hi+(x-right)*options.outside_compression
        return mid+x*options.station_compression
    def warp(point):
        x,y = point
        return longitudinal(x), y-baseline_value(baseline,x)
    # Peripheral bypasses can cross the station's longitudinal interval while
    # being kilometres away laterally. They must not set the station scale.
    body_y0 = min(p[1] for p in body)-180 if body else -math.inf
    body_y1 = max(p[1] for p in body)+180 if body else math.inf
    core_values = [warp(p) for p in station_points if lo <= p[0] <= hi and body_y0 <= p[1] <= body_y1]
    core_values += [warp(p) for p in body]
    if not core_values:
        core_values = [warp(p) for p in station_points]
    y0,y1 = min(p[1] for p in core_values),max(p[1] for p in core_values)
    width,height = options.canvas_size
    font,margin = options.label_size,options.margin
    gutter = max(230,font*11) if options.show_endpoints else margin
    left,right = margin+gutter,width-margin-gutter
    top = margin+(options.title_size*2.3 if options.show_title else 20)
    bottom = height-margin-(130 if options.show_legend else 40)
    if right-left < 120 or bottom-top < 120:
        raise ValueError('画布过小，无法容纳字号和边距；请增大输出尺寸')
    delta = theta-angle
    cd,sd = math.cos(delta),math.sin(delta)
    available_axis = min((right-left)/abs(cd) if abs(cd) > 1e-6 else math.inf,
                         (bottom-top)/abs(sd) if abs(sd) > 1e-6 else math.inf)
    available_cross = min((right-left)/abs(sd) if abs(sd) > 1e-6 else math.inf,
                          (bottom-top)/abs(cd) if abs(cd) > 1e-6 else math.inf)
    # Fixed source-to-page scale independent of selected outside length and
    # compression settings. Greater outside compression therefore exposes a
    # longer source interval rather than shrinking the complete drawing.
    scale = min(available_axis*.78*1.5/(hi-lo),available_cross*.72/max(y1-y0,80))
    cx,cy = (left+right)/2,(top+bottom)/2
    my = (y0+y1)/2
    def page(point):
        x,y = warp(point)
        return cx+(x*cd-(y-my)*sd)*scale,cy-(x*sd+(y-my)*cd)*scale
    def project(point):
        return page(rotate(point))
    frame = box(left,top,right,bottom)
    knots = sorted({lo,hi} | {p[0] for p in baseline})
    nodes = {node: project((repo.nodes[node].lon,repo.nodes[node].lat)) for key in chosen
             for node in (repo.edges[key].from_node_id,repo.edges[key].to_node_id)}
    edges, full_geometry, source_points_by_edge = {},{},{}
    for key in sorted(chosen):
        edge = repo.edges[key]
        points = densify(raw[key],knots)
        screen = list(map(page,points))
        # Use the actual shared endpoint coordinates, even if a source fragment
        # stores a slightly different rounding of its end.
        screen[0],screen[-1] = nodes[edge.from_node_id],nodes[edge.to_node_id]
        geometry = LineString(screen)
        full_geometry[key] = geometry
        source_points_by_edge[key] = points
        parts = tuple(line_parts(geometry.intersection(frame)))
        if not parts:
            continue
        center_x = sum(p[0] for p in raw[key])/len(raw[key])
        zone = 'station' if lo <= center_x <= hi else 'external'
        edges[key] = DiagramEdge(key,tuple(screen),edge_role(repo,edge,options),key not in inner,zone,parts)
    symbols = []
    for ident,kind,points in platforms:
        rectangle = MultiPoint(list(map(project,points))).minimum_rotated_rectangle
        corners = list(rectangle.exterior.coords) if rectangle.geom_type == 'Polygon' else list(rectangle.coords)
        sides = [(math.dist(a,b),a,b) for a,b in zip(corners,corners[1:])]
        if not sides:
            continue
        length,a,b = max(sides)
        center = rectangle.centroid
        if not frame.covers(center):
            continue
        thickness = rectangle.area/max(length,1) if rectangle.geom_type == 'Polygon' else 8
        # Symbol width is a style choice; length changes only with the axial
        # coordinate transform, never with an independent false length factor.
        symbols.append(PlatformSymbol(ident,kind,center.x,center.y,length,max(8,thickness)*options.platform_width,
                                      math.degrees(math.atan2(b[1]-a[1],b[0]-a[0]))))
    terminal_interval = (min(p[0] for p in body)-50,max(p[0] for p in body)+50) if body else (lo+(hi-lo)*.15,hi-(hi-lo)*.15)
    ports = make_ports(repo,edges,full_geometry,source_points_by_edge,frame,theta,local,*terminal_interval,options=options)
    c0,c1 = page((lo,y0+baseline_value(baseline,lo))),page((hi,y1+baseline_value(baseline,hi)))
    bounds = (min(c0[0],c1[0]),min(c0[1],c1[1]),max(c0[0],c1[0]),max(c0[1],c1[1]))
    warnings = ['未载入真实站台，未生成推测站台'] if not platforms else []
    if options.remove_common_bend and not baseline:
        warnings.append('缺少可靠共同主干，共同弯曲消除未应用')
    source_visible = (inverse_x(-available_axis/(2*scale)),inverse_x(available_axis/(2*scale)))
    return DiagramLayout(width,height,angle,axis_source,edges,symbols,nodes,ports,bounds,
        (left,top,right,bottom),project,warnings,systems,scale,(lo,hi),source_visible,baseline)


def make_ports(repo,drawings,full,source,frame,theta,local,lo,hi,options):
    """Frame cuts and graph leaves, with original geographic direction vectors."""
    from shapely.geometry import Point
    degree = defaultdict(int)
    for key in full:
        e = repo.edges[key]
        degree[e.from_node_id] += 1
        degree[e.to_node_id] += 1
    ports = []
    def point_parts(value):
        if value.geom_type == 'Point':
            yield value
        elif hasattr(value,'geoms'):
            for part in value.geoms:
                yield from point_parts(part)
    ca,sa = math.cos(theta),math.sin(theta)
    station_geometries = [full[key] for key,drawing in drawings.items() if drawing.role == 'station']
    for key,drawing in drawings.items():
        line_rule = options.line_overrides.get(repo.edges[key].infrastructure_line_id,{})
        requested_label = line_rule.get('label')
        if drawing.role != 'main' and requested_label is not True:
            continue
        edge = repo.edges[key]
        line = repo.lines.get(edge.infrastructure_line_id)
        mainline_label = diagram_helpers()[2]
        if not line or not mainline_label(line) and requested_label is not True:
            continue
        geometry = full[key]
        candidates = [(p,None) for p in point_parts(geometry.intersection(frame.boundary))]
        for node,index,direction in ((edge.from_node_id,0,-1),(edge.to_node_id,-1,1)):
            p = Point(drawing.points[index])
            x = source[key][index][0]
            if degree[node] == 1 and frame.contains(p) and not lo <= x <= hi:
                candidates.append((p,direction))
        for p,direction in candidates:
            # If a crop still cuts the station-track fan, this is not yet a
            # clean main-line mouth. Do not attach an outlet label to the fan.
            if requested_label is not True and any(geometry.distance(p) < 24 for geometry in station_geometries):
                continue
            distance = geometry.project(p)
            step = min(5,geometry.length/4)
            a,b = geometry.interpolate(max(0,distance-step)),geometry.interpolate(min(geometry.length,distance+step))
            if direction is None and frame.contains(a) == frame.contains(b):
                continue
            outward = direction if direction else -1 if not frame.covers(a) else 1
            # Find the source segment for this display cut. Densification
            # supplies paired coordinates, so shear does not reverse cities.
            best = min(range(len(drawing.points)-1), key=lambda i: LineString(drawing.points[i:i+2]).distance(p))
            aa,bb = source[key][best:best+2]
            dx,dy = (bb[0]-aa[0])*outward,(bb[1]-aa[1])*outward
            norm = math.hypot(dx,dy)
            if norm < .001:
                continue
            vector = ((dx*ca-dy*sa)/norm,(dx*sa+dy*ca)/norm)
            # Endpoint text sits directly at the final outlet without leaders.
            side = 'left' if p.x < (frame.bounds[0]+frame.bounds[2])/2 else 'right'
            item = next((v for v in ports if system_name(v['line']) == system_name(line) and v['side'] == side
                         and sum(a*b for a,b in zip(v['vector'],vector)) > .85
                         and math.dist(v['point'],(p.x,p.y)) < 160),None)
            if item:
                item['edge_ids'].add(key)
                if not any(math.dist(v,(p.x,p.y)) < .1 for v in item['points']):
                    item['points'].append((p.x,p.y))
            else:
                ports.append({'line':line,'side':side,'point':(p.x,p.y),'points':[(p.x,p.y)],
                              'vector':vector,'edge_ids':{key},'role':drawing.role})
    counts = Counter((port['line'].id,port['side']) for port in ports)
    for port in ports:
        port['key'] = port['line'].id+':'+port['side']
        if counts[(port['line'].id,port['side'])] > 1:
            port['key'] += ':'+min(port['edge_ids'])
        port['original_points'] = tuple(port['points'])
        rule = options.port_overrides.get(port['key'],{})
        port['visible'] = rule.get('visible',options.line_overrides.get(port['line'].id,{}).get('label') is not False)
        lateral = all(frame.bounds[1]+1 < p[1] < frame.bounds[3]-1 for p in port['points'])
        extend = rule.get('extend',options.align_main_outlets and lateral)
        port['extended'] = False
        if extend:
            # A drawing extension only, never an invented NetworkEdge. Its
            # provenance and original terminal point are recorded in metadata.
            x = frame.bounds[0] if port['side']=='left' else frame.bounds[2]
            port['points'] = [(x,p[1]) for p in port['points']]
            port['point'] = (x,port['point'][1])
            port['extended'] = any(abs(a[0]-b[0]) > .1 for a,b in zip(port['original_points'],port['points']))
    return ports
