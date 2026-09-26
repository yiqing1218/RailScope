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
except ImportError:
    from geometry import distance_m
    from data_install import active_directory
    from transport_modes import other_transport

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from railscope.services.importers.native_paths import native_path  # noqa: E402


def polygon_points(geometry):
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
    grid = defaultdict(list)
    for station in stations:
        x, y = station["geometry"]["coordinates"]
        grid[int(x / 0.02), int(y / 0.02)].append(station)
    for feature in sorted(features, key=lambda f: f["properties"]["infrastructure_id"]):
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
        ]
        nearest = min(
            nearby,
            key=lambda s: (
                distance_m(center, s["geometry"]["coordinates"]),
                s["properties"]["osm_node_id"],
            ),
            default=None,
        )
        source_id = props["infrastructure_id"]
        if nearest and distance_m(center, nearest["geometry"]["coordinates"]) <= 1200:
            node = nearest["properties"]["osm_node_id"]
            name = nearest["properties"]["name"]
            props["station_id"] = "node/" + str(node)
            props["station_id"] = nearest["properties"].get("station_id") or props["station_id"]
            props["associated_station_ids"] = [node]
            props["association_source"] = "空间关联真实铁路车站，待复核"
            props["association_verification_status"] = "automatic_match"
            props["association_confidence"] = .5
            props["facility_class"] = nearest["properties"].get(
                "facility_class", "铁路车站（高铁属性未判定）"
            )
        else:
            name = props["source_name"] or "未关联铁路车站"
            props["associated_station_ids"] = []
            props["association_source"] = "尚未关联车站"
            props["association_verification_status"] = "unresolved"
            props["association_confidence"] = None
        kind = props["boundary_kind"]
        ref = props["way_tags"].get("ref") or props["way_tags"].get("local_ref")
        label = (
            "站台 " + (str(ref) if ref else "未标号")
            if kind == "platform"
            else "车站建筑"
            if kind == "station_building"
            else "站区"
        )
        props["name"] = f"{name} · {label} · {source_id}"
    return features


def extract(pbf, directory, progress=print):
    import osmium

    directory = Path(directory).resolve()
    source = directory / "rail.sqlite"
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as db:
        stations = [
            json.loads(raw)
            for (raw,) in db.execute(
                "SELECT data FROM features WHERE kind='railPoints' AND json_extract(data,'$.properties.kind') IN ('station','halt')"
            )
        ]
        old = [
            json.loads(raw)
            for (raw,) in db.execute(
                "SELECT data FROM features WHERE kind IN ('railPlatforms','railStationAreas')"
            )
        ]
    tags = osmium.filter.TagFilter(
        ("railway", "station"),
        ("railway", "halt"),
        ("railway", "platform"),
        ("public_transport", "platform"),
        ("building", "train_station"),
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
                    },
                    "geometry": geometry,
                }
            )
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
            feature["geometry"]["type"] not in ("Polygon", "MultiPolygon")
            or ident in identities
            or ident.endswith("/None")
        ):
            continue
        props.update(
            infrastructure_id=ident,
            source_name=props.get("source_name", props.get("name", "")),
            boundary_kind=props.get("boundary_kind", "platform"),
            retained_previous_snapshot=True,
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
    station_lookup = {s["properties"]["osm_node_id"]: s for s in stations}
    for feature in features:
        associated = feature["properties"].get("associated_station_ids", [])
        # Names and shared station IDs are unchanged; classification never splits a station.
        if associated:
            feature["properties"]["facility_class"] = station_lookup[associated[0]][
                "properties"
            ]["facility_class"]
    coverage = [
        {
            "station_id": "node/" + str(s["properties"]["osm_node_id"]),
            "name": s["properties"]["name"],
            "facility_class": s["properties"].get("facility_class"),
            "highspeed_distance_m": s["properties"].get("highspeed_distance_m"),
            "boundary_types": sorted(covered[s["properties"]["osm_node_id"]]),
            "status": "已有真实多边形（关联待复核）"
            if covered[s["properties"]["osm_node_id"]]
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
                    "SELECT id FROM features WHERE kind IN ('railPlatforms','railStationAreas') AND json_extract(data,'$.geometry.type') IN ('Polygon','MultiPolygon')"
                )
            ]
            db.executemany("DELETE FROM bounds WHERE id=?", ((i,) for i in ids))
            db.executemany("DELETE FROM features WHERE id=?", ((i,) for i in ids))
            number = db.execute("SELECT coalesce(max(id),0) FROM features").fetchone()[
                0
            ]
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
        if kind == "platform":
            # An open platform way is not an outline, but must not disappear from exports.
            subset.extend(f for f in old if f["geometry"]["type"] == "LineString")
        outputs.append((filename, {"type": "FeatureCollection", "features": subset}))
    for name, value in outputs:
        target = directory / name
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(target)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pbf", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(extract(args.pbf, args.directory), ensure_ascii=False, indent=2))
