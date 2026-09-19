"""Distance-based geometry interpolation shared by rail and metro presenters."""
from bisect import bisect_right
from math import asin, cos, radians, sin, sqrt


def distance_m(a, b):
    lat1, lat2 = radians(a[1]), radians(b[1])
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin(radians(b[0] - a[0]) / 2) ** 2
    return 6371008.8 * 2 * asin(min(1, sqrt(h)))


def interpolate(path, distance):
    """Use an already measured polyline; accepts the legacy desktop path dict."""
    coordinates, cumulative = path['coordinates'], path['cumulative']
    if len(coordinates) < 2 or len(coordinates) != len(cumulative):
        raise ValueError('a measured polyline needs at least two coordinates')
    distance = max(0, min(path['length_m'], distance))
    index = min(len(coordinates) - 2, max(0, bisect_right(cumulative, distance) - 1))
    a, b = coordinates[index:index + 2]
    span = cumulative[index + 1] - cumulative[index]
    ratio = (distance - cumulative[index]) / span if span else 0
    return [a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio]


def route_coordinate(repo, edge_refs, distance):
    """Convert route distance to a point on its real, direction-aware edge shape."""
    if not edge_refs:
        raise ValueError('empty physical path')
    distance = max(0, min(edge_refs[-1].end_distance_m, distance))
    ref = next((ref for ref in edge_refs if distance <= ref.end_distance_m), edge_refs[-1])
    edge = repo.edges[ref.edge_id]
    coordinates = edge.coordinates if ref.forward else edge.coordinates[::-1]
    cumulative = [0.0]
    for a, b in zip(coordinates, coordinates[1:]):
        cumulative.append(cumulative[-1] + distance_m(a, b))
    if cumulative[-1] <= 0 or edge.length_m <= 0:
        raise ValueError('zero-length physical edge')
    geometry_distance = (distance - ref.start_distance_m) / edge.length_m * cumulative[-1]
    return interpolate({'coordinates': coordinates, 'cumulative': cumulative, 'length_m': cumulative[-1]}, geometry_distance)
