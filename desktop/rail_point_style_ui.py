"""Validated workspace style for railway stations and control points."""

import json
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QSpinBox,
)


def defaults():
    return {
        "station": {"size": 4, "shape": "ring"},
        "control": {"size": 3, "shape": "solid"},
        "labels": {
            "show_station_names": True,
            "show_line_names": True,
            "station_font_size": 12,
            "line_font_size": 11,
        },
    }


def validate(value):
    if not isinstance(value, dict) or not {"station", "control"} <= set(value):
        raise ValueError("站点样式应包含车站和线路所/道岔两组")
    result = {}
    for kind in ("station", "control"):
        item = value[kind]
        if not isinstance(item, dict) or set(item) != {"size", "shape"}:
            raise ValueError("站点样式字段无效")
        if type(item["size"]) is not int or not 2 <= item["size"] <= 16:
            raise ValueError("站点半径须在 2–16 px")
        if item["shape"] not in {"ring", "solid"}:
            raise ValueError("站点图形须为空心圆或实心圆")
        result[kind] = dict(item)
    labels = value.get("labels", defaults()["labels"])
    if not isinstance(labels, dict):
        raise ValueError("铁路名称显示设置无效")
    normalized = dict(defaults()["labels"])
    normalized.update(labels)
    if type(normalized["show_station_names"]) is not bool or type(normalized["show_line_names"]) is not bool:
        raise ValueError("名称显示开关无效")
    for key in ("station_font_size", "line_font_size"):
        if type(normalized[key]) is not int or not 8 <= normalized[key] <= 32:
            raise ValueError("名称字号须在 8–32 px")
    result["labels"] = normalized
    return result


def load(path):
    try:
        return validate(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return defaults()


class RailPointStyleDialog(QDialog):
    def __init__(self, value, parent=None):
        super().__init__(parent)
        self.setWindowTitle("国铁车站与线路所/道岔样式")
        form = QFormLayout(self)
        self.controls = {}
        for kind, label in (("station", "车站"), ("control", "线路所与道岔")):
            size = QSpinBox()
            size.setRange(2, 16)
            size.setSuffix(" px 半径")
            size.setValue(value[kind]["size"])
            shape = QComboBox()
            shape.addItem("空心圆", "ring")
            shape.addItem("实心圆", "solid")
            shape.setCurrentIndex(shape.findData(value[kind]["shape"]))
            form.addRow(label + "大小", size)
            form.addRow(label + "图形", shape)
            self.controls[kind] = size, shape
        self.show_station_names = QCheckBox("显示铁路车站与线路所名称")
        self.show_station_names.setChecked(value["labels"]["show_station_names"])
        self.show_line_names = QCheckBox("显示铁路线名称")
        self.show_line_names.setChecked(value["labels"]["show_line_names"])
        self.station_font_size = QSpinBox()
        self.station_font_size.setRange(8, 32)
        self.station_font_size.setSuffix(" px")
        self.station_font_size.setValue(value["labels"]["station_font_size"])
        self.line_font_size = QSpinBox()
        self.line_font_size.setRange(8, 32)
        self.line_font_size.setSuffix(" px")
        self.line_font_size.setValue(value["labels"]["line_font_size"])
        form.addRow(self.show_station_names)
        form.addRow("车站名称字号", self.station_font_size)
        form.addRow(self.show_line_names)
        form.addRow("线路名称字号", self.line_font_size)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def value(self):
        value = {
            kind: {"size": size.value(), "shape": shape.currentData()}
            for kind, (size, shape) in self.controls.items()
        }
        value["labels"] = {
            "show_station_names": self.show_station_names.isChecked(),
            "show_line_names": self.show_line_names.isChecked(),
            "station_font_size": self.station_font_size.value(),
            "line_font_size": self.line_font_size.value(),
        }
        return validate(value)
