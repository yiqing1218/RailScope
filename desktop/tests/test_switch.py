import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest

pytest.importorskip("PySide6")
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from desktop.components import Switch, SquareSwitch, visibility_row


def test_real_thumb_moves_and_blocked_parent_sync_updates_visual():
    app = QApplication.instance() or QApplication([])
    assert app is not None
    control = Switch(False)
    control.show()
    control.click()
    QTest.qWait(200)
    assert control.isChecked()
    assert control.get_position() == pytest.approx(1)
    control.blockSignals(True)
    control.setChecked(False)
    control.blockSignals(False)
    assert control.get_position() == pytest.approx(0)
    control.setMixed(True)
    assert control._mixed
    assert not control.grab().isNull()
    control.close()


def test_visibility_square_is_left_aligned_and_keeps_partial_state():
    app = QApplication.instance() or QApplication([])
    control = SquareSwitch(False)
    row = visibility_row('车站及线路所', control)
    row.show()
    app.processEvents()
    assert row.layout().itemAt(0).widget() is control
    control.click()
    assert control.isChecked()
    control.setMixed(True)
    assert control._mixed
    control.setChecked(False)
    assert not control._mixed
    assert not control.grab().isNull()
    row.close()
