"""One palette shared by native panels and map navigation; user settings only."""
import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                              QFormLayout, QLabel, QSlider)

DEFAULT = {"palette": "mist", "glass": True, "opacity": 86, "density": "comfortable"}
PALETTES = {
    "mist": {"name": "雾青玻璃", "text": "#173b43", "muted": "#48656d", "accent": "#087e75",
             "surface": "#f2faf8", "base": "#e8f1ef", "edge": "#bad0ce", "hover": "#deeeea", "selected": "#cde5df"},
    "ocean": {"name": "冰蓝玻璃", "text": "#193b53", "muted": "#48647b", "accent": "#146cb5",
              "surface": "#f1f7fd", "base": "#e6eef7", "edge": "#b9cddd", "hover": "#dceafa", "selected": "#cedff4"},
    "graphite": {"name": "深色石墨", "text": "#e5f3f2", "muted": "#adc8cd", "accent": "#62d5c5",
                 "surface": "#192c35", "base": "#12212a", "edge": "#486570", "hover": "#29444d", "selected": "#32565c"},
}


def normalize(value):
    if not isinstance(value, dict):
        return DEFAULT.copy()
    result = DEFAULT.copy()
    if value.get("palette") in PALETTES:
        result["palette"] = value["palette"]
    if value.get("density") in ("comfortable", "compact"):
        result["density"] = value["density"]
    if type(value.get("glass")) is bool:
        result["glass"] = value["glass"]
    if type(value.get("opacity")) is int and 70 <= value["opacity"] <= 98:
        result["opacity"] = value["opacity"]
    return result


