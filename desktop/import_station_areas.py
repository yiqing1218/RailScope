"""Extract real OSM subway station polygons, including relation multipolygons.

Uses native filters before Python iteration. No buffers or inferred outlines.
Reuses the downloaded national PBF; preserves the previous derived output.
"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import subprocess
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from railscope.services.importers.metro import is_metro_station_area_tags
from railscope.services.importers.native_paths import native_path, temporary_directory


def extract(pbf, output, previous=None):
    import osmium

    pool = osmium.io.ThreadPool(2, 4)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    index = native_path(output.parent / f".station-area-{uuid4().hex}.idx", output=True)
    factory = osmium.geom.GeoJSONFactory()
    features, errors = [], []
    stations_path = output.parent / "china_metro_stations.geojson"
    known_stations = (
        json.loads(stations_path.read_text(encoding="utf-8"))["features"]
        if stations_path.exists()
        else []
    )
    by_station = {s["properties"]["osm_node_id"]: s for s in known_stations}
    members = defaultdict(set)
    # Membership catches platforms whose POI is outside the polygon or whose
    # own tags omit subway=yes. Never infer a footprint from a stop node.
    relations = (
        osmium.FileProcessor(str(native_path(pbf)), entities=osmium.osm.RELATION, thread_pool=pool)
        .with_filter(
            osmium.filter.TagFilter(
                ("public_transport", "stop_area"),
            )
        )
    )
    for relation in relations:
        station_ids = {
            m.ref for m in relation.members if m.type == "n" and m.ref in by_station
        }
        for member in relation.members:
            if member.type == "w" and (
                relation.tags.get("public_transport") == "stop_area"
                or member.role.startswith("platform")
            ):
                members["way", member.ref].update(station_ids)
            elif member.type == "r":
                members["relation", member.ref].update(station_ids)
    try:
        from .metro_data import associate_station_areas, station_area_index
        from .station_search import name_keys, station_names
    except ImportError:
        from metro_data import associate_station_areas, station_area_index
        from station_search import name_keys, station_names
    association_index = station_area_index(known_stations)
    known_names = {key for station in known_stations for key in name_keys(station["properties"])}
    # Select source IDs before expensive polygon assembly. Generic buildings
    # are candidates only with a station alias or explicit stop_area membership.
    candidates = {"way": set(), "relation": set()}
    required_ways = set()
    objects = (osmium.FileProcessor(str(native_path(pbf)), entities=osmium.osm.WAY | osmium.osm.RELATION, thread_pool=pool)
        .with_filter(osmium.filter.KeyFilter("railway", "public_transport", "building", "station", "subway", "railway:station")))
    for obj in objects:
        raw = dict(obj.tags)
        kind = "way" if obj.is_way() else "relation"
        transport = (is_metro_station_area_tags(raw) or raw.get("railway") in ("station", "platform")
                     or raw.get("public_transport") in ("station", "platform")
                     or raw.get("building") in ("train_station", "transportation"))
        named_building = bool(raw.get("building") and name_keys(raw) & known_names)
        member_building = bool(raw.get("building") and members.get((kind, obj.id)))
        if transport or named_building or member_building:
            candidates[kind].add(obj.id)
            if kind == "relation":
                required_ways.update(member.ref for member in obj.members if member.type == "w")
    required_ways.update(candidates["way"])
    required_nodes = set()
    def selected_objects(entity, ids):
        return (osmium.FileProcessor(str(native_path(pbf)), entities=entity, thread_pool=pool)
                .with_filter(osmium.filter.IdFilter(ids)))
    for way in selected_objects(osmium.osm.WAY, required_ways):
        required_nodes.update(node.ref for node in way.nodes)
    # Area assembly sees only reference-complete station candidates. This
    # avoids indexing every national OSM node in RAM or a mapped location file.
    subset = index.with_suffix(".osm.pbf")
    with osmium.SimpleWriter(str(subset), thread_pool=pool) as writer:
        for node in selected_objects(osmium.osm.NODE, required_nodes):
            writer.add_node(node)
        for way in selected_objects(osmium.osm.WAY, required_ways):
            writer.add_way(way)
        for relation in selected_objects(osmium.osm.RELATION, candidates["relation"]):
            writer.add_relation(relation)
    subset_counts = {"nodes": len(required_nodes), "ways": len(required_ways), "relations": len(candidates["relation"])}
    del required_nodes, required_ways
    locations = osmium.index.create_map(f"sparse_file_array,{index}")
    processor = (osmium.FileProcessor(str(subset), thread_pool=pool)
        .with_locations(locations)
        .with_areas(osmium.filter.IdFilter(candidates["relation"]))
        .with_filter(osmium.filter.EntityFilter(osmium.osm.AREA))
        .with_filter(osmium.filter.IdFilter([number * 2 for number in candidates["way"]]
                                          + [number * 2 + 1 for number in candidates["relation"]])))
    snapshot = {"file": Path(pbf).name, "bytes": Path(pbf).stat().st_size,
                "mtime_ns": Path(pbf).stat().st_mtime_ns}
    try:
        for area in processor:
            raw = dict(area.tags)
            platform = (
                raw.get("railway") == "platform"
                or raw.get("public_transport") == "platform"
            )
            explicit = raw.get("subway") in ("yes", "true") or raw.get("railway:station") == "subway" or raw.get("station") in (
                "subway",
                "light_rail",
            )
            if raw.get("train") == "yes" and not explicit:
                continue
            try:
                geometry = json.loads(factory.create_multipolygon(area))
            except (RuntimeError, ValueError) as error:
                errors.append({"osm_id": area.orig_id(), "reason": str(error)})
                continue
            kind = "way" if area.from_way() else "relation"
            feature = {
                "type": "Feature",
                "properties": {
                    f"osm_{kind}_id": area.orig_id(),
                    "name": raw.get("name") or raw.get("name:zh") or "未命名地铁站区",
                    "station_area_tags": raw,
                    "boundary_kind": "platform"
                    if platform
                    else "station_building"
                    if raw.get("building")
                    else "station_outline",
                    "source": "OpenStreetMap",
                    "geometry_source": "osm_polygon",
                    "verification_status": "osm_derived" if explicit else "needs_review",
                    "source_snapshot": snapshot,
                    "aliases": station_names(raw),
                    "attribution": "© OpenStreetMap contributors",
                    "license": "ODbL 1.0",
                    "member_station_ids": sorted(members.get((kind, area.orig_id()), set())),
                },
                "geometry": geometry,
            }
            associate_station_areas([feature], [], index=association_index)
            if not explicit and not is_metro_station_area_tags(raw):
                if not feature["properties"].get("associated_station_ids") and not feature["properties"].get("association_candidate_station_ids"):
                    continue
                feature["properties"]["mode_source"] = "空间关联已知地铁站，待人工复核"
            features.append(feature)
    finally:
        area = None
        locations.clear()
        del processor
        del locations
        import gc

        gc.collect()
        subset.unlink(missing_ok=True)
        try:
            index.unlink(missing_ok=True)
        except PermissionError:
            # Native area assembly retains the mapping until process exit on Windows.
            pass
    old = (
        json.loads(output.read_text(encoding="utf-8"))
        if output.exists()
        else {"features": []}
    )
    if (
        previous
        and Path(previous).exists()
        and Path(previous).resolve() != output.resolve()
    ):
        retained = json.loads(Path(previous).read_text(encoding="utf-8")).get(
            "features", []
        )
        old["features"].extend(
            {**f, "properties": {**f["properties"], "retained_previous_snapshot": True}}
            for f in retained
            if f.get("geometry", {}).get("type") in ("Polygon", "MultiPolygon")
            and f.get("properties", {}).get("source") == "OpenStreetMap"
            and (
                "osm_way_id" in f["properties"] or "osm_relation_id" in f["properties"]
            )
        )
    keys = {
        (f["properties"].get("osm_way_id"), f["properties"].get("osm_relation_id"))
        for f in features
    }
    # Retain older real boundaries not represented in this PBF snapshot.
    old_unique = {}
    for f in old["features"]:
        key = (
            f["properties"].get("osm_way_id"),
            f["properties"].get("osm_relation_id"),
        )
        old_unique.setdefault(key, f)
    features.extend(
        f
        for f in old_unique.values()
        if (f["properties"].get("osm_way_id"), f["properties"].get("osm_relation_id"))
        not in keys
    )
    for feature in features:
        props = feature["properties"]
        kind = "way" if "osm_way_id" in props else "relation"
        props["member_station_ids"] = sorted(members.get((kind, props.get("osm_" + kind + "_id")), set()))
        props.setdefault("source_snapshot", {"file": "previous_snapshot", "verification_status": "unverified"})
    associate_station_areas(features, [], index=association_index)
    if not features:
        raise RuntimeError("没有获取到有效的真实站区多边形，旧数据未改动")
    backup = None
    if output.exists():
        backup = output.with_name(
            output.name
            + "."
            + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            + ".bak"
        )
        shutil.copy2(output, backup)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {"type": "FeatureCollection", "features": features}, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    temporary.replace(output)
    report = {
        "source": str(pbf),
        "areas": len(features),
        "association_version": "station-area-v2",
        "assembly_subset": subset_counts,
        "source_snapshot": snapshot,
        "station_nodes": len(known_stations),
        "associated_station_nodes": len({node for f in features for node in f["properties"].get("associated_station_ids", [])}),
        "unresolved_areas": sum(not f["properties"].get("associated_station_ids") for f in features),
        "types": dict(
            Counter(
                "way" if "osm_way_id" in f["properties"] else "relation"
                for f in features
            )
        ),
        "invalid_geometries": errors,
        "backup": str(backup) if backup else None,
        "temporary_index": str(index) if index.exists() else None,
        "notice": "真实 OSM 站台、站区与车站建筑多边形；非显式地铁标注的空间关联候选待复核，不推测未绘制边界",
    }
    output.with_name("china_metro_station_area_manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pbf", type=Path, default=ROOT / "data/raw/osm/china-latest.osm.pbf"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data/processed/osm/china_metro_station_areas.geojson",
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--previous", type=Path)
    args = parser.parse_args()
    if not args.worker:
        # A separate process releases Windows native memory-mapped indexes.
        native_cache = temporary_directory(args.pbf)
        subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:], "--worker"],
            check=True,
            env={**os.environ, "RAILSCOPE_NATIVE_TEMP": str(native_cache)},
        )
        manifest = args.output.with_name("china_metro_station_area_manifest.json")
        report = json.loads(manifest.read_text(encoding="utf-8"))
        if report.get("temporary_index"):
            index = Path(report["temporary_index"]).resolve()
            if (
                index.parent
                not in (args.output.resolve().parent, native_cache.resolve())
                or (
                    index.parent == args.output.resolve().parent
                    and not index.name.startswith(".station-area-")
                )
                or index.suffix != ".idx"
            ):
                raise RuntimeError("临时索引路径不合法，未删除")
            index.unlink(missing_ok=True)
            report["temporary_index"] = None
            temporary = manifest.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(manifest)
        for alias in native_cache.iterdir():
            if alias.is_file() and alias.suffix in (".pbf", ".osm", ".idx"):
                alias.unlink()
        raise SystemExit(0)
    print("正在提取全国 OSM 真实地铁站区（含多面关系）…", flush=True)
    print(
        json.dumps(
            extract(args.pbf, args.output, args.previous), ensure_ascii=False, indent=2
        )
    )
