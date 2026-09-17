"""Editable province or planning-corridor / section catalogs; source tags untouched."""

import json
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QComboBox,
    QTreeWidget,
    QTreeWidgetItem,
    QPushButton,
    QDialog,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QDialogButtonBox,
    QHeaderView,
    QMessageBox,
)

try:
    from .components import Switch
except ImportError:
    from components import Switch


class RailCatalog(QWidget):
    def __init__(self, directory, settings, map_view, parent=None):
        super().__init__(parent)
        self.map = map_view
        self.path = Path(settings)
        source = Path(directory) / "rail_catalog.json"
        self.catalog = (
            json.loads(source.read_text(encoding="utf-8")) if source.exists() else {}
        )
        self.overrides = {}
        self.visible = {key for key in self.catalog}
        if self.path.exists():
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("分类设置必须是对象")
                self.overrides = {
                    k: v
                    for k, v in value.items()
                    if isinstance(v, dict)
                    and all(
                        isinstance(v.get(f), str) and v[f].strip()
                        for f in ("province", "corridor", "section")
                    )
                }
            except (ValueError, OSError) as error:
                QMessageBox.warning(self, "国铁分类设置未载入", str(error))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.mode = QComboBox()
        self.mode.addItems(["按省份", "按规划通道 → 分段"])
        self.mode.currentIndexChanged.connect(self.populate)
        layout.addWidget(self.mode)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumHeight(220)
        self.tree.setMaximumHeight(340)
        self.tree.setColumnWidth(1, 60)
        layout.addWidget(self.tree)
        edit = QPushButton("整理国铁通道 / 分段…")
        edit.clicked.connect(self.organize)
        layout.addWidget(edit)
        layout.addWidget(
            QLabel("八纵八横为官方规划骨架；未确认的线路保持未分类，不按站名推测归属。")
        )
        self.populate()

    def populate(self):
        if not hasattr(self, "tree"):
            return
        self.tree.clear()
        groups = {}
        for name, record in sorted(self.catalog.items()):
            meta = {**record, **self.overrides.get(name, {})}
            parents = (
                (meta["province"],)
                if self.mode.currentIndex() == 0
                else (meta["corridor"], meta["section"])
            )
            parent = self.tree.invisibleRootItem()
            key = ()
            for label in parents:
                key += (label,)
                if key not in groups:
                    groups[key] = QTreeWidgetItem(parent, [label])
                parent = groups[key]
            item = QTreeWidgetItem(parent, [name])
            item.setToolTip(0, name)
            switch = Switch(name in self.visible)
            switch.toggled.connect(lambda on, n=name: self.toggle(n, on))
            self.tree.setItemWidget(item, 1, switch)

    def toggle(self, name, on):
        if on:
            self.visible.add(name)
        else:
            self.visible.discard(name)
        ids = [way for name in self.visible for way in self.catalog[name]["way_ids"]]
        self.map.call("setRailWays", ids)

    def organize(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("国铁分类整理 · 省份 / 规划通道 / 分段")
        dialog.resize(1000, 650)
        layout = QVBoxLayout(dialog)
        layout.addWidget(
            QLabel(
                "双击修改后三列，保存后立即重建目录；不修改原始 OSM 属性。跨省线路暂未归类时留在未分类。"
            )
        )
        table = QTableWidget(len(self.catalog), 4)
        table.setHorizontalHeaderLabels(
            ["原始线路 / 工程名称", "省份", "规划通道", "通道分段"]
        )
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(table)
        names = sorted(self.catalog)
        for row, name in enumerate(names):
            meta = {**self.catalog[name], **self.overrides.get(name, {})}
            for col, value in enumerate(
                (name, meta["province"], meta["corridor"], meta["section"])
            ):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row, col, item)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        layout.addWidget(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        overrides = {
            name: {
                key: table.item(row, col).text().strip()
                or (
                    "未分类省份"
                    if key == "province"
                    else "未分配通道"
                    if key == "corridor"
                    else "未分配分段"
                )
                for col, key in enumerate(("province", "corridor", "section"), 1)
            }
            for row, name in enumerate(names)
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(overrides, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp.replace(self.path)
            self.overrides = overrides
            self.populate()
        except OSError as error:
            QMessageBox.warning(self, "无法保存", str(error))
