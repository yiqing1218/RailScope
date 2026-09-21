import os
import json

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QScrollArea
from desktop.components import THEME, Switch, switch_row
from desktop.rail_catalog_ui import RailCatalog
from desktop.tests.test_operating_ui import MapStub


def test_sidebar_never_requires_horizontal_scrolling(tmp_path):
    app = QApplication.instance() or QApplication([])
    (tmp_path / "rail_catalog.json").write_text(
        json.dumps(
            {
                "很长的铁路工程名称" * 5: {
                    "way_ids": [1],
                    "province": "上海市",
                    "corridor": "未分配通道",
                    "section": "未分配分段",
                }
            }
        ),
        encoding="utf-8",
    )
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    body = QWidget()
    layout = QVBoxLayout(body)
    row = switch_row(
        "车站 / 线路所 / 道岔", Switch(), "保留每股道原始属性；几台几线未标注时不猜测。"
    )
    layout.addWidget(row)
    catalog = RailCatalog(tmp_path, tmp_path / "settings.json", MapStub())
    layout.addWidget(catalog)
    scroll.setWidget(body)
    scroll.setStyleSheet(THEME)
    scroll.resize(280, 650)
    scroll.show()
    app.processEvents()
    assert scroll.horizontalScrollBar().maximum() == 0
    assert not catalog.visible
    scroll.close()


def test_all_overlays_are_off_on_every_startup():
    from desktop.layer_state import initial_visibility

    assert initial_visibility() and not any(initial_visibility().values())


def test_three_pane_splitter_gives_national_editor_visible_space():
    from desktop.layer_state import editor_sizes

    assert editor_sizes(800, 1) == [260, 0, 540]
    assert editor_sizes(800, 0, expanded=True) == [200, 600, 0]
    assert editor_sizes(800, 1, expanded=True) == [200, 0, 600]


def test_midpoint_classifies_using_polygon_not_station_name():
    from desktop.provinces import ProvinceIndex, midpoint

    polygons = {
        "features": [
            {
                "properties": {"shapeName": "Shanghai"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]],
                },
            }
        ]
    }
    index = ProvinceIndex(polygons)
    assert index.locate(midpoint([[0.5, 1], [1.5, 1]])) == "上海市"
    assert index.locate([3, 1]) == "省界外 / 待核对"


def test_same_rail_name_can_have_different_province_sections():
    from desktop.provinces import ProvinceIndex, add_track

    index = ProvinceIndex()
    catalog = {}
    for ident, point in [(1, [121.45, 31.2]), (2, [118.8, 32.1])]:
        add_track(
            catalog,
            {
                "properties": {
                    "osm_way_id": ident,
                    "way_tags": {"name": "京沪高速铁路"},
                },
                "geometry": {"coordinates": [point, [point[0] + 0.01, point[1]]]},
            },
            index,
        )
    assert len(catalog) == 2
    assert {r["province"] for r in catalog.values()} == {"上海市", "江苏省"}


def test_cross_boundary_way_is_present_on_both_province_sides():
    from desktop.provinces import ProvinceIndex, add_track

    data = {
        "features": [
            {
                "properties": {"shapeName": "Shanghai"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[1, 0], [2, 0], [2, 2], [1, 2], [1, 0]]],
                },
            },
            {
                "properties": {"shapeName": "Jiangsu"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [1, 0], [1, 2], [0, 2], [0, 0]]],
                },
            },
        ]
    }
    catalog = {}
    add_track(
        catalog,
        {
            "properties": {
                "osm_way_id": 99,
                "way_tags": {"name": "京沪高铁", "highspeed": "yes"},
            },
            "geometry": {"coordinates": [[0.8, 1], [1.2, 1]]},
        },
        ProvinceIndex(data),
    )
    assert {record["province"] for record in catalog.values()} == {
        "江苏省",
        "上海市",
    }
    assert all(record["way_ids"] == [99] for record in catalog.values())


def test_province_holes_and_half_length_not_coordinate_average():
    from desktop.provinces import ProvinceIndex, midpoint

    data = {
        "features": [
            {
                "properties": {"shapeName": "Shanghai"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]],
                        [[1, 1], [3, 1], [3, 3], [1, 3], [1, 1]],
                    ],
                },
            }
        ]
    }
    index = ProvinceIndex(data)
    assert index.locate([2, 2]) == "省界外 / 待核对"
    assert index.locate([0.5, 2]) == "上海市"
    assert abs(midpoint([[0, 0], [0.1, 0], [2, 0]])[0] - 1) < 0.00001


