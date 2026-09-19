"""Search source names without changing station identities or source tags."""

import re
import unicodedata


def normalize_name(value):
    value = unicodedata.normalize("NFKC", str(value)).casefold().strip()
    value = re.sub(r"(?:地铁站|轨道交通站|站|(?:\s+(?:metro|subway))?\s*station)$", "", value)
    return re.sub(r"[\s·・_\-（）()]", "", value)


def station_names(props):
    values = set()
    for tags in (props, props.get("station_tags", {}), props.get("station_area_tags", {}), props.get("way_tags", {})):
        for key, value in tags.items():
            if key in ("name", "source_name", "alt_name", "old_name", "short_name", "official_name", "loc_name") or key.startswith(("name:", "alt_name:", "old_name:")):
                if isinstance(value, str):
                    values.update(v.strip() for v in re.split(r"[;；]", value) if v.strip())
    return sorted(values)


def name_keys(props):
    return {normalize_name(name) for name in station_names(props) if not name.startswith("未命名")} - {""}


def matches_query(record, query):
    tokens = [token for value in query.split() if (token := normalize_name(value))]
    names = " ".join(normalize_name(name) for name in [record.get("name", ""), *record.get("aliases", [])])
    context = normalize_name(" ".join(map(str, [record.get("network", ""), record.get("status", ""), record.get("osm_node_id", ""), record.get("station_id", "")])) )
    return all(token in names or token in context for token in tokens)
