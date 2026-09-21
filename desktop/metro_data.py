"""Derived UI associations. Raw OSM tags are preserved and never rewritten."""

from collections import defaultdict
from copy import deepcopy
import heapq
import json
import math
from pathlib import Path
import re

try:
    from .geometry import build_demo_path, distance_m
except ImportError:
    from geometry import build_demo_path, distance_m


def iter_geojson_features(path, chunk_size=1024 * 1024):
    """Yield a GeoJSON feature array without retaining the national file."""
    decoder = json.JSONDecoder()
    with Path(path).open("r", encoding="utf-8-sig") as stream:
        buffer = ""
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                raise ValueError("GeoJSON 缺少 features 数组")
            buffer += chunk
            match = re.search(r'"features"\s*:\s*\[', buffer)
            if match:
                buffer = buffer[match.end() :]
                break
            if len(buffer) > chunk_size * 4:
                buffer = buffer[-chunk_size * 2 :]
        while True:
            buffer = buffer.lstrip(" \t\r\n,")
            if buffer.startswith("]"):
                return
            try:
                feature, end = decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                chunk = stream.read(chunk_size)
                if not chunk:
                    raise ValueError("GeoJSON features 数组未完整结束")
                buffer += chunk
                continue
            if not isinstance(feature, dict):
                raise ValueError("GeoJSON feature 必须是对象")
            yield feature
            buffer = buffer[end:]


def station_area_index(stations):
    """One reusable spatial/name index for an entire national import."""
    try:
        from .station_search import name_keys
    except ImportError:
        from station_search import name_keys
    grid = defaultdict(list)
    by_node, names = {}, {}
    for station in stations:
        x, y = station["geometry"]["coordinates"]
        grid[int(x / 0.005), int(y / 0.005)].append(station)
        props = station["properties"]
        if "osm_node_id" in props:
            by_node[props["osm_node_id"]] = station
        names[id(station)] = name_keys(props)
    return grid, by_node, names


def ring_distance(ring, point):
    """Distance to the actual polygon perimeter, not its bounding-box centre."""
    scale = math.cos(math.radians(point[1]))
    best = math.inf
    for a, b in zip(ring, ring[1:] + ring[:1]):
        ax, ay = (a[0] - point[0]) * scale, a[1] - point[1]
        dx, dy = (b[0] - a[0]) * scale, b[1] - a[1]
        norm = dx * dx + dy * dy
        t = max(0, min(1, -(ax * dx + ay * dy) / norm)) if norm else 0
        best = min(best, math.hypot(ax + t * dx, ay + t * dy) * 111195)
    return best


def associate_station_areas(areas, stations, grid=None, index=None):
    try:
        from .station_search import name_keys
    except ImportError:
        from station_search import name_keys
    if index is None:
        index = station_area_index(stations if grid is None else [s for values in grid.values() for s in values])
    grid, by_node, names = index
    for area in areas:
        props, geometry = area["properties"], area["geometry"]
        polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
        explicit = [by_node[node] for node in props.get("member_station_ids", []) if node in by_node]
        inside, candidates = list(explicit), {}
        area_names = name_keys(props)
        # A stop_area has explicit station membership. Do not add unrelated
        # adjacent stations, and never treat a whole route as a stop_area.
        if not explicit:
            for polygon in polygons:
                ring, holes = polygon[0], polygon[1:]
                xs, ys = zip(*ring)
                for a in range(int(min(xs) / 0.005) - 1, int(max(xs) / 0.005) + 2):
                    for b in range(int(min(ys) / 0.005) - 1, int(max(ys) / 0.005) + 2):
                        for station in grid.get((a, b), []):
                            point = station["geometry"]["coordinates"]
                            if any(contains(hole, point) for hole in holes):
                                continue
                            if contains(ring, point):
                                inside.append(station)
                                continue
                            gap = ring_distance(ring, point)
                            same_name = bool(area_names & names[id(station)])
                            if (same_name and gap <= 350) or (not area_names and gap <= 80):
                                candidates[id(station)] = station
            if not inside and candidates:
                # Shared names may represent duplicate platforms of one stop;
                # competing station names stay unresolved instead of nearest-wins.
                groups = {frozenset(names[key]) for key in candidates}
                if len(groups) == 1 and next(iter(groups)):
                    inside = list(candidates.values())
        props["route_relation_ids"] = sorted({r for station in inside for r in station["properties"].get("route_relation_ids", [])})
        props["associated_station_ids"] = sorted({station["properties"]["osm_node_id"] for station in inside if "osm_node_id" in station["properties"]})
        props["association_candidate_station_ids"] = sorted({station["properties"]["osm_node_id"] for station in candidates.values() if "osm_node_id" in station["properties"]}) if not inside else []
        props["association_source"] = "OSM 关系成员关联" if explicit else "名称与真实边界空间派生关联，非 OSM 原始标签" if inside else "尚未关联到线路"
        props["association_verification_status"] = "osm_derived" if explicit else "automatic_match" if inside else "unresolved"
        props["association_confidence"] = .95 if explicit else .5 if inside else None
        props["association_version"] = "station-area-v2"
    return areas


