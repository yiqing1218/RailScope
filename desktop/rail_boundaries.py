"""Real national station/platform polygons and shared, source-stable identities."""

import argparse
import gc
from collections import Counter, defaultdict
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

try:
    from .geometry import distance_m
    from .data_install import active_directory
    from .transport_modes import other_transport
    from .rail_station_directory import compatible_area
    from .rail_station_types import TAG_TYPES, station_point_kind
except ImportError:
    from geometry import distance_m
    from data_install import active_directory
    from transport_modes import other_transport
    from rail_station_directory import compatible_area
    from rail_station_types import TAG_TYPES, station_point_kind

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from railscope.services.importers.native_paths import native_path  # noqa: E402


def polygon_points(geometry):
    if geometry['type'] == 'LineString':
        return geometry['coordinates']
    polygons = (
        geometry["coordinates"]
        if geometry["type"] == "MultiPolygon"
        else [geometry["coordinates"]]
    )
    return [point for polygon in polygons for ring in polygon for point in ring]


def classify_stations(stations, highspeed_edges):
    """Spatial high-speed candidates are evidence hints, not verified platform ownership."""
    grid = defaultdict(list)
    for edge in highspeed_edges:
        for coordinate in edge["coordinates"]:
            grid[int(coordinate[0] / 0.02), int(coordinate[1] / 0.02)].append(
                coordinate
            )
    for station in stations:
        props = station["properties"]
        coordinate = station["geometry"]["coordinates"]
        x, y = int(coordinate[0] / 0.02), int(coordinate[1] / 0.02)
        nearby = [
            p
            for a in range(x - 1, x + 2)
            for b in range(y - 1, y + 2)
            for p in grid[a, b]
        ]
        gap = min((distance_m(coordinate, p) for p in nearby), default=float("inf"))
        explicit = props.get("node_tags", {}).get("highspeed") == "yes"
        props["facility_class"] = (
            "OSM 明确标注高铁站"
            if explicit
            else "邻近高铁轨道的车站候选（待复核）"
            if gap <= 1500
            else "铁路车站（高铁属性未判定）"
        )
        props["highspeed_distance_m"] = round(gap, 1) if gap != float("inf") else None
    return stations


