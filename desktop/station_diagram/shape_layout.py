"""Shape-led shared transform; topology validates endpoints without moving objects."""
from collections import defaultdict, Counter
import math
from statistics import median

from shapely.geometry import LineString, MultiPoint, Point, box

from .helpers import (DiagramEdge, DiagramLayout, PlatformSymbol, edge_role,
    principal_axis, reliable_platform_axes, station_projection, platform_parts, mainline_label)
from .extractor import extract, station_boundaries
from .topology import build_graph
from .yard_classifier import classify
from .crossings import crossing_symbols
from .shape_annotations import shape_annotations
from .types import Lane
from .shape_paths import rounded_path
from .annotation_layout import place_port_labels
from .direction_layout import bearing
try:
    from ..station_diagram_layout import DiagramOptions, system_name
    from ..station_diagram_geometry import (common_baseline, reference_paths, baseline_value,
        smooth_baseline, densify, line_parts)
except ImportError:
    from station_diagram_layout import DiagramOptions, system_name
    from station_diagram_geometry import (common_baseline, reference_paths, baseline_value,
        smooth_baseline, densify, line_parts)


def build_layout(repo, context=(), options=None):
    options = options or DiagramOptions()
    extraction = extract(repo, context, options)
    graph = build_graph(repo, extraction.selected)
    ownership, ownership_warnings = classify(repo, extraction.selected, options)
    inner, chosen = extraction.inner, extraction.selected
    local, *_ = station_projection(repo)
    platforms = list(platform_parts(context))
    axes = reliable_platform_axes([[local(p) for p in points] for _,_,points in platforms])
    main = [key for key in sorted(inner) if edge_role(repo,repo.edges[key],options) == 'main']
    track_keys = {ref.edge_id for t in repo.station_tracks.values() for ref in t.edge_refs} & chosen
    theta = principal_axis(axes or [[local(p) for p in repo.edges[key].coordinates] for key in sorted(track_keys) or main or sorted(inner)])
    axis_source = 'source_platform_axis' if axes else 'main_track_axis' if main else 'station_tracks'
    angle = theta-(math.pi/2 if options.orientation == 'portrait' else 0) if options.auto_rotate else 0
    ca,sa = math.cos(theta), math.sin(theta)
    def rotate(point):
        x,y = local(point)
        return x*ca+y*sa, -x*sa+y*ca
    raw = {key: tuple(map(rotate,repo.edges[key].coordinates)) for key in sorted(chosen | inner)}
    platform_values = [[rotate(p) for p in points] for _,_,points in platforms]
    body = [p for path in platform_values for p in path]
    station_points = [p for key in track_keys or inner for p in raw[key]]
    boundary = station_boundaries(context)
    if boundary is not None:
        station_points = []
        for key in inner & chosen:
            clipped = LineString(repo.edges[key].coordinates).intersection(boundary)
            pieces = clipped.geoms if hasattr(clipped,'geoms') else [clipped]
            station_points.extend(rotate(p) for piece in pieces if piece.geom_type=='LineString' for p in piece.coords)
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
    systems = {key: value.system for key, value in ownership.items()}
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
    scale = min(available_axis*.72/(hi-lo),available_cross*.72/max(y1-y0,80))
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
        edges[key] = DiagramEdge(key,tuple(screen),edge_role(repo,edge,options),key not in inner,zone,parts,
            " ".join(rounded_path(part) for part in parts))
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
        symbols.append(PlatformSymbol(ident,kind,center.x,center.y,length,thickness*options.platform_width,
                                      math.degrees(math.atan2(b[1]-a[1],b[0]-a[0]))))
    terminal_interval = (min(p[0] for p in body)-50,max(p[0] for p in body)+50) if body else (lo+(hi-lo)*.15,hi-(hi-lo)*.15)
    ports = make_ports(repo,edges,full_geometry,source_points_by_edge,frame,theta,local,*terminal_interval,options=options)
    c0,c1 = page((lo,y0+baseline_value(baseline,lo))),page((hi,y1+baseline_value(baseline,hi)))
    bounds = (min(c0[0],c1[0]),min(c0[1],c1[1]),max(c0[0],c1[0]),max(c0[1],c1[1]))
    warnings = ['未载入真实站台，未生成推测站台'] if not platforms else []
    if options.remove_common_bend and not baseline:
        warnings.append('缺少可靠共同主干，共同弯曲消除未应用')
    source_visible = (inverse_x(-available_axis/(2*scale)),inverse_x(available_axis/(2*scale)))
    for port in ports:
        vx,vy = port['vector']
        port['screen_vector'] = (vx*math.cos(angle)+vy*math.sin(angle), vx*math.sin(angle)-vy*math.cos(angle))
        port['angle_degrees'] = math.degrees(math.atan2(vy,vx)) % 360
        port['direction_source'] = 'source_geometry_bearing_at_radius'
        port['nodes'] = sorted({n for key in port['edge_ids'] for n in graph.endpoints[key]})
    place_port_labels(ports, options)
    yard_labels, line_labels = shape_annotations(repo, edges, ownership, bounds, options)
    lanes = []
    for track in repo.station_tracks.values():
        keys = {ref.edge_id for ref in track.edge_refs if ref.edge_id in edges}
        if not keys:
            continue
        samples = [p for key in keys for p in raw[key] if lo <= p[0] <= hi]
        if not samples:
            continue
        source_y = median(p[1] for p in samples)
        center = page((median(p[0] for p in samples),source_y))
        label_x = (min(p[0] for p in body)+max(p[0] for p in body))/2 if body else mid
        label_ys = [a[1]+(label_x-a[0])/(b[0]-a[0])*(b[1]-a[1])
                    for key in keys for a,b in zip(raw[key],raw[key][1:])
                    if min(a[0],b[0])<=label_x<=max(a[0],b[0]) and abs(b[0]-a[0])>1e-6]
        label_point = page((label_x,min(label_ys,key=lambda y:abs(y-source_y)))) if label_ys else center
        own = ownership[min(keys)]
        lanes.append(Lane(track.id,keys,source_y,center[1],track.yard_id,own.yard_name,
                          own.system,track.track_number,label_point))
    return DiagramLayout(width,height,angle,axis_source,edges,symbols,nodes,ports,bounds,
        (left,top,right,bottom),project,extraction.warnings + ownership_warnings + warnings,
        systems,scale,(lo,hi),source_visible,baseline, lanes=lanes, ownership=ownership,
        yard_labels=yard_labels, line_labels=line_labels, boundary_source=extraction.boundary_source,
        crossings=crossing_symbols(edges,graph,nodes),
        switch_nodes=[n for n,keys in graph.adjacency.items() if len(keys)>=3 and frame.covers(Point(nodes[n]))])


