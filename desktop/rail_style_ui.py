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
    QComboBox,
)
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt

try:
    from .rail_categories import TRACK_TYPES
except ImportError:
    from rail_categories import TRACK_TYPES


def defaults():
    colors = {
        "高速铁路线": "#c52c3b",
        "普速铁路线": "#283541",
        "货运铁路线": "#78532c",
        "联络线 / 匝道": "#75609a",
        "支线 / 岔道": "#557a69",
        "渡线 / 道岔连接轨": "#e09036",
        "高速铁路站场股道": "#b75964",
    }
    return {
        name: {
            "color": colors.get(name, "#667887"),
            "width": 2.5,
            "pattern": "alternating",
        }
        for name in TRACK_TYPES
    }


def validate_styles(value):
    if not isinstance(value, dict) or set(value) != set(TRACK_TYPES):
        raise ValueError("铁路样式必须完整包含所有轨道类型")
    for item in value.values():
        if (
            not isinstance(item, dict)
            or not {"color", "width"}.issubset(item)
            or set(item) - {"color", "width", "pattern"}
        ):
            raise ValueError("样式只能包含 color、width 和 pattern")
        if item.get("pattern", "alternating") not in ("alternating", "solid"):
            raise ValueError("pattern 为 alternating 或 solid")
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
        table = QTableWidget(len(TRACK_TYPES), 4)
        table.setHorizontalHeaderLabels(["轨道类型", "颜色", "线宽 px", "轨道样式"])
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
            pattern = QComboBox()
            pattern.addItem("彩白相间", "alternating")
            pattern.addItem("实线", "solid")
            pattern.setCurrentIndex(1 if styles[name].get("pattern") == "solid" else 0)
            table.setCellWidget(row, 3, pattern)
            self.controls[name] = color, width, pattern
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
        for name, (color, width, pattern) in self.controls.items():
            color.setText(defaults()[name]["color"])
            width.setValue(defaults()[name]["width"])
            pattern.setCurrentIndex(0)

    def value(self):
        return validate_styles(
            {
                name: {
                    "color": color.text(),
                    "width": width.value(),
                    "pattern": pattern.currentData(),
                }
                for name, (color, width, pattern) in self.controls.items()
            }
        )
