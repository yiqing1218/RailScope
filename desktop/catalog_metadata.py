"""Editable catalog metadata derived from source data without rewriting it."""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import sqlite3

try:
    from .provinces import ProvinceIndex
except ImportError:
    from provinces import ProvinceIndex


STATION_TYPES = (
    "客运站",
    "货运站",
    "客货运站",
    "编组站",
    "区段站",
    "中间站",
    "线路所",
    "乘降所",
    "越行站",
    "会让站",
    "动车所/客整所",
    "货场",
    "未定义",
)

STATION_OVERVIEW_FIELDS = (
    ("chinese_name", "中文名"),
    ("foreign_name", "外文名"),
    ("commissioning_date", "投用日期"),
    ("region", "所属地区"),
    ("station_grade", "车站等级"),
    ("main_lines", "主要线路"),
    ("regional_management", "区域管理"),
    ("platform_scale", "站台规模"),
    ("annual_freight_volume", "年货运量"),
    ("address", "车站地址"),
)


def station_overview(properties, record=None, custom=None):
    properties, record, custom = properties or {}, record or {}, custom or {}
    tags = properties.get("node_tags", {})
    folder = custom.get("folder_path")
    region_parts = folder[:2] if isinstance(folder, list) and folder else (
        record.get("province", ""), record.get("city", "")
    )
    result = {
        "chinese_name": record.get("name") or properties.get("name") or tags.get("name:zh", ""),
        "foreign_name": tags.get("name:en", ""),
        "commissioning_date": tags.get("opening_date") or tags.get("start_date", ""),
        "region": "".join(value for value in region_parts if value),
        "station_grade": tags.get("railway:station_category") or tags.get("station:class", ""),
        "main_lines": "、".join(record.get("line_names", [])),
        "regional_management": tags.get("operator") or properties.get("operator", ""),
        "platform_scale": tags.get("platforms") or tags.get("tracks", ""),
        "annual_freight_volume": tags.get("freight:annual") or tags.get("annual_freight", ""),
        "address": tags.get("addr:full") or "".join(
            str(tags.get(key, ""))
            for key in ("addr:province", "addr:city", "addr:district", "addr:street", "addr:housenumber")
        ),
    }
    result.update(custom.get("overview_attributes", {}))
    result.update(custom.get("custom_attributes", {}))
    if isinstance(folder, list) and folder:
        result["region"] = "".join(value for value in region_parts if value)
    return {key: str(value).strip() for key, value in result.items() if value not in (None, "")}


def normalize_station_attributes(values, custom=False):
    if not isinstance(values, dict):
        raise ValueError("站点概览属性必须是对象")
    allowed = {key for key, _label in STATION_OVERVIEW_FIELDS}
    if not custom and set(values) - allowed:
        raise ValueError("站点概览包含未知标准字段")
    result = {}
    for key, value in values.items():
        name, text = str(key).strip(), str(value).strip()
        if not name or len(name) > 60 or len(text) > 1000:
            raise ValueError("自定义属性名称或内容过长")
        if text:
            result[name] = text
    return result


def station_type(tags, kind=""):
    """Use explicit OSM evidence only; ambiguous stations remain 未定义."""
    tags = tags or {}
    text = " ".join(
        str(tags.get(key, ""))
        for key in ("name", "name:zh", "description", "railway:station_category")
    )
    facility = tags.get("railway:facility", "")
    if kind in {"signal_box", "junction", "crossing"} or "线路所" in text:
        return "线路所"
    if any(word in text for word in ("编组站", "编组场")) or facility == "classification_yard":
        return "编组站"
    if "区段站" in text:
        return "区段站"
    if "越行站" in text:
        return "越行站"
    if "会让站" in text:
        return "会让站"
    if kind == "halt" or tags.get("railway") == "halt" or "乘降所" in text:
        return "乘降所"
    if any(word in text for word in ("动车所", "动车段", "客整所", "客车整备所")):
        return "动车所/客整所"
    if "货场" in text or facility in {"freight_terminal", "freight_yard"}:
        return "货场"
    passenger = tags.get("passenger")
    freight = tags.get("freight")
    if passenger == "yes" and freight == "yes":
        return "客货运站"
    if passenger == "yes" and freight in {"no", None, ""}:
        return "客运站"
    if freight == "yes" and passenger in {"no", None, ""}:
        return "货运站"
    return "未定义"


