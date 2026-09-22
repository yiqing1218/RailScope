"""Viewport index: national data stays on disk, not in the browser at startup."""

import json
import math
from pathlib import Path
import sqlite3
from contextlib import closing
from uuid import uuid4
from threading import BoundedSemaphore

try:
    from .rail_categories import track_type as classify_track_type
except ImportError:
    from rail_categories import track_type as classify_track_type

# Rendering budgets only: persisted infrastructure and route resolution stay complete.
VIEWPORT_FEATURES = 6000
VIEWPORT_BYTES = 8 * 1024 * 1024
VIEWPORT_VERTICES = 100000
VIEWPORT_FEATURE_BYTES = 1024 * 1024
SELECTED_VIEWPORT_FEATURES = 12000
SELECTED_VIEWPORT_BYTES = 32 * 1024 * 1024
SELECTED_VIEWPORT_VERTICES = 1000000
_viewport_gate = BoundedSemaphore(1)


def coordinate_count(value):
    if not value:
        return 0
    if isinstance(value[0], (int, float)):
        return 1
    return sum(coordinate_count(part) for part in value)


def _rail_feature(edge, section_props=None):
    tags = edge.get("way_tags", {})
    category, evidence = classify_track_type(tags)
    source_way = edge.get("osm_way_id") or edge.get(
        "source_edge_id", edge["id"]
    ).split(":")[0].removeprefix("w")
    if str(source_way).isdigit():
        source_way = int(source_way)
    return {
        "type": "Feature",
        "properties": {
            "network_edge_id": edge["id"],
            "line_id": edge.get("line_id"),
            "line_name": edge.get("line_name")
            or tags.get("name")
            or tags.get("ref")
            or "未命名轨道",
            "from_node_id": edge.get("from_node_id", edge["from_node"]),
            "to_node_id": edge.get("to_node_id", edge["to_node"]),
            "osm_way_id": source_way,
            "way_tags": tags,
            "track_type": edge.get("track_type", category),
            "track_type_evidence": edge.get("track_type_evidence", evidence),
            "service": tags.get("service", "main"),
            "construction": bool(edge.get("construction", False)),
            "construction_status": edge.get(
                "construction_status",
                "construction" if edge.get("construction") else "operating",
            ),
            "direction": edge.get("direction", "both"),
            "source": edge.get("source", "OpenStreetMap"),
            "license": "ODbL 1.0",
            "attribution": "© OpenStreetMap contributors",
            **(section_props or {}),
        },
        "geometry": {"type": "LineString", "coordinates": edge["coordinates"]},
    }


