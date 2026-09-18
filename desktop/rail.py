"""Cross-line train-number plans require an explicit, node-connected physical path."""

from datetime import date
import math
from copy import deepcopy
import hashlib
import json

try:
    from .operating import Plan, strict_fields
    from .geometry import distance_m
    from .operating import parse_time
except ImportError:
    from operating import Plan, strict_fields
    from geometry import distance_m
    from operating import parse_time


def expanded_document(payload):
    """Read v1 compatibility plans or strict v2 shared-route references."""
    if payload.get("schema") != "railscope.rail-plan.v2":
        return deepcopy(payload)
    strict_fields(
        payload,
        {
            "schema",
            "service_date",
            "timezone",
            "source",
            "extensions",
            "required_capabilities",
            "routes",
            "trains",
        },
        set(),
        "国铁共享径路计划",
    )
    if not isinstance(payload["routes"], list) or not isinstance(
        payload["trains"], list
    ):
        raise ValueError("径路与车次必须是数组")
    routes = {}
    for route in payload["routes"]:
        strict_fields(
            route,
            {"id", "path", "extensions"},
            {"name", "track_changes", "sequence"},
            "单向运行通道",
        )
        if (
            not isinstance(route["id"], str)
            or not route["id"].strip()
            or route["id"] in routes
        ):
            raise ValueError("径路 ID 无效或重复")
        if not isinstance(route["extensions"], dict):
            raise ValueError("径路扩展必须是对象")
        routes[route["id"]] = route
    result = deepcopy(payload)
    result.pop("routes")
    result["schema"] = "railscope.rail-plan.v1"
    for train in result["trains"]:
        strict_fields(
            train,
            {"id", "route_id", "stops", "extensions"},
            {"station_paths"},
            "国铁车次",
        )
        if train["route_id"] not in routes:
            raise ValueError("车次引用的共享径路不存在")
        train["path"] = deepcopy(routes[train.pop("route_id")]["path"])
    return result


def train_path(base_path, overrides, edges):
    """Replace bounded station sections, never change the reusable corridor."""
    if not isinstance(base_path, list) or not base_path:
        raise ValueError("车次须有明确物理径路")
    if not isinstance(overrides, list):
        raise ValueError("station_paths 必须是数组")
    lookup = {edge["id"]: edge for edge in edges}
    boundaries = []
    for leg in base_path:
        strict_fields(leg, {"edge_id", "direction"}, set(), "通道轨道区间")
        if not isinstance(leg["edge_id"], str) or leg["direction"] not in (
            "forward",
            "reverse",
        ):
            raise ValueError("轨道区间编号或方向无效")
        edge = lookup.get(leg["edge_id"])
        if not edge:
            raise ValueError("通道引用的轨道不存在")
        a, b = edge["from_node"], edge["to_node"]
        if leg["direction"] == "reverse":
            a, b = b, a
        if boundaries and boundaries[-1] != a:
            raise ValueError("通道物理连接不连续")
        if not boundaries:
            boundaries.append(a)
        boundaries.append(b)
    result, cursor = [], 0
    for section in overrides:
        strict_fields(
            section,
            {"from_node", "to_node", "path", "extensions"},
            {"sequence"},
            "车次站场径路",
        )
        if not isinstance(section["extensions"], dict):
            raise ValueError("站场径路扩展必须是对象")
        if any(type(section[key]) is not int for key in ("from_node", "to_node")):
            raise ValueError("站场进出端点必须是整数基础设施节点")
        try:
            start = boundaries.index(section["from_node"], cursor)
            end = boundaries.index(section["to_node"], start + 1)
        except ValueError as error:
            raise ValueError(
                "站场端点必须是通道上按方向排列且已经切分的端点；不能重叠"
            ) from error
        if section.get("sequence"):
            validate_corridors(
                [
                    {
                        "id": "station-section",
                        "path": section["path"],
                        "sequence": section["sequence"],
                        "extensions": section["extensions"],
                    }
                ],
                edges,
            )
        replacement = section["path"]
        if not isinstance(replacement, list) or not replacement:
            raise ValueError("站场径路不能为空")
        current = section["from_node"]
        for leg in replacement:
            strict_fields(leg, {"edge_id", "direction"}, set(), "站场轨道区间")
            edge = lookup.get(leg["edge_id"])
            if (
                not edge
                or edge["construction"]
                or leg["direction"] not in ("forward", "reverse")
            ):
                raise ValueError("站场径路引用不存在、在建或方向错误的轨道")
            a, b = edge["from_node"], edge["to_node"]
            if leg["direction"] == "reverse":
                a, b = b, a
            if a != current:
                raise ValueError("站场径路不连续")
            current = b
        if current != section["to_node"]:
            raise ValueError("站场径路必须回到指定通道端点")
        result.extend(base_path[cursor:start])
        result.extend(replacement)
        cursor = end
    result.extend(base_path[cursor:])
    return result


