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

    assert editor_sizes(800, 1) == [360, 0, 440]
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
    assert map_view.calls[-1] == ("setRailWays", [2])
    catalog.close()
