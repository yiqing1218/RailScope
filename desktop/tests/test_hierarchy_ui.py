import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from desktop.hierarchy import Hierarchy
from desktop.hierarchy_ui import HierarchyDialog


def model(path):
    return Hierarchy(
        [
            {
                "osm_relation_id": 1,
                "ref": "4",
                "name": "昆明4正向",
                "network": "昆明地铁",
            },
            {
                "osm_relation_id": 2,
                "ref": "4",
                "name": "昆明4反向",
                "network": "昆明地铁",
            },
            {"osm_relation_id": 3, "ref": "1", "name": "昆明1", "network": "昆明地铁"},
        ],
        {},
        [("昆明", "云南省", 102.83, 24.88)],
        path,
    )


def test_dialog_moves_both_directions_and_saves_then_resets(tmp_path):
    app = QApplication.instance() or QApplication([])
    assert app is not None
    original = model(tmp_path / "settings.json")
    dialog = HierarchyDialog(original, selected_ids={1, 2})
    dialog.show()
    QTest.qWait(20)
    assert dialog.ids() == {1, 2}
    dialog.province.setCurrentText("我的省")
    dialog.city.setCurrentText("我的城市")
    dialog.label.setText("我的4号线")
    dialog.apply_button.click()
    assert dialog.model.parent(original.routes[0]) == (
        "我的省",
        "我的城市",
        "我的4号线",
    )
    assert original.overrides == {}
    dialog.save_and_accept()
    original.load()
    assert original.parent(original.routes[1]) == ("我的省", "我的城市", "我的4号线")
    reset = HierarchyDialog(original, selected_ids={1, 2})
    reset.reset_button.click()
    reset.save_and_accept()
    original.load()
    assert original.overrides == {}
    assert original.parent(original.routes[0]) == ("云南省", "昆明", "4号线")
    dialog.close()
    reset.close()


def test_city_can_move_under_new_province_without_merging_lines(tmp_path):
    app = QApplication.instance() or QApplication([])
    assert app is not None
    original = model(tmp_path / "settings.json")
    dialog = HierarchyDialog(original)
    city = next(item for item in dialog.items if item.text(0) == "昆明")
    city.setSelected(True)
    assert dialog.ids() == {1, 2, 3}
    assert dialog.label.text() == ""
    dialog.province.setCurrentText("新的省")
    dialog.apply_button.click()
    assert set(dialog.model.grouped()["新的省"]["昆明"]) == {"1号线", "4号线"}
    dialog.reject()
    assert original.overrides == {}
    assert not original.path.exists()
