"""Shared station presentation adapter; source keys are not domain object IDs.

The compact index owns authoritative names. Track anchors and cached corridor
labels never establish a station's identity. Domain IDs remain in the existing
IdentityRegistry/Repository adapters.
"""

from contextlib import closing
from functools import lru_cache
import json
from pathlib import Path
import sqlite3

try:
    from .station_search import rail_name_key, station_names
    from .transport_modes import other_transport
except ImportError:
    from station_search import rail_name_key, station_names
    from transport_modes import other_transport


def display_name(name):
    value = str(name or '').strip()
    if value and any('\u4e00' <= c <= '\u9fff' for c in value) and not value.endswith(
            ('站', '线路所', '信号所', '乘降所', '车辆段', '机务段', '动车段', '动车所', '客整所', '整备所', '检修所', '检修段', '场', '）', ')')):
        value += '站'
    return value


def source_names(props):
    return station_names({**props.get('node_tags', {}), **props})


def compatible_area(props, station):
    """Proximity alone cannot make another named station an alias."""
    if other_transport(props.get('way_tags', {})):
        return False
    if props.get('boundary_kind') == 'platform':
        return True  # A platform's own label need not be a station name.
    name = props.get('source_name', '')
    return not name or rail_name_key(name) in {rail_name_key(n) for n in source_names(station)}


def build_station_directory(db, source):
    try:
        from .catalog_metadata import station_type
    except ImportError:
        from catalog_metadata import station_type
    db.execute('CREATE TABLE IF NOT EXISTS station_directory(source_id TEXT PRIMARY KEY, name TEXT, data TEXT)')
    db.execute('DELETE FROM station_directory')
    db.execute('CREATE TABLE IF NOT EXISTS station_track_positions(source_id TEXT,edge_id TEXT,data TEXT,PRIMARY KEY(source_id,edge_id))')
    db.execute('DELETE FROM station_track_positions')
    stations = {}
    with closing(sqlite3.connect(Path(source).resolve().as_uri() + '?mode=ro', uri=True)) as src:
        type_evidence = {}
        for (raw,) in src.execute("SELECT data FROM features WHERE kind='railStationAreas'"):
            area = json.loads(raw)['properties']
            hint = station_type(area.get('way_tags', {}))
            if hint == '未定义':
                continue
            for member in area.get('associated_station_ids', []):
                source_key = str(member) if str(member).startswith(('node/','way/','relation/')) else 'node/' + str(member)
                type_evidence.setdefault(source_key, {}).setdefault(hint, []).append(area.get('infrastructure_id'))
        for (raw,) in src.execute("SELECT data FROM features WHERE kind='railPoints'"):
            feature = json.loads(raw)
            p = feature.get('properties', {})
            if p.get('kind') not in ('station', 'halt', 'signal_box', 'junction', 'crossing', 'yard', 'depot', 'workshop', 'works', 'engine_shed'):
                continue
            if other_transport(p.get('node_tags', {})):
                db.execute('DELETE FROM station_aliases WHERE source_id=?', ('node/' + str(p.get('osm_node_id')),))
                continue
            source_id = p.get('station_source_id') or 'node/' + str(p['osm_node_id'])
            hints = type_evidence.get(source_id, {})
            if station_type(p.get('node_tags', {}), p.get('kind','')) == '未定义' and len(hints) == 1:
                p['station_type_hint'] = next(iter(hints))
                p['station_type_provenance'] = {'source':'associated_osm_area',
                    'source_member_ids':next(iter(hints.values())), 'verification_status':'automatic_reference',
                    'snapshot':str(Path(source).stat().st_mtime_ns)}
                raw = json.dumps(feature, ensure_ascii=False)
            stations[source_id] = p
            db.execute('INSERT OR REPLACE INTO station_directory VALUES(?,?,?)',
                       (source_id, display_name(p.get('name') or source_id), raw))
    # Old indexes accepted every nearby building's name as a station alias.
    # Keep only names actually belonging to this source station, including its
    # explicit old/alternative names. This changes the derived lookup only.
    for source_id, p in stations.items():
        allowed = {rail_name_key(n) for n in source_names(p)}
        rows = db.execute('SELECT DISTINCT alias FROM station_aliases WHERE source_id=?', (source_id,)).fetchall()
        for (alias,) in rows:
            if rail_name_key(alias) not in allowed:
                db.execute('DELETE FROM station_aliases WHERE source_id=? AND alias=?', (source_id, alias))
        # Repair sparse topology: a long edge may pass the station even when
        # neither endpoint lies inside the old search radius.
        coordinate = json.loads(db.execute('SELECT data FROM station_directory WHERE source_id=?', (source_id,)).fetchone()[0]).get('geometry', {}).get('coordinates')
        if coordinate:
            for position in nearby_tracks(source, db, coordinate, 800):
                db.execute('INSERT OR REPLACE INTO station_track_positions VALUES(?,?,?)',
                           (source_id, position['edge_id'], json.dumps(position)))
                for alias in source_names(p):
                    db.execute('INSERT OR REPLACE INTO station_aliases VALUES(?,?,?,?,?,?,?,?,?)',
                        (source_id, alias, p.get('osm_node_id', source_id), position['anchor_node'], position['gap_m'],
                         'automatic_track_projection', max(.5, 1-position['gap_m']/1600), *coordinate))


