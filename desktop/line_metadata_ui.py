"""Line name, catalog placement and editable overview attributes."""

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
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


class LineMetadataDialog(QDialog):
    def __init__(self, kind, name, path, attributes, track_types=None, track_type="", parent=None,
                 rail_semantics=None, directory_location=None, line_name="", directory_view="lines"):
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
        general_form.addRow("显示名称", self.name)
        self.directory_view = None
        if kind == "rail":
            self.directory_view = QComboBox()
            self.directory_view.addItem("线路目录", "lines")
            self.directory_view.addItem("车站目录", "facilities")
            self.directory_view.setCurrentIndex(max(0, self.directory_view.findData(directory_view)))
            general_form.addRow("所属目录", self.directory_view)
        general_form.addRow("一级目录", self.province)
        general_form.addRow("二级目录", self.city)
        general_form.addRow("线路 / 分类目录", self.folder)
        self.track_type = None
        self.line_name = None
        if kind == "rail":
            self.line_name = QLineEdit(line_name)
            general_form.addRow("线路名称", self.line_name)
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
        tail = (self._original_tail if self.folder.text() == " / ".join(self._original_tail)
                else [part.strip() for part in self.folder.text().split(" / ") if part.strip()])
        return {
            "display_name": self.name.text().strip(),
            "folder_path": [
                value.strip()
                for value in (self.province.text(), self.city.text(), *tail)
                if value.strip()
            ],
            "track_type": self.track_type.currentText() if self.track_type else None,
            "line_name": self.line_name.text().strip() if self.line_name else None,
            "directory_view": self.directory_view.currentData() if self.directory_view else None,
            "technical_attributes": normalize_line_attributes(attributes, self.kind),
            "rail_semantics": {key: control.currentData() for key, control in self.rail_semantics.items()
                               if control.currentData() != self._original_semantics.get(key, 'unknown')},
        }
