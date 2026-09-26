"""Disk-backed metro geometry and station index.

GeoJSON is an import source. Normal startup and map requests read bounded rows
from SQLite; the source files are rebuilt only when their snapshots change.
"""

from __future__ import annotations

from contextlib import closing
from collections import Counter
from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

try:
    from .metro_data import (
        associate_station_areas,
        display_station_areas,
        display_stations,
        iter_geojson_features,
    )
except ImportError:
    from metro_data import (
        associate_station_areas,
        display_station_areas,
        display_stations,
        iter_geojson_features,
    )

try:
    from .catalog_metadata import _metro_platform_id
except ImportError:
    from catalog_metadata import _metro_platform_id


VERSION = "metro-viewport-v2"
SOURCE_FILES = {
    "metro": "china_metro_routes.geojson",
    "stations": "china_metro_stations.geojson",
    "areas": "china_metro_station_areas.geojson",
    "construction": "china_metro_construction.geojson",
}
EMPTY = {"type": "FeatureCollection", "features": []}


def database_path(directory: Path) -> Path:
    return Path(directory) / "metro.sqlite"


def _snapshot(directory: Path) -> str:
    return json.dumps({
        name: (path.stat().st_size, path.stat().st_mtime_ns) if path.exists() else None
        for name, filename in SOURCE_FILES.items()
        for path in [Path(directory) / filename]
    }, sort_keys=True)


def _bounds(coordinates):
    values = []

    def visit(part):
        if not part:
            return
        if isinstance(part[0], (int, float)):
            values.append(part)
        else:
            for child in part:
                visit(child)

    visit(coordinates)
    if not values:
        return None, 0
    xs = [point[0] for point in values]
    ys = [point[1] for point in values]
    return (min(xs), max(xs), min(ys), max(ys)), len(values)


def _write_feature(db, kind, feature):
    geometry = feature.get("geometry") or {}
    bounds, vertices = _bounds(geometry.get("coordinates"))
    if bounds is None:
        return
    data = json.dumps(feature, ensure_ascii=False, separators=(",", ":"))
    props = feature.get("properties") or {}
    cursor = db.execute(
        "INSERT INTO features(kind,name,vertices,data) VALUES(?,?,?,?)",
        (kind, str(props.get("name") or ""), vertices, data),
    )
    ident = cursor.lastrowid
    db.execute("INSERT INTO bounds VALUES(?,?,?,?,?)", (ident, *bounds))
    for relation in props.get("route_relation_ids", []):
        db.execute("INSERT OR IGNORE INTO relations VALUES(?,?)", (ident, int(relation)))
    relation = props.get("route_relation_id")
    if relation is not None:
        db.execute("INSERT OR IGNORE INTO relations VALUES(?,?)", (ident, int(relation)))
    if kind == "construction":
        way_id = props.get("osm_way_id")
        if way_id is not None:
            db.execute("INSERT OR IGNORE INTO relations VALUES(?,?)", (ident, int(way_id)))
    if kind == "stations":
        station_id = str(props.get("infrastructure_id") or props.get("station_id") or "")
        if station_id:
            db.execute("INSERT OR IGNORE INTO station_links VALUES(?,?)", (ident, station_id))
        for relation in props.get("route_relation_ids", []):
            alias = _metro_platform_id(props, station_id, relation)
            db.execute("INSERT OR IGNORE INTO station_links VALUES(?,?)", (ident, alias))
            db.execute(
                "INSERT OR REPLACE INTO station_aliases VALUES(?,?,?,?,?,?,?,?)",
                (alias, station_id, int(relation), str(props.get("name") or "未命名地铁站"),
                 float(geometry["coordinates"][0]), float(geometry["coordinates"][1]),
                 json.dumps(props, ensure_ascii=False, separators=(",", ":")), ident),
            )
    elif kind == "areas":
        for station_id in props.get("station_ids", []):
            db.execute("INSERT OR IGNORE INTO station_links VALUES(?,?)", (ident, str(station_id)))
            for relation in props.get("route_relation_ids", []):
                db.execute("INSERT OR IGNORE INTO station_links VALUES(?,?)", (ident, f"{station_id}@line-{relation}"))


