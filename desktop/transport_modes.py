"""Shared transport predicates for legacy display and future OSM imports."""

from contextlib import closing
from functools import lru_cache
import json
from pathlib import Path
import sqlite3


def other_transport(tags):
    urban = (tags.get('station') in ('subway', 'light_rail', 'monorail')
             or tags.get('railway:station') == 'subway'
             or any(tags.get(key) in ('yes', 'true') for key in ('subway', 'light_rail', 'tram', 'monorail')))
    return urban or tags.get('train') == 'no' or (tags.get('train') != 'yes' and (
        tags.get('highway') in ('platform', 'bus_stop')
        or any(tags.get(key) == 'yes' for key in ('bus', 'trolleybus', 'ferry'))))


def metro_source_ids(path):
    if not path or not Path(path).is_file():
        return frozenset()
    path = Path(path).resolve()
    return _metro_source_ids(str(path), path.stat().st_mtime_ns, path.stat().st_size)


@lru_cache(maxsize=2)
def _metro_source_ids(path, modified, size):
    result = set()
    with closing(sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True)) as db:
        for (raw,) in db.execute("SELECT data FROM features WHERE kind IN ('stations','areas')"):
            props = json.loads(raw).get('properties', {})
            for kind in ('node', 'way', 'relation'):
                value = props.get('osm_' + kind + '_id')
                if value is not None:
                    result.add((kind, str(value)))
            for member in props.get('source_member_ids', []):
                parts = str(member).replace(':', '/').split('/')
                if len(parts) >= 2 and parts[-2] in ('node', 'way', 'relation'):
                    result.add((parts[-2], parts[-1]))
    return frozenset(result)


def rail_display_predicate(db, metro_database):
    """SQL filters all station assets before the viewport row budget."""
    ids = metro_source_ids(metro_database)
    db.execute('CREATE TEMP TABLE metro_source_ids(kind TEXT,id TEXT,PRIMARY KEY(kind,id))')
    db.executemany('INSERT INTO metro_source_ids VALUES(?,?)', sorted(ids))
    clauses = []
    for kind in ('node', 'way', 'relation'):
        clauses.append(f"NOT EXISTS (SELECT 1 FROM metro_source_ids m WHERE m.kind='{kind}' AND m.id=CAST(json_extract(f.data,'$.properties.osm_{kind}_id') AS TEXT))")
    for field in ('node_tags', 'way_tags', 'station_area_tags'):
        def tag(key):
            return f"coalesce(json_extract(f.data,'$.properties.{field}.\"{key}\"'),'')"
        clauses.extend([
            tag('station') + " NOT IN ('subway','light_rail','monorail')",
            tag('railway:station') + "!='subway'",
            tag('train') + "!='no'",
            '(' + tag('train') + "='yes' OR (" + tag('highway') + " NOT IN ('platform','bus_stop') AND "
            + ' AND '.join(tag(key) + "!='yes'" for key in ('bus', 'trolleybus', 'ferry')) + '))',
        ])
        clauses.extend(tag(key) + " NOT IN ('yes','true')" for key in ('subway', 'light_rail', 'tram', 'monorail'))
    return ' AND '.join(clauses)
