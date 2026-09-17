"""Derived UI associations. Raw OSM tags are preserved and never rewritten."""

from collections import defaultdict
import heapq
import math

try:
    from .geometry import build_demo_path, distance_m
except ImportError:
    from geometry import build_demo_path, distance_m


def associate_station_areas(areas, stations):
    grid = defaultdict(list)
    for station in stations:
        x, y = station["geometry"]["coordinates"]
        grid[int(x / 0.005), int(y / 0.005)].append(station)
    for area in areas:
        geometry = area["geometry"]
        polygons = (
            geometry["coordinates"]
            if geometry["type"] == "MultiPolygon"
            else [geometry["coordinates"]]
        )
        inside = []
        for polygon in polygons:
            ring, holes = polygon[0], polygon[1:]
            xs, ys = zip(*ring)
            center = [(min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2]
            nearby = [
                s
                for a in range(int(min(xs) / 0.005) - 1, int(max(xs) / 0.005) + 2)
                for b in range(int(min(ys) / 0.005) - 1, int(max(ys) / 0.005) + 2)
                for s in grid[a, b]
                if not any(
                    contains(hole, s["geometry"]["coordinates"]) for hole in holes
                )
            ]
            matched = [
                s for s in nearby if contains(ring, s["geometry"]["coordinates"])
            ]
            if not matched and nearby:
                nearest = min(
                    nearby,
                    key=lambda s: distance_m(center, s["geometry"]["coordinates"]),
                )
                if distance_m(center, nearest["geometry"]["coordinates"]) <= 150:
                    name = nearest["properties"].get("name")
                    matched = [
                        s
                        for s in nearby
                        if s["properties"].get("name") == name
                        and distance_m(center, s["geometry"]["coordinates"]) <= 250
                    ]
            inside.extend(matched)
        ids = sorted(
            {r for s in inside for r in s["properties"].get("route_relation_ids", [])}
        )
        area["properties"]["route_relation_ids"] = ids
        area["properties"]["association_source"] = (
            "空间派生关联，非 OSM 原始标签" if ids else "尚未关联到线路"
        )
    return areas


def contains(ring, point):
    x, y = point
    inside = False
    for a, b in zip(ring, ring[1:] + ring[:1]):
        if (a[1] > y) != (b[1] > y) and x < (b[0] - a[0]) * (y - a[1]) / (
            b[1] - a[1]
        ) + a[0]:
            inside = not inside
    return inside


def graph_path(features, origin, destination):
    graph = defaultdict(dict)
    points = {}
    for feature in features:
        coordinates = feature["geometry"]["coordinates"]
        for a, b in zip(coordinates, coordinates[1:]):
            ka, kb = tuple(round(v, 7) for v in a), tuple(round(v, 7) for v in b)
            points[ka] = a
            points[kb] = b
            weight = distance_m(a, b)
            graph[ka][kb] = weight
            graph[kb][ka] = weight
    start = min(points, key=lambda key: distance_m(points[key], origin))
    end = min(points, key=lambda key: distance_m(points[key], destination))
    if (
        distance_m(points[start], origin) > 300
        or distance_m(points[end], destination) > 300
    ):
        raise ValueError("端站与线路距离过大")
    queue = [(0, start)]
    costs = {start: 0}
    parents = {}
    while queue:
        cost, node = heapq.heappop(queue)
        if node == end:
            break
        if cost > costs[node]:
            continue
        for other, weight in graph[node].items():
            proposed = cost + weight
            if proposed < costs.get(other, math.inf):
                costs[other] = proposed
                parents[other] = node
                heapq.heappush(queue, (proposed, other))
    if end not in parents:
        raise ValueError("端站之间没有连续的 OSM 轨道路径")
    keys = [end]
    while keys[-1] != start:
        keys.append(parents[keys[-1]])
    coordinates = [points[k] for k in keys[::-1]]
    cumulative = [0]
    for a, b in zip(coordinates, coordinates[1:]):
        cumulative.append(cumulative[-1] + distance_m(a, b))
    return {
        "coordinates": coordinates,
        "cumulative": cumulative,
        "length_m": cumulative[-1],
    }


def project_distance(path, point):
    scale = math.cos(math.radians(point[1]))
    best = (math.inf, 0)
    for i, (a, b) in enumerate(zip(path["coordinates"], path["coordinates"][1:])):
        ax, ay = (a[0] - point[0]) * scale, a[1] - point[1]
        dx, dy = (b[0] - a[0]) * scale, b[1] - a[1]
        norm = dx * dx + dy * dy
        t = max(0, min(1, -(ax * dx + ay * dy) / norm)) if norm else 0
        gap = math.hypot(ax + t * dx, ay + t * dy) * 111195
        distance = path["cumulative"][i] + t * (
            path["cumulative"][i + 1] - path["cumulative"][i]
        )
        if gap < best[0]:
            best = (gap, distance)
    return best


def build_shanghai_lines(catalog, routes, stations):
    by_relation = defaultdict(list)
    for f in routes:
        by_relation[f["properties"]["route_relation_id"]].append(f)
    by_node = {s["properties"]["osm_node_id"]: s for s in stations}
    groups = defaultdict(list)
    for route in catalog:
        if "上海" in str(route.get("network", "")):
            groups[str(route.get("ref") or route["name"])].append(route)
    lines = []
    for ref, relations in sorted(
        groups.items(),
        key=lambda p: (not p[0].isdigit(), int(p[0]) if p[0].isdigit() else p[0]),
    ):
        variants = []
        errors = []
        for relation in relations:
            rid = relation["osm_relation_id"]
            raw_stations = []
            seen = set()
            for member in relation["members"]:
                node = (
                    by_node.get(member["ref"])
                    if member["type"] == "n" and member["role"].startswith("stop")
                    else None
                )
                if node:
                    name = node["properties"]["name"]
                    if name not in seen:
                        seen.add(name)
                        raw_stations.append(node)
            if len(raw_stations) < 2:
                continue
            try:
                try:
                    path = build_demo_path(by_relation[rid], rid)
                except ValueError:
                    path = graph_path(
                        by_relation[rid],
                        raw_stations[0]["geometry"]["coordinates"],
                        raw_stations[-1]["geometry"]["coordinates"],
                    )
                projected = []
                for node in raw_stations:
                    gap, distance = project_distance(
                        path, node["geometry"]["coordinates"]
                    )
                    if gap > 300:
                        raise ValueError("车站无法关联到该路径")
                    projected.append(
                        {
                            "id": str(node["properties"]["osm_node_id"]),
                            "name": node["properties"]["name"],
                            "distance_m": round(distance, 3),
                            "coordinates": node["geometry"]["coordinates"],
                        }
                    )
                # Physical path orientation is independent of OSM stop order.
                projected.sort(key=lambda s: s["distance_m"])
                variants.append(
                    {
                        "path": path,
                        "stations": projected,
                        "relation_id": rid,
                        "source_name": relation["name"],
                    }
                )
            except ValueError as error:
                errors.append(str(error))
        selected = (
            max(variants, key=lambda v: v["path"]["length_m"])
            if variants
            else {"path": None, "stations": [], "relation_id": None}
        )
        if ref == "1":
            selected = next(
                (v for v in variants if v["relation_id"] == 199200), selected
            )
        lines.append(
            {
                "id": "sh-" + ref,
                "ref": ref,
                "name": ref + "号线" if ref.isdigit() else ref,
                "color": relations[0]["display_color"],
                **selected,
                "variants": variants,
                "geometry_errors": errors,
                "official": False,
            }
        )
    return lines
