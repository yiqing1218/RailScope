import json
import sqlite3
from copy import deepcopy

from desktop.rail_catalog_ui import CatalogTree, RailCatalog
from desktop.tests.test_operating_ui import MapStub


def test_real_platform_lines_and_switches_are_browsable_without_map_selection(qtbot, tmp_path):
    from desktop.provinces import VERSION

    (tmp_path / "rail_catalog.json").write_text(
        json.dumps({"ST-YARD": {"name": "测试站", "station_name": "测试站", "provinces": ["省界外 / 待核对"], "way_ids": [8], "track_type": "站场股道", "classification": VERSION}}),
        encoding="utf-8",
    )
    station = {"type": "Feature", "properties": {"osm_node_id": 1, "kind": "station", "name": "测试站"}, "geometry": {"type": "Point", "coordinates": [120, 30]}}
    switches = [
        {"type": "Feature", "properties": {"osm_node_id": node, "kind": "switch", "name": str(node)}, "geometry": {"type": "Point", "coordinates": [120 + node / 100000, 30]}}
        for node in (2, 3)
    ]
    platform = {"type": "Feature", "properties": {"osm_way_id": 8, "name": "1站台"}, "geometry": {"type": "LineString", "coordinates": [[120, 30], [120.0001, 30]]}}
    with sqlite3.connect(tmp_path / "rail.sqlite") as db:
        db.executescript("CREATE TABLE features(id INTEGER PRIMARY KEY,kind TEXT,data TEXT);CREATE VIRTUAL TABLE bounds USING rtree(id,minx,maxx,miny,maxy);")
        for index, (kind, feature) in enumerate([("railPoints", station), *( ("railPoints", value) for value in switches), ("railPlatforms", platform)], 1):
            db.execute("INSERT INTO features VALUES(?,?,?)", (index, kind, json.dumps(feature, ensure_ascii=False)))
            x, y = feature["geometry"]["coordinates"][0] if kind == "railPlatforms" else feature["geometry"]["coordinates"]
            db.execute("INSERT INTO bounds VALUES(?,?,?,?,?)", (index, x, x, y, y))
    map_view = MapStub()
    widget = RailCatalog(tmp_path, tmp_path / "settings.json", map_view)
    qtbot.addWidget(widget)
    assert widget.yard_tree.topLevelItemCount() == 1
    item = widget.station_items["node/1"]
    assert any(item.child(i).text(0).startswith("真实站台线") for i in range(item.childCount()))
    assert all(widget.tabs.tabText(i) != "道岔目录" for i in range(widget.tabs.count()))
    current_tab = widget.tabs.currentWidget()
    widget.focus_switch_node(2)
    assert widget.tabs.currentWidget() is current_tab
    assert any(call[0] == "focus" and "道岔" in call[-1] for call in map_view.calls)
    widget.tabs.setCurrentWidget(widget.platform_page)
    assert widget.platform_tree.topLevelItemCount() == 1
    widget.focus_platform_item(widget.platform_tree.topLevelItem(0), 0)
    assert any(call[0] == "focus" for call in map_view.calls)
    platform_control = widget.platform_tree.itemWidget(widget.platform_tree.topLevelItem(0), 1)
    platform_control.click()
    assert widget.asset_visible["platform"] == {8}
    assert ("setRailAssetSelection", [8], [], [], []) in map_view.calls
    widget.set_station_master("station", True)
    partial = []
    widget.station_partial_changed.connect(lambda group,on: partial.append(on))
    widget.toggle_station_records({"node/1"}, False)
    assert partial == []  # Turning off a city must not turn off the master layer.
    assert widget.station_masters["station"]
    widget.toggle_assets("switch", {2}, False)
    assert widget.asset_hidden["switch"] == {2}


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


