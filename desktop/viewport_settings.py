"""User controlled rendering budgets; infrastructure data remains on disk."""

import json
from pathlib import Path


DEFAULT = {"features": 6000, "bytes": 8 * 1024 * 1024,
           "vertices": 100000, "feature_bytes": 1024 * 1024}
HIGH = {"features": 30000, "bytes": 64 * 1024 * 1024,
        "vertices": 1000000, "feature_bytes": 8 * 1024 * 1024}
UNLIMITED = dict.fromkeys(DEFAULT)
PRESETS = {"default": DEFAULT, "high": HIGH, "unlimited": UNLIMITED}


def normalize(value):
    """Reject corrupt settings instead of letting them disable the request guard."""
    if not isinstance(value, dict):
        return DEFAULT.copy()
    result = {}
    for key, default in DEFAULT.items():
        item = value.get(key, default)
        if item is not None and (type(item) is not int or item < 1):
            return DEFAULT.copy()
        result[key] = item
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
