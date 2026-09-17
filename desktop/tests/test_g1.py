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
