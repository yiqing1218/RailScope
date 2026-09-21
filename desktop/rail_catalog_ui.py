"""Topology catalog: track type -> physical line/station -> endpoint section."""

import json
from pathlib import Path
import threading
import sqlite3
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
    QMenu,
    QInputDialog,
    QFormLayout,
    QTabWidget,
)

try:
    from .components import Switch, text_label, GrowingTree
    from .provinces import geographic_catalog, VERSION
    from .rail_categories import catalog_parents, TRACK_TYPES
    from .catalog_metadata import rail_station_records, STATION_TYPES
except ImportError:
    from components import Switch, text_label, GrowingTree
    from provinces import geographic_catalog, VERSION
    from rail_categories import catalog_parents, TRACK_TYPES
    from catalog_metadata import rail_station_records, STATION_TYPES

MAX_CATALOG_TREE_ITEMS = 4000


class RailCatalog(QWidget):
    classified = Signal(dict)
    classification_failed = Signal(str)
    enabled_requested = Signal()
    station_enabled_requested = Signal(str)
    line_names_changed = Signal(dict)
    metadata_changed = Signal()

    def __init__(self, directory, settings, map_view, parent=None, regions=None):
        super().__init__(parent)
        self.map = map_view
        self.directory = Path(directory).resolve()
        self.path = Path(settings)
        source = Path(directory) / "rail_catalog.json"
        self.catalog = (
            json.loads(source.read_text(encoding="utf-8")) if source.exists() else {}
        )
        cache = Path(directory) / "rail_catalog.topology.json"
        if cache.exists():
            value = json.loads(cache.read_text(encoding="utf-8"))
            if value.get("version") == VERSION:
                self.catalog = value["catalog"]
        self.overrides = {}
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
        if self.path.exists():
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("分类设置必须是对象")
                self.overrides = {
                    k: v
                    for k, v in value.items()
                    if isinstance(v, dict)
                    and isinstance(v.get("archived", False), bool)
                    and (
                        "folder_path" not in v
                        or (
                            isinstance(v["folder_path"], list)
                            and bool(v["folder_path"])
                            and all(
                                isinstance(f, str) and f.strip()
                                for f in v["folder_path"]
                            )
                        )
                    )
                }
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
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.context_menu)
        line_layout.addWidget(self.tree)
        line_layout.addStretch(1)
        self.tabs.addTab(self.line_page, "线路目录")
        self.station_page = QWidget()
        station_layout = QVBoxLayout(self.station_page)
        station_layout.setContentsMargins(0, 0, 0, 0)
        station_layout.addWidget(text_label("省 / 市 / 车站实体", "sectionLabel"))
        self.station_tree = GrowingTree()
        self.station_tree.setColumnCount(2)
        self.station_tree.setHeaderHidden(True)
        self.station_tree.setIndentation(12)
        self.station_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.station_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.station_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.station_tree.setColumnWidth(1, 62)
        self.station_tree.itemDoubleClicked.connect(self.focus_station_item)
        station_layout.addWidget(self.station_tree)
        self.station_note = text_label("正在读取车站实体目录…", wrap=True)
        station_layout.addWidget(self.station_note)
        station_layout.addStretch(1)
        self.tabs.addTab(self.station_page, "车站目录")
        layout.addWidget(self.tabs)
        self.note = text_label(
            self.catalog_note(),
            wrap=True,
        )
        layout.addWidget(self.note)
        self.populate()
        self.populate_station_tree()
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
        self.note.setText(
            self.catalog_note("目录已按轨道类型和物理线路/车站重建。")
        )

    def catalog_note(self, prefix=""):
        base = prefix or (
            "按全国铁路业务分类整理；默认完整列出有名称的业务线路。"
            "未命名 OSM 轨道可在主搜索框输入“未命名轨道”后整理。"
            "具体端点线段保存在对象属性和通道编辑器中。"
        )
        if len(self.catalog) > MAX_CATALOG_TREE_ITEMS:
            named = sum(
                not str(key).startswith("ST-")
                and not self.display_name(key).startswith("未命名轨道")
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
        # almost every properly named business line.  Show every matching named
        # line first and leave station-yard groups to the station directory.
        candidates = [
            name
            for name in self.catalog
            if not str(name).startswith("ST-")
            and matches(name)
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
        for name in names:
            record = self.catalog[name]
            meta = self.meta(name)
            parents = self.parents(name)
            parent = self.tree.invisibleRootItem()
            key = ()
            for label in parents:
                key += (label,)
                if key not in groups:
                    groups[key] = QTreeWidgetItem(parent, [label])
                parent = groups[key]
            label = self.display_name(name)
            item = QTreeWidgetItem(parent, [label])
            count = record.get("edge_count") or len(
                record.get("edge_ids", record["way_ids"])
            )
            topology = (
                f"包含 {record.get('section_count', 0)} 个端点线段；点击地图上的具体线段查看两端点和相邻线段。"
                if record.get("catalog_group_id")
                else f"起点：{meta.get('from_name', '旧目录无端点')}\n终点：{meta.get('to_name', '旧目录无端点')}\n"
                f"起点相邻：{', '.join(meta.get('from_adjacent_sections', [])) or '待重建'}\n"
                f"终点相邻：{', '.join(meta.get('to_adjacent_sections', [])) or '待重建'}"
            )
            item.setToolTip(
                0,
                f"{label}\n{meta.get('track_type', '未确认类型')} · {count} 个 NetworkEdge\n"
                f"{topology}\n"
                f"依据：{meta.get('type_evidence', '待核对')}",
            )
            self.items[name] = item
            item.setData(0, Qt.ItemDataRole.UserRole, name)
            self.members[id(item)] = {name}
            ancestor = parent
            while ancestor is not self.tree.invisibleRootItem():
                self.members.setdefault(id(ancestor), set()).add(name)
                ancestor = ancestor.parent() or self.tree.invisibleRootItem()
            switch = Switch(self.is_visible(name))
            switch.setEnabled(not meta.get("archived", False))
            switch.toggled.connect(lambda on, n=name: self.toggle(n, on))
            self.tree.setItemWidget(item, 1, switch)
        for item in groups.values():
            keys = self.members[id(item)]
            item.setText(0, item.text(0) + f" · {len(keys)} 项")
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
        if search_type in ("全部", "铁路线"):
            self.search.blockSignals(True)
            self.search.setText(value)
            self.search.blockSignals(False)
            self.populate()
        if search_type in ("全部", "铁路车站", "线路所及道岔"):
            self.station_query = value
            self.populate_station_tree()
            self.tabs.setCurrentWidget(self.station_page)
        elif search_type == "铁路线":
            self.tabs.setCurrentWidget(self.line_page)

    def populate_station_tree(self):
        self.station_records, self.station_total = rail_station_records(
            self.directory, self.regions, self.station_query, MAX_CATALOG_TREE_ITEMS
        )
        self.station_tree.clear()
        self.station_items = {}
        groups = {}
        for record in self.station_records:
            custom = self.overrides.get("station:" + record["id"], {})
            if custom.get("display_name"):
                record["name"] = custom["display_name"]
            if custom.get("station_type") in STATION_TYPES:
                record["station_type"] = custom["station_type"]
            folder = custom.get("folder_path")
            if isinstance(folder, list) and len(folder) >= 2:
                record["province"], record["city"] = folder[:2]
            parent = self.station_tree.invisibleRootItem()
            path = (record["province"], record["city"])
            current = ()
            for label in path:
                current += (label,)
                if current not in groups:
                    groups[current] = QTreeWidgetItem(parent, [label, ""])
                parent = groups[current]
            label = record["name"]
            item = QTreeWidgetItem(parent, [label, ""])
            item.setData(0, Qt.ItemDataRole.UserRole, record["id"])
            item.setToolTip(
                0,
                f"{label}\n车站类型：{record['station_type']}\n"
                f"所在铁路：{', '.join(record['line_names']) or '待关联'}\n"
                f"稳定编号：{record['id']}",
            )
            control = Switch(self.station_is_visible(record))
            control.toggled.connect(
                lambda on, station=record: self.toggle_station(station, on)
            )
            self.station_tree.setItemWidget(item, 1, control)
            self.station_items[record["id"]] = item
        for path, item in groups.items():
            item.setText(0, item.text(0) + f" · {item.childCount()} 项")
        shown = len(self.station_records)
        suffix = "；结果已限制，请在主地图搜索框继续缩小范围" if shown < self.station_total else ""
        self.station_note.setText(
            f"共匹配 {self.station_total:,} 个车站、线路所或道岔，当前列出 {shown:,} 项{suffix}。"
        )
        self.station_tree.schedule_height()

    @staticmethod
    def station_group(record):
        return "station" if record.get("kind") in {"station", "halt"} else "control"

    def station_is_visible(self, record):
        return (
            self.station_masters[self.station_group(record)]
            and record["id"] not in self.station_excluded
        )

    def set_station_master(self, group, on):
        self.station_masters[group] = bool(on)
        if on:
            self.station_excluded = {
                key
                for key in self.station_excluded
                if next(
                    (
                        self.station_group(record) != group
                        for record in self.station_records
                        if record["id"] == key
                    ),
                    True,
                )
            }
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

    def toggle_station(self, record, on):
        group = self.station_group(record)
        if on and not self.station_masters[group]:
            self.station_enabled_requested.emit(group)
        self.station_excluded.discard(record["id"]) if on else self.station_excluded.add(
            record["id"]
        )
        self.sync_station_switches()
        self.send_station_visibility()

    def send_station_visibility(self):
        hidden = [
            int(value.split("/", 1)[1])
            for value in self.station_excluded
            if value.startswith("node/") and value.split("/", 1)[1].isdigit()
        ]
        self.map.call("setRailPointExclusions", hidden or None)

    def focus_station_item(self, item, column):
        if column != 0:
            return
        station_id = item.data(0, Qt.ItemDataRole.UserRole)
        record = next((r for r in self.station_records if r["id"] == station_id), None)
        if record:
            self.map.call("focus", *record["coordinates"], 15, record["name"])

    def select_station(self, osm_node_id):
        key = f"node/{osm_node_id}"
        item = self.station_items.get(key)
        if not item:
            self.station_query = str(osm_node_id)
            self.populate_station_tree()
            item = self.station_items.get(key)
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

    def save_station_override(
        self, station_id, display_name=None, folder_path=None, station_type_value=None
    ):
        record = next((r for r in self.station_records if r["id"] == station_id), None)
        if not record:
            raise ValueError("车站目录中不存在该对象，请先通过主搜索框定位")
        key = "station:" + station_id
        change = {**self.overrides.get(key, {})}
        if display_name is not None:
            if not display_name.strip():
                raise ValueError("名称不能为空")
            change["display_name"] = display_name.strip()
        if folder_path is not None:
            if len(folder_path) < 2 or any(not str(v).strip() for v in folder_path):
                raise ValueError("车站目录至少需要省和市两级")
            change["folder_path"] = [str(v).strip() for v in folder_path[:2]]
        if station_type_value is not None:
            if station_type_value not in STATION_TYPES:
                raise ValueError("车站类型无效")
            change["station_type"] = station_type_value
        proposed = {**self.overrides, key: change}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(proposed, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)
        self.overrides = proposed
        self.metadata_changed.emit()
        self.populate_station_tree()

    def save_overrides(self, changes):
        """Persist presentation metadata atomically; never write the GIS source."""
        proposed = {**self.overrides}
        for key, change in changes.items():
            if key not in self.catalog:
                raise ValueError("目录项已变化，请重新选择")
            meta = self.meta(key)
            proposed[key] = {
                **self.overrides.get(key, {}),
                **{
                    field: meta[field]
                    for field in (
                        "display_name",
                        "folder_path",
                        "archived",
                        "track_type",
                    )
                    if field in meta
                },
                **change,
            }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(proposed, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)
        self.overrides = proposed
        self.visible = {
            key for key in self.visible if not self.meta(key).get("archived", False)
        }
        self.send_visibility(False)
        self.populate()
        if changes:
            item = self.items.get(sorted(changes)[0])
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
        if (
            not isinstance(folders, list)
            or not folders
            or any(not isinstance(f, str) or not f.strip() for f in folders)
        ):
            raise ValueError("请填写有效目录路径，例如：上海市 / 虹桥站 / 站场股道")
        folders = [f.strip() for f in folders]
        if folders[0] == "已归档":
            raise ValueError("「已归档」是保留目录；请使用归档功能")
        self.save_overrides({key: {"folder_path": folders} for key in keys})
        self.note.setText(
            "已移动目录项：" + " / ".join(folders) + "；原始分类和数据保留。"
        )

    def rename_item(self, key, name):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("名称不能为空")
        self.save_overrides({key: {"display_name": name.strip()}})
        line_id = self.catalog[key].get("line_id")
        if line_id:
            self.line_names_changed.emit({line_id: name.strip()})
        self.note.setText("已更新目录显示名称；原始名称、编号和通道引用保留。")

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
        self.tree.setCurrentItem(item)
        menu = self.item_menu(item)
        menu.exec(self.tree.viewport().mapToGlobal(position))
        menu.deleteLater()

    def item_menu(self, item):
        keys = set(self.members.get(id(item), set()))
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
                        self.rename_item(leaf, value)
                        if leaf
                        else self.rename_folder(folder, value)
                    )
                )

        rename_action = menu.addAction("重命名…", rename)
        rename_action.setEnabled(bool(leaf or (folder and folder != ("已归档",))))
        menu.addAction("移动到文件夹…", lambda: self.move_dialog(keys))
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

    def move_dialog(self, keys):
        if not keys:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("移动目录项 · 原始数据保留")
        dialog.resize(520, 180)
        form = QFormLayout(dialog)
        form.addRow(
            text_label(
                f"移动 {len(keys)} 个目录项。选择已有目录，或输入新的多级路径。",
                wrap=True,
            )
        )
        destination = QComboBox()
        destination.setEditable(True)
        paths = {self.parents(key) for key in self.catalog}
        paths = {p[1:] if p[0] == "已归档" else p for p in paths}
        destination.addItems(sorted(" / ".join(path) for path in paths))
        current = self.parents(sorted(keys)[0])
        if current[0] == "已归档":
            current = current[1:]
        destination.setCurrentText(" / ".join(current))
        form.addRow("目标文件夹", destination)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("移动")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.rejected.connect(dialog.reject)

        def accept():
            try:
                folders = destination.currentText().split("/")
                self.move_items(keys, folders)
                dialog.accept()
            except (ValueError, OSError) as error:
                QMessageBox.warning(dialog, "目录未移动", str(error))

        buttons.accepted.connect(accept)
        form.addRow(buttons)
        dialog.exec()

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
        overrides = dict(self.overrides)
        overrides.update(
            {
                name: {
                **self.overrides.get(name, {}),
                "track_type": table.cellWidget(row, 3).currentText(),
                "display_name": table.item(row, 4).text().strip()
                or self.catalog[name].get("name", name),
            }
            for row, name in enumerate(names)
            }
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(overrides, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp.replace(self.path)
            self.overrides = overrides
            self.metadata_changed.emit()
            self.populate()
            changed_names = {
                self.catalog[name].get("line_id"): overrides[name]["display_name"]
                for name in names
                if self.catalog[name].get("line_id")
            }
            if changed_names:
                self.line_names_changed.emit(changed_names)
        except OSError as error:
            QMessageBox.warning(self, "无法保存", str(error))
