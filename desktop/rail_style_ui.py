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
    QMessageBox,
)
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt

try:
    from .rail_categories import TRACK_TYPES
except ImportError:
    from rail_categories import TRACK_TYPES

ZOOM_CURVE_KEY = "_zoom_width_curve"
RECOMMENDED_ZOOM_CURVE = (
    {"zoom": 3.0, "scale": 0.8},
    {"zoom": 5.0, "scale": 0.9},
    {"zoom": 8.0, "scale": 1.05},
    {"zoom": 12.0, "scale": 1.25},
    {"zoom": 16.0, "scale": 1.5},
    {"zoom": 19.0, "scale": 1.8},
)


def recommended_zoom_curve():
    return [dict(point) for point in RECOMMENDED_ZOOM_CURVE]


def defaults():
    colors = {
        "高速铁路线": "#c52c3b",
        "普速铁路线": "#283541",
        "货运铁路线": "#4b5055",
        "联络线 / 匝道": "#75609a",
        "支线 / 岔道": "#557a69",
        "渡线 / 道岔连接轨": "#e09036",
        "高速铁路站场股道": "#b75964",
    }
    styles = {
        name: {
            "color": colors.get(name, "#667887"),
            "width": 2.5 if name in ("高速铁路线", "普速铁路线", "货运铁路线") else 1.5,
            "pattern": "alternating",
        }
        for name in TRACK_TYPES
    }
    styles[ZOOM_CURVE_KEY] = recommended_zoom_curve()
    return styles


def validate_styles(value):
    if not isinstance(value, dict):
        raise ValueError("铁路样式必须完整包含所有轨道类型")
    value = dict(value)
    value.setdefault(ZOOM_CURVE_KEY, recommended_zoom_curve())
    if set(value) != set(TRACK_TYPES) | {ZOOM_CURVE_KEY}:
        raise ValueError("铁路样式必须完整包含所有轨道类型和缩放线宽曲线")
    for name in TRACK_TYPES:
        item = value[name]
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
    curve = value[ZOOM_CURVE_KEY]
    if not isinstance(curve, list) or not 2 <= len(curve) <= 8:
        raise ValueError("视角高度—线宽曲线需要 2–8 个控制点")
    normalized, previous = [], -1.0
    for point in curve:
        if not isinstance(point, dict) or set(point) != {"zoom", "scale"}:
            raise ValueError("曲线控制点只能包含 zoom 和 scale")
        zoom, scale = point["zoom"], point["scale"]
        if (
            type(zoom) not in (int, float)
            or type(scale) not in (int, float)
            or not math.isfinite(zoom)
            or not math.isfinite(scale)
            or not 0 <= zoom <= 22
            or not 0.1 <= scale <= 4
            or zoom <= previous
        ):
            raise ValueError("缩放级别须递增且位于 0–22，线宽倍率须位于 0.1–4")
        normalized.append({"zoom": float(zoom), "scale": float(scale)})
        previous = zoom
    value[ZOOM_CURVE_KEY] = normalized
    return value


def load_styles(path):
    try:
        return validate_styles(json.loads(Path(path).read_text(encoding="utf-8")))
    except (ValueError, OSError, TypeError):
        return defaults()


class RailStyleDialog(QDialog):
    def __init__(self, styles, parent=None):
        super().__init__(parent)
        self.setWindowTitle("铁路样式 · 轨道类型与视角线宽")
        self.resize(700, 760)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("先设置各轨道类型的基准线宽，再用下方曲线定义不同地图缩放级别的线宽倍率。"))
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
        layout.addWidget(QLabel("视角高度—线宽关系（缩放级别越小，视角越高；推荐曲线保证全国视角仍清晰可见）"))
        self.curve_table = QTableWidget(len(styles[ZOOM_CURVE_KEY]), 2)
        self.curve_table.setHorizontalHeaderLabels(["地图缩放级别", "基准线宽倍率"])
        self.curve_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.curve_table.verticalHeader().hide()
        self.curve_controls = []
        for row, point in enumerate(styles[ZOOM_CURVE_KEY]):
            zoom = QDoubleSpinBox()
            zoom.setRange(0, 22)
            zoom.setSingleStep(0.5)
            zoom.setValue(point["zoom"])
            scale = QDoubleSpinBox()
            scale.setRange(0.1, 4)
            scale.setSingleStep(0.05)
            scale.setValue(point["scale"])
            scale.setSuffix(" ×")
            self.curve_table.setCellWidget(row, 0, zoom)
            self.curve_table.setCellWidget(row, 1, scale)
            self.curve_table.setRowHeight(row, 36)
            self.curve_controls.append((zoom, scale))
        self.curve_table.setMaximumHeight(250)
        layout.addWidget(self.curve_table)
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

    def accept(self):
        try:
            self.value()
        except ValueError as error:
            QMessageBox.warning(self, "视角线宽曲线无效", str(error))
            return
        super().accept()

    def reset(self):
        for name, (color, width, pattern) in self.controls.items():
            color.setText(defaults()[name]["color"])
            width.setValue(defaults()[name]["width"])
            pattern.setCurrentIndex(0)
        for controls, point in zip(self.curve_controls, recommended_zoom_curve()):
            controls[0].setValue(point["zoom"])
            controls[1].setValue(point["scale"])

    def value(self):
        value = {
            name: {
                "color": color.text(),
                "width": width.value(),
                "pattern": pattern.currentData(),
            }
            for name, (color, width, pattern) in self.controls.items()
        }
        value[ZOOM_CURVE_KEY] = [
            {"zoom": zoom.value(), "scale": scale.value()}
            for zoom, scale in self.curve_controls
        ]
        return validate_styles(value)
