"""Editable catalog metadata derived from source data without rewriting it."""

from __future__ import annotations

from collections import defaultdict
import json
import math
from pathlib import Path

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
    result = {
        "chinese_name": record.get("name") or properties.get("name") or tags.get("name:zh", ""),
        "foreign_name": tags.get("name:en", ""),
        "commissioning_date": tags.get("opening_date") or tags.get("start_date", ""),
        "region": "".join(
            value for value in (record.get("province", ""), record.get("city", "")) if value
        ),
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
        proposed = {**self.values}
        proposed[str(key)] = {**proposed.get(str(key), {}), **changes}
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


def metro_station_directory(stations, routes, hierarchy, overrides=None):
    """province -> city -> line -> station, with shared stable station IDs."""
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
        custom = overrides.get(station_id, {})
        record = {
            "id": station_id,
            "name": custom.get("display_name") or props.get("name") or "未命名地铁站",
            "source_name": props.get("name") or "",
            "coordinates": feature.get("geometry", {}).get("coordinates", []),
            "route_relation_ids": list(props.get("route_relation_ids", [])),
            "properties": props,
        }
        by_id[station_id] = record
        for relation_id in record["route_relation_ids"]:
            route = route_lookup.get(relation_id)
            if not route:
                continue
            province, city, line = hierarchy.parent(route)
            folder = custom.get("folder_path")
            if isinstance(folder, list) and len(folder) >= 3:
                province, city, line = folder[:3]
            grouped[province][city][line].append(record)
    for cities in grouped.values():
        for lines in cities.values():
            for line, records in lines.items():
                lines[line] = sorted(records, key=lambda item: item["name"])
    return grouped, by_id


def rail_station_records(directory, regions, query="", limit=4000):
    """Read a bounded station/control-point catalog directly from the source index."""
    import sqlite3

    directory = Path(directory)
    source = directory / "rail.sqlite"
    if not source.exists():
        return [], 0
    where = "kind='railPoints'"
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
    else:
        where += " AND (json_extract(data,'$.properties.kind') IN ('station','halt','signal_box','junction','crossing') OR json_extract(data,'$.properties.name') != CAST(json_extract(data,'$.properties.osm_node_id') AS TEXT))"
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
    if region_match:
        city = region_match[0].removesuffix("市")
        result = [record for record in result if record["city"].removesuffix("市") == city]
        total = len(result)
        result = result[:limit]
    return result, total
