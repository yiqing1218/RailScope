"""Extract real OSM subway station polygons, including relation multipolygons.

Uses native filters before Python iteration. No buffers or inferred outlines.
Reuses the downloaded national PBF; preserves the previous derived output.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import subprocess
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from railscope.services.importers.metro import is_metro_station_area_tags


def extract(pbf, output):
    import osmium

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    index = output.parent / f".station-area-{uuid4().hex}.idx"
    tags = osmium.filter.TagFilter(
        ("station", "subway"),
        ("station", "light_rail"),
        ("subway", "yes"),
        ("subway", "true"),
        ("railway:station", "subway"),
    )
    locations = osmium.index.create_map(f"sparse_file_array,{index}")
    processor = (
        osmium.FileProcessor(str(pbf))
        .with_locations(locations)
        .with_areas(tags)
        .with_filter(osmium.filter.EntityFilter(osmium.osm.AREA))
        .with_filter(tags)
    )
    factory = osmium.geom.GeoJSONFactory()
    features, errors = [], []
    try:
        for area in processor:
            raw = dict(area.tags)
            if not is_metro_station_area_tags(raw):
                continue
            try:
                geometry = json.loads(factory.create_multipolygon(area))
            except (RuntimeError, ValueError) as error:
                errors.append({"osm_id": area.orig_id(), "reason": str(error)})
                continue
            kind = "way" if area.from_way() else "relation"
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        f"osm_{kind}_id": area.orig_id(),
                        "name": raw.get("name")
                        or raw.get("name:zh")
                        or "未命名地铁站区",
                        "station_area_tags": raw,
                        "source": "OpenStreetMap",
                        "attribution": "© OpenStreetMap contributors",
                        "license": "ODbL 1.0",
                    },
                    "geometry": geometry,
                }
            )
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
    if not features:
        raise RuntimeError("没有获取到有效的真实站区多边形，旧数据未改动")
    old = (
        json.loads(output.read_text(encoding="utf-8"))
        if output.exists()
        else {"features": []}
    )
    keys = {
        (f["properties"].get("osm_way_id"), f["properties"].get("osm_relation_id"))
        for f in features
    }
    # Retain older real boundaries not represented in this PBF snapshot.
    features.extend(
        f
        for f in old["features"]
        if (f["properties"].get("osm_way_id"), f["properties"].get("osm_relation_id"))
        not in keys
    )
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
        "notice": "只包含 OSM 已绘制且明确标注为地铁/轻轨车站的真实面，不推测未绘制的范围",
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
    args = parser.parse_args()
    if not args.worker:
        # A separate process releases Windows native memory-mapped indexes.
        subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:], "--worker"],
            check=True,
        )
        manifest = args.output.with_name("china_metro_station_area_manifest.json")
        report = json.loads(manifest.read_text(encoding="utf-8"))
        if report.get("temporary_index"):
            index = Path(report["temporary_index"]).resolve()
            if (
                index.parent != args.output.resolve().parent
                or not index.name.startswith(".station-area-")
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
        raise SystemExit(0)
    print("正在提取全国 OSM 真实地铁站区（含多面关系）…", flush=True)
    print(json.dumps(extract(args.pbf, args.output), ensure_ascii=False, indent=2))
