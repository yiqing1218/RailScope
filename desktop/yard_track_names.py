"""Persistent display names for individual endpoint-delimited yard tracks."""
import re
import sqlite3
from collections import defaultdict
from contextlib import closing


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
    if overrides.get(marker, {}).get('snapshot') == snapshot:
        return {}
    stations = {key: record for key, record in groups
                if str(key).startswith('ST-') and record.get('station_name') not in (None, '', '未关联站场')}
    tracks = defaultdict(dict)
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='rail_feature_groups'").fetchone():
            return {}
        rows = db.execute("""SELECT g.group_id,
            json_extract(f.data,'$.properties.section_id'),
            json_extract(f.data,'$.properties.way_tags.name'),
            coalesce(json_extract(f.data,'$.properties.way_tags."railway:track_ref"'),
                     json_extract(f.data,'$.properties.way_tags.ref'))
            FROM rail_feature_groups g JOIN features f ON f.id=g.feature_id
            WHERE g.group_id >= 'ST-' AND g.group_id < 'ST.'""")
        for group, section, name, ref in rows:
            record = stations.get(group)
            if not record or not section:
                continue
            station = (record['station_name'], tuple(sorted(record.get('provinces', []))))
            value = tracks[station].setdefault(section, {'names': set(), 'refs': set()})
            if name and not name.startswith('未命名'):
                value['names'].add(name)
            if ref:
                value['refs'].add(str(ref))
    changes = {}
    for (station, _province), sections in tracks.items():
        used = set()
        for section, values in sections.items():
            saved = overrides.get('object:section_id:' + section, {})
            for text in (*values['refs'], *values['names'], str(saved.get('track_number', '')),
                         saved.get('display_name', '')):
                match = re.search(r'(\d+)(?:股道|道|\s*$)', text)
                if match:
                    used.add(int(match[1]))
        number = 1
        for section, values in sorted(sections.items()):
            key = 'object:section_id:' + section
            if overrides.get(key, {}).get('display_name') or values['names']:
                continue
            ref = next(iter(values['refs'])) if len(values['refs']) == 1 else None
            if ref:
                label = f'{station} · {ref}' + ('' if ref.endswith('道') else '股道')
            else:
                while number in used:
                    number += 1
                used.add(number)
                label = f'{station} · 第{number}股道（暂编）'
            changes[key] = {'display_name': label, 'source': 'osm_track_ref' if ref else 'automatic_yard_track_number',
                            'snapshot': snapshot, 'version': 1, 'verification_status': 'source_named' if ref else 'automatic_reference',
                            'confidence': None, 'track_number': ref or number}
    changes[marker] = {'snapshot': snapshot, 'version': 1}
    return changes
