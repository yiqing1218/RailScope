"""Desktop path assembly using the shared backend simulation geometry."""

from railscope.services.simulation.geometry import distance_m, interpolate


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