def nearby_tracks(source, db, coordinate, radius=800, line_ids=None):
    """Query real edge geometry, then measure perpendicular distance to it."""
    try:
        from .station_positions import project
    except ImportError:
        from station_positions import project
    from math import cos, radians
    source = Path(source)
    if not source.exists():
        return []
    x, y = coordinate
    dy = radius / 110000
    dx = dy / max(.1, cos(radians(y)))
    result = []
    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as src:
        if not src.execute("SELECT 1 FROM sqlite_master WHERE name='bounds'").fetchone():
            return []
        for (raw,) in src.execute("SELECT f.data FROM bounds b JOIN features f ON f.id=b.id WHERE f.kind='rail' "
                'AND b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=?', (x-dx, x+dx, y-dy, y+dy)):
            f = json.loads(raw)
            edge_id = f['properties'].get('network_edge_id')
            edge = db.execute('SELECT a,b,line_id,length_m,construction,direction FROM edges WHERE id=?', (edge_id,)).fetchone()
            if not edge or edge[4] or edge[5] == 'closed' or (line_ids is not None and edge[2] not in line_ids):
                continue
            projected = project(f['geometry']['coordinates'], coordinate)
            if not projected or projected[0] > radius:
                continue
            gap, offset, point = projected
            result.append({'edge_id': edge_id, 'line_id': edge[2], 'offset_m': offset,
                'coordinate': point, 'gap_m': gap, 'anchor_node': edge[0] if offset <= edge[3]/2 else edge[1],
                'source': 'station_poi_track_projection', 'verification_status': 'automatic_reference',
                'confidence': None, 'snapshot': str(source.stat().st_mtime_ns)})
    return sorted(result, key=lambda p: (p['gap_m'], p['edge_id']))


def read_directory(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='station_directory'").fetchone():
        return {}
    return {key: {'name': name, 'feature': json.loads(raw)}
            for key, name, raw in db.execute('SELECT source_id,name,data FROM station_directory')}


@lru_cache(maxsize=4)
def _cached_directory(path, size, modified):
    with closing(sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True)) as db:
        return read_directory(db)


def load_directory(index):
    path = Path(index).resolve()
    if not path.exists():
        return {}
    stat = path.stat()
    return _cached_directory(str(path), stat.st_size, stat.st_mtime_ns)


def station_key(props):
    if props.get('station_source_id'):
        return props['station_source_id']
    key = props.get('station_key') or props.get('station_id')
    if str(key).startswith(('station:', 'node/', 'way/', 'relation/')):
        return str(key).removeprefix('station:')
    source = props.get('source_station_node')
    if source is not None:
        return 'node/' + str(source)
    if props.get('kind') in ('station', 'halt', 'signal_box', 'junction', 'crossing', 'yard', 'depot', 'workshop', 'works', 'engine_shed'):
        return props.get('infrastructure_id') or ('node/' + str(props.get('osm_node_id')))
    return None


def apply_station_names(collection, directory, overrides=None):
    overrides = overrides or {}
    for f in collection.get('features', []):
        p = f.get('properties', {})
        key = station_key(p)
        record = directory.get(key)
        if record is None:
            continue
        if f.get('geometry', {}).get('type') != 'Point' and not compatible_area(p, record['feature']['properties']):
            continue
        name = overrides.get('station:' + key, {}).get('display_name') or record['name']
        p['station_key'] = 'station:' + key
        p['station_name'] = name
        p['display_name'] = name
    return collection


def refresh_plan_names(payload, library):
    """Refresh cached labels only; leave station, edge, node and offset IDs intact."""
    changes = []
    for route in payload.get('routes', []):
        positions = route.get('extensions', {}).get('railscope.org/station-track-positions', [])
        replacements = {}
        for p in positions:
            key = str(p.get('station_id', '')).removeprefix('station:')
            if key not in library.station_directory:
                continue
            new = library.endpoint_label('station:' + key)
            old = p.get('station_name', '')
            if old != new:
                p['station_name'] = new
                replacements[old] = new
                changes.append({'station_key': 'station:' + key, 'old_name': old, 'name': new})
        # Only generated endpoint titles are updated. User-authored titles stay.
        title = route.get('name', '')
        if ' → ' in title and title.endswith((' · 单向通道', ' · 单向参考通道')):
            parts = title.split(' → ')
            parts[0] = replacements.get(parts[0], parts[0])
            end, separator, suffix = parts[-1].partition(' · ')
            parts[-1] = replacements.get(end, end) + separator + suffix
            route['name'] = ' → '.join(parts)
    return changes
