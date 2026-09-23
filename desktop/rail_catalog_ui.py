"""Topology catalog: track type -> physical line/station -> endpoint section."""

import json
import hashlib
from copy import deepcopy
from pathlib import Path
import threading
import sqlite3
import math
from PySide6.QtCore import Qt, Signal, QTimer, QMimeData
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QComboBox,
    QTreeWidgetItem,
    QTreeWidget,
    QDialog,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QDialogButtonBox,
    QHeaderView,
    QMessageBox,
    QLineEdit,
    QMenu,
    QInputDialog,
    QTabWidget,
    QAbstractItemView,
    QSpinBox,
    QHBoxLayout,
)

try:
    from .components import Switch, text_label, GrowingTree
    from .provinces import geographic_catalog, VERSION
    from .rail_categories import catalog_parents, TRACK_TYPES
    from .catalog_metadata import (
        rail_station_records,
        rail_switch_owner,
        STATION_TYPES,
        normalize_station_attributes,
    )
except ImportError:
    from components import Switch, text_label, GrowingTree
    from provinces import geographic_catalog, VERSION
    from rail_categories import catalog_parents, TRACK_TYPES
    from catalog_metadata import (
        rail_station_records,
        rail_switch_owner,
        STATION_TYPES,
        normalize_station_attributes,
    )

MAX_CATALOG_TREE_ITEMS = 4000
MAX_STATION_TREE_ITEMS = 25000
SHARED_CATALOG_PATH = Path(__file__).resolve().parents[1] / "data/catalog/rail_catalog_overrides.json"


def _read_catalog_overrides(path):
    path = Path(path)
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"铁路目录文件格式无效：{path}")
    records = {
        key: value for key, value in payload.items()
        if isinstance(key, str) and isinstance(value, dict)
        and isinstance(value.get("archived", False), bool)
        and (
            "folder_path" not in value
            or isinstance(value["folder_path"], list)
            and bool(value["folder_path"])
            and all(isinstance(part, str) and part.strip() for part in value["folder_path"])
        )
    }
    # The former automatic folders are no longer meaningful in the new
    # taxonomy. Keep every other manual path and every other override field.
    for key, value in records.items():
        folder = value.get("folder_path")
        if not key.startswith("station:") and isinstance(folder, list) and len(folder) == 3 and folder[:2] == ["高速铁路", "区域高速铁路"]:
            region = folder[2].lstrip("123456 ")
            if region in {"华北", "东北", "华东", "中南", "西南", "西北"}:
                records[key] = {**value, "folder_path": ["高速铁路", region, "其他/速度待核对"]}
        elif not key.startswith("station:") and isinstance(folder, list) and len(folder) == 3 and folder[:2] == ["高速铁路", "八纵八横"]:
            records[key] = {**value, "folder_path": ["高速铁路", "干线", "其他/速度待核对"]}
        elif not key.startswith("station:") and folder in (
            ["高速铁路", "区域高速铁路"],
            ["高速铁路", "八纵八横"],
            ["普速铁路", "区域干线"],
            ["普速铁路", "国家铁路干线"],
        ):
            records[key] = {field: content for field, content in value.items() if field != "folder_path"}
    return records


class CatalogTree(GrowingTree):
    MIME_TYPE = "application/x-railscope-catalog-items"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.drop_callback = None

    def dropEvent(self, event):
        target = self.itemAt(event.position().toPoint())
        selected = list(self.selectedItems())
        if target and selected and callable(self.drop_callback):
            callback = self.drop_callback
            event.acceptProposedAction()
            QTimer.singleShot(
                0, lambda: self._finish_drop(callback, selected, target)
            )
            return
        event.ignore()

    def _finish_drop(self, callback, selected, target):
        try:
            callback(selected, target)
        except (ValueError, OSError, RuntimeError) as error:
            QMessageBox.warning(self, "目录未移动", str(error))

    def mimeData(self, items):
        """Do not serialize thousands of selected tree rows for an internal move."""
        data = QMimeData()
        data.setData(self.MIME_TYPE, b"move")
        return data

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(self.MIME_TYPE):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(self.MIME_TYPE):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)


def add_folder_move_menu(menu, paths, callback):
    """Build an existing-folder cascade and return the created root actions."""
    tree = {}
    terminal = "__railscope_folder__"
    for path in paths:
        clean = tuple(str(part).strip() for part in path if str(part).strip())
        if not clean or clean[0] == "已归档":
            continue
        node = tree
        for part in clean:
            node = node.setdefault(part, {})
        node[terminal] = True

    def populate(parent, node, prefix=()):
        def schedule(path):
            QTimer.singleShot(0, lambda value=list(path): callback(value))

        for label in sorted(key for key in node if key != terminal):
            child = node[label]
            path = (*prefix, label)
            descendants = [key for key in child if key != terminal]
            if descendants:
                submenu = parent.addMenu(label)
                if child.get(terminal):
                    submenu.addAction(
                        "移动到这里", lambda checked=False, value=path: schedule(value)
                    )
                    submenu.addSeparator()
                populate(submenu, child, path)
            else:
                parent.addAction(
                    label, lambda checked=False, value=path: schedule(value)
                )

    populate(menu, tree)
    if not tree:
        action = menu.addAction("暂无可用文件夹")
        action.setEnabled(False)
    return menu.actions()


