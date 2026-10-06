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
    facility = tags.get('railway') in ('yard', 'depot', 'workshop', 'works', 'engine_shed') or tags.get('landuse') == 'railway'
    text = ' '.join(str(tags.get(key, '')) for key in ('name', 'name:zh', 'name:en', 'operator', 'network')).casefold()
    urban |= facility and (tags.get('depot') in ('subway', 'light_rail', 'tram', 'monorail')
        or any(word in text for word in ('地铁', '地鐵', '港铁', '港鐵', 'metro', 'mtr ', 'subway', 'tram')))
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
        for kind, raw in db.execute("SELECT kind,data FROM features WHERE kind IN ('stations','areas')"):
            props = json.loads(raw).get('properties', {})
            # Unresolved spatial candidates in old metro snapshots are not
            # evidence that a national-rail platform belongs to metro.
            tags = props.get('station_area_tags', props.get('way_tags', {}))
            if kind == 'areas' and not (other_transport(tags) or props.get('member_station_ids')):
                continue
            for kind in ('node', 'way', 'relation'):
                value = props.get('osm_' + kind + '_id')
                if value is not None:
                    result.add((kind, str(value)))
            for member in props.get('source_member_ids', []):
                parts = str(member).replace(':', '/').split('/')
                if len(parts) >= 2 and parts[-2] in ('node', 'way', 'relation'):
                    result.add((parts[-2], parts[-1]))
    return frozenset(result)


def rail_display_predicate(db, metro_database, directory=None):
    """SQL filters all station assets before the viewport row budget."""
    ids = metro_source_ids(metro_database)
    db.execute('CREATE TEMP TABLE metro_source_ids(kind TEXT,id TEXT,PRIMARY KEY(kind,id))')
    db.executemany('INSERT INTO metro_source_ids VALUES(?,?)', sorted(ids))
    if directory:
        for source in urban_facility_ids(directory):
            kind, _, ident = source.partition('/')
            db.execute('INSERT OR IGNORE INTO metro_source_ids VALUES(?,?)', (kind, ident))
    clauses = []
    for kind in ('node', 'way', 'relation'):
        clauses.append(f"NOT EXISTS (SELECT 1 FROM metro_source_ids m WHERE m.kind='{kind}' AND m.id=CAST(json_extract(f.data,'$.properties.osm_{kind}_id') AS TEXT))")
    for field in ('station_source_id', 'infrastructure_id'):
        clauses.append("NOT EXISTS (SELECT 1 FROM metro_source_ids m WHERE m.kind||'/'||m.id="
                       + f"json_extract(f.data,'$.properties.{field}'))")
    for field in ('node_tags', 'way_tags', 'station_area_tags'):
        def tag(key):
            return f"coalesce(json_extract(f.data,'$.properties.{field}.\"{key}\"'),'')"
        clauses.extend([
            tag('depot') + " NOT IN ('subway','light_rail','tram','monorail')",
            tag('station') + " NOT IN ('subway','light_rail','monorail')",
            tag('railway:station') + "!='subway'",
            tag('train') + "!='no'",
            '(' + tag('train') + "='yes' OR (" + tag('highway') + " NOT IN ('platform','bus_stop') AND "
            + ' AND '.join(tag(key) + "!='yes'" for key in ('bus', 'trolleybus', 'ferry')) + '))',
        ])
        clauses.extend(tag(key) + " NOT IN ('yes','true')" for key in ('subway', 'light_rail', 'tram', 'monorail'))
        context = '(' + tag('railway') + " IN ('yard','depot','workshop','works','engine_shed') OR " + tag('landuse') + "='railway')"
        for word in ('地铁', '地鐵', '港铁', '港鐵', 'metro', 'mtr ', 'subway', 'tram'):
            text = "lower(" + "||' '||".join(tag(key) for key in ('name','name:zh','name:en','operator','network')) + ")"
            clauses.append('(NOT ' + context + ' OR instr(' + text + ", '" + word + "')=0)")
    return ' AND '.join(clauses)


def urban_facility_ids(directory):
    path = Path(directory) / 'rail_transport_context.json'
    if not path.is_file():
        return frozenset()
    return _urban_facility_ids(str(path.resolve()), path.stat().st_mtime_ns, path.stat().st_size)


@lru_cache(maxsize=4)
def _urban_facility_ids(path, modified, size):
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    return frozenset(key for key, record in payload.get('facilities', {}).items()
                     if record.get('mode') == 'urban' and record.get('verification_status') == 'automatic_reference')
