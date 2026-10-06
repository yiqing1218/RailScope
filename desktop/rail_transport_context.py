"""Source-based facility mode evidence, separate from imported geometry."""
from collections import defaultdict
from contextlib import closing
import json
from pathlib import Path


def classify_facility_tracks(features, tracks, snapshot=None):
    from shapely.geometry import shape
    from shapely.strtree import STRtree
    from .geometry import distance_m
    areas = [(f['properties']['infrastructure_id'], shape(f['geometry'])) for f in features]
    areas = [(key, g) for key, g in areas if g.is_valid and not g.is_empty]
    tree = STRtree([g for _, g in areas])
    lengths = defaultdict(lambda: {'urban': 0., 'rail': 0.})
    members = defaultdict(lambda: {'urban': [], 'rail': []})
    for track in tracks:
        props = track['properties']; tags = props.get('way_tags', {})
        railway = tags.get('railway')
        mode = 'urban' if railway in ('subway','light_rail','tram','monorail') else 'rail' if railway == 'rail' else None
        if mode is None:
            continue
        line = shape(track['geometry'])
        if line.is_empty or line.length == 0:
            continue
        coords = track['geometry']['coordinates']
        length = sum(distance_m(a,b) for a,b in zip(coords,coords[1:]))
        for index in tree.query(line, predicate='intersects'):
            key, area = areas[int(index)]
            share = area.intersection(line).length / line.length
            # An approach/crossing does not identify a depot. Most of this
            # physical source way must actually lie inside its real boundary.
            if share < .8 or length * share < 25:
                continue
            lengths[key][mode] += length * share
            members[key][mode].append(props['osm_way_id'])
    result = {}
    for key, _ in areas:
        rail, urban = lengths[key]['rail'], lengths[key]['urban']
        mode = 'urban' if urban >= 50 and rail < 25 else 'rail' if rail >= 50 and urban < 25 else 'mixed' if rail >= 25 and urban >= 25 else 'unknown'
        result[key] = {'mode': mode, 'source': 'osm_tracks_inside_real_facility_boundary',
            'snapshot': snapshot, 'version': 1, 'verification_status': 'automatic_reference',
            'confidence': .9 if mode in ('urban','rail') else None,
            'track_length_m': {k: round(v,1) for k,v in lengths[key].items()},
            'source_way_ids': {k: sorted(set(v)) for k,v in members[key].items()}}
    return result


def build_transport_context(directory, pbf, location_index=None, progress=print):
    import osmium
    from .rail_station_types import station_type, FACILITY_TYPES
    from .persistence import write_json_atomic
    from railscope.services.importers.native_paths import native_path
    directory, pbf = Path(directory), Path(pbf)
    with closing(__import__('sqlite3').connect((directory/'rail.sqlite').resolve().as_uri()+'?mode=ro', uri=True)) as db:
        features = [json.loads(raw) for (raw,) in db.execute("SELECT data FROM features WHERE kind='railStationAreas'")]
    features = [f for f in features if station_type(f['properties'].get('way_tags', {})) in FACILITY_TYPES]
    snapshot = {'file': pbf.name, 'size': pbf.stat().st_size, 'mtime_ns': pbf.stat().st_mtime_ns}
    progress(f'逐一核查 {len(features)} 个场段真实边界内的轨道制式…')
    if not features:
        write_json_atomic(directory/'rail_transport_context.json', {'version':1,'snapshot':snapshot,'facilities':{}})
        return {}
    index_path = Path(location_index) if location_index else directory/'.rail-mode-locations.idx'
    locations = osmium.index.create_map(f'sparse_file_array,{native_path(index_path,output=True)}')
    processor = (osmium.FileProcessor(str(native_path(pbf.resolve()))).with_locations(locations)
                 .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
                 .with_filter(osmium.filter.TagFilter(*[('railway', k) for k in ('rail','subway','light_rail','tram','monorail')])))
    def tracks():
        count = 0
        for way in processor:
            try:
                coords = [[n.lon,n.lat] for n in way.nodes]
            except (RuntimeError, osmium.InvalidLocationError):
                continue
            if len(coords) < 2:
                continue
            yield {'properties': {'osm_way_id': way.id, 'way_tags': dict(way.tags)},
                   'geometry': {'type': 'LineString','coordinates': coords}}
            count += 1
            if count % 50000 == 0:
                progress(f'已核查 {count:,} 条来源轨道…')
    try:
        facilities = classify_facility_tracks(features, tracks(), snapshot)
    finally:
        locations.clear()
        del processor, locations
        if not location_index:
            import gc
            gc.collect()
            try:
                index_path.unlink(missing_ok=True)
            except PermissionError:
                # Pyosmium may retain its Windows mmap until process exit.
                # Reuse the derived location index on the next import.
                pass
    write_json_atomic(directory/'rail_transport_context.json', {'version': 1, 'snapshot': snapshot,
                      'facilities': facilities})
    progress(f'完成：{sum(r["mode"] == "urban" for r in facilities.values())} 个城市轨道场段从国铁目录排除；原始设施和轨道保留。')
    return facilities
