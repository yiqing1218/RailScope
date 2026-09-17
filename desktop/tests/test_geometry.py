import pytest

from desktop.geometry import build_demo_path, distance_m, interpolate


def segment(points, sequence, relation=199200):
    return {
        "properties": {"route_relation_id": relation, "member_sequence": sequence},
        "geometry": {"type": "LineString", "coordinates": points},
    }


def test_path_orders_reverses_and_excludes_other_directions():
    a, b, c = [121.4, 31.2], [121.401, 31.2], [121.402, 31.2]
    path = build_demo_path(
        [segment([c, b], 2), segment([a, b], 1), segment([[0, 0], [1, 1]], 1, 99)]
    )
    assert path["coordinates"] == [a, b, c]
    assert path["length_m"] == pytest.approx(distance_m(a, b) + distance_m(b, c))
    assert interpolate(path, -10) == a
    assert interpolate(path, path["length_m"] + 10) == c
    assert interpolate(path, path["cumulative"][1]) == b
    assert interpolate(path, path["cumulative"][1] / 2) == pytest.approx(
        [121.4005, 31.2]
    )


def test_path_rejects_missing_route_and_discontinuity():
    with pytest.raises(ValueError, match="未找到"):
        build_demo_path([])
    with pytest.raises(ValueError, match="不连续"):
        build_demo_path(
            [segment([[0, 0], [0.001, 0]], 1), segment([[1, 0], [1.001, 0]], 2)]
        )