def make_ports(repo,drawings,full,source,frame,theta,local,lo,hi,options):
    """Frame cuts and graph leaves, with original geographic direction vectors."""
    from shapely.geometry import Point
    degree = defaultdict(int)
    station_keys = {ref.edge_id for track in repo.station_tracks.values() for ref in track.edge_refs}
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
    station_geometries = [({repo.edges[key].from_node_id,repo.edges[key].to_node_id},LineString(source[key]))
                          for key,drawing in drawings.items() if drawing.role == 'station']
    for key,drawing in drawings.items():
        line_rule = options.line_overrides.get(repo.edges[key].infrastructure_line_id,{})
        requested_label = line_rule.get('label')
        if drawing.role != 'main' and requested_label is not True:
            continue
        edge = repo.edges[key]
        line = repo.lines.get(edge.infrastructure_line_id)
        if not line or not mainline_label(line) and requested_label is not True:
            continue
        geometry = full[key]
        candidates = [(p,None) for p in point_parts(geometry.intersection(frame.boundary))]
        for node,index,direction in ((edge.from_node_id,0,-1),(edge.to_node_id,-1,1)):
            p = Point(drawing.points[index])
            x = source[key][index][0]
            if key not in station_keys and degree[node] == 1 and frame.contains(p) and not lo <= x <= hi:
                candidates.append((p,direction))
        for p,direction in candidates:
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
            segment_length = math.dist(drawing.points[best], drawing.points[best+1])
            t = min(1,max(0,math.dist(drawing.points[best],(p.x,p.y))/max(segment_length,1e-9)))
            cut = Point(tuple(aa[i]+t*(bb[i]-aa[i]) for i in (0,1)))
            # Test the original geometry, not compressed pixels. A distant
            # throat must not suppress a real outlet when the view is compact.
            if requested_label is not True and any(
                    ends.intersection((edge.from_node_id,edge.to_node_id)) and g.distance(cut)<20
                    for ends,g in station_geometries):
                continue
            dx,dy = (bb[0]-aa[0])*outward,(bb[1]-aa[1])*outward
            norm = math.hypot(dx,dy)
            if norm < .001:
                continue
            vector = ((dx*ca-dy*sa)/norm,(dx*sa+dy*ca)/norm)
            # Direction affects annotations only. The actual source shape,
            # frame cut and shared endpoint remain at their projected positions.
            continuation = ([aa,*source[key][best+1:]] if outward>0 else
                            [bb,*source[key][:best+1][::-1]])
            vector = bearing(continuation,
                lambda v:(v[0]*ca-v[1]*sa,v[0]*sa+v[1]*ca),options.direction_radius_m)
            # Endpoint text sits directly at the final outlet without leaders.
            distances = {'left':abs(p.x-frame.bounds[0]),'right':abs(p.x-frame.bounds[2]),
                         'top':abs(p.y-frame.bounds[1]),'bottom':abs(p.y-frame.bounds[3])}
            side = min(distances, key=distances.get)
            if distances[side] > .01:
                sx,sy = ((b.x-a.x)*outward,(b.y-a.y)*outward)
                side = ('right' if sx>0 else 'left') if abs(sx)>=abs(sy) else ('bottom' if sy>0 else 'top')
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
        port['extended'] = False
    return ports