def nearest_city(coordinates, province, regions, tags=None):
    tags = tags or {}
    explicit = (
        tags.get("addr:city")
        or tags.get("is_in:city")
        or tags.get("addr:district")
    )
    if explicit:
        return str(explicit).removesuffix("市")
    if province in {"北京市", "上海市", "天津市", "重庆市"}:
        return province.removesuffix("市")
    lon, lat = coordinates[:2]
    candidates = []
    for city, region_province, x, y in regions or []:
        if region_province != province:
            continue
        dx = (x - lon) * math.cos(math.radians(lat))
        distance_km = math.hypot(dx, y - lat) * 111.195
        candidates.append((distance_km, city))
    if candidates and min(candidates)[0] <= 180:
        return min(candidates)[1]
    return "城市待核对"


class CatalogOverrides:
    """Small atomic workspace layer for names and directory placement."""

    def __init__(self, path, schema):
        self.path = Path(path)
        self.schema = schema
        self.values = {}

    def load(self):
        if not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("schema") != self.schema or not isinstance(
            payload.get("overrides"), dict
        ):
            raise ValueError("目录编辑文件格式无效")
        self.values = {
            str(key): value
            for key, value in payload["overrides"].items()
            if isinstance(value, dict)
        }

    def update(self, key, **changes):
        self.update_many({str(key): changes})

    def update_many(self, changes):
        proposed = {**self.values}
        for key, value in changes.items():
            if not isinstance(value, dict):
                raise ValueError("目录批量修改内容无效")
            proposed[str(key)] = {**proposed.get(str(key), {}), **value}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {"schema": self.schema, "overrides": proposed},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(self.path)
        self.values = proposed


def _metro_platform_id(properties, entity_id, relation_id):
    """Return a line-platform ID while retaining explicit shared-platform facts."""
    mapping = properties.get("line_platform_ids")
    if isinstance(mapping, dict):
        value = mapping.get(str(relation_id), mapping.get(relation_id))
        if value:
            return str(value)
    shared = properties.get("shared_platform_relation_groups", [])
    if isinstance(shared, list):
        for index, group in enumerate(shared):
            if isinstance(group, list) and relation_id in group:
                return f"{entity_id}@shared-{index + 1}"
    return f"{entity_id}@line-{relation_id}"


def metro_station_directory(stations, routes, hierarchy, overrides=None):
    """province -> city -> line -> platform object, linked to a physical station."""
    overrides = overrides or {}
    route_lookup = {route["osm_relation_id"]: route for route in routes}
    grouped = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    by_id = {}
    for feature in stations:
        props = feature.get("properties", {})
        station_id = str(
            props.get("infrastructure_id")
            or props.get("station_id")
            or f"osm-node/{props.get('osm_node_id')}"
        )
        relation_ids = list(props.get("route_relation_ids", []))
        for relation_id in relation_ids:
            route = route_lookup.get(relation_id)
            if not route:
                continue
            platform_id = _metro_platform_id(props, station_id, relation_id)
            custom = overrides.get(platform_id, overrides.get(station_id, {}))
            record = {
                "id": platform_id,
                "physical_station_id": station_id,
                "name": custom.get("display_name") or props.get("name") or "未命名地铁站",
                "source_name": props.get("name") or "",
                "coordinates": feature.get("geometry", {}).get("coordinates", []),
                "route_relation_ids": [relation_id],
                "properties": props,
                "archived": bool(custom.get("archived", False)),
            }
            existing = by_id.get(platform_id)
            if existing is not None:
                existing["route_relation_ids"] = list(dict.fromkeys(
                    [*existing["route_relation_ids"], relation_id]
                ))
                record = existing
            else:
                by_id[platform_id] = record
            province, city, line = hierarchy.parent(route)
            folder = custom.get("folder_path")
            if isinstance(folder, list) and len(folder) >= 3:
                province, city, line = folder[:3]
            if record["archived"]:
                province, city, line = "已归档", province, city + " / " + line
            entries = grouped[province][city][line]
            if not any(item["id"] == platform_id for item in entries):
                entries.append(record)
    for cities in grouped.values():
        for lines in cities.values():
            for line, records in lines.items():
                lines[line] = sorted(records, key=lambda item: item["name"])
    return grouped, by_id


