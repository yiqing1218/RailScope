"""Measured polyline interpolation used by the single-train demonstration."""

import bisect
import math


def distance_m(a, b):
    lat1, lat2 = math.radians(a[1]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, math.radians(b[0] - a[0])
    h = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 6371008.8 * 2 * math.asin(min(1, math.sqrt(h)))


def build_demo_path(features, relation_id=199200):
    selected = sorted(
        (
            f
            for f in features
            if f["properties"].get("route_relation_id") == relation_id
        ),
        key=lambda f: f["properties"].get("member_sequence", 0),
    )
    path = []
    for feature in selected:
        coordinates = list(feature["geometry"]["coordinates"])
        if path and distance_m(path[-1], coordinates[-1]) < distance_m(
            path[-1], coordinates[0]
        ):
            coordinates.reverse()
        if path and distance_m(path[-1], coordinates[0]) > 100:
            raise ValueError("上海 1 号线几何不连续，无法进行沿线演示")
        for point in coordinates:
            if not path or point != path[-1]:
                path.append(point)
    if len(path) < 2:
        raise ValueError("未找到上海地铁 1 号线的连续几何")
    cumulative = [0.0]
    for a, b in zip(path, path[1:]):
        cumulative.append(cumulative[-1] + distance_m(a, b))
    return {
        "coordinates": path,
        "cumulative": cumulative,
        "length_m": cumulative[-1],
        "relation_id": relation_id,
    }


def interpolate(path, distance):
    distance = max(0, min(path["length_m"], distance))
    index = min(
        len(path["coordinates"]) - 2,
        max(0, bisect.bisect_right(path["cumulative"], distance) - 1),
    )
    a, b = path["coordinates"][index : index + 2]
    span = path["cumulative"][index + 1] - path["cumulative"][index]
    ratio = (distance - path["cumulative"][index]) / span if span else 0
    return [a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio]