def test_province_and_corridor_switches_stay_in_sync(tmp_path):
    app = QApplication.instance() or QApplication([])
    (tmp_path / "rail_catalog.json").write_text(
        json.dumps(
            {
                n: {
                    "way_ids": [i],
                    "province": "上海市",
                    "corridor": "通道",
                    "section": "分段",
                }
                for i, n in enumerate(("铁路A", "铁路B"), 1)
            }
        ),
        encoding="utf-8",
    )
    map_view = MapStub()
    catalog = RailCatalog(tmp_path, tmp_path / "settings.json", map_view)
    catalog.mode.setCurrentIndex(1)
    group = catalog.tree.topLevelItem(0)
    group.setExpanded(True)
    catalog.tree.itemWidget(group, 1).click()
    app.processEvents()
    assert catalog.visible == {"铁路A", "铁路B"}
    assert catalog.tree.topLevelItem(0).isExpanded()
    catalog.toggle("铁路A", False)
    section = catalog.tree.topLevelItem(0).child(0)
    assert catalog.tree.itemWidget(section, 1).isChecked()
    assert catalog.tree.itemWidget(section, 1)._mixed
    assert ("setRailSelection", [], [2]) in map_view.calls
    catalog.close()


def test_topology_catalog_groups_by_line_or_station_and_links_both_endpoints(tmp_path):
    import sqlite3
    from desktop.provinces import geographic_catalog
    from desktop.rail_categories import catalog_parents

    def edge(ident, source_way, a, b, line_id, line_name, tags=None):
        return {
            "id": ident,
            "osm_way_id": source_way,
            "from_node": source_way * 10,
            "to_node": source_way * 10 + 1,
            "from_node_id": a,
            "to_node_id": b,
            "node_ids": [source_way * 10, source_way * 10 + 1],
            "coordinates": [[120 + source_way / 1000, 30], [120 + source_way / 1000 + 0.005, 30]],
            "line_id": line_id,
            "line_name": line_name,
            "way_tags": {"name": line_name, **(tags or {})},
            "construction": False,
        }

    edges = [
        edge("NE-A", 1, "NN-A", "NN-J", "IL-MAIN", "京沪高铁", {"highspeed": "yes"}),
        edge("NE-B", 2, "NN-J", "NN-B", "IL-MAIN", "京沪高铁", {"highspeed": "yes"}),
        edge("NE-C", 3, "NN-J", "NN-C", "IL-BRANCH", "测试联络线", {"service": "spur"}),
        edge("NE-Y", 4, "NN-J", "NN-Y", "IL-YARD", "测试站场", {"service": "yard", "highspeed": "yes"}),
        edge("NE-S1", 5, "NN-S1", "NN-S2", "IL-STRAIGHT", "连续测试线", {"highspeed": "yes"}),
        edge("NE-S2", 6, "NN-S2", "NN-S3", "IL-STRAIGHT", "连续测试线", {"highspeed": "yes"}),
    ]
    station = {
        "type": "Feature",
        "properties": {
            "osm_node_id": 11,
            "infrastructure_node_id": "NN-J",
            "name": "测试站",
            "kind": "station",
        },
        "geometry": {"type": "Point", "coordinates": edges[0]["coordinates"][-1]},
    }
    with sqlite3.connect(tmp_path / "rail.sqlite") as db:
        db.executescript("CREATE TABLE edges(id TEXT PRIMARY KEY,data TEXT); CREATE TABLE features(kind TEXT,data TEXT);")
        db.executemany("INSERT INTO edges VALUES(?,?)", ((e["id"], json.dumps(e, ensure_ascii=False)) for e in edges))
        db.execute("INSERT INTO features VALUES(?,?)", ("railPoints", json.dumps(station, ensure_ascii=False)))
    catalog = geographic_catalog(tmp_path)
    assert catalog and all("province" not in item and "corridor" not in item for item in catalog.values())
    straight = [item for item in catalog.values() if item["line_id"] == "IL-STRAIGHT"]
    assert len(straight) == 1
    assert straight[0]["edge_count"] == 2 and straight[0]["section_count"] == 1
    yard = next(item for item in catalog.values() if item["station_name"] == "测试站")
    assert yard["station_name"] == "测试站"
    assert catalog_parents(yard, 0) == ("其他铁路", "不确定铁路")
    main = next(item for item in catalog.values() if item["line_id"] == "IL-MAIN")
    assert catalog_parents(main, 0) == ("高速铁路", "国家高速铁路主干线")
    view = MapStub()
    widget = RailCatalog(tmp_path, tmp_path / "settings.json", view)
    widget.toggle(main["id"], True)
    assert ("setRailSelection", [], [], [main["id"]]) in view.calls
    widget.close()


