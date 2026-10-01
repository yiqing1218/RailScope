"""Persistent display names for individual endpoint-delimited yard tracks."""
import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from railscope.rail_semantics import edge_semantics
try:
    from .station_track_semantics import track_number, migrate_track_override, semantic_track_name
except ImportError:
    from station_track_semantics import track_number, migrate_track_override, semantic_track_name


def yard_track_key(props):
    if (str(props.get('catalog_group_id', '')).startswith('ST-')
            or props.get('service') in ('yard', 'siding')
            or props.get('way_tags', {}).get('service') in ('yard', 'siding')):
        for field in ('section_id', 'network_edge_id'):
            if props.get(field):
                return 'object:' + field + ':' + str(props[field])
    return None


def automatic_track_names(database, groups, overrides):
    if not database.exists():
        return {}
    snapshot = str(database.stat().st_mtime_ns)
    marker = 'system:yard_track_names'
    if overrides.get(marker, {}).get('snapshot') == snapshot and overrides.get(marker, {}).get('version') == 3:
        return {}
    stations = {key: record for key, record in groups
                if str(key).startswith('ST-') and record.get('station_name') not in (None, '', '未关联站场')}
    tracks = defaultdict(dict)
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='rail_feature_groups'").fetchone():
            return {}
        rows = db.execute("""SELECT g.group_id,
            json_extract(f.data,'$.properties.section_id'),
            json_extract(f.data,'$.properties')
            FROM rail_feature_groups g JOIN features f ON f.id=g.feature_id
            WHERE g.group_id >= 'ST-' AND g.group_id < 'ST.'""")
        for group, section, raw_props in rows:
            record = stations.get(group)
            if not record or not section:
                continue
            station = (record['station_name'], tuple(sorted(record.get('provinces', []))))
            props = json.loads(raw_props)
            tags = props.get('way_tags', {})
            name, ref = tags.get('name'), track_number(tags)
            value = tracks[station].setdefault(section, {'names': set(), 'refs': set(), 'roles': set()})
            value['roles'].add(edge_semantics(props)['track_role'])
            if name and not name.startswith('未命名'):
                value['names'].add(name)
            if ref:
                value['refs'].add(str(ref))
    changes = {}
    for (station, _province), sections in tracks.items():
        for section, values in sorted(sections.items()):
            key = 'object:section_id:' + section
            role = next(iter(values['roles'])) if len(values['roles']) == 1 else 'unknown'
            original = overrides.get(key, {})
            saved = migrate_track_override(original, station, role)
            if saved != original:
                changes[key] = saved
            if saved.get('display_name') or values['names']:
                continue
            ref = next(iter(values['refs'])) if len(values['refs']) == 1 else None
            if ref:
                label = f'{station} · {ref}' + ('' if ref.endswith('道') else '股道')
            else:
                label = semantic_track_name(station, role)
            changes[key] = {'display_name': label, 'source': 'osm_track_ref' if ref else 'station_track_semantic_label',
                            'snapshot': snapshot, 'version': 3, 'verification_status': 'source_named' if ref else 'unverified',
                            'station_name': station, 'confidence': None, 'track_number': ref}
    changes[marker] = {'snapshot': snapshot, 'version': 3}
    return changes
