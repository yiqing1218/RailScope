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
    QHBoxLayout,
    QMessageBox,
)
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt

try:
    from .rail_categories import TRACK_TYPES
    from .rail_style_resolver import (STYLE_SOURCES, STYLE_LABELS, COMPAT_STYLE_SOURCES,
        STYLE_SELECTIONS, DEFAULT_STYLE_KEY, CONFIGURED_STYLE_KEYS, GROUP_LABELS, CATEGORY_LABELS,
        TRACK_LINE_LABELS, STATION_LINE_LABELS, SPEED_BANDS, selection_style_key)
except ImportError:
    from rail_categories import TRACK_TYPES
    from rail_style_resolver import (STYLE_SOURCES, STYLE_LABELS, COMPAT_STYLE_SOURCES,
        STYLE_SELECTIONS, DEFAULT_STYLE_KEY, CONFIGURED_STYLE_KEYS, GROUP_LABELS, CATEGORY_LABELS,
        TRACK_LINE_LABELS, STATION_LINE_LABELS, SPEED_BANDS, selection_style_key)

SPEED_STYLE_KEYS = tuple('高速铁路线 · ' + str(speed) + ' km/h' for speed in (350, 250, 200, 160))
LEGACY_STYLE_TYPES = (TRACK_TYPES[0], *SPEED_STYLE_KEYS, *TRACK_TYPES[1:])
STYLE_TYPES = tuple(STYLE_SELECTIONS)
PATTERNS = {'alternating': '彩白相间', 'solid': '实线', 'dashed': '短虚线', 'long_dash': '长虚线', 'dotted': '点线', 'dash_dot': '点划线'}

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
    for key in SPEED_STYLE_KEYS:
        styles[key] = dict(styles['高速铁路线'])
    for key, legacy in STYLE_SOURCES.items():
        styles[key] = dict(styles[legacy])
    styles[DEFAULT_STYLE_KEY] = {'color': '#667887', 'width': 1.8, 'pattern': 'alternating'}
    configured = ['track.conventional.main_line', 'track.conventional.branch_line',
                  'track.conventional.connecting_line', 'track.freight.main_line']
    band_colors = {'300-350': '#c52c3b', '250-300': '#df7835', '200-250': '#2b8694', '150-200': '#587db0', 'unknown': '#667887'}
    for key, (_group, category, function, band) in STYLE_SELECTIONS.items():
        if category == 'high_speed' and function in ('main_line', 'connecting_line'):
            styles[key]['color'] = band_colors[band]
            configured.append(key)
    styles[CONFIGURED_STYLE_KEYS] = configured
    for key in STYLE_SELECTIONS.keys() - set(configured):
        styles[key] = dict(styles[DEFAULT_STYLE_KEY])
    styles[ZOOM_CURVE_KEY] = recommended_zoom_curve()
    return styles