def build_index(directory, tracks, points, platforms, edges):
    directory = Path(directory)
    temporary = directory / ("rail." + uuid4().hex + ".sqlite.tmp")
    db = sqlite3.connect(temporary)
    db.executescript(
        "CREATE TABLE features(id INTEGER PRIMARY KEY,kind TEXT,service TEXT,data TEXT); CREATE VIRTUAL TABLE bounds USING rtree(id,minx,maxx,miny,maxy); CREATE TABLE edges(id TEXT PRIMARY KEY,data TEXT); CREATE TABLE edge_aliases(alias TEXT PRIMARY KEY,id TEXT NOT NULL REFERENCES edges(id));"
    )
    try:
        from .rail_lines import RailLineLibrary
    except ImportError:
        from rail_lines import RailLineLibrary
    number = 0
    section_library = RailLineLibrary(edges, points)
    sections = section_library.sections()
    sections_by_id = {section["id"]: section for section in sections}
    endpoint_sections = {}
    edge_sections = {}
    for section in sections:
        endpoint_sections.setdefault(section["from_node"], []).append(section["id"])
        endpoint_sections.setdefault(section["to_node"], []).append(section["id"])
        for leg in section["path"]:
            edge_sections[leg["edge_id"]] = section
    for kind, features in [
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
    # The map uses the same endpoint-delimited physical units as Corridors and
    # the RS directory. Whole OSM ways remain source metadata, not render units.
    for edge in edges:
        section = edge_sections.get(edge["id"])
        section_props = (
            {
                "line_id": section["line_id"],
                "line_name": section_library.lines[section["line_id"]]["source_name"],
                "section_id": section["id"],
                "section_name": section["name"],
                "section_from_node_id": section["from_node"],
                "section_from_name": section_library.nodes[section["from_node"]],
                "section_to_node_id": section["to_node"],
                "section_to_name": section_library.nodes[section["to_node"]],
                "from_adjacent_section_ids": sorted(endpoint_sections[section["from_node"]]),
                "from_adjacent_section_names": [
                    sections_by_id[ident]["name"]
                    for ident in sorted(endpoint_sections[section["from_node"]])
                ],
                "to_adjacent_section_ids": sorted(endpoint_sections[section["to_node"]]),
                "to_adjacent_section_names": [
                    sections_by_id[ident]["name"]
                    for ident in sorted(endpoint_sections[section["to_node"]])
                ],
            }
            if section
            else {}
        )
        feature = _rail_feature(edge, section_props)
        coords = feature["geometry"]["coordinates"]
        xs, ys = zip(*coords)
        number += 1
        db.execute(
            "INSERT INTO features VALUES(?,?,?,?)",
            (
                number,
                "rail",
                feature["properties"]["service"],
                json.dumps(feature, ensure_ascii=False),
            ),
        )
        db.execute(
            "INSERT INTO bounds VALUES(?,?,?,?,?)",
            (number, min(xs), max(xs), min(ys), max(ys)),
        )
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
    # The topology catalog is built from the committed SQLite snapshot in the
    # background. Keep this compatibility file free of obsolete province data.
    (directory / "rail_catalog.json").write_text("{}", encoding="utf-8")


def upgrade_render_features(directory, catalog):
    """Upgrade an existing rail.sqlite to endpoint-edge rendering in place.

    This migration uses only persisted NetworkEdges and the generated topology
    catalog, so users do not have to download or import the source PBF again.
    """
    path = Path(directory) / "rail.sqlite"
    if not path.exists() or not catalog:
        return False
    with sqlite3.connect(path) as db:
        feature_columns = {
            row[1] for row in db.execute("PRAGMA table_info(features)")
        }
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            )
        }
        if not {"id", "kind", "data"}.issubset(feature_columns) or not {
            "bounds",
            "edges",
        }.issubset(tables):
            return False
        edge_count = db.execute("SELECT count(*) FROM edges").fetchone()[0]
        rows = db.execute(
            "SELECT data FROM features WHERE kind='rail' LIMIT 1"
        ).fetchall()
        rendered_count = db.execute(
            "SELECT count(*) FROM features WHERE kind='rail'"
        ).fetchone()[0]
        if rows:
            props = json.loads(rows[0][0]).get("properties", {})
            if (
                props.get("network_edge_id")
                and props.get("catalog_group_id")
                and rendered_count == edge_count
                and "rail_feature_groups" in tables
            ):
                return False

        edge_sections = {
            edge_id: record
            for record in catalog.values()
            for edge_id in record.get("edge_ids", [])
        }
        by_id = dict(catalog)

        def section_properties(record):
            if not record:
                return {}
            from_adjacent = record.get("from_adjacent_sections", [])
            to_adjacent = record.get("to_adjacent_sections", [])
            return {
                "line_id": record["line_id"],
                "line_name": record["line_name"],
                "section_id": record["id"],
                "catalog_group_id": record["catalog_group_id"],
                "section_name": record["name"],
                "section_from_node_id": record["from_node"],
                "section_from_name": record["from_name"],
                "section_to_node_id": record["to_node"],
                "section_to_name": record["to_name"],
                "from_adjacent_section_ids": from_adjacent,
                "from_adjacent_section_names": [
                    by_id[ident]["name"] for ident in from_adjacent if ident in by_id
                ],
                "to_adjacent_section_ids": to_adjacent,
                "to_adjacent_section_names": [
                    by_id[ident]["name"] for ident in to_adjacent if ident in by_id
                ],
            }

        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "CREATE TABLE IF NOT EXISTS rail_feature_groups("
            "feature_id INTEGER PRIMARY KEY,group_id TEXT NOT NULL)"
        )
        db.execute("DROP INDEX IF EXISTS rail_feature_group_id")
        db.execute("DELETE FROM rail_feature_groups")
        db.execute(
            "DELETE FROM bounds WHERE id IN "
            "(SELECT id FROM features WHERE kind='rail')"
        )
        db.execute("DELETE FROM features WHERE kind='rail'")
        number = db.execute("SELECT coalesce(max(id),0) FROM features").fetchone()[0]
        has_service = "service" in feature_columns
        for (raw,) in db.execute("SELECT data FROM edges ORDER BY id"):
            edge = json.loads(raw)
            feature = _rail_feature(
                edge, section_properties(edge_sections.get(edge["id"]))
            )
            coordinates = feature["geometry"]["coordinates"]
            if not coordinates:
                continue
            xs, ys = zip(*coordinates)
            number += 1
            encoded = json.dumps(feature, ensure_ascii=False)
            if has_service:
                db.execute(
                    "INSERT INTO features(id,kind,service,data) VALUES(?,?,?,?)",
                    (number, "rail", feature["properties"]["service"], encoded),
                )
            else:
                db.execute(
                    "INSERT INTO features(id,kind,data) VALUES(?,?,?)",
                    (number, "rail", encoded),
                )
            db.execute(
                "INSERT INTO bounds VALUES(?,?,?,?,?)",
                (number, min(xs), max(xs), min(ys), max(ys)),
            )
            group_id = feature["properties"].get("catalog_group_id")
            if group_id:
                db.execute(
                    "INSERT INTO rail_feature_groups VALUES(?,?)",
                    (number, group_id),
                )
        db.execute(
            "CREATE INDEX rail_feature_group_id ON rail_feature_groups(group_id)"
        )
        db.commit()
    return True


