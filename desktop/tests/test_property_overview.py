from PySide6.QtCore import Qt
from desktop.property_overview import PropertyOverview


def test_overview_stacks_all_fields_and_keeps_long_values(qtbot):
    from PySide6.QtGui import QFontDatabase, QFont
    from pathlib import Path
    font = Path('C:/Windows/Fonts/msyh.ttc')
    if font.exists():
        family = QFontDatabase.applicationFontFamilies(QFontDatabase.addApplicationFont(str(font)))[0]
    else:
        family = 'Arial'
    overview = PropertyOverview()
    qtbot.addWidget(overview)
    overview.setFont(QFont(family, 10))
    overview.resize(300, 500)
    stable = 'RL-' + '0123456789abcdef' * 8
    overview.set_rows([('稳定编号', stable), ('运行状态', '行驶'), ('备注', '第一行\n第二行')])
    overview.show()
    qtbot.waitUntil(lambda: overview.isVisible())
    assert overview.columnCount() == 1
    assert overview.item(0, 0).text() == '稳定编号'
    assert overview.item(1, 0).text() == stable
    assert overview.rowHeight(1) > overview.rowHeight(0)
    assert overview.item(1, 0).data(Qt.ItemDataRole.ToolTipRole) == stable
    overview.update_value('运行状态', '停车')
    assert overview.item(3, 0).text() == '停车'
    overview.resize(450, 500)
    qtbot.wait(10)
    assert overview.item(1, 0).text() == stable
