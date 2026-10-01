"""Calendar-effective edge DTOs; never write back to source GIS files."""

from railscope.services.history import effective_lifecycle, lifecycle_state

try:
    from .rail_lines import line_identity
except ImportError:
    from rail_lines import line_identity


def temporal_edges(edges, records, as_of):
    aliases = {
        alias: effective_lifecycle(records, key)
        for key, value in records.items()
        if value.mode == "rail"
        for alias in (value.id, *value.source_aliases)
    }
    result = []
    for edge in edges:
        keys = (
            edge["id"],
            "object:network_edge_id:" + edge["id"],
            "object:section_id:" + str(edge.get("section_id", "")),
            "object:osm_way_id:" + str(edge.get("osm_way_id", "")),
            edge.get("line_id"),
            line_identity(edge)[0],
        )
        value = next((aliases[key] for key in keys if key in aliases), None)
        state = lifecycle_state(value, as_of)
        if state == "unknown":
            result.append(edge)
            continue
        result.append(
            {
                **edge,
                "source_construction_status": edge.get("source_construction_status")
                or edge.get("construction_status"),
                "construction_status": "planned" if state == "absent" else state,
                "construction": state != "operating",
                "history_id": value.id,
                "history_date": as_of,
            }
        )
    return result