def load(path):
    try:
        return normalize(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return DEFAULT.copy()


def save(path, value):
    value = normalize(value)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return value


def web_theme(value):
    value = normalize(value)
    return {**PALETTES[value["palette"]], **value}


def native_palette(value):
    colors = web_theme(value)
    palette = QPalette()
    for role, key in ((QPalette.ColorRole.Window, "base"), (QPalette.ColorRole.Base, "surface"),
                      (QPalette.ColorRole.AlternateBase, "hover"), (QPalette.ColorRole.WindowText, "text"),
                      (QPalette.ColorRole.Text, "text"), (QPalette.ColorRole.Button, "surface"),
                      (QPalette.ColorRole.PlaceholderText, "muted"),
                      (QPalette.ColorRole.ButtonText, "text"), (QPalette.ColorRole.Highlight, "selected"),
                      (QPalette.ColorRole.HighlightedText, "text"), (QPalette.ColorRole.ToolTipBase, "surface"),
                      (QPalette.ColorRole.ToolTipText, "text")):
        palette.setColor(role, QColor(colors[key]))
    return palette


def stylesheet(value):
    t = web_theme(value)
    size, padding = (12, 5) if t["density"] == "compact" else (13, 7)
    # All nested containers are transparent; only named surfaces provide a tint.
    return f"""QWidget {{ color:{t['text']}; background:transparent; font-family:'Microsoft YaHei UI','Aptos'; font-size:{size}px; }}
QMainWindow, #workspace, QDialog {{ background:{t['base']}; }}
QMenuBar {{ background:{t['surface']}; padding:3px 10px; border-bottom:1px solid {t['edge']}; }}
QMenuBar::item {{ padding:6px 12px; border-radius:6px; }}
QMenuBar::item:selected, QMenu::item:selected {{ background:{t['selected']}; }}
QMenu {{ background:{t['surface']}; border:1px solid {t['edge']}; padding:6px; }}
QMenu::item {{ padding:8px 28px 8px 12px; }}
#header {{ background:{t['surface']}; border-bottom:1px solid {t['edge']}; }}
#brand {{ font-size:22px; font-weight:700; }}
#subheading, #muted {{ color:{t['muted']}; font-size:{size-1}px; }}
#panelTitle {{ font-size:17px; font-weight:700; }}
#selectedTitle {{ font-size:18px; font-weight:700; }}
#dialogTitle {{ font-size:23px; font-weight:700; }}
#sectionLabel {{ color:{t['muted']}; font-size:11px; font-weight:600; }}
#badge {{ background:{t['hover']}; color:{t['accent']}; padding:4px 9px; border-radius:7px; font-size:11px; }}
#card, #demoCard {{ background:{t['hover']}; border:1px solid {t['edge']}; border-radius:10px; }}
#panel, #rail {{ background:transparent; border:none; }}
#fold {{ background:transparent; border:none; border-bottom:1px solid {t['edge']}; border-radius:0; }}
#metricValue {{ font-size:24px; font-weight:700; color:{t['accent']}; }}
QPushButton, QToolButton {{ background:{t['surface']}; border:1px solid {t['edge']}; border-radius:8px; padding:{padding}px 10px; }}
QPushButton:hover, QToolButton:hover {{ background:{t['hover']}; border-color:{t['accent']}; }}
QPushButton:checked, QToolButton:checked {{ background:{t['selected']}; color:{t['accent']}; }}
QPushButton:focus, QToolButton:focus {{ border-color:{t['accent']}; }}
QPushButton:disabled, QToolButton:disabled {{ color:{t['muted']}; background:{t['base']}; }}
QPushButton#primary {{ background:{t['accent']}; color:{t['base'] if t['palette']=='graphite' else '#ffffff'}; font-weight:600; }}
QToolButton#railButton {{ border:none; background:transparent; padding:5px 2px; font-size:12px; }}
QToolButton#railButton:checked {{ background:{t['selected']}; color:{t['accent']}; }}
QToolButton#foldHeader {{ border:none; background:transparent; text-align:left; padding:10px 7px; font-weight:600; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit {{ background:{t['surface']}; border:1px solid {t['edge']}; border-radius:7px; padding:{padding}px 8px; selection-background-color:{t['selected']}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color:{t['accent']}; }}
QComboBox QLineEdit {{ border:none; background:transparent; padding:0; }}
QComboBox::drop-down {{ border:none; width:22px; }}
QComboBox QAbstractItemView {{ background:{t['surface']}; border:1px solid {t['edge']}; selection-background-color:{t['selected']}; selection-color:{t['text']}; }}
QAbstractScrollArea, QTreeView, QTreeWidget, QListView {{ background:transparent; border:none; outline:0; alternate-background-color:transparent; }}
QAbstractScrollArea > QWidget, QScrollArea > QWidget, QStackedWidget, QTabWidget::pane {{ background:transparent; border:none; }}
QTreeView::item, QTreeWidget::item {{ padding:{padding-2}px 2px; min-height:23px; border-radius:5px; }}
QTreeView::item:hover, QTreeWidget::item:hover {{ background:{t['hover']}; }}
QTreeView::item:selected, QTreeWidget::item:selected {{ background:{t['selected']}; color:{t['text']}; }}
QTabWidget::pane {{ border:none; }}
QTabBar::tab {{ background:transparent; color:{t['muted']}; border:none; border-bottom:2px solid transparent; padding:8px 10px; }}
QTabBar::tab:selected {{ color:{t['accent']}; border-bottom-color:{t['accent']}; }}
QTableView, QTableWidget {{ background:{t['surface']}; alternate-background-color:{t['hover']}; gridline-color:{t['edge']}; border:1px solid {t['edge']}; border-radius:7px; }}
QTableView::item:selected, QTableWidget::item:selected {{ background:{t['selected']}; color:{t['text']}; }}
QHeaderView::section {{ background:{t['hover']}; border:none; padding:7px; color:{t['muted']}; }}
QPlainTextEdit, QTextEdit {{ background:{t['surface']}; border:1px solid {t['edge']}; border-radius:7px; padding:6px; }}
QScrollBar:vertical {{ width:7px; background:transparent; margin:2px; }}
QScrollBar:horizontal {{ height:7px; background:transparent; margin:2px; }}
QScrollBar::handle {{ background:{t['edge']}; border-radius:3px; min-height:24px; min-width:24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width:0; height:0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background:transparent; }}
QStatusBar {{ background:{t['surface']}; border-top:1px solid {t['edge']}; color:{t['muted']}; }}
QSplitter::handle {{ background:transparent; width:6px; height:6px; }}
QGroupBox {{ background:transparent; border:1px solid {t['edge']}; border-radius:9px; margin-top:12px; padding:12px 8px 8px; }}
QGroupBox::title {{ color:{t['muted']}; subcontrol-origin:margin; left:10px; padding:0 4px; }}
QSlider::groove:horizontal {{ height:4px; border-radius:2px; background:{t['edge']}; }}
QSlider::handle:horizontal {{ width:16px; margin:-6px 0; border-radius:8px; background:{t['accent']}; }}
QCheckBox::indicator {{ width:17px; height:17px; border:1px solid {t['muted']}; border-radius:4px; background:{t['surface']}; }}
QCheckBox::indicator:checked {{ background:{t['accent']}; }}
QToolTip {{ background:{t['surface']}; color:{t['text']}; border:1px solid {t['edge']}; padding:7px; }}
QProgressBar {{ background:{t['hover']}; border:none; border-radius:4px; }}
QProgressBar::chunk {{ background:{t['accent']}; border-radius:4px; }}
"""


class AppearanceDialog(QDialog):
    def __init__(self, value, preview, parent=None):
        super().__init__(parent)
        self.setWindowTitle("界面外观")
        self.setMinimumWidth(380)
        self.preview = preview
        form = QFormLayout(self)
        form.setSpacing(14)
        caption = QLabel("配色同步应用于侧栏与地图工具。调整可实时预览，取消恢复原设置。")
        caption.setWordWrap(True)
        form.addRow(caption)
        self.palette_choice = QComboBox()
        for key, palette in PALETTES.items():
            self.palette_choice.addItem(palette["name"], key)
        self.palette_choice.setCurrentIndex(self.palette_choice.findData(value["palette"]))
        self.glass = QCheckBox("透出地图并柔化背景")
        self.glass.setChecked(value["glass"])
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(70, 98)
        self.opacity.setValue(value["opacity"])
        self.opacity_label = QLabel()
        self.density = QComboBox()
        self.density.addItem("舒适 · 更宽松的间距", "comfortable")
        self.density.addItem("紧凑 · 显示更多目录行", "compact")
        self.density.setCurrentIndex(self.density.findData(value["density"]))
        form.addRow("配色", self.palette_choice)
        form.addRow("玻璃效果", self.glass)
        form.addRow("面板不透明度", self.opacity)
        form.addRow("", self.opacity_label)
        form.addRow("界面密度", self.density)
        reset = QDialogButtonBox(QDialogButtonBox.StandardButton.RestoreDefaults)
        reset.clicked.connect(self.reset)
        form.addRow(reset)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存并应用")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        for signal in (self.palette_choice.currentIndexChanged, self.glass.toggled,
                       self.opacity.valueChanged, self.density.currentIndexChanged):
            signal.connect(self.changed)
        self.changed()

    def value(self):
        return {"palette": self.palette_choice.currentData(), "glass": self.glass.isChecked(),
                "opacity": self.opacity.value(), "density": self.density.currentData()}

    def changed(self, *_):
        self.opacity.setEnabled(self.glass.isChecked())
        self.opacity_label.setText(f"{self.opacity.value()}% · 数值越小，透出的地图越明显")
        self.preview(self.value())

    def reset(self, *_):
        self.palette_choice.setCurrentIndex(self.palette_choice.findData(DEFAULT["palette"]))
        self.glass.setChecked(DEFAULT["glass"])
        self.opacity.setValue(DEFAULT["opacity"])
        self.density.setCurrentIndex(0)
