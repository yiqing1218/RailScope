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