def display_stations(stations, radius_m=350, registry=None):
    """One logical anchor; raw members and legacy source aliases stay inspectable.

    Desktop supplies a persistent StationRegistry. Pure callers can use the
    preview mode, whose legacy alias is explicitly not a canonical business ID.
    """
    from railscope.services.stations import (
        anchor_feature, group_station_sources,
    )

    groups = group_station_sources(stations, radius_m)
    resolved = registry.resolve(groups) if registry else [
        (None, group["members"]) for group in groups
    ]
    result = []
    for station, members in resolved:
        canonical = anchor_feature(members)
        feature = deepcopy(canonical)
        props = feature["properties"]
        ids = sorted(s["properties"]["osm_node_id"] for s in members)
        legacy_id = "metro-station/" + str(ids[0])
        props["infrastructure_id"] = station.id if station else legacy_id
        props["station_id"] = station.id if station else None
        props["legacy_station_id"] = legacy_id
        props["verification_status"] = station.verification_status if station else "automatic_match"
        props["confidence"] = station.confidence if station else None
        props["identity_status"] = "canonical" if station else "source_preview"
        props["associated_station_ids"] = ids
        props["route_relation_ids"] = sorted({
            rid for s in members for rid in s["properties"].get("route_relation_ids", [])
        })
        props["source_member_ids"] = list(station.source_member_ids) if station else [
            f"osm:node:{s['properties']['osm_node_id']}" for s in members
        ]
        props["source_members"] = deepcopy(members)
        result.append(feature)
    return result


def display_station_areas(areas, stations, registry=None):
    """Names and station identities are derived; source names/tags/geometry stay intact."""
    markers = display_stations(stations, registry=registry)
    by_node = {
        node: marker["properties"]
        for marker in markers
        for node in marker["properties"]["associated_station_ids"]
    }
    result = deepcopy(areas)
    for feature in result:
        props = feature["properties"]
        kind = "way" if "osm_way_id" in props else "relation"
        number = props.get("osm_" + kind + "_id")
        if number is None:
            continue
        props["infrastructure_id"] = f"{kind}/{number}"
        matched = {
            by_node[node]["infrastructure_id"]: by_node[node]
            for node in props.get("associated_station_ids", [])
            if node in by_node
        }
        props["station_ids"] = sorted(matched)
        if registry:
            area = registry.register_area(
                f"osm:{kind}:{number}", props["station_ids"],
                props.get("boundary_kind", "station_outline"), feature["geometry"],
            )
            props["source_object_id"] = props["infrastructure_id"]
            props["infrastructure_id"] = area.id
            props["area_id"] = area.id
        props.setdefault("source_name", props.get("name", ""))
        names = (
            " / ".join(sorted({p["name"] for p in matched.values()}))
            or props["source_name"]
            or "未关联地铁站"
        )
        label = {"platform": "站台", "station_building": "车站建筑"}.get(
            props.get("boundary_kind"), "站区"
        )
        tags = props.get("way_tags", props.get("station_area_tags", {}))
        ref = tags.get("ref", tags.get("local_ref", ""))
        props["display_name"] = f"{names} · {label}{ref}".rstrip()
        props["name"] = props["display_name"]
    return result


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
                if (
                    ref == "4"
                    and path["coordinates"][0] == path["coordinates"][-1]
                    and projected
                ):
                    # An explicitly closed physical ring needs its return stop;
                    # this is an operating occurrence, not a second map POI.
                    projected.append(
                        {**projected[0], "distance_m": round(path["length_m"], 3)}
                    )
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
