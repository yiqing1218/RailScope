"""Editable province or planning-corridor / section catalogs; source tags untouched."""

import json
from pathlib import Path
import threading
import sqlite3
import hashlib
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QComboBox,
    QTreeWidgetItem,
    QDialog,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QDialogButtonBox,
    QHeaderView,
    QMessageBox,
    QLineEdit,
)

try:
    from .components import Switch, text_label, GrowingTree
    from .provinces import geographic_catalog, VERSION
    from .rail_categories import catalog_parents, TRACK_TYPES, CORRIDORS
except ImportError:
    from components import Switch, text_label, GrowingTree
    from provinces import geographic_catalog, VERSION
    from rail_categories import catalog_parents, TRACK_TYPES, CORRIDORS


class RailCatalog(QWidget):
    classified = Signal(dict)
    classification_failed = Signal(str)
    enabled_requested = Signal()

    def __init__(self, directory, settings, map_view, parent=None):
        super().__init__(parent)
        self.map = map_view
        self.directory = Path(directory).resolve()
        self.path = Path(settings)
        source = Path(directory) / "rail_catalog.json"
        self.catalog = (
            json.loads(source.read_text(encoding="utf-8")) if source.exists() else {}
        )
        cache = Path(directory) / "rail_catalog.provinces.json"
        if cache.exists():
            value = json.loads(cache.read_text(encoding="utf-8"))
            if value.get("version") == VERSION:
                self.catalog = value["catalog"]
        self.overrides = {}
        self.way_names = {}
        self.visible = set()
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
        self.setMinimumWidth(0)
        self.mode = QComboBox()
        self.mode.addItems(["轨道类型 → 高速通道 → 省份", "省份 → 轨道类型"])
        self.mode.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.mode.setMinimumContentsLength(10)
        self.mode.currentIndexChanged.connect(self.populate)
        layout.addWidget(self.mode)
        search = QLineEdit()
        search.setPlaceholderText("筛选省份 / 线路 / 工程")
        search.textChanged.connect(self.filter_tree)
        self.search = search
        layout.addWidget(search)
        self.tree = GrowingTree()
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(12)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(1, 62)
        self.tree.setMinimumWidth(0)
        self.tree.itemDoubleClicked.connect(self.focus_item)
        layout.addWidget(self.tree)
        self.note = text_label(
            "先按轨道用途分类；高速主线再按八纵八横整理。省份保留，未知类型/通道不猜测。",
            wrap=True,
        )
        layout.addWidget(self.note)
        self.populate()
        self.classified.connect(self.apply_classification)
        self.classification_failed.connect(self.note.setText)
        if (Path(directory) / "rail.sqlite").exists() and not all(
            r.get("classification") == VERSION for r in self.catalog.values()
        ):
            self.note.setText("正在后台整理轨道类型与省级目录…无需重新导入 PBF。")

            def classify():
                try:
                    result = geographic_catalog(directory)
                    self.classified.emit(result)
                except (ValueError, OSError, RuntimeError, sqlite3.Error) as error:
                    try:
                        self.classification_failed.emit("省界分类未完成：" + str(error))
                    except RuntimeError:
                        pass  # Window was closed; any completed cache remains reusable.

            threading.Thread(target=classify, daemon=True).start()

    def apply_classification(self, catalog):
        ways = {w for key in self.visible for w in self.catalog[key]["way_ids"]}
        self.catalog = catalog
        self.visible = {
            key
            for key, record in catalog.items()
            if ways.intersection(record["way_ids"])
        }
        if ways:
            self.send_visibility(False)
        self.populate()
        self.note.setText(
            "轨道类型来自 OSM 标签；仅高速主线列入通道。编辑菜单可核对类型/通道/省份。"
        )

    def meta(self, key):
        record = self.catalog[key]
        legacy = json.dumps(
            [record["province"], record.get("name", key)],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return {
            **record,
            **self.overrides.get(
                key,
                self.overrides.get(
                    legacy, self.overrides.get(record.get("name", key), {})
                ),
            ),
        }

    def populate(self):
        if not hasattr(self, "tree"):
            return
        expanded = {
            key
            for key, item in getattr(self, "groups", {}).items()
            if item.isExpanded()
        }
        self.tree.clear()
        groups = {}
        self.items = {}
        self.members = {}
        for name, record in sorted(self.catalog.items()):
            meta = self.meta(name)
            parents = catalog_parents(meta, self.mode.currentIndex())
            parent = self.tree.invisibleRootItem()
            key = ()
            for label in parents:
                key += (label,)
                if key not in groups:
                    groups[key] = QTreeWidgetItem(parent, [label])
                parent = groups[key]
            label = self.display_name(name)
            item = QTreeWidgetItem(parent, [label])
            item.setToolTip(
                0,
                f"{label}\n{meta['province']} · {meta.get('track_type', '未确认类型')} · {len(record['way_ids'])} 个轨道段\n依据：{meta.get('type_evidence', '待核对')}",
            )
            self.items[name] = item
            self.members[id(item)] = {name}
            ancestor = parent
            while ancestor is not self.tree.invisibleRootItem():
                self.members.setdefault(id(ancestor), set()).add(name)
                ancestor = ancestor.parent() or self.tree.invisibleRootItem()
            switch = Switch(name in self.visible)
            switch.toggled.connect(lambda on, n=name: self.toggle(n, on))
            self.tree.setItemWidget(item, 1, switch)
        for item in groups.values():
            keys = self.members[id(item)]
            item.setText(0, item.text(0) + f" · {len(keys)} 项")
            control = Switch(bool(keys & self.visible))
            control.setMixed(bool(keys & self.visible) and not keys <= self.visible)
            control.toggled.connect(lambda on, k=keys: self.toggle_group(k, on))
            self.tree.setItemWidget(item, 1, control)
        for key, item in groups.items():
            item.setExpanded(key in expanded)
        self.groups = groups
        self.filter_tree(self.search.text())

    def filter_tree(self, text):
        query = text.strip().lower()

        def visit(item, inherited=False):
            matches = inherited or query in item.text(0).lower()
            child_matches = [
                visit(item.child(i), matches) for i in range(item.childCount())
            ]
            shown = matches or any(child_matches)
            item.setHidden(not shown)
            if query and child_matches and shown:
                item.setExpanded(True)
            return shown

        for i in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(i))
        self.tree.schedule_height()

    def display_name(self, key):
        record = self.meta(key)
        aliases = sorted(
            {
                self.way_names[str(way)]
                for way in record["way_ids"]
                if str(way) in self.way_names
            }
        )
        label = record.get("display_name") or (
            aliases[0] if aliases else record.get("name", key)
        )
        # A province/type directory entry is not itself a national physical line.
        # Give these slices unique IDs too, without altering source OSM tags.
        ident = "RC-" + hashlib.sha256(key.encode()).hexdigest()[:12]
        return f"{label} · {ident}"

    def reload_names(self, path):
        path = Path(path)
        self.way_names = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        )
        self.populate()

    def toggle_group(self, keys, on):
        self.visible.update(keys) if on else self.visible.difference_update(keys)
        self.send_visibility(on)
        QTimer.singleShot(0, self.populate)

    def set_all(self, on):
        self.visible = set(self.catalog) if on else set()
        self.send_visibility(False)
        self.populate()

    def toggle(self, name, on):
        if on:
            self.visible.add(name)
        else:
            self.visible.discard(name)
        self.send_visibility(on)
        # Update ancestors without destroying the switch handling the current event.
        for key, item in self.items.items():
            control = self.tree.itemWidget(item, 1)
            control.blockSignals(True)
            control.setChecked(key in self.visible)
            control.blockSignals(False)
        for item in self.groups.values():
            control = self.tree.itemWidget(item, 1)
            keys = self.members[id(item)]
            control.blockSignals(True)
            control.setChecked(bool(keys & self.visible))
            control.setMixed(bool(keys & self.visible) and not keys <= self.visible)
            control.blockSignals(False)

    def send_visibility(self, request_enable):
        ids = [
            way
            for name in sorted(self.visible)
            for way in self.catalog[name]["way_ids"]
        ]
        self.map.call("setRailWays", ids)
        if request_enable:
            self.enabled_requested.emit()

    def focus_item(self, item, column):
        if column != 0:
            return  # The switch column only controls visibility.
        keys = self.members.get(id(item), set())
        ways = sorted({w for key in keys for w in self.catalog[key]["way_ids"]})
        database = self.directory / "rail.sqlite"
        if not ways or not database.exists():
            self.note.setText("无法定位：该目录没有可用的铁路几何数据。")
            return
        bounds = []
        try:
            # Use the complete disk index, not the currently loaded viewport.
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
                for start in range(0, len(ways), 500):
                    batch = ways[start : start + 500]
                    row = db.execute(
                        "SELECT min(b.minx),min(b.miny),max(b.maxx),max(b.maxy) "
                        "FROM features f JOIN bounds b ON f.id=b.id "
                        "WHERE f.kind='rail' AND "
                        "json_extract(f.data,'$.properties.osm_way_id') IN ("
                        + ",".join("?" for _ in batch)
                        + ")",
                        batch,
                    ).fetchone()
                    if row and row[0] is not None:
                        bounds.append(row)
        except sqlite3.Error as error:
            self.note.setText("无法定位：" + str(error))
            return
        if not bounds:
            self.note.setText("无法定位：索引中没有找到该线路的轨道段。")
            return
        self.map.call(
            "fit",
            [
                [min(b[0] for b in bounds), min(b[1] for b in bounds)],
                [max(b[2] for b in bounds), max(b[3] for b in bounds)],
            ],
            item.text(0),
        )
        self.note.setText("已定位：" + item.text(0) + "；显示开关保持不变。")

    def organize(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("国铁分类整理 · 轨道类型 / 省份 / 高速通道")
        dialog.resize(1000, 650)
        layout = QVBoxLayout(dialog)
        layout.addWidget(
            QLabel(
                "修改类型、省份、通道与分段，保存后重建目录；不修改 OSM 原始属性。仅高速主线参加八纵八横。"
            )
        )
        table = QTableWidget(len(self.catalog), 6)
        table.setHorizontalHeaderLabels(
            [
                "原始线路 / 工程名称",
                "省份",
                "高速规划通道",
                "通道分段",
                "轨道类型",
                "目录显示名称",
            ]
        )
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(table)
        names = sorted(self.catalog)
        for row, name in enumerate(names):
            meta = self.meta(name)
            for col, value in enumerate(
                (
                    self.catalog[name].get("name", name),
                    meta["province"],
                    meta["corridor"],
                    meta["section"],
                    meta.get("track_type", "未确认类型"),
                    meta.get("display_name", self.catalog[name].get("name", name)),
                )
            ):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row, col, item)
            kind = QComboBox()
            kind.addItems(TRACK_TYPES)
            kind.setCurrentText(meta.get("track_type", "未确认类型"))
            table.setCellWidget(row, 4, kind)
            corridor = QComboBox()
            corridor.setEditable(True)
            corridor.addItems(["高速通道待核对", "不适用", *CORRIDORS])
            corridor.setCurrentText(meta["corridor"])
            table.setCellWidget(row, 2, corridor)
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
                **{
                    key: (
                        table.cellWidget(row, 2).currentText()
                        if col == 2
                        else table.item(row, col).text()
                    ).strip()
                    or (
                        "未分类省份"
                        if key == "province"
                        else "未分配通道"
                        if key == "corridor"
                        else "未分配分段"
                    )
                    for col, key in enumerate(("province", "corridor", "section"), 1)
                },
                "track_type": table.cellWidget(row, 4).currentText(),
                "display_name": table.item(row, 5).text().strip()
                or self.catalog[name].get("name", name),
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
