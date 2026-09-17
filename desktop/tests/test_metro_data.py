from copy import deepcopy
from desktop.metro_data import associate_station_areas, graph_path
import pytest


def test_station_area_association_preserves_real_geometry_and_tags():
    area = {
        "type": "Feature",
        "properties": {"way_tags": {"railway": "station", "name": "A"}},
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[121, 31], [121.001, 31], [121.001, 31.001], [121, 31.001], [121, 31]]
            ],
        },
    }
    original = deepcopy(area)
    station = {
        "properties": {"name": "A", "route_relation_ids": [199200, 199201]},
        "geometry": {"type": "Point", "coordinates": [121.0005, 31.0005]},
    }
    result = associate_station_areas([area], [station])[0]
    assert result["geometry"] == original["geometry"]
    assert result["properties"]["way_tags"] == original["properties"]["way_tags"]
    assert result["properties"]["route_relation_ids"] == [199200, 199201]
    assert "派生" in result["properties"]["association_source"]


def test_unmatched_areas_do_not_invent_route_membership():
    area = {
        "properties": {},
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[121, 31], [121.001, 31], [121, 31.001], [121, 31]]],
        },
    }
    assert (
        associate_station_areas([area], [])[0]["properties"]["route_relation_ids"] == []
    )


def test_graph_reorders_osm_ways_without_drawing_missing_links():
    def way(coords):
        return {"geometry": {"coordinates": coords}}

    path = graph_path(
        [way([[121.01, 31], [121.02, 31]]), way([[121, 31], [121.01, 31]])],
        [121, 31],
        [121.02, 31],
    )
    assert path["coordinates"] == [[121, 31], [121.01, 31], [121.02, 31]]
    with pytest.raises(ValueError):
        graph_path(
            [way([[121, 31], [121.001, 31]]), way([[121.02, 31], [121.021, 31]])],
            [121, 31],
            [121.021, 31],
        )
