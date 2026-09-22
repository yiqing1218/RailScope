"""Persistent metro line widths with a zoom-dependent recommended curve."""

import json
import math
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QTableWidget,
    QHeaderView,
)


def defaults():
    return {
        "operating_width": 5.0,
        "construction_width": 5.0,
        "zoom_curve": [
            {"zoom": 3.0, "scale": 0.30},
            {"zoom": 8.0, "scale": 0.60},
            {"zoom": 12.0, "scale": 1.00},
            {"zoom": 16.0, "scale": 1.25},
            {"zoom": 19.0, "scale": 1.45},
        ],
    }


def validate_styles(value):
    if not isinstance(value, dict) or set(value) != {
        "operating_width",
        "construction_width",
        "zoom_curve",
    }:
        raise ValueError("地铁线路样式字段不完整")
    result = dict(value)
    for key in ("operating_width", "construction_width"):
        number = value[key]
        if type(number) not in (int, float) or not math.isfinite(number) or not 0.25 <= number <= 12:
            raise ValueError("地铁线路基准线宽须为 0.25–12 px")
        result[key] = float(number)
    curve = value["zoom_curve"]
    if not isinstance(curve, list) or not 2 <= len(curve) <= 8:
        raise ValueError("地铁视角线宽曲线需要 2–8 个控制点")
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
    result["zoom_curve"] = normalized
    return result


def load_styles(path):
    try:
        return validate_styles(json.loads(Path(path).read_text(encoding="utf-8")))
    except (ValueError, OSError, TypeError):
        return defaults()


class MetroStyleDialog(QDialog):
    def __init__(self, styles, parent=None):
        super().__init__(parent)
        self.setWindowTitle("地铁线路样式 · 视角线宽")
        self.resize(600, 500)
        form = QFormLayout(self)
        form.addRow(QLabel("设置运营线和在建线的基准宽度；下方倍率随地图缩放平滑变化。"))
        self.operating = QDoubleSpinBox()
        self.construction = QDoubleSpinBox()
        for control in (self.operating, self.construction):
            control.setRange(0.25, 12)
            control.setSingleStep(0.25)
            control.setSuffix(" px")
        self.operating.setValue(styles["operating_width"])
        self.construction.setValue(styles["construction_width"])
        form.addRow("运营地铁线", self.operating)
        form.addRow("在建地铁线", self.construction)
        self.table = QTableWidget(len(styles["zoom_curve"]), 2)
        self.table.setHorizontalHeaderLabels(["地图缩放级别", "线宽倍率"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.controls = []
        for row, point in enumerate(styles["zoom_curve"]):
            zoom, scale = QDoubleSpinBox(), QDoubleSpinBox()
            zoom.setRange(0, 22)
            zoom.setSingleStep(0.5)
            scale.setRange(0.1, 4)
            scale.setSingleStep(0.05)
            scale.setSuffix(" ×")
            zoom.setValue(point["zoom"])
            scale.setValue(point["scale"])
            self.table.setCellWidget(row, 0, zoom)
            self.table.setCellWidget(row, 1, scale)
            self.controls.append((zoom, scale))
        form.addRow("视角—线宽曲线", self.table)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(self.reset)
        form.addRow(buttons)

    def reset(self):
        value = defaults()
        self.operating.setValue(value["operating_width"])
        self.construction.setValue(value["construction_width"])
        for controls, point in zip(self.controls, value["zoom_curve"]):
            controls[0].setValue(point["zoom"])
            controls[1].setValue(point["scale"])

    def value(self):
        return validate_styles(
            {
                "operating_width": self.operating.value(),
                "construction_width": self.construction.value(),
                "zoom_curve": [
                    {"zoom": zoom.value(), "scale": scale.value()}
                    for zoom, scale in self.controls
                ],
            }
        )

    def accept(self):
        try:
            self.value()
        except ValueError as error:
            QMessageBox.warning(self, "地铁线路样式无效", str(error))
            return
        super().accept()