def viewport(directory, kind, bbox, zoom, selection=None):
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
    selection = selection if kind == "rail" and isinstance(selection, dict) else {}
    selected_values = {}
    for name in ("sections", "ways", "groups"):
        values = selection.get(name, [])
        if not isinstance(values, list) or len(values) > 5000:
            raise ValueError("线路选择无效")
        if any(not isinstance(value, (str, int)) or isinstance(value, bool) for value in values):
            raise ValueError("线路选择无效")
        selected_values[name] = list(dict.fromkeys(values))
    selected = any(selected_values.values())
    feature_limit = SELECTED_VIEWPORT_FEATURES if selected else VIEWPORT_FEATURES
    byte_limit = SELECTED_VIEWPORT_BYTES if selected else VIEWPORT_BYTES
    vertex_limit = SELECTED_VIEWPORT_VERTICES if selected else VIEWPORT_VERTICES
    # Do not queue concurrent national JSON decoding jobs after rapid camera moves.
    if not _viewport_gate.acquire(blocking=False):
        return {"type": "FeatureCollection", "features": [], "busy": True}
    features, byte_count, vertices, truncated = [], 0, 0, False
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("PRAGMA cache_size=-2048")
            clauses = [
                "f.kind=?",
                "b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=?",
            ]
            parameters = [kind, west, east, south, north]
            if selected:
                selectors = []
                for name, property_name in (
                    ("sections", "section_id"),
                    ("ways", "osm_way_id"),
                    ("groups", "catalog_group_id"),
                ):
                    values = selected_values[name]
                    if values:
                        placeholders = ",".join("?" for _ in values)
                        selectors.append(
                            f"json_extract(f.data, '$.properties.{property_name}') IN ({placeholders})"
                        )
                        parameters.extend(values)
                clauses.append("(" + " OR ".join(selectors) + ")")
            else:
                clauses.append('(? >= 10 OR f.service="main")')
                parameters.append(zoom)
            parameters.append(feature_limit + 1)
            rows = db.execute(
                'SELECT CASE WHEN length(f.data)<=? THEN f.data ELSE NULL END '
                "FROM features f JOIN bounds b ON f.id=b.id WHERE "
                + " AND ".join(clauses)
                + " LIMIT ?",
                (VIEWPORT_FEATURE_BYTES, *parameters),
            )
            for (raw,) in rows:
                if raw is None:
                    truncated = True
                    continue
                size = len(raw.encode("utf-8"))
                if len(features) >= feature_limit or byte_count + size > byte_limit:
                    truncated = True
                    break
                feature = json.loads(raw)
                count = coordinate_count(feature["geometry"]["coordinates"])
                if vertices + count > vertex_limit:
                    truncated = True
                    continue
                features.append(feature)
                byte_count += size
                vertices += count
    finally:
        _viewport_gate.release()
    if kind == "rail":
        for feature in features:
            props = feature["properties"]
            props["track_type"] = classify_track_type(
                props.get("way_tags", props)
            )[0]
    elif kind in ("railPoints", "railPlatforms", "railStationAreas") and features:
        if kind == "railPoints":
            node_ids = [
                feature["properties"].get("osm_node_id") for feature in features
            ]
        else:
            node_ids = sorted({
                node
                for feature in features
                for node in feature["properties"].get("associated_station_ids", [])
                if node is not None
            })
        line_path = Path(directory) / "rail_lines.sqlite"
        if line_path.exists():
            line_map = {node_id: set() for node_id in node_ids}
            line_names = {}
            with closing(sqlite3.connect(str(line_path))) as lines:
                line_names = dict(lines.execute("SELECT id,source_name FROM lines"))
                for start in range(0, len(node_ids), 800):
                    batch = node_ids[start : start + 800]
                    marks = ",".join("?" for _ in batch)
                    for node_id, line_id in lines.execute(
                        "SELECT a.source_id,l.line_id FROM node_aliases a "
                        "JOIN line_nodes l ON l.node_id=a.node_id "
                        f"WHERE a.source_id IN ({marks})",
                        batch,
                    ):
                        line_map.setdefault(node_id, set()).add(line_id)
                    for node_id, line_id in lines.execute(
                        "SELECT a.station_node_id,l.line_id FROM station_aliases a "
                        "JOIN line_nodes l ON l.node_id=a.anchor_node "
                        f"WHERE a.station_node_id IN ({marks})",
                        batch,
                    ):
                        line_map.setdefault(node_id, set()).add(line_id)
            for feature in features:
                props = feature["properties"]
                associated = (
                    [props.get("osm_node_id")]
                    if kind == "railPoints"
                    else props.get("associated_station_ids", [])
                )
                line_ids = sorted(set().union(
                    *(line_map.get(node, set()) for node in associated)
                )) if associated else []
                props["line_ids"] = line_ids
                props["line_names"] = [line_names.get(value, value) for value in line_ids]
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
