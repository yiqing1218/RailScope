"""Independent, persisted Chinese type choices for each interface region."""
import json
from pathlib import Path
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFontComboBox,
                              QGridLayout, QLabel, QSpinBox)
from PySide6.QtGui import QFont

LABELS = {"ui": "默认界面", "menu": "菜单栏与菜单", "brand": "顶部标题", "panel": "侧栏标题",
          "directory": "目录与列表", "notes": "说明与提示", "table": "表格", "navigation": "地图导航工具",
          "map": "地图中文标签"}
SIZES = {"ui": 13, "menu": 13, "brand": 22, "panel": 17, "directory": 13,
         "notes": 12, "table": 12, "navigation": 12, "map": 12}
DEFAULT = {key: {"family": "Microsoft YaHei UI", "size": size} for key, size in SIZES.items()}


def normalize(value):
    result = {}
    for key, default in DEFAULT.items():
        entry = value.get(key, {}) if isinstance(value, dict) else {}
        if not isinstance(entry, dict):
            entry = {}
        family, size = entry.get("family"), entry.get("size")
        # Font family names are displayed in QSS/CSS: reject control syntax.
        if not isinstance(family, str) or not family.strip() or len(family) > 100 or any(c in family for c in "'\";{}\n\r"):
            family = default["family"]
        if type(size) is not int or not 9 <= size <= 32:
            size = default["size"]
        result[key] = {"family": family, "size": size}
    return result


def load(path):
    try:
        return normalize(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return normalize(None)


def save(path, value):
    value = normalize(value)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return value


def stylesheet(value):
    selectors = {"ui": "QWidget", "menu": "QMenuBar, QMenu", "brand": "#brand", "panel": "#panelTitle, #selectedTitle",
                 "directory": "QTreeView, QTreeWidget, QListView", "notes": "#muted, #subheading, #sectionLabel",
                 "table": "QTableView, QTableWidget, QHeaderView"}
    fonts = normalize(value)
    return "\n".join(f"{selector} {{ font-family:'{fonts[key]['family']}'; font-size:{fonts[key]['size']}px; }}"
                     for key, selector in selectors.items())


class FontDialog(QDialog):
    def __init__(self, value, preview, parent=None):
        super().__init__(parent)
        self.setWindowTitle("中文字体 · 分区设置")
        self.setMinimumWidth(560)
        self.controls = {}
        self.preview = preview
        layout = QGridLayout(self)
        layout.setSpacing(12)
        note = QLabel("各处独立选择本机字体与字号（像素）。地图中文标签在保存后重新加载字体；缺字由系统回退。")
        note.setWordWrap(True)
        layout.addWidget(note, 0, 0, 1, 3)
        for row, (key, label) in enumerate(LABELS.items(), 1):
            family = QFontComboBox()
            family.setCurrentFont(QFont(value[key]["family"]))
            size = QSpinBox()
            size.setRange(9, 32)
            size.setSuffix(" px")
            size.setValue(value[key]["size"])
            layout.addWidget(QLabel(label), row, 0)
            layout.addWidget(family, row, 1)
            layout.addWidget(size, row, 2)
            self.controls[key] = (family, size)
        sample = QLabel("中文预览：全国铁路 · 上海交通大学 · 线路目录")
        layout.addWidget(sample, len(LABELS)+1, 0, 1, 3)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.RestoreDefaults)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存并应用")
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(self.reset)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons, len(LABELS)+2, 0, 1, 3)
        for family, size in self.controls.values():
            family.currentFontChanged.connect(self.changed)
            size.valueChanged.connect(self.changed)

    def value(self):
        return {key: {"family": family.currentFont().family(), "size": size.value()}
                for key, (family, size) in self.controls.items()}

    def changed(self, *_):
        self.preview(self.value())

    def reset(self):
        for key, (family, size) in self.controls.items():
            family.setCurrentFont(QFont(DEFAULT[key]["family"]))
            size.setValue(DEFAULT[key]["size"])