def _distance_m(a, b):
    lat = math.radians((float(a[1]) + float(b[1])) / 2)
    return math.hypot(
        (float(a[0]) - float(b[0])) * math.cos(lat),
        float(a[1]) - float(b[1]),
    ) * 111_195


def custom_signal_box_records(overrides, regions):
    """Materialise user-created signal boxes from the workspace override layer."""
    index = ProvinceIndex()
    result = []
    for key, custom in (overrides or {}).items():
        if not key.startswith("station:signalbox/"):
            continue
        coordinates = custom.get("coordinates")
        switches = custom.get("member_switch_ids")
        if (
            not isinstance(coordinates, list)
            or len(coordinates) < 2
            or not all(isinstance(value, (int, float)) for value in coordinates[:2])
            or not isinstance(switches, list)
            or len(switches) < 2
        ):
            continue
        ident = key.removeprefix("station:")
        province = index.locate(coordinates)
        folder = custom.get("folder_path")
        city = nearest_city(coordinates, province, regions)
        if isinstance(folder, list) and folder:
            province = folder[0]
            if len(folder) > 1:
                city = folder[1]
        lines = [
            value.get("line_id") for value in custom.get("connected_lines", [])
            if isinstance(value, dict) and value.get("line_id")
        ]
        result.append({
            "id": ident,
            "name": custom.get("display_name") or "未命名线路所",
            "kind": "signal_box",
            "station_type": "线路所",
            "province": province,
            "city": city,
            "coordinates": coordinates[:2],
            "osm_node_id": None,
            "line_ids": lines,
            "line_names": list(custom.get("line_names", lines)),
            "member_switch_ids": [int(value) for value in switches if str(value).isdigit()],
            "properties": {
                "infrastructure_id": ident,
                "kind": "signal_box",
                "source": "RailScope workspace",
            },
        })
    return result


