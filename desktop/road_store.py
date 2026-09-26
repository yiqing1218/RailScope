"""Disk-backed national motorway catalog and viewport, derived from OSM."""

from contextlib import closing
import gc
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from uuid import uuid4

try:
    from .provinces import ProvinceIndex
except ImportError:
    from provinces import ProvinceIndex


REFERENCE = re.compile(r"(?<![A-Z0-9])([GS])\s*(\d{1,5}[A-Z]?)(?![A-Z0-9])", re.I)
VIEWPORT_LIMIT = 12000


def database_path(root):
    return Path(root) / "data/processed/roads/roads.sqlite"


def classify_motorway(tags, provinces, way_id):
    """Classify explicit G/S refs only; preserve incomplete OSM records."""
    references = list(dict.fromkeys(
        (letter.upper() + number.upper())
        for letter, number in REFERENCE.findall(tags.get("ref", ""))
    ))
    name = tags.get("name:zh") or tags.get("name") or ""
    result = []
    for ref in references:
        if ref.startswith("G"):
            result.append((f"G/{ref}", "national", "", ref, name))
        else:
            for province in provinces:
                result.append((f"S/{province}/{ref}", "provincial", province, ref, name))
    if not result:
        for province in provinces:
            label = name or "未标号高速路段"
            result.append((f"U/{province}/{label}", "unresolved", province, "", label))
    return result


def national_group(ref):
    """UI grouping: radial/longitudinal/transverse routes vs regional routes.

    Regional rings (G91--G99), connectors, parallel routes and city rings
    belong in the latter group. This is a browsing taxonomy, not a change to
    the official source classification or ref.
    """
    match = re.fullmatch(r"G(\d{1,4})([A-Z]?)", str(ref).upper())
    if not match:
        return "国家高速 · 待核对"
    number = int(match.group(1))
    if number <= 0 or len(match.group(1)) == 3:
        return "国家高速 · 待核对"
    return "国家干线" if len(match.group(1)) <= 2 and number <= 90 and not match.group(2) else "区域线路"


