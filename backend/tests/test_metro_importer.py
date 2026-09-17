from railscope.services.importers.metro import FALLBACK_COLOR, color_metadata, is_metro_station_area_tags, route_record
from railscope.services.importers.construction import is_construction_metro_tags


def test_metro_color_uses_osm_colour_verbatim_and_preserves_all_tags():
    tags = {"type": "route", "route": "subway", "name": "测试线", "colour": "#E4002B", "operator": "示例运营方"}
    record = route_record(123, tags, [{"sequence": 0, "type": "w", "ref": 99, "role": ""}])
    assert record["display_color"] == "#E4002B"
    assert record["color_raw"] == "#E4002B"
    assert record["color_source"] == "osm:colour"
    assert record["relation_tags"] == tags


def test_metro_color_falls_back_only_when_osm_has_no_renderable_value():
    assert color_metadata({"color": "orange"}) == ("orange", "orange", "osm:color")
    display, raw, source = color_metadata({"colour": "not-a-map-colour", "name": "保留原值"})
    assert (display, raw, source) == (FALLBACK_COLOR, "not-a-map-colour", "unrenderable:osm:colour")
    assert color_metadata({}) == (FALLBACK_COLOR, None, "missing")


def test_construction_metro_filter_is_explicit_and_does_not_include_generic_railwork():
    assert is_construction_metro_tags({"railway": "construction", "construction": "subway"})
    assert is_construction_metro_tags({"railway": "light_rail", "construction": "yes"})
    assert not is_construction_metro_tags({"railway": "construction", "construction": "rail"})
    assert not is_construction_metro_tags({"railway": "rail", "construction": "yes"})


def test_metro_station_areas_require_explicit_station_and_subway_tags():
    assert is_metro_station_area_tags({"railway": "station", "station": "subway"})
    assert is_metro_station_area_tags({"public_transport": "station", "subway": "yes"})
    assert not is_metro_station_area_tags({"building": "train_station", "name": "未标注线路"})
    assert not is_metro_station_area_tags({"railway": "station", "station": "rail"})
