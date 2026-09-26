"""User-controlled minimum map zoom for infrastructure categories."""

import json
from pathlib import Path


DEFAULT = {
    "metroLines": 0,
    "metroStations": 9,
    "metroAreas": 12,
    "metroConstruction": 0,
    "railLines": 0,
    "railStations": 10,
    "railSwitches": 15,
    "railPlatforms": 12,
    "railAreas": 11,
    "roads": 0,
}

LABELS = {
    "metroLines": "地铁线路",
    "metroStations": "地铁站点",
    "metroAreas": "地铁真实站区",
    "metroConstruction": "地铁在建工程",
    "railLines": "国铁线路",
    "railStations": "国铁车站与线路所",
    "railSwitches": "国铁道岔",
    "railPlatforms": "国铁站台线",
    "railAreas": "国铁真实站区",
    "roads": "高速公路",
}


def normalize(value):
    if not isinstance(value, dict):
        return DEFAULT.copy()
    result = {}
    for key, default in DEFAULT.items():
        item = value.get(key, default)
        result[key] = item if type(item) is int and 0 <= item <= 22 else default
    return result


def load(path):
    try:
        return normalize(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return DEFAULT.copy()


def save(path, value):
    value = normalize(value)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return value
