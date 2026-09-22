import json
from copy import deepcopy

from desktop.rail_catalog_ui import RailCatalog
from desktop.tests.test_operating_ui import MapStub


def make_catalog(qtbot, tmp_path):
    source = {
        key: {
            "name": name,
            "province": "上海市",
            "corridor": "不适用",
            "section": "虹桥站",
            "way_ids": [way],
            "track_type": "站场股道（类型待核对）",
        }
        for key, name, way in [
            ("track20", "上海虹桥站20道", 20),
            ("track21", "上海虹桥站21道", 21),
        ]
    }
    path = tmp_path / "rail_catalog.json"
    path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    widget = RailCatalog(tmp_path, tmp_path / "settings.json", MapStub())
    qtbot.addWidget(widget)
    return widget, path


def test_context_rename_move_archive_restore_persist_without_source_edits(
    qtbot, tmp_path, monkeypatch
):
    from PySide6.QtWidgets import QInputDialog

    widget, path = make_catalog(qtbot, tmp_path)
    original_file, original_catalog = path.read_bytes(), deepcopy(widget.catalog)
    original_id = "track20"
    menu = widget.item_menu(widget.items["track20"])
    assert [action.text() for action in menu.actions() if not action.isSeparator()] == [
        "重命名…",
        "移动到",
        "查看端点与相邻线段…",
        "归档",
    ]
    monkeypatch.setattr(
        QInputDialog, "getText", lambda *args, **kwargs: ("虹桥20道（自用）", True)
    )
    menu.actions()[0].trigger()
    assert widget.items["track20"].text(0).startswith("虹桥20道（自用）")
    assert widget.items["track20"].data(0, 0x0100) == original_id
    widget.move_items({"track20"}, ["我的目录", "虹桥站", "站台"])
    assert widget.parents("track20") == ("我的目录", "虹桥站", "站台")
    widget.toggle("track20", True)
    menu = widget.item_menu(widget.items["track20"])
    menu.actions()[-1].trigger()
    assert "track20" not in widget.visible
    assert widget.parents("track20")[0] == "已归档"
    assert not widget.tree.itemWidget(widget.items["track20"], 1).isEnabled()
    widget.set_all(True)
    assert widget.visible == {"track21"}
    widget.toggle("track20", True)
    assert widget.visible == {"track21"}
    restored = RailCatalog(tmp_path, tmp_path / "settings.json", MapStub())
    qtbot.addWidget(restored)
    assert restored.parents("track20") == ("已归档", "我的目录", "虹桥站", "站台")
    assert restored.display_name("track20").startswith("虹桥20道（自用）")
    menu = restored.item_menu(restored.items["track20"])
    assert menu.actions()[-1].text() == "取消归档 / 恢复"
    menu.actions()[-1].trigger()
    assert restored.parents("track20") == ("我的目录", "虹桥站", "站台")
    assert restored.visible == set()
    assert widget.catalog == original_catalog
    assert path.read_bytes() == original_file


def test_folder_rename_preserves_descendants_and_archive_is_reversible(qtbot, tmp_path):
    widget, path = make_catalog(qtbot, tmp_path)
    original = path.read_bytes()
    widget.move_items({"track20"}, ["站场", "到发线"])
    widget.move_items({"track21"}, ["站场", "调车线"])
    widget.rename_folder(("站场",), "虹桥站场")
    assert widget.parents("track20") == ("虹桥站场", "到发线")
    assert widget.parents("track21") == ("虹桥站场", "调车线")
    group = widget.groups[("虹桥站场",)]
    widget.item_menu(group).actions()[-1].trigger()
    assert all(widget.parents(key)[0] == "已归档" for key in widget.catalog)
    widget.item_menu(widget.groups[("已归档",)]).actions()[-1].trigger()
    assert widget.parents("track20") == ("虹桥站场", "到发线")
    widget.mode.setCurrentIndex(1)
    assert widget.parents("track20") == ("虹桥站场", "到发线")
    assert path.read_bytes() == original


def test_failed_directory_save_leaves_state_and_map_unchanged(
    qtbot, tmp_path, monkeypatch
):
    import pytest
    from pathlib import Path

    widget, _ = make_catalog(qtbot, tmp_path)
    widget.toggle("track20", True)
    previous_calls = list(widget.map.calls)
    monkeypatch.setattr(
        Path,
        "replace",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("保存失败")),
    )
    with pytest.raises(OSError):
        widget.archive_items({"track20"})
    assert widget.overrides == {}
    assert widget.visible == {"track20"}
    assert widget.map.calls == previous_calls


def test_move_context_action_uses_existing_folder_cascade_without_rebuild(
    qtbot, tmp_path, monkeypatch
):
    widget, _ = make_catalog(qtbot, tmp_path)
    widget.move_items({"track21"}, ["上海市", "上海市", "虹桥站"])
    original_item = widget.items["track20"]
    monkeypatch.setattr(
        widget,
        "populate",
        lambda: (_ for _ in ()).throw(AssertionError("移动不应重建整棵线路树")),
    )
    move = widget.item_menu(original_item).actions()[1].menu()
    province = next(action.menu() for action in move.actions() if action.text() == "上海市")
    city = next(action.menu() for action in province.actions() if action.text() == "上海市")
    next(action for action in city.actions() if action.text() == "虹桥站").trigger()
    assert widget.parents("track20") == ("上海市", "上海市", "虹桥站")
    assert widget.items["track20"] is original_item


