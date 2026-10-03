"""Shared geometric transforms; the shape-led pipeline owns diagram assembly."""
from bisect import bisect_right
from collections import defaultdict
import math
from statistics import median


def common_baseline(paths, interval):
    """Median of the dominant parallel trunk at source vertices and core samples.

    Subtracting the same b(x) from all objects removes collective bending but
    retains differences y1(x)-y2(x). There is no per-track straightening.
    """
    lo,hi = interval
    knots = sorted({p[0] for path in paths for p in path} | {lo+(hi-lo)*i/40 for i in range(41)})
    result = []
    for x in knots:
        values = []
        for path in paths:
            samples = []
            for a,b in zip(path,path[1:]):
                if min(a[0],b[0]) <= x <= max(a[0],b[0]) and abs(b[0]-a[0]) > .001:
                    t = (x-a[0])/(b[0]-a[0])
                    samples.append(a[1]+t*(b[1]-a[1]))
            if samples:
                values.append(median(samples))
        if values:
            result.append((x,median(values)))
    return tuple(result)


def reference_paths(repo, keys, raw):
    """Join physical degree-two fragments before measuring a parallel pair.

    Otherwise one side of a pair can momentarily be counted twice at an OSM
    fragment endpoint, shifting the median onto a single rail by one spacing.
    """
    adjacency = defaultdict(list)
    for key in keys:
        edge = repo.edges[key]
        adjacency[edge.from_node_id].append(key)
        adjacency[edge.to_node_id].append(key)
    remaining, paths = set(keys), []
    while remaining:
        endpoints = sorted(node for node,edges in adjacency.items() if len(edges) != 2
                           and any(k in remaining for k in edges))
        first = min(remaining)
        node = endpoints[0] if endpoints else repo.edges[first].from_node_id
        path = []
        while True:
            key = next((k for k in sorted(adjacency[node]) if k in remaining),None)
            if key is None:
                break
            remaining.remove(key)
            edge = repo.edges[key]
            forward = node == edge.from_node_id
            points = raw[key] if forward else raw[key][::-1]
            path.extend(points if not path else points[1:])
            node = edge.to_node_id if forward else edge.from_node_id
            if len(adjacency[node]) != 2:
                break
        if path:
            paths.append(path)
    return paths


def baseline_value(knots, x):
    if not knots:
        return 0.0
    index = bisect_right(knots, (x,math.inf))-1
    if index < 0:
        return knots[0][1]
    if index >= len(knots)-1:
        return knots[-1][1]
    a,b = knots[index:index+2]
    return a[1]+(x-a[0])/(b[0]-a[0])*(b[1]-a[1])


def smooth_baseline(knots):
    """Fit a cubic broad trend; never reproduce the raw median's short steps."""
    if len(knots) < 4 or knots[-1][0]-knots[0][0] < 100:
        return ()
    lo,hi = knots[0][0],knots[-1][0]
    samples = [(lo+(hi-lo)*i/80, -1+2*i/80) for i in range(81)]
    matrix = [[sum(t**(i+j) for _,t in samples) for j in range(4)]
              + [sum(t**i*baseline_value(knots,x) for x,t in samples)] for i in range(4)]
    for column in range(4):
        pivot = max(range(column,4),key=lambda row:abs(matrix[row][column]))
        matrix[column],matrix[pivot] = matrix[pivot],matrix[column]
        denominator = matrix[column][column]
        if abs(denominator) < 1e-10:
            return ()
        matrix[column] = [v/denominator for v in matrix[column]]
        for row in range(4):
            if row != column:
                factor = matrix[row][column]
                matrix[row] = [a-factor*b for a,b in zip(matrix[row],matrix[column])]
    coefficients = [matrix[i][-1] for i in range(4)]
    return tuple((x,sum(c*t**i for i,c in enumerate(coefficients))) for x,t in samples)


def densify(points, x_knots):
    """Split display geometry at warp knots, without splitting NetworkEdges."""
    result = [points[0]]
    for a,b in zip(points,points[1:]):
        if a[0] != b[0]:
            lo,hi = sorted((a[0],b[0]))
            start,stop = bisect_right(x_knots,lo), bisect_right(x_knots,hi)
            middle = x_knots[start:stop]
            if b[0] < a[0]:
                middle = middle[::-1]
            for x in middle:
                t = (x-a[0])/(b[0]-a[0])
                if 0 < t < 1:
                    result.append((x,a[1]+t*(b[1]-a[1])))
        result.append(b)
    return result


def line_parts(value):
    if value.geom_type == 'LineString' and value.length > .1:
        yield tuple(value.coords)
    elif hasattr(value,'geoms'):
        for part in value.geoms:
            yield from line_parts(part)


def build_layout(repo, context=(), options=None):
    """Compatibility entry for the independent shape-led diagram pipeline."""
    try:
        from .station_diagram.pipeline import build_layout as build
    except ImportError:
        from station_diagram.pipeline import build_layout as build
    return build(repo, context, options)
