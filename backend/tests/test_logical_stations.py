from railscope.services.stations import StationRegistry, group_station_sources


def feature(node, name, lon, network="上海地铁", stop_area=99, kind="stop_position"):
    return {
        "type": "Feature",
        "properties": {
            "osm_node_id": node,
            "name": name,
            "station_tags": {
                "name": name,
                "network": network,
                "public_transport": kind,
                "railway": "station" if kind == "station" else "stop",
            },
            "stop_area_relation_ids": [stop_area] if stop_area else [],
            "station_relation_ids": [],
        },
        "geometry": {"type": "Point", "coordinates": [lon, 31.0]},
    }


def test_stop_area_members_form_one_stable_logical_station(tmp_path):
    sources = [feature(2, "人民广场", 121.471), feature(1, "人民广场站", 121.47, kind="station")]
    registry = StationRegistry(tmp_path / "workspace.sqlite")
    first = registry.resolve(group_station_sources(sources))
    second = registry.resolve(group_station_sources(list(reversed(sources))))
    assert len(first) == len(second) == 1
    assert first[0][0].id == second[0][0].id
    assert first[0][0].anchor_node_id == second[0][0].anchor_node_id
    assert set(first[0][0].source_member_ids) == {"osm:node:1", "osm:node:2"}
    assert registry.station_id("osm:node:2") == first[0][0].id


def test_same_name_different_networks_do_not_merge(tmp_path):
    sources = [feature(1, "中心站", 121.47, "上海地铁", None), feature(2, "中心站", 121.471, "国铁", None)]
    result = StationRegistry(tmp_path / "workspace.sqlite").resolve(group_station_sources(sources))
    assert len(result) == 2


def test_only_real_station_polygons_can_be_registered(tmp_path):
    registry = StationRegistry(tmp_path / "workspace.sqlite")
    polygon = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
    area = registry.register_area("osm:way:3", ["ST-1"], "platform", polygon)
    assert area.geometry == polygon and area.area_type == "platform"
    import pytest
    with pytest.raises(ValueError, match="real Polygon"):
        registry.register_area("invented", ["ST-1"], "station_outline", {"type": "Point", "coordinates": [0, 0]})