def test_large_directory_prioritizes_named_business_lines_and_omits_station_groups(
    qtbot, tmp_path
):
    source = {
        **{
            f"RL-U-{index}": {
                "name": f"未命名轨道·w{index}",
                "line_id": f"RL-U-{index}",
                "way_ids": [index],
                "track_type": "未确认类型",
            }
            for index in range(4100)
        },
        "RL-NAMED": {
            "name": "京沪高速铁路",
            "line_id": "RL-NAMED",
            "way_ids": [5000],
            "track_type": "高速铁路线",
        },
        "ST-YARD": {
            "name": "上海虹桥站",
            "line_id": None,
            "way_ids": [5001],
            "track_type": "高速铁路站场股道",
        },
    }
    (tmp_path / "rail_catalog.json").write_text(
        json.dumps(source, ensure_ascii=False), encoding="utf-8"
    )
    widget = RailCatalog(tmp_path, tmp_path / "settings.json", MapStub())
    qtbot.addWidget(widget)
    widget.resize(380, 900)
    widget.show()
    qtbot.wait(1)
    assert "RL-NAMED" in widget.items
    assert "ST-YARD" not in widget.items
    assert not any(key.startswith("RL-U-") for key in widget.items)
    assert widget.tree.topLevelItemCount() > 0
    assert widget.tree.verticalScrollBar().value() == 0
    assert widget.tree.y() < 80


def test_station_context_menu_requests_the_shared_metadata_editor(
    qtbot, tmp_path, monkeypatch
):
    record = {
        "id": "node/100",
        "name": "测试站",
        "kind": "station",
        "station_type": "客运站",
        "province": "安徽省",
        "city": "合肥市",
        "coordinates": [117.28, 31.80],
        "osm_node_id": 100,
        "line_ids": ["RL-A"],
        "line_names": ["甲线"],
        "properties": {},
    }
    monkeypatch.setattr(
        "desktop.rail_catalog_ui.rail_station_records",
        lambda *args, **kwargs: ([dict(record)], 1),
    )
    widget = RailCatalog(tmp_path, tmp_path / "settings.json", MapStub())
    qtbot.addWidget(widget)
    requested = []
    widget.station_edit_requested.connect(requested.append)
    menu = widget.station_item_menu(widget.station_items["node/100"])
    assert [action.text() for action in menu.actions()] == [
        "编辑名称、目录、类型和接轨线路…",
        "移动到",
        "在地图中定位",
        "归档",
    ]
    destinations = []
    widget.save_station_changes = (
        lambda ids, **changes: destinations.append((set(ids), changes["folder_path"]))
    )
    move = menu.actions()[1].menu()
    province = next(action.menu() for action in move.actions() if action.text() == "安徽省")
    next(action for action in province.actions() if action.text() == "合肥市").trigger()
    assert destinations == [({"node/100"}, ["安徽省", "合肥市"])]
    menu.actions()[0].trigger()
    assert requested == ["node/100"]


def test_station_connection_override_persists_and_updates_station_directory(
    qtbot, tmp_path, monkeypatch
):
    record = {
        "id": "node/100",
        "name": "测试站",
        "kind": "station",
        "station_type": "客运站",
        "province": "安徽省",
        "city": "合肥市",
        "coordinates": [117.28, 31.80],
        "osm_node_id": 100,
        "line_ids": ["RL-A"],
        "line_names": ["甲线"],
        "properties": {},
    }
    monkeypatch.setattr(
        "desktop.rail_catalog_ui.rail_station_records",
        lambda *args, **kwargs: ([dict(record)], 1),
    )
    settings = tmp_path / "settings.json"
    widget = RailCatalog(tmp_path, settings, MapStub())
    qtbot.addWidget(widget)
    connection = {
        "line_id": "RL-B",
        "anchor_node": 300,
        "distance_m": 52.14,
        "source": "manual",
        "verification_status": "user_verified",
    }
    widget.save_station_override("node/100", connected_lines=[connection])
    stored = json.loads(settings.read_text(encoding="utf-8"))
    assert stored["station:node/100"]["connected_lines"] == [
        {**connection, "distance_m": 52.1}
    ]
    assert widget.station_records[0]["line_ids"] == ["RL-B"]
    assert "RL-B" in widget.station_items["node/100"].toolTip(0)


def test_station_overview_archive_and_arbitrary_folder_are_workspace_overrides(
    qtbot, tmp_path, monkeypatch
):
    record = {
        "id": "node/100",
        "name": "兖州北站",
        "kind": "station",
        "station_type": "货运站",
        "province": "山东省",
        "city": "济宁市",
        "coordinates": [116.8, 35.5],
        "osm_node_id": 100,
        "line_ids": ["RL-A"],
        "line_names": ["京沪铁路"],
        "properties": {},
    }
    monkeypatch.setattr(
        "desktop.rail_catalog_ui.rail_station_records",
        lambda *args, **kwargs: ([dict(record)], 1),
    )
    widget = RailCatalog(tmp_path, tmp_path / "settings.json", MapStub())
    qtbot.addWidget(widget)
    original_item = widget.station_items["node/100"]
    monkeypatch.setattr(
        widget,
        "populate_station_tree",
        lambda: (_ for _ in ()).throw(AssertionError("编辑站点不应重建全国站点树")),
    )
    widget.save_station_override(
        "node/100",
        folder_path=["自定义站点"],
        overview_attributes={"foreign_name": "Yanzhoubei Railway Station"},
        custom_attributes={"年货运量": "611.9百万吨"},
    )
    assert widget.station_items["node/100"] is original_item
    assert widget.station_items["node/100"].parent().text(0).startswith("自定义站点")
    widget.save_station_changes({"node/100"}, archived=True)
    assert widget.station_items["node/100"].parent().text(0).startswith("自定义站点")
    assert widget.station_items["node/100"].parent().parent().text(0).startswith("已归档")
    stored = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert stored["station:node/100"]["overview_attributes"]["foreign_name"].startswith("Yanzhoubei")
    assert stored["station:node/100"]["custom_attributes"]["年货运量"] == "611.9百万吨"
