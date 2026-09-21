"""Geographic catalog metadata, never edits original OSM tags or geometries."""

import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

try:
    from .geometry import distance_m
    from .rail_categories import track_type, corridor_for
except ImportError:
    from geometry import distance_m
    from rail_categories import track_type, corridor_for

VERSION = "topology-line-endpoint-catalog-v9"
NAMES = {
    "Hainan": "海南省",
    "Taiwan": "台湾省",
    "Guangxi": "广西壮族自治区",
    "Fujian": "福建省",
    "Yunnan": "云南省",
    "Guizhou": "贵州省",
    "Jiangxi": "江西省",
    "Hunan": "湖南省",
    "Zhejiang": "浙江省",
    "Shanghai": "上海市",
    "Chongqing": "重庆市",
    "Hubei": "湖北省",
    "Sichuan": "四川省",
    "Anhui": "安徽省",
    "Jiangsu": "江苏省",
    "Henan": "河南省",
    "Tibet": "西藏自治区",
    "Shandong": "山东省",
    "Qinghai": "青海省",
    "Ningxia": "宁夏回族自治区",
    "Shaanxi": "陕西省",
    "Tianjin": "天津市",
    "Shanxi": "山西省",
    "Beijing": "北京市",
    "Gansu": "甘肃省",
    "Hebei": "河北省",
    "Liaoning": "辽宁省",
    "Jilin": "吉林省",
    "Xinjiang": "新疆维吾尔自治区",
    "Inner Mongolia": "内蒙古自治区",
    "Heilongjiang": "黑龙江省",
    "Macau": "澳门特别行政区",
    "Hong Kong": "香港特别行政区",
    # The provider calls Guangdong "Guangzhou Province"; retain source file.
    "Guangzhou": "广东省",
    "Guangdong": "广东省",
}


def midpoint(coords):
    if len(coords) < 2:
        return coords[0]
    lengths = [distance_m(a, b) for a, b in zip(coords, coords[1:])]
    remaining = sum(lengths) / 2
    for a, b, length in zip(coords, coords[1:], lengths):
        if remaining <= length:
            ratio = remaining / length if length else 0
            return [a[0] + ratio * (b[0] - a[0]), a[1] + ratio * (b[1] - a[1])]
        remaining -= length
    return coords[-1]


def in_ring(point, ring):
    x, y = point
    inside = False
    for a, b in zip(ring, ring[1:] + ring[:1]):
        ax, ay = a[:2]
        bx, by = b[:2]
        cross = (x - ax) * (by - ay) - (y - ay) * (bx - ax)
        if (
            abs(cross) < 1e-10
            and min(ax, bx) <= x <= max(ax, bx)
            and min(ay, by) <= y <= max(ay, by)
        ):
            return True
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            inside = not inside
    return inside


class ProvinceIndex:
    def __init__(self, data=None):
        if data is None:
            data = json.loads(
                (Path(__file__).parent / "assets/china-provinces.geojson").read_text(
                    encoding="utf-8"
                )
            )
        self.polygons = []
        self.grid = {}
        for feature in data["features"]:
            raw = feature["properties"]["shapeName"]
            name = next(
                (
                    cn
                    for en, cn in NAMES.items()
                    if raw == en or raw.startswith(en + " ")
                ),
                raw,
            )
            geometry = feature["geometry"]
            polygons = (
                [geometry["coordinates"]]
                if geometry["type"] == "Polygon"
                else geometry["coordinates"]
            )
            for rings in polygons:
                xs, ys = zip(*(p[:2] for p in rings[0]))
                bounds = min(xs), min(ys), max(xs), max(ys)
                ident = len(self.polygons)
                self.polygons.append((name, rings, bounds))
                for x in range(int(bounds[0]), int(bounds[2]) + 1):
                    for y in range(int(bounds[1]), int(bounds[3]) + 1):
                        self.grid.setdefault((x, y), []).append(ident)

    def locate(self, point):
        x, y = point[:2]
        for ident in self.grid.get((int(x), int(y)), []):
            name, rings, (west, south, east, north) = self.polygons[ident]
            if (
                west <= x <= east
                and south <= y <= north
                and in_ring(point, rings[0])
                and not any(in_ring(point, hole) for hole in rings[1:])
            ):
                return name
        return "省界外 / 待核对"

    def along(self, coordinates):
        """Return every province touched by an OSM way without clipping source geometry."""
        # OSM railway ways are short. Endpoints catch boundary crossings while
        # the half-length point retains the former single-province behavior.
        samples = [coordinates[0], midpoint(coordinates), coordinates[-1]]
        names = {self.locate(point) for point in samples}
        inside = names - {"省界外 / 待核对"}
        return sorted(inside or names)