def validate_styles(value):
    if not isinstance(value, dict):
        raise ValueError("铁路样式必须完整包含所有轨道类型")
    value = dict(value)
    recommended = defaults()
    old_format = CONFIGURED_STYLE_KEYS not in value
    value.setdefault(DEFAULT_STYLE_KEY, recommended[DEFAULT_STYLE_KEY])
    configured = value.get(CONFIGURED_STYLE_KEYS, recommended[CONFIGURED_STYLE_KEYS])
    if not isinstance(configured, list):
        raise ValueError('线路样式选择必须为列表')
    configured = list(configured)
    value.setdefault(ZOOM_CURVE_KEY, recommended_zoom_curve())
    for key in SPEED_STYLE_KEYS:
        value.setdefault(key, dict(value.get('高速铁路线', recommended['高速铁路线'])))
    for key, legacy in STYLE_SOURCES.items():
        if key not in value:
            inherited = value.get(legacy, recommended[legacy])
            if key in STYLE_SELECTIONS and old_format:
                # Preserve customized old line-role styles over composite type defaults.
                group, category, function, band = STYLE_SELECTIONS[key]
                old_key = ('line.' + function if group == 'track' and function in ('branch_line','connecting_line') else
                           'role.' + function if group == 'station' and 'role.' + function in COMPAT_STYLE_SOURCES else
                           'class.' + ('unknown' if category == 'other' else category) + ('.station' if group == 'station' else ''))
                if group == 'track' and category == 'high_speed' and function == 'main_line' and band != 'unknown':
                    speed = {'300-350':350,'250-300':250,'200-250':200,'150-200':160}[band]
                    old_key = 'class.high_speed.' + str(speed)
                inherited = value.get(old_key, inherited)
                if inherited != recommended.get(old_key, recommended[legacy]):
                    if key not in configured: configured.append(key)
                    value[key] = dict(inherited)
                    continue
            value[key] = dict(recommended[key] if key in configured else value[DEFAULT_STYLE_KEY])
    if not isinstance(configured, list) or any(key not in STYLE_SELECTIONS for key in configured) or len(configured) != len(set(configured)):
        raise ValueError('线路样式选择无效或重复')
    value[CONFIGURED_STYLE_KEYS] = configured
    for key in STYLE_SELECTIONS.keys() - set(configured):
        value[key] = dict(value[DEFAULT_STYLE_KEY])
    if set(value) != set(STYLE_SOURCES) | set(LEGACY_STYLE_TYPES) | {ZOOM_CURVE_KEY, DEFAULT_STYLE_KEY, CONFIGURED_STYLE_KEYS}:
        raise ValueError("铁路样式必须完整包含所有轨道类型和缩放线宽曲线")
    for name in (*STYLE_SOURCES, *LEGACY_STYLE_TYPES, DEFAULT_STYLE_KEY):
        item = value[name]
        if (
            not isinstance(item, dict)
            or not {"color", "width"}.issubset(item)
            or set(item) - {"color", "width", "pattern"}
        ):
            raise ValueError("样式只能包含 color、width 和 pattern")
        if item.get("pattern", "alternating") not in PATTERNS:
            raise ValueError("未知线路线型")
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
        styles = validate_styles(styles)
        self._styles = styles
        self._legacy_styles = {key: dict(styles[key]) for key in (*LEGACY_STYLE_TYPES, *COMPAT_STYLE_SOURCES)}
        self.setWindowTitle("铁路样式 · 属性匹配与默认样式")
        self.resize(960, 760)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("未单独规定的类别采用默认样式。新增时选择线的种类、铁路类别和功能；高速线再选择速度范围。"))
        selector = QHBoxLayout()
        self.group_choice, self.class_choice, self.function_choice, self.band_choice = [QComboBox() for _ in range(4)]
        for key, label in GROUP_LABELS.items(): self.group_choice.addItem(label, key)
        for key, label in CATEGORY_LABELS.items(): self.class_choice.addItem(label, key)
        for key, label in SPEED_BANDS.items(): self.band_choice.addItem(label, key)
        for combo in (self.group_choice, self.class_choice, self.function_choice, self.band_choice): selector.addWidget(combo)
        self.group_choice.currentIndexChanged.connect(self._sync_choices)
        self.class_choice.currentIndexChanged.connect(self._sync_choices)
        self._sync_choices()
        add = QPushButton('新增 / 定位样式'); add.clicked.connect(self.add_style); selector.addWidget(add)
        remove = QPushButton('恢复默认样式'); remove.clicked.connect(self.remove_style); selector.addWidget(remove)
        layout.addLayout(selector)
        table = QTableWidget(0, 4)
        self.style_table = table
        table.setHorizontalHeaderLabels(["轨道类型", "颜色", "线宽 px", "轨道样式"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().hide()
        self.controls = {}
        for name in (DEFAULT_STYLE_KEY, *styles[CONFIGURED_STYLE_KEYS]):
            self._append_style(name, styles[name])
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

    def _sync_choices(self):
        functions = TRACK_LINE_LABELS if self.group_choice.currentData() == 'track' else STATION_LINE_LABELS
        current = self.function_choice.currentData()
        self.function_choice.clear()
        for key, label in functions.items(): self.function_choice.addItem(label, key)
        self.function_choice.setCurrentIndex(max(0, self.function_choice.findData(current)))
        self.band_choice.setVisible(self.class_choice.currentData() == 'high_speed')

    def _append_style(self, name, style):
        row = self.style_table.rowCount(); self.style_table.insertRow(row)
        label = QTableWidgetItem('默认线路样式' if name == DEFAULT_STYLE_KEY else STYLE_LABELS[name])
        label.setData(Qt.ItemDataRole.UserRole, name)
        label.setFlags(label.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.style_table.setItem(row, 0, label)
        color = QPushButton(style['color']); color.clicked.connect(lambda checked=False, b=color: self.choose_color(b))
        width = QDoubleSpinBox(); width.setRange(.25, 12); width.setSingleStep(.25); width.setValue(style['width'])
        pattern = QComboBox()
        for key, title in PATTERNS.items(): pattern.addItem(title, key)
        pattern.setCurrentIndex(pattern.findData(style.get('pattern', 'alternating')))
        for col, control in enumerate((color, width, pattern), 1): self.style_table.setCellWidget(row, col, control)
        self.style_table.setRowHeight(row, 40)
        self.controls[name] = color, width, pattern

    def add_style(self):
        key = selection_style_key(self.group_choice.currentData(), self.class_choice.currentData(),
                                  self.function_choice.currentData(), self.band_choice.currentData())
        if key not in self.controls:
            self._append_style(key, {field: control.text() if field == 'color' else control.value() if field == 'width' else control.currentData()
                                    for field, control in zip(('color','width','pattern'), self.controls[DEFAULT_STYLE_KEY])})
        row = next(i for i in range(self.style_table.rowCount()) if self.style_table.item(i,0).data(Qt.ItemDataRole.UserRole) == key)
        self.style_table.setCurrentCell(row, 0)
        self.style_table.scrollToItem(self.style_table.item(row, 0))

    def remove_style(self):
        row = self.style_table.currentRow()
        if row <= 0: return
        key = self.style_table.item(row,0).data(Qt.ItemDataRole.UserRole)
        self.controls.pop(key); self.style_table.removeRow(row)

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
        recommended = defaults()
        self.style_table.setRowCount(0); self.controls.clear()
        self._legacy_styles = {key: dict(recommended[key]) for key in self._legacy_styles}
        for name in (DEFAULT_STYLE_KEY, *recommended[CONFIGURED_STYLE_KEYS]): self._append_style(name, recommended[name])
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
        value.update(self._legacy_styles)
        value[CONFIGURED_STYLE_KEYS] = [key for key in self.controls if key != DEFAULT_STYLE_KEY]
        value[ZOOM_CURVE_KEY] = [
            {"zoom": zoom.value(), "scale": scale.value()}
            for zoom, scale in self.curve_controls
        ]
        return validate_styles(value)
