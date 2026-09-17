from pathlib import Path
import json


def test_portable_g1_has_real_continuous_path_and_seven_stops():
    from desktop.rail import compile_rail_plan

    asset = Path(__file__).parents[1] / "examples/g1-reference.json"
    data = json.loads(asset.read_text(encoding="utf-8"))
    plan, lines = compile_rail_plan(data["plan"], data["edges"], data["points"])
    assert len(plan.trains) == 1 and plan.trains[0]["id"] == "G1"
    assert [s["name"] for s in lines[0]["stations"]] == [
        "北京南",
        "沧州西",
        "德州东",
        "曲阜东",
        "南京南",
        "苏州北",
        "上海虹桥",
    ]
    assert plan.trains[0]["stops"][0]["departure_s"] == 23400
    assert plan.trains[0]["stops"][-1]["arrival_s"] == 41040
    assert 1250000 < lines[0]["path"]["length_m"] < 1400000
    assert plan.position("G1", 24000)["state"] == "区间运行"
    legs = data["plan"]["trains"][0]["path"]
    assert len(legs) == len({leg["edge_id"] for leg in legs}), (
        "参考干线路径不能为了追随站点 POI 在支线来回折返"
    )


def test_g1_works_without_national_download_and_starts_paused(tmp_path):
    from PySide6.QtWidgets import QApplication
    from desktop.tests.test_operating_ui import MapStub
    from desktop.rail_ui import RailEditor

    app = QApplication.instance() or QApplication([])
    assert app
    editor = RailEditor(MapStub(), tmp_path, tmp_path / "user-plan.json")
    sidebar = editor.sidebar()
    editor.load_g1_example()
    assert editor.plan.trains[0]["id"] == "G1"
    assert editor.table.rowCount() == 7
    assert not editor.enabled and not editor.playing
    assert editor.clock == 23400
    editor.write(tmp_path / "g1.json")
    editor.apply_payload(json.loads((tmp_path / "g1.json").read_text(encoding="utf-8")))
    editor.play()
    assert editor.current_vehicle_features
    editor.pause()
    editor.timer.stop()
    editor.close()
    sidebar.close()


def test_rail_and_metro_have_separate_models_clocks_and_vehicle_sources(tmp_path):
    from PySide6.QtWidgets import QApplication
    from desktop.tests.test_operating_ui import MapStub
    from desktop.rail_ui import RailEditor
    from desktop.operating_ui import OperationsEditor
    from desktop.operating import Plan

    app = QApplication.instance() or QApplication([])
    assert app
    shared_map = MapStub()
    metro = OperationsEditor(Plan([]), shared_map, [], tmp_path / "metro.json")
    rail = RailEditor(shared_map, tmp_path, tmp_path / "rail.json")
    assert not rail.enabled and rail.plan.trains[0]["id"] == "G1"
    rail.play()
    assert metro.clock == 25200 and not metro.enabled
    assert not metro.plan.trains and rail.plan.trains[0]["id"] == "G1"
    assert shared_map.calls[-1][0] == "setRailOperatingVehicles"
    rail.commit_stop("G1", 1, 26280, 26410)
    assert not metro.plan.trains
    rail.pause()
    for editor in (rail, metro):
        editor.timer.stop()
        editor.close()


def test_rail_workspace_is_compact_and_has_direct_g1_entry(tmp_path):
    from PySide6.QtWidgets import QApplication, QPushButton
    from desktop.tests.test_operating_ui import MapStub
    from desktop.rail_ui import RailEditor
    from desktop.components import THEME

    app = QApplication.instance() or QApplication([])
    editor = RailEditor(MapStub(), tmp_path, tmp_path / "rail.json")
    editor.setStyleSheet(THEME)
    editor.resize(1600, 680)
    editor.show()
    app.processEvents()
    assert editor.width() <= 1120
    assert editor.tabs.height() >= editor.height() * 0.60
    assert "国铁" in editor.workspace_title.text()
    assert not any("大小交路" in b.text() for b in editor.findChildren(QPushButton))
    assert not hasattr(editor, "g1_button")
    editor.load_g1_example()
    app.processEvents()
    assert editor.tabs.currentIndex() == 0 and editor.table.rowCount() == 7
    assert editor.table.item(0, 4).text() == "06:30:00"
    assert editor.table.viewport().height() > 160
    editor.timer.stop()
    editor.close()


def test_saved_rail_plan_can_show_reference_route_after_restart(tmp_path):
    from PySide6.QtWidgets import QApplication
    from desktop.tests.test_operating_ui import MapStub
    from desktop.rail_ui import RailEditor

    app = QApplication.instance() or QApplication([])
    assert app
    source = RailEditor(MapStub(), tmp_path, tmp_path / "rail.json")
    source.load_g1_example()
    source.save()
    restarted_map = MapStub()
    restored = RailEditor(restarted_map, tmp_path, tmp_path / "rail.json")
    sidebar = restored.sidebar()
    assert restored.table.rowCount() == 7 and not restored.enabled
    assert not restored.route_switch.isChecked()
    restored.route_switch.click()
    assert any(
        c[0] == "setRailPlan" and len(c[1]["features"]) == 8
        for c in restarted_map.calls
    )
    for editor in (source, restored):
        editor.timer.stop()
        editor.close()
    sidebar.close()