def associate(features, stations):
    from shapely.geometry import shape
    from shapely.strtree import STRtree
    try:
        from .station_search import rail_name_key
    except ImportError:
        from station_search import rail_name_key
    by_node = {s['properties'].get('osm_node_id', s['properties'].get('station_source_id')):s for s in stations}
    by_name = defaultdict(list)
    for s in stations:
        name = rail_name_key(s['properties'].get('name', ''))
        if name: by_name[name].append(s)
    grid = defaultdict(list)
    for station in stations:
        x, y = station["geometry"]["coordinates"]
        grid[int(x / 0.02), int(y / 0.02)].append(station)
    outlines = []
    outline_shapes = []
    tree = None
    # Resolve real station footprints first; platforms can inherit their owner.
    ordered = sorted(features, key=lambda f: (f['properties'].get('boundary_kind') == 'platform', f['properties']['infrastructure_id']))
    for feature in ordered:
        props = feature["properties"]
        points = polygon_points(feature["geometry"])
        xs, ys = zip(*points)
        center = [(min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2]
        x, y = int(center[0] / 0.02), int(center[1] / 0.02)
        nearby = [
            s
            for a in range(x - 1, x + 2)
            for b in range(y - 1, y + 2)
            for s in grid[a, b]
            if compatible_area(props, s['properties'])
        ]
        nearest = min(
            nearby,
            key=lambda s: (
                distance_m(center, s["geometry"]["coordinates"]),
                str(s['properties'].get('osm_node_id', s['properties'].get('station_source_id'))),
            ),
            default=None,
        )
        source_id = props["infrastructure_id"]
        evidence = '空间关联真实铁路车站，待复核'
        confidence = .5
        explicit = {node for node in props.get('member_station_ids', []) if node in by_node}
        if source_id in by_node and props.get('boundary_kind') != 'platform':
            nearest = by_node[source_id]
            evidence, confidence = 'same_osm_source_area', 1.0
        elif len(explicit) == 1:
            nearest = by_node[next(iter(explicit))]
            evidence, confidence = 'osm_stop_area_membership', .95
        elif len(explicit) > 1:
            nearest = None  # multiple explicit owners cannot become an arbitrary nearest station
        elif props.get('boundary_kind') != 'platform' and len(by_name.get(rail_name_key(props.get('source_name','')), [])) == 1:
            nearest = by_name[rail_name_key(props['source_name'])][0]
            evidence, confidence = 'unique_source_station_name', .8
        elif props.get('boundary_kind') == 'platform' and outlines:
            if tree is None: tree = STRtree(outline_shapes)
            geometry = shape(feature['geometry'])
            candidates = {owner for index in tree.query(geometry, predicate='intersects')
                          for owner in outlines[int(index)]['properties'].get('associated_station_ids', [])}
            if len(candidates) == 1 and next(iter(candidates)) in by_node:
                nearest = by_node[next(iter(candidates))]
                evidence, confidence = 'real_station_outline_intersection', .8
            else:
                blockers = [outlines[int(index)] for index in tree.query(geometry, predicate='intersects')
                            if outlines[int(index)]['properties'].get('source_name')]
                if blockers:
                    nearest = None
                    props['association_candidate_station_ids'] = sorted({f['properties']['infrastructure_id'] for f in blockers})
        if nearest and (evidence != '空间关联真实铁路车站，待复核' or distance_m(center, nearest["geometry"]["coordinates"]) <= 1200):
            node = nearest['properties'].get('osm_node_id', nearest['properties'].get('station_source_id'))
            name = nearest["properties"]["name"]
            props["station_id"] = nearest['properties'].get('station_source_id') or "node/" + str(node)
            props["station_id"] = nearest["properties"].get("station_id") or props["station_id"]
            props["associated_station_ids"] = [node]
            props["association_source"] = evidence
            props["association_verification_status"] = "automatic_match"
            props["association_confidence"] = confidence
            props["facility_class"] = nearest["properties"].get(
                "facility_class", "铁路车站（高铁属性未判定）"
            )
        else:
            name = props.get("source_name") or "未关联铁路车站"
            props["associated_station_ids"] = sorted(explicit, key=str)
            props.pop('station_id', None)
            props["association_source"] = "尚未关联车站"
            props["association_verification_status"] = "unresolved"
            props["association_confidence"] = None
        kind = props["boundary_kind"]
        ref = props.get("way_tags", {}).get("ref") or props.get("way_tags", {}).get("local_ref")
        label = (
            "站台 " + (str(ref) if ref else "未标号")
            if kind == "platform"
            else "车站建筑"
            if kind == "station_building"
            else "站区"
        )
        props["name"] = f"{name} · {label} · {source_id}"
        if kind in ('station_outline', 'station_building'):
            geometry = shape(feature['geometry'])
            if geometry.is_valid:
                outlines.append(feature); outline_shapes.append(geometry); tree = None
    return features


def stop_area_members(pbf, stations):
    """Actual relation membership, including nested stop-area groups."""
    import osmium
    known = {s['properties'].get('osm_node_id') for s in stations} - {None}
    nodes, ways, children = {}, {}, {}
    processor = (osmium.FileProcessor(str(native_path(Path(pbf).resolve())), entities=osmium.osm.RELATION)
                 .with_filter(osmium.filter.TagFilter(('public_transport','stop_area'), ('public_transport','stop_area_group'))))
    for relation in processor:
        nodes[relation.id] = {m.ref for m in relation.members if m.type == 'n' and m.ref in known}
        ways[relation.id] = [m.ref for m in relation.members if m.type == 'w']
        children[relation.id] = [m.ref for m in relation.members if m.type == 'r']
    def owners(ident, visited=None):
        visited = set() if visited is None else visited
        if ident in visited: return set()
        visited.add(ident)
        result = set(nodes.get(ident, ()))
        for child in children.get(ident, ()): result.update(owners(child, visited))
        return result
    result = defaultdict(set)
    for ident in nodes:
        members = owners(ident)
        for way in ways[ident]: result['way',way].update(members)
        for child in children[ident]: result['relation',child].update(members)
    return result


def extract(pbf, directory, progress=print):
    import osmium

    directory = Path(directory).resolve()
    source = directory / "rail.sqlite"
    try:
        from .rail_line_store import fingerprint, refresh_station_index
    except ImportError:
        from rail_line_store import fingerprint, refresh_station_index
    previous_signature = fingerprint(source, [])
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as db:
        stations = [
            json.loads(raw)
            for (raw,) in db.execute(
                "SELECT data FROM features WHERE kind='railPoints' AND json_extract(data,'$.properties.kind') IN ('station','halt','yard','depot','workshop','works','engine_shed')"
            )
        ]
        old = [
            json.loads(raw)
            for (raw,) in db.execute(
                "SELECT data FROM features WHERE kind IN ('railPlatforms','railStationAreas')"
            )
        ]
        known_point_nodes = {node for (node,) in db.execute(
            "SELECT json_extract(data,'$.properties.osm_node_id') FROM features WHERE kind='railPoints'")}
    progress('检查铁路站区关系中的真实站台成员…')
    memberships = stop_area_members(pbf, stations)
    tags = osmium.filter.TagFilter(
        ("railway", "station"),
        ("railway", "halt"),
        ("railway", "platform"),
        ("public_transport", "platform"),
        ("building", "train_station"),
        ('railway', 'yard'), ('railway', 'depot'), ('railway', 'workshop'),
        ('railway', 'works'), ('railway', 'engine_shed'), ('landuse', 'railway'),
        ('railway', 'service_station'), ('building', 'depot'), ('building', 'service_station'),
        *(("railway:facility", value) for value in TAG_TYPES),
    )
    index_path = directory / ".rail-boundary-locations.idx"
    locations = osmium.index.create_map(
        f"sparse_file_array,{native_path(index_path, output=True)}"
    )
    processor = (
        osmium.FileProcessor(str(native_path(Path(pbf).resolve())))
        .with_locations(locations)
        .with_areas(tags)
        .with_filter(osmium.filter.EntityFilter(osmium.osm.AREA))
        .with_filter(tags)
    )
    factory, features, invalid = osmium.geom.GeoJSONFactory(), [], []
    facility_points = []
    try:
        from .catalog_metadata import station_type
        from .station_search import rail_name_key
    except ImportError:
        from catalog_metadata import station_type
        from station_search import rail_name_key
    # Recover explicit facility POIs omitted by older imports. Keep every raw
    # node and all operational references; this only adds missing map owners.
    known_nodes = known_point_nodes
    progress('补查铁路站场设施点位…')
    for node in (osmium.FileProcessor(str(native_path(Path(pbf).resolve())), entities=osmium.osm.NODE)
                 .with_filter(osmium.filter.KeyFilter('railway', 'railway:facility', 'landuse', 'building'))):
        raw = dict(node.tags)
        point_kind = station_point_kind(raw)
        if not point_kind or node.id in known_nodes or other_transport(raw) or not node.location.valid():
            continue
        source_id = f'node/{node.id}'
        label = station_type(raw, point_kind)
        feature = {'type': 'Feature', 'properties': {
            'osm_node_id': node.id, 'station_source_id': source_id, 'kind': point_kind,
            'name': raw.get('name') or raw.get('name:zh') or f'未命名{label}（{source_id}）',
            'node_tags': raw, 'source': 'OpenStreetMap', 'source_member_ids': [source_id],
            'geometry_source': 'osm_node', 'verification_status': 'osm_derived',
            'snapshot_id': str(Path(pbf).stat().st_mtime_ns)},
            'geometry': {'type': 'Point', 'coordinates': [node.location.lon, node.location.lat]}}
        stations.append(feature)
        facility_points.append(feature)
        known_nodes.add(node.id)
    metro_path = active_directory(ROOT) / "china_metro_station_areas.geojson"
    metro_platforms = set()
    if metro_path.exists():
        for feature in json.loads(metro_path.read_text(encoding="utf-8"))["features"]:
            props = feature["properties"]
            if props.get("boundary_kind") == "platform" and props.get(
                "member_station_ids"
            ):
                kind = "way" if "osm_way_id" in props else "relation"
                metro_platforms.add((kind, props.get("osm_" + kind + "_id")))
    progress("提取全国真实铁路站区、建筑与站台多边形（包括多面关系）…")
    try:
        for area in processor:
            raw = dict(area.tags)
            if other_transport(raw):
                continue
            facility_type = station_type(raw, raw.get('railway',''))
            if raw.get('landuse') == 'railway' and not raw.get('railway') and facility_type == '未定义':
                continue
            try:
                geometry = json.loads(factory.create_multipolygon(area))
                if (
                    geometry["type"] == "MultiPolygon"
                    and len(geometry["coordinates"]) == 1
                ):
                    geometry = {
                        "type": "Polygon",
                        "coordinates": geometry["coordinates"][0],
                    }
            except (RuntimeError, ValueError) as error:
                invalid.append({"id": area.orig_id(), "reason": str(error)})
                continue
            kind = "way" if area.from_way() else "relation"
            platform = (
                raw.get("railway") == "platform"
                or raw.get("public_transport") == "platform"
            )
            if platform and (kind, area.orig_id()) in metro_platforms:
                continue
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        f"osm_{kind}_id": area.orig_id(),
                        "infrastructure_id": f"{kind}/{area.orig_id()}",
                        "source_name": raw.get("name", raw.get("name:zh", "")),
                        "way_tags": raw,
                        "boundary_kind": "platform"
                        if platform
                        else "station_building"
                        if raw.get("building") == "train_station"
                        else "station_outline",
                        "mode_verified": raw.get("train") == "yes" or not platform,
                        "source": "OpenStreetMap",
                        "geometry_source": "osm_polygon",
                        "verification_status": "osm_derived",
                        "license": "ODbL 1.0",
                        'snapshot_id': str(Path(pbf).stat().st_mtime_ns),
                        'member_station_ids': sorted(memberships.get((kind, area.orig_id()), ())),
                    },
                    "geometry": geometry,
                }
            )
            area_name = raw.get('name') or raw.get('name:zh')
            if not platform and (station_point_kind(raw) or (area_name and raw.get('railway') == 'station')):
                from shapely.geometry import shape
                point = shape(geometry).representative_point()
                coordinate = [point.x, point.y]
                existing = [s for s in stations if area_name and rail_name_key(s['properties'].get('name','')) == rail_name_key(area_name)
                            and distance_m(s['geometry']['coordinates'], coordinate) <= 3000]
                if not existing:
                    source_id = f'{kind}/{area.orig_id()}'
                    point_kind = station_point_kind(raw) or 'yard'
                    feature = {'type':'Feature', 'properties':{'station_source_id':source_id,
                        'infrastructure_id':source_id, 'kind':point_kind,
                        'name':area_name or f'未命名{facility_type}（{source_id}）',
                        'node_tags':raw, 'source':'OpenStreetMap', 'source_member_ids':[source_id],
                        'geometry_source':'osm_area_representative_point', 'verification_status':'osm_derived',
                        'snapshot_id':str(Path(pbf).stat().st_mtime_ns)},
                        'geometry':{'type':'Point','coordinates':coordinate}}
                    stations.append(feature);facility_points.append(feature)
    finally:
        area = None
        locations.clear()
        del processor, locations
        gc.collect()
        try:
            index_path.unlink(missing_ok=True)
        except PermissionError:
            # Windows may retain a native mmap until process exit; the next run reuses this file.
            pass
    # Preserve previously extracted real polygons absent from this snapshot.
    identities = {f["properties"]["infrastructure_id"] for f in features}
    for feature in old:
        props = feature["properties"]
        if other_transport(props.get("way_tags", {})):
            continue
        kind = "way" if "osm_way_id" in props else "relation"
        ident = (
            props.get("infrastructure_id")
            or f"{kind}/{props.get('osm_' + kind + '_id')}"
        )
        if (kind, props.get("osm_" + kind + "_id")) in metro_platforms:
            continue
        if (
            feature["geometry"]["type"] not in ("Polygon", "MultiPolygon", "LineString")
            or ident in identities
            or ident.endswith("/None")
        ):
            continue
        props.update(
            infrastructure_id=ident,
            source_name=props.get("source_name", props.get("name", "")),
            boundary_kind=props.get("boundary_kind", "platform"),
            retained_previous_snapshot=True,
            member_station_ids=sorted(memberships.get((kind, props.get('osm_' + kind + '_id')), ())),
        )
        features.append(feature)
        identities.add(ident)
    associate(features, stations)
    covered = defaultdict(lambda: set())
    for feature in features:
        for node in feature["properties"].get("associated_station_ids", []):
            covered[node].add(feature["properties"]["boundary_kind"])
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as db:
        classify_stations(
            stations,
            (
                json.loads(raw)
                for (raw,) in db.execute(
                    "SELECT data FROM edges WHERE json_extract(data,'$.way_tags.highspeed')='yes' AND json_extract(data,'$.construction')=0"
                )
            ),
        )
    station_lookup = {s['properties'].get('osm_node_id',s['properties'].get('station_source_id')): s for s in stations}
    for feature in features:
        associated = feature["properties"].get("associated_station_ids", [])
        # Names and shared station IDs are unchanged; classification never splits a station.
        if associated:
            feature["properties"]["facility_class"] = station_lookup[associated[0]][
                "properties"
            ]["facility_class"]
    coverage = [
        {
            "station_id": s['properties'].get('station_source_id') or "node/" + str(s["properties"]["osm_node_id"]),
            "name": s["properties"]["name"],
            "facility_class": s["properties"].get("facility_class"),
            "highspeed_distance_m": s["properties"].get("highspeed_distance_m"),
            "boundary_types": sorted(covered[s['properties'].get('osm_node_id',s['properties'].get('station_source_id'))]),
            "status": "已有真实多边形（关联待复核）"
            if covered[s['properties'].get('osm_node_id',s['properties'].get('station_source_id'))]
            else "OSM 未获取到真实面，需补充合法来源",
        }
        for s in stations
    ]
    # One transaction: no edge or rail point is rewritten; source identities stay shared.
    with closing(sqlite3.connect(source)) as db:
        with db:
            ids = [
                row[0]
                for row in db.execute(
                    "SELECT id FROM features WHERE kind IN ('railPlatforms','railStationAreas')"
                )
            ]
            db.executemany("DELETE FROM bounds WHERE id=?", ((i,) for i in ids))
            db.executemany("DELETE FROM features WHERE id=?", ((i,) for i in ids))
            number = db.execute("SELECT coalesce(max(id),0) FROM features").fetchone()[
                0
            ]
            existing_facility_points = defaultdict(list)
            for ident, source_id in db.execute("SELECT id,json_extract(data,'$.properties.station_source_id') FROM features "
                                               "WHERE kind='railPoints' AND json_extract(data,'$.properties.station_source_id') IS NOT NULL"):
                existing_facility_points[source_id].append(ident)
            for feature in facility_points:
                source_id = feature['properties']['station_source_id']
                old_ids = existing_facility_points.get(source_id, [])
                for ident in old_ids:
                    db.execute('DELETE FROM bounds WHERE id=?',(ident,));db.execute('DELETE FROM features WHERE id=?',(ident,))
                number += 1
                x,y = feature['geometry']['coordinates']
                db.execute('INSERT INTO features VALUES(?,?,?,?)',(number,'railPoints','main',json.dumps(feature,ensure_ascii=False)))
                db.execute('INSERT INTO bounds VALUES(?,?,?,?,?)',(number,x,x,y,y))
            for feature in features:
                number += 1
                points = polygon_points(feature["geometry"])
                xs, ys = zip(*points)
                kind = (
                    "railPlatforms"
                    if feature["properties"]["boundary_kind"] == "platform"
                    else "railStationAreas"
                )
                db.execute(
                    "INSERT INTO features VALUES(?,?,?,?)",
                    (number, kind, "main", json.dumps(feature, ensure_ascii=False)),
                )
                db.execute(
                    "INSERT INTO bounds VALUES(?,?,?,?,?)",
                    (number, min(xs), max(xs), min(ys), max(ys)),
                )
    report = {
        "source": str(Path(pbf).resolve()),
        'facility_points': len(facility_points),
        "polygons": len(features),
        "platform_polygons": sum(
            f["properties"]["boundary_kind"] == "platform" for f in features
        ),
        "station_polygons": sum(
            f["properties"]["boundary_kind"] != "platform" for f in features
        ),
        "stations": len(stations),
        "covered_stations": sum(bool(r["boundary_types"]) for r in coverage),
        "facility_classes": dict(Counter(r["facility_class"] for r in coverage)),
        "invalid": invalid,
        "notice": "全体铁路站区/站台，非全部已验证高铁设施；关联与高铁类别需原始证据复核，不制造缺失面。",
    }
    outputs = [
        ("rail_boundary_manifest.json", report),
        ("rail_boundary_coverage.json", coverage),
    ]
    for kind, filename in [
        ("platform", "rail_platforms.geojson"),
        ("station", "rail_station_areas.geojson"),
    ]:
        subset = [
            f
            for f in features
            if (f["properties"]["boundary_kind"] == "platform") == (kind == "platform")
        ]
        outputs.append((filename, {"type": "FeatureCollection", "features": subset}))
    for name, value in outputs:
        target = directory / name
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(target)
    # A new source snapshot also needs fresh mode evidence for untagged depots.
    try:
        from .rail_transport_context import build_transport_context
    except ImportError:
        from desktop.rail_transport_context import build_transport_context
    build_transport_context(directory, pbf, progress=progress)
    refresh_station_index(source, directory / 'rail_lines.sqlite', previous_signature, progress)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pbf", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(extract(args.pbf, args.directory), ensure_ascii=False, indent=2))