def ensure_index(directory: Path, registry=None) -> Path:
    directory = Path(directory)
    target = database_path(directory)
    snapshot = _snapshot(directory)
    if target.is_file():
        try:
            with closing(sqlite3.connect(target)) as db:
                saved = dict(db.execute("SELECT key,value FROM metadata"))
            if saved.get("version") == VERSION and saved.get("snapshot") == snapshot:
                return target
        except sqlite3.DatabaseError:
            pass
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f"metro.{uuid4().hex}.sqlite.tmp"
    try:
        with closing(sqlite3.connect(temporary)) as db:
            db.executescript("""
                CREATE TABLE features(id INTEGER PRIMARY KEY,kind TEXT NOT NULL,
                    name TEXT NOT NULL,vertices INTEGER NOT NULL,data TEXT NOT NULL);
                CREATE VIRTUAL TABLE bounds USING rtree(id,minx,maxx,miny,maxy);
                CREATE TABLE relations(feature_id INTEGER NOT NULL,relation_id INTEGER NOT NULL,
                    PRIMARY KEY(feature_id,relation_id));
                CREATE TABLE station_links(feature_id INTEGER NOT NULL,station_id TEXT NOT NULL,
                    PRIMARY KEY(feature_id,station_id));
                CREATE TABLE station_aliases(id TEXT NOT NULL,physical_id TEXT NOT NULL,
                    route_id INTEGER NOT NULL,name TEXT NOT NULL,x REAL NOT NULL,y REAL NOT NULL,
                    properties TEXT NOT NULL,feature_id INTEGER NOT NULL,
                    PRIMARY KEY(id,route_id));
                CREATE TABLE route_summary(route_id INTEGER PRIMARY KEY,
                    minx REAL NOT NULL,maxx REAL NOT NULL,miny REAL NOT NULL,maxy REAL NOT NULL,
                    center_x REAL NOT NULL,center_y REAL NOT NULL);
                CREATE TABLE directory_nodes(id TEXT PRIMARY KEY,parent_id TEXT NOT NULL,
                    label TEXT NOT NULL,kind TEXT NOT NULL,object_id TEXT,
                    path TEXT NOT NULL,child_count INTEGER NOT NULL,total INTEGER NOT NULL,
                    archived INTEGER NOT NULL,province TEXT,city TEXT,object_type TEXT,
                    name TEXT,line_id TEXT,station_id TEXT);
                CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE INDEX features_kind_name ON features(kind,name);
                CREATE INDEX relations_relation ON relations(relation_id,feature_id);
                CREATE INDEX station_links_station ON station_links(station_id,feature_id);
                CREATE INDEX station_aliases_physical ON station_aliases(physical_id);
                CREATE INDEX station_aliases_route ON station_aliases(route_id);
                CREATE INDEX station_aliases_name ON station_aliases(name);
                CREATE INDEX directory_parent ON directory_nodes(parent_id,label);
                CREATE INDEX directory_object ON directory_nodes(object_id);
                CREATE INDEX directory_province_city ON directory_nodes(province,city);
                CREATE INDEX directory_type_name ON directory_nodes(object_type,name);
                CREATE INDEX directory_archived ON directory_nodes(archived);
                CREATE INDEX directory_line ON directory_nodes(line_id);
                CREATE INDEX directory_station ON directory_nodes(station_id);
            """)
            for kind in ("metro", "construction"):
                path = directory / SOURCE_FILES[kind]
                if path.exists():
                    for feature in iter_geojson_features(path):
                        _write_feature(db, kind, feature)
                        if kind == "metro":
                            relation = feature.get("properties", {}).get("route_relation_id")
                            box, _ = _bounds(feature.get("geometry", {}).get("coordinates"))
                            if relation is not None and box is not None:
                                row = db.execute("SELECT minx,maxx,miny,maxy FROM route_summary WHERE route_id=?", (relation,)).fetchone()
                                if row:
                                    box = (min(box[0], row[0]), max(box[1], row[1]), min(box[2], row[2]), max(box[3], row[3]))
                                coords = feature["geometry"]["coordinates"]
                                db.execute(
                                    "INSERT OR REPLACE INTO route_summary VALUES(?,?,?,?,?,?,?)",
                                    (int(relation), *box, float(coords[0][0]), float(coords[0][1])),
                                )
            # Identity/area association is an import-time operation. It does not
            # keep national feature lists in the running Desk after this returns.
            station_path = directory / SOURCE_FILES["stations"]
            stations = list(iter_geojson_features(station_path)) if station_path.exists() else []
            for feature in stations:
                _write_feature(db, "station_sources", feature)
            for feature in display_stations(stations, registry=registry):
                feature["properties"].pop("source_members", None)
                _write_feature(db, "stations", feature)
            area_path = directory / SOURCE_FILES["areas"]
            areas = list(iter_geojson_features(area_path)) if area_path.exists() else []
            for feature in display_station_areas(
                associate_station_areas(areas, stations), stations, registry=registry
            ):
                _write_feature(db, "areas", feature)
            db.executemany("INSERT INTO metadata VALUES(?,?)", [("version", VERSION), ("snapshot", snapshot)])
            db.commit()
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def viewport(path: Path, kind: str, bbox, selected=None, limit=6000, vertex_limit=100000):
    if kind not in SOURCE_FILES:
        raise ValueError("未知地铁图层")
    if len(bbox) != 4 or not all(isinstance(value, (float, int)) for value in bbox):
        raise ValueError("无效视窗")
    west, south, east, north = bbox
    if west > east or south > north or not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90):
        raise ValueError("无效视窗")
    selected = list(dict.fromkeys(str(value) for value in (selected or [])))
    if not selected:
        return dict(EMPTY)
    if len(selected) > 100000:
        raise ValueError("选择范围过大")
    relation_kind = kind in ("metro", "construction")
    table = "relations" if relation_kind else "station_links"
    field = "relation_id" if relation_kind else "station_id"
    sql = (
        "SELECT f.data,f.vertices FROM bounds b JOIN features f ON f.id=b.id "
        f"WHERE f.kind=? AND b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=? "
        f"AND EXISTS(SELECT 1 FROM {table} s JOIN request_selection q ON q.id=CAST(s.{field} AS TEXT) "
        "WHERE s.feature_id=f.id) "
        "ORDER BY f.id LIMIT ?"
    )
    args = [kind, west, east, south, north, limit + 1]
    features, vertices, truncated = [], 0, False
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TEMP TABLE request_selection(id TEXT PRIMARY KEY)")
        db.executemany("INSERT OR IGNORE INTO request_selection VALUES(?)", ((value,) for value in selected))
        for data, count in db.execute(sql, args):
            if len(features) >= limit or vertices + count > vertex_limit:
                truncated = True
                break
            features.append(json.loads(data))
            vertices += count
    return {"type": "FeatureCollection", "features": features, "truncated": truncated}


