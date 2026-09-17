import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication
from desktop.cycle_ui import CycleDialog
from desktop.operating import Plan
from desktop.operating_ui import OperationsEditor
from desktop.tests.test_operating_ui import MapStub


def test_unified_cycle_dialog_generates_and_undoes_entire_plan(tmp_path):
    app = QApplication.instance() or QApplication([])
    assert app
    line = {
        "id": "L",
        "name": "测试",
        "ref": "L",
        "relation_id": 1,
        "color": "#d02020",
        "variants": [],
        "path": {
            "coordinates": [[121, 31], [121.02, 31]],
            "cumulative": [0, 2000],
            "length_m": 2000,
        },
        "stations": [
            {"id": str(i), "name": str(i), "distance_m": i * 1000} for i in range(3)
        ],
    }
    editor = OperationsEditor(Plan([line]), MapStub(), [line], tmp_path / "plan.json")
    sidebar = editor.sidebar()
    dialog = CycleDialog(editor)
    dialog.name.setText("L/short")
    dialog.last.setCurrentIndex(1)
    dialog.fill_fleet()
    dialog.apply()
    assert editor.plan.cycles[0]["id"] == "L/short" and len(editor.plan.vehicles) == 6
    assert all(len(t["stops"]) == 2 for t in editor.plan.trains)
    editor.undo()
    assert not editor.plan.trains and not editor.plan.cycles
    editor.redo()
    assert editor.plan.cycles and editor.plan.vehicles
    editor.timer.stop()
    dialog.close()
    editor.close()
    sidebar.close()
