"""User-owned display hierarchy, independent of immutable OSM facts."""

from copy import deepcopy
import json
from pathlib import Path
import re

try:
    from .geometry import distance_m
except ImportError:
    from geometry import distance_m


def infer_parent(route, point, regions):
    tags = route.get("relation_tags", {})
    # An operator is an organization, not a city. For example 云南京建 contains
    # 南京 but operates Kunming Line 4. Never substring-match the operator.
    for text in (
        route.get("network"),
        tags.get("network:zh"),
        tags.get("network"),
        tags.get("name:zh"),
        route.get("name"),
        tags.get("name"),
    ):
        matches = [r for r in regions if r[0] in str(text or "")]
        if len(matches) == 1:
            return matches[0][1], matches[0][0]
        if len(matches) > 1 and point:
            nearest = min(matches, key=lambda r: distance_m(point, r[2:4]))
            if distance_m(point, nearest[2:4]) < 95000:
                return nearest[1], nearest[0]
    if point and regions:
        nearest = min(regions, key=lambda r: distance_m(point, r[2:4]))
        if distance_m(point, nearest[2:4]) < 95000:
            return nearest[1], nearest[0]
    return "未归类地区", route.get("network") or "未归类城市"


def label_order(label):
    numbered = re.fullmatch(r"(\d+)\s*号线", label)
    return (0, int(numbered[1])) if numbered else (1, label)


def checked_text(value):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > 160
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError("目录名称必须是 1–160 字的非空单行文本")
    return value.strip()


class Hierarchy:
    def __init__(self, routes, centers, regions, path=None):
        self.routes = routes
        self.lookup = {r["osm_relation_id"]: r for r in routes}
        self.centers, self.regions = centers, regions
        self.path = Path(path) if path else None
        self.overrides = {}

    def automatic(self, route):
        province, city = infer_parent(
            route, self.centers.get(route["osm_relation_id"]), self.regions
        )
        ref = str(route.get("ref") or route.get("name", "未命名线路").split("：")[0])
        return province, city, ref + "号线" if ref.isdigit() else ref

    def parent(self, route):
        custom = self.overrides.get(str(route["osm_relation_id"]))
        return (
            (custom["province"], custom["city"], custom["label"])
            if custom
            else self.automatic(route)
        )

    def grouped(self):
        groups = {}
        for route in self.routes:
            province, city, label = self.parent(route)
            groups.setdefault(province, {}).setdefault(city, {}).setdefault(
                label, []
            ).append(route)
        return groups

    def set_parent(self, ids, province=None, city=None, label=None):
        replacements = [
            checked_text(v) if v is not None else None for v in (province, city, label)
        ]
        candidate = deepcopy(self.overrides)
        for rid in ids:
            if rid not in self.lookup:
                raise ValueError("目录中不存在该线路关系")
            current = self.parent(self.lookup[rid])
            values = [
                new if new is not None else old
                for new, old in zip(replacements, current)
            ]
            candidate[str(rid)] = dict(zip(("province", "city", "label"), values))
        self.overrides = candidate

    def reset(self, ids):
        for rid in ids:
            self.overrides.pop(str(rid), None)

    def clone(self):
        result = Hierarchy(self.routes, self.centers, self.regions, self.path)
        result.overrides = deepcopy(self.overrides)
        return result

    def save(self):
        if not self.path:
            raise ValueError("未配置目录设置保存路径")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                {"schema": "railscope.layer-hierarchy.v1", "overrides": self.overrides},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def load(self):
        if not self.path or not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != "railscope.layer-hierarchy.v1"
            or not isinstance(payload.get("overrides"), dict)
        ):
            raise ValueError("目录设置格式无效")
        candidate = {}
        for key, value in payload["overrides"].items():
            if (
                not key.lstrip("-").isdigit()
                or int(key) == 0
                or str(int(key)) != key
                or not isinstance(value, dict)
            ):
                raise ValueError("目录设置标识无效")
            if set(value) != {"province", "city", "label"}:
                raise ValueError("目录设置字段不完整")
            candidate[key] = {
                field: checked_text(value[field])
                for field in ("province", "city", "label")
            }
        # Retain unknown IDs so temporary data reimports do not erase settings.
        self.overrides = candidate
