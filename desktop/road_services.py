"""Service POIs, source footprints and buildings share one display identity."""

from contextlib import closing
import gc
import json
from pathlib import Path
import sqlite3
from uuid import NAMESPACE_URL, uuid5

from shapely.geometry import Point, shape, mapping
from shapely import make_valid
from shapely.strtree import STRtree


def create_tables(db):
    db.executescript('''
        CREATE TABLE services(id TEXT PRIMARY KEY,name TEXT,province TEXT,city TEXT,county TEXT,
            minx REAL,miny REAL,maxx REAL,maxy REAL);
        CREATE TABLE service_features(id INTEGER PRIMARY KEY,service_id TEXT,kind TEXT,data TEXT);
        CREATE INDEX service_owner ON service_features(service_id,kind);
        CREATE VIRTUAL TABLE service_bounds USING rtree(id,minx,maxx,miny,maxy);
    ''')


class AdminLocator:
    def __init__(self, database, provinces):
        self.provinces = provinces
        self.db = sqlite3.connect(database) if Path(database).is_file() else None

    def locate(self, point, tags):
        province = tags.get('addr:province') or self.provinces.along([point, point])[0]
        city, county = tags.get('addr:city', ''), tags.get('addr:district', '')
        if self.db:
            for level, raw in self.db.execute(
                'SELECT f.level,f.detail FROM features f JOIN bounds b ON b.id=f.id '
                'WHERE b.minx<=? AND b.maxx>=? AND b.miny<=? AND b.maxy>=?',
                (point[0], point[0], point[1], point[1])):
                feature = json.loads(raw)
                if not shape(feature['geometry']).covers(Point(point)):
                    continue
                name = feature['properties']['name']
                if level == 4:
                    province = name
                elif level == 5 and not city:
                    city = name
                elif level == 6 and not county:
                    county = name
        if not city and province in ('北京市', '上海市', '天津市', '重庆市'):
            city = province
        return province, city or '市级待核对', county or '县级待核对'

    def close(self):
        if self.db:
            self.db.close()


def build_services(pbf, db, provinces, location_index, admin_database):
    import osmium
    from railscope.services.importers.native_paths import native_path

    snapshot = str(Path(pbf).stat().st_mtime_ns)
    factory = osmium.geom.GeoJSONFactory()
    tag_filter = osmium.filter.TagFilter(('highway', 'services'), ('highway', 'rest_area'))
    processor = (osmium.FileProcessor(str(native_path(pbf)))
        .with_locations(f'sparse_file_array,{location_index}')
        .with_areas(tag_filter).with_filter(tag_filter)
        .with_filter(osmium.filter.EntityFilter(osmium.osm.NODE | osmium.osm.AREA)))
    records, points = [], []
    try:
        for obj in processor:
            tags = dict(obj.tags)
            if obj.is_node():
                if obj.location.valid():
                    points.append((str(obj.id), tags, Point(obj.location.lon, obj.location.lat)))
                continue
            try:
                geometry = make_valid(shape(json.loads(factory.create_multipolygon(obj))))
            except (RuntimeError, ValueError):
                continue
            if geometry.is_empty or geometry.geom_type not in ('Polygon', 'MultiPolygon'):
                continue
            source = ('way/' if obj.from_way() else 'relation/') + str(obj.orig_id())
            records.append([source, tags, geometry, []])
    finally:
        del processor
        gc.collect()
    tree = STRtree([record[2] for record in records])
    for ident, tags, point in points:
        # Only a unique containing footprint can absorb a duplicate POI.
        matches = [int(i) for i in tree.query(point) if records[int(i)][2].covers(point)
                   and (not tags.get('name') or not records[int(i)][1].get('name')
                        or tags['name'] == records[int(i)][1]['name'])]
        if len(matches) == 1:
            records[matches[0]][3].append((ident, tags, point))
        else:
            records.append(['node/' + ident, tags, point, []])
    locator = AdminLocator(admin_database, provinces)
    owners, polygons = [], []

    def insert_feature(owner, kind, geometry, props):
        feature = {'type': 'Feature', 'properties': {**props, 'service_id': owner, 'asset_kind': kind},
                   'geometry': mapping(geometry)}
        cur = db.execute('INSERT INTO service_features(service_id,kind,data) VALUES(?,?,?)',
                         (owner, kind, json.dumps(feature, ensure_ascii=False, separators=(',', ':'))))
        w, s, e, n = geometry.bounds
        db.execute('INSERT INTO service_bounds VALUES(?,?,?,?,?)', (cur.lastrowid, w, e, s, n))

    try:
        for source, tags, geometry, pois in records:
            owner = 'RSA-' + uuid5(NAMESPACE_URL, 'railscope:road-service:' + source).hex[:24]
            point = pois[0][2] if pois else geometry.representative_point()
            name = tags.get('name:zh') or tags.get('name') or (pois[0][1].get('name') if pois else '') or '未命名服务区'
            province, city, county = locator.locate(list(point.coords)[0], tags)
            props = {'name': name, 'source': 'OpenStreetMap', 'source_ref': source, 'source_tags': tags,
                     'snapshot': snapshot, 'verification_status': 'source_unverified',
                     'province': province, 'city': city, 'county': county,
                     'admin_assignment': 'source_tags_or_boundary_containment',
                     'license': 'ODbL 1.0', 'attribution': '© OpenStreetMap contributors'}
            w, s, e, n = geometry.bounds
            db.execute('INSERT INTO services VALUES(?,?,?,?,?,?,?,?,?)', (owner, name, province, city, county, w, s, e, n))
            insert_feature(owner, 'poi', point, {**props, 'geometry_status': 'source_point' if pois or geometry.geom_type == 'Point' else 'footprint_label_point',
                           'source_pois': [{'osm_node_id': ident, 'tags': tags, 'coordinate': list(p.coords)[0]} for ident, tags, p in pois]})
            if geometry.geom_type != 'Point':
                insert_feature(owner, 'outline', geometry, {**props, 'geometry_status': 'source_geometry'})
                owners.append((owner, props))
                polygons.append(geometry)
    finally:
        locator.close()
    if polygons:
        tree = STRtree(polygons)
        # Building areas are assembled from both closed ways and multipolygon relations.
        building_filter = osmium.filter.KeyFilter('building')
        processor = (osmium.FileProcessor(str(native_path(pbf)))
            .with_locations(f'sparse_file_array,{location_index}')
            .with_areas(building_filter).with_filter(building_filter)
            .with_filter(osmium.filter.EntityFilter(osmium.osm.AREA)))
        try:
            for area in processor:
                if area.tags.get('building') == 'no':
                    continue
                # Reject distant buildings before allocating their full geometry.
                outer = next(iter(area.outer_rings()), None)
                if outer is None or not len(outer) or not outer[0].location.valid():
                    continue
                candidates = tree.query(Point(outer[0].lon, outer[0].lat))
                if not len(candidates):
                    continue
                try:
                    geometry = make_valid(shape(json.loads(factory.create_multipolygon(area))))
                except (RuntimeError, ValueError):
                    continue
                matches = [int(i) for i in candidates if polygons[int(i)].covers(geometry)]
                if len(matches) != 1:
                    continue
                owner, props = owners[matches[0]]
                insert_feature(owner, 'building', geometry, {**props,
                    'source_ref': ('way/' if area.from_way() else 'relation/') + str(area.orig_id()),
                    'source_tags': dict(area.tags), 'geometry_status': 'source_geometry',
                    'verification_status': 'automatic_containment', 'confidence': 1.0})
        finally:
            del processor
            gc.collect()
    db.execute("INSERT INTO metadata VALUES('service_count',?)", (str(len(records)),))
    db.commit()
    return len(records)


