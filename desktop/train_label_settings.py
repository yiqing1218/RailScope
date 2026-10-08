"""Persisted, shared label settings for both moving train overlays."""
import json
from pathlib import Path

DEFAULT = {"size": 12, "minZoom": 11}


def normalize(value):
    value = value if isinstance(value, dict) else {}
    return {key: item if type(item := value.get(key)) is int and low <= item <= high else DEFAULT[key]
            for key, low, high in (("size", 8, 32), ("minZoom", 0, 22))}


def load(path):
    try:
        return normalize(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return DEFAULT.copy()


def save(path, value):
    value = normalize(value)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return value
