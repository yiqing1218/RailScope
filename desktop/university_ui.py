"""Province/city campus directory; one checkbox controls outline and POI."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFileDialog, QInputDialog, QLineEdit, QMenu, QMessageBox,
                              QPushButton, QTreeView, QVBoxLayout, QWidget)
from components import Fold, directory_checkbox_style, text_label
from lazy_directory import SqliteDirectoryModel
import university_store


class CampusDirectoryModel(SqliteDirectoryModel):
    def __init__(self, path, overrides):
        super().__init__(path)
        self.overrides = overrides

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.isValid() and role == Qt.ItemDataRole.DisplayRole:
            node = self._node(index)
            name = self.overrides.get(node.object_id, {}).get("name")
            if name:
                return name
        return super().data(index, role)

    def _search_seed(self):
        matched = [ident for ident, edit in self.overrides.items() if self.search.casefold() in edit.get("name", "").casefold()]
        return (f"SELECT id,parent_id FROM {self.table} WHERE label LIKE ? ESCAPE '\\' OR object_id IN (SELECT value FROM json_each(?))",
                (self._literal_search(), json.dumps(matched)))


class UniversityCatalog(QWidget):
    profile = university_store.UNIVERSITY
    store = university_store
    selection_command = "setUniversitySelection"
    reload_command = "reloadUniversityViewport"

    visibilityChanged = Signal(bool, bool)
    overridesChanged = Signal(dict)

    def __init__(self, root, map_view, parent=None):
        super().__init__(parent)
        self.root, self.map_view = Path(root), map_view
        self.visible = set()
        self.override_path = self.root / "data/user_settings" / (self.profile["directory"]+".json")
        try:
            self.overrides = json.loads(self.override_path.read_text(encoding="utf-8"))
            if not isinstance(self.overrides, dict):
                self.overrides = {}
            self.overrides = {key: edit for key, edit in self.overrides.items() if isinstance(key, str) and isinstance(edit, dict) and isinstance(edit.get('name'), str)}
        except (OSError, ValueError):
            self.overrides = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 6)
        layout.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索"+self.profile["label"])
        layout.addWidget(self.search)
        self.summary = text_label("", wrap=True)
        layout.addWidget(self.summary)
        self.tree = QTreeView()
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setMinimumHeight(220)
        self.tree.setStyleSheet(directory_checkbox_style())
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.context_menu)
        self.tree.doubleClicked.connect(self.locate)
        self.tree.setSelectionMode(QTreeView.SelectionMode.ExtendedSelection)
        layout.addWidget(self.tree)
        layout.addWidget(text_label(f"省 → 市 → {self.profile['noun']}。一个目录项同时控制真实轮廓与 POI；无来源边界时只显示点位。", wrap=True))
        update = QPushButton("从本地 OSM 更新"+self.profile["label"]+"图层…")
        update.clicked.connect(self.import_data)
        layout.addWidget(update)
        self.search.textChanged.connect(self.search_changed)
        self.reload()

    def reload(self):
        if hasattr(self, "model"):
            self.model._sessions.close_current_thread()
            self.model.deleteLater()
        self.path = self.store.ensure_index(self.store.database_path(self.root))
        self.model = CampusDirectoryModel(self.path, self.overrides)
        self.model.toggled.connect(self.toggled)
        self.tree.setModel(self.model)
        self.model.fetchMore()
        with closing(sqlite3.connect(self.path)) as db:
            self.all_ids = {row[0] for row in db.execute(f"SELECT id FROM {self.profile['table']}")}
            outlines = int(dict(db.execute("SELECT key,value FROM metadata")).get("outlines", 0))
        self.visible.intersection_update(self.all_ids)
        self.summary.setText(f"{len(self.all_ids):,} 个{self.profile['noun']} · {outlines:,} 个来源轮廓" if self.all_ids else "尚未导入"+self.profile["label"]+"数据，请使用下方更新按钮。")
        # Never silently reassign a human edit to a new source object.
        missing = sorted(set(self.overrides) - self.all_ids)
        conflict_path = self.override_path.with_name(self.profile["key"]+"_conflicts.json")
        if missing or conflict_path.exists():
            conflicts = [{self.profile["id_key"]: key, "override": self.overrides[key],
                          "reason": "missing_"+self.profile["key"]+"_in_active_snapshot", "verification_status": "unresolved"}
                         for key in missing]
            try:
                conflict_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = conflict_path.with_suffix(".json.tmp")
                temporary.write_text(json.dumps(conflicts, ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(conflict_path)
            except OSError:
                self.summary.setText(self.summary.text()+" · 改名冲突记录未能保存")
            if missing:
                self.summary.setText(self.summary.text()+f" · {len(missing)} 项改名待重新关联")
        self.model.set_visible(self.visible)
        self.publish()

    def search_changed(self, text):
        self.model.set_search(text)
        self.model.fetchMore()

    def set_all(self, on):
        self.visible = self.all_ids.copy() if on else set()
        self.model.set_visible(self.visible)
        self.publish()

    def toggled(self, key, on):
        values = self.model.ids_below(key)
        self.visible.update(values) if on else self.visible.difference_update(values)
        self.model.set_visible(self.visible)
        self.publish()

    def publish(self):
        self.map_view.call(self.selection_command, sorted(self.visible))
        self.visibilityChanged.emit(bool(self.visible), bool(self.visible) and self.visible != self.all_ids)
        self.overridesChanged.emit(self.overrides)

    def select_campus(self, ident):
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, Fold):
                parent.button.setChecked(True)
                break
            parent = parent.parentWidget()
        index = self.model.index_for_key(self.profile["node_prefix"]+ident)
        if not index.isValid():
            return
        parent = index.parent()
        while parent.isValid():
            self.tree.expand(parent)
            parent = parent.parent()
        self.tree.setCurrentIndex(index)
        self.tree.scrollTo(index)

    def locate(self, index):
        ids = self.model.ids_below(self.model._node(index).key)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TEMP TABLE selected(id TEXT PRIMARY KEY)")
            db.executemany("INSERT INTO selected VALUES(?)", ((key,) for key in ids))
            data = [json.loads(row[0]) for row in db.execute(f"SELECT data FROM {self.profile['table']} WHERE id IN (SELECT id FROM selected)")]
        if not data:
            return
        bounds = [row["bounds"] for row in data]
        self.map_view.call("fit", [[min(b[0] for b in bounds), min(b[1] for b in bounds)],
                                    [max(b[2] for b in bounds), max(b[3] for b in bounds)]], self.model.data(index))
        self.visible.update(ids)
        self.model.set_visible(self.visible)
        self.publish()

    def rename(self, ident):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(f"SELECT name FROM {self.profile['table']} WHERE id=?", (ident,)).fetchone()
        if not row:
            return
        current = self.overrides.get(ident, {}).get("name", row[0])
        name, accepted = QInputDialog.getText(self, self.profile["noun"]+"显示名称", self.profile["label"]+"名称", text=current)
        if not accepted or not name.strip():
            return
        value = {**self.overrides, ident: {"name": name.strip(), "source": "manual", "verification_status": "user_named"}}
        try:
            self.override_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.override_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.override_path)
        except OSError as error:
            QMessageBox.warning(self, "名称未保存", str(error))
            return
        self.overrides = value
        self.model.overrides = value
        index = self.model.index_for_key(self.profile["node_prefix"]+ident)
        self.model.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole])
        self.overridesChanged.emit(value)
        self.map_view.call(self.reload_command)

    def context_menu(self, point):
        index = self.tree.indexAt(point)
        if not index.isValid():
            return
        node = self.model._node(index)
        menu = QMenu(self)
        menu.addAction("定位"+self.profile["noun"]+" / 目录范围", lambda: self.locate(index))
        menu.addAction("显示轮廓与 POI", lambda: self.toggled(node.key, True))
        menu.addAction("隐藏轮廓与 POI", lambda: self.toggled(node.key, False))
        if node.kind == "object":
            menu.addAction("修改显示名称…", lambda: self.rename(node.object_id))
        menu.exec(self.tree.viewport().mapToGlobal(point))

    def import_data(self):
        from background_work import prepare_with_progress
        filename, _ = QFileDialog.getOpenFileName(self, "选择包含"+self.profile["label"]+"的 OSM 快照",
            str(self.root / "data/raw/osm/china-latest.osm.pbf"), "OSM (*.osm.pbf *.pbf *.osm)")
        if not filename:
            return
        try:
            prepare_with_progress(self, "建立"+self.profile["label"]+"图层", lambda progress: self.store.install_index(self.root, filename, progress))
            self.reload()
            self.map_view.call(self.reload_command)
        except (OSError, ValueError, RuntimeError) as error:
            QMessageBox.warning(self, self.profile["label"]+"图层未更新", str(error))