def feature_count(path: Path, kind: str) -> int:
    with closing(sqlite3.connect(path)) as db:
        return db.execute("SELECT count(*) FROM features WHERE kind=?", (kind,)).fetchone()[0]


def find_station(path: Path, query: str):
    with closing(sqlite3.connect(path)) as db:
        row = db.execute(
            "SELECT data FROM features WHERE kind='stations' AND name LIKE ? ORDER BY name LIMIT 1",
            (f"%{query}%",),
        ).fetchone()
    return json.loads(row[0]) if row else None


def iter_features(path: Path, kind: str, bbox=None):
    with closing(sqlite3.connect(path)) as db:
        if bbox is None:
            rows = db.execute("SELECT data FROM features WHERE kind=?", (kind,))
        else:
            west, south, east, north = bbox
            rows = db.execute(
                "SELECT f.data FROM bounds b JOIN features f ON f.id=b.id "
                "WHERE f.kind=? AND b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=?",
                (kind, west, east, south, north),
            )
        for (data,) in rows:
            yield json.loads(data)


def iter_relation_features(path: Path, kind: str, relation_ids):
    """Stream a chosen operating subset without materialising national geometry."""
    ids = [int(value) for value in relation_ids]
    if not ids:
        return
    with closing(sqlite3.connect(path)) as db:
        for start in range(0, len(ids), 800):
            batch = ids[start : start + 800]
            marks = ",".join("?" for _ in batch)
            for (data,) in db.execute(
                "SELECT DISTINCT f.data FROM relations r JOIN features f ON f.id=r.feature_id "
                f"WHERE f.kind=? AND r.relation_id IN ({marks})",
                [kind, *batch],
            ):
                yield json.loads(data)


def route_summaries(path: Path):
    with closing(sqlite3.connect(path)) as db:
        for route, minx, maxx, miny, maxy, x, y in db.execute(
            "SELECT route_id,minx,maxx,miny,maxy,center_x,center_y FROM route_summary"
        ):
            yield route, [minx, miny, maxx, maxy], [x, y]


def find_station_alias(path: Path, alias: str):
    with closing(sqlite3.connect(path)) as db:
        rows = db.execute(
            "SELECT physical_id,route_id,name,x,y,properties FROM station_aliases WHERE id=?",
            (alias,),
        ).fetchall()
    if not rows:
        return None
    physical_id, _route, name, x, y, properties = rows[0]
    return {
        "id": alias, "physical_station_id": physical_id, "name": name,
        "source_name": name, "coordinates": [x, y],
        "route_relation_ids": [row[1] for row in rows],
        "properties": json.loads(properties), "archived": False,
    }