def rail_switch_owner(directory, osm_node_id, regions, overrides=None):
    """Resolve a switch to a stable station/signal-box owner without listing it alone."""
    switch_id = int(osm_node_id)
    for record in custom_signal_box_records(overrides, regions):
        if switch_id in record.get("member_switch_ids", []):
            return record
    directory = Path(directory)
    source = directory / "rail.sqlite"
    line_db = directory / "rail_lines.sqlite"
    if not source.exists() or not line_db.exists():
        return None
    with sqlite3.connect(source) as db:
        row = db.execute(
            "SELECT data FROM features WHERE kind='railPoints' "
            "AND json_extract(data,'$.properties.osm_node_id')=? LIMIT 1",
            (switch_id,),
        ).fetchone()
    if not row:
        return None
    feature = json.loads(row[0])
    if feature.get("properties", {}).get("kind") != "switch":
        return None
    coordinates = feature.get("geometry", {}).get("coordinates", [])
    if len(coordinates) < 2:
        return None
    owner = None
    with sqlite3.connect(line_db) as db:
        columns = {value[1] for value in db.execute("PRAGMA table_info(station_aliases)")}
        if {"source_x", "source_y"} <= columns:
            candidates = db.execute(
                "SELECT source_id,alias,source_x,source_y FROM station_aliases "
                "WHERE source_x BETWEEN ? AND ? AND source_y BETWEEN ? AND ? "
                "AND source_x IS NOT NULL AND source_y IS NOT NULL "
                "ORDER BY confidence DESC,distance_m LIMIT 300",
                (coordinates[0] - .04, coordinates[0] + .04,
                 coordinates[1] - .04, coordinates[1] + .04),
            ).fetchall()
            ranked = sorted(
                (
                    (_distance_m(coordinates, [x, y]), str(source_id), alias, [x, y])
                    for source_id, alias, x, y in candidates
                ),
                key=lambda value: (value[0], value[1]),
            )
            if ranked and ranked[0][0] <= 2500:
                _gap, source_id, alias, point = ranked[0]
                owner = (source_id, alias, point, "station")
        topology = db.execute(
            "SELECT id,label,x,y,kind FROM nodes WHERE kind IN ('junction','signal_box') "
            "AND label IS NOT NULL AND x BETWEEN ? AND ? AND y BETWEEN ? AND ?",
            (coordinates[0] - .04, coordinates[0] + .04,
             coordinates[1] - .04, coordinates[1] + .04),
        ).fetchall()
        ranked_topology = sorted(
            ((_distance_m(coordinates, [x, y]), node, label, [x, y], kind)
             for node, label, x, y, kind in topology),
            key=lambda value: (value[0], str(value[1])),
        )
        if ranked_topology and ranked_topology[0][0] <= 1800 and (
            owner is None or ranked_topology[0][0] < _distance_m(coordinates, owner[2])
        ):
            _gap, node, label, point, kind = ranked_topology[0]
            owner = (f"node/{node}", label, point, kind)
        topology_node = db.execute(
            "SELECT node_id FROM node_aliases WHERE source_id=?", (switch_id,)
        ).fetchone()
        line_rows = [] if not topology_node else db.execute(
            "SELECT l.id,l.source_name FROM line_nodes n JOIN lines l ON l.id=n.line_id "
            "WHERE n.node_id=? ORDER BY l.source_name,l.id", (topology_node[0],)
        ).fetchall()
    index = ProvinceIndex()
    if owner is not None:
        owner_id, name, point, kind = owner
        province = index.locate(point)
        return {
            "id": owner_id,
            "name": str(name) or "未命名线路所",
            "kind": kind,
            "station_type": station_type({}, kind),
            "province": province,
            "city": nearest_city(point, province, regions),
            "coordinates": point,
            "osm_node_id": int(owner_id.split('/', 1)[1]) if owner_id.startswith("node/") and owner_id.split('/', 1)[1].isdigit() else None,
            "line_ids": [row[0] for row in line_rows],
            "line_names": [row[1] for row in line_rows],
            "member_switch_ids": [switch_id],
            "properties": {"kind": kind, "owner_switch_id": switch_id},
        }
    line_key = "|".join(row[0] for row in line_rows) or "unassigned"
    grid_key = f"{round(coordinates[0], 2)}|{round(coordinates[1], 2)}"
    ident = "signalbox/auto-" + hashlib.sha256(
        f"{line_key}|{grid_key}".encode("utf-8")
    ).hexdigest()[:16]
    line_names = [row[1] for row in line_rows]
    province = index.locate(coordinates)
    return {
        "id": ident,
        "name": ((" / ".join(line_names[:2]) + " · ") if line_names else "") + "待命名线路所",
        "kind": "signal_box",
        "station_type": "线路所",
        "province": province,
        "city": nearest_city(coordinates, province, regions),
        "coordinates": coordinates[:2],
        "osm_node_id": None,
        "line_ids": [row[0] for row in line_rows],
        "line_names": line_names,
        "member_switch_ids": [switch_id],
        "properties": {"kind": "signal_box", "owner_switch_id": switch_id, "automatic": True},
    }


