"""Line name, catalog placement and editable overview attributes."""

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

try:
    from .line_metadata import METRO_LINE_FIELDS, RAIL_LINE_FIELDS, normalize_line_attributes
    from .rail_style_resolver import CLASS_LABELS, LINE_ROLE_LABELS, ROLE_LABELS
except ImportError:
    from line_metadata import METRO_LINE_FIELDS, RAIL_LINE_FIELDS, normalize_line_attributes
    from rail_style_resolver import CLASS_LABELS, LINE_ROLE_LABELS, ROLE_LABELS


class CascadingPathEditor(QWidget):
    """A single-row cascading directory picker with an inline custom level."""

    def __init__(self, paths, current, parent=None):
        super().__init__(parent)
        self._paths = sorted({tuple(part for part in path if part) for path in paths if path})
        self._current = list(current) if current else []
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self.combos = []
        self._build_from(0)

    def _prefix(self, up_to):
        result = []
        for combo in self.combos[:up_to]:
            data = combo.currentData()
            if data in (None, "__custom__"):
                break
            result.append(data)
        return tuple(result)

    def _options(self, level):
        prefix = self._prefix(level)
        options = set()
        for path in self._paths:
            if len(path) > level and tuple(path[:level]) == prefix:
                options.add(path[level])
        return sorted(options)

    def _build_from(self, level):
        while len(self.combos) > level:
            combo = self.combos.pop()
            self._layout.removeWidget(combo)
            combo.deleteLater()
        options = self._options(level)
        current = self._current[level] if level < len(self._current) else None
        if not options and not current:
            return
        combo = QComboBox()
        combo.addItem("自定义…", "__custom__")
        for option in options:
            combo.addItem(option, option)
        if current is not None and combo.findData(current) < 0:
            combo.addItem(current, current)
        index = combo.findData(current) if current is not None else -1
        combo.setCurrentIndex(max(0, index))
        combo.currentIndexChanged.connect(lambda _index, l=level: self._on_level_changed(l))
        self._layout.addWidget(combo, 1)
        self.combos.append(combo)
        prefix = self._prefix(level + 1)
        has_children = any(
            len(path) > level + 1 and tuple(path[: level + 1]) == prefix
            for path in self._paths
        )
        if has_children or level + 1 < len(self._current):
            self._build_from(level + 1)

    def _on_level_changed(self, level):
        combo = self.combos[level]
        if combo.currentData() == "__custom__":
            value, ok = QInputDialog.getText(self, "新建目录", f"第 {level + 1} 级目录名称")
            if ok and value.strip():
                value = value.strip()
                if combo.findData(value) < 0:
                    combo.addItem(value, value)
                combo.setCurrentIndex(combo.findData(value))
            else:
                combo.blockSignals(True)
                combo.setCurrentIndex(0)
                combo.blockSignals(False)
        self._build_from(level + 1)

    def path(self):
        result = []
        for combo in self.combos:
            data = combo.currentData()
            if data in (None, "__custom__"):
                break
            result.append(data)
        return result


class LineMetadataDialog(QDialog):
    def __init__(self, kind, name, path, attributes, track_types=None, track_type="", parent=None,
                 rail_semantics=None, directory_location=None, line_name="", directory_view="lines",
                 path_options=None):
        super().__init__(parent)
        self.kind = kind
        self.setWindowTitle(("地铁" if kind == "metro" else "国铁") + "线路信息")
        self.resize(720, 700)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        self.tabs = tabs
        general = QWidget()
        general_form = QFormLayout(general)
        self.name = QLineEdit(name)
        self.province = QLineEdit(path[0] if path else "")
        self.city = QLineEdit(path[1] if len(path) > 1 else "")
        self._original_tail = list(path[2:])
        self.folder = QLineEdit(" / ".join(self._original_tail))
        general_form.addRow("名称", self.name)
        self.directory_view = None
        self.path_editor = None
        if kind == "rail":
            self.directory_view = QComboBox()
            self.directory_view.addItem("线路目录", "lines")
            self.directory_view.addItem("车站目录", "facilities")
            self.directory_view.setCurrentIndex(max(0, self.directory_view.findData(directory_view)))
            general_form.addRow("所属目录", self.directory_view)
        if kind == "rail" and path_options:
            self.path_editor = CascadingPathEditor(path_options, path)
            general_form.addRow("目录", self.path_editor)
        else:
            general_form.addRow("一级目录", self.province)
            general_form.addRow("二级目录", self.city)
            general_form.addRow("线路 / 分类目录", self.folder)
        self.track_type = None
        self.line_name = None
        if kind == "rail":
            self.track_type = QComboBox()
            self.track_type.addItems(track_types or ())
            if track_type and self.track_type.findText(track_type) < 0:
                self.track_type.addItem(track_type)
            self.track_type.setCurrentText(track_type)
            general_form.addRow("轨道类型", self.track_type)
        self.rail_semantics = {}
        self._original_semantics = dict(rail_semantics or {})
        if kind == 'rail':
            for key, title, labels in (('railway_class','铁路类别',CLASS_LABELS),
                                       ('line_role','线路网络角色',LINE_ROLE_LABELS),
                                       ('track_role','物理轨道用途',ROLE_LABELS)):
                control = QComboBox()
                for ident, label in labels.items():
                    control.addItem(label, ident)
                control.setCurrentIndex(max(0, control.findData(self._original_semantics.get(key, 'unknown'))))
                general_form.addRow(title, control)
                self.rail_semantics[key] = control
        tabs.addTab(general, "名称与目录")

        detail_host = QWidget()
        detail_form = QFormLayout(detail_host)
        self.attribute_controls = {}
        fields = METRO_LINE_FIELDS if kind == "metro" else RAIL_LINE_FIELDS
        for key, label, hint in fields:
            control = QPlainTextEdit() if key == "remarks" else QLineEdit()
            if isinstance(control, QPlainTextEdit):
                control.setMaximumHeight(90)
                control.setPlainText(attributes.get(key, ""))
                control.setPlaceholderText(hint)
            else:
                control.setText(attributes.get(key, ""))
                control.setPlaceholderText(hint)
            detail_form.addRow(label, control)
            self.attribute_controls[key] = control
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(detail_host)
        tabs.addTab(scroll, "线路概览")
        layout.addWidget(tabs)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        self.relationship_validator = None
        self.relationship_updates = {}
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self):
        try:
            if self.relationship_validator:
                self.relationship_updates = self.relationship_validator()
            self.accept()
        except ValueError as error:
            QMessageBox.warning(self, '关联关系未保存', str(error))

    def values(self):
        attributes = {}
        for key, control in self.attribute_controls.items():
            attributes[key] = (
                control.toPlainText() if isinstance(control, QPlainTextEdit) else control.text()
            )
        if self.path_editor is not None:
            folder_path = self.path_editor.path()
        else:
            tail = (self._original_tail if self.folder.text() == " / ".join(self._original_tail)
                    else [part.strip() for part in self.folder.text().split(" / ") if part.strip()])
            folder_path = [
                value.strip()
                for value in (self.province.text(), self.city.text(), *tail)
                if value.strip()
            ]
        return {
            "display_name": self.name.text().strip(),
            "folder_path": folder_path,
            "track_type": self.track_type.currentText() if self.track_type else None,
            "line_name": self.name.text().strip(),
            "directory_view": self.directory_view.currentData() if self.directory_view else None,
            "technical_attributes": normalize_line_attributes(attributes, self.kind),
            "rail_semantics": {key: control.currentData() for key, control in self.rail_semantics.items()
                               if control.currentData() != self._original_semantics.get(key, 'unknown')},
        }
