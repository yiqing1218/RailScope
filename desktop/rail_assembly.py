"""Build the Jinghu example from shared infrastructure, never a cached train path."""

from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3

try:
    from .geometry import distance_m
    from .rail_lines import RailLineLibrary, line_identity, RESOLUTION_KEY
except ImportError:
    from geometry import distance_m
    from rail_lines import RailLineLibrary, line_identity, RESOLUTION_KEY


def assemble_jinghu(directory, service_path=None):
    source = Path(directory).resolve() / "rail.sqlite"
    service_path = service_path or Path(__file__).parent / "examples/g1-service.json"
    service = json.loads(Path(service_path).read_text(encoding="utf-8"))
    names = [stop["name"] for stop in service["stops"]]
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as db:
        edges = [
            json.loads(raw)
            for (raw,) in db.execute(
                "SELECT data FROM edges WHERE json_extract(data,'$.way_tags.name')='京沪高铁'"
            )
        ]
        station_rows = db.execute(
            "SELECT data FROM features WHERE kind='railPoints' AND json_extract(data,'$.properties.name') IN ("
            + ",".join("?" for _ in names)
            + ") ORDER BY json_extract(data,'$.properties.osm_node_id')",
            names,
        ).fetchall()
    if not edges:
        raise ValueError("已导入铁路库中没有京沪高铁，请先获取对应基础设施")
    identities = Counter(
        line_identity(edge)[0]
        for edge in edges
        if not edge.get("construction")
        and edge.get("way_tags", {}).get("service", "main") == "main"
    )
    if not identities:
        raise ValueError("未找到可用京沪高铁主线")
    line_id = identities.most_common(1)[0][0]
    edges = [edge for edge in edges if line_identity(edge)[0] == line_id]
    stations = {}
    for (raw,) in station_rows:
        feature = json.loads(raw)
        if feature["geometry"]["type"] == "Point" and feature["properties"].get(
            "kind"
        ) in ("station", "halt"):
            stations.setdefault(feature["properties"]["name"], feature)
    missing = [name for name in names if name not in stations]
    if missing:
        raise ValueError("铁路库缺少车站定位：" + "、".join(missing))
    node_coordinates = {}
    for edge in edges:
        node_coordinates[edge["from_node"]] = edge["coordinates"][0]
        node_coordinates[edge["to_node"]] = edge["coordinates"][-1]

    def anchor(name):
        coordinate = stations[name]["geometry"]["coordinates"]
        node = min(
            node_coordinates,
            key=lambda node: (distance_m(node_coordinates[node], coordinate), node),
        )
        if distance_m(node_coordinates[node], coordinate) > 2000:
            raise ValueError(name + "附近没有可用主线端点，不跨越缺失数据连线")
        return node

    start, finish = anchor(names[0]), anchor(names[-1])
    library = RailLineLibrary(edges, [])
    sequence = [
        {"kind": "endpoint", "node_id": start},
        {"kind": "line", "line_id": line_id},
        {"kind": "endpoint", "node_id": finish},
    ]
    path = library.resolve(sequence, "mainline")
    lookup = library.edges
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
    for index, stop in enumerate(service["stops"]):
        station = stations[stop["name"]]
        coordinate = station["geometry"]["coordinates"]
        position = (
            0
            if index == 0
            else len(nodes) - 1
            if index == len(names) - 1
            else min(
                range(previous + 1, len(nodes)),
                key=lambda i: (distance_m(coordinates[i], coordinate), i),
            )
        )
        offset = distance_m(coordinates[position], coordinate)
        if offset > 2000 or position <= previous:
            raise ValueError(stop["name"] + "未能匹配到连续主线中的经停锚点")
        previous = position
        mapping = {
            "station_name": stop["name"],
            "source_station_node": station["properties"]["osm_node_id"],
            "anchor_node": nodes[position],
            "offset_m": round(offset, 1),
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