def rail_station_records(directory, regions, query="", limit=4000, overrides=None):
    """Read station/signal-box owners; raw switches are always owned children."""
    directory = Path(directory)
    source = directory / "rail.sqlite"
    if not source.exists():
        return [], 0
    where = (
        "kind='railPoints' AND json_extract(data,'$.properties.kind') "
        "IN ('station','halt','signal_box','junction','crossing')"
    )
    args = []
    normalized_query = query.strip().removesuffix("市").removesuffix("站").casefold()
    region_match = next(
        (
            (city, province, lon, lat)
            for city, province, lon, lat in regions
            if normalized_query == str(city).removesuffix("市").casefold()
        ),
        None,
    )
    if region_match:
        _, _, lon, lat = region_match
        where += (
            " AND json_extract(data,'$.geometry.coordinates[0]') BETWEEN ? AND ?"
            " AND json_extract(data,'$.geometry.coordinates[1]') BETWEEN ? AND ?"
        )
        args.extend([lon - 2.0, lon + 2.0, lat - 2.0, lat + 2.0])
    elif query:
        where += " AND (json_extract(data,'$.properties.name') LIKE ? OR CAST(json_extract(data,'$.properties.osm_node_id') AS TEXT) LIKE ?)"
        args.extend([f"%{normalized_query}%", f"%{query.strip()}%"])
    with sqlite3.connect(source) as db:
        total = db.execute(f"SELECT count(*) FROM features WHERE {where}", args).fetchone()[0]
        rows = db.execute(
            f"SELECT data FROM features WHERE {where} ORDER BY CASE json_extract(data,'$.properties.kind') WHEN 'station' THEN 0 WHEN 'halt' THEN 1 ELSE 2 END, json_extract(data,'$.properties.name') LIMIT ?",
            [*args, limit * 4 if region_match else limit],
        ).fetchall()
    features = [json.loads(row[0]) for row in rows]
    node_ids = [f["properties"].get("osm_node_id") for f in features]
    line_map = defaultdict(set)
    line_names = {}
    line_db = directory / "rail_lines.sqlite"
    if line_db.exists() and node_ids:
        with sqlite3.connect(line_db) as db:
            line_names = dict(db.execute("SELECT id,source_name FROM lines"))
            for start in range(0, len(node_ids), 800):
                batch = node_ids[start : start + 800]
                marks = ",".join("?" for _ in batch)
                sql = (
                    "SELECT a.source_id,l.line_id FROM node_aliases a JOIN line_nodes l ON l.node_id=a.node_id "
                    f"WHERE a.source_id IN ({marks})"
                )
                for node, line_id in db.execute(sql, batch):
                    line_map[node].add(line_id)
                alias_sql = (
                    "SELECT a.station_node_id,l.line_id FROM station_aliases a "
                    "JOIN line_nodes l ON l.node_id=a.anchor_node "
                    f"WHERE a.station_node_id IN ({marks})"
                )
                for node, line_id in db.execute(alias_sql, batch):
                    line_map[node].add(line_id)
    index = ProvinceIndex()
    result = []
    for feature in features:
        props = feature["properties"]
        coordinates = feature["geometry"]["coordinates"]
        tags = props.get("node_tags", {})
        province = index.locate(coordinates)
        node_id = props.get("osm_node_id")
        lines = sorted(line_map.get(node_id, set()))
        result.append(
            {
                "id": f"node/{node_id}",
                "name": props.get("name") or f"节点 {node_id}",
                "kind": props.get("kind", ""),
                "station_type": station_type(tags, props.get("kind", "")),
                "province": province,
                "city": nearest_city(coordinates, province, regions, tags),
                "coordinates": coordinates,
                "osm_node_id": node_id,
                "line_ids": lines,
                "line_names": [line_names.get(line, line) for line in lines],
                "properties": props,
            }
        )
    custom_records = custom_signal_box_records(overrides, regions)
    if query:
        query_key = query.strip().casefold()
        custom_records = [
            record for record in custom_records
            if query_key in (record["name"] + " " + record["id"]).casefold()
        ]
    result.extend(custom_records)
    result = list({record["id"]: record for record in result}.values())
    if region_match:
        city = region_match[0].removesuffix("市")
        result = [record for record in result if record["city"].removesuffix("市") == city]
        total = len(result)
        result = result[:limit]
    else:
        result = result[:limit]
        total += len(custom_records)
    return result, total