def shared_document(payload):
    """Geometry belongs to infrastructure; store each directed route once."""
    if payload.get("schema") == "railscope.rail-plan.v2":
        expanded_document(
            payload
        )  # Validate references without discarding route extensions.
        return deepcopy(payload)
    result = expanded_document(payload)
    result["schema"] = "railscope.rail-plan.v2"
    result["routes"] = []
    routes = {}
    for train in result["trains"]:
        path = train.pop("path")
        key = json.dumps(path, sort_keys=True, separators=(",", ":"))
        ident = "route/" + hashlib.sha256(key.encode()).hexdigest()[:20]
        if ident not in routes:
            routes[ident] = path
            result["routes"].append({"id": ident, "path": path, "extensions": {}})
        train["route_id"] = ident
    return result


def compile_rail_plan(payload, edges, points, platforms=()):
    if payload.get("schema") == "railscope.rail-plan.v2":
        validate_corridors(payload.get("routes", []), edges)
    payload = expanded_document(payload)
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
    platform_ids = {
        p["properties"]["osm_way_id"]
        for p in platforms
        if "osm_way_id" in p["properties"]
    }
    platform_refs = {
        p["properties"].get("infrastructure_id")
        or (
            "way/" + str(p["properties"]["osm_way_id"])
            if "osm_way_id" in p["properties"]
            else "relation/" + str(p["properties"].get("osm_relation_id"))
        )
        for p in platforms
    }
    lines, trains, paths = [], [], {}
    for train in payload["trains"]:
        strict_fields(
            train, {"id", "path", "stops", "extensions"}, {"station_paths"}, "国铁车次"
        )
        train["path"] = train_path(train["path"], train.get("station_paths", []), edges)
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
                {"platform_id", "platform_ref", "extensions", "track_change"},
                "国铁经停",
            )
            if type(stop["node_id"]) is not int:
                raise ValueError("线路所/车站节点必须为整数 OSM ID")
            if "track_change" in stop:
                change = stop["track_change"]
                strict_fields(
                    change,
                    {"from_track", "to_track", "via_node", "time"},
                    set(),
                    "变道预留信息",
                )
                if not all(isinstance(v, str) for v in change.values()):
                    raise ValueError("变道预留字段须为字符串，未知用空字符串")
                if change["via_node"] and not change["via_node"].isdigit():
                    raise ValueError("变道节点须为 OSM 整数 ID 或空")
                if change["time"]:
                    parse_time(change["time"])
            try:
                index = node_ids.index(stop["node_id"], offset)
            except ValueError as error:
                raise ValueError("经停/通过节点不在已声明的径路上或站序倒退") from error
            offset = index + 1
            if "platform_id" in stop and (
                type(stop["platform_id"]) is not int
                or stop["platform_id"] not in platform_ids
            ):
                raise ValueError("站台 ID 不在本地真实站台图层中")
            if "platform_ref" in stop and (
                not isinstance(stop["platform_ref"], str)
                or stop["platform_ref"] not in platform_refs
            ):
                raise ValueError("站台引用不在共享基础设施库中")
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
        path_key = tuple((leg["edge_id"], leg["direction"]) for leg in train["path"])
        path = paths.setdefault(
            path_key,
            {
                "coordinates": coords,
                "cumulative": cumulative,
                "length_m": cumulative[-1],
            },
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
                "path": path,
                "stations": stations,
                "resolved_rail_path": deepcopy(train["path"]),
                "station_paths": deepcopy(train.get("station_paths", [])),
            }
        )
        trains.append(
            {
                "id": train["id"],
                "vehicle_id": train["id"],
                "line_id": line_id,
                "direction": "forward",
                "enabled": True,
                "source": (
                    train["extensions"]
                    .get("railscope.org/provenance", {})
                    .get("source", payload["source"])
                    if isinstance(
                        train["extensions"].get("railscope.org/provenance", {}), dict
                    )
                    else payload["source"]
                ),
                "stops": stops,
                "extensions": train["extensions"],
            }
        )
    plan = Plan(lines, "rail")
    plan.validate(trains)
    plan.trains = trains
    plan.extensions = payload["extensions"]
    return plan, lines