def add_track(catalog, feature, index):
    props = feature["properties"]
    tags = props["way_tags"]
    name = tags.get("project:name") or tags.get("name") or "未命名轨道"
    category, evidence = track_type(tags)
    coordinates = feature["geometry"]["coordinates"]
    for province in index.along(coordinates):
        key = json.dumps(
            [province, name, category], ensure_ascii=False, separators=(",", ":")
        )
        record = catalog.setdefault(
            key,
            {
                "name": name,
                "province": province,
                "way_ids": [],
                "corridor": corridor_for(name, category),
                "section": name,
                "track_type": category,
                "type_evidence": evidence,
                "classification": VERSION,
                "construction": bool(props.get("construction")),
            },
        )
        if props["osm_way_id"] not in record["way_ids"]:
            record["way_ids"].append(props["osm_way_id"])


def _catalog_group(record):
    if (
        "站场" in record["track_type"]
        and record.get("station_name")
        and record["station_name"] != "未关联站场"
    ):
        key = json.dumps(
            [record["track_type"], record["station_name"]],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return "ST-" + hashlib.sha256(key.encode()).hexdigest()[:20]
    return record["line_id"]


def _compact_catalog(sections):
    """Keep the sidebar at physical-line/station granularity."""
    result = {}
    for section in sections.values():
        group_id = section["catalog_group_id"]
        station_group = group_id.startswith("ST-")
        record = result.setdefault(
            group_id,
            {
                "id": group_id,
                "name": section["station_name"]
                if station_group
                else section["line_display_name"],
                "line_id": None if station_group else section["line_id"],
                "line_name": None if station_group else section["line_name"],
                "line_display_name": None
                if station_group
                else section["line_display_name"],
                "station_name": section["station_name"] if station_group else None,
                "track_type": section["track_type"],
                "type_evidence": section["type_evidence"],
                "section_count": 0,
                "edge_count": 0,
                "way_ids": [],
                "catalog_group_id": group_id,
                "classification": VERSION,
                "construction": bool(section.get("construction")),
            },
        )
        record["section_count"] += 1
        record["edge_count"] += len(section["edge_ids"])
        record["construction"] = record["construction"] and bool(
            section.get("construction")
        )
        if record["type_evidence"] != section["type_evidence"]:
            record["type_evidence"] = "组内线段具有多种可追溯分类依据"
    return result


def geographic_catalog(directory):
    """Build the business-line catalog and its internal endpoint graph index.

    Provinces and planning corridors are intentionally absent from the line
    tree.  Endpoint-delimited RS records remain in the disk index for path
    expansion, migration and graph export; they are not catalog leaves.
    """
    directory = Path(directory)
    cache = directory / "rail_catalog.topology.json"
    if cache.exists():
        value = json.loads(cache.read_text(encoding="utf-8"))
        if value.get("version") == VERSION:
            catalog = value["catalog"]
            try:
                from .rail_store import upgrade_render_features
            except ImportError:
                from rail_store import upgrade_render_features
            upgrade_render_features(directory, catalog)
            return catalog
    try:
        from .rail_line_store import (
            build_line_index,
            DiskRailLineLibrary,
            fingerprint,
            index_ready,
        )
    except ImportError:
        from rail_line_store import (
            build_line_index,
            DiskRailLineLibrary,
            fingerprint,
            index_ready,
        )
    source = directory / "rail.sqlite"
    line_index = directory / "rail_lines.sqlite"
    signature = fingerprint(source, [])
    if not index_ready(line_index, signature):
        build_line_index(source, line_index, [], [], lambda value: None)
    library = DiskRailLineLibrary(line_index)
    with sqlite3.connect(str(line_index)) as db:
        edge_ways = dict(db.execute("SELECT id,source_way FROM edges"))
        stations = []
        seen_stations = set()
        for alias, anchor, x, y, status, confidence in db.execute(
            "SELECT a.alias,a.anchor_node,n.x,n.y,a.verification_status,a.confidence "
            "FROM station_aliases a JOIN nodes n ON n.id=a.anchor_node "
            "WHERE a.anchor_node IS NOT NULL AND n.x IS NOT NULL AND n.y IS NOT NULL "
            "ORDER BY a.distance_m,a.alias"
        ):
            key = (alias, anchor)
            if key in seen_stations:
                continue
            seen_stations.add(key)
            stations.append((alias, anchor, x, y, status, confidence))

    station_grid = {}
    for station in stations:
        station_grid.setdefault((int(station[2] * 20), int(station[3] * 20)), []).append(station)

    def nearest_station(section):
        candidates = []
        for coordinate in (
            section.get("from_coordinate"),
            section.get("to_coordinate"),
        ):
            if not coordinate:
                continue
            cell = int(coordinate[0] * 20), int(coordinate[1] * 20)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for station in station_grid.get((cell[0] + dx, cell[1] + dy), []):
                        gap = distance_m(coordinate, [station[2], station[3]])
                        if gap <= 5000:
                            candidates.append((gap, station))
        if not candidates:
            return "未关联站场", "unresolved", None
        gap, station = min(candidates, key=lambda item: (item[0], item[1][0], str(item[1][1])))
        status = "source_control_point" if gap < 1 else "automatic_nearby_control_point"
        confidence = 1.0 if gap < 1 else round(max(0.1, 1 - gap / 5000), 3)
        return station[0], status, confidence

    catalog = {}
    endpoint_sections = {}
    for section in library.sections():
        line = library.lines[section["line_id"]]
        category = section["track_type"]
        station_name, station_status, station_confidence = (
            nearest_station(section)
            if "站场" in category
            else (None, "not_applicable", None)
        )
        edge_ids = [leg["edge_id"] for leg in section["path"]]
        record = {
            "id": section["id"],
            "name": section["name"],
            "section": section["name"],
            "line_id": section["line_id"],
            "line_name": line["source_name"],
            "line_display_name": line["source_name"] + " · " + section["line_id"],
            "from_node": section["from_node"],
            "from_name": section["from_name"],
            "to_node": section["to_node"],
            "to_name": section["to_name"],
            "edge_ids": edge_ids,
            "way_ids": sorted({
                int(edge_ways[edge]) if str(edge_ways[edge]).isdigit() else edge_ways[edge]
                for edge in edge_ids
                if edge in edge_ways
            }),
            "track_type": category,
            "type_evidence": section["type_evidence"],
            "station_name": station_name,
            "station_assignment_status": station_status,
            "station_assignment_confidence": station_confidence,
            "classification": VERSION,
            "construction": bool(section.get("construction")),
        }
        catalog[section["id"]] = record
        endpoint_sections.setdefault(section["from_node"], []).append(section["id"])
        endpoint_sections.setdefault(section["to_node"], []).append(section["id"])
    for record in catalog.values():
        record["from_adjacent_sections"] = sorted(endpoint_sections[record["from_node"]])
        record["to_adjacent_sections"] = sorted(endpoint_sections[record["to_node"]])
        record["catalog_group_id"] = _catalog_group(record)
    try:
        from .rail_store import upgrade_render_features
    except ImportError:
        from rail_store import upgrade_render_features
    upgrade_render_features(directory, catalog)
    catalog = _compact_catalog(catalog)
    temporary = cache.with_name(cache.name + "." + uuid4().hex + ".tmp")
    temporary.write_text(
        json.dumps({"version": VERSION, "catalog": catalog}, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(cache)
    return catalog
