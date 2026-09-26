"""National expressway grouping keeps source ways and province evidence separate."""

import pytest

from desktop.road_store import build_index, classify_motorway, national_group, routes, viewport


def test_g_and_s_refs_are_classified_without_guessing_missing_refs():
    provinces = ["安徽省", "江苏省"]
    assert classify_motorway({"ref": "G3; S12", "name": "测试高速"}, provinces, 1) == [
        ("G/G3", "national", "", "G3", "测试高速"),
        ("S/安徽省/S12", "provincial", "安徽省", "S12", "测试高速"),
        ("S/江苏省/S12", "provincial", "江苏省", "S12", "测试高速"),
    ]
    assert classify_motorway({"name": "未编号高速"}, ["安徽省"], 2)[0][1] == "unresolved"
    assert national_group("G2") == "国家干线"
    assert national_group("G2501") == "区域线路"
    assert national_group("G92") == "区域线路"
    assert national_group("G4W") == "区域线路"
    assert national_group("G102") == "国家高速 · 待核对"


def test_osm_motorways_build_disk_catalog_and_spatial_viewport(tmp_path, monkeypatch, qtbot):
    osmium = pytest.importorskip("osmium")
    from desktop import road_store
    from desktop.road_catalog_ui import RoadCatalog
    from desktop.tests.test_operating_ui import MapStub

    class Provinces:
        def along(self, _coordinates):
            return ["江苏省"]

    monkeypatch.setattr(road_store, "ProvinceIndex", Provinces)
    pbf = tmp_path / "roads.osm.pbf"
    with osmium.SimpleWriter(str(pbf)) as writer:
        for ident in range(1, 9):
            writer.add_node(osmium.osm.mutable.Node(id=ident, location=(120 + ident / 100, 31)))
        for ident, nodes, tags in (
            (10, [1, 2], {"highway": "motorway", "ref": "G2", "name": "京沪高速"}),
            (11, [3, 4], {"highway": "motorway", "ref": "S12", "name": "沪宁高速"}),
            (12, [5, 6], {"highway": "motorway", "name": "待核查高速"}),
            (13, [7, 8], {"highway": "trunk", "ref": "G30"}),
        ):
            writer.add_way(osmium.osm.mutable.Way(id=ident, nodes=nodes, tags=tags))
    database = tmp_path / "roads.sqlite"
    assert build_index(pbf, database) == 3
    records = routes(database)
    assert {record["key"] for record in records} == {
        "G/G2", "S/江苏省/S12", "U/江苏省/待核查高速"
    }
    all_roads = viewport(database, [119, 30, 122, 32])
    assert len(all_roads["features"]) == 3
    selected = viewport(database, [119, 30, 122, 32], "S/江苏省/S12")
    assert [feature["properties"]["osm_way_id"] for feature in selected["features"]] == [11]
    selected_many = viewport(database, [119, 30, 122, 32], route_keys=["G/G2", "S/江苏省/S12"])
    assert {feature["properties"]["osm_way_id"] for feature in selected_many["features"]} == {10, 11}
    widget = RoadCatalog(database, MapStub())
    qtbot.addWidget(widget)
    roots = {widget.model.data(widget.model.index(row, 0)).split(" · ")[0]: widget.model.index(row, 0)
             for row in range(widget.model.rowCount())}
    assert set(roots) == {"国家高速", "省级高速", "待核对高速"}
    province = roots["省级高速"]
    widget.model.fetchMore(province)
    province_group = widget.model.index(0, 0, province)
    assert widget.model.data(province_group).startswith("江苏省")
    widget.model.fetchMore(province_group)
    leaf = widget.model.index(0, 0, province_group)
    widget.focus_item(leaf)
    assert widget.map.calls[-1][0] == "fit"
    before = list(widget.map.calls)
    widget.select_item(leaf)
    assert widget.map.calls == before
