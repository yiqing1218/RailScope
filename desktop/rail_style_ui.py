"""Persistent type-specific rail styles; source geometry and tags remain untouched."""

import json
import math
from pathlib import Path
import re
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QPushButton,
    QDoubleSpinBox,
    QColorDialog,
    QDialogButtonBox,
    QLabel,
    QHeaderView,
)
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt

try:
    from .rail_categories import TRACK_TYPES
except ImportError:
    from rail_categories import TRACK_TYPES


def defaults():
    return {name: {"color": "#667887", "width": 2.0} for name in TRACK_TYPES}


def validate_styles(value):
    if not isinstance(value, dict) or set(value) != set(TRACK_TYPES):
        raise ValueError("铁路样式必须完整包含所有轨道类型")
    for item in value.values():
        if not isinstance(item, dict) or set(item) != {"color", "width"}:
            raise ValueError("样式只能包含 color 和 width")
        if not isinstance(item["color"], str) or not re.fullmatch(
            r"#[0-9a-fA-F]{6}", item["color"]
        ):
            raise ValueError("颜色格式为 #RRGGBB")
        if (
            type(item["width"]) not in (int, float)
            or not math.isfinite(item["width"])
            or not 0.25 <= item["width"] <= 12
        ):
            raise ValueError("线宽范围 0.25–12 px")
    return value


def load_styles(path):
    try:
        return validate_styles(json.loads(Path(path).read_text(encoding="utf-8")))
    except (ValueError, OSError, TypeError):
        return defaults()


class RailStyleDialog(QDialog):
    def __init__(self, styles, parent=None):
        super().__init__(parent)
        self.setWindowTitle("铁路样式 · 按轨道类型")
        self.resize(640, 560)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("设置颜色与基准线宽；远景自动细化。在建线路保留虚线。"))
        table = QTableWidget(len(TRACK_TYPES), 3)
        table.setHorizontalHeaderLabels(["轨道类型", "颜色", "线宽 px"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().hide()
        self.controls = {}
        for row, name in enumerate(TRACK_TYPES):
            label = QTableWidgetItem(name)
            label.setFlags(label.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row, 0, label)
            color = QPushButton(styles[name]["color"])
            color.clicked.connect(
                lambda checked=False, button=color: self.choose_color(button)
            )
            table.setCellWidget(row, 1, color)
            width = QDoubleSpinBox()
            width.setRange(0.25, 12)
            width.setSingleStep(0.25)
            width.setValue(styles[name]["width"])
            table.setCellWidget(row, 2, width)
            table.setRowHeight(row, 40)
            self.controls[name] = color, width
        layout.addWidget(table)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(
            self.reset
        )
        layout.addWidget(buttons)

    def choose_color(self, button):
        color = QColorDialog.getColor(QColor(button.text()), self, "轨道颜色")
        if color.isValid():
            button.setText(color.name())

    def reset(self):
        for name, (color, width) in self.controls.items():
            color.setText(defaults()[name]["color"])
            width.setValue(defaults()[name]["width"])

    def value(self):
        return validate_styles(
            {
                name: {"color": color.text(), "width": width.value()}
                for name, (color, width) in self.controls.items()
            }
        )
