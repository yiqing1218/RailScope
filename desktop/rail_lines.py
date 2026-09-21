"""Reusable named infrastructure, endpoint-delimited sections and corridor notation."""

from collections import defaultdict, deque
from copy import deepcopy
import hashlib
import json
import csv
import io

try:
    from .geometry import distance_m
    from .rail_categories import track_type
except ImportError:
    from geometry import distance_m
    from rail_categories import track_type

RESOLUTION_KEY = "railscope.org/line-resolution"

CONTROL_POINT_TYPES = {
    "station": "车站轨道节点",
    "halt": "停靠点",
    "stop": "停车位置",
    "junction": "线路所",
    "signal_box": "线路所",
    "switch": "道岔",
    "signal": "信号点",
    "topology_junction": "拓扑岔接点",
    "line_change": "线路归属变化点",
    "line_terminal": "线路端点",
}


def control_point_label(ident, name="", kind=None):
    category = CONTROL_POINT_TYPES.get(kind, "轨道端点")
    return f"{name or '未命名' + category} · {category} · {ident}"


def resolution_policy(extensions):
    value = extensions.get(RESOLUTION_KEY, {})
    if not isinstance(value, dict):
        raise ValueError("线路拼接策略须为对象")
    policy = value.get("policy", "strict")
    if policy not in ("strict", "mainline"):
        raise ValueError("不支持的线路拼接策略")
    return policy


def edge_length(edge):
    if "length_m" in edge:
        return max(0.001, float(edge["length_m"]))
    coordinates = edge.get("coordinates", [])
    return max(
        0.001, sum(distance_m(a, b) for a, b in zip(coordinates, coordinates[1:]))
    )


CORRIDOR_COLUMNS = (
    "corridor_id",
    "corridor_name",
    "segment",
    "from_node",
    "line_id",
    "section_id",
    "to_node",
)

LEGACY_CORRIDOR_COLUMNS = tuple(
    column for column in CORRIDOR_COLUMNS if column != "section_id"
)


def edge_endpoints(edge):
    """Return stable RailScope endpoints when the imported snapshot has them."""
    return (
        edge.get("from_node_id", edge["from_node"]),
        edge.get("to_node_id", edge["to_node"]),
    )


def traversal_allowed(edge, direction):
    # New imports persist a normalized direction. Older snapshots did not, and
    # interpreting their raw OSM tags here would silently change saved routes.
    configured = edge.get("direction", "both")
    if configured not in ("both", "forward", "reverse", "closed"):
        configured = "both"
    return configured != "closed" and configured in ("both", direction)


def _endpoint(value):
    value = value.strip()
    if not value:
        raise ValueError("通道端点不能为空")
    # Old portable examples used integer OSM nodes. New national imports use
    # persistent NN-* control-point IDs. Keep the old form importable only.
    return int(value) if value.isdigit() else value


def import_corridor_csv(text):
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if reader.fieldnames not in (list(CORRIDOR_COLUMNS), list(LEGACY_CORRIDOR_COLUMNS)):
        raise ValueError(
            "通道 CSV 表头必须为：" + ",".join(CORRIDOR_COLUMNS)
            + "（旧版不含 section_id 仍可导入）"
        )
    has_sections = "section_id" in reader.fieldnames
    routes = {}
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("通道 CSV 列数错误")
        row = {key: value.strip() for key, value in row.items()}
        route = routes.setdefault(
            row["corridor_id"],
            {
                "id": row["corridor_id"],
                "name": row["corridor_name"],
                "sequence": [],
                "extensions": {RESOLUTION_KEY: {"policy": "strict"}},
            },
        )
        sequence = route["sequence"]
        if row["corridor_name"] != route["name"] or row["segment"] != str(
            (len(sequence) + 1) // 2 if sequence else 1
        ):
            raise ValueError("通道名称不一致或组合段序号须从 1 连续递增")
        a, b = _endpoint(row["from_node"]), _endpoint(row["to_node"])
        if sequence and sequence[-1]["node_id"] != a:
            raise ValueError("相邻行必须共用端点")
        if not sequence:
            sequence.append({"kind": "endpoint", "node_id": a})
        line = {"kind": "line", "line_id": row["line_id"]}
        if has_sections and row["section_id"]:
            line["section_id"] = row["section_id"]
        sequence.extend([line, {"kind": "endpoint", "node_id": b}])
    if not routes:
        raise ValueError("通道表格为空")
    return {
        "schema": "railscope.rail-corridors.v2",
        "source": "用户通道 CSV；非实际联锁进路",
        "extensions": {},
        "required_capabilities": [],
        "corridors": list(routes.values()),
    }


