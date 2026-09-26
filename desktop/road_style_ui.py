"""Highway appearance stored separately from source geometry."""

import json
import math
from pathlib import Path
import re

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QFormLayout, QComboBox, QDoubleSpinBox,
                             QPushButton, QColorDialog, QDialogButtonBox, QLabel)


def defaults():
    return {'national_color': '#176c9a', 'provincial_color': '#bb7538',
            'other_color': '#8898a4', 'width': 2.5, 'pattern': 'solid',
            'dash_length': 3.0, 'dash_gap': 2.0}


def validate_styles(value):
    if not isinstance(value, dict) or set(value) != set(defaults()):
        raise ValueError('高速公路样式字段不完整')
    for key in ('national_color', 'provincial_color', 'other_color'):
        if not isinstance(value[key], str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', value[key]):
            raise ValueError('颜色格式为 #RRGGBB')
    for key in ('width', 'dash_length', 'dash_gap'):
        if type(value[key]) not in (int, float) or not math.isfinite(value[key]) or not .25 <= value[key] <= 12:
            raise ValueError('线宽及虚线长度范围为 0.25–12')
    if value['pattern'] not in ('solid', 'dashed'):
        raise ValueError('请选择实线或虚线')
    return dict(value)


def load_styles(path):
    try:
        return validate_styles(json.loads(Path(path).read_text(encoding='utf-8')))
    except (ValueError, OSError, TypeError):
        return defaults()


class RoadStyleDialog(QDialog):
    def __init__(self, styles, parent=None):
        super().__init__(parent)
        self.setWindowTitle('高速公路样式')
        self.resize(460, 350)
        layout = QFormLayout(self)
        layout.addRow(QLabel('实线连续显示；虚线的线段与间隔按线宽倍数设置。'))
        self.controls = {}
        for key, label in [('national_color', '国家高速 G'), ('provincial_color', '省级高速 S'), ('other_color', '其他高速')]:
            button = QPushButton(styles[key]); self.controls[key] = button
            button.setStyleSheet('color: ' + styles[key])
            button.clicked.connect(lambda _, b=button: self.choose_color(b))
            layout.addRow(label, button)
        pattern = QComboBox(); pattern.addItem('实线', 'solid'); pattern.addItem('虚线', 'dashed')
        pattern.setCurrentIndex(pattern.findData(styles['pattern']))
        self.controls['pattern'] = pattern
        layout.addRow('线型', pattern)
        for key, label in [('width', '基准线宽 px'), ('dash_length', '虚线线段'), ('dash_gap', '虚线间隔')]:
            control = QDoubleSpinBox(); control.setRange(.25, 12); control.setSingleStep(.25)
            control.setValue(styles[key]); self.controls[key] = control
            layout.addRow(label, control)
        def toggle():
            for key in ('dash_length', 'dash_gap'):
                self.controls[key].setEnabled(pattern.currentData() == 'dashed')
        pattern.currentIndexChanged.connect(toggle); toggle()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('应用样式')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def choose_color(self, button):
        color = QColorDialog.getColor(QColor(button.text()), self)
        if color.isValid():
            button.setText(color.name()); button.setStyleSheet('color: ' + color.name())

    def value(self):
        return validate_styles({key: control.currentData() if key == 'pattern' else
            control.text() if key.endswith('_color') else control.value() for key, control in self.controls.items()})
