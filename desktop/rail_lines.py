"""Reusable named infrastructure, endpoint-delimited sections and corridor notation."""

from collections import defaultdict, deque
from copy import deepcopy
import hashlib
import json
import csv
import io
import heapq

try:
    from .geometry import distance_m
    from .rail_categories import track_type
except ImportError:
    from geometry import distance_m
    from rail_categories import track_type

RESOLUTION_KEY = "railscope.org/line-resolution"


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
    "to_node",
)


def import_corridor_csv(text):
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if reader.fieldnames != list(CORRIDOR_COLUMNS):
        raise ValueError("通道 CSV 表头必须严格为：" + ",".join(CORRIDOR_COLUMNS))
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
                "extensions": {RESOLUTION_KEY: {"policy": "mainline"}},
            },
        )
        sequence = route["sequence"]
        if row["corridor_name"] != route["name"] or row["segment"] != str(
            (len(sequence) + 1) // 2 if sequence else 1
        ):
            raise ValueError("通道名称不一致或组合段序号须从 1 连续递增")
        if not row["from_node"].isdigit() or not row["to_node"].isdigit():
            raise ValueError("通道端点必须为整数轨道节点")
        a, b = int(row["from_node"]), int(row["to_node"])
        if sequence and sequence[-1]["node_id"] != a:
            raise ValueError("相邻行必须共用端点")
        if not sequence:
            sequence.append({"kind": "endpoint", "node_id": a})
        sequence.extend(
            [
                {"kind": "line", "line_id": row["line_id"]},
                {"kind": "endpoint", "node_id": b},
            ]
        )
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
        self.names = dict(names or {})
        labels = {
            p["properties"]["osm_node_id"]: p["properties"].get("name", "")
            for p in points
            if "osm_node_id" in p.get("properties", {})
        }
        for point in points:
            props = point.get("properties", {})
            kind = {
                "switch": "道岔",
                "junction": "线路所",
                "station": "车站轨道节点",
                "halt": "停靠节点",
                "signal": "信号节点",
            }.get(props.get("kind"))
            if (
                kind
                and props.get("osm_node_id")
                and not labels.get(props["osm_node_id"])
            ):
                labels[props["osm_node_id"]] = f"{kind} {props['osm_node_id']}"
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
            a, b = edge["from_node"], edge["to_node"]
            record["graph"][a].append((b, edge["id"], "forward"))
            record["graph"][b].append((a, edge["id"], "reverse"))
            for node in (a, b):
                self.nodes[node] = labels.get(node) or f"轨道端点 {node}"
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
        path = []
        for i, entry in enumerate(sequence):
            required = {"kind", "node_id"} if i % 2 == 0 else {"kind", "line_id"}
            if (
                not isinstance(entry, dict)
                or set(entry) != required
                or entry["kind"] != ("endpoint" if i % 2 == 0 else "line")
            ):
                raise ValueError("通道表格字段或交替顺序无效")
            if i % 2 == 0 and (
                type(entry["node_id"]) is not int or entry["node_id"] not in self.nodes
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
            previous = {a: None}
            if policy == "mainline":
                queue, costs = [(0.0, a)], {a: 0.0}
                settled = set()
                while queue:
                    cost, node = heapq.heappop(queue)
                    if node in settled:
                        continue
                    settled.add(node)
                    if node == b:
                        break
                    for other, edge_id, direction in sorted(record["graph"][node]):
                        if self.edges[edge_id].get("construction"):
                            continue
                        candidate = cost + edge_length(self.edges[edge_id])
                        if candidate < costs.get(other, float("inf")):
                            costs[other] = candidate
                            previous[other] = (node, edge_id, direction)
                            heapq.heappush(queue, (candidate, other))
            else:
                queue = deque([a])
                while queue and b not in previous:
                    node = queue.popleft()
                    for other, edge_id, direction in record["graph"][node]:
                        if other not in previous:
                            previous[other] = (node, edge_id, direction)
                            queue.append(other)
            if b not in previous:
                raise ValueError("指定铁路线在两个端点之间不连通；请补充真实基础设施")
            legs, node = [], b
            while node != a:
                node, edge_id, direction = previous[node]
                legs.append({"edge_id": edge_id, "direction": direction})
            direct = [v for v in record["graph"][a] if v[0] == b]
            if policy == "strict" and len(direct) == 1:
                legs = [{"edge_id": direct[0][1], "direction": direct[0][2]}]
            elif policy == "strict" and any(
                leg["edge_id"] not in self.bridges(ident) for leg in legs
            ):
                raise ValueError(
                    "两个端点之间存在分支 / 多条股道，请增加明确的岔道端点，不自动猜测"
                )
            path.extend(reversed(legs))
        if sequence[0]["node_id"] == sequence[-1]["node_id"]:
            raise ValueError("当前通道为单向非循环，反向请另建通道")
        visited = {sequence[0]["node_id"]}
        for leg in path:
            edge = self.edges[leg["edge_id"]]
            end = (
                edge["to_node"] if leg["direction"] == "forward" else edge["from_node"]
            )
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
            a, b = (
                (edge["from_node"], edge["to_node"])
                if leg["direction"] == "forward"
                else (edge["to_node"], edge["from_node"])
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
            global_degree[edge["from_node"]] += 1
            global_degree[edge["to_node"]] += 1
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
                or not self.nodes[node].startswith("轨道端点 ")
                or len({role(edge_id) for _, edge_id, _ in neighbors}) > 1
            }
            # Remaining degree-two cycles are physical infrastructure too,
            # even though a running corridor may not be a closed loop.
            for start in sorted(endpoints) + sorted(set(graph) - endpoints):
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
                            "name": self.lines[ident]["name"]
                            + " · "
                            + category
                            + " · "
                            + self.nodes[start]
                            + " → "
                            + self.nodes[end]
                            + " · "
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
