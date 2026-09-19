"""Viewport index: national data stays on disk, not in the browser at startup."""

import json
import math
from pathlib import Path
import sqlite3
from contextlib import closing
from uuid import uuid4
from threading import BoundedSemaphore

# Rendering budgets only: persisted infrastructure and route resolution stay complete.
VIEWPORT_FEATURES = 6000
VIEWPORT_BYTES = 8 * 1024 * 1024
VIEWPORT_VERTICES = 100000
VIEWPORT_FEATURE_BYTES = 1024 * 1024
_viewport_gate = BoundedSemaphore(1)


def coordinate_count(value):
    if not value:
        return 0
    if isinstance(value[0], (int, float)):
        return 1
    return sum(coordinate_count(part) for part in value)


def build_index(directory, tracks, points, platforms, edges):
    directory = Path(directory)
    temporary = directory / ("rail." + uuid4().hex + ".sqlite.tmp")
    db = sqlite3.connect(temporary)
    db.executescript(
        "CREATE TABLE features(id INTEGER PRIMARY KEY,kind TEXT,service TEXT,data TEXT); CREATE VIRTUAL TABLE bounds USING rtree(id,minx,maxx,miny,maxy); CREATE TABLE edges(id TEXT PRIMARY KEY,data TEXT); CREATE TABLE edge_aliases(alias TEXT PRIMARY KEY,id TEXT NOT NULL REFERENCES edges(id));"
    )
    try:
        from .provinces import ProvinceIndex, add_track
    except ImportError:
        from provinces import ProvinceIndex, add_track
    provinces = ProvinceIndex()
    catalog = {}
    number = 0
    for kind, features in [
        ("rail", tracks),
        ("railPoints", points),
        ("railPlatforms", platforms),
    ]:
        for feature in features:
            geometry = feature["geometry"]
            coords = geometry["coordinates"]
            if geometry["type"] == "Point":
                coords = [coords]
            elif geometry["type"] == "Polygon":
                coords = coords[0]
            xs, ys = zip(*coords)
            number += 1
            props = feature["properties"]
            db.execute(
                "INSERT INTO features VALUES(?,?,?,?)",
                (
                    number,
                    kind,
                    props.get("service", "main"),
                    json.dumps(feature, ensure_ascii=False),
                ),
            )
            db.execute(
                "INSERT INTO bounds VALUES(?,?,?,?,?)",
                (number, min(xs), max(xs), min(ys), max(ys)),
            )
            if kind == "rail":
                add_track(catalog, feature, provinces)
    db.executemany(
        "INSERT INTO edges VALUES(?,?)",
        ((e["id"], json.dumps(e, ensure_ascii=False)) for e in edges),
    )
    db.executemany(
        "INSERT OR IGNORE INTO edge_aliases VALUES(?,?)",
        ((e.get("source_edge_id", e["id"]), e["id"]) for e in edges),
    )
    db.commit()
    db.close()
    temporary.replace(directory / "rail.sqlite")
    (directory / "rail_catalog.json").write_text(
        json.dumps(dict(catalog), ensure_ascii=False), encoding="utf-8"
    )


def viewport(directory, kind, bbox, zoom):
    if kind not in ("rail", "railPoints", "railPlatforms", "railStationAreas"):
        raise ValueError("图层无效")
    west, south, east, north = bbox
    if not math.isfinite(zoom):
        raise ValueError("缩放级别无效")
    if not -180 <= west < east <= 180 or not -90 <= south < north <= 90:
        raise ValueError("视窗无效")
    path = Path(directory) / "rail.sqlite"
    if not path.exists():
        return {"type": "FeatureCollection", "features": []}
    minimum_zoom = {"railPoints": 10, "railPlatforms": 12, "railStationAreas": 11}
    if zoom < minimum_zoom.get(kind, 0):
        return {"type": "FeatureCollection", "features": [], "truncated": False}
    # Do not queue concurrent national JSON decoding jobs after rapid camera moves.
    if not _viewport_gate.acquire(blocking=False):
        return {"type": "FeatureCollection", "features": [], "busy": True}
    features, byte_count, vertices, truncated = [], 0, 0, False
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("PRAGMA cache_size=-2048")
            rows = db.execute(
                'SELECT CASE WHEN length(f.data)<=? THEN f.data ELSE NULL END '
                'FROM features f JOIN bounds b ON f.id=b.id WHERE f.kind=? '
                'AND b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=? '
                'AND (? >= 10 OR f.service="main") LIMIT ?',
                (VIEWPORT_FEATURE_BYTES, kind, west, east, south, north, zoom, VIEWPORT_FEATURES + 1),
            )
            for (raw,) in rows:
                if raw is None:
                    truncated = True
                    continue
                size = len(raw.encode("utf-8"))
                if len(features) >= VIEWPORT_FEATURES or byte_count + size > VIEWPORT_BYTES:
                    truncated = True
                    break
                feature = json.loads(raw)
                count = coordinate_count(feature["geometry"]["coordinates"])
                if vertices + count > VIEWPORT_VERTICES:
                    truncated = True
                    continue
                features.append(feature)
                byte_count += size
                vertices += count
    finally:
        _viewport_gate.release()
    if kind == "rail":
        try:
            from .rail_categories import track_type
        except ImportError:
            from rail_categories import track_type
        for feature in features:
            props = feature["properties"]
            props["track_type"] = track_type(props.get("way_tags", props))[0]
    for feature in features:
        props = feature["properties"]
        if "infrastructure_id" not in props:
            source = "node" if "osm_node_id" in props else "way" if "osm_way_id" in props else "relation"
            props["infrastructure_id"] = f"{source}/{props.get('osm_'+source+'_id')}"
    if zoom < 10:
        for feature in features:
            if feature["geometry"]["type"] == "LineString":
                coordinates = feature["geometry"]["coordinates"]
                step = max(1, len(coordinates) // 8)
                feature["geometry"]["coordinates"] = coordinates[::step] + (
                    [coordinates[-1]]
                    if coordinates[-1] != coordinates[::step][-1]
                    else []
                )
    return {
        "type": "FeatureCollection",
        "features": features,
        "truncated": truncated,
    }


def load_edges(directory, ids):
    if len(ids) > 50000:
        raise ValueError("径路区间过多")
    with sqlite3.connect(str(Path(directory) / "rail.sqlite")) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result = []
        for key in dict.fromkeys(ids):
            row = db.execute("SELECT data FROM edges WHERE id=?", (key,)).fetchone()
            if row is None and "edge_aliases" in tables:
                row = db.execute(
                    "SELECT e.data FROM edges e JOIN edge_aliases a ON a.id=e.id WHERE a.alias=?",
                    (key,),
                ).fetchone()
            if row is not None:
                edge = json.loads(row[0])
                if edge["id"] != key:
                    edge["requested_edge_alias"] = key
                result.append(edge)
        return result
