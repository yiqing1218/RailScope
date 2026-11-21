"""Resolve old RailScope corridors to stable IDs without using geographic proximity."""
import argparse
import heapq
import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from functools import lru_cache
from pathlib import Path


def read_only(path):
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


class Migration:
    def __init__(self, old, canonical):
        self.old = old
        self.canonical = canonical

    @lru_cache(maxsize=4096)
    def old_edge(self, ident):
        row = self.old.execute(
            "SELECT a,b,source_way,direction FROM edges WHERE id=? AND construction=0", (ident,)
        ).fetchone()
        if not row:
            raise ValueError(f"旧物理区段不存在或不可运行：{ident}")
        return row

    @lru_cache(maxsize=2048)
    def modern_way(self, way):
        return self.canonical.execute("""
            SELECT e.id,x.from_node,x.to_node,a.source_ref,b.source_ref,x.forward,x.reverse
            FROM entity e JOIN edge x ON x.id=e.id JOIN node a ON a.id=x.from_node JOIN node b ON b.id=x.to_node
            WHERE e.source_ref=? AND e.kind='rail' AND e.status='operating' AND e.class!='unknown'
            ORDER BY e.id
        """, (f"osm:way:{way}",)).fetchall()

    def source_path(self, ident, start, finish):
        graph = defaultdict(list)
        for edge, a, b, direction, length in self.old.execute("""
            SELECT id,a,b,direction,length_m FROM edges WHERE line_id=? AND construction=0 ORDER BY id
        """, (ident,)):
            if direction in ("both", "forward"):
                graph[a].append((b, edge, "forward", length))
            if direction in ("both", "reverse"):
                graph[b].append((a, edge, "reverse", length))
        heap = [(0, start)]
        distances = {start: 0}
        parents = {}
        while heap:
            distance, node = heapq.heappop(heap)
            if distance != distances[node]:
                continue
            if node == finish:
                break
            for target, edge, direction, length in graph[node]:
                score = distance + max(length, .001)
                if score < distances.get(target, float("inf")):
                    distances[target] = score
                    parents[target] = (node, edge, direction)
                    heapq.heappush(heap, (score, target))
        if finish not in distances or start == finish:
            raise ValueError(f"旧线路 {ident} 上的端点 {start} → {finish} 没有显式连接")
        path = []
        while finish != start:
            node, edge, direction = parents[finish]
            path.append({"edge_id": edge, "direction": direction})
            finish = node
        return list(reversed(path))

    def reassemble(self, route):
        intent = route.get("sequence") or []
        if len(intent) != 3:
            raise ValueError("需逐段审校旧编排中的多个线路组合")
        start, line, stop = intent
        ident = line["line_id"]
        metadata = self.old.execute("SELECT source_name FROM lines WHERE id=?", (ident,)).fetchone()
        if not metadata or not metadata[0] or metadata[0].startswith("未命名轨道"):
            raise ValueError("缺少可验证的旧业务线路身份")
        ways = {x for (x,) in self.old.execute(
            "SELECT DISTINCT source_way FROM edges WHERE line_id=? AND construction=0", (ident,)
        )}
        candidates = []
        for (line_id,) in self.canonical.execute("""
            SELECT e.id FROM entity e JOIN source_object s ON s.ref=e.source_ref
            WHERE e.kind='line' AND e.name=? AND e.status NOT IN ('construction','planned','disused')
              AND json_extract(s.tags,'$.route')='railway'
        """, metadata):
            member_ways = {ref.removeprefix("osm:way:") for (ref,) in self.canonical.execute(
                "SELECT DISTINCT e.source_ref FROM line_member m JOIN entity e ON e.id=m.member_id WHERE m.line_id=? AND e.kind='rail'",
                (line_id,))}
            if len(ways & member_ways) >= max(1, len(ways) // 3):
                candidates.append((line_id, member_ways))
        if len(candidates) != 1:
            raise ValueError("同名现代业务线路缺少唯一的原始轨道成员证据")
        line_id, member_ways = candidates[0]
        directions = defaultdict(list)
        refs = defaultdict(set)
        for way in sorted(member_ways | ways):
            for edge, a, b, ar, br, forward, reverse in self.modern_way(way):
                refs[ar].add(a)
                refs[br].add(b)
                if forward:
                    directions[a].append((b, edge, "forward"))
                if reverse:
                    directions[b].append((a, edge, "reverse"))
        a = refs[f'osm:node:{start["node_id"]}']
        b = refs[f'osm:node:{stop["node_id"]}']
        if len(a) != 1 or len(b) != 1:
            raise ValueError("新版线路图缺少唯一的原始控制端点")
        origin, destination = next(iter(a)), next(iter(b))
        queue = [origin]
        parents = {origin: None}
        for node in queue:
            if node == destination:
                break
            for target, edge, direction in directions[node]:
                if target not in parents:
                    parents[target] = (node, edge, direction)
                    queue.append(target)
        if destination not in parents or origin == destination:
            raise ValueError("当前同源线路及旧线路成员无可用方向连接两个原始端点")
        path = []
        while destination != origin:
            destination, edge, direction = parents[destination]
            path.append({"edge_id": edge, "direction": direction})
        return {"id": route["id"], "name": route["name"], "path": list(reversed(path)),
                "migration_source": f"保留原始端点，按同源业务线路 {line_id} 重新校验方向"}

    def convert(self, route, fallback):
        intent = route.get("sequence") or []
        path = route.get("path") or fallback.get(json.dumps(intent, sort_keys=True))
        method = "旧档已保存的有向物理区段"
        if not path:
            if len(intent) < 3 or len(intent) % 2 != 1:
                raise ValueError("旧档缺少完整端点与线路序列")
            method = "旧线路成员限定的端点编排重算"
            path = []
            for i in range(0, len(intent)-2, 2):
                start, line, stop = intent[i:i+3]
                if (start["kind"], line["kind"], stop["kind"]) != ("endpoint", "line", "endpoint"):
                    raise ValueError("旧档序列需要端点与线路交替")
                path.extend(self.source_path(line["line_id"], start["node_id"], stop["node_id"]))
        spans = []
        for item in path:
            a, b, way, allowed = self.old_edge(item["edge_id"])
            direction = item["direction"]
            if direction not in ("forward", "reverse") or allowed not in ("both", direction):
                raise ValueError(f"旧区段方向无法确认：{item['edge_id']}")
            first, last = (a, b) if direction == "forward" else (b, a)
            if spans and spans[-1][2] != first:
                raise ValueError(f"旧档本身在来源端点 {first} 处不连续")
            if spans and spans[-1][0] == way and spans[-1][3] == direction:
                spans[-1] = (way, spans[-1][1], last, direction)
            else:
                spans.append((way, first, last, direction))
        if not spans or len(spans) > 10000:
            raise ValueError("旧档路径为空或超出预算")
        modern = []
        control = None
        for way, first, last, direction in spans:
            # A span can expand to several new atomic edges. Every choice uses the exact
            # same source Way and exact endpoint references from the saved legacy graph.
            want_start, want_end = f"osm:node:{first}", f"osm:node:{last}"
            eligible = defaultdict(list)
            for edge, a, b, ar, br, fw, rev in self.modern_way(way):
                if direction == "forward" and fw:
                    eligible[ar].append((edge, a, b, br, True))
                elif direction == "reverse" and rev:
                    eligible[br].append((edge, b, a, ar, False))
            visited = set()
            while want_start != want_end:
                candidates = [item for item in eligible[want_start] if control is None or item[1] == control]
                if len(candidates) != 1 or want_start in visited:
                    raise ValueError(f"OSM Way {way} 在 {want_start} → {want_end} 缺少唯一同向区段")
                visited.add(want_start)
                edge, _, control, want_start, forward = candidates[0]
                modern.append({"edge_id": edge, "direction": "forward" if forward else "reverse"})
        record = {
            "id": route["id"], "name": route["name"], "path": modern,
            "migration_source": method,
        }
        return record


def prepare(canonical, legacy_graph, inputs, output):
    routes = {}
    fallback = {}
    for path in inputs:
        source = load(path)
        for row in source.get("routes", []) + source.get("corridors", []):
            routes[row["id"]] = row
            if row.get("path") and row.get("sequence"):
                fallback.setdefault(json.dumps(row["sequence"], sort_keys=True), row["path"])
    migrated, conflicts = [], []
    with closing(read_only(legacy_graph)) as old, closing(read_only(canonical)) as current:
        migration = Migration(old, current)
        for ident, route in routes.items():
            try:
                migrated.append(migration.convert(route, fallback))
            except (ValueError, KeyError) as error:
                try:
                    migrated.append(migration.reassemble(route))
                except (ValueError, KeyError) as reassembly_error:
                    conflicts.append({"id": ident, "name": route.get("name", ident),
                                      "reason": f"{error}。{reassembly_error}"})
    payload = {
        "schema": "railscope.rail-corridors.v1", "source": "本机旧版 RailScope 通道迁移",
        "corridors": migrated, "migration_conflicts": conflicts,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(migrated), len(conflicts)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("canonical", type=Path)
    parser.add_argument("legacy_graph", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("inputs", nargs="+", type=Path)
    args = parser.parse_args()
    migrated, conflicts = prepare(args.canonical, args.legacy_graph, args.inputs, args.output)
    print(f"{migrated} candidates, {conflicts} conflicts")