def iter_station_aliases(path: Path):
    with closing(sqlite3.connect(path)) as db:
        current_id, record = None, None
        for alias, physical_id, route, name, x, y, properties in db.execute(
            "SELECT id,physical_id,route_id,name,x,y,properties FROM station_aliases ORDER BY id,route_id"
        ):
            if alias != current_id:
                if record is not None:
                    yield current_id, record
                current_id = alias
                record = {
                    "id": alias, "physical_station_id": physical_id, "name": name,
                    "source_name": name, "coordinates": [x, y],
                    "route_relation_ids": [], "properties": json.loads(properties),
                    "archived": False,
                }
            record["route_relation_ids"].append(route)
        if record is not None:
            yield current_id, record


def find_physical_aliases(path: Path, physical_id: str):
    with closing(sqlite3.connect(path)) as db:
        return [row[0] for row in db.execute(
            "SELECT DISTINCT id FROM station_aliases WHERE physical_id=?", (physical_id,)
        )]


def area_summary(path: Path):
    with closing(sqlite3.connect(path)) as db:
        total = db.execute("SELECT count(*) FROM features WHERE kind='areas'").fetchone()[0]
        linked = db.execute(
            "SELECT count(DISTINCT f.id) FROM features f JOIN relations r ON r.feature_id=f.id WHERE f.kind='areas'"
        ).fetchone()[0]
    return total, linked


def _directory_fingerprint(route_paths, overrides):
    payload = json.dumps([route_paths, overrides], ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sync_station_directory(path: Path, route_paths: dict, overrides: dict):
    """Reconcile the derived directory once per changed workspace snapshot.

    The source features stay immutable. Live edits use update_station_directory
    so one change does not rebuild the Qt tree or this table.
    """
    fingerprint = _directory_fingerprint(route_paths, overrides)
    with closing(sqlite3.connect(path)) as db:
        saved = db.execute("SELECT value FROM metadata WHERE key='directory_fingerprint'").fetchone()
        if saved and saved[0] == fingerprint:
            return
        db.execute("BEGIN")
        db.execute("DELETE FROM directory_nodes")
        folders = {}
        counts = Counter()
        for alias, physical, route_id, source_name in db.execute(
            "SELECT id,physical_id,route_id,name FROM station_aliases ORDER BY id,route_id"
        ):
            default_path = route_paths.get(str(route_id))
            if not default_path:
                continue
            custom = overrides.get(alias, overrides.get(physical, {}))
            folder = custom.get("folder_path")
            labels = [str(value) for value in (folder[:3] if isinstance(folder, list) and len(folder) >= 3 else default_path)]
            archived = bool(custom.get("archived", False))
            if archived:
                labels = ["已归档", *labels]
            name = str(custom.get("display_name") or source_name)
            parent = ""
            for depth in range(1, len(labels) + 1):
                parts = labels[:depth]
                key = "folder:" + json.dumps(parts, ensure_ascii=False)
                folders[key] = (parent, parts[-1], json.dumps(parts, ensure_ascii=False),
                                parts[0], parts[1] if len(parts) > 1 else "")
                counts[key] += 1
                parent = key
            leaf_key = "object:" + json.dumps([alias, route_id], ensure_ascii=False)
            db.execute(
                "INSERT INTO directory_nodes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (leaf_key, parent, name, "object", alias, json.dumps(labels, ensure_ascii=False),
                 0, 1, int(archived), labels[0], labels[1] if len(labels) > 1 else "",
                 "metro_station", name, str(route_id), physical),
            )
        for key, (parent, label, encoded, province, city) in folders.items():
            db.execute(
                "INSERT INTO directory_nodes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, parent, label, "folder", None, encoded, 0, counts[key], 0,
                 province, city, "folder", label, None, None),
            )
        db.execute(
            "UPDATE directory_nodes SET child_count=(SELECT count(*) FROM directory_nodes c "
            "WHERE c.parent_id=directory_nodes.id) WHERE kind='folder'"
        )
        db.execute(
            "INSERT OR REPLACE INTO metadata VALUES('directory_fingerprint',?)", (fingerprint,)
        )
        db.commit()


