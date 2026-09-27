"""Presentation-only names; source IDs, geometry and operational references stay intact."""


def feature_name_key(properties):
    for field, prefix in (("osm_node_id", "node/"), ("osm_way_id", "way/"),
                          ("osm_relation_id", "relation/")):
        if properties.get(field) is not None:
            return prefix + str(properties[field])
    return properties.get("infrastructure_id") or properties.get("source_ref")


def display_manifest(catalog, overrides, way_names=None):
    names = {"lines": {}, "stations": {}, "features": {}, "yards": {}}
    names["features"].update({"way/" + str(key): value for key, value in (way_names or {}).items()})
    for key, custom in overrides.items():
        name = str(custom.get("display_name") or custom.get("assembly_name") or "").strip()
        if not name:
            continue
        if key.startswith("station:"):
            names["stations"][key.removeprefix("station:")] = name
        elif key.startswith("switch:"):
            names["features"][key.removeprefix("switch:")] = name
        elif key.startswith("feature:"):
            names["features"][key.removeprefix("feature:")] = name
        elif key in catalog:
            names["lines"][key] = name
            line_id = catalog[key].get("line_id")
            if line_id:
                names["lines"][line_id] = name
    yards = catalog.station_groups() if hasattr(catalog, 'station_groups') else catalog.items()
    for key, record in yards:
        station = record.get("station_name")
        if station and station != "未关联站场" and "站场" in record.get("track_type", ""):
            names["yards"][key] = station + " · 站场股道"
    return names
