"""Service POIs, source footprints and buildings share one display identity."""

from contextlib import closing
import gc
import json
from pathlib import Path
import sqlite3
from uuid import NAMESPACE_URL, uuid5
from functools import lru_cache

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
        matches = [int(i) for i in tree.query(point) if records[int(i)][2].covers(point)]
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


def services(path, overrides=None):
    if not Path(path).is_file():
        return []
    with closing(sqlite3.connect(path)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='services'").fetchone():
            return []
        db.row_factory = sqlite3.Row
        aliases = service_aliases(path)
        records = [dict(row) for row in db.execute('SELECT * FROM services ORDER BY province,city,county,name,id')]
        result = []
        for row in records:
            if aliases.get(row['id'], row['id']) != row['id']:
                continue
            value = service_override(row['id'], aliases, overrides or {})
            row.update({key: value[key] for key in ('province', 'city', 'county') if value.get(key)})
            row['name'] = value.get('display_name') or row['name']
            result.append(row)
        return result


def service_override(ident, aliases, overrides):
    """Replay POI overrides through the entity alias; canonical edits win."""
    value = {}
    for source, target in sorted(aliases.items()):
        if target == ident:
            value.update(overrides.get('object:service_id:' + source, {}))
    value.update(overrides.get('object:service_id:' + ident, {}))
    return value


def service_aliases(path):
    path = Path(path)
    return _service_aliases(str(path.resolve()), path.stat().st_mtime_ns)


@lru_cache(maxsize=4)
def _service_aliases(path, version):
    """Replay legacy POI/AOI identity unification without modifying source rows."""
    with closing(sqlite3.connect(path)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='service_features'").fetchone():
            return {}
        outlines = [(owner, shape(json.loads(raw)['geometry'])) for owner, raw in
                    db.execute("SELECT service_id,data FROM service_features WHERE kind='outline'")]
        if not outlines:
            return {}
        tree = STRtree([geometry for _, geometry in outlines])
        has_outline = {owner for owner, _ in outlines}
        aliases = {}
        for owner, raw in db.execute("SELECT service_id,data FROM service_features WHERE kind='poi'"):
            if owner in has_outline:
                continue
            point = shape(json.loads(raw)['geometry'])
            matches = {outlines[int(i)][0] for i in tree.query(point) if outlines[int(i)][1].covers(point)}
            if len(matches) == 1:
                aliases[owner] = matches.pop()
        return aliases


def service_repository(path, overrides=None, selected=None):
    from railscope.domain import ServiceArea, ServiceAreaGeometry
    from railscope.repository import RailRepository
    repo = RailRepository()
    aliases = service_aliases(path)
    if selected is not None:
        selected = [aliases.get(key, key) for key in selected]
    for row in services(path, overrides):
        if selected is not None and row['id'] not in selected:
            continue
        repo.service_areas[row['id']] = ServiceArea(row['id'], row['name'],
            (row['minx'] + row['maxx']) / 2, (row['miny'] + row['maxy']) / 2,
            attributes={key: row[key] for key in ('province', 'city', 'county')},
            verification_status='automatic_reference' if row['id'] in aliases.values() else 'source_unverified',
            source='unique_source_area_containment' if row['id'] in aliases.values() else 'OpenStreetMap',
            snapshot_id=str(Path(path).stat().st_mtime_ns))
    with closing(sqlite3.connect(path)) as db:
        from dataclasses import replace
        members = {}
        query, args = 'SELECT service_id,kind,data FROM service_features', []
        if selected is not None:
            args = list(set(selected) | {source for source,target in aliases.items() if target in selected})
            query += ' WHERE service_id IN (' + (','.join('?' for _ in args) or 'NULL') + ')'
        for owner, kind, raw in db.execute(query, args):
            owner = aliases.get(owner, owner)
            feature = json.loads(raw)
            source = str(feature['properties'].get('source_ref') or owner + '/' + kind)
            ident = 'SAG-' + uuid5(NAMESPACE_URL, owner + '/' + source + '/' + kind).hex[:20]
            repo.service_area_geometries[ident] = ServiceAreaGeometry(ident, owner, kind, feature['geometry'], source)
            members.setdefault(owner, set()).add(source)
            for poi in feature['properties'].get('source_pois', []):
                if poi.get('osm_node_id'):
                    members[owner].add('node/' + str(poi['osm_node_id']))
            if kind == 'poi':
                entity = repo.service_areas[owner]
                repo.service_areas[owner] = replace(entity, lon=feature['geometry']['coordinates'][0], lat=feature['geometry']['coordinates'][1])
        for ident, entity in repo.service_areas.items():
            repo.service_areas[ident] = replace(entity, source_member_ids=tuple(sorted(members.get(ident, ()))))
    return repo


def sync_directory(path, overrides=None):
    records = services(path, overrides)
    with closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE IF NOT EXISTS service_directory AS SELECT * FROM directory_nodes WHERE 0')
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS service_directory_id ON service_directory(id)')
        db.execute('CREATE INDEX IF NOT EXISTS service_directory_parent ON service_directory(parent_id,label)')
        current = dict(db.execute("SELECT object_id,label FROM service_directory WHERE kind='object'"))
        if current == {row['id']: row['name'] for row in records} and not overrides:
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
    aliases = service_aliases(path)
    with closing(sqlite3.connect(path)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='services'").fetchone():
            return {**result, 'upgrade_required': True}
        where = ''
        names = dict(db.execute('SELECT id,name FROM services'))
        poi_seen = set()
        if selected is not None:
            selected = [aliases.get(value, value) for value in selected]
            selected = list(set(selected) | {source for source, target in aliases.items() if target in selected})
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
            feature = json.loads(raw)
            props = feature['properties']
            props['service_id'] = aliases.get(props['service_id'], props['service_id'])
            props['entity_id'] = props['service_id']
            props['entity_type'] = 'ServiceArea'
            props['service_alias_ids'] = [source for source, target in aliases.items() if target == props['service_id']]
            if props['service_alias_ids']:
                props['entity_association'] = {'source':'unique_source_area_containment', 'version':1,
                    'snapshot':str(Path(path).stat().st_mtime_ns), 'verification_status':'automatic_reference', 'confidence':None}
            props['name'] = names[props['service_id']]
            if props.get('asset_kind') == 'poi':
                if props['service_id'] in poi_seen:
                    continue
                poi_seen.add(props['service_id'])
            result['features'].append(feature)
    return result
