"""Complete motorway overview: simplify geometry, never sample away source ways."""

from collections import defaultdict
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from threading import Lock
from uuid import uuid4

VERSION = 3
_build_lock = Lock()


def simplify(points, tolerance):
    """Iterative Douglas-Peucker with exact endpoints retained."""
    keep = {0, len(points)-1}
    stack = [(0, len(points)-1)]
    threshold = tolerance*tolerance
    while stack:
        first, last = stack.pop()
        a, b = points[first], points[last]
        dx, dy = b[0]-a[0], b[1]-a[1]
        length = dx*dx+dy*dy
        index, maximum = None, threshold
        for i in range(first+1, last):
            x, y = points[i][0]-a[0], points[i][1]-a[1]
            t = min(1, max(0, (x*dx+y*dy)/length)) if length else 0
            distance = (x-t*dx)**2+(y-t*dy)**2
            if distance > maximum:
                index, maximum = i, distance
        if index is not None:
            keep.add(index)
            stack.extend(((first,index),(index,last)))
    return [points[i] for i in sorted(keep)]


def chains(parts):
    """Join only identical source endpoints with degree two in this route group."""
    endpoints = defaultdict(list)
    for i, part in enumerate(parts):
        endpoints[tuple(part[0])].append(i)
        endpoints[tuple(part[-1])].append(i)
    used = set()

    def walk(index, start):
        coordinates, count = [], 0
        while index not in used:
            used.add(index)
            part = parts[index]
            if tuple(part[0]) != start:
                part = part[::-1]
            coordinates.extend(part if not coordinates else part[1:])
            count += 1
            start = tuple(part[-1])
            neighbors = endpoints[start]
            if len(neighbors) != 2:
                break
            index = neighbors[0] if neighbors[1] == index else neighbors[1]
        return coordinates, count

    for endpoint, neighbors in endpoints.items():
        if len(neighbors) != 2:
            for index in neighbors:
                if index not in used:
                    yield walk(index, endpoint)
    for index, part in enumerate(parts):
        if index not in used:
            yield walk(index, tuple(part[0]))


def fingerprint(path):
    stat = path.stat()
    return json.dumps([VERSION, stat.st_size, stat.st_mtime_ns])


def ensure_overview(source):
    source = Path(source).resolve()
    destination = source.with_name(source.stem + '_overview.sqlite')
    with _build_lock:
        signature = fingerprint(source)
        if destination.is_file():
            with closing(sqlite3.connect(destination)) as db:
                if db.execute("SELECT value FROM metadata WHERE key='source'").fetchone() == (signature,):
                    return destination
        temporary = destination.with_name(destination.name + '.' + uuid4().hex + '.tmp')
        try:
            with closing(sqlite3.connect(temporary)) as target, closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src:
                target.executescript('PRAGMA temp_store=FILE; PRAGMA cache_size=-8192;'
                    'CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);'
                    'CREATE TABLE staging(group_key TEXT,coordinates TEXT);'
                    'CREATE TABLE groups(id INTEGER PRIMARY KEY,properties TEXT);'
                    'CREATE TABLE paths(id INTEGER PRIMARY KEY,lod INTEGER,group_id INTEGER,source_count INTEGER,coordinates TEXT);'
                    'CREATE VIRTUAL TABLE bounds USING rtree(id,minx,maxx,miny,maxy);')
                groups = {}
                for (raw,) in src.execute('SELECT data FROM features ORDER BY id'):
                    feature = json.loads(raw)
                    props = feature['properties']
                    key = json.dumps([props.get('road_class','unresolved'), props.get('construction',False), props.get('name',''), sorted(props.get('route_keys',[]))],ensure_ascii=False)
                    if key not in groups:
                        groups[key] = {k: v for k, v in props.items() if k != 'osm_way_id'}
                        groups[key]['name'] = props.get('name') or props.get('ref') or '高速公路'
                    groups[key]['provinces'] = sorted(set(groups[key].get('provinces', [])) | set(props.get('provinces', [])))
                    coords = feature['geometry']['coordinates']
                    target.execute('INSERT INTO staging VALUES(?,?)',(key,json.dumps(coords,separators=(',',':'))))
                target.execute('CREATE INDEX staging_group ON staging(group_key)')
                for group_id, (key, props) in enumerate(sorted(groups.items()), 1):
                    props = {**props, 'overview': True, 'geometry_status': 'simplified_source_geometry', 'snapshot': signature}
                    target.execute('INSERT INTO groups VALUES(?,?)',(group_id,json.dumps(props,ensure_ascii=False,separators=(',',':'))))
                    parts = [json.loads(row[0]) for row in target.execute('SELECT coordinates FROM staging WHERE group_key=?',(key,))]
                    for coordinates, count in chains(parts):
                        for lod, tolerance in ((6,.008),(9,.0015),(12,.00015)):
                            simplified = simplify(coordinates, tolerance)
                            # A closed short loop must retain enough vertices to render.
                            if simplified[0] == simplified[-1] and len(simplified) < 4:
                                simplified = coordinates
                            cur = target.execute('INSERT INTO paths(lod,group_id,source_count,coordinates) VALUES(?,?,?,?)',
                                (lod,group_id,count,json.dumps(simplified,separators=(',',':'))))
                            xs, ys = zip(*simplified)
                            target.execute('INSERT INTO bounds VALUES(?,?,?,?,?)',(cur.lastrowid,min(xs),max(xs),min(ys),max(ys)))
                target.execute('DROP TABLE staging')
                target.execute('CREATE INDEX path_lod ON paths(lod)')
                target.execute("INSERT INTO metadata VALUES('source',?)",(signature,))
                target.commit()
                target.execute('VACUUM')
            if fingerprint(source) != signature:
                raise ValueError('高速数据已更新，请重新载入地图')
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    return destination


def viewport(source, bbox, zoom, route_key=None, route_keys=None):
    database = ensure_overview(source)
    lod = 6 if zoom <= 6 else 9 if zoom <= 9 else 12
    selected = {route_key} if route_key else set(route_keys) if route_keys is not None else None
    features, count = {}, 0
    west, south, east, north = bbox
    with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        groups = {ident: json.loads(raw) for ident, raw in db.execute('SELECT id,properties FROM groups')}
        for group, raw, source_count in db.execute(
                'SELECT p.group_id,p.coordinates,p.source_count FROM bounds b JOIN paths p ON p.id=b.id '
                'WHERE p.lod=? AND b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=?',(lod,west,east,south,north)):
            props = groups[group]
            if selected is not None and not selected.intersection(props.get('route_keys',[])):
                continue
            feature = features.setdefault(group, {'type':'Feature','properties':props,
                'geometry':{'type':'MultiLineString','coordinates':[]}})
            feature['geometry']['coordinates'].append(json.loads(raw))
            count += source_count
    return {'type':'FeatureCollection','features':list(features.values()), 'truncated':False,
            'generalized':True, 'lod':lod, 'source_segment_count':count}
