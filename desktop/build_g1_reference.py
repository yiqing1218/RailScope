"""Build the small portable G1 reference from an installed OSM rail index.

Only follows real node-connected edges. No artificial gap bridges are allowed.
Run explicitly during asset maintenance, never at application startup.
"""

import argparse
from collections import defaultdict
import heapq
import json
from pathlib import Path
import sqlite3

try:
    from .geometry import distance_m
    from .rail import compile_rail_plan
except ImportError:
    from geometry import distance_m
    from rail import compile_rail_plan

STOPS = [
    ("北京南", "06:30", "06:30", [116.3723994, 39.8634831]),
    ("沧州西", "07:18", "07:20", [116.7619627, 38.3062792]),
    ("德州东", "07:45", "07:47", [116.4560824, 37.4087582]),
    ("曲阜东", "08:34", "08:36", [117.064341, 35.5565465]),
    ("南京南", "10:13", "10:15", [118.7930516, 31.970797]),
    ("苏州北", "10:59", "11:01", [120.638926, 31.4237197]),
    ("上海虹桥", "11:24", "11:24", [121.3162004, 31.1959782]),
]


def build(directory, output):
    edges = {}
    with sqlite3.connect(str(Path(directory) / "rail.sqlite")) as db:
        for row in db.execute(
            "SELECT data FROM edges WHERE json_extract(data,'$.way_tags.highspeed')='yes' OR json_extract(data,'$.way_tags.name')='京沪高铁'"
        ):
            e = json.loads(row[0])
            x, y = e["coordinates"][0]
            if not e["construction"] and 115.8 <= x <= 122 and 30.9 <= y <= 40.1:
                edges[e["id"]] = e
        for _, _, _, (x, y) in STOPS:
            way_ids = [
                json.loads(r[0])["properties"]["osm_way_id"]
                for r in db.execute(
                    "SELECT f.data FROM features f JOIN bounds b ON f.id=b.id WHERE f.kind='rail' AND b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=?",
                    (x - 0.035, x + 0.035, y - 0.025, y + 0.025),
                )
            ]
            for ident in way_ids:
                for row in db.execute(
                    "SELECT data FROM edges WHERE id GLOB ?", (f"w{ident}:*",)
                ):
                    e = json.loads(row[0])
                    if not e["construction"]:
                        edges[e["id"]] = e
    adjacency = defaultdict(list)
    nodes = {}
    for e in edges.values():
        a, b = e["from_node"], e["to_node"]
        nodes[a], nodes[b] = e["coordinates"][0], e["coordinates"][-1]
        length = sum(
            distance_m(x, y) for x, y in zip(e["coordinates"], e["coordinates"][1:])
        )
        weight = length * (
            1
            if e["way_tags"].get("name") == "京沪高铁"
            else 3
            if e["way_tags"].get("highspeed") == "yes"
            else 20
        )
        adjacency[a].append((b, e["id"], "forward", weight))
        adjacency[b].append((a, e["id"], "reverse", weight))
    # Select near-station anchors only on the largest connected network.
    remaining = set(nodes)
    largest = set()
    while remaining:
        seed = remaining.pop()
        component = {seed}
        queue = [seed]
        while queue:
            a = queue.pop()
            for b, *_ in adjacency[a]:
                if b in remaining:
                    remaining.remove(b)
                    component.add(b)
                    queue.append(b)
        if len(component) > len(largest):
            largest = component
    main_nodes = {
        n
        for e in edges.values()
        if e["way_tags"].get("name") == "京沪高铁"
        for n in (e["from_node"], e["to_node"])
    } & largest
    terminals = [
        min(main_nodes, key=lambda n: distance_m(nodes[n], stop[3]))
        for stop in (STOPS[0], STOPS[-1])
    ]
    path = []
    # One simple through path first. Independent nearest-POI anchors can lie
    # on sidings and introduce fictitious station reversals into the demo.
    for start, end in [(terminals[0], terminals[1])]:
        distances = {start: 0}
        previous = {}
        queue = [(0, start)]
        while queue:
            cost, a = heapq.heappop(queue)
            if cost != distances[a]:
                continue
            if a == end:
                break
            for b, ident, direction, weight in adjacency[a]:
                proposal = cost + weight
                if proposal < distances.get(b, float("inf")):
                    distances[b] = proposal
                    previous[b] = (a, ident, direction)
                    heapq.heappush(queue, (proposal, b))
        if end not in previous:
            raise ValueError("OSM 径路不连续，不能构建示例")
        legs = []
        a = end
        while a != start:
            a, ident, direction = previous[a]
            legs.append({"edge_id": ident, "direction": direction})
        path.extend(legs[::-1])
    path_nodes, path_coords = [], []
    for leg in path:
        edge = edges[leg["edge_id"]]
        ids, coords = edge["node_ids"], edge["coordinates"]
        if leg["direction"] == "reverse":
            ids, coords = ids[::-1], coords[::-1]
        path_nodes.extend(ids if not path_nodes else ids[1:])
        path_coords.extend(coords if not path_coords else coords[1:])
    anchors, offsets = [], []
    begin = 0
    for index, stop in enumerate(STOPS):
        position = (
            0
            if index == 0
            else len(path_nodes) - 1
            if index == len(STOPS) - 1
            else min(
                range(begin, len(path_nodes)),
                key=lambda i: distance_m(path_coords[i], stop[3]),
            )
        )
        anchors.append(path_nodes[position])
        offsets.append(round(distance_m(path_coords[position], stop[3]), 1))
        begin = position + 1
    if max(offsets) > 800:
        raise ValueError(f"参考干线路径偏离站区过远，不能伪造连接：{offsets}")
    seconds = lambda t: int(t[:2]) * 3600 + int(t[3:]) * 60
    notice = "G1 公开时刻参考（资料更新2026-08-14，查阅2026-09-17）；非12306实时查询。OSM连通径路为参考，非实际股道/联锁进路；站间匀速插值，非真实调度运行图。"
    payload = {
        "schema": "railscope.rail-plan.v1",
        "service_date": "2026-09-17",
        "timezone": "Asia/Shanghai",
        "source": notice,
        "extensions": {
            "railscope.org/reference": {
                "timetable_url": "https://www.gaotie.com.cn/checi/G1.html",
                "timetable_verified_by_12306": False,
                "geometry_source": "© OpenStreetMap contributors / Geofabrik China 2026-09-16 / ODbL 1.0",
                "path_status": "connected_osm_reference_not_dispatch_route",
                "anchor_offsets_m": offsets,
            }
        },
        "required_capabilities": [],
        "trains": [
            {
                "id": "G1",
                "path": path,
                "stops": [
                    {
                        "node_id": n,
                        "arrival_s": seconds(arr),
                        "departure_s": seconds(dep),
                    }
                    for n, (_, arr, dep, _) in zip(anchors, STOPS)
                ],
                "extensions": {},
            }
        ],
    }
    points = [
        {"properties": {"osm_node_id": n, "name": s[0]}} for n, s in zip(anchors, STOPS)
    ]
    selected = [edges[ident] for ident in dict.fromkeys(leg["edge_id"] for leg in path)]
    plan, lines = compile_rail_plan(payload, selected, points)
    result = {"plan": payload, "edges": selected, "points": points, "notice": notice}
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    print(
        {
            "edges": len(selected),
            "length_km": round(lines[0]["path"]["length_m"] / 1000, 2),
            "anchors_m": offsets,
            "bytes": output.stat().st_size,
            "names": [s["name"] for s in lines[0]["stations"]],
        }
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.directory, args.output)
