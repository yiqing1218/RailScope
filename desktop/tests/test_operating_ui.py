import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QObject, Signal, Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from desktop.operating import Plan
from desktop.operating_ui import OperationsEditor, TimeHandle


class Bridge(QObject):
    initialized = Signal()


class MapStub:
    is_ready = True

    def __init__(self):
        self.bridge = Bridge()
        self.calls = []

    def call(self, *args):
        self.calls.append(args)


def test_clean_checkout_with_no_metro_data_opens_editor(tmp_path):
    app = QApplication.instance() or QApplication([])
    assert app
    editor = OperationsEditor(Plan([]), MapStub(), [], tmp_path / "plan.json")
    sidebar = editor.sidebar()
    assert editor.table.rowCount() == 0
    assert not editor.enabled and editor.current_line() is None
    editor.timer.stop()
    editor.close()
    sidebar.close()


def test_beijing_time_uses_wall_clock_at_one_speed_and_exits_on_manual_control(tmp_path,monkeypatch):
    from datetime import datetime, timezone, timedelta
    import desktop.operating_ui as module
    from desktop.session_time import SessionTime
    app=QApplication.instance() or QApplication([])
    editor=OperationsEditor(Plan([]),MapStub(),[],tmp_path/'plan.json')
    sidebar=editor.sidebar();session=SessionTime();editor.bind_session(session)
    now=datetime(2026,10,8,23,59,59,500000,tzinfo=timezone(timedelta(hours=8)))
    monkeypatch.setattr(module,'beijing_now',lambda:now)
    editor.beijing_switch.click()
    assert editor.enabled and editor.playing and editor.follow_beijing
    assert session.day=='2026-10-08' and session.seconds==86399.5
    assert editor.motion_speed()==1 and not editor.speed_slider.isEnabled()
    now=datetime(2026,10,9,0,0,1,tzinfo=now.tzinfo)
    editor.tick()
    assert session.day=='2026-10-09' and editor.clock==1
    editor.beijing_switch.click()
    assert not editor.follow_beijing and editor.playing and editor.clock==1
    assert editor.speed_slider.isEnabled() and editor.motion_speed()==30
    editor.beijing_switch.click();editor.time_input.setText('12:30:00');editor.jump()
    assert not editor.follow_beijing and editor.clock==45000
    editor.beijing_switch.click();editor.pause()
    assert not editor.follow_beijing and not editor.beijing_switch.isChecked()
    editor.timer.stop();session._timer.stop();sidebar.close();editor.close()


def test_running_only_pauses_explicitly_and_does_not_stop_when_display_is_hidden(tmp_path):
    from time import monotonic
    app=QApplication.instance() or QApplication([])
    editor=OperationsEditor(Plan([]),MapStub(),[],tmp_path/'plan.json')
    sidebar=editor.sidebar()
    editor.play();editor.set_enabled(False)
    editor._last_tick=monotonic()-1
    before=editor.clock;editor.tick()
    assert editor.playing and editor.clock>before
    sidebar.hide();editor.hide()
    assert editor.playing
    editor.clock=172798;editor._last_tick=monotonic()-1;editor.tick()
    assert editor.playing and editor.clock<100
    editor.pause();assert not editor.playing
    editor.timer.stop();sidebar.close();editor.close()


def test_simulation_is_opt_in_and_can_pause_disable_and_change_marker(tmp_path):
    app = QApplication.instance() or QApplication([])
    assert app
    line = {
        "id": "sh-1",
        "ref": "1",
        "relation_id": 199200,
        "name": "1号线",
        "color": "#c82732",
        "variants": [],
        "path": {
            "coordinates": [[121, 31], [121.01, 31]],
            "length_m": 1000,
            "cumulative": [0, 1000],
        },
        "stations": [
            {"id": "a", "name": "A", "distance_m": 0},
            {"id": "b", "name": "B", "distance_m": 1000},
        ],
    }
    plan = Plan([line])
    plan.add_train("sh-1", "T1", 25200)
    map_view = MapStub()
    editor = OperationsEditor(plan, map_view, [line], tmp_path / "plan.json")
    sidebar = editor.sidebar()
    map_view.bridge.initialized.emit()
    editor.tick()
    assert not editor.enabled and not editor.playing
    assert editor.clock == 25200 and editor.current_vehicle_features == []
    editor.play()
    assert editor.enabled and editor.playing and editor.current_vehicle_features
    editor.pause()
    clock = editor.clock
    editor.tick()
    assert editor.enabled and not editor.playing and editor.clock == clock
    assert editor.current_vehicle_features
    editor.marker_size.setValue(28)
    editor.marker_style.setCurrentIndex(2)
    assert ("setVehicleAppearance", {"size": 28, "style": "train"}) in map_view.calls
    editor.set_enabled(False)
    assert not editor.enabled and not editor.playing
    assert editor.current_vehicle_features == []
    assert not editor.enabled_switch.isChecked()
    editor.timer.stop()
    editor.close()
    sidebar.close()


