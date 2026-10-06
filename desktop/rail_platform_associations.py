"""Recompute display association without rewriting OSM-derived geometry."""
from contextlib import closing
from copy import deepcopy
from functools import lru_cache
import json
import hashlib
from pathlib import Path
import sqlite3

FIELDS = ('associated_station_ids', 'station_id', 'association_source',
          'association_verification_status', 'association_confidence',
          'association_candidate_station_ids', 'name', 'facility_class')


def source_fingerprint(feature):
    props = feature.get('properties', {})
    value = [feature.get('geometry'),props.get('way_tags'),props.get('snapshot_id')]
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def association_edits(directory):
    path = Path(directory)/'rail_platform_associations.json'
    if not path.exists():
        return {}
    return _read(str(path.resolve()),path.stat().st_mtime_ns,path.stat().st_size)


@lru_cache(maxsize=4)
def _read(path,modified,size):
    return json.loads(Path(path).read_text(encoding='utf-8')).get('associations', {})


def apply_associations(features, directory):
    edits = association_edits(directory)
    result = []
    for feature in features:
        props = feature.get('properties', {})
        source = props.get('infrastructure_id') or 'way/'+str(props.get('osm_way_id'))
        if source in edits and edits[source].get('source_fingerprint') == source_fingerprint(feature):
            feature = deepcopy(feature)
            for key,value in edits[source]['properties'].items():
                if value is None:
                    feature['properties'].pop(key,None)
                else:
                    feature['properties'][key] = value
            feature['properties']['association_provenance'] = {k:v for k,v in edits[source].items() if k != 'properties'}
        result.append(feature)
    return result


def rebuild_associations(directory):
    from .rail_boundaries import associate
    from .transport_modes import other_transport, urban_facility_ids
    from .persistence import write_json_atomic
    directory = Path(directory)
    source = directory/'rail.sqlite'
    urban = urban_facility_ids(directory)
    with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        features = [json.loads(raw) for (raw,) in db.execute("SELECT data FROM features WHERE kind IN ('railPlatforms','railStationAreas')")]
    with closing(sqlite3.connect((directory/'rail_lines.sqlite').resolve().as_uri()+'?mode=ro',uri=True)) as db:
        stations = [json.loads(raw) for key,raw in db.execute('SELECT source_id,data FROM station_directory') if key not in urban]
    stations = [f for f in stations if not other_transport(f['properties'].get('node_tags', {}))]
    original = {f['properties']['infrastructure_id']: deepcopy(f['properties']) for f in features}
    features = [f for f in features if f['properties']['infrastructure_id'] not in urban and
                not other_transport(f['properties'].get('way_tags', {}))]
    associate(features,stations)
    edits = {}
    for feature in features:
        props = feature['properties']; key = props['infrastructure_id']
        if any(props.get(field) != original[key].get(field) for field in FIELDS):
            edits[key] = {'properties': {field:props.get(field) for field in FIELDS},
                          'source_fingerprint': source_fingerprint(feature),
                          'source': 'real_station_footprint_and_osm_membership',
                          'snapshot': str(source.stat().st_mtime_ns), 'version': 1,
                          'verification_status': props.get('association_verification_status'),
                          'confidence': props.get('association_confidence')}
    write_json_atomic(directory/'rail_platform_associations.json', {'version':1,'associations':edits})
    return edits