def test_manual_line_assembly_can_merge_across_folders_and_split(qtbot, tmp_path):
    widget, source = make_catalog(qtbot, tmp_path)
    original = source.read_bytes()
    widget.move_items({"track21"}, ["江苏省", "南京市", "仙林铁路"])
    assert widget.parents("track20") != widget.parents("track21")
    ident = widget.merge_line_segments({"track20", "track21"}, "仙林铁路")
    assert ident.startswith("RLU-")
    assert widget.items["track20"] is widget.items["track21"]
    assert widget.members[id(widget.items["track20"])] == {"track20", "track21"}
    widget.split_line_assembly({"track20"})
    assert widget.meta("track20").get("assembly_id") is None
    assert widget.meta("track21").get("assembly_id") is None
    assert widget.parents("track20") != widget.parents("track21")
    assert source.read_bytes() == original


def test_automatic_same_name_group_can_be_split_and_reloaded(qtbot,tmp_path):
    widget, source=make_catalog(qtbot,tmp_path)
    widget.save_overrides({key:{"display_name":"仙林铁路","folder_path":["江苏省","南京市"]} for key in widget.catalog})
    assert widget.items['track20'] is widget.items['track21']
    widget.split_line_assembly({'track20'})
    assert widget.items['track20'] is not widget.items['track21']
    widget.populate()
    assert widget.items['track20'] is not widget.items['track21']


def test_construction_master_keeps_individually_selected_operating_lines(qtbot,tmp_path):
    widget,_=make_catalog(qtbot,tmp_path)
    widget.save_overrides({'track21':{'construction':True}})
    widget.toggle('track20',True)
    widget.set_line_master('construction',True)
    assert widget.visible == {'track20','track21'}
    widget.set_line_master('construction',False)
    assert widget.visible == {'track20'}


def test_catalog_drop_finishes_after_qt_drop_event_returns(qtbot, monkeypatch):
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QTreeWidgetItem

    tree = CatalogTree()
    qtbot.addWidget(tree)
    source = QTreeWidgetItem(tree, ["源对象"])
    target = QTreeWidgetItem(tree, ["目标文件夹"])
    source.setSelected(True)
    calls = []
    tree.drop_callback = lambda selected, folder: calls.append((selected, folder))
    monkeypatch.setattr(tree, "itemAt", lambda position: target)

    class Event:
        accepted = False

        def position(self):
            return QPointF(1, 1)

        def acceptProposedAction(self):
            self.accepted = True

        def ignore(self):
            pass

    event = Event()
    tree.dropEvent(event)
    assert event.accepted and calls == []
    qtbot.waitUntil(lambda: bool(calls))
    assert calls == [([source], target)]


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
    initial_overrides = deepcopy(widget.overrides)
    widget.toggle("track20", True)
    previous_calls = list(widget.map.calls)
    monkeypatch.setattr(
        Path,
        "replace",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("保存失败")),
    )
    with pytest.raises(OSError):
        widget.archive_items({"track20"})
    assert widget.overrides == initial_overrides
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
    qtbot.waitUntil(
        lambda: widget.parents("track20") == ("上海市", "上海市", "虹桥站")
    )
    assert widget.parents("track20") == ("上海市", "上海市", "虹桥站")
    assert widget.items["track20"] is not original_item
    assert widget.tree.itemWidget(widget.items["track20"], 1) is not None