def test_native_table_and_diagram_drag_edit_same_plan(tmp_path):
    app = QApplication.instance() or QApplication([])
    assert app is not None
    line = {
        "id": "sh-1",
        "relation_id": 199200,
        "ref": "1",
        "name": "1号线",
        "color": "#c82732",
        "variants": [],
        "path": {
            "coordinates": [[121, 31], [121.01, 31]],
            "length_m": 1000,
            "cumulative": [0, 1000],
        },
        "stations": [
            {"id": "a", "name": "A", "distance_m": 0},
            {"id": "b", "name": "B", "distance_m": 1000},
        ],
    }
    plan = Plan([line])
    plan.add_train("sh-1", "T1", 25200)
    editor = OperationsEditor(plan, MapStub(), [line], tmp_path / "plan.json")
    editor.resize(1000, 800)
    editor.show()
    QTest.qWait(50)
    editor.table.item(0, 5).setText("07:00:45")
    assert plan.train("T1")["stops"][0]["departure_s"] == 25245
    editor.undo()
    assert plan.train("T1")["stops"][0]["departure_s"] == 25230
    editor.redo()
    assert plan.train("T1")["stops"][0]["departure_s"] == 25245
    editor.tabs.setCurrentIndex(1)
    QTest.qWait(50)
    handle = next(
        item
        for item in editor.scene.items()
        if isinstance(item, TimeHandle) and item.index == 0 and item.kind == "arrival_s"
    )
    editor.diagram.ensureVisible(handle)
    QTest.qWait(50)
    start = editor.diagram.mapFromScene(handle.scenePos())
    end = start + QPoint(5, 0)
    QTest.mousePress(
        editor.diagram.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        start,
    )
    QTest.mouseMove(editor.diagram.viewport(), end, 20)
    QTest.mouseRelease(
        editor.diagram.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        end,
    )
    QTest.qWait(50)
    assert plan.train("T1")["stops"][0]["arrival_s"] == 25215
    assert editor.table.item(0, 4).text() == "07:00:15"
    editor.timer.stop()
    editor.close()


def test_vehicle_source_updates_are_throttled_and_per_train_style_is_embedded(tmp_path):
    app = QApplication.instance() or QApplication([])
    assert app is not None
    line = {
        "id": "sh-1", "ref": "1", "relation_id": 199200, "name": "1号线",
        "color": "#c82732", "variants": [],
        "path": {"coordinates": [[121, 31], [121.01, 31]], "length_m": 1000, "cumulative": [0, 1000]},
        "stations": [{"id": "a", "name": "A", "distance_m": 0}, {"id": "b", "name": "B", "distance_m": 1000}],
    }
    plan = Plan([line])
    train = plan.add_train("sh-1", "T1", 25200)
    train["extensions"] = {"railscope.org/display": {"style": "ring", "size": 22, "color": "#123456"}}
    view = MapStub()
    editor = OperationsEditor(plan, view, [line], tmp_path / "plan.json")
    editor.set_enabled(True)
    before = len([call for call in view.calls if call[0] == "setOperatingVehicles"])
    editor.push_positions(throttled=True)
    editor.push_positions(throttled=True)
    after = [call for call in view.calls if call[0] == "setOperatingVehicles"]
    assert len(after) == before
    props = editor.current_vehicle_features[0]["properties"]
    assert (props["display_style"], props["display_size"], props["display_color"]) == ("ring", 22, "#123456")
    editor.timer.stop()
    editor.close()


def test_switching_lines_reuses_visibility_controls_but_plan_changes_rebuild(tmp_path):
    from copy import deepcopy
    app = QApplication.instance() or QApplication([])
    assert app
    first = {'id': 'sh-1', 'ref': '1', 'relation_id': 1, 'name': '1号线',
        'color': '#c82732', 'variants': [],
        'path': {'coordinates': [[121, 31], [121.01, 31]], 'length_m': 1000, 'cumulative': [0, 1000]},
        'stations': [{'id': 'a', 'name': 'A', 'distance_m': 0}, {'id': 'b', 'name': 'B', 'distance_m': 1000}]}
    second = {**deepcopy(first), 'id': 'sh-2', 'ref': '2', 'relation_id': 2, 'name': '2号线'}
    plan = Plan([first, second])
    plan.add_train('sh-1', 'T1', 25200)
    plan.add_train('sh-2', 'T2', 25200)
    editor = OperationsEditor(plan, MapStub(), [first, second], tmp_path / 'plan.json')
    sidebar = editor.sidebar()
    tree = editor.vehicle_tree
    parent = tree.topLevelItem(1)
    control = parent.child(0)
    for index in range(20):
        editor.line_combo.setCurrentIndex(index % 2)
        assert tree.topLevelItem(1).child(0) is control
    plan.add_train('sh-1', 'T3', 26000)
    editor.refresh_vehicle_tree()
    assert tree.topLevelItem(1).childCount() == 2
    editor.set_trains_visible({'T1'}, False)
    editor.refresh_vehicle_tree()
    assert tree.topLevelItem(1).child(0).checkState(0)==Qt.CheckState.Unchecked
    assert tree.columnCount()==1
    tree.topLevelItem(1).setCheckState(0,Qt.CheckState.Unchecked)
    app.processEvents()
    assert editor.hidden_trains=={'T1','T3'}
    tree.topLevelItem(0).setCheckState(0,Qt.CheckState.Checked)
    app.processEvents()
    assert not editor.hidden_trains
    editor.timer.stop()
    editor.close()
    sidebar.close()
