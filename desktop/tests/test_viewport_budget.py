"""Budget checks exercise real SQLite rows and the final presented payload."""
import json
import sqlite3

from desktop.rail_store import viewport
from desktop.viewport_settings import DEFAULT


def point(number, name="站"):
    return {"type": "Feature", "properties": {"osm_node_id": number, "kind": "station", "name": name},
            "geometry": {"type": "Point", "coordinates": [121, 31]}}


def store(directory, features, kind="railPoints"):
    with sqlite3.connect(directory / "rail.sqlite") as db:
        db.executescript("CREATE TABLE features(id INTEGER PRIMARY KEY,kind TEXT,service TEXT,data TEXT);"
                         "CREATE VIRTUAL TABLE bounds USING rtree(id,minx,maxx,miny,maxy);")
        for number, feature in enumerate(features, 1):
            db.execute("INSERT INTO features VALUES(?,?,?,?)", (number, kind, "main", json.dumps(feature, ensure_ascii=False)))
            db.execute("INSERT INTO bounds VALUES(?,?,?,?,?)", (number, 121, 121.01, 31, 31.01))


def test_selected_line_obeys_same_feature_limit(tmp_path):
    features = [{"type": "Feature", "properties": {"osm_way_id": 10},
                 "geometry": {"type": "LineString", "coordinates": [[121, 31], [121.01, 31.01]]}}
                for _ in range(4)]
    store(tmp_path, features, "rail")
    result = viewport(tmp_path, "rail", [120, 30, 122, 32], 14, {"ways": [10]}, {**DEFAULT, "features": 2})
    assert len(result["features"]) == 2
    assert result["truncated"] is True


def test_oversized_rows_do_not_consume_returned_feature_slots(tmp_path):
    store(tmp_path, [point(1, "长" * 1000), point(2, "长" * 1000), point(3), point(4)])
    result = viewport(tmp_path, "railPoints", [120, 30, 122, 32], 14,
                      limits={**DEFAULT, "features": 2, "feature_bytes": 500})
    assert [f["properties"]["osm_node_id"] for f in result["features"]] == [3, 4]
    assert result["truncated"] is True


def test_feature_bytes_use_utf8_and_skip_large_row_for_smaller_rows(tmp_path):
    store(tmp_path, [point(1, "站" * 70), point(2)])
    result = viewport(tmp_path, "railPoints", [120, 30, 122, 32], 14,
                      limits={**DEFAULT, "feature_bytes": 350})
    assert [f["properties"]["osm_node_id"] for f in result["features"]] == [2]
    assert result["truncated"] is True


def test_total_bytes_can_skip_nonfitting_row_and_keep_smaller_row(tmp_path):
    store(tmp_path, [point(1, "站" * 100), point(2)])
    result = viewport(tmp_path, "railPoints", [120, 30, 122, 32], 14,
                      limits={**DEFAULT, "bytes": 350})
    assert [f["properties"]["osm_node_id"] for f in result["features"]] == [2]


def test_hidden_control_points_do_not_consume_station_budget(tmp_path):
    control = point(1)
    control["properties"]["kind"] = "topology_junction"
    store(tmp_path, [control, point(2)])
    result = viewport(tmp_path, "railPoints", [120, 30, 122, 32], 16,
                      selection={"point_stations": True, "point_controls": False},
                      limits={**DEFAULT, "features": 1})
    assert [f["properties"]["osm_node_id"] for f in result["features"]] == [2]
    assert result["truncated"] is False


def test_presented_payload_is_rechecked_after_names_and_properties_grow():
    from desktop.viewport_settings import enforce_budget

    result = {"type": "FeatureCollection", "features": [point(1, "站" * 100), point(2)]}
    enforce_budget(result, {**DEFAULT, "feature_bytes": 350})
    assert [f["properties"]["osm_node_id"] for f in result["features"]] == [2]
    assert result["budget"]["usage"]["bytes"] == len(json.dumps(result["features"][0], ensure_ascii=False).encode("utf-8"))
    assert result["budget"]["reasons"] == ["feature_bytes"]
    assert result["budget"]["usage"]["vertices"] == 1