def test_large_directory_prioritizes_named_business_lines_and_includes_station_yards(
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
    assert "ST-YARD" in widget.items
    assert widget.yard_tree.topLevelItemCount() >= 1
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
    qtbot.waitUntil(lambda: bool(destinations))
    assert destinations == [({"node/100"}, ["安徽省", "合肥市"])]
    menu.actions()[0].trigger()
    assert requested == ["node/100"]


def test_single_station_toggle_does_not_enable_or_resync_national_station_tree(
    qtbot, tmp_path, monkeypatch
):
    records = [
        {
            "id": f"node/{index}",
            "name": f"测试站{index}",
            "kind": "station",
            "station_type": "客运站",
            "province": "山东省",
            "city": "泰安市",
            "coordinates": [117.1, 36.2],
            "osm_node_id": index,
            "line_ids": [],
            "line_names": [],
            "properties": {},
        }
        for index in range(1, 201)
    ]
    monkeypatch.setattr(
        "desktop.rail_catalog_ui.rail_station_records",
        lambda *args, **kwargs: ([dict(record) for record in records], len(records)),
    )
    map_view = MapStub()
    widget = RailCatalog(tmp_path, tmp_path / "settings.json", map_view)
    qtbot.addWidget(widget)
    partial = []
    legacy_master = []
    widget.station_partial_changed.connect(lambda group, on: partial.append((group, on)))
    widget.station_enabled_requested.connect(legacy_master.append)
    monkeypatch.setattr(
        widget,
        "sync_station_switches",
        lambda: (_ for _ in ()).throw(AssertionError("单站点不应同步全国开关")),
    )

    widget.toggle_station(widget.station_records[99], True)

    assert widget.station_masters["station"] is False
    assert widget.station_direct_visible == {"node/100"}
    assert partial == [("station", True)] and legacy_master == []
    assert map_view.calls[-3:] == [
        ("setRailPointExclusions", None),
        ("setRailPointSelection", [100]),
        ("setRailControlPointSelection", []),
    ]
    widget.station_query = "其他城市"
    widget.populate_station_tree()
    widget.send_station_visibility()
    assert ("setRailPointSelection", [100]) in map_view.calls[-3:]


def test_selecting_switch_inserts_its_owner_without_rebuilding_station_tree(
    qtbot, tmp_path, monkeypatch
):
    owner = {
        "id": "signalbox/RSB-TEST",
        "name": "测试线路所",
        "kind": "signal_box",
        "station_type": "线路所",
        "province": "安徽省",
        "city": "合肥市",
        "coordinates": [117.28, 31.80],
        "osm_node_id": None,
        "line_ids": ["RL-A"],
        "line_names": ["甲线"],
        "member_switch_ids": [101, 102],
        "properties": {},
    }
    monkeypatch.setattr(
        "desktop.rail_catalog_ui.rail_station_records",
        lambda *args, **kwargs: ([], 0),
    )
    monkeypatch.setattr(
        "desktop.rail_catalog_ui.rail_switch_owner",
        lambda *args, **kwargs: dict(owner),
    )
    widget = RailCatalog(tmp_path, tmp_path / "settings.json", MapStub())
    qtbot.addWidget(widget)
    monkeypatch.setattr(
        widget,
        "populate_station_tree",
        lambda: (_ for _ in ()).throw(AssertionError("选择道岔不应重建全国目录")),
    )

    assert widget.select_station(101)
    item = widget.station_items["signalbox/RSB-TEST"]
    assert item.text(0) == "测试线路所"
    assert item.childCount() == 2
    assert item.child(0).text(0).startswith("道岔 SW-")
    assert widget.selected_switch_owners[101] == "signalbox/RSB-TEST"
    widget.station_masters["station"] = True
    widget.station_excluded.add("signalbox/RSB-TEST")
    widget.send_station_visibility()
    assert ("setRailPointExclusions", [101, 102]) in widget.map.calls[-3:]


def test_switch_rename_uses_workspace_override_and_keeps_directory(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("desktop.rail_catalog_ui.rail_station_records", lambda *args, **kwargs: ([], 0))
    widget = RailCatalog(tmp_path, tmp_path / "settings.json", MapStub())
    qtbot.addWidget(widget)
    before = widget.tabs.currentWidget()
    widget.save_switch_name(101, "东咽喉 1 号岔")
    assert widget.tabs.currentWidget() is before
    assert widget.switch_name(101) == "东咽喉 1 号岔"
    assert json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))["switch:node/101"]["display_name"] == "东咽喉 1 号岔"


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
        {**connection, "distance_m": 52.1, "anchor_policy": "auto_reachable",
         "anchor_verification_status": "automatic_nearest_hint"}
    ]
    assert widget.station_records[0]["line_ids"] == ["RL-B"]
    assert "RL-B" in widget.station_items["node/100"].toolTip(0)


