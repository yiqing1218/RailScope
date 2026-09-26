"""Local administrative boundaries: one streamed import, bounded viewport reads.

Chinese OSM levels 4/5/6 are province/city/county (special regions use 3).
See https://wiki.openstreetmap.org/wiki/China/Boundaries . The source geometry is
reference data, not an official boundary determination. Nothing is added to
the startup config or national Python object collections.
"""

from contextlib import closing
import gc
import json
import math
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

LEVELS = {4: "省级", 5: "市级", 6: "县级"}


def database_path(root):
    return Path(root) / "data/processed/admin/admin.sqlite"


def _simplify_ring(points, tolerance):
    """Radial prefilter then iterative RDP, keeping closed rings and holes."""
    if len(points) <= 4:
        return points
    squared = tolerance * tolerance
    radial = [points[0]]
    for point in points[1:-1]:
        if sum((point[i] - radial[-1][i]) ** 2 for i in (0, 1)) >= squared:
            radial.append(point)
    radial.append(points[-1])
    keep = {0, len(radial) - 1}
    stack = [(0, len(radial) - 1)]
    while stack:
        start, end = stack.pop()
        a, b = radial[start], radial[end]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = dx * dx + dy * dy
        furthest, distance = None, squared
        for i in range(start + 1, end):
            x, y = radial[i]
            t = max(0, min(1, ((x-a[0])*dx + (y-a[1])*dy)/length)) if length else 0
            d = (x-a[0]-t*dx)**2 + (y-a[1]-t*dy)**2
            if d > distance:
                furthest, distance = i, d
        if furthest is not None:
            keep.add(furthest)
            stack.extend(((start, furthest), (furthest, end)))
    result = [radial[i] for i in sorted(keep)]
    return result if len(result) >= 4 else points


def build_index(pbf, output, progress=None):
    import osmium
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    from railscope.services.importers.native_paths import native_path

    pbf, output = Path(pbf), Path(output)
    stat = pbf.stat()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + "." + uuid4().hex + ".tmp")
    location_index = native_path(temporary.with_suffix(".idx"), output=True)
    count = 0
    skipped = 0
    processor = None
    db = sqlite3.connect(temporary)
    db.executescript("""
        CREATE TABLE features(id INTEGER PRIMARY KEY,level INTEGER NOT NULL,
            coarse TEXT NOT NULL,medium TEXT NOT NULL,detail TEXT NOT NULL);
        CREATE INDEX admin_level ON features(level);
        CREATE VIRTUAL TABLE bounds USING rtree(id,minx,maxx,miny,maxy);
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    """)
    try:
        if progress:
            progress("正在扫描 OSM 行政关系并建立磁盘坐标索引…")
        # The first-pass filters restrict relation assembly to administrative
        # polygons. Node coordinates live in a disk index, never a Python dict.
        boundary = osmium.filter.TagFilter(("boundary", "administrative"))
        levels = osmium.filter.TagFilter(*[("admin_level", str(n)) for n in (3, 4, 5, 6)])
        processor = (osmium.FileProcessor(str(native_path(pbf)))
                     .with_locations(f"sparse_file_array,{location_index}")
                     .with_areas(boundary, levels)
                     .with_filter(osmium.filter.EntityFilter(osmium.osm.AREA))
                     .with_filter(boundary).with_filter(levels))
        factory = osmium.geom.GeoJSONFactory()
        for area in processor:
            if area.from_way():
                continue  # Administrative relations are the canonical objects.
            try:
                geometry = json.loads(factory.create_multipolygon(area))
            except RuntimeError:
                # Some extract relations have incomplete/invalid rings.
                # Keep valid areas and report the omissions, never invent a polygon.
                skipped += 1
                continue
            polygons = geometry["coordinates"]
            points = [p for polygon in polygons for ring in polygon for p in ring]
            if not points:
                continue
            minx = min(p[0] for p in points)
            maxx = max(p[0] for p in points)
            miny = min(p[1] for p in points)
            maxy = max(p[1] for p in points)
            source_level = int(area.tags["admin_level"])
            name = area.tags.get("name:zh") or area.tags.get("name") or "未命名行政区"
            if source_level == 3 and not name.startswith(("香港", "澳门", "澳門")):
                continue
            level = 4 if source_level == 3 else source_level
            props = {"name": name,
                     "admin_level": level, "source_admin_level": source_level, "osm_relation_id": area.orig_id(),
                     "source": "OpenStreetMap", "snapshot": str(stat.st_mtime_ns),
                     "verification_status": "source_unverified"}
            variants = []
            for tolerance in (.015, .003, .0003):
                simplified = [[_simplify_ring(ring, tolerance) for ring in polygon] for polygon in polygons]
                variants.append(json.dumps({"type": "Feature", "id": area.orig_id(),
                    "properties": props, "geometry": {"type": "MultiPolygon", "coordinates": simplified}},
                    ensure_ascii=False, separators=(",", ":")))
            db.execute("INSERT INTO features VALUES(?,?,?,?,?)", (area.orig_id(), level, *variants))
            db.execute("INSERT INTO bounds VALUES(?,?,?,?,?)", (area.orig_id(), minx, maxx, miny, maxy))
            count += 1
            if count % 100 == 0:
                db.commit()
                if progress:
                    progress(f"已建立 {count:,} 个行政区边界")
        if not count:
            raise ValueError("快照未包含可组装的省、市、县行政边界关系")
        db.executemany("INSERT INTO metadata VALUES(?,?)", [
            ("source", pbf.name), ("source_size", str(stat.st_size)),
            ("source_mtime_ns", str(stat.st_mtime_ns)), ("count", str(count)),
            ("skipped_invalid_areas", str(skipped)),
        ])
        db.commit()
        db.close()
        temporary.replace(output)
    except Exception:
        db.close()
        temporary.unlink(missing_ok=True)
        raise
    finally:
        del processor
        gc.collect()
        try:
            Path(location_index).unlink(missing_ok=True)
        except PermissionError:
            pass  # Windows releases a lingering native mapping at worker exit.
    if progress:
        progress(f"行政边界索引完成：{count:,} 个行政区；跳过 {skipped} 个不完整边界")
    return count


def viewport(path, bbox, level=4, zoom=4):
    if level not in LEVELS or len(bbox) != 4 or not all(math.isfinite(v) for v in (*bbox, zoom)):
        raise ValueError("行政边界查询参数无效")
    west, south, east, north = bbox
    if not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90):
        raise ValueError("行政边界范围无效")
    result = {"type": "FeatureCollection", "features": [], "level": level}
    if not Path(path).is_file():
        return {**result, "missing": True}
    column = "coarse" if zoom < 7 else "medium" if zoom < 10 else "detail"
    size = 0
    with closing(sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)) as db:
        selected = "f.level=?"
        if level == 5:
            # Municipalities have no intermediate prefectural polygon.
            selected = "(f.level=? OR (f.level=4 AND json_extract(f.coarse,'$.properties.name') IN ('北京市','天津市','上海市','重庆市')))"
        rows = db.execute(f"SELECT f.{column} FROM bounds b JOIN features f ON f.id=b.id "
                          f"WHERE b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=? AND {selected} "
                          "ORDER BY f.id LIMIT 4001", (west, east, south, north, level))
        for (data,) in rows:
            size += len(data)
            if len(result["features"]) >= 4000 or size > 8_000_000:
                result["truncated"] = True
                break
            result["features"].append(json.loads(data))
    return result