def test_budget_dialog_ranges_and_roundtrip(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QDoubleSpinBox, QMainWindow, QSpinBox
    import launcher
    from desktop.viewport_settings import MAXIMUM

    app = QApplication.instance() or QApplication([])
    owner = QMainWindow()
    current = {**DEFAULT, "bytes": 1234567, "feature_bytes": 234567}
    owner.config = {"railViewportBudget": current.copy()}
    calls = []
    owner.map = SimpleNamespace(call=lambda *args: calls.append(args))
    monkeypatch.setattr(launcher, "ROOT", tmp_path)

    def accept(dialog):
        assert dialog.findChild(QComboBox).currentData() == "custom"
        controls = dialog.findChildren(QSpinBox) + dialog.findChildren(QDoubleSpinBox)
        assert len(controls) == 4
        for control in controls:
            key = control.property("budgetKey")
            assert round(control.maximum() * control.property("budgetDivisor")) == MAXIMUM[key]
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", accept)
    launcher.Desk.edit_viewport_budget(owner)
    assert owner.config["railViewportBudget"] == current
    assert calls == [("reloadRailViewport",)]
    owner.close()


def test_custom_profile_survives_equal_preset_values_and_reopening(monkeypatch, tmp_path, qtbot):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QComboBox, QDialog, QMainWindow
    import launcher
    from viewport_settings import HIGH, load_settings
    owner=QMainWindow();qtbot.addWidget(owner)
    owner.config={'railViewportBudget':HIGH.copy(),'railViewportMode':'custom','railViewportCustomBudget':HIGH.copy()}
    owner.map=SimpleNamespace(call=lambda *args:None)
    monkeypatch.setattr(launcher,'ROOT',tmp_path)
    def accept(dialog):
        assert dialog.findChild(QComboBox).currentData()=='custom'
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog,'exec',accept)
    launcher.Desk.edit_viewport_budget(owner)
    saved=load_settings(tmp_path/'data/user_settings/rail_viewport_budget.json')
    assert saved['mode']=='custom'
    assert saved['limits']==HIGH
    launcher.Desk.edit_viewport_budget(owner)


def test_custom_values_above_high_preset_are_effective_and_preserved(tmp_path):
    from viewport_settings import HIGH, normalize, save_settings, load_settings
    custom={key:value*2 for key,value in HIGH.items()}
    assert normalize(custom)==custom
    path=tmp_path/'budget.json'
    save_settings(path,'custom',custom)
    assert load_settings(path)=={'mode':'custom','limits':custom,'custom':custom}
    save_settings(path,'low',custom)
    assert load_settings(path)['custom']==custom


def test_http_budget_is_applied_after_display_names(monkeypatch, tmp_path):
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.request import Request, urlopen
    import launcher
    import rail_station_directory

    store(tmp_path, [point(1), point(2)])
    monkeypatch.setattr(launcher, "active_rail_directory", lambda root: tmp_path)
    monkeypatch.setattr(rail_station_directory, "load_directory", lambda path: {})
    monkeypatch.setattr(launcher, "apply_rail_presentation", lambda *args: None)

    def rename(collection, *args):
        collection["features"][0]["properties"]["display_name"] = "长名称" * 100

    monkeypatch.setattr(launcher, "apply_names", rename)
    server = ThreadingHTTPServer(("127.0.0.1", 0), launcher.LocalHandler)
    server.config = {"railViewportBudget": {**DEFAULT, "feature_bytes": 350}}
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        body = json.dumps({"kind": "railPoints", "bbox": "120,30,122,32", "zoom": "16",
                           "point_stations": "true", "point_controls": "false"}).encode()
        request = Request(f"http://127.0.0.1:{server.server_port}/api/rail", data=body,
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=3) as response:
            result = json.load(response)
        assert [f["properties"]["osm_node_id"] for f in result["features"]] == [2]
        assert result["budget"]["reasons"] == ["feature_bytes"]
        assert result["budget"]["usage"]["features"] == 1
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_custom_budget_can_load_beyond_the_old_scan_ceiling(tmp_path):
    from desktop.viewport_settings import HIGH
    store(tmp_path, [point(i) for i in range(24010)])
    custom = {key: value * 3 for key, value in HIGH.items()}
    result = viewport(tmp_path, "railPoints", [120, 30, 122, 32], 16, limits=custom)
    assert len(result["features"]) == 24010
    assert not result["truncated"] and result["budget"]["limits"] == custom


def test_dialog_restores_custom_after_visiting_both_presets(monkeypatch, tmp_path, qtbot):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QDialog, QMainWindow, QComboBox, QSpinBox
    import launcher
    owner = QMainWindow()
    qtbot.addWidget(owner)
    owner.config = {"railViewportBudget": DEFAULT.copy(), "railViewportMode": "low",
                    "railViewportCustomBudget": {**DEFAULT, "features": 30000}}
    owner.map = SimpleNamespace(call=lambda *args: None)
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    def accept(dialog):
        combo = dialog.findChild(QComboBox)
        fields = {w.property("budgetKey"): w for w in dialog.findChildren(QSpinBox)}
        combo.setCurrentIndex(combo.findData("custom"))
        assert fields["features"].value() == 30000
        fields["features"].setValue(40000)
        for mode in ("high", "low", "custom"):
            combo.setCurrentIndex(combo.findData(mode))
        assert fields["features"].value() == 40000
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, "exec", accept)
    launcher.Desk.edit_viewport_budget(owner)
    assert owner.config["railViewportMode"] == "custom"
    assert owner.config["railViewportBudget"]["features"] == 40000