def services(path):
    if not Path(path).is_file():
        return []
    with closing(sqlite3.connect(path)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='services'").fetchone():
            return []
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute('SELECT * FROM services ORDER BY province,city,county,name,id')]


def sync_directory(path):
    records = services(path)
    with closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE IF NOT EXISTS service_directory AS SELECT * FROM directory_nodes WHERE 0')
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS service_directory_id ON service_directory(id)')
        db.execute('CREATE INDEX IF NOT EXISTS service_directory_parent ON service_directory(parent_id,label)')
        if db.execute("SELECT count(*) FROM service_directory WHERE kind='object'").fetchone()[0] == len(records):
            return
        db.execute('DELETE FROM service_directory')
        folders = {}
        for item in records:
            parts = [item['province'], item['city'], item['county']]
            parent = ''
            for depth in range(1, 4):
                encoded = json.dumps(parts[:depth], ensure_ascii=False)
                key = 'folder:' + encoded
                folder = folders.setdefault(key, [parent, parts[depth-1], encoded, 0])
                folder[3] += 1
                parent = key
            db.execute('INSERT INTO service_directory VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                ('object:' + item['id'], parent, item['name'], 'object', item['id'],
                 json.dumps(parts, ensure_ascii=False), 0, 1, 0, *parts[:2], 'service_area', item['name'], None, item['id']))
        for key, (parent, label, encoded, total) in folders.items():
            db.execute('INSERT INTO service_directory VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (key, parent, label, 'folder', None, encoded, 0, total, 0, '', '', 'folder', label, None, None))
        db.execute("UPDATE service_directory SET child_count=(SELECT count(*) FROM service_directory c WHERE c.parent_id=service_directory.id) WHERE kind='folder'")
        db.commit()


def viewport(path, bbox, selected=None, zoom=12):
    result = {'type': 'FeatureCollection', 'features': [], 'truncated': False}
    if not Path(path).is_file() or selected == []:
        return result
    if selected is not None and (not isinstance(selected, list) or len(selected) > 100000 or any(not isinstance(k, str) for k in selected)):
        raise ValueError('服务区选择无效')
    with closing(sqlite3.connect(path)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='services'").fetchone():
            return {**result, 'upgrade_required': True}
        where = ''
        if selected is not None:
            db.execute('CREATE TEMP TABLE chosen(id TEXT PRIMARY KEY)')
            db.executemany('INSERT OR IGNORE INTO chosen VALUES(?)', ((key,) for key in selected))
            where += ' AND f.service_id IN (SELECT id FROM chosen)'
        if zoom < 11:
            where += " AND f.kind='poi'"
        w, s, e, n = bbox
        for (raw,) in db.execute('SELECT f.data FROM service_bounds b JOIN service_features f ON f.id=b.id '
                'WHERE b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=?' + where + ' LIMIT 20001', (w,e,s,n)):
            if len(result['features']) == 20000:
                result['truncated'] = True
                break
            result['features'].append(json.loads(raw))
    return result