def build_index(pbf, output, progress=None):
    """Stream one PBF into SQLite; publish it only after a complete scan."""
    import osmium

    pbf, output = Path(pbf), Path(output)
    if not pbf.is_file():
        raise FileNotFoundError(f"全国 OSM 快照不存在：{pbf}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + "." + uuid4().hex + ".tmp")
    index = ProvinceIndex()
    snapshot = str(pbf.stat().st_mtime_ns)
    count = 0
    db = sqlite3.connect(temporary)
    db.executescript(
        "CREATE TABLE features(id INTEGER PRIMARY KEY,way_id INTEGER UNIQUE,data TEXT NOT NULL);"
        "CREATE VIRTUAL TABLE bounds USING rtree(id,minx,maxx,miny,maxy);"
        "CREATE TABLE routes(key TEXT PRIMARY KEY,kind TEXT NOT NULL,province TEXT NOT NULL,"
        "ref TEXT NOT NULL,name TEXT NOT NULL,segment_count INTEGER NOT NULL,"
        "minx REAL,miny REAL,maxx REAL,maxy REAL);"
        "CREATE TABLE route_segments(route_key TEXT NOT NULL,feature_id INTEGER NOT NULL,"
        "PRIMARY KEY(route_key,feature_id));"
        "CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);"
    )
    try:
        from .road_services import create_tables, build_services
    except ImportError:
        from road_services import create_tables, build_services
    create_tables(db)

    class Motorways(osmium.SimpleHandler):
        def way(self, way):
            nonlocal count
            construction = (way.tags.get('highway') == 'construction' and way.tags.get('construction') == 'motorway') or way.tags.get('construction:highway') == 'motorway'
            if way.tags.get("highway") != "motorway" and not construction:
                return
            coordinates = []
            for node in way.nodes:
                if not node.location.valid():
                    return
                coordinates.append([node.location.lon, node.location.lat])
            if len(coordinates) < 2:
                return
            tags = dict(way.tags)
            provinces = index.along(coordinates)
            routes = classify_motorway(tags, provinces, int(way.id))
            if construction:
                routes = [(key + '/construction', kind, province, ref, name) for key, kind, province, ref, name in routes]
            count += 1
            xs, ys = zip(*coordinates)
            minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
            feature = {
                "type": "Feature",
                "properties": {
                    "osm_way_id": int(way.id),
                    "name": tags.get("name:zh") or tags.get("name") or tags.get("ref") or "未命名高速路段",
                    "ref": tags.get("ref", ""),
                    "highway": "motorway",
                    "construction": construction,
                    "construction_status": "construction" if construction else "operating",
                    "source_tags": tags,
                    "snapshot": snapshot,
                    "verification_status": "source_unverified",
                    "road_class": "national" if any(entry[1] == "national" for entry in routes) else routes[0][1],
                    "route_keys": [entry[0] for entry in routes],
                    "provinces": provinces,
                    "source": "OpenStreetMap",
                    "license": "ODbL 1.0",
                    "attribution": "© OpenStreetMap contributors",
                },
                "geometry": {"type": "LineString", "coordinates": coordinates},
            }
            db.execute("INSERT INTO features VALUES(?,?,?)", (
                count, int(way.id), json.dumps(feature, ensure_ascii=False, separators=(",", ":"))
            ))
            db.execute("INSERT INTO bounds VALUES(?,?,?,?,?)", (count, minx, maxx, miny, maxy))
            for key, kind, province, ref, name in routes:
                db.execute(
                    "INSERT INTO routes VALUES(?,?,?,?,?,1,?,?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET "
                    "segment_count=segment_count+1,"
                    "name=CASE WHEN routes.name='' THEN excluded.name ELSE routes.name END,"
                    "minx=min(routes.minx,excluded.minx),miny=min(routes.miny,excluded.miny),"
                    "maxx=max(routes.maxx,excluded.maxx),maxy=max(routes.maxy,excluded.maxy)",
                    (key, kind, province, ref, name, minx, miny, maxx, maxy),
                )
                db.execute("INSERT INTO route_segments VALUES(?,?)", (key, count))
            if count % 1000 == 0:
                db.commit()
                if progress:
                    progress(count)

    try:
        # libosmium needs node locations for way geometry. Keep its index on
        # disk rather than materialising all national nodes in Python memory.
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
        from railscope.services.importers.native_paths import native_path

        native_source = native_path(pbf)
        location_index = native_path(temporary.with_suffix(".idx"), output=True)
        handler = Motorways()
        try:
            handler.apply_file(
                str(native_source), locations=True,
                idx=f"sparse_file_array,{location_index}",
            )
            if progress:
                progress('正在提取服务区 POI、真实轮廓和建筑…')
            build_services(pbf, db, index, location_index, output.parent.parent / 'admin/admin.sqlite')
        finally:
            del handler
            gc.collect()
            try:
                Path(location_index).unlink(missing_ok=True)
            except PermissionError:
                pass  # The worker process releases a lingering native mapping on exit.
        if count == 0:
            raise ValueError("OSM 快照中未找到 highway=motorway 路段")
        stat = pbf.stat()
        db.executemany("INSERT INTO metadata VALUES(?,?)", [
            ("source", str(pbf)), ("source_size", str(stat.st_size)),
            ("source_mtime_ns", str(stat.st_mtime_ns)), ("feature_count", str(count)),
        ])
        db.execute("CREATE INDEX route_segments_feature ON route_segments(feature_id)")
        db.commit()
        db.close()
        # Publish complete directory tables with the snapshot: active Qt models
        # may query immediately, before the import-finished callback refreshes.
        sync_directory(temporary)
        try:
            from .road_services import sync_directory as sync_services
        except ImportError:
            from road_services import sync_directory as sync_services
        sync_services(temporary)
        for attempt in range(10):
            try:
                temporary.replace(output)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.25)
    except Exception:
        db.close()
        try:
            temporary.unlink(missing_ok=True)
        except PermissionError:
            pass  # Preserve the original error; Windows can retain a native mapping.
        raise
    if progress:
        progress(count)
    return count