def test_topology_catalog_upgrades_old_way_rendering_without_reimport(tmp_path):
    import sqlite3
    from desktop.provinces import geographic_catalog

    edges = [
        {
            "id": ident,
            "osm_way_id": 90,
            "from_node": a,
            "to_node": b,
            "node_ids": [a, b],
            "coordinates": [[120 + a / 100, 30], [120 + b / 100, 30]],
            "line_id": "IL-UPGRADE",
            "line_name": "升级测试线",
            "way_tags": {"name": "升级测试线"},
            "construction": False,
        }
        for ident, a, b in (("NE-A", 1, 2), ("NE-B", 2, 3))
    ]
    old_feature = {
        "type": "Feature",
        "properties": {"osm_way_id": 90, "way_tags": {"name": "升级测试线"}},
        "geometry": {"type": "LineString", "coordinates": [[120.01, 30], [120.03, 30]]},
    }
    with sqlite3.connect(tmp_path / "rail.sqlite") as db:
        db.executescript(
            "CREATE TABLE features(id INTEGER PRIMARY KEY,kind TEXT,service TEXT,data TEXT);"
            "CREATE VIRTUAL TABLE bounds USING rtree(id,minx,maxx,miny,maxy);"
            "CREATE TABLE edges(id TEXT PRIMARY KEY,data TEXT);"
        )
        db.execute(
            "INSERT INTO features VALUES(1,'rail','main',?)",
            (json.dumps(old_feature, ensure_ascii=False),),
        )
        db.execute("INSERT INTO bounds VALUES(1,120.01,120.03,30,30)")
        db.executemany(
            "INSERT INTO edges VALUES(?,?)",
            ((edge["id"], json.dumps(edge, ensure_ascii=False)) for edge in edges),
        )
    catalog = geographic_catalog(tmp_path)
    assert len(catalog) == 1
    with sqlite3.connect(tmp_path / "rail.sqlite") as db:
        rendered = [
            json.loads(row[0])
            for row in db.execute("SELECT data FROM features WHERE kind='rail'")
        ]
    assert len(rendered) == 2
    assert {item["properties"]["network_edge_id"] for item in rendered} == {
        "NE-A",
        "NE-B",
    }
    assert all(
        item["properties"].get("section_id")
        and item["properties"].get("catalog_group_id") == "IL-UPGRADE"
        and item["properties"].get("line_id") == "IL-UPGRADE"
        for item in rendered
    )


def test_large_topology_tree_is_bounded_and_still_searches_every_section(
    tmp_path, monkeypatch
):
    import desktop.rail_catalog_ui as catalog_module

    monkeypatch.setattr(catalog_module, "MAX_CATALOG_TREE_ITEMS", 2)
    source = {
        f"RS-{index}": {
            "id": f"RS-{index}",
            "name": f"第 {index} 线段",
            "line_name": "测试线",
            "line_display_name": "测试线 · IL-TEST",
            "track_type": "普通铁路线",
            "from_node": f"NN-{index}",
            "from_name": f"端点 {index}",
            "to_node": f"NN-{index + 1}",
            "to_name": f"端点 {index + 1}",
            "edge_ids": [f"NE-{index}"],
            "way_ids": [index],
        }
        for index in range(3)
    }
    (tmp_path / "rail_catalog.json").write_text(
        json.dumps(source, ensure_ascii=False), encoding="utf-8"
    )
    view = MapStub()
    widget = RailCatalog(tmp_path, tmp_path / "settings.json", view)
    assert len(widget.items) == 2
    assert all(
        not widget.tree.itemWidget(group, 1).isEnabled()
        for group in widget.groups.values()
    )
    widget.search.setText("RS-2")
    widget.search_timer.stop()
    widget.populate()
    assert set(widget.items) == {"RS-2"}
    widget.set_all(True)
    assert ("setRailSelection", None, None) in view.calls
    widget.toggle("RS-2", False)
    assert ("setRailExclusions", ["RS-2"], []) in view.calls
    widget.close()
