"""Build the Jinghu example from shared infrastructure, never a cached train path."""

import json
from pathlib import Path

try:
    from .geometry import distance_m
    from .rail_lines import RESOLUTION_KEY
    from .rail_line_store import (
        DiskRailLineLibrary,
        build_line_index,
        fingerprint,
        index_ready,
    )
    from .rail_store import load_edges
except ImportError:
    from geometry import distance_m
    from rail_lines import RESOLUTION_KEY
    from rail_line_store import (
        DiskRailLineLibrary,
        build_line_index,
        fingerprint,
        index_ready,
    )
    from rail_store import load_edges


def assemble_jinghu(directory, service_path=None):
    directory = Path(directory).resolve()
    source = directory / "rail.sqlite"
    line_index = directory / "rail_lines.sqlite"
    signature = fingerprint(source, [])
    if not index_ready(line_index, signature):
        build_line_index(source, line_index, [], [], lambda _text: None)
    service_path = service_path or Path(__file__).parent / "examples/g1-service.json"
    service = json.loads(Path(service_path).read_text(encoding="utf-8"))
    names = [stop["name"] for stop in service["stops"]]
    library = DiskRailLineLibrary(line_index)
    candidates = library.search_lines("京沪高铁", limit=100)
    aliases = {"京沪高铁", "京沪高速线", "京沪高速铁路"}
    candidates.sort(
        key=lambda item: (
            item["source_name"] not in aliases,
            -item["edge_count"],
            item["id"],
        )
    )
    if not candidates:
        raise ValueError("已导入铁路库中没有京沪高铁，请先获取对应基础设施")
    line_id = candidates[0]["id"]
    logical_stations = {}
    for name in names:
        matches = library.search_endpoints(name, line_id, 50)
        exact = next(
            (
                endpoint
                for endpoint, _label in matches
                if library.endpoint_label(endpoint) == name
            ),
            matches[0][0] if matches else None,
        )
        if exact is None:
            raise ValueError("铁路库缺少车站定位：" + name)
        logical_stations[name] = exact
    sequence = [
        {"kind": "endpoint", "node_id": logical_stations[names[0]]},
        {"kind": "line", "line_id": line_id},
        {"kind": "endpoint", "node_id": logical_stations[names[-1]]},
    ]
    normalized_sequence = library.normalize_sequence(sequence)
    path = library.resolve(normalized_sequence, "mainline")
    edges = load_edges(directory, [leg["edge_id"] for leg in path])
    if len(edges) != len(path):
        raise ValueError("京沪高铁通道引用的基础设施区间不完整")
    sequence = normalized_sequence
    lookup = {edge["id"]: edge for edge in edges}
    nodes, coordinates = [], []
    for leg in path:
        edge = lookup[leg["edge_id"]]
        ids, coords = edge["node_ids"], edge["coordinates"]
        if leg["direction"] == "reverse":
            ids, coords = ids[::-1], coords[::-1]
        nodes.extend(ids if not nodes else ids[1:])
        coordinates.extend(coords if not coordinates else coords[1:])
    stops, mappings = [], []
    previous = -1
    for stop in service["stops"]:
        name = stop["name"]
        logical_id = logical_stations[name]
        source_id = (
            logical_id.removeprefix("station:")
            if isinstance(logical_id, str)
            else str(logical_id)
        )
        with library.connect() as db:
            row = db.execute(
                "SELECT source_x,source_y FROM station_aliases "
                "WHERE source_id=? AND source_x IS NOT NULL LIMIT 1",
                (source_id,),
            ).fetchone()
        if not row:
            raise ValueError(name + "缺少可核验的车站坐标")
        station_coordinate = [row[0], row[1]]
        position = min(
            range(previous + 1, len(nodes)),
            key=lambda value: (distance_m(coordinates[value], station_coordinate), value),
        )
        anchor_node = nodes[position]
        if position <= previous:
            raise ValueError(stop["name"] + "未能匹配到连续主线中的经停锚点")
        previous = position
        offset = round(distance_m(coordinates[position], station_coordinate), 1)
        if offset > 2000:
            raise ValueError(name + "附近没有可用主线端点，不跨越缺失数据连线")
        mapping = {
            "station_name": name,
            "source_station_node": int(source_id.removeprefix("node/"))
            if source_id.startswith("node/")
            and source_id.removeprefix("node/").isdigit()
            else source_id,
            "anchor_node": anchor_node,
            "offset_m": offset,
        }
        mappings.append(mapping)
        stops.append(
            {
                "node_id": nodes[position],
                "arrival_s": stop["arrival_s"],
                "departure_s": stop["departure_s"],
                "extensions": {"railscope.org/station-anchor": mapping},
            }
        )
    extensions = {
        RESOLUTION_KEY: {
            "policy": "mainline",
            "data_source": "railway_database",
            "edge_count": len(path),
            "geometry_status": "assembled_geometry_not_dispatch_route",
        }
    }
    plan = {
        "schema": "railscope.rail-plan.v2",
        "service_date": service["service_date"],
        "timezone": "Asia/Shanghai",
        "source": service["source"],
        "required_capabilities": [],
        "extensions": {
            "railscope.org/assembly": {
                "method": "endpoint_line_endpoint",
                "data_source": "railway_database",
                "source_file": "rail.sqlite",
                "cached_train_path_used": False,
                "stations": mappings,
            },
            "railscope.org/timetable-reference": {
                "url": service["timetable_url"],
                "verified_by_12306": False,
            },
        },
        "routes": [
            {
                "id": "COR-JINGHU-ASSEMBLED-DOWN",
                "name": "北京南 → 京沪高铁 → 上海虹桥",
                "sequence": sequence,
                "path": path,
                "extensions": extensions,
            }
        ],
        "trains": [
            {
                "id": service["train_id"],
                "route_id": "COR-JINGHU-ASSEMBLED-DOWN",
                "stops": stops,
                "extensions": {},
            }
        ],
    }
    points = [
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": coordinates[nodes.index(mapping["anchor_node"])],
            },
            "properties": {
                "osm_node_id": mapping["anchor_node"],
                "name": mapping["station_name"],
                "source_station_node": mapping["source_station_node"],
                "anchor_offset_m": mapping["offset_m"],
            },
        }
        for mapping in mappings
    ]
    return plan, points
