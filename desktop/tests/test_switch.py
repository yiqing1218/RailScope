import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest

pytest.importorskip("PySide6")
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from desktop.components import Switch


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
