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


def test_name_alias_matches_near_real_boundary_but_ambiguity_stays_unresolved():
    area = {
        "properties": {"station_area_tags": {"name:zh": "人民广场站", "alt_name": "广场东站"}},
        "geometry": {"type": "Polygon", "coordinates": [[
            [121, 31], [121.001, 31], [121.001, 31.001], [121, 31.001], [121, 31]
        ]]},
    }
    station = {
        "properties": {
            "osm_node_id": 1,
            "name": "People's Square",
            "station_tags": {"name:zh": "人民广场", "name:en": "People's Square"},
            "route_relation_ids": [10],
        },
        "geometry": {"coordinates": [121.0015, 31.0005]},
    }
    matched = associate_station_areas([deepcopy(area)], [station])[0]["properties"]
    assert matched["associated_station_ids"] == [1]
    assert matched["association_verification_status"] == "automatic_match"

    competing = deepcopy(station)
    competing["properties"] = {**competing["properties"], "osm_node_id": 2, "name": "广场东站", "station_tags": {"name": "广场东站"}}
    unresolved = associate_station_areas([deepcopy(area)], [station, competing])[0]["properties"]
    assert unresolved["associated_station_ids"] == []
    assert unresolved["association_candidate_station_ids"] == [1, 2]


def test_all_multipolygon_parts_are_associated_and_holes_are_excluded():
    def ring(x):
        return [[x, 31], [x + 0.02, 31], [x + 0.02, 31.02], [x, 31.02], [x, 31]]

    area = {
        "properties": {},
        "geometry": {
            "type": "MultiPolygon",
            "coordinates": [
                [
                    ring(121),
                    [
                        [121.004, 31.004],
                        [121.016, 31.004],
                        [121.016, 31.016],
                        [121.004, 31.016],
                        [121.004, 31.004],
                    ],
                ],
                [ring(122)],
            ],
        },
    }
    stations = [
        {
            "properties": {"name": "B", "route_relation_ids": [2]},
            "geometry": {"coordinates": [122.01, 31.01]},
        },
        {
            "properties": {"name": "courtyard", "route_relation_ids": [3]},
            "geometry": {"coordinates": [121.01, 31.01]},
        },
    ]
    assert associate_station_areas([area], stations)[0]["properties"][
        "route_relation_ids"
    ] == [2]


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
