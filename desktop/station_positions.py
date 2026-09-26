"""Station reference positions on shared physical tracks, never invented areas."""

from contextlib import closing
import json
import sqlite3

from railscope.services.simulation.geometry import distance_m
try:
    from .transport_modes import other_transport
except ImportError:
    from transport_modes import other_transport

POSITION_KEY = 'railscope.org/station-track-positions'
STOP_POSITION_KEY = 'railscope.org/track-position'


def project(coordinates, point):
    from math import cos, radians
    scale = max(.1, cos(radians(point[1])))
    travelled, best = 0., None
    for a, b in zip(coordinates, coordinates[1:]):
        ax, ay = (a[0]-point[0])*scale, a[1]-point[1]
        dx, dy = (b[0]-a[0])*scale, b[1]-a[1]
        fraction = max(0., min(1., -(ax*dx+ay*dy)/(dx*dx+dy*dy))) if dx or dy else 0.
        coordinate = [a[0]+fraction*(b[0]-a[0]), a[1]+fraction*(b[1]-a[1])]
        length = distance_m(a,b)
        candidate = (distance_m(coordinate, point), travelled + length*fraction, coordinate)
        if best is None or candidate[:2] < best[:2]:
            best = candidate
        travelled += length
    return best


def station_center(library, endpoint):
    """Use real station platforms/outline; otherwise identify the POI fallback."""
    if not str(endpoint).startswith('station:'):
        return None
    sources = library._station_sources(endpoint)
    with library.connect() as db:
        if not {'source_x', 'source_y'} <= {row[1] for row in db.execute('PRAGMA table_info(station_aliases)')}:
            return None
        rows = db.execute(f"SELECT source_x,source_y,station_node_id FROM station_aliases WHERE source_id IN ({','.join('?' for _ in sources)}) AND source_x IS NOT NULL AND source_y IS NOT NULL", sources).fetchall()
    if not rows:
        return None
    x,y = rows[0][:2]
    nodes = {row[2] for row in rows if row[2] is not None}
    source = library.path.with_name('rail.sqlite')
    geometries = []
    if source.is_file():
        with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro', uri=True)) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'bounds' in tables:
                for (raw,) in db.execute("SELECT f.data FROM features f JOIN bounds b ON f.id=b.id WHERE f.kind IN ('railPlatforms','railStationAreas') AND b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=?", (x-.025,x+.025,y-.02,y+.02)):
                    feature=json.loads(raw);props=feature['properties']
                    if (not other_transport(props.get('way_tags', {})) and
                        nodes.intersection(props.get('associated_station_ids', [])) and feature['geometry']['type'] in ('Polygon','MultiPolygon')):
                        geometries.append(feature)
    platforms = [f for f in geometries if f['properties'].get('boundary_kind') == 'platform']
    points=[]
    def visit(part):
        if part and isinstance(part[0], (int,float)):
            points.append(part)
        else:
            for child in part: visit(child)
    for feature in platforms or geometries:
        visit(feature['geometry']['coordinates'])
    if points:
        x=(min(p[0] for p in points)+max(p[0] for p in points))/2
        y=(min(p[1] for p in points)+max(p[1] for p in points))/2
    return {'coordinate':[x,y], 'source':'real_platform_extent' if platforms else 'real_station_extent' if points else 'station_poi_projection',
            'station_id':endpoint, 'station_name':library.endpoint_label(endpoint)}


def terminal_tracks(library, endpoint):
    centre = station_center(library, endpoint)
    if not centre or centre['source']=='station_poi_projection':
        return {}
    try:
        from .rail_store import load_edges
    except ImportError:
        from rail_store import load_edges
    nearby=library._station_transfer_edges(endpoint, 3000)
    if not nearby:
        return {}
    result={}
    for edge in load_edges(library.path.parent, [v['id'] for v in nearby]):
        projected=project(edge['coordinates'], centre['coordinate'])
        if not projected: continue
        gap,offset,coordinate=projected
        length=sum(distance_m(a,b) for a,b in zip(edge['coordinates'],edge['coordinates'][1:]))
        # The chosen track must actually pass the station middle; merely
        # touching an outside throat is insufficient.
        if gap <= 180 and 1 < offset < length-1:
            result[edge['id']]={**centre,'edge_id':edge['id'],'offset_m':offset,
                'length_m':length,'gap_m':gap,'coordinate':coordinate,
                'nodes':[edge['from_node'],edge['to_node']]}
    best=min((v['gap_m'] for v in result.values()), default=0)
    return {key:value for key,value in result.items() if value['gap_m']<=best+45}


def route_positions(library, path, endpoints):
    try:
        from .rail_store import load_edges
    except ImportError:
        from rail_store import load_edges
    lookup={edge['id']:edge for edge in load_edges(library.path.parent, [v['edge_id'] for v in path])}
    with library.connect() as db:
        snapshot = db.execute("SELECT value FROM metadata WHERE key='source'").fetchone()
    result=[]
    for endpoint in dict.fromkeys(endpoints):
        centre=station_center(library,endpoint)
        if not centre: continue
        candidates=[];travelled=0.
        for leg in path:
            edge=lookup[leg['edge_id']];coords=edge['coordinates']
            length=sum(distance_m(a,b) for a,b in zip(coords,coords[1:]))
            gap,offset,coordinate=project(coords,centre['coordinate'])
            along=offset if leg['direction']=='forward' else length-offset
            candidates.append((gap,travelled+along,edge,offset,coordinate))
            travelled+=length
        gap,along,edge,offset,coordinate=min(candidates,key=lambda v:v[:2])
        if gap>800: continue
        ids=edge.get('node_ids',[edge['from_node'],edge['to_node']])
        nearest=min(zip(ids,edge['coordinates']), key=lambda item:distance_m(item[1],coordinate))[0]
        result.append({**centre,'edge_id':edge['id'],'offset_m':offset,'distance_m':along,
            'coordinate':coordinate,'node_id':nearest,'gap_m':gap,'version':1,
            'verification_status':'automatic_reference_not_dispatch_verified','confidence':None,
            'snapshot':edge.get('snapshot_id') or (snapshot[0] if snapshot else 'unknown')})
    return sorted(result,key=lambda v:v['distance_m'])


def position_distance(path, lookup, position):
    """Validate a stable edge offset against this complete directed corridor."""
    from math import isfinite
    offset=position.get('offset_m')
    if type(offset) not in (int,float) or not isfinite(offset):
        raise ValueError('站内轨道偏移无效')
    travelled=0.
    for leg in path:
        edge=lookup[leg['edge_id']]
        length=sum(distance_m(a,b) for a,b in zip(edge['coordinates'],edge['coordinates'][1:]))
        if leg['edge_id']==position.get('edge_id'):
            if not 0<=offset<=length:
                raise ValueError('站内轨道偏移超出物理区间')
            return travelled+(offset if leg['direction']=='forward' else length-offset)
        travelled+=length
    raise ValueError('停靠轨道不在完整通道中')
