"""Viewport index: national data stays on disk, not in the browser at startup."""

import json
import math
from pathlib import Path
import sqlite3
from contextlib import closing
from uuid import uuid4
from threading import BoundedSemaphore
from railscope.rail_semantics import edge_semantics
try:
    from .viewport_settings import DEFAULT, scan_limit, normalize, coordinate_count, enforce_budget
except ImportError:
    from viewport_settings import DEFAULT, scan_limit, normalize, coordinate_count, enforce_budget

try:
    from .rail_categories import track_type as classify_track_type
except ImportError:
    from rail_categories import track_type as classify_track_type

# Compatibility name used by import and viewport regression tests.
VIEWPORT_FEATURES = DEFAULT["features"]
_viewport_gate = BoundedSemaphore(1)


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
            **edge_semantics(edge),
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
        edge = {**edge, **edge_semantics(edge)}
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


def viewport(directory, kind, bbox, zoom, selection=None, limits=None, min_zooms=None, metro_database=None, overrides=None):
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
    minimum_zoom = {
        "rail": (min_zooms or {}).get("railLines", 0),
        "railPoints": min((min_zooms or {}).get("railStations", 10),
                          (min_zooms or {}).get("railSwitches", 15)),
        "railPlatforms": (min_zooms or {}).get("railPlatforms", 12),
        "railStationAreas": (min_zooms or {}).get("railAreas", 11),
    }
    if zoom < minimum_zoom.get(kind, 0):
        return {"type": "FeatureCollection", "features": [], "truncated": False}
    selection = selection if isinstance(selection, dict) else {}
    selected_values = {}
    for name in ("sections", "ways", "groups", "facility_groups", "included_edges", "excluded_edges"):
        values = selection.get(name, [])
        if not isinstance(values, list) or len(values) > 100000:
            raise ValueError("线路选择无效")
        if any(not isinstance(value, (str, int)) or isinstance(value, bool) for value in values):
            raise ValueError("线路选择无效")
        selected_values[name] = list(dict.fromkeys(values))
    excluded = selection.get("exclude", False)
    if type(excluded) is not bool:
        raise ValueError("线路排除选项无效")
    selected = any(selected_values[name] for name in ("sections", "ways", "groups"))
    explicit = selected or bool(selected_values["facility_groups"] or selected_values['included_edges'])
    only_explicit = selection.get('only_explicit',False)
    if type(only_explicit) is not bool:
        raise ValueError('线路选择模式无效')
    facility_mode = selection.get("facility", "all")
    if kind == "rail" and facility_mode not in ("all", "lines", "facilities"):
        raise ValueError("站场轨道选择无效")
    budget = normalize(limits) if limits is not None else DEFAULT
    maximum_scan = scan_limit(budget)
    feature_limit = budget["features"]
    byte_limit = budget["bytes"]
    vertex_limit = budget["vertices"]
    feature_byte_limit = budget["feature_bytes"]
    # Do not queue concurrent national JSON decoding jobs after rapid camera moves.
    if not _viewport_gate.acquire(blocking=False):
        return {"type": "FeatureCollection", "features": [], "busy": True}
    features, byte_count, vertices = [], 0, 0
    reasons = set()
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("PRAGMA cache_size=-2048")
            clauses = [
                "f.kind=?",
                "b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=?",
            ]
            parameters = [kind, west, east, south, north]
            edge_predicate = "json_extract(f.data,'$.properties.network_edge_id') IN (SELECT id FROM included_edges)"
            for name in ('included_edges','excluded_edges'):
                db.execute(f'CREATE TEMP TABLE {name}(id TEXT PRIMARY KEY)')
                db.executemany(f'INSERT INTO {name} VALUES(?)',((str(key),) for key in selected_values[name]))
            if kind == "rail" and facility_mode != "all":
                catalog_path = Path(directory) / "rail_catalog.sqlite"
                if not catalog_path.exists():
                    raise ValueError("铁路目录索引未就绪")
                db.execute("ATTACH DATABASE ? AS catalog_visibility",
                           (catalog_path.resolve().as_uri() + "?mode=ro",))
                member = ("EXISTS (SELECT 1 FROM catalog_visibility.rail_directory_members m "
                          "JOIN catalog_visibility.rail_directory_nodes d ON d.id=m.node_id "
                          "WHERE m.catalog_id=json_extract(f.data,'$.properties.catalog_group_id') "
                          "AND d.view='facilities')")
                if db.execute("SELECT 1 FROM catalog_visibility.sqlite_master WHERE name='rail_facility_track_owners'").fetchone():
                    member = ('('+member+" OR EXISTS (SELECT 1 FROM catalog_visibility.rail_facility_track_owners o "
                        "WHERE o.object_id='object:network_edge_id:'||json_extract(f.data,'$.properties.network_edge_id')))")
                exceptions = selected_values["facility_groups"]
                extra = ""
                if exceptions:
                    db.execute("CREATE TEMP TABLE facility_exceptions(group_id TEXT PRIMARY KEY)")
                    db.executemany("INSERT OR IGNORE INTO facility_exceptions VALUES(?)",
                                   ((str(group),) for group in exceptions))
                    extra = (" OR json_extract(f.data,'$.properties.catalog_group_id') "
                             "IN (SELECT group_id FROM facility_exceptions)")
                extra += ' OR '+edge_predicate if selected_values['included_edges'] else ''
                clauses.append("(NOT " + member + extra + ")" if facility_mode == "lines"
                               else "(" + member + extra + ")")
            group_index = bool(selected_values["groups"]) and db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rail_feature_groups'"
            ).fetchone() is not None
            if kind == "rail" and "states" in selection:
                states = selection['states']
                if not isinstance(states, list) or any(v not in ('operating','construction','planned','disused','unknown') for v in states):
                    raise ValueError('线路状态选择无效')
                marks = ','.join('?' for _ in states) or "NULL"
                try:
                    from .rail_style_resolver import STATUS_ALIASES
                except ImportError:
                    from rail_style_resolver import STATUS_ALIASES
                db.execute('CREATE TEMP TABLE status_overrides(id TEXT PRIMARY KEY,status TEXT)')
                for key in overrides or {}:
                    edit = overrides.get(key, {})
                    status = edit.get('rail_semantics', {}).get('construction_status')
                    semantics = edit.get('rail_semantics', {})
                    if (semantics.get('source') == 'rail_line_review' and
                            semantics.get('scope') == 'line_group' and status == 'unknown'):
                        continue  # mixed line summaries retain each source edge's lifecycle
                    if status is None:
                        status = STATUS_ALIASES.get(edit.get('technical_attributes', {}).get('operating_status'))
                    if status is None and type(edit.get('construction')) is bool:
                        status = 'construction' if edit['construction'] else 'operating'
                    if status is not None:
                        if status not in ('operating','construction','planned','disused','unknown'):
                            raise ValueError('运营状态覆盖无效')
                        db.execute('INSERT INTO status_overrides VALUES(?,?)',(str(key),status))
                properties = "json_extract(f.data,'$.properties.{}')"
                effective_status = 'coalesce(' + ','.join(
                    "(SELECT status FROM status_overrides WHERE id=" + expression + ")" for expression in (
                        "'object:network_edge_id:'||" + properties.format('network_edge_id'),
                        properties.format('catalog_group_id'), properties.format('line_id'))) + "," + properties.format('construction_status') + ",CASE WHEN " + properties.format('construction') + "=1 THEN 'construction' ELSE 'operating' END)"
                clauses.append(f"{effective_status} IN ({marks})")
                parameters.extend(states)
            if kind != "rail":
                try:
                    from .transport_modes import rail_display_predicate
                except ImportError:
                    from transport_modes import rail_display_predicate
                clauses.append(rail_display_predicate(db, metro_database, directory))
            if kind == "railPoints":
                station_zoom = (min_zooms or {}).get("railStations", 10)
                switch_zoom = (min_zooms or {}).get("railSwitches", 15)
                station_kinds = "('station','halt','yard','depot','workshop','works','engine_shed')"
                for key in ("point_stations", "point_controls"):
                    if type(selection.get(key, True)) is not bool:
                        raise ValueError("铁路点显示选项无效")
                if zoom < switch_zoom or not selection.get("point_controls", True):
                    clauses.append("coalesce(json_extract(f.data,'$.properties.kind'),'') IN " + station_kinds)
                if zoom < station_zoom or not selection.get("point_stations", True):
                    clauses.append("coalesce(json_extract(f.data,'$.properties.kind'),'') NOT IN " + station_kinds)
                # Legacy indices may contain urban-only POIs. Keep the two
                # station switches independent even before reimporting OSM.
                clauses.append("coalesce(json_extract(f.data,'$.properties.node_tags.station'),'') NOT IN ('subway','light_rail','monorail')")
                clauses.append("coalesce(json_extract(f.data,'$.properties.node_tags.subway'),'') NOT IN ('yes','true')")
                clauses.append("coalesce(json_extract(f.data,'$.properties.node_tags.\"railway:station\"'),'')!='subway'")
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
                            f"f.id IN (SELECT feature_id FROM rail_feature_groups WHERE group_id IN ({placeholders}))"
                            if name == "groups" and group_index else
                            f"json_extract(f.data, '$.properties.{property_name}') IN ({placeholders})"
                        )
                        parameters.extend(values)
                predicate = "(" + " OR ".join(selectors) + ")"
                predicate = "NOT coalesce(" + predicate + ",0)" if excluded else predicate
                clauses.append('('+predicate+' OR '+edge_predicate+')')
            elif only_explicit and not excluded:
                clauses.append(edge_predicate)
            elif not explicit:
                clauses.append('(? >= 10 OR f.service="main")')
                parameters.append(zoom)
            if selected_values['excluded_edges']:
                clauses.append("coalesce(json_extract(f.data,'$.properties.network_edge_id'),'') NOT IN (SELECT id FROM excluded_edges)")
            parameters.append(maximum_scan + 1)
            rows = db.execute(
                'SELECT CASE WHEN length(CAST(f.data AS BLOB))<=? THEN f.data ELSE NULL END '
                "FROM features f JOIN bounds b ON f.id=b.id WHERE "
                + " AND ".join(clauses)
                + " LIMIT ?",
                (feature_byte_limit, *parameters),
            )
            for scanned, (raw,) in enumerate(rows):
                if len(features) >= feature_limit:
                    reasons.add("features")
                    break
                if scanned >= maximum_scan:
                    reasons.add("scan")
                    break
                if raw is None:
                    reasons.add("feature_bytes")
                    continue
                size = len(raw.encode("utf-8"))
                if byte_count + size > byte_limit:
                    reasons.add("bytes")
                    continue
                feature = json.loads(raw)
                if zoom < 10 and feature["geometry"]["type"] == "LineString":
                    coordinates = feature["geometry"]["coordinates"]
                    step = max(1, len(coordinates) // 8)
                    feature["geometry"]["coordinates"] = coordinates[::step] + (
                        [coordinates[-1]] if coordinates[-1] != coordinates[::step][-1] else [])
                count = coordinate_count(feature["geometry"]["coordinates"])
                if vertices + count > vertex_limit:
                    reasons.add("vertices")
                    continue
                features.append(feature)
                byte_count += size
                vertices += count
    finally:
        _viewport_gate.release()
    if kind in ('railPlatforms', 'railStationAreas'):
        try:
            from .rail_platform_associations import apply_associations
        except ImportError:
            from rail_platform_associations import apply_associations
        features = apply_associations(features, directory)
    if kind == "rail":
        for feature in features:
            props = feature["properties"]
            props["track_type"] = classify_track_type(
                props.get("way_tags", props)
            )[0]
            props.update(edge_semantics(props))
    elif kind in ("railPoints", "railPlatforms", "railStationAreas") and features:
        if kind == "railPoints":
            node_ids = [
                feature['properties'].get('osm_node_id', feature['properties'].get('station_source_id')) for feature in features
            ]
        else:
            node_ids = sorted({
                node
                for feature in features
                for node in feature["properties"].get("associated_station_ids", [])
                if node is not None
            }, key=str)
        line_path = Path(directory) / "rail_lines.sqlite"
        if line_path.exists():
            line_map = {node_id: set() for node_id in node_ids}
            line_names = {}
            construction_map, operating_map = {}, {}
            with closing(sqlite3.connect(str(line_path))) as lines:
                for start in range(0, len(node_ids), 800):
                    batch = node_ids[start : start + 800]
                    marks = ",".join("?" for _ in batch)
                    # State belongs to the incident edge, not to every node of
                    # a long railway that happens to contain a construction part.
                    for table, source, anchor in (('node_aliases','source_id','node_id'),
                                                   ('station_aliases','station_node_id','anchor_node')):
                        for endpoint in ('a', 'b'):
                            for node_id, line_id, construction in lines.execute(
                                f"SELECT a.{source},e.line_id,e.construction FROM {table} a "
                                f"JOIN edges e ON e.{endpoint}=a.{anchor} WHERE a.{source} IN ({marks})", batch):
                                line_map.setdefault(node_id, set()).add(line_id)
                                target = construction_map if construction else operating_map
                                target.setdefault(node_id, set()).add(line_id)
                used_line_ids = sorted(set().union(*line_map.values())) if line_map else []
                for start in range(0, len(used_line_ids), 800):
                    batch = used_line_ids[start : start + 800]
                    marks = ",".join("?" for _ in batch)
                    line_names.update(lines.execute(
                        f"SELECT id,source_name FROM lines WHERE id IN ({marks})", batch
                    ))
            for feature in features:
                props = feature["properties"]
                associated = (
                    [props.get('osm_node_id', props.get('station_source_id'))]
                    if kind == "railPoints"
                    else props.get("associated_station_ids", [])
                )
                line_ids = sorted(set().union(
                    *(line_map.get(node, set()) for node in associated)
                )) if associated else []
                props["line_ids"] = line_ids
                props['construction_line_ids'] = sorted({v for node in associated for v in construction_map.get(node, ())})
                props['operating_line_ids'] = sorted({v for node in associated for v in operating_map.get(node, ())})
                props["line_names"] = [line_names.get(value, value) for value in line_ids]
    for feature in features:
        props = feature["properties"]
        if "infrastructure_id" not in props:
            source = "node" if "osm_node_id" in props else "way" if "osm_way_id" in props else "relation"
            props["infrastructure_id"] = f"{source}/{props.get('osm_'+source+'_id')}"
    if kind == 'railPlatforms':
        try:
            from .rail_platforms import platform_display
        except ImportError:
            from rail_platforms import platform_display
        features = platform_display(features)
    return enforce_budget({
        "type": "FeatureCollection",
        "features": features,
        "truncated": bool(reasons),
        "budget": {"reasons": sorted(reasons)},
    }, budget)


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
