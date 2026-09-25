import json
import sqlite3

from PySide6.QtWidgets import QApplication

from desktop.rail_catalog_ui import RailCatalog
from desktop.tests.test_operating_ui import MapStub


def test_rail_directory_double_click_fits_full_line_without_toggling(tmp_path):
    app = QApplication.instance() or QApplication([])
    assert app
    catalog = {
        "line": {
            "name": "测试铁路",
            "province": "上海市",
            "corridor": "不适用",
            "section": "测试铁路",
            "way_ids": [11, 12],
        }
    }
    (tmp_path / "rail_catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    view = MapStub()
    widget = RailCatalog(tmp_path, tmp_path / "settings.json", view)
    # Create after construction to avoid triggering the province upgrade thread.
    with sqlite3.connect(tmp_path / "rail.sqlite") as db:
        db.executescript(
            "CREATE TABLE features(id INTEGER PRIMARY KEY,kind TEXT,data TEXT);"
            "CREATE VIRTUAL TABLE bounds USING rtree(id,minx,maxx,miny,maxy);"
        )
        for ident, way, bounds in [
            (1, 11, (120, 121, 30, 31)),
            (2, 12, (121, 123, 31, 33)),
        ]:
            db.execute(
                "INSERT INTO features VALUES(?,?,?)",
                (ident, "rail", json.dumps({"properties": {"osm_way_id": way}})),
            )
            db.execute("INSERT INTO bounds VALUES(?,?,?,?,?)", (ident, *bounds))
    opened = []
    widget.feature_activated.connect(opened.append)
    widget.tree.itemDoubleClicked.emit(widget.items["line"], 0)
    assert view.calls[-1] == (
        "fit",
        [[120, 30], [123, 33]],
        widget.items["line"].text(0),
    )
    assert widget.visible == set(), "定位不能更改显示开关"
    assert opened[-1]["layer"] == "rail" and opened[-1]["properties"]["display_name"] == "测试铁路"
    before = len(view.calls)
    widget.tree.itemDoubleClicked.emit(widget.items["line"], 1)
    assert len(view.calls) == before, "双击开关列不能触发定位"
    widget.close()


def test_rail_directory_without_geometry_does_not_move_map(tmp_path):
    app = QApplication.instance() or QApplication([])
    assert app
    view = MapStub()
    widget = RailCatalog(tmp_path, tmp_path / "settings.json", view)
    widget.catalog = {
        "missing": {
            "name": "无几何线路",
            "province": "上海市",
            "corridor": "不适用",
            "section": "测试",
            "way_ids": [123],
        }
    }
    widget.populate()
    widget.tree.itemDoubleClicked.emit(widget.items["missing"], 0)
    assert view.calls == []
    assert "无法定位" in widget.note.text()
    widget.close()


def test_same_named_rail_fragments_share_one_directory_leaf(tmp_path):
    app = QApplication.instance() or QApplication([])
    assert app
    catalog = {
        key: {
            "name": "渝厦高速线",
            "folder_path": ["高速铁路", "区域高速铁路"],
            "track_type": "高速铁路线",
            "way_ids": [way],
            "edge_count": 2,
        }
        for key, way in (("part-a", 11), ("part-b", 12))
    }
    (tmp_path / "rail_catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False), encoding="utf-8"
    )
    widget = RailCatalog(tmp_path, tmp_path / "settings.json", MapStub())
    assert widget.items["part-a"] is widget.items["part-b"]
    leaf = widget.items["part-a"]
    assert widget.members[id(leaf)] == {"part-a", "part-b"}
    assert "已合并 2 个同名线路片段" in leaf.toolTip(0)
    widget.close()


def test_map_selected_way_is_revealed_in_rail_directory(tmp_path):
    app = QApplication.instance() or QApplication([])
    assert app
    catalog = {
        "line": {
            "name": "京沪高铁",
            "province": "上海市",
            "corridor": "纵向 · 京沪通道",
            "section": "京沪高铁",
            "track_type": "高速铁路线",
            "way_ids": [99],
        }
    }
    (tmp_path / "rail_catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    widget = RailCatalog(tmp_path, tmp_path / "settings.json", MapStub())
    widget.search.setText("不会匹配")
    assert widget.select_way(99)
    assert widget.search.text() == "不会匹配"
    assert widget.tree.currentItem() is widget.items["line"]
    assert widget.items["line"].parent().isExpanded()
    widget.close()


def test_map_selected_endpoint_section_wins_over_shared_osm_way(tmp_path):
    app = QApplication.instance() or QApplication([])
    assert app
    catalog = {
        ident: {
            "id": ident,
            "name": ident,
            "line_name": "测试线",
            "track_type": "普通铁路线",
            "from_node": "NN-A",
            "from_name": "甲",
            "to_node": "NN-B",
            "to_name": "乙",
            "way_ids": [99],
        }
        for ident in ("RS-FIRST", "RS-SECOND")
    }
    (tmp_path / "rail_catalog.json").write_text(
        json.dumps(catalog), encoding="utf-8"
    )
    widget = RailCatalog(tmp_path, tmp_path / "settings.json", MapStub())
    assert widget.select_way(99, section_id="RS-SECOND")
    assert widget.tree.currentItem() is widget.items["RS-SECOND"]
    widget.close()


def test_map_selection_keeps_existing_directory_rows_when_result_is_capped(tmp_path, monkeypatch):
    from desktop import rail_catalog_ui

    app = QApplication.instance() or QApplication([])
    assert app
    monkeypatch.setattr(rail_catalog_ui, "MAX_CATALOG_TREE_ITEMS", 2)
    catalog = {
        key: {"id": key, "name": label, "province": "河南省", "track_type": "普通铁路线", "way_ids": [way]}
        for key, label, way in [("RL-A", "甲线", 1), ("RL-B", "乙线", 2), ("RL-C", "丙线", 3)]
    }
    (tmp_path / "rail_catalog.json").write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    widget = RailCatalog(tmp_path, tmp_path / "settings.json", MapStub())
    before = set(widget.items)
    assert len(before) == 2
    missing = (set(catalog) - before).pop()
    assert widget.select_way(catalog[missing]["way_ids"][0])
    assert set(widget.items) == before | {missing}
    assert widget.search.text() == ""
    widget.close()
