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
    is_ready = False

    def __init__(self):
        self.bridge = Bridge()

    def call(self, *args):
        pass


def test_native_table_and_diagram_drag_edit_same_plan(tmp_path):
    app = QApplication.instance() or QApplication([])
    assert app is not None
    line = {
        "id": "sh-1",
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
