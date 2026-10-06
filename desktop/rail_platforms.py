"""Display-only platform deduplication; every original source remains stored."""
from copy import deepcopy


def platform_display(features):
    from shapely.geometry import shape
    from shapely.strtree import STRtree
    rows = [deepcopy(f) for f in features]
    for feature in rows:
        if feature['geometry']['type'] == 'LineString':
            feature.setdefault('properties', {})['geometry_status'] = '来源仅有站台线，缺少真实站台面'
    polygons = [(i, shape(f['geometry'])) for i, f in enumerate(rows)
                if f['geometry']['type'] in ('Polygon', 'MultiPolygon')]
    polygons = [(i, g) for i, g in polygons if g.is_valid and g.area > 0]
    if not polygons:
        return rows
    tree = STRtree([g for _, g in polygons])
    hidden = set()
    # Prefer current snapshot and an explicit train label over retained geometry.
    priority = lambda i: (bool(rows[i]['properties'].get('retained_previous_snapshot')),
                          rows[i]['properties'].get('way_tags', {}).get('train') != 'yes', i)
    for index in sorted(range(len(polygons)), key=lambda k: priority(polygons[k][0])):
        i, geometry = polygons[index]
        if i in hidden:
            continue
        props = rows[i]['properties']
        for candidate in tree.query(geometry, predicate='intersects'):
            j, other = polygons[int(candidate)]
            if i == j or j in hidden or priority(j) < priority(i):
                continue
            peer = rows[j]['properties']
            # Floors and separate station owners must never be merged.
            if props.get('way_tags', {}).get('layer') != peer.get('way_tags', {}).get('layer'):
                continue
            if set(map(str, props.get('associated_station_ids', []))) != set(map(str, peer.get('associated_station_ids', []))):
                continue
            if geometry.intersection(other).area / geometry.union(other).area < .85:
                continue
            source = peer.get('infrastructure_id') or 'way/' + str(peer.get('osm_way_id'))
            props.setdefault('display_duplicate_source_ids', []).append(source)
            hidden.add(j)
    return [f for i, f in enumerate(rows) if i not in hidden]
