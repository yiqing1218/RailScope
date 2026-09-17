"""Cross-line train-number plans require an explicit, node-connected physical path."""

from datetime import date
import math

try:
    from .operating import Plan, strict_fields
    from .geometry import distance_m
except ImportError:
    from operating import Plan, strict_fields
    from geometry import distance_m


def compile_rail_plan(payload, edges, points, platforms=()):
    strict_fields(
        payload,
        {
            "schema",
            "service_date",
            "timezone",
            "source",
            "extensions",
            "required_capabilities",
            "trains",
        },
        set(),
        "国铁运行计划",
    )
    if (
        payload["schema"] != "railscope.rail-plan.v1"
        or payload["timezone"] != "Asia/Shanghai"
        or not isinstance(payload["source"], str)
    ):
        raise ValueError("国铁计划版本、时区或来源无效")
    date.fromisoformat(payload["service_date"])
    if payload["required_capabilities"] != []:
        raise ValueError("计划要求尚不支持的运行能力")
    if not isinstance(payload["trains"], list):
        raise ValueError("车次必须是数组")
    edge_lookup = {e["id"]: e for e in edges}
    names = {
        p["properties"]["osm_node_id"]: p["properties"].get(
            "name", str(p["properties"]["osm_node_id"])
        )
        for p in points
    }
    platform_ids = {p["properties"]["osm_way_id"] for p in platforms}
    lines, trains = [], []
    for train in payload["trains"]:
        strict_fields(train, {"id", "path", "stops", "extensions"}, set(), "国铁车次")
        if (
            not isinstance(train["path"], list)
            or not train["path"]
            or not isinstance(train["stops"], list)
        ):
            raise ValueError("车次须有明确径路和经停时刻")
        coords, node_ids, previous = [], [], None
        for leg in train["path"]:
            strict_fields(leg, {"edge_id", "direction"}, set(), "径路区间")
            edge = edge_lookup.get(leg["edge_id"])
            if not edge or edge["construction"]:
                raise ValueError("径路区间不存在或尚在建设，不能运营")
            if leg["direction"] not in ("forward", "reverse"):
                raise ValueError("区间方向无效")
            forward = leg["direction"] == "forward"
            ids = edge.get("node_ids", [edge["from_node"], edge["to_node"]])
            geometry = edge["coordinates"]
            if not forward:
                ids, geometry = ids[::-1], geometry[::-1]
            if previous is not None and ids[0] != previous:
                raise ValueError("跨线径路不连续：必须在同一个真实 OSM 节点连接")
            if len(ids) != len(geometry):
                raise ValueError("区间节点与几何数量不一致")
            node_ids.extend(ids if previous is None else ids[1:])
            coords.extend(geometry if previous is None else geometry[1:])
            previous = ids[-1]
        cumulative = [0]
        for a, b in zip(coords, coords[1:]):
            cumulative.append(cumulative[-1] + distance_m(a, b))
        if not math.isfinite(cumulative[-1]) or cumulative[-1] <= 0:
            raise ValueError("径路长度无效")
        stations, stops = [], []
        offset = 0
        for stop in train["stops"]:
            strict_fields(
                stop,
                {"node_id", "arrival_s", "departure_s"},
                {"platform_id", "extensions"},
                "国铁经停",
            )
            if type(stop["node_id"]) is not int:
                raise ValueError("线路所/车站节点必须为整数 OSM ID")
            try:
                index = node_ids.index(stop["node_id"], offset)
            except ValueError as error:
                raise ValueError("经停/通过节点不在已声明的径路上或站序倒退") from error
            offset = index + 1
            if "platform_id" in stop and stop["platform_id"] not in platform_ids:
                raise ValueError("站台 ID 不在本地真实站台图层中")
            stations.append(
                {
                    "id": str(stop["node_id"]),
                    "name": names.get(stop["node_id"], str(stop["node_id"])),
                    "distance_m": cumulative[index],
                }
            )
            stops.append(
                {
                    "station_id": str(stop["node_id"]),
                    "distance_m": cumulative[index],
                    "arrival_s": stop["arrival_s"],
                    "departure_s": stop["departure_s"],
                    "extensions": {"railscope.org/rail-stop": stop},
                }
            )
        line_id = "rail/" + train["id"]
        lines.append(
            {
                "id": line_id,
                "name": train["id"] + " · 跨线径路",
                "ref": train["id"],
                "relation_id": 0,
                "color": "#466979",
                "variants": [],
                "path": {
                    "coordinates": coords,
                    "cumulative": cumulative,
                    "length_m": cumulative[-1],
                },
                "stations": stations,
            }
        )
        trains.append(
            {
                "id": train["id"],
                "line_id": line_id,
                "direction": "forward",
                "enabled": True,
                "source": payload["source"],
                "stops": stops,
                "extensions": train["extensions"],
            }
        )
    plan = Plan(lines, "rail")
    plan.validate(trains)
    plan.trains = trains
    plan.extensions = payload["extensions"]
    return plan, lines