def export_corridor_csv(document):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CORRIDOR_COLUMNS)
    writer.writeheader()
    for route in document["corridors"]:
        sequence = route["sequence"]
        for index in range(1, len(sequence), 2):
            writer.writerow(
                {
                    "corridor_id": route["id"],
                    "corridor_name": route["name"],
                    "segment": (index + 1) // 2,
                    "from_node": sequence[index - 1]["node_id"],
                    "line_id": sequence[index]["line_id"],
                    "section_id": sequence[index].get("section_id", ""),
                    "to_node": sequence[index + 1]["node_id"],
                }
            )
    return output.getvalue()


def line_identity(edge):
    tags = edge.get("way_tags", {})
    if edge.get("line_id"):
        return edge["line_id"], edge.get("line_name") or tags.get("name") or "未命名轨道"
    name = tags.get("name") or tags.get("full_name")
    # Never merge every unnamed siding nationwide into one fictional railway.
    fallback = edge["id"].split(":")[0]
    key = [
        name or fallback,
        tags.get("ref", ""),
        tags.get("service", "main"),
        tags.get("highspeed", "no"),
        bool(edge.get("construction")),
    ]
    ident = (
        "RL-"
        + hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:20]
    )
    return ident, name or "未命名轨道·" + fallback


class RailLineLibrary:
    def __init__(self, edges, points, names=None):
        self.edges = {e["id"]: e for e in edges}
        self.lines, self.edge_lines, self.nodes = {}, {}, {}
        self.endpoint_aliases = {}
        self.names = dict(names or {})
        labels = {}
        for point in points:
            props = point.get("properties", {})
            if "osm_node_id" not in props:
                continue
            node_id = props.get("infrastructure_node_id", props["osm_node_id"])
            labels[node_id] = control_point_label(
                node_id, props.get("name", ""), props.get("kind")
            )
        self.control_nodes = set(labels)
        for point in points:
            props = point.get("properties", {})
            if (
                props.get("kind") in CONTROL_POINT_TYPES
                and props.get("osm_node_id") is not None
            ):
                node_id = props.get("infrastructure_node_id", props["osm_node_id"])
                self.control_nodes.add(node_id)
        for edge in self.edges.values():
            ident, source_name = line_identity(edge)
            record = self.lines.setdefault(
                ident,
                {
                    "id": ident,
                    "source_name": source_name,
                    "edge_ids": [],
                    "graph": defaultdict(list),
                    "track_type": track_type(edge.get("way_tags", {}))[0],
                    "type_evidence": track_type(edge.get("way_tags", {}))[1],
                },
            )
            record["edge_ids"].append(edge["id"])
            self.edge_lines[edge["id"]] = ident
            a, b = edge_endpoints(edge)
            self.endpoint_aliases[edge.get("from_node", a)] = a
            self.endpoint_aliases[edge.get("to_node", b)] = b
            record["graph"][a].append((b, edge["id"], "forward"))
            record["graph"][b].append((a, edge["id"], "reverse"))
            for node in (a, b):
                self.nodes[node] = labels.get(node) or control_point_label(node)
        for ident, record in self.lines.items():
            record["name"] = (
                self.names.get(ident, record["source_name"]) + " · " + ident
            )
        self._bridges = {}

    def search_lines(self, query="", limit=100, offset=0):
        values = (
            r
            for r in sorted(self.lines.values(), key=lambda r: r["name"])
            if query.casefold() in r["name"].casefold()
        )
        from itertools import islice

        return list(islice(values, offset, offset + limit))

    def search_nodes(self, query="", line_id=None, limit=100):
        nodes = self.lines[line_id]["graph"] if line_id in self.lines else self.nodes
        from itertools import islice

        return list(
            islice(
                (
                    (key, self.nodes[key])
                    for key in sorted(nodes)
                    if query.casefold() in (str(key) + " " + self.nodes[key]).casefold()
                ),
                limit,
            )
        )

    def search_sections(self, query="", line_id=None, limit=100):
        values = (
            section
            for section in self.sections()
            if (not line_id or section["line_id"] == line_id)
            and query.casefold() in (section["id"] + " " + section["name"]).casefold()
        )
        from itertools import islice

        return list(islice(values, limit))

    def section(self, section_id, line_id=None):
        matches = [
            section
            for section in self.sections()
            if section["id"] == section_id
            and (line_id is None or section["line_id"] == line_id)
        ]
        if len(matches) != 1:
            raise ValueError("端点分段不存在或不属于所选铁路线：" + str(section_id))
        return matches[0]

    def normalize_sequence(self, sequence):
        result = deepcopy(sequence)
        for index in range(0, len(result), 2):
            if isinstance(result[index], dict) and "node_id" in result[index]:
                result[index]["node_id"] = self.endpoint_aliases.get(
                    result[index]["node_id"], result[index]["node_id"]
                )
        return result

    def bridges(self, ident):
        if ident in self._bridges:
            return self._bridges[ident]
        graph = self.lines[ident]["graph"]
        entered, low, result = {}, {}, set()
        for root in graph:
            if root in entered:
                continue
            entered[root] = low[root] = len(entered)
            stack = [(root, None, None, iter(graph[root]))]
            while stack:
                node, parent, parent_edge, iterator = stack[-1]
                try:
                    other, edge_id, _ = next(iterator)
                except StopIteration:
                    stack.pop()
                    if parent is not None:
                        low[parent] = min(low[parent], low[node])
                        if low[node] > entered[parent]:
                            result.add(parent_edge)
                    continue
                edge = self.edges[edge_id]
                if edge.get("construction") or edge.get(
                    "construction_status", "operating"
                ) != "operating":
                    continue
                if edge_id == parent_edge:
                    continue
                if other in entered:
                    low[node] = min(low[node], entered[other])
                else:
                    entered[other] = low[other] = len(entered)
                    stack.append((other, node, edge_id, iter(graph[other])))
        self._bridges[ident] = result
        return result

    def resolve(self, sequence, policy="strict"):
        if policy not in ("strict", "mainline"):
            raise ValueError("不支持的线路拼接策略")
        if (
            not isinstance(sequence, list)
            or len(sequence) < 3
            or len(sequence) % 2 != 1
        ):
            raise ValueError("通道必须为端点—线路—端点，交替排列")
        sequence = self.normalize_sequence(sequence)
        path = []
        for i, entry in enumerate(sequence):
            required = {"kind", "node_id"} if i % 2 == 0 else {"kind", "line_id"}
            allowed = required if i % 2 == 0 else required | {"section_id"}
            if (
                not isinstance(entry, dict)
                or not required.issubset(entry)
                or not set(entry).issubset(allowed)
                or entry["kind"] != ("endpoint" if i % 2 == 0 else "line")
            ):
                raise ValueError("通道表格字段或交替顺序无效")
            if i % 2 == 0 and (
                not isinstance(entry["node_id"], (int, str))
                or isinstance(entry["node_id"], bool)
                or entry["node_id"] not in self.nodes
            ):
                raise ValueError("端点不是已索引的轨道节点；不能使用离线 POI 坐标代替")
        for i in range(1, len(sequence), 2):
            ident = sequence[i]["line_id"]
            if ident not in self.lines:
                raise ValueError("铁路线编号不存在：" + str(ident))
            record = self.lines[ident]
            a, b = sequence[i - 1]["node_id"], sequence[i + 1]["node_id"]
            if a == b or a not in record["graph"] or b not in record["graph"]:
                raise ValueError("起终端点相同，或不在指定铁路线中")
            section_id = sequence[i].get("section_id")
            if section_id:
                section = self.section(section_id, ident)
                if (a, b) == (section["from_node"], section["to_node"]):
                    directed_section = deepcopy(section["path"])
                elif (a, b) == (section["to_node"], section["from_node"]):
                    directed_section = list(
                        {
                            "edge_id": leg["edge_id"],
                            "direction": "reverse"
                            if leg["direction"] == "forward"
                            else "forward",
                        }
                        for leg in reversed(section["path"])
                    )
                else:
                    raise ValueError("所选 RS 区间的真实端点与本行起终点不一致")
                if any(
                    not traversal_allowed(self.edges[leg["edge_id"]], leg["direction"])
                    for leg in directed_section
                ):
                    raise ValueError("所选 RS 区间不允许当前运行方向")
                path.extend(directed_section)
                continue
            previous = {a: None}
            queue = deque([a])
            while queue and b not in previous:
                node = queue.popleft()
                for other, edge_id, direction in sorted(record["graph"][node], key=lambda value: (str(value[0]), value[1], value[2])):
                    edge = self.edges[edge_id]
                    if edge.get("construction") or edge.get("construction_status", "operating") != "operating":
                        continue
                    if not traversal_allowed(edge, direction):
                        continue
                    if other not in previous:
                        previous[other] = (node, edge_id, direction)
                        queue.append(other)
            if b not in previous:
                raise ValueError("指定铁路线在两个端点之间不连通；请补充真实基础设施")
            legs, node = [], b
            while node != a:
                node, edge_id, direction = previous[node]
                legs.append({"edge_id": edge_id, "direction": direction})
            if any(leg["edge_id"] not in self.bridges(ident) for leg in legs):
                raise ValueError(
                    "两个端点之间存在分支 / 多条合法径路；请增加控制端点或选择明确的 RS 区间，不自动采用几何最短路"
                )
            path.extend(reversed(legs))
        if sequence[0]["node_id"] == sequence[-1]["node_id"]:
            raise ValueError("当前通道为单向非循环，反向请另建通道")
        visited = {sequence[0]["node_id"]}
        for leg in path:
            edge = self.edges[leg["edge_id"]]
            a, b = edge_endpoints(edge)
            end = b if leg["direction"] == "forward" else a
            if end in visited:
                raise ValueError("通道组合重复经过端点，当前不支持循环通道")
            visited.add(end)
        return path

    def describe(self, path):
        """Lossless notation of an existing path; retain branch endpoints for clarity."""
        sequence = []
        previous_line = None
        for index, leg in enumerate(path):
            edge = self.edges[leg["edge_id"]]
            edge_a, edge_b = edge_endpoints(edge)
            a, b = (
                (edge_a, edge_b)
                if leg["direction"] == "forward"
                else (edge_b, edge_a)
            )
            ident = self.edge_lines[edge["id"]]
            if index == 0:
                sequence.append({"kind": "endpoint", "node_id": a})
            if ident != previous_line:
                if previous_line is not None:
                    sequence.append({"kind": "endpoint", "node_id": a})
                sequence.append({"kind": "line", "line_id": ident})
            previous_line = ident
        if path:
            sequence.append({"kind": "endpoint", "node_id": b})
        return sequence

    def sections(self):
        """Each maximal degree-two chain reuses original edge IDs; no copied geometry."""
        output = []
        global_degree = defaultdict(int)
        for edge in self.edges.values():
            a, b = edge_endpoints(edge)
            global_degree[a] += 1
            global_degree[b] += 1
        for ident, record in self.lines.items():
            graph, seen = record["graph"], set()

            def role(edge_id):
                edge = self.edges[edge_id]
                return edge.get("track_type") or track_type(edge.get("way_tags", {}))[0]

            endpoints = {
                node
                for node, neighbors in graph.items()
                if len(neighbors) != 2
                or global_degree[node] != 2
                or node in getattr(self, "split_nodes", set())
                or node in getattr(self, "control_nodes", set())
                or len({role(edge_id) for _, edge_id, _ in neighbors}) > 1
            }
            # Remaining degree-two cycles are physical infrastructure too,
            # even though a running corridor may not be a closed loop.
            for start in sorted(endpoints, key=str) + sorted(set(graph) - endpoints, key=str):
                for other, edge_id, direction in graph[start]:
                    if edge_id in seen:
                        continue
                    legs = [{"edge_id": edge_id, "direction": direction}]
                    seen.add(edge_id)
                    end = other
                    while end not in endpoints:
                        options = [v for v in graph[end] if v[1] not in seen]
                        if not options:
                            break
                        end, edge_id, direction = options[0]
                        seen.add(edge_id)
                        legs.append({"edge_id": edge_id, "direction": direction})
                    key = json.dumps(sorted(leg["edge_id"] for leg in legs))
                    section_id = (
                        "RS-" + hashlib.sha256((ident + key).encode()).hexdigest()[:20]
                    )
                    first_edge = self.edges[legs[0]["edge_id"]]
                    category = role(legs[0]["edge_id"])
                    evidence = (
                        first_edge.get("type_evidence")
                        or track_type(first_edge.get("way_tags", {}))[1]
                    )
                    output.append(
                        {
                            "id": section_id,
                            "name": self.lines[ident]["source_name"]
                            + "｜"
                            + self.nodes[start]
                            + "—"
                            + self.nodes[end]
                            + "｜"
                            + category
                            + "｜"
                            + section_id,
                            "track_type": category,
                            "type_evidence": evidence,
                            "construction": bool(first_edge.get("construction")),
                            "length_m": round(
                                sum(
                                    edge_length(self.edges[leg["edge_id"]])
                                    for leg in legs
                                ),
                                3,
                            ),
                            "line_id": ident,
                            "from_node": start,
                            "to_node": end,
                            "path": legs,
                        }
                    )
        return deepcopy(output)