class RailCatalog(QWidget):
    classified = Signal(dict)
    classification_failed = Signal(str)
    enabled_requested = Signal()
    station_enabled_requested = Signal(str)
    station_partial_changed = Signal(str, bool)
    station_edit_requested = Signal(str)
    line_names_changed = Signal(dict)
    metadata_changed = Signal()
    feature_activated = Signal(dict)

    def __init__(self, directory, settings, map_view, parent=None, regions=None, shared_path=None):
        super().__init__(parent)
        self.map = map_view
        self.directory = Path(directory).resolve()
        self.path = Path(settings)
        self.shared_path = Path(shared_path) if shared_path is not None else SHARED_CATALOG_PATH
        source = Path(directory) / "rail_catalog.json"
        self.catalog = (
            json.loads(source.read_text(encoding="utf-8")) if source.exists() else {}
        )
        cache = Path(directory) / "rail_catalog.topology.json"
        if cache.exists():
            value = json.loads(cache.read_text(encoding="utf-8"))
            if value.get("version") == VERSION:
                self.catalog = value["catalog"]
        self.shared_overrides = {}
        self.local_overrides = {}
        self.overrides = {}
        self.catalog_undo = []
        self.catalog_redo = []
        self.way_names = {}
        self.visible = set()
        self.all_visible = False
        self.excluded = set()
        self.line_masters = {"operating": False, "construction": False}
        self.regions = regions or []
        self.station_records = []
        self.station_total = 0
        self.station_query = ""
        self.station_masters = {"station": False, "control": False}
        self.station_excluded = set()
        self.station_direct_visible = set()
        self.station_direct_groups = {}
        self.station_folder_paths = set()
        self.transient_station_records = {}
        self.selected_switch_owners = {}
        self.line_folder_paths = set()
        try:
            self.shared_overrides = _read_catalog_overrides(self.shared_path)
            self.local_overrides = _read_catalog_overrides(self.path)
            self._merge_catalog_overrides()
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "国铁分类设置未载入", str(error))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.setMinimumWidth(0)
        self.mode = QComboBox()
        self.mode.addItems(["全国铁路业务分类 → 整条线路"])
        self.mode.setEnabled(False)
        self.mode.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.mode.setMinimumContentsLength(10)
        self.mode.currentIndexChanged.connect(self.populate)
        # Compatibility state for old workspace files/tests.  Search and mode
        # controls live in the map header and are intentionally not in this pane.
        search = QLineEdit()
        search.setPlaceholderText("筛选业务线路名称 / RL 编号")
        self.search = search
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(180)
        self.search_timer.timeout.connect(self.populate)
        search.textChanged.connect(self.search_changed)
        self.tabs = QTabWidget()
        self.line_page = QWidget()
        line_layout = QVBoxLayout(self.line_page)
        line_layout.setContentsMargins(0, 0, 0, 0)
        line_layout.addWidget(text_label("全国铁路线", "sectionLabel"))
        self.tree = CatalogTree()
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(12)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(1, 62)
        self.tree.setMinimumWidth(0)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setDragEnabled(True)
        self.tree.setAcceptDrops(True)
        self.tree.setDropIndicatorShown(True)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.tree.drop_callback = self.drop_line_items
        self.tree.itemDoubleClicked.connect(self.focus_item)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.context_menu)
        line_layout.addWidget(self.tree)
        line_layout.addStretch(1)
        self.tabs.addTab(self.line_page, "线路目录")
        self.station_page = QWidget()
        station_layout = QVBoxLayout(self.station_page)
        station_layout.setContentsMargins(0, 0, 0, 0)
        station_layout.addWidget(text_label("省 / 市 / 车站实体", "sectionLabel"))
        self.station_tree = CatalogTree()
        self.station_tree.setColumnCount(2)
        self.station_tree.setHeaderHidden(True)
        self.station_tree.setIndentation(12)
        self.station_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.station_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.station_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.station_tree.setColumnWidth(1, 62)
        self.station_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.station_tree.setDragEnabled(True)
        self.station_tree.setAcceptDrops(True)
        self.station_tree.setDropIndicatorShown(True)
        self.station_tree.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.station_tree.drop_callback = self.drop_station_items
        self.station_tree.itemDoubleClicked.connect(self.focus_station_item)
        self.station_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.station_tree.customContextMenuRequested.connect(
            self.station_context_menu
        )
        station_layout.addWidget(self.station_tree)
        self.station_note = text_label("正在读取车站实体目录…", wrap=True)
        station_layout.addWidget(self.station_note)
        station_layout.addStretch(1)
        self.tabs.addTab(self.station_page, "车站目录")
        self.yard_page = QWidget()
        yard_layout = QVBoxLayout(self.yard_page)
        yard_layout.setContentsMargins(0, 0, 0, 0)
        self.yard_tree = QTreeWidget()
        self.yard_tree.setHeaderHidden(True)
        self.yard_tree.itemDoubleClicked.connect(self.focus_yard_item)
        yard_layout.addWidget(self.yard_tree)
        self.tabs.addTab(self.yard_page, "站场股道")
        self.switch_page = QWidget()
        switch_layout = QVBoxLayout(self.switch_page)
        switch_layout.setContentsMargins(0, 0, 0, 0)
        self.switch_search = QLineEdit()
        self.switch_search.setPlaceholderText("搜索道岔源节点编号")
        self.switch_search.returnPressed.connect(self.populate_switch_tree)
        switch_layout.addWidget(self.switch_search)
        self.switch_tree = QTreeWidget()
        self.switch_tree.setHeaderHidden(True)
        self.switch_tree.itemDoubleClicked.connect(self.focus_switch_item)
        switch_layout.addWidget(self.switch_tree)
        pager = QHBoxLayout()
        self.switch_page_number = QSpinBox()
        self.switch_page_number.setMinimum(1)
        self.switch_page_number.valueChanged.connect(self.populate_switch_tree)
        pager.addWidget(text_label("页码"))
        pager.addWidget(self.switch_page_number)
        self.switch_count = text_label("")
        pager.addWidget(self.switch_count, 1)
        switch_layout.addLayout(pager)
        self.tabs.addTab(self.switch_page, "道岔目录")
        self.platform_page = QWidget()
        platform_layout = QVBoxLayout(self.platform_page)
        platform_layout.setContentsMargins(0, 0, 0, 0)
        self.platform_tree = QTreeWidget()
        self.platform_tree.setHeaderHidden(True)
        self.platform_tree.itemDoubleClicked.connect(self.focus_platform_item)
        platform_layout.addWidget(self.platform_tree)
        self.tabs.addTab(self.platform_page, "站台线目录")
        self.tabs.currentChanged.connect(self._load_catalog_tab)
        self.station_tree.itemExpanded.connect(self._load_station_switches)
        layout.addWidget(self.tabs)
        self.note = text_label(
            self.catalog_note(),
            wrap=True,
        )
        layout.addWidget(self.note)
        self.populate()
        self.populate_station_tree()
        self.populate_yard_tree()
        self.classified.connect(self.apply_classification)
        self.classification_failed.connect(self.note.setText)
        if (Path(directory) / "rail.sqlite").exists() and not all(
            r.get("classification") == VERSION for r in self.catalog.values()
        ):
            self.note.setText("正在后台按线路拓扑重建端点线段目录…无需重新导入 PBF。")

            def classify():
                try:
                    result = geographic_catalog(directory)
                    self.classified.emit(result)
                except (ValueError, OSError, RuntimeError, sqlite3.Error) as error:
                    try:
                        self.classification_failed.emit("拓扑目录未完成：" + str(error))
                    except RuntimeError:
                        pass  # Window was closed; any completed cache remains reusable.

            threading.Thread(target=classify, daemon=True).start()

    def apply_classification(self, catalog):
        self._merge_catalog_overrides()
        was_all = self.all_visible or (
            bool(self.catalog)
            and len(self.visible)
            == sum(
                not self.meta(key).get("archived", False) for key in self.catalog
            )
        )
        ways = {w for key in self.visible for w in self.catalog[key]["way_ids"]}
        self.catalog = catalog
        self.excluded = set()
        if was_all and len(catalog) > MAX_CATALOG_TREE_ITEMS:
            self.all_visible = True
            self.visible = set()
        else:
            self.all_visible = False
            self.visible = {
                key
                for key, record in catalog.items()
                if ways.intersection(record["way_ids"])
                and not self.meta(key).get("archived", False)
            }
        if ways or was_all:
            self.send_visibility(False)
        self.populate()
        self.populate_station_tree()
        self.populate_yard_tree()
        self.note.setText(
            self.catalog_note("目录已按轨道类型和物理线路/车站重建。")
        )

    def _merge_catalog_overrides(self):
        keys = self.shared_overrides.keys() | self.local_overrides.keys()
        self.overrides = {
            key: {**self.shared_overrides.get(key, {}), **self.local_overrides.get(key, {})}
            for key in keys
        }

    def _save_local_overrides(self, changes):
        before = deepcopy(self.local_overrides)
        proposed = {**self.local_overrides}
        for key, change in changes.items():
            proposed[key] = {**proposed.get(key, {}), **change}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(proposed, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)
        self.local_overrides = proposed
        self._merge_catalog_overrides()
        self.catalog_undo.append(before)
        self.catalog_undo = self.catalog_undo[-30:]
        self.catalog_redo.clear()

    def _restore_local_overrides(self, value):
        previous = self.local_overrides
        changed = {
            key for key in previous.keys() | value.keys()
            if previous.get(key) != value.get(key)
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)
        self.local_overrides = value
        self._merge_catalog_overrides()
        self.metadata_changed.emit()
        line_keys = {key for key in changed if key in self.catalog}
        station_ids = {key.removeprefix("station:") for key in changed if key.startswith("station:")}
        folder_only = all(
            {field for field in set(previous.get(key, {})) | set(value.get(key, {}))
             if previous.get(key, {}).get(field) != value.get(key, {}).get(field)} <= {"folder_path"}
            for key in line_keys
        )
        if line_keys and not (folder_only and self._move_line_items_in_tree(line_keys)):
            self.populate()
        if station_ids:
            self._refresh_station_items(station_ids)
        self.send_visibility(False)
        self.send_station_visibility()

    def undo_catalog(self):
        if self.catalog_undo:
            value = self.catalog_undo.pop()
            self.catalog_redo.append(deepcopy(self.local_overrides))
            self._restore_local_overrides(value)

    def redo_catalog(self):
        if self.catalog_redo:
            value = self.catalog_redo.pop()
            self.catalog_undo.append(deepcopy(self.local_overrides))
            self._restore_local_overrides(value)

    def refresh_catalog(self):
        self.note.setText("正在重新扫描铁路拓扑、线路与全部站点；工作区修改会自动重放…")

        def rebuild():
            try:
                self.classified.emit(geographic_catalog(self.directory, force=True))
            except (ValueError, OSError, RuntimeError, sqlite3.Error) as error:
                self.classification_failed.emit("刷新铁路目录失败：" + str(error))

        threading.Thread(target=rebuild, daemon=True).start()

    def catalog_note(self, prefix=""):
        base = prefix or (
            "按全国铁路业务分类整理；默认完整列出有名称的业务线路。"
            "未命名 OSM 轨道可在主搜索框输入“未命名轨道”后整理。"
            "具体端点线段保存在对象属性和通道编辑器中。"
        )
        if len(self.catalog) > MAX_CATALOG_TREE_ITEMS:
            named = sum(
                not self.display_name(key).startswith("未命名轨道")
                for key in self.catalog
            )
            return (
                base
                + f" 当前快照含 {named:,} 条有名称业务线路。"
                + f"搜索结果过多时每次显示前 {MAX_CATALOG_TREE_ITEMS:,} 项。"
            )
        return base

    def meta(self, key):
        record = self.catalog[key]
        legacy = json.dumps(
            [record.get("province", ""), record.get("name", key)],
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
        query = self.search.text().strip().casefold()

        def matches(name):
            if not query:
                return True
            meta = self.meta(name)
            values = (
                name,
                meta.get("name", ""),
                meta.get("line_name", ""),
                meta.get("line_display_name", ""),
                meta.get("station_name", ""),
                meta.get("from_name", ""),
                meta.get("to_name", ""),
                meta.get("from_node", ""),
                meta.get("to_node", ""),
                meta.get("track_type", ""),
            )
            return query in " ".join(map(str, values)).casefold()

        # A national snapshot contains tens of thousands of unnamed OSM way
        # groups.  Taking the first 4,000 insertion-order records used to hide
        # almost every properly named business line.  Named yard groups are
        # included and can also be reached from their station owner.
        candidates = [
            name
            for name in self.catalog
            if matches(name)
            and (
                bool(query)
                or not self.display_name(name).startswith("未命名轨道")
            )
        ]
        candidates.sort(
            key=lambda key: (
                self.display_name(key).startswith("未命名轨道"),
                self.display_name(key).casefold(),
                str(key),
            )
        )
        matched = len(candidates)
        names = candidates[:MAX_CATALOG_TREE_ITEMS]
        self.catalog_limited = matched > len(names)
        merged_lines = {}
        for name in names:
            merged_lines.setdefault(
                (self.parents(name), self.display_name(name).strip().casefold()), []
            ).append(name)
        for (parents, _normalized_label), component_names in merged_lines.items():
            component_names = sorted(component_names)
            name = component_names[0]
            record = self.catalog[name]
            meta = self.meta(name)
            parent = self.tree.invisibleRootItem()
            key = ()
            for label in parents:
                key += (label,)
                if key not in groups:
                    groups[key] = QTreeWidgetItem(parent, [label])
                parent = groups[key]
            label = self.display_name(name)
            item = QTreeWidgetItem(parent, [label])
            count = sum(
                component.get("edge_count")
                or len(component.get("edge_ids", component["way_ids"]))
                for component in (self.catalog[value] for value in component_names)
            )
            topology = (
                f"包含 {sum(self.catalog[value].get('section_count', 0) for value in component_names)} 个端点线段；点击地图上的具体线段查看两端点和相邻线段。"
                if record.get("catalog_group_id")
                else f"起点：{meta.get('from_name', '旧目录无端点')}\n终点：{meta.get('to_name', '旧目录无端点')}\n"
                f"起点相邻：{', '.join(meta.get('from_adjacent_sections', [])) or '待重建'}\n"
                f"终点相邻：{', '.join(meta.get('to_adjacent_sections', [])) or '待重建'}"
            )
            merged_note = (
                f"已合并 {len(component_names)} 个同名线路片段。\n"
                if len(component_names) > 1
                else ""
            )
            item.setToolTip(
                0,
                f"{label}\n{meta.get('track_type', '未确认类型')} · {count} 个 NetworkEdge\n"
                f"{merged_note}"
                f"{topology}\n"
                f"依据：{meta.get('type_evidence', '待核对')}",
            )
            for component_name in component_names:
                self.items[component_name] = item
            item.setData(0, Qt.ItemDataRole.UserRole, name)
            self.members[id(item)] = set(component_names)
            ancestor = parent
            while ancestor is not self.tree.invisibleRootItem():
                self.members.setdefault(id(ancestor), set()).update(component_names)
                ancestor = ancestor.parent() or self.tree.invisibleRootItem()
            visible_components = {
                value for value in component_names if self.is_visible(value)
            }
            switch = Switch(bool(visible_components))
            switch.setMixed(
                bool(visible_components) and len(visible_components) != len(component_names)
            )
            switch.setEnabled(
                not all(self.meta(value).get("archived", False) for value in component_names)
            )
            switch.toggled.connect(
                lambda on, values=set(component_names): self.toggle_group(values, on)
            )
            self.tree.setItemWidget(item, 1, switch)
        for item in groups.values():
            keys = self.members[id(item)]
            displayed_items = {id(self.items[key]) for key in keys if key in self.items}
            item.setText(0, item.text(0) + f" · {len(displayed_items)} 项")
            visible_keys = {key for key in keys if self.is_visible(key)}
            control = Switch(bool(visible_keys))
            control.setEnabled(
                not self.catalog_limited
                and not all(self.meta(k).get("archived", False) for k in keys)
            )
            if self.catalog_limited:
                control.setToolTip("结果过多，请先搜索具体物理线路、车站或稳定编号。")
            control.setMixed(bool(visible_keys) and visible_keys != keys)
            control.toggled.connect(lambda on, k=keys: self.toggle_group(k, on))
            self.tree.setItemWidget(item, 1, control)
        for key, item in groups.items():
            item.setExpanded(key in expanded)
        self.groups = groups
        self.line_group_paths = {id(item): path for path, item in groups.items()}
        self.line_folder_paths.update(
            path[1:] if path and path[0] == "已归档" else path for path in groups
        )
        if len(self.catalog) <= MAX_CATALOG_TREE_ITEMS:
            self.filter_tree(self.search.text())
        else:
            self.tree.schedule_height()
        # QTreeWidget retains an internal scroll offset even with its scrollbar
        # hidden.  After a large result is collapsed/rebuilt that offset appears
        # as a large blank block above the first directory row.
        self.tree.scrollToTop()
        QTimer.singleShot(0, self.tree.scrollToTop)

    def search_changed(self, text):
        if len(self.catalog) <= MAX_CATALOG_TREE_ITEMS:
            self.filter_tree(text)
        else:
            self.search_timer.start()

    def parents(self, key):
        meta = self.meta(key)
        folders = meta.get("folder_path")
        parents = (
            tuple(folders)
            if isinstance(folders, list) and folders
            else tuple(catalog_parents(meta, self.mode.currentIndex()))
        )
        return (("已归档",) + parents) if meta.get("archived", False) else parents

    def set_query(self, text, search_type="全部"):
        """Receive the single map-header search instead of owning a second box."""
        value = text.strip()
        self.tree.set_filter_active(bool(value) and search_type in ("全部", "铁路线"))
        self.station_tree.set_filter_active(
            bool(value) and search_type in ("全部", "车站及线路所")
        )
        if search_type in ("全部", "铁路线"):
            self.search.blockSignals(True)
            self.search.setText(value)
            self.search.blockSignals(False)
            self.populate()
        if search_type in ("全部", "车站及线路所"):
            self.station_query = value
            self.populate_station_tree()
            self.tabs.setCurrentWidget(self.station_page)
        elif search_type == "铁路线":
            self.tabs.setCurrentWidget(self.line_page)

    def _load_catalog_tab(self, index):
        if self.tabs.widget(index) is self.yard_page:
            self.populate_yard_tree()
        elif self.tabs.widget(index) is self.switch_page:
            self.populate_switch_tree()
        elif self.tabs.widget(index) is self.platform_page:
            self.populate_platform_tree()

    def _platform_records(self):
        if hasattr(self, "platform_records"):
            return self.platform_records
        self.platform_records = []
        source = self.directory / "rail.sqlite"
        if source.exists():
            with sqlite3.connect(source) as db:
                for (raw,) in db.execute("SELECT data FROM features WHERE kind='railPlatforms'"):
                    feature = json.loads(raw)
                    if feature.get("geometry", {}).get("type") != "LineString":
                        continue
                    coords = feature["geometry"].get("coordinates") or []
                    if not coords:
                        continue
                    point = coords[len(coords)//2]
                    props = feature.get("properties", {})
                    self.platform_records.append({
                        "way_id": props.get("osm_way_id"),
                        "name": props.get("name") or props.get("way_tags", {}).get("ref") or "未命名站台线",
                        "coordinates": point,
                        "properties": props,
                    })
        return self.platform_records

    def populate_platform_tree(self):
        if self.platform_tree.topLevelItemCount():
            return
        for record in self._platform_records():
            item = QTreeWidgetItem(self.platform_tree, [f"站台线 {record['name']} · OSM {record['way_id']}"])
            item.setData(0, Qt.ItemDataRole.UserRole, record["way_id"])
            item.setToolTip(0, "真实 OSM railway=platform 线；目录显示不代表已核验停靠股道")

    def populate_yard_tree(self):
        self.yard_tree.clear()
        groups = {}
        for key, group in sorted(self.catalog.items(), key=lambda value: (str(value[1].get("station_name") or ""), value[0])):
            if not str(key).startswith("ST-"):
                continue
            provinces = group.get("provinces") or []
            province = provinces[0] if len(provinces) == 1 else "跨省同名 / 区域待核对"
            parent = groups.get(province)
            if parent is None:
                parent = QTreeWidgetItem(self.yard_tree, [province])
                groups[province] = parent
            item = QTreeWidgetItem(parent, [f"{self.display_name(key)} · {group.get('track_type', '')}"])
            item.setData(0, Qt.ItemDataRole.UserRole, key)
            item.setToolTip(0, f"稳定目录编号：{key}\n{group.get('type_evidence', '分类待核对')}")

    def focus_yard_item(self, item, column):
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if key in self.catalog:
            self.focus_catalog_key(key, column)

    def focus_catalog_key(self, key, column=0):
        line_item = self.items.get(key)
        if line_item is not None:
            self.focus_item(line_item, column)
            return
        proxy = QTreeWidgetItem([self.display_name(key)])
        self.members[id(proxy)] = {key}
        try:
            self.focus_item(proxy, column)
        finally:
            self.members.pop(id(proxy), None)

    def focus_platform_item(self, item, column):
        way_id = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(way_id, tuple):
            way_id = way_id[1]
        record = next((value for value in self._platform_records() if value["way_id"] == way_id), None)
        if record:
            self.map.call("focus", *record["coordinates"], 17, str(record["name"]))
            self.feature_activated.emit({"layer": "rail-platform-line", "properties": record["properties"]})

    def populate_switch_tree(self, *_args):
        source = self.directory / "rail.sqlite"
        if not source.exists():
            self.switch_count.setText("尚未导入道岔数据")
            return
        query = self.switch_search.text().strip().removeprefix("SW-")
        clause = "kind='railPoints' AND json_extract(data,'$.properties.kind')='switch'"
        args = []
        if query:
            clause += " AND CAST(json_extract(data,'$.properties.osm_node_id') AS TEXT) LIKE ?"
            args.append(f"%{query}%")
        with sqlite3.connect(source) as db:
            total = db.execute(f"SELECT count(*) FROM features WHERE {clause}", args).fetchone()[0]
            pages = max(1, (total + 499) // 500)
            self.switch_page_number.blockSignals(True)
            self.switch_page_number.setMaximum(pages)
            self.switch_page_number.setValue(min(self.switch_page_number.value(), pages))
            self.switch_page_number.blockSignals(False)
            rows = db.execute(
                f"SELECT json_extract(data,'$.properties.osm_node_id') FROM features WHERE {clause} "
                "ORDER BY CAST(json_extract(data,'$.properties.osm_node_id') AS INTEGER) LIMIT 500 OFFSET ?",
                [*args, (self.switch_page_number.value() - 1) * 500],
            ).fetchall()
        self.switch_tree.clear()
        for (node_id,) in rows:
            item = QTreeWidgetItem(self.switch_tree, [f"道岔 · SW-{node_id}"])
            item.setData(0, Qt.ItemDataRole.UserRole, int(node_id))
            item.setToolTip(0, "OSM 来源节点；双击定位并查看所属车站或线路所")
        self.switch_count.setText(f"{total:,} 个道岔 · 第 {self.switch_page_number.value()}/{pages} 页")

    def focus_switch_item(self, item, column):
        node_id = item.data(0, Qt.ItemDataRole.UserRole)
        if node_id is not None:
            self.focus_switch_node(node_id)

    def focus_switch_node(self, node_id):
        source = self.directory / "rail.sqlite"
        if not source.exists():
            return
        with sqlite3.connect(source) as db:
            row = db.execute(
                "SELECT data FROM features WHERE kind='railPoints' "
                "AND json_extract(data,'$.properties.kind')='switch' "
                "AND json_extract(data,'$.properties.osm_node_id')=? LIMIT 1",
                (node_id,),
            ).fetchone()
        if row:
            feature = json.loads(row[0])
            self.map.call("focus", *feature["geometry"]["coordinates"], 17, f"道岔 SW-{node_id}")
            self.feature_activated.emit({"layer": "rail-detail-points", **feature})
            self.station_owner_for_node(node_id)

    def _load_station_switches(self, item):
        station_id = item.data(0, Qt.ItemDataRole.UserRole)
        record = self.station_record_by_id.get(station_id)
        if record is None or item.data(0, Qt.ItemDataRole.UserRole + 2):
            return
        item.setData(0, Qt.ItemDataRole.UserRole + 2, True)
        source = self.directory / "rail.sqlite"
        point = record.get("coordinates") or []
        if not source.exists() or len(point) < 2:
            return
        lon, lat = point[:2]
        dx = .003 / max(.2, math.cos(math.radians(lat)))
        with sqlite3.connect(source) as db:
            rows = db.execute(
                "SELECT json_extract(f.data,'$.properties.osm_node_id') FROM bounds b "
                "JOIN features f ON f.id=b.id WHERE f.kind='railPoints' "
                "AND json_extract(f.data,'$.properties.kind')='switch' "
                "AND b.minx BETWEEN ? AND ? AND b.miny BETWEEN ? AND ?",
                (lon-dx, lon+dx, lat-.003, lat+.003),
            ).fetchall()
        known = set(record.get("member_switch_ids", []))
        for (switch_id,) in rows:
            if switch_id in known:
                continue
            owner = rail_switch_owner(self.directory, switch_id, self.regions, self.overrides)
            if owner and owner["id"] == station_id:
                known.add(switch_id)
                leaf = QTreeWidgetItem(item, [f"道岔 SW-{switch_id}", ""])
                leaf.setData(0, Qt.ItemDataRole.UserRole, ("switch", switch_id))
                leaf.setToolTip(0, f"归属：{record['name']}\nOSM 道岔节点：{switch_id}")
        record["member_switch_ids"] = sorted(known)

    def populate_station_tree(self):
        self.station_records, self.station_total = rail_station_records(
            self.directory,
            self.regions,
            self.station_query,
            MAX_STATION_TREE_ITEMS,
            self.overrides,
        )
        known = {record["id"] for record in self.station_records}
        self.station_records.extend(
            deepcopy(record)
            for key, record in self.transient_station_records.items()
            if key not in known
        )
        self.station_record_by_id = {
            record["id"]: record for record in self.station_records
        }
        by_name = {}
        station_grid = {}
        for record in self.station_records:
            by_name.setdefault((record["name"].removesuffix("站").casefold(), record["province"]), []).append(record["id"])
            point = record.get("coordinates") or []
            if len(point) >= 2:
                station_grid.setdefault((int(point[0]*100), int(point[1]*100)), []).append(record)
        yards = {}
        for key, group in self.catalog.items():
            if not str(key).startswith("ST-"):
                continue
            candidates = {sid for province in group.get("provinces", []) for sid in by_name.get((str(group.get("station_name") or "").removesuffix("站").casefold(), province), [])}
            if len(candidates) == 1:
                yards.setdefault(next(iter(candidates)), []).append(key)
        platforms = {}
        for platform in self._platform_records():
            lon, lat = platform["coordinates"][:2]
            nearby = []
            cell = (int(lon*100), int(lat*100))
            for x in range(cell[0]-1, cell[0]+2):
                for y in range(cell[1]-1, cell[1]+2):
                    for station in station_grid.get((x,y), []):
                        gap = math.hypot((lon-station["coordinates"][0])*math.cos(math.radians(lat)), lat-station["coordinates"][1])*111195
                        if gap <= 250:
                            nearby.append((gap, station["id"]))
            nearby.sort()
            if nearby and (len(nearby) == 1 or nearby[1][0] - nearby[0][0] >= 75):
                platforms.setdefault(nearby[0][1], []).append(platform)
        self.station_tree.setUpdatesEnabled(False)
        self.station_tree.clear()
        self.station_items = {}
        self.station_members = {}
        groups = {}
        for record in self.station_records:
            record["_source_name"] = record["name"]
            record["_source_station_type"] = record["station_type"]
            record["_source_line_ids"] = list(record["line_ids"])
            record["_source_line_names"] = list(record["line_names"])
            self._apply_station_override(record)
            path = self._station_path(record)
            self.station_folder_paths.add(
                tuple(path[1:] if path and path[0] == "已归档" else path)
            )
            parent = self.station_tree.invisibleRootItem()
            current = ()
            for label in path:
                current += (label,)
                if current not in groups:
                    groups[current] = QTreeWidgetItem(parent, [label, ""])
                parent = groups[current]
            label = record["name"]
            item = QTreeWidgetItem(parent, [label, ""])
            item.setData(0, Qt.ItemDataRole.UserRole, record["id"])
            item.setToolTip(0, self._station_tooltip(record))
            control = Switch(self.station_is_visible(record))
            control.setEnabled(not record["archived"])
            control.toggled.connect(
                lambda on, station=record: self.toggle_station(station, on)
            )
            self.station_tree.setItemWidget(item, 1, control)
            self.station_items[record["id"]] = item
            self.station_members[id(item)] = {record["id"]}
            self._add_station_assets(item, record, yards.get(record["id"], []), platforms.get(record["id"], []))
            ancestor = parent
            while ancestor is not self.station_tree.invisibleRootItem():
                self.station_members.setdefault(id(ancestor), set()).add(record["id"])
                ancestor = ancestor.parent() or self.station_tree.invisibleRootItem()
            for switch_id in record.get("member_switch_ids", []):
                switch_item = QTreeWidgetItem(item, [f"道岔 SW-{switch_id}", ""])
                switch_item.setToolTip(0, f"归属：{record['name']}\nOSM 道岔节点：{switch_id}")
                switch_item.setData(0, Qt.ItemDataRole.UserRole, ("switch", switch_id))
            item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        for path, item in groups.items():
            members = self.station_members.get(id(item), set())
            item.setText(0, item.text(0) + f" · {len(members)} 项")
            active = {value for value in members if self.station_is_visible(self.station_record_by_id[value])}
            control = Switch(bool(active))
            control.setMixed(bool(active) and active != members)
            control.toggled.connect(
                lambda on, node=item: self.toggle_station_records(
                    self.station_members.get(id(node), set()), on
                )
            )
            self.station_tree.setItemWidget(item, 1, control)
        self.station_groups = groups
        self.station_group_paths = {id(item): path for path, item in groups.items()}
        shown = len(self.station_records)
        suffix = "；结果已限制，请在主地图搜索框继续缩小范围" if shown < self.station_total else ""
        self.station_note.setText(
            f"共匹配 {self.station_total:,} 个车站或线路所，当前列出 {shown:,} 项；已关联道岔列在站点下，其余可到道岔目录检索{suffix}。"
        )
        self.station_tree.schedule_height()
        self.station_tree.setUpdatesEnabled(True)

    def _add_station_assets(self, item, record, yards, platforms):
        if yards:
            folder = QTreeWidgetItem(item, ["站场股道", ""])
            for key in sorted(yards):
                group = self.catalog[key]
                leaf = QTreeWidgetItem(folder, [f"{self.display_name(key)} · {group.get('track_type', '')}", ""])
                leaf.setData(0, Qt.ItemDataRole.UserRole, ("yard", key))
                leaf.setToolTip(0, f"目录编号：{key}\n来源：{group.get('type_evidence', '待核对')}")
        if platforms:
            folder = QTreeWidgetItem(item, ["真实站台线（邻近推断，待核验）", ""])
            for platform in platforms:
                leaf = QTreeWidgetItem(folder, [f"站台线 {platform['name']}", ""])
                leaf.setData(0, Qt.ItemDataRole.UserRole, ("platform", platform["way_id"]))
                leaf.setToolTip(0, f"OSM 来源轨道：{platform['way_id']}；邻近关联，非运行股道认定")

    def _apply_station_override(self, record):
        custom = self.overrides.get("station:" + record["id"], {})
        record["name"] = custom.get("display_name") or record.get(
            "_source_name", record["name"]
        )
        source_type = record.get("_source_station_type", record["station_type"])
        record["station_type"] = (
            custom.get("station_type")
            if custom.get("station_type") in STATION_TYPES
            else source_type
        )
        record["line_ids"] = list(record.get("_source_line_ids", record["line_ids"]))
        record["line_names"] = list(
            record.get("_source_line_names", record["line_names"])
        )
        if isinstance(custom.get("connected_lines"), list):
            record["line_ids"] = [
                value["line_id"]
                for value in custom["connected_lines"]
                if isinstance(value, dict) and value.get("line_id")
            ]
            record["line_names"] = [
                self.display_name(line_id) if line_id in self.catalog else line_id
                for line_id in record["line_ids"]
            ]
        record["archived"] = bool(custom.get("archived", False))
        record["overview_attributes"] = custom.get("overview_attributes", {})
        record["custom_attributes"] = custom.get("custom_attributes", {})

    def _station_path(self, record):
        custom = self.overrides.get("station:" + record["id"], {})
        folder = custom.get("folder_path")
        path = (
            tuple(folder)
            if isinstance(folder, list) and folder
            else (record["province"], record["city"])
        )
        return ("已归档", *path) if record.get("archived") else path

    @staticmethod
    def _station_tooltip(record):
        return (
            f"{record['name']}\n车站类型：{record['station_type']}\n"
            f"所在铁路：{', '.join(record['line_names']) or '待关联'}\n"
            f"稳定编号：{record['id']}"
        )

    @staticmethod
    def station_group(record):
        return "station"

    def station_is_visible(self, record):
        return (
            not record.get("archived", False)
            and (
                record["id"] in self.station_direct_visible
                or (
                    self.station_masters[self.station_group(record)]
                    and record["id"] not in self.station_excluded
                )
            )
        )

    def set_station_master(self, group, on):
        # One business master controls both owners and their child switches.
        self.station_masters["station"] = bool(on)
        self.station_masters["control"] = bool(on)
        group_ids = {
            record["id"]
            for record in self.station_records
            if self.station_group(record) == "station"
        }
        direct_ids = set(self.station_direct_visible)
        self.station_direct_visible.difference_update(direct_ids)
        for station_id in direct_ids:
            self.station_direct_groups.pop(station_id, None)
        self.station_excluded.difference_update(group_ids)
        self.sync_station_switches()
        self.send_station_visibility()

    def toggle_station_records(self, station_ids, on):
        for station_id in set(station_ids):
            record = self.station_record_by_id.get(station_id)
            if record is None or record.get("archived"):
                continue
            if self.station_masters["station"]:
                self.station_excluded.discard(station_id) if on else self.station_excluded.add(station_id)
            else:
                self.station_direct_visible.add(station_id) if on else self.station_direct_visible.discard(station_id)
                if on:
                    self.station_direct_groups[station_id] = "station"
                else:
                    self.station_direct_groups.pop(station_id, None)
        self.station_partial_changed.emit("station", bool(self.station_direct_visible))
        self.sync_station_switches()
        self.send_station_visibility()

    def sync_station_switches(self):
        for record in self.station_records:
            item = self.station_items.get(record["id"])
            if not item:
                continue
            control = self.station_tree.itemWidget(item, 1)
            control.blockSignals(True)
            control.setChecked(self.station_is_visible(record))
            control.blockSignals(False)
        for item in getattr(self, "station_groups", {}).values():
            members = self.station_members.get(id(item), set())
            visible = {
                value for value in members
                if value in self.station_record_by_id
                and self.station_is_visible(self.station_record_by_id[value])
            }
            control = self.station_tree.itemWidget(item, 1)
            if control is None:
                continue
            control.blockSignals(True)
            control.setChecked(bool(visible))
            control.setMixed(bool(visible) and visible != members)
            control.blockSignals(False)

    def toggle_station(self, record, on):
        group = "station"
        if self.station_masters["station"]:
            self.station_excluded.discard(record["id"]) if on else self.station_excluded.add(
                record["id"]
            )
        else:
            self.station_direct_visible.add(record["id"]) if on else self.station_direct_visible.discard(
                record["id"]
            )
            if on:
                self.station_direct_groups[record["id"]] = group
            else:
                self.station_direct_groups.pop(record["id"], None)
            partial = bool(self.station_direct_visible)
            self.station_partial_changed.emit(group, partial)
        item = self.station_items.get(record["id"])
        control = self.station_tree.itemWidget(item, 1) if item is not None else None
        if control is not None and control.isChecked() != self.station_is_visible(record):
            control.blockSignals(True)
            control.setChecked(self.station_is_visible(record))
            control.blockSignals(False)
        self.send_station_visibility()

    def send_station_visibility(self):
        hidden_ids = self.station_excluded | {
            key.removeprefix("station:")
            for key, value in self.overrides.items()
            if key.startswith("station:") and value.get("archived", False)
        }
        hidden = [
            int(value.split("/", 1)[1])
            for value in hidden_ids
            if value.startswith("node/") and value.split("/", 1)[1].isdigit()
        ]
        hidden.extend(
            int(switch_id)
            for station_id in hidden_ids
            for switch_id in self.station_record_by_id.get(station_id, {}).get(
                "member_switch_ids", []
            )
            if str(switch_id).isdigit()
        )
        hidden = sorted(set(hidden))
        self.map.call("setRailPointExclusions", hidden or None)
        def directly_visible():
            return [
                int(station_id.split("/", 1)[1])
                for station_id in self.station_direct_visible
                if station_id.startswith("node/")
                and station_id.split("/", 1)[1].isdigit()
                and not self.overrides.get("station:" + station_id, {}).get("archived", False)
            ]
        visible_stations = directly_visible()
        visible_controls = sorted({
            int(switch_id)
            for station_id in self.station_direct_visible
            for switch_id in self.station_record_by_id.get(station_id, {}).get("member_switch_ids", [])
            if str(switch_id).isdigit()
        })
        self.map.call(
            "setRailPointSelection",
            None if self.station_masters["station"] else visible_stations,
        )
        self.map.call(
            "setRailControlPointSelection",
            None if self.station_masters["station"] else visible_controls,
        )

    def focus_station_item(self, item, column):
        if column != 0:
            return
        station_id = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(station_id, tuple):
            kind, ident = station_id
            if kind == "switch":
                self.focus_switch_node(ident)
            elif kind == "platform":
                self.focus_platform_item(item, column)
            elif kind == "yard":
                self.focus_catalog_key(ident, column)
            return
        record = next((r for r in self.station_records if r["id"] == station_id), None)
        if record:
            self.map.call("focus", *record["coordinates"], 15, record["name"])
            self.feature_activated.emit(
                {
                    "layer": "rail-points",
                    "properties": {
                        **record.get("properties", {}),
                        "display_name": record["name"],
                        "station_type": record["station_type"],
                        "province": record["province"],
                        "city": record["city"],
                        "line_ids": record["line_ids"],
                        "line_names": record["line_names"],
                    },
                    "geometry": {"type": "Point", "coordinates": record["coordinates"]},
                }
            )

    def station_context_menu(self, position):
        item = self.station_tree.itemAt(position)
        if not item or not item.data(0, Qt.ItemDataRole.UserRole):
            return
        selected = self.station_tree.selectedItems()
        if item not in selected:
            self.station_tree.clearSelection()
            self.station_tree.setCurrentItem(item)
            selected = [item]
        ids = {
            station_id
            for selected_item in selected
            for station_id in self.station_members.get(id(selected_item), set())
        }
        menu = self.station_item_menu(item, ids)
        menu.exec(self.station_tree.viewport().mapToGlobal(position))
        menu.deleteLater()

    def station_item_menu(self, item, station_ids=None):
        station_id = item.data(0, Qt.ItemDataRole.UserRole)
        station_ids = set(station_ids or ([station_id] if station_id else []))
        menu = QMenu(self)

        def perform(action):
            try:
                action()
            except (ValueError, OSError, RuntimeError) as error:
                QMessageBox.warning(self, "目录修改未保存", str(error))

        edit = menu.addAction(
            "编辑名称、目录、类型和接轨线路…",
            lambda: self.station_edit_requested.emit(station_id),
        )
        edit.setEnabled(len(station_ids) == 1 and bool(station_id))
        move = menu.addMenu("移动到")
        add_folder_move_menu(
            move,
            self.station_destination_paths(),
            lambda path: perform(
                lambda: self.save_station_changes(station_ids, folder_path=path)
            ),
        )
        menu.addAction("在地图中定位", lambda: self.focus_station_item(item, 0))
        archived = all(
            self.overrides.get("station:" + value, {}).get("archived", False)
            for value in station_ids
        )
        menu.addAction(
            "取消归档 / 恢复" if archived else "归档",
            lambda: perform(
                lambda: self.save_station_changes(station_ids, archived=not archived)
            ),
        )
        return menu

    def station_destination_paths(self):
        paths = set(self.station_folder_paths)
        for path in self.station_groups:
            clean = path[1:] if path and path[0] == "已归档" else path
            if clean:
                paths.add(tuple(clean))
        for key, value in self.overrides.items():
            folder = value.get("folder_path") if key.startswith("station:") else None
            if isinstance(folder, list) and folder:
                paths.add(tuple(folder))
        return paths

    def drop_station_items(self, items, target):
        path = next(
            (path for path, folder in self.station_groups.items() if folder is target),
            None,
        )
        if path is None and target.parent():
            path = next(
                (path for path, folder in self.station_groups.items() if folder is target.parent()),
                None,
            )
        station_ids = {
            station_id
            for item in items
            for station_id in self.station_members.get(id(item), set())
        }
        if path and station_ids:
            clean = list(path[1:] if path[0] == "已归档" else path)
            self.save_station_changes(station_ids, folder_path=clean)

    def _station_group_path(self, item):
        return self.station_group_paths.get(id(item)) if item is not None else None

    def _ensure_station_group(self, path):
        parent = self.station_tree.invisibleRootItem()
        current = ()
        for label in path:
            current += (label,)
            if current not in self.station_groups:
                self.station_groups[current] = QTreeWidgetItem(parent, [label, ""])
                self.station_members[id(self.station_groups[current])] = set()
                self.station_group_paths[id(self.station_groups[current])] = current
                control = Switch(False)
                control.toggled.connect(
                    lambda on, node=self.station_groups[current]: self.toggle_station_records(
                        self.station_members.get(id(node), set()), on
                    )
                )
                self.station_tree.setItemWidget(self.station_groups[current], 1, control)
            parent = self.station_groups[current]
        return parent

    def _insert_station_record(self, record):
        """Insert one resolved owner without rebuilding the national station tree."""
        station_id = record["id"]
        existing = self.station_items.get(station_id)
        if existing is not None:
            current = self.station_record_by_id[station_id]
            known = {
                int(value)
                for value in current.get("member_switch_ids", [])
                if str(value).isdigit()
            }
            additions = sorted({
                int(value)
                for value in record.get("member_switch_ids", [])
                if str(value).isdigit()
            } - known)
            for switch_id in additions:
                switch_item = QTreeWidgetItem(
                    existing, [f"道岔 SW-{switch_id}", ""]
                )
                switch_item.setToolTip(
                    0, f"归属：{current['name']}\nOSM 道岔节点：{switch_id}"
                )
                switch_item.setData(0, Qt.ItemDataRole.UserRole, ("switch", switch_id))
            if additions:
                current["member_switch_ids"] = sorted(known | set(additions))
            return existing
        record = deepcopy(record)
        record["_source_name"] = record["name"]
        record["_source_station_type"] = record["station_type"]
        record["_source_line_ids"] = list(record["line_ids"])
        record["_source_line_names"] = list(record["line_names"])
        self._apply_station_override(record)
        self.station_records.append(record)
        self.station_record_by_id[station_id] = record
        path = self._station_path(record)
        clean = path[1:] if path and path[0] == "已归档" else path
        if clean:
            self.station_folder_paths.add(tuple(clean))
        parent = self._ensure_station_group(path)
        item = QTreeWidgetItem(parent, [record["name"], ""])
        item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        item.setData(0, Qt.ItemDataRole.UserRole, station_id)
        item.setToolTip(0, self._station_tooltip(record))
        control = Switch(self.station_is_visible(record))
        control.setEnabled(not record.get("archived", False))
        control.toggled.connect(lambda on, station=record: self.toggle_station(station, on))
        self.station_tree.setItemWidget(item, 1, control)
        self.station_items[station_id] = item
        self.station_members[id(item)] = {station_id}
        for switch_id in record.get("member_switch_ids", []):
            switch_item = QTreeWidgetItem(item, [f"道岔 SW-{switch_id}", ""])
            switch_item.setToolTip(0, f"归属：{record['name']}\nOSM 道岔节点：{switch_id}")
            switch_item.setData(0, Qt.ItemDataRole.UserRole, ("switch", switch_id))
        for length in range(1, len(path) + 1):
            prefix = path[:length]
            group = self.station_groups[prefix]
            members = self.station_members.setdefault(id(group), set())
            members.add(station_id)
            group.setText(0, f"{prefix[-1]} · {len(members)} 项")
            active = {
                value
                for value in members
                if self.station_is_visible(self.station_record_by_id[value])
            }
            group_control = self.station_tree.itemWidget(group, 1)
            group_control.blockSignals(True)
            group_control.setChecked(bool(active))
            group_control.setMixed(bool(active) and active != members)
            group_control.blockSignals(False)
        self.station_tree.schedule_height()
        return item

    def _refresh_station_items(self, station_ids):
        affected_paths = set()
        self.station_tree.setCurrentItem(None)
        self.station_tree.setUpdatesEnabled(False)
        try:
            for station_id in station_ids:
                record = self.station_record_by_id.get(station_id)
                old_item = self.station_items.get(station_id)
                if record is None or old_item is None:
                    continue
                was_selected = old_item.isSelected()
                if was_selected:
                    old_item.setSelected(False)
                old_parent = old_item.parent()
                old_path = self._station_group_path(old_parent)
                if old_path:
                    for length in range(1, len(old_path) + 1):
                        prefix = old_path[:length]
                        affected_paths.add(prefix)
                        group = self.station_groups.get(prefix)
                        if group:
                            self.station_members.setdefault(id(group), set()).discard(
                                station_id
                            )
                control = self.station_tree.itemWidget(old_item, 1)
                self.station_tree.removeItemWidget(old_item, 1)
                if control is not None:
                    control.deleteLater()
                children = old_item.takeChildren()
                self.station_items.pop(station_id, None)
                self.station_members.pop(id(old_item), None)
                detached = None
                if old_parent:
                    detached = old_parent.takeChild(old_parent.indexOfChild(old_item))
                self._apply_station_override(record)
                new_path = self._station_path(record)
                clean = new_path[1:] if new_path and new_path[0] == "已归档" else new_path
                if clean:
                    self.station_folder_paths.add(tuple(clean))
                new_parent = self._ensure_station_group(new_path)
                item = QTreeWidgetItem(new_parent, [record["name"], ""])
                item.addChildren(children)
                item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
                item.setData(0, Qt.ItemDataRole.UserRole, station_id)
                item.setToolTip(0, self._station_tooltip(record))
                control = Switch(self.station_is_visible(record))
                control.setEnabled(not record.get("archived", False))
                control.toggled.connect(
                    lambda on, station=record: self.toggle_station(station, on)
                )
                self.station_tree.setItemWidget(item, 1, control)
                self.station_items[station_id] = item
                self.station_members[id(item)] = {station_id}
                for length in range(1, len(new_path) + 1):
                    prefix = new_path[:length]
                    affected_paths.add(prefix)
                    group = self.station_groups[prefix]
                    self.station_members.setdefault(id(group), set()).add(station_id)
                item.setSelected(was_selected)
                del detached

            for path in sorted(affected_paths, key=len, reverse=True):
                group = self.station_groups.get(path)
                if group is None or group.childCount():
                    continue
                parent = group.parent() or self.station_tree.invisibleRootItem()
                detached = parent.takeChild(parent.indexOfChild(group))
                self.station_members.pop(id(group), None)
                self.station_group_paths.pop(id(group), None)
                del self.station_groups[path]
                clean = path[1:] if path and path[0] == "已归档" else path
                self.station_folder_paths.discard(tuple(clean))
                del detached
            for path in sorted(affected_paths, key=len):
                group = self.station_groups.get(path)
                if group is not None:
                    group.setText(0, f"{path[-1]} · {group.childCount()} 项")
            self.station_tree.schedule_height()
        finally:
            self.station_tree.setUpdatesEnabled(True)

    def station_owner_for_node(self, osm_node_id):
        """Return and lazily materialise a station/box owner for a map rail point."""
        key = f"node/{osm_node_id}"
        if key not in self.station_items:
            owner = rail_switch_owner(
                self.directory, osm_node_id, self.regions, self.overrides
            )
            if owner is not None:
                key = owner["id"]
                member_ids = set(owner.get("member_switch_ids", []))
                previous = self.transient_station_records.get(key)
                if previous:
                    member_ids.update(previous.get("member_switch_ids", []))
                owner["member_switch_ids"] = sorted(member_ids)
                self.transient_station_records[key] = owner
                self.selected_switch_owners[int(osm_node_id)] = key
            else:
                records, _total = rail_station_records(
                    self.directory,
                    self.regions,
                    str(osm_node_id),
                    5,
                    self.overrides,
                )
                direct = next(
                    (record for record in records if record["id"] == key), None
                )
                if direct:
                    self.transient_station_records[key] = direct
            record = self.transient_station_records.get(key)
            if record is not None:
                self.station_tree.setUpdatesEnabled(False)
                try:
                    self._insert_station_record(record)
                finally:
                    self.station_tree.setUpdatesEnabled(True)
        return key if key in self.station_items else None

    def select_station(self, osm_node_id):
        key = self.station_owner_for_node(osm_node_id)
        item = self.station_items.get(key) if key else None
        if not item:
            return False
        ancestor = item.parent()
        while ancestor:
            ancestor.setExpanded(True)
            ancestor = ancestor.parent()
        self.tabs.setCurrentWidget(self.station_page)
        self.station_tree.setCurrentItem(item)
        self.station_tree.scrollToItem(item)
        return True

    def signal_box_line_candidates(self, switch_ids):
        switch_ids = sorted({int(value) for value in switch_ids})
        if len(switch_ids) < 2:
            raise ValueError("请至少选择两个道岔")
        database = self.directory / "rail_lines.sqlite"
        if not database.exists():
            raise ValueError("铁路拓扑索引尚未建立")
        marks = ",".join("?" for _ in switch_ids)
        with sqlite3.connect(database) as db:
            rows = db.execute(
                "SELECT DISTINCT l.id,l.source_name,a.source_id,a.node_id "
                "FROM node_aliases a JOIN line_nodes n ON n.node_id=a.node_id "
                "JOIN lines l ON l.id=n.line_id "
                f"WHERE a.source_id IN ({marks}) ORDER BY l.source_name,l.id",
                switch_ids,
            ).fetchall()
        result = {}
        for line_id, source_name, source_id, anchor in rows:
            result.setdefault(line_id, {
                "line_id": line_id,
                "name": self.display_name(line_id) if line_id in self.catalog else source_name,
                "anchors": {},
            })["anchors"][int(source_id)] = anchor
        return list(result.values())

    def create_signal_box(self, name, switch_ids, line_ids):
        name = str(name).strip()
        if not name:
            raise ValueError("线路所名称不能为空")
        switch_ids = sorted({int(value) for value in switch_ids})
        candidates = {value["line_id"]: value for value in self.signal_box_line_candidates(switch_ids)}
        selected = list(dict.fromkeys(str(value) for value in line_ids))
        if not selected:
            raise ValueError("至少选择一条经过线路所的业务线路")
        if any(value not in candidates for value in selected):
            raise ValueError("所选线路没有通过这些真实道岔节点")
        source = self.directory / "rail.sqlite"
        marks = ",".join("?" for _ in switch_ids)
        with sqlite3.connect(source) as db:
            rows = db.execute(
                "SELECT json_extract(data,'$.properties.osm_node_id'),"
                "json_extract(data,'$.geometry.coordinates[0]'),"
                "json_extract(data,'$.geometry.coordinates[1]') "
                "FROM features WHERE kind='railPoints' "
                f"AND json_extract(data,'$.properties.osm_node_id') IN ({marks}) "
                "AND json_extract(data,'$.properties.kind')='switch'",
                switch_ids,
            ).fetchall()
        if len(rows) != len(switch_ids):
            raise ValueError("选择中包含非道岔对象或道岔已从数据源删除")
        xs, ys = [float(row[1]) for row in rows], [float(row[2]) for row in rows]
        if max(xs) - min(xs) > .06 or max(ys) - min(ys) > .06:
            raise ValueError("所选道岔相距过远，不能组成同一个线路所")
        coordinates = [sum(xs) / len(xs), sum(ys) / len(ys)]
        owner = rail_switch_owner(self.directory, switch_ids[0], self.regions, {})
        province = owner.get("province", "省界外 / 待核对") if owner else "省界外 / 待核对"
        city = owner.get("city", "城市待核对") if owner else "城市待核对"
        ident = "signalbox/RSB-" + hashlib.sha256(
            ",".join(map(str, switch_ids)).encode("ascii")
        ).hexdigest()[:16]
        connected = []
        line_names = []
        for line_id in selected:
            candidate = candidates[line_id]
            anchors = candidate["anchors"]
            anchor_source = next(value for value in switch_ids if value in anchors)
            connected.append({
                "line_id": line_id,
                "anchor_node": anchors[anchor_source],
                "distance_m": 0.0,
                "source": "manual-signal-box",
                "verification_status": "user_verified",
            })
            line_names.append(candidate["name"])
        self._save_local_overrides({
            "station:" + ident: {
                "display_name": name,
                "station_type": "线路所",
                "folder_path": [province, city],
                "coordinates": coordinates,
                "member_switch_ids": switch_ids,
                "connected_lines": connected,
                "line_names": line_names,
                "source": "manual",
                "verification_status": "user_verified",
            }
        })
        self.metadata_changed.emit()
        self.populate_station_tree()
        self.select_station_record(ident)
        return ident

    def signal_box_geojson(self):
        features = []
        custom = [
            (key.removeprefix("station:"), value)
            for key, value in self.overrides.items()
            if key.startswith("station:signalbox/")
            and isinstance(value.get("member_switch_ids"), list)
        ]
        source = self.directory / "rail.sqlite"
        if not custom or not source.exists():
            return {"type": "FeatureCollection", "features": []}
        with sqlite3.connect(source) as db:
            for ident, value in custom:
                switch_ids = [int(item) for item in value["member_switch_ids"] if str(item).isdigit()]
                if len(switch_ids) < 2:
                    continue
                marks = ",".join("?" for _ in switch_ids)
                rows = db.execute(
                    "SELECT json_extract(data,'$.geometry.coordinates[0]'),"
                    "json_extract(data,'$.geometry.coordinates[1]') FROM features "
                    "WHERE kind='railPoints' "
                    f"AND json_extract(data,'$.properties.osm_node_id') IN ({marks})",
                    switch_ids,
                ).fetchall()
                if len(rows) < 2:
                    continue
                xs, ys = [float(row[0]) for row in rows], [float(row[1]) for row in rows]
                pad = max(.00012, min(.001, max(max(xs) - min(xs), max(ys) - min(ys)) * .15))
                west, east, south, north = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad
                props = {
                    "infrastructure_id": ident,
                    "name": value.get("display_name") or "未命名线路所",
                    "kind": "signal_box",
                    "member_switch_ids": switch_ids,
                    "line_ids": [
                        item.get("line_id") for item in value.get("connected_lines", [])
                        if isinstance(item, dict) and item.get("line_id")
                    ],
                }
                features.extend([
                    {"type": "Feature", "properties": props, "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[west, south], [east, south], [east, north], [west, north], [west, south]]],
                    }},
                    {"type": "Feature", "properties": props, "geometry": {
                        "type": "Point", "coordinates": [sum(xs) / len(xs), sum(ys) / len(ys)],
                    }},
                ])
        return {"type": "FeatureCollection", "features": features}

    def select_station_record(self, station_id):
        item = self.station_items.get(station_id)
        if item is None:
            return False
        ancestor = item.parent()
        while ancestor:
            ancestor.setExpanded(True)
            ancestor = ancestor.parent()
        self.tabs.setCurrentWidget(self.station_page)
        self.station_tree.setCurrentItem(item)
        self.station_tree.scrollToItem(item)
        return True

    def save_station_override(
        self,
        station_id,
        display_name=None,
        folder_path=None,
        station_type_value=None,
        connected_lines=None,
        overview_attributes=None,
        custom_attributes=None,
        archived=None,
    ):
        record = self.station_record_by_id.get(station_id)
        if not record:
            raise ValueError("车站目录中不存在该对象，请先通过主搜索框定位")
        key = "station:" + station_id
        change = {**self.overrides.get(key, {})}
        if display_name is not None:
            if not display_name.strip():
                raise ValueError("名称不能为空")
            change["display_name"] = display_name.strip()
        if folder_path is not None:
            if not folder_path or any(not str(v).strip() for v in folder_path):
                raise ValueError("车站目录至少需要一级有效文件夹")
            change["folder_path"] = [str(v).strip() for v in folder_path]
        if station_type_value is not None:
            if station_type_value not in STATION_TYPES:
                raise ValueError("车站类型无效")
            change["station_type"] = station_type_value
        if connected_lines is not None:
            normalized = []
            for value in connected_lines:
                if (
                    not isinstance(value, dict)
                    or not isinstance(value.get("line_id"), str)
                    or not value["line_id"].strip()
                    or not isinstance(value.get("anchor_node"), (int, str))
                    or not isinstance(value.get("distance_m"), (int, float))
                    or value["distance_m"] < 0
                ):
                    raise ValueError("接轨线路覆盖数据无效")
                normalized.append(
                    {
                        "line_id": value["line_id"].strip(),
                        "anchor_node": value["anchor_node"],
                        "distance_m": round(float(value["distance_m"]), 1),
                        "source": "manual",
                        "verification_status": "user_verified",
                    }
                )
            if len({value["line_id"] for value in normalized}) != len(normalized):
                raise ValueError("接轨线路不能重复")
            change["connected_lines"] = normalized
        if overview_attributes is not None:
            change["overview_attributes"] = normalize_station_attributes(
                overview_attributes
            )
        if custom_attributes is not None:
            change["custom_attributes"] = normalize_station_attributes(
                custom_attributes, custom=True
            )
        if archived is not None:
            change["archived"] = bool(archived)
        self._save_local_overrides({key: change})
        self.metadata_changed.emit()
        self._refresh_station_items({station_id})
        self.send_station_visibility()

    def save_station_changes(self, station_ids, **changes):
        station_ids = set(station_ids)
        if not station_ids:
            return
        if "folder_path" in changes:
            folder = changes["folder_path"]
            if (
                not isinstance(folder, list)
                or not folder
                or any(not isinstance(value, str) or not value.strip() for value in folder)
            ):
                raise ValueError("请选择有效的目标文件夹")
            changes["folder_path"] = [value.strip() for value in folder]
        self._save_local_overrides({"station:" + station_id: changes for station_id in station_ids})
        self.metadata_changed.emit()
        self._refresh_station_items(station_ids)
        self.send_station_visibility()

    def prefix_station_names(self, station_ids, prefix):
        """Rename a station batch with one atomic write and one tree refresh."""
        station_ids = {
            station_id
            for station_id in station_ids
            if station_id in self.station_record_by_id
        }
        prefix = str(prefix).strip()
        if not station_ids or not prefix:
            return
        changes = {
            "station:" + station_id: {
                "display_name": prefix + self.station_record_by_id[station_id]["name"]
            }
            for station_id in station_ids
        }
        self._save_local_overrides(changes)
        self.metadata_changed.emit()
        self._refresh_station_items(station_ids)
        self.send_station_visibility()

    def save_overrides(self, changes):
        """Persist presentation metadata atomically; never write the GIS source."""
        effective = {}
        for key, change in changes.items():
            if key not in self.catalog:
                raise ValueError("目录项已变化，请重新选择")
            meta = self.meta(key)
            current_path = list(self.parents(key))
            if current_path and current_path[0] == "已归档":
                current_path = current_path[1:]
            values = {}
            for field, value in change.items():
                current = (
                    self.display_name(key)
                    if field == "display_name"
                    else current_path
                    if field == "folder_path"
                    else meta.get(field)
                )
                if value != current:
                    values[field] = value
            if values:
                effective[key] = values
        if not effective:
            return

        self._save_local_overrides(effective)
        self.visible = {
            key for key in self.visible if not self.meta(key).get("archived", False)
        }
        self.send_visibility(False)
        folder_only = all(set(change) <= {"folder_path"} for change in effective.values())
        if not (folder_only and self._move_line_items_in_tree(set(effective))):
            self.populate()
        if effective:
            item = self.items.get(sorted(effective)[0])
            if item:
                ancestor = item.parent()
                while ancestor:
                    ancestor.setExpanded(True)
                    ancestor = ancestor.parent()
                self.tree.setCurrentItem(item)
                self.tree.schedule_height()
                self.tree.scrollToItem(item)

    def archive_items(self, keys, archived=True):
        self.save_overrides({key: {"archived": archived} for key in keys})
        self.note.setText(
            "已归档并关闭地图显示；可在「已归档」目录右键恢复。"
            if archived
            else "已恢复目录项；地图显示仍关闭，可按需打开。"
        )

    def move_items(self, keys, folders):
        keys = set(keys)
        if (
            not isinstance(folders, list)
            or not folders
            or any(not isinstance(f, str) or not f.strip() for f in folders)
        ):
            raise ValueError("请填写有效目录路径，例如：上海市 / 虹桥站 / 站场股道")
        folders = [f.strip() for f in folders]
        if folders[0] == "已归档":
            raise ValueError("「已归档」是保留目录；请使用归档功能")
        for key in keys:
            if key not in self.catalog:
                raise ValueError("目录项已变化，请重新选择")
        self._save_local_overrides({key: {"folder_path": folders} for key in keys})
        self.metadata_changed.emit()
        if not self._move_line_items_in_tree(keys):
            self.populate()
        self.note.setText(
            "已移动目录项：" + " / ".join(folders) + "；原始分类和数据保留。"
        )

    def _line_group_path(self, item):
        return self.line_group_paths.get(id(item)) if item is not None else None

    def _ensure_line_group(self, path):
        parent = self.tree.invisibleRootItem()
        current = ()
        for label in path:
            current += (label,)
            if current not in self.groups:
                self.groups[current] = QTreeWidgetItem(parent, [label, ""])
                self.members[id(self.groups[current])] = set()
                self.line_group_paths[id(self.groups[current])] = current
            parent = self.groups[current]
        return parent

    def _sync_line_group(self, path):
        item = self.groups.get(path)
        if item is None:
            return
        keys = self.members.setdefault(id(item), set())
        displayed = {id(self.items[key]) for key in keys if key in self.items}
        item.setText(0, f"{path[-1]} · {len(displayed)} 项")
        control = self.tree.itemWidget(item, 1)
        if control is None:
            control = Switch(False)
            control.toggled.connect(lambda on, values=keys: self.toggle_group(values, on))
            self.tree.setItemWidget(item, 1, control)
        visible = {key for key in keys if self.is_visible(key)}
        control.blockSignals(True)
        control.setChecked(bool(visible))
        control.setMixed(bool(visible) and visible != keys)
        control.setEnabled(
            not self.catalog_limited
            and bool(keys)
            and not all(self.meta(key).get("archived", False) for key in keys)
        )
        control.blockSignals(False)

    def _move_line_items_in_tree(self, keys):
        items = {id(self.items[key]): self.items[key] for key in keys if key in self.items}
        moves = []
        for item in items.values():
            members = set(self.members.get(id(item), set()))
            if not members or not members.issubset(keys):
                return False
            destinations = {self.parents(key) for key in members}
            if len(destinations) != 1:
                return False
            moves.append((item, members, destinations.pop()))
        if not moves:
            return True

        affected = set()
        self.tree.setCurrentItem(None)
        self.tree.setUpdatesEnabled(False)
        try:
            for item, members, destination in moves:
                label = item.text(0)
                tooltip = item.toolTip(0)
                leaf = item.data(0, Qt.ItemDataRole.UserRole)
                old_parent = item.parent()
                old_path = self._line_group_path(old_parent)
                if old_path:
                    for length in range(1, len(old_path) + 1):
                        prefix = old_path[:length]
                        affected.add(prefix)
                        group = self.groups.get(prefix)
                        if group:
                            self.members.setdefault(id(group), set()).difference_update(
                                members
                            )
                was_selected = item.isSelected()
                if was_selected:
                    item.setSelected(False)
                control = self.tree.itemWidget(item, 1)
                self.tree.removeItemWidget(item, 1)
                if control is not None:
                    control.deleteLater()
                self.members.pop(id(item), None)
                detached = None
                if old_parent:
                    detached = old_parent.takeChild(old_parent.indexOfChild(item))
                new_parent = self._ensure_line_group(destination)
                item = QTreeWidgetItem(new_parent, [label, ""])
                item.setData(0, Qt.ItemDataRole.UserRole, leaf)
                item.setToolTip(0, tooltip)
                visible_members = {value for value in members if self.is_visible(value)}
                control = Switch(bool(visible_members))
                control.setMixed(
                    bool(visible_members) and len(visible_members) != len(members)
                )
                control.setEnabled(
                    not all(self.meta(value).get("archived", False) for value in members)
                )
                control.toggled.connect(
                    lambda on, values=set(members): self.toggle_group(values, on)
                )
                self.tree.setItemWidget(item, 1, control)
                self.members[id(item)] = set(members)
                for value in members:
                    self.items[value] = item
                for length in range(1, len(destination) + 1):
                    prefix = destination[:length]
                    affected.add(prefix)
                    group = self.groups[prefix]
                    self.members.setdefault(id(group), set()).update(members)
                    group.setExpanded(True)
                item.setSelected(was_selected)
                clean = destination[1:] if destination[0] == "已归档" else destination
                self.line_folder_paths.add(tuple(clean))
                del detached

            for path in sorted(affected, key=len, reverse=True):
                group = self.groups.get(path)
                if group is None or group.childCount():
                    continue
                parent = group.parent() or self.tree.invisibleRootItem()
                control = self.tree.itemWidget(group, 1)
                self.tree.removeItemWidget(group, 1)
                if control is not None:
                    control.deleteLater()
                detached = parent.takeChild(parent.indexOfChild(group))
                self.members.pop(id(group), None)
                self.line_group_paths.pop(id(group), None)
                del self.groups[path]
                clean = path[1:] if path and path[0] == "已归档" else path
                self.line_folder_paths.discard(tuple(clean))
                del detached
            for path in sorted(affected, key=len):
                self._sync_line_group(path)
            self.tree.schedule_height()
            return True
        finally:
            self.tree.setUpdatesEnabled(True)

    def rename_item(self, key, name):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("名称不能为空")
        self.save_overrides({key: {"display_name": name.strip()}})
        line_id = self.catalog[key].get("line_id")
        if line_id:
            self.line_names_changed.emit({line_id: name.strip()})
        self.note.setText("已更新目录显示名称；原始名称、编号和通道引用保留。")

    def rename_items(self, keys, name):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("名称不能为空")
        value = name.strip()
        self.save_overrides({key: {"display_name": value} for key in keys})
        line_names = {
            self.catalog[key].get("line_id"): value
            for key in keys
            if self.catalog[key].get("line_id")
        }
        if line_names:
            self.line_names_changed.emit(line_names)
        self.note.setText(
            f"已统一更新 {len(keys)} 个同名线路片段；稳定编号和通道引用保留。"
        )

    def rename_folder(self, path, name):
        if (
            not isinstance(name, str)
            or not name.strip()
            or "/" in name
            or name.strip() == "已归档"
        ):
            raise ValueError("请填写有效文件夹名称（不能包含 / 或使用保留名称）")
        prefix = tuple(path[1:]) if path and path[0] == "已归档" else tuple(path)
        if not prefix:
            raise ValueError("归档目录不能重命名")
        changes = {}
        for key in self.catalog:
            parents = self.parents(key)
            if parents and parents[0] == "已归档":
                parents = parents[1:]
            if parents[: len(prefix)] == prefix and self.meta(key).get(
                "archived", False
            ) == (path[0] == "已归档"):
                changes[key] = {
                    "folder_path": [*prefix[:-1], name.strip(), *parents[len(prefix) :]]
                }
        self.save_overrides(changes)

    def context_menu(self, position):
        item = self.tree.itemAt(position)
        if not item:
            return
        selected = self.tree.selectedItems()
        if item not in selected:
            self.tree.clearSelection()
            self.tree.setCurrentItem(item)
            selected = [item]
        keys = {
            key
            for selected_item in selected
            for key in self.members.get(id(selected_item), set())
        }
        menu = self.item_menu(item, keys)
        menu.exec(self.tree.viewport().mapToGlobal(position))
        menu.deleteLater()

    def item_menu(self, item, selected_keys=None):
        keys = set(selected_keys or self.members.get(id(item), set()))
        leaf = item.data(0, Qt.ItemDataRole.UserRole)
        folder = next(
            (path for path, candidate in self.groups.items() if candidate is item), None
        )
        menu = QMenu(self)

        def perform(action):
            try:
                action()
            except (ValueError, OSError) as error:
                QMessageBox.warning(self, "目录修改未保存", str(error))

        def rename():
            old = (
                self.meta(leaf).get("display_name", self.meta(leaf).get("name", leaf))
                if leaf
                else folder[-1]
            )
            value, accepted = QInputDialog.getText(
                self,
                "重命名目录项" if leaf else "重命名文件夹",
                "显示名称（原始数据保留）",
                text=old,
            )
            if accepted:
                perform(
                    lambda: (
                        self.rename_items(keys, value)
                        if leaf
                        else self.rename_folder(folder, value)
                    )
                )

        rename_action = menu.addAction("重命名…", rename)
        rename_action.setEnabled(bool(leaf or (folder and folder != ("已归档",))))
        move = menu.addMenu("移动到")
        add_folder_move_menu(
            move,
            self.line_destination_paths(),
            lambda path: perform(lambda: self.move_items(keys, path)),
        )
        if leaf:
            menu.addAction(
                "查看线路属性…"
                if self.meta(leaf).get("catalog_group_id")
                else "查看端点与相邻线段…",
                lambda: self.show_topology(leaf),
            )
        menu.addSeparator()
        archived = all(self.meta(key).get("archived", False) for key in keys)
        menu.addAction(
            "取消归档 / 恢复" if archived else "归档",
            lambda: perform(lambda: self.archive_items(keys, not archived)),
        )
        return menu

    def line_destination_paths(self):
        paths = set(self.line_folder_paths)
        paths.update(self.station_destination_paths())
        for path in self.groups:
            clean = path[1:] if path and path[0] == "已归档" else path
            if clean:
                paths.add(tuple(clean))
        for key, value in self.overrides.items():
            folder = value.get("folder_path") if key in self.catalog else None
            if isinstance(folder, list) and folder:
                paths.add(tuple(folder))
        return paths

    def drop_line_items(self, items, target):
        path = next((path for path, folder in self.groups.items() if folder is target), None)
        if path is None and target.parent():
            path = next(
                (path for path, folder in self.groups.items() if folder is target.parent()),
                None,
            )
        keys = {
            key for item in items for key in self.members.get(id(item), set())
        }
        if path and keys:
            clean = list(path[1:] if path[0] == "已归档" else path)
            self.move_items(keys, clean)

    def show_topology(self, key):
        meta = self.meta(key)
        if meta.get("catalog_group_id"):
            QMessageBox.information(
                self,
                "线路属性",
                f"目录对象：{self.display_name(key)}\n"
                f"目录编号：{meta['catalog_group_id']}\n"
                f"轨道类型：{meta.get('track_type', '未确认类型')}\n"
                f"端点线段：{meta.get('section_count', 0)} 项\n"
                f"NetworkEdge：{meta.get('edge_count', 0)} 项\n\n"
                "具体 RS 编号、两端点及相邻线段请点击地图上的轨道线段查看。",
            )
            return
        detail = (
            f"线段：{meta.get('name', key)}\n"
            f"线段编号：{meta.get('id', key)}\n"
            f"物理线路：{meta.get('line_display_name', meta.get('line_name', '旧目录未记录'))}\n"
            f"轨道类型：{meta.get('track_type', '未确认类型')}\n\n"
            f"起点：{meta.get('from_name', '旧目录未记录')}\n"
            f"起点编号：{meta.get('from_node', '旧目录未记录')}\n"
            f"相接线段：{', '.join(meta.get('from_adjacent_sections', [])) or '无 / 待重建'}\n\n"
            f"终点：{meta.get('to_name', '旧目录未记录')}\n"
            f"终点编号：{meta.get('to_node', '旧目录未记录')}\n"
            f"相接线段：{', '.join(meta.get('to_adjacent_sections', [])) or '无 / 待重建'}"
        )
        QMessageBox.information(self, "端点与线段属性", detail)

    def filter_tree(self, text):
        query = text.strip().lower()
        self.tree.set_filter_active(bool(query))

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
        stable = record.get("line_id") or record.get("catalog_group_id")
        if stable and label.endswith(" · " + str(stable)):
            label = label[: -(len(str(stable)) + 3)]
        return label

    def reload_names(self, path):
        path = Path(path)
        self.way_names = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        )
        self.populate()

    def toggle_group(self, keys, on):
        keys = {key for key in keys if not self.meta(key).get("archived", False)}
        if self.all_visible:
            self.excluded.difference_update(keys) if on else self.excluded.update(keys)
        else:
            self.visible.update(keys) if on else self.visible.difference_update(keys)
        self.send_visibility(on)
        QTimer.singleShot(0, self.populate)

    def set_all(self, on):
        self.line_masters = {"operating": bool(on), "construction": bool(on)}
        if len(self.catalog) > MAX_CATALOG_TREE_ITEMS:
            self.all_visible = on
            self.excluded = set()
            self.visible = set()
        else:
            self.all_visible = False
            self.excluded = set()
            self.visible = (
                {key for key in self.catalog if not self.meta(key).get("archived", False)}
                if on
                else set()
            )
        self.send_visibility(False)
        self.populate()

    def set_line_master(self, group, on):
        if group not in self.line_masters:
            raise ValueError("铁路总开关类型无效")
        self.line_masters[group] = bool(on)
        construction = {
            key for key in self.catalog if self.meta(key).get("construction")
            and not self.meta(key).get("archived", False)
        }
        if self.line_masters["operating"]:
            self.all_visible = True
            self.visible = set()
            self.excluded = set() if self.line_masters["construction"] else construction
        elif self.line_masters["construction"]:
            self.all_visible = False
            self.excluded = set()
            self.visible = construction
        else:
            self.all_visible = False
            self.excluded = set()
            self.visible = set()
        self.send_visibility(False)
        self.populate()

    def has_visible_lines(self):
        return self.all_visible or bool(self.visible)

    def is_visible(self, name):
        return (
            self.all_visible
            and name not in self.excluded
            and not self.meta(name).get("archived", False)
        ) or name in self.visible

    def toggle(self, name, on):
        if self.meta(name).get("archived", False):
            return
        if self.all_visible:
            self.excluded.discard(name) if on else self.excluded.add(name)
        elif on:
            self.visible.add(name)
        else:
            self.visible.discard(name)
        self.send_visibility(on)
        # Update ancestors without destroying the switch handling the current event.
        for key, item in self.items.items():
            control = self.tree.itemWidget(item, 1)
            control.blockSignals(True)
            control.setChecked(self.is_visible(key))
            control.blockSignals(False)
        for item in self.groups.values():
            control = self.tree.itemWidget(item, 1)
            keys = self.members[id(item)]
            control.blockSignals(True)
            visible_keys = {key for key in keys if self.is_visible(key)}
            control.setChecked(bool(visible_keys))
            control.setMixed(bool(visible_keys) and visible_keys != keys)
            control.blockSignals(False)

    def send_visibility(self, request_enable):
        if self.all_visible:
            hidden = self.excluded | {
                name
                for name in self.catalog
                if self.meta(name).get("archived", False)
            }
            if not hidden:
                self.map.call("setRailSelection", None, None)
            else:
                self._send_catalog_filter(hidden, "setRailExclusions")
            if request_enable:
                self.enabled_requested.emit()
            self.map.call("setRailLineSelection", None)
            return
        active_count = sum(
            not self.meta(name).get("archived", False) for name in self.catalog
        )
        if active_count and len(self.visible) == active_count:
            # Null removes the MapLibre filter. Sending hundreds of thousands
            # of IDs would waste RAM and can exhaust the WebGL process.
            self.map.call("setRailSelection", None, None)
            self.map.call("setRailLineSelection", None)
            if request_enable:
                self.enabled_requested.emit()
            return
        self._send_catalog_filter(self.visible, "setRailSelection")
        self.map.call(
            "setRailLineSelection",
            sorted(
                {
                    self.meta(name).get("line_id")
                    for name in self.visible
                    if self.meta(name).get("line_id")
                }
            ),
        )
        if request_enable:
            self.enabled_requested.emit()

    def _send_catalog_filter(self, names, method):
        sections = [
            self.catalog[name].get("id", name)
            for name in sorted(names)
            if self.catalog[name].get("edge_ids")
        ]
        ids = [
            way
            for name in sorted(names)
            if not self.catalog[name].get("edge_ids")
            for way in self.catalog[name]["way_ids"]
        ]
        groups = [
            self.catalog[name]["catalog_group_id"]
            for name in sorted(names)
            if self.catalog[name].get("catalog_group_id")
        ]
        if groups:
            self.map.call(method, sections, ids, groups)
        else:
            self.map.call(method, sections, ids)

    def focus_item(self, item, column):
        if column != 0:
            return  # The switch column only controls visibility.
        keys = self.members.get(id(item), set())
        if keys:
            if len(keys) == 1:
                key = next(iter(keys))
                props = {**self.meta(key), "display_name": self.display_name(key)}
            else:
                ordered_keys = sorted(keys)
                primary = self.meta(ordered_keys[0])
                props = {
                    **primary,
                    "display_name": item.text(0).split(" · ", 1)[0],
                    "kind": "合并线路目录",
                    "merged_catalog_ids": ordered_keys,
                    "line_count": len(keys),
                    "section_count": sum(
                        self.catalog[key].get("section_count", 0) for key in keys
                    ),
                    "edge_count": sum(
                        self.catalog[key].get("edge_count", 0) for key in keys
                    ),
                    "line_names": [self.display_name(key) for key in ordered_keys[:100]],
                }
            props.update(self.line_relationships(keys))
            self.feature_activated.emit({"layer": "rail", "properties": props})
        edge_ids = sorted(
            {
                edge
                for key in keys
                for edge in self.catalog[key].get("edge_ids", [])
            }
        )
        ways = sorted({w for key in keys for w in self.catalog[key]["way_ids"]})
        group_ids = sorted(
            {
                self.catalog[key]["catalog_group_id"]
                for key in keys
                if self.catalog[key].get("catalog_group_id")
            }
        )
        database = self.directory / "rail.sqlite"
        if not (ways or edge_ids or group_ids) or not database.exists():
            self.note.setText("无法定位：该目录没有可用的铁路几何数据。")
            return
        bounds = []
        try:
            if edge_ids:
                try:
                    from .rail_store import load_edges
                except ImportError:
                    from rail_store import load_edges
                for edge in load_edges(self.directory, edge_ids):
                    xs, ys = zip(*edge["coordinates"])
                    bounds.append((min(xs), min(ys), max(xs), max(ys)))
            # Use the complete disk index, not the currently loaded viewport.
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
                for group_id in group_ids:
                    row = db.execute(
                        "SELECT min(b.minx),min(b.miny),max(b.maxx),max(b.maxy) "
                        "FROM rail_feature_groups g JOIN bounds b ON b.id=g.feature_id "
                        "WHERE g.group_id=?",
                        (group_id,),
                    ).fetchone()
                    if row and row[0] is not None:
                        bounds.append(row)
                for start in range(0, len(ways) if not edge_ids else 0, 500):
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

    def line_relationships(self, keys):
        line_ids = sorted(
            {
                self.catalog[key].get("line_id")
                for key in keys
                if self.catalog[key].get("line_id")
            }
        )
        database = self.directory / "rail_lines.sqlite"
        if not line_ids or not database.exists():
            return {"station_names": [], "connected_line_names": []}
        marks = ",".join("?" for _ in line_ids)
        with sqlite3.connect(database) as db:
            stations, seen = [], set()
            for source_id, alias in db.execute(
                "SELECT a.source_id,a.alias FROM station_aliases a "
                "JOIN line_nodes n ON n.node_id=a.anchor_node "
                f"WHERE n.line_id IN ({marks}) AND a.confidence>=0.5 "
                "ORDER BY a.alias,a.distance_m",
                line_ids,
            ):
                custom = self.overrides.get("station:" + str(source_id), {})
                name = custom.get("display_name") or alias
                normalized = "".join(str(name).split()).removesuffix("站").casefold()
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    stations.append(str(name))
            connected = [
                row[0]
                for row in db.execute(
                    "SELECT DISTINCT l.source_name FROM line_nodes own "
                    "JOIN line_nodes other ON other.node_id=own.node_id "
                    "JOIN lines l ON l.id=other.line_id "
                    f"WHERE own.line_id IN ({marks}) AND other.line_id NOT IN ({marks}) "
                    "ORDER BY l.source_name LIMIT 100",
                    [*line_ids, *line_ids],
                )
                if row[0]
            ]
        return {
            "station_names": stations[:500],
            "connected_line_names": connected,
        }

    def select_way(self, way_id, province=None, section_id=None, group_id=None):
        """Reveal the exact map-selected RS section, with legacy way fallback."""
        if group_id in self.catalog:
            candidates = [group_id]
        elif section_id in self.catalog:
            candidates = [section_id]
        else:
            candidates = [
                key
                for key, record in self.catalog.items()
                if way_id in record.get("way_ids", [])
            ]
        if province:
            preferred = [
                key for key in candidates if self.meta(key).get("province") == province
            ]
            candidates = preferred or candidates
        if not candidates:
            return False
        current = self.tree.currentItem()
        current_key = (
            current.data(0, Qt.ItemDataRole.UserRole) if current is not None else None
        )
        key = current_key if current_key in candidates else sorted(candidates)[0]
        if key not in self.items and len(self.catalog) > MAX_CATALOG_TREE_ITEMS:
            self.search.setText(key)
            self.search_timer.stop()
            self.populate()
        elif self.search.text():
            self.search.clear()
        item = self.items.get(key)
        if item is None:
            return False
        ancestor = item.parent()
        while ancestor:
            ancestor.setExpanded(True)
            ancestor = ancestor.parent()
        self.tree.setCurrentItem(item)
        self.tree.scrollToItem(item)
        self.note.setText("地图已选择：" + item.text(0))
        return True

    def organize(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("国铁线路整理 · 轨道类型 / 业务线路 / 目录")
        dialog.resize(1000, 650)
        layout = QVBoxLayout(dialog)
        layout.addWidget(
            QLabel(
                "核对轨道类型和目录显示名；原始 OSM 与端点拓扑保持不变。"
            )
        )
        names = sorted(self.items)
        if len(names) < len(self.catalog):
            layout.addWidget(
                QLabel(
                    f"为控制内存，本次编辑当前搜索结果中的 {len(names):,} 项；"
                    "可先在左侧搜索具体线路或车站。"
                )
            )
        table = QTableWidget(len(names), 5)
        table.setHorizontalHeaderLabels(
            [
                "稳定编号",
                "业务线路",
                "端点线段数",
                "轨道类型",
                "目录显示名称",
            ]
        )
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(table)
        for row, name in enumerate(names):
            meta = self.meta(name)
            for col, value in enumerate(
                (
                    meta.get("catalog_group_id", name),
                    meta.get("line_display_name")
                    or meta.get("station_name")
                    or meta.get("line_name")
                    or name,
                    str(meta.get("section_count", 1)),
                    meta.get("track_type", "未确认类型"),
                    meta.get("display_name", self.catalog[name].get("name", name)),
                )
            ):
                item = QTableWidgetItem(value)
                if col < 3:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row, col, item)
            kind = QComboBox()
            kind.addItems(TRACK_TYPES)
            kind.setCurrentText(meta.get("track_type", "未确认类型"))
            table.setCellWidget(row, 3, kind)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        layout.addWidget(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        changes = {
            name: {
                "track_type": table.cellWidget(row, 3).currentText(),
                "display_name": table.item(row, 4).text().strip()
                or self.catalog[name].get("name", name),
            }
            for row, name in enumerate(names)
        }
        try:
            self._save_local_overrides(changes)
            self.metadata_changed.emit()
            self.populate()
            changed_names = {
                self.catalog[name].get("line_id"): changes[name]["display_name"]
                for name in names
                if self.catalog[name].get("line_id")
            }
            if changed_names:
                self.line_names_changed.emit(changed_names)
        except OSError as error:
            QMessageBox.warning(self, "无法保存", str(error))
