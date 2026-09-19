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

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    index = native_path(output.parent / f".station-area-{uuid4().hex}.idx", output=True)
    tags = osmium.filter.TagFilter(
        ("station", "subway"),
        ("station", "light_rail"),
        ("subway", "yes"),
        ("subway", "true"),
        ("railway:station", "subway"),
        ("railway", "platform"),
        ("public_transport", "platform"),
        ("railway", "station"),
        ("building", "train_station"),
    )
    locations = osmium.index.create_map(f"sparse_file_array,{index}")
    processor = (
        osmium.FileProcessor(str(native_path(pbf)))
        .with_locations(locations)
        .with_areas(tags)
        .with_filter(osmium.filter.EntityFilter(osmium.osm.AREA))
        .with_filter(tags)
    )
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
        osmium.FileProcessor(str(native_path(pbf)))
        .with_filter(osmium.filter.EntityFilter(osmium.osm.RELATION))
        .with_filter(
            osmium.filter.TagFilter(
                ("public_transport", "stop_area"),
                ("route", "subway"),
                ("route", "light_rail"),
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
            elif member.type == "r" and member.role.startswith("platform"):
                members["relation", member.ref].update(station_ids)
    station_grid = defaultdict(list)
    for station in known_stations:
        x, y = station["geometry"]["coordinates"]
        station_grid[int(x / 0.005), int(y / 0.005)].append(station)
    try:
        from .metro_data import associate_station_areas
    except ImportError:
        from metro_data import associate_station_areas
    try:
        for area in processor:
            raw = dict(area.tags)
            platform = (
                raw.get("railway") == "platform"
                or raw.get("public_transport") == "platform"
            )
            explicit = raw.get("subway") in ("yes", "true") or raw.get("station") in (
                "subway",
                "light_rail",
            )
            if (
                not is_metro_station_area_tags(raw)
                and not platform
                and raw.get("building") != "train_station"
                and raw.get("railway") != "station"
            ):
                continue
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
                    if raw.get("building") == "train_station"
                    else "station_outline",
                    "source": "OpenStreetMap",
                    "geometry_source": "osm_polygon",
                    "verification_status": "osm_derived",
                    "attribution": "© OpenStreetMap contributors",
                    "license": "ODbL 1.0",
                    "member_station_ids": sorted(members[kind, area.orig_id()]),
                },
                "geometry": geometry,
            }
            associate_station_areas([feature], [], grid=station_grid)
            if not explicit and not is_metro_station_area_tags(raw):
                if not feature["properties"].get("route_relation_ids"):
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