def validate_corridors(routes, edges):
    """Validate directed physical connectivity, not unverified dispatch/track assignment."""
    if not isinstance(routes, list):
        raise ValueError("通道必须为数组")
    lookup, seen = {e["id"]: e for e in edges}, set()
    for route in routes:
        strict_fields(
            route,
            {"id", "path", "extensions"},
            {"name", "track_changes", "sequence"},
            "单向运行通道",
        )
        if (
            not isinstance(route["id"], str)
            or not route["id"].strip()
            or route["id"] in seen
        ):
            raise ValueError("通道编号为空或重复")
        seen.add(route["id"])
        if "name" in route and (
            not isinstance(route["name"], str) or not route["name"].strip()
        ):
            raise ValueError("通道名称为空或无效")
        if not isinstance(route["path"], list) or not route["path"]:
            raise ValueError("通道须有明确的单向物理区间组合")
        if "sequence" in route:
            try:
                from .rail_lines import RailLineLibrary, resolution_policy
            except ImportError:
                from rail_lines import RailLineLibrary, resolution_policy
            library = RailLineLibrary(
                [
                    lookup[leg["edge_id"]]
                    for leg in route["path"]
                    if leg["edge_id"] in lookup
                ],
                [],
            )
            if (
                library.resolve(
                    route["sequence"], resolution_policy(route["extensions"])
                )
                != route["path"]
            ):
                raise ValueError("端点—线路表格与物理通道组合不一致")
        nodes = []
        for leg in route["path"]:
            strict_fields(leg, {"edge_id", "direction"}, set(), "通道区间")
            edge = lookup.get(leg["edge_id"])
            if (
                not edge
                or edge["construction"]
                or leg["direction"] not in ("forward", "reverse")
            ):
                raise ValueError("通道区间不存在、在建或方向无效")
            ids = edge.get("node_ids", [edge["from_node"], edge["to_node"]])
            if len(ids) != len(edge["coordinates"]):
                raise ValueError("通道节点与几何数量不一致")
            ids = ids if leg["direction"] == "forward" else ids[::-1]
            if nodes and nodes[-1] != ids[0]:
                raise ValueError("通道区间不连续，必须共享真实 OSM 节点")
            nodes.extend(ids if not nodes else ids[1:])
        if nodes[0] == nodes[-1]:
            raise ValueError("当前仅支持非循环的单向运行通道")
        changes = route.get("track_changes", [])
        if not isinstance(changes, list):
            raise ValueError("通道变道信息必须为数组")
        node_set = set(nodes)
        for change in changes:
            strict_fields(
                change,
                {"node_id", "from_track", "to_track", "via_node", "extensions"},
                set(),
                "通道变道预留",
            )
            if type(change["node_id"]) is not int or change["node_id"] not in node_set:
                raise ValueError("通道变道控制点必须在通道径路上")
            if change["via_node"] is not None and (
                type(change["via_node"]) is not int
                or change["via_node"] not in node_set
            ):
                raise ValueError("通道变道道岔节点必须在通道上，未知用 null")
            if not isinstance(change["from_track"], str) or not isinstance(
                change["to_track"], str
            ):
                raise ValueError("通道股道标识须为字符串，未知用空字符串")