def routes(path, query=""):
    if not Path(path).is_file():
        return []
    with closing(sqlite3.connect(path)) as db:
        rows = db.execute(
            "SELECT key,kind,province,ref,name,segment_count,minx,miny,maxx,maxy "
            "FROM routes WHERE ref LIKE ? OR name LIKE ? OR province LIKE ? "
            "ORDER BY kind,province,ref,name",
            (f"%{query}%", f"%{query}%", f"%{query}%"),
        )
        return [dict(zip(("key", "kind", "province", "ref", "name", "segment_count",
                          "minx", "miny", "maxx", "maxy"), row)) for row in rows]


def route(path, key):
    with closing(sqlite3.connect(path)) as db:
        row = db.execute(
            "SELECT key,kind,province,ref,name,segment_count,minx,miny,maxx,maxy "
            "FROM routes WHERE key=?", (key,),
        ).fetchone()
    fields = ("key", "kind", "province", "ref", "name", "segment_count", "minx", "miny", "maxx", "maxy")
    return dict(zip(fields, row)) if row else None


def sync_directory(path):
    """Build a small derived folder index once per road snapshot."""
    if not Path(path).is_file():
        return
    with closing(sqlite3.connect(path)) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS directory_nodes(
                id TEXT PRIMARY KEY,parent_id TEXT NOT NULL,label TEXT NOT NULL,
                kind TEXT NOT NULL,object_id TEXT,path TEXT NOT NULL,
                child_count INTEGER NOT NULL,total INTEGER NOT NULL,archived INTEGER NOT NULL,
                province TEXT,city TEXT,object_type TEXT,name TEXT,line_id TEXT,station_id TEXT);
            CREATE INDEX IF NOT EXISTS road_directory_parent ON directory_nodes(parent_id,label);
            CREATE INDEX IF NOT EXISTS road_directory_object ON directory_nodes(object_id);
            CREATE INDEX IF NOT EXISTS road_directory_province ON directory_nodes(province);
            CREATE INDEX IF NOT EXISTS road_directory_type_name ON directory_nodes(object_type,name);
            CREATE INDEX IF NOT EXISTS road_directory_archived ON directory_nodes(archived);
            CREATE INDEX IF NOT EXISTS road_directory_line ON directory_nodes(line_id);
            CREATE INDEX IF NOT EXISTS road_directory_station ON directory_nodes(station_id);
        """)
        count = db.execute("SELECT count(*) FROM routes").fetchone()[0]
        existing = db.execute("SELECT value FROM metadata WHERE key='directory_route_count'").fetchone()
        version = db.execute("SELECT value FROM metadata WHERE key='directory_version'").fetchone()
        if existing and int(existing[0]) == count and version and version[0] == "3":
            return
        db.execute("DELETE FROM directory_nodes")
        folders = {}
        for key, kind, province, ref, name, segments in db.execute(
            "SELECT key,kind,province,ref,name,segment_count FROM routes ORDER BY key"
        ):
            if kind == "national":
                labels = ["国家高速", national_group(ref)]
            elif kind == "provincial":
                labels = ["省级高速", province]
            else:
                labels = ["待核对高速", province or "地区待核对"]
            parent = ""
            for depth in range(1, len(labels) + 1):
                parts = labels[:depth]
                folder_key = "folder:" + json.dumps(parts, ensure_ascii=False)
                entry = folders.setdefault(folder_key, [parent, parts[-1], json.dumps(parts, ensure_ascii=False), 0])
                entry[3] += 1
                parent = folder_key
            label = ' · '.join(dict.fromkeys(value for value in (ref, name) if value)) or "未命名高速"
            if key.endswith('/construction'):
                label += '（在建）'
            db.execute(
                "INSERT INTO directory_nodes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("object:" + key, parent, f"{label} · {segments:,} 段", "object", key,
                 json.dumps(labels, ensure_ascii=False), 0, 1, 0, labels[0],
                 labels[1], "motorway", label, key, None),
            )
        for folder_key, (parent, label, encoded, total) in folders.items():
            labels = json.loads(encoded)
            db.execute(
                "INSERT INTO directory_nodes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (folder_key, parent, label, "folder", None, encoded, 0, total, 0,
                 labels[0], labels[1] if len(labels) > 1 else "", "folder", label, None, None),
            )
        db.execute("UPDATE directory_nodes SET child_count=(SELECT count(*) FROM directory_nodes c WHERE c.parent_id=directory_nodes.id) WHERE kind='folder'")
        db.execute("INSERT OR REPLACE INTO metadata VALUES('directory_route_count',?)", (str(count),))
        db.execute("INSERT OR REPLACE INTO metadata VALUES('directory_version','3')")
        db.commit()


def viewport(path, bbox, route_key=None, limit=VIEWPORT_LIMIT, route_keys=None, zoom=None):
    if not Path(path).is_file():
        return {"type": "FeatureCollection", "features": [], "truncated": False}
    west, south, east, north = bbox
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("视窗无效")
    if type(limit) is not int or not 1 <= limit <= 100000:
        raise ValueError("视窗上限无效")
    if zoom is not None and (not math.isfinite(zoom) or not 0 <= zoom <= 24):
        raise ValueError('缩放级别无效')
    if route_keys is not None and (not isinstance(route_keys, list) or len(route_keys) > 100000
                                   or any(not isinstance(key, str) for key in route_keys)):
        raise ValueError('高速线路选择过大')
    if route_keys == [] and not route_key:
        return {"type": "FeatureCollection", "features": [], "truncated": False}
    def overview():
        try:
            from .road_overview import viewport as overview_viewport
        except ImportError:
            from road_overview import viewport as overview_viewport
        return overview_viewport(path, bbox, zoom, route_key, route_keys)
    if zoom is not None and zoom <= 9:
        return overview()
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA cache_size=-2048")
        if route_keys is not None and route_key is None:
            selected = list(dict.fromkeys(str(value) for value in route_keys))
            if len(selected) > 100000:
                raise ValueError("高速线路选择过大")
            if not selected:
                return {"type": "FeatureCollection", "features": [], "truncated": False}
            db.execute("CREATE TEMP TABLE chosen_routes(key TEXT PRIMARY KEY)")
            db.executemany("INSERT OR IGNORE INTO chosen_routes VALUES(?)", ((value,) for value in selected))
            rows = db.execute(
                "SELECT f.data FROM bounds b JOIN features f ON f.id=b.id "
                "WHERE b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=? "
                "AND EXISTS(SELECT 1 FROM route_segments r JOIN chosen_routes c ON c.key=r.route_key "
                "WHERE r.feature_id=f.id) LIMIT ?",
                (west, east, south, north, limit + 1),
            )
        elif route_key:
            rows = db.execute(
                "SELECT f.data FROM route_segments r JOIN features f ON f.id=r.feature_id "
                "JOIN bounds b ON b.id=f.id WHERE r.route_key=? "
                "AND b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=? LIMIT ?",
                (route_key, west, east, south, north, limit + 1),
            )
        else:
            rows = db.execute(
                "SELECT f.data FROM features f JOIN bounds b ON b.id=f.id "
                "WHERE b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=? LIMIT ?",
                (west, east, south, north, limit + 1),
            )
        values = [json.loads(raw) for (raw,) in rows]
    if len(values) > limit and zoom is not None:
        return overview()
    return {"type": "FeatureCollection", "features": values[:limit],
            "truncated": len(values) > limit}
