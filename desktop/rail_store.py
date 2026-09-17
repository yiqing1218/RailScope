"""Viewport index: national data stays on disk, not in the browser at startup."""

from collections import defaultdict
import json
from pathlib import Path
import sqlite3


def build_index(directory, tracks, points, platforms, edges):
    directory = Path(directory)
    db = sqlite3.connect(directory / "rail.sqlite")
    db.executescript(
        "CREATE TABLE features(id INTEGER PRIMARY KEY,kind TEXT,service TEXT,data TEXT); CREATE VIRTUAL TABLE bounds USING rtree(id,minx,maxx,miny,maxy); CREATE TABLE edges(id TEXT PRIMARY KEY,data TEXT);"
    )
    catalog = defaultdict(
        lambda: {
            "way_ids": [],
            "province": "未分类省份",
            "corridor": "未分配通道",
            "section": "未分配分段",
        }
    )
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
                tags = props["way_tags"]
                name = tags.get("project:name") or tags.get("name") or "未命名轨道"
                record = catalog[name]
                record["way_ids"].append(props["osm_way_id"])
                record["province"] = tags.get("addr:province", record["province"])
    db.executemany(
        "INSERT INTO edges VALUES(?,?)",
        ((e["id"], json.dumps(e, ensure_ascii=False)) for e in edges),
    )
    db.commit()
    db.close()
    (directory / "rail_catalog.json").write_text(
        json.dumps(dict(catalog), ensure_ascii=False), encoding="utf-8"
    )


def viewport(directory, kind, bbox, zoom):
    if kind not in ("rail", "railPoints", "railPlatforms"):
        raise ValueError("图层无效")
    west, south, east, north = bbox
    if not -180 <= west < east <= 180 or not -90 <= south < north <= 90:
        raise ValueError("视窗无效")
    path = Path(directory) / "rail.sqlite"
    if not path.exists():
        return {"type": "FeatureCollection", "features": []}
    with sqlite3.connect(str(path)) as db:
        rows = db.execute(
            'SELECT f.data FROM features f JOIN bounds b ON f.id=b.id WHERE f.kind=? AND b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=? AND (? >= 10 OR f.service="main") LIMIT 12001',
            (kind, west, east, south, north, zoom),
        ).fetchall()
    features = [json.loads(r[0]) for r in rows[:12000]]
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
        "truncated": len(rows) > 12000,
    }


def load_edges(directory, ids):
    if len(ids) > 50000:
        raise ValueError("径路区间过多")
    with sqlite3.connect(str(Path(directory) / "rail.sqlite")) as db:
        return [
            json.loads(row[0])
            for key in set(ids)
            for row in db.execute("SELECT data FROM edges WHERE id=?", (key,))
        ]