def test_shared_classification_is_read_only_and_local_undo_restores_it(qtbot, tmp_path):
    source = {
        "RL-TEST": {
            "name": "测试线", "line_name": "测试线", "track_type": "普速铁路线",
            "way_ids": [1], "edge_count": 1,
        }
    }
    (tmp_path / "rail_catalog.json").write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    shared = tmp_path / "shared.json"
    shared.write_text(json.dumps({"RL-TEST": {"folder_path": ["普速铁路", "华东", "客货运"]}}, ensure_ascii=False), encoding="utf-8")
    local = tmp_path / "local.json"
    widget = RailCatalog(tmp_path, local, MapStub(), shared_path=shared)
    qtbot.addWidget(widget)
    assert widget.parents("RL-TEST") == ("普速铁路", "华东", "客货运")
    widget.move_items({"RL-TEST"}, ["自定义", "目录"])
    assert widget.parents("RL-TEST") == ("自定义", "目录")
    assert json.loads(shared.read_text(encoding="utf-8"))["RL-TEST"]["folder_path"] == ["普速铁路", "华东", "客货运"]
    widget.undo_catalog()
    assert widget.parents("RL-TEST") == ("普速铁路", "华东", "客货运")
    widget.redo_catalog()
    assert widget.parents("RL-TEST") == ("自定义", "目录")


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
    selected = []
    widget.feature_activated.connect(selected.append)
    widget.show_station_details(original_item, 0)
    assert selected[-1]["properties"]["infrastructure_id"] == "node/100"
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
    assert widget.station_items["node/100"] is not original_item
    assert widget.station_tree.itemWidget(widget.station_items["node/100"], 1) is not None
    assert widget.station_items["node/100"].parent().text(0).startswith("自定义站点")
    widget.save_station_changes({"node/100"}, archived=True)
    assert widget.station_items["node/100"].parent().text(0).startswith("自定义站点")
    assert widget.station_items["node/100"].parent().parent().text(0).startswith("已归档")
    stored = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert stored["station:node/100"]["overview_attributes"]["foreign_name"].startswith("Yanzhoubei")
    assert stored["station:node/100"]["custom_attributes"]["年货运量"] == "611.9百万吨"
    from desktop.catalog_metadata import station_overview
    overview = station_overview({}, widget.station_record_by_id["node/100"], widget.overrides["station:node/100"])
    assert overview["region"] == "自定义站点"


def test_station_directory_is_the_only_display_region():
    from desktop.catalog_metadata import (
        normalize_station_attributes,
        station_directory_path,
        station_overview,
    )

    record = {"name": "麻套", "province": "山东省", "city": "济南"}
    custom = {
        "folder_path": ["山东省", "泰安", "麻套支线"],
        "overview_attributes": {"region": "山东省济南"},
    }
    assert station_directory_path(record, custom) == ("山东省", "泰安", "麻套支线")
    assert station_overview({}, record, custom)["region"] == "山东省泰安"
    assert "region" not in normalize_station_attributes({"region": "山东省济南"})


def test_map_station_detail_uses_the_same_workspace_directory(monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    from launcher import Desk

    class Field:
        def setText(self, value):
            self.value = value

        def setPlainText(self, value):
            self.value = value

        def setChecked(self, _value):
            pass

        def show(self):
            pass

    record = {
        "id": "node/101", "name": "麻套编辑名", "province": "山东省",
        "city": "济南", "station_type": "中间站", "line_ids": [], "line_names": [],
    }
    catalog = SimpleNamespace(
        catalog={}, station_record_by_id={"node/101": record},
        overrides={"station:node/101": {"folder_path": ["山东省", "泰安"]}},
    )
    rows = []
    inspector = SimpleNamespace(
        rail_catalog_widget=catalog, route_lookup={}, selected_title=Field(),
        selected_type=Field(), raw=Field(), right=Field(), detail_rail=Field(),
        set_property_rows=rows.extend, _restore_inspector_width=lambda: None,
    )
    Desk.display_feature(inspector, {
        "layer": "rail-points",
        "properties": {"osm_node_id": 101, "kind": "station", "name": "麻套"},
    })
    assert inspector.selected_title.value == "麻套编辑名"
    assert ("所属目录", "山东省 / 泰安") in rows
    assert not {"所属地区", "省级行政区", "城市"} & {label for label, _value in rows}
    assert inspector.selected_data["properties"]["station_overview"]["region"] == "山东省泰安"