def update_station_directory(path: Path, aliases, route_paths: dict, overrides: dict):
    """Update only edited station leaves and their ancestor counters."""
    affected = set()
    with closing(sqlite3.connect(path)) as db:
        db.execute("BEGIN")
        for alias in set(aliases):
            old = db.execute(
                "SELECT id,parent_id,path FROM directory_nodes WHERE kind='object' AND object_id=?",
                (alias,),
            ).fetchall()
            for key, parent, encoded in old:
                affected.add(parent)
                labels = json.loads(encoded)
                db.execute("DELETE FROM directory_nodes WHERE id=?", (key,))
                for depth in range(1, len(labels) + 1):
                    folder_key = "folder:" + json.dumps(labels[:depth], ensure_ascii=False)
                    db.execute("UPDATE directory_nodes SET total=total-1 WHERE id=?", (folder_key,))
                    affected.add(folder_key)
            rows = db.execute(
                "SELECT physical_id,route_id,name FROM station_aliases WHERE id=?", (alias,)
            ).fetchall()
            for physical, relation, source_name in rows:
                default_path = route_paths.get(str(relation))
                if not default_path:
                    continue
                custom = overrides.get(alias, overrides.get(physical, {}))
                folder = custom.get("folder_path")
                labels = [str(value) for value in (folder[:3] if isinstance(folder, list) and len(folder) >= 3 else default_path)]
                archived = bool(custom.get("archived", False))
                if archived:
                    labels = ["已归档", *labels]
                parent = ""
                for depth in range(1, len(labels) + 1):
                    parts = labels[:depth]
                    key = "folder:" + json.dumps(parts, ensure_ascii=False)
                    encoded = json.dumps(parts, ensure_ascii=False)
                    inserted = db.execute(
                        "INSERT OR IGNORE INTO directory_nodes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (key, parent, parts[-1], "folder", None, encoded, 0, 0, 0,
                         parts[0], parts[1] if len(parts) > 1 else "", "folder", parts[-1], None, None),
                    )
                    db.execute("UPDATE directory_nodes SET total=total+1 WHERE id=?", (key,))
                    affected.add(key)
                    if inserted.rowcount:
                        affected.add(parent)
                    parent = key
                name = str(custom.get("display_name") or source_name)
                leaf_key = "object:" + json.dumps([alias, relation], ensure_ascii=False)
                db.execute(
                    "INSERT INTO directory_nodes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (leaf_key, parent, name, "object", alias, json.dumps(labels, ensure_ascii=False),
                     0, 1, int(archived), labels[0], labels[1] if len(labels) > 1 else "",
                     "metro_station", name, str(relation), physical),
                )
                affected.add(parent)
        for key, parent in db.execute(
            "SELECT id,parent_id FROM directory_nodes WHERE kind='folder' AND total<=0"
        ).fetchall():
            affected.add(parent)
            affected.discard(key)
        db.execute("DELETE FROM directory_nodes WHERE kind='folder' AND total<=0")
        for key in affected:
            db.execute(
                "UPDATE directory_nodes SET child_count=(SELECT count(*) FROM directory_nodes c WHERE c.parent_id=?) WHERE id=?",
                (key, key),
            )
        db.execute("DELETE FROM metadata WHERE key='directory_fingerprint'")
        db.commit()
    return affected


def station_directory_paths(path: Path):
    with closing(sqlite3.connect(path)) as db:
        return [json.loads(row[0]) for row in db.execute(
            "SELECT path FROM directory_nodes WHERE kind='folder' "
            "AND json_array_length(path)=3 AND province!='已归档' ORDER BY label"
        )]


class StationLookup(Mapping):
    """Dict-like ID lookup that never retains the national station catalog."""

    def __init__(self, path: Path, overrides: dict):
        self.path = Path(path)
        self.overrides = overrides

    def __getitem__(self, key):
        record = find_station_alias(self.path, str(key))
        if record is None:
            raise KeyError(key)
        custom = self.overrides.get(str(key), self.overrides.get(record["physical_station_id"], {}))
        record["name"] = custom.get("display_name") or record["name"]
        record["archived"] = bool(custom.get("archived", False))
        return record

    def __iter__(self):
        for key, _ in iter_station_aliases(self.path):
            yield key

    def __len__(self):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute("SELECT count(DISTINCT id) FROM station_aliases").fetchone()[0]

    def items(self):
        for key, record in iter_station_aliases(self.path):
            custom = self.overrides.get(key, self.overrides.get(record["physical_station_id"], {}))
            record["name"] = custom.get("display_name") or record["name"]
            record["archived"] = bool(custom.get("archived", False))
            yield key, record

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default
