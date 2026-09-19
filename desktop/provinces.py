"""Geographic catalog metadata, never edits original OSM tags or geometries."""

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

VERSION = "geoboundaries-CHN-ADM1-43563684-type-track-span-v4"
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
            },
        )
        if props["osm_way_id"] not in record["way_ids"]:
            record["way_ids"].append(props["osm_way_id"])


def geographic_catalog(directory):
    """Upgrade an existing dataset without re-downloading/re-importing the PBF."""
    directory = Path(directory)
    cache = directory / "rail_catalog.provinces.json"
    if cache.exists():
        value = json.loads(cache.read_text(encoding="utf-8"))
        if value.get("version") == VERSION:
            return value["catalog"]
    index = ProvinceIndex()
    catalog = {}
    with sqlite3.connect(str(directory / "rail.sqlite")) as db:
        for row in db.execute("SELECT data FROM features WHERE kind='rail'"):
            add_track(catalog, json.loads(row[0]), index)
    temporary = cache.with_name(cache.name + "." + uuid4().hex + ".tmp")
    temporary.write_text(
        json.dumps({"version": VERSION, "catalog": catalog}, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(cache)
    return catalog
