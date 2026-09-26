"""Land-only display cache; the original administrative relation stays intact."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import Lock
from urllib.request import urlopen
from uuid import uuid4

from shapely import make_valid, union_all
from shapely.geometry import shape, mapping, box, MultiPolygon
from shapely.strtree import STRtree


LAND_URL = 'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_land.geojson'
LAND_SOURCE = 'Natural Earth 1:10m land (public domain)'
VERSION = 1
_lock = Lock()


def land_path(source):
    return Path(source).parent / 'natural_earth_10m_land.geojson'


def ensure_land(path):
    path = Path(path)
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        with urlopen(LAND_URL, timeout=90) as response, temporary.open('wb') as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        value = json.loads(temporary.read_text(encoding='utf-8'))
        if value.get('type') != 'FeatureCollection' or not value.get('features'):
            raise ValueError('陆地轮廓下载无效')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def polygonal(geometry):
    if geometry.geom_type in ('Polygon', 'MultiPolygon'):
        return geometry
    parts = []
    for item in getattr(geometry, 'geoms', ()):
        value = polygonal(item)
        if value.geom_type == 'Polygon' and not value.is_empty:
            parts.append(value)
        elif value.geom_type == 'MultiPolygon':
            parts.extend(value.geoms)
    return MultiPolygon(parts)


def clip_feature(feature, land):
    clipped = polygonal(make_valid(shape(feature['geometry'])).intersection(land))
    if clipped.is_empty:
        return None
    return {**feature, 'properties': {**feature['properties'], 'display_geometry': 'land_only',
            'land_source': LAND_SOURCE, 'coastline_scale': '1:10000000'}, 'geometry': mapping(clipped)}


def ensure_clipped(source, mask=None):
    source = Path(source).resolve()
    target = source.with_name(source.stem + '_land.sqlite')
    with _lock:
        mask = ensure_land(mask or land_path(source))
        signature = json.dumps([VERSION, source.stat().st_size, source.stat().st_mtime_ns,
                                mask.stat().st_size, mask.stat().st_mtime_ns])
        if target.is_file():
            with closing(sqlite3.connect(target)) as db:
                if db.execute("SELECT value FROM metadata WHERE key='signature'").fetchone() == (signature,):
                    return target
        data = json.loads(mask.read_text(encoding='utf-8'))
        land = [make_valid(shape(feature['geometry'])) for feature in data['features']]
        tree = STRtree(land)
        temporary = target.with_name(target.name + '.' + uuid4().hex + '.tmp')
        try:
            with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(temporary)) as db:
                db.executescript('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);'
                    'CREATE TABLE features(id INTEGER PRIMARY KEY,level INTEGER,coarse TEXT,medium TEXT,detail TEXT);'
                    'CREATE INDEX admin_level ON features(level);'
                    'CREATE VIRTUAL TABLE bounds USING rtree(id,minx,maxx,miny,maxy);')
                for ident, level, raw in src.execute('SELECT id,level,detail FROM features'):
                    feature = json.loads(raw)
                    geometry = make_valid(shape(feature['geometry']))
                    window = box(*geometry.bounds)
                    local_land = union_all([land[int(i)].intersection(window) for i in tree.query(window)])
                    clipped = clip_feature(feature, local_land)
                    if clipped is None:
                        continue
                    geometry = shape(clipped['geometry'])
                    w, s, e, n = geometry.bounds
                    variants = []
                    for tolerance in (.008, .0015, 0):
                        # Clip once more after simplification so no shortcut extends into the sea.
                        display = polygonal(geometry.simplify(tolerance, preserve_topology=True).intersection(local_land)) if tolerance else geometry
                        variants.append(json.dumps({**clipped, 'geometry': mapping(display)}, ensure_ascii=False, separators=(',', ':')))
                    db.execute('INSERT INTO features VALUES(?,?,?,?,?)', (ident, level, *variants))
                    db.execute('INSERT INTO bounds VALUES(?,?,?,?,?)', (ident, w, e, s, n))
                db.executemany('INSERT INTO metadata VALUES(?,?)', [('signature', signature), ('land_url', LAND_URL),
                    ('land_sha256', hashlib.sha256(mask.read_bytes()).hexdigest())])
                db.commit()
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return target
