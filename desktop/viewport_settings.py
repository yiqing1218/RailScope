"""User controlled rendering budgets; infrastructure data remains on disk."""

import json
from pathlib import Path


DEFAULT = {"features": 6000, "bytes": 8 * 1024 * 1024,
           "vertices": 100000, "feature_bytes": 1024 * 1024}
HIGH = {"features": 12000, "bytes": 16 * 1024 * 1024,
        "vertices": 250000, "feature_bytes": 2 * 1024 * 1024}
PRESETS = {"default": DEFAULT, "high": HIGH}
# Bound scanning even when many geometries cannot fit the requested budget.
SCAN_LIMIT = HIGH["features"] * 2


def coordinate_count(value):
    if not value:
        return 0
    if isinstance(value[0], (int, float)):
        return 1
    return sum(coordinate_count(part) for part in value)


def enforce_budget(collection, limits):
    """Limit final feature JSON, including presentation/override properties.

    Bytes are the sum of UTF-8 serialized features (not the response envelope
    or browser memory). Call again after HTTP presentation adds properties.
    """
    budget = normalize(limits)
    reasons = set(collection.get("budget", {}).get("reasons", []))
    usage = {"features": 0, "bytes": 0, "vertices": 0, "feature_bytes": 0}
    accepted = []
    for feature in collection["features"]:
        if len(accepted) >= budget["features"]:
            reasons.add("features")
            break
        size = len(json.dumps(feature, ensure_ascii=False).encode("utf-8"))
        vertices = coordinate_count(feature["geometry"].get("coordinates", []))
        exceeded = set()
        if size > budget["feature_bytes"]:
            exceeded.add("feature_bytes")
        if usage["bytes"] + size > budget["bytes"]:
            exceeded.add("bytes")
        if usage["vertices"] + vertices > budget["vertices"]:
            exceeded.add("vertices")
        if exceeded:
            reasons.update(exceeded)
            continue
        accepted.append(feature)
        usage["bytes"] += size
        usage["vertices"] += vertices
        usage["feature_bytes"] = max(usage["feature_bytes"], size)
    usage["features"] = len(accepted)
    collection["features"] = accepted
    collection["truncated"] = bool(collection.get("truncated") or reasons)
    collection["budget"] = {"limits": budget, "usage": usage, "reasons": sorted(reasons)}
    return collection


def normalize(value):
    """Reject corrupt settings instead of letting them disable the request guard."""
    if not isinstance(value, dict):
        return DEFAULT.copy()
    result = {}
    for key, default in DEFAULT.items():
        item = value.get(key, default)
        # Old workspaces could save null for every limit. Never pass an
        # unbounded national query into the WebView process.
        if item is None:
            item = HIGH[key]
        if type(item) is not int or item < 1:
            return DEFAULT.copy()
        result[key] = min(item, HIGH[key])
    return result


def load(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT.copy()
    return normalize(value)


def save(path, value):
    value = normalize(value)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return value
