"""Reusable corridor catalog integrated into the national railway running panel."""

from copy import deepcopy
import sqlite3
from uuid import uuid4
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QScrollArea,
    QLineEdit,
    QTreeWidgetItem,
    QPushButton,
    QHBoxLayout,
    QDialog,
    QFormLayout,
    QTableWidget,
    QDialogButtonBox,
    QMessageBox,
    QHeaderView,
    QComboBox,
    QCompleter,
    QMenu,
)

try:
    from .components import GrowingTree, text_label, Switch
    from .rail_lines import RESOLUTION_KEY
except ImportError:
    from components import GrowingTree, text_label, Switch
    from rail_lines import RESOLUTION_KEY


class SearchChoice(QComboBox):
    """At most 100 search candidates, regardless of the national dataset size."""

    def __init__(self, search, placeholder, value=None, label=""):
        super().__init__()
        self.search = search
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setMinimumWidth(0)
        self.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.setMinimumContentsLength(10)
        self.setMaxVisibleItems(12)
        self.lineEdit().setPlaceholderText(placeholder)
        self.completer().setCompletionMode(
            QCompleter.CompletionMode.UnfilteredPopupCompletion
        )
        self.completer().activated[str].connect(self.select_result)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self.find_results)
        self.lineEdit().textEdited.connect(lambda: self._search_timer.start(220))
        if value is not None:
            self.addItem(label, value)
        else:
            self.setCurrentIndex(-1)

    def select_result(self, text):
        index = self.findText(text)
        if index >= 0:
            self.setCurrentIndex(index)

    def find_results(self):
        query = self.currentText().strip()
        try:
            choices = self.search(query)
        except (ValueError, sqlite3.Error):
            choices = []
        self.blockSignals(True)
        self.clear()
        for key, label in choices:
            self.addItem(label if isinstance(key, str) else f"{label} · {key}", key)
        self.setCurrentIndex(-1)
        self.setEditText(query)
        self.blockSignals(False)
        if self.lineEdit().hasFocus():
            self.completer().complete()

    def showPopup(self):
        if not self.count():
            for key, label in self.search(""):
                self.addItem(label if isinstance(key, str) else f"{label} · {key}", key)
            self.setCurrentIndex(-1)
        super().showPopup()


class CorridorPanel(QScrollArea):
    selected = Signal(str, str)

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 5, 0)
        layout.addWidget(
            text_label("完整物理路径 → 单向 Corridor → 多个 TrainRun", "muted", True)
        )
        layout.addWidget(
            text_label(
                "运行通道不是“八纵八横”规划分类。点击通道或车次，在主地图显示共享径路。",
                wrap=True,
            )
        )
        self.search = QLineEdit()
        self.search.setPlaceholderText("筛选运行通道 / 车次")
        self.search.textChanged.connect(self.refresh)
        layout.addWidget(self.search)
        self.tree = GrowingTree()
        self.tree.setColumnCount(2)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(1, 58)
        self.tree.setHeaderHidden(True)
        self.tree.itemClicked.connect(self.choose)
        self.tree.itemDoubleClicked.connect(
            lambda item, column: self.edit_table(item.data(0, Qt.ItemDataRole.UserRole))
        )
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.context_menu)
        layout.addWidget(self.tree)
        edit = QPushButton("编辑端点—线路通道表格…")
        edit.clicked.connect(self.edit_selected)
        layout.addWidget(edit)
        create = QPushButton("新建单向通道…")
        create.clicked.connect(lambda: self.edit_table(None))
        layout.addWidget(create)
        save = QPushButton("保存通道与国铁车次")
        save.clicked.connect(self.save)
        layout.addWidget(save)
        self.note = text_label(
            "通道导入 / 导出在“文件”菜单。新增车次只引用现有通道。", wrap=True
        )
        layout.addWidget(self.note)
        layout.addStretch()
        self.setWidget(body)
        self._signature = None
        editor.updated.connect(self.refresh)
        self.refresh()

    def refresh(self, *args):
        if not self.editor.rail_payload:
            return
        # Timer updates may happen 10 times/sec: do not serialize the complete plan or rebuild a focused tree.
        payload = self.editor.rail_payload
        signature = (
            id(payload),
            tuple(t["id"] for t in self.editor.plan.trains),
            self.search.text(),
            tuple(sorted(self.editor.visible_corridors)),
        )
        if signature == self._signature:
            return
        self._signature = signature
        selected = self.tree.currentItem()
        selected_id = selected.data(0, Qt.ItemDataRole.UserRole) if selected else None
        expanded = {
            self.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
            for i in range(self.tree.topLevelItemCount())
            if self.tree.topLevelItem(i).isExpanded()
        }
        self.tree.clear()
        query = self.search.text().strip().lower()
        for route in payload.get("routes", []):
            trains = [
                t
                for t in self.editor.plan.trains
                if self.editor.plan.lines[t["line_id"]].get("corridor_id")
                == route["id"]
            ]
            name = route.get("name", route["id"])
            if (
                query
                and query
                not in (name + route["id"] + " ".join(t["id"] for t in trains)).lower()
            ):
                continue
            root = QTreeWidgetItem(self.tree, [f"{name} · {len(trains)} 车次"])
            root.setData(0, Qt.ItemDataRole.UserRole, route["id"])
            switch = Switch(route["id"] in self.editor.visible_corridors)
            switch.toggled.connect(lambda on, ident=route["id"]: self.editor.set_corridor_visible(ident, on))
            self.tree.setItemWidget(root, 1, switch)
            root.setToolTip(
                0,
                f"{route['id']}\n{len(route['path'])} 个真实物理区间 · 完整连续 · 单向",
            )
            root.setExpanded(bool(query) or route["id"] in expanded)
            if route["id"] == selected_id:
                self.tree.setCurrentItem(root)
            for train in trains:
                child = QTreeWidgetItem(root, [train["id"]])
                child.setData(0, Qt.ItemDataRole.UserRole, route["id"])
                child.setData(0, Qt.ItemDataRole.UserRole + 1, train["id"])
        self.tree.schedule_height()

    def choose(self, item, column):
        self.selected.emit(
            item.data(0, Qt.ItemDataRole.UserRole),
            item.data(0, Qt.ItemDataRole.UserRole + 1) or "",
        )

    def focus_item(self, route_id, train_id=""):
        """Reveal the map-selected corridor or TrainRun in the left directory."""
        self.search.clear()
        self.refresh()
        for index in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(index)
            if root.data(0, Qt.ItemDataRole.UserRole) != route_id:
                continue
            target = root
            if train_id:
                for child_index in range(root.childCount()):
                    child = root.child(child_index)
                    if child.data(0, Qt.ItemDataRole.UserRole + 1) == train_id:
                        target = child
                        break
            root.setExpanded(True)
            self.tree.setCurrentItem(target)
            self.tree.scrollToItem(target)
            return True
        return False

    def context_menu(self, position):
        item = self.tree.itemAt(position)
        if not item:
            return
        route_id = item.data(0, Qt.ItemDataRole.UserRole)
        train_id = item.data(0, Qt.ItemDataRole.UserRole + 1)
        menu = QMenu(self)
        menu.addAction("查看引用关系…", lambda: self.show_references(route_id, train_id))
        menu.exec(self.tree.viewport().mapToGlobal(position))
        menu.deleteLater()

    def show_references(self, route_id, selected_train=""):
        route = next(r for r in self.editor.rail_payload["routes"] if r["id"] == route_id)
        trains = [
            train["id"] for train in self.editor.rail_payload["trains"]
            if train["route_id"] == route_id
        ]
        station_routes = sorted({
            stop["station_route_id"]
            for train in self.editor.rail_payload["trains"] if train["route_id"] == route_id
            for stop in train["stops"] if stop.get("station_route_id")
        })
        detail = (
            f"Corridor：{route.get('name', route_id)}\n"
            f"稳定编号：{route_id}\n"
            f"物理 NetworkEdge：{len(route['path'])} 个\n"
            f"引用 TrainRun：{', '.join(trains) if trains else '无'}\n"
            f"引用 StationRoute：{', '.join(station_routes) if station_routes else '无'}"
        )
        if selected_train:
            detail += f"\n当前车次：{selected_train}"
        QMessageBox.information(self, "通道引用关系", detail)

    def save(self):
        self.editor.save()
        self.note.setText(self.editor.message.text())

    def edit_selected(self):
        item = self.tree.currentItem()
        if not item:
            self.note.setText("请先在目录中选择一个运行通道")
            return
        self.edit_table(item.data(0, Qt.ItemDataRole.UserRole))

    def edit_table(self, ident):
        try:
            library = self.editor.line_library(interactive=True)
        except (OSError, ValueError, sqlite3.Error) as error:
            QMessageBox.warning(self, "铁路基础设施未就绪", str(error))
            return
        route = next(
            (r for r in self.editor.rail_payload["routes"] if r["id"] == ident), None
        )
        dialog = QDialog(self)
        dialog.setWindowTitle("单向通道 · 端点—铁路线—端点")
        dialog.resize(1100, 620)
        dialog.setStyleSheet(
            "QDialog { background: #f5f9fa; } QTableWidget { background: white; border: 1px solid #d4e2e7; border-radius: 8px; gridline-color: #e5eef1; } QHeaderView::section { background: #e7f3f1; padding: 10px; border: 0; color: #245c60; }"
        )
        form = QFormLayout(dialog)
        form.setContentsMargins(20, 18, 20, 18)
        form.setVerticalSpacing(12)
        heading = text_label("通道编排")
        heading.setStyleSheet("font-size: 22px; font-weight: 600; color: #163d46;")
        form.addRow(heading)
        code = QLineEdit(ident or "COR-" + uuid4().hex[:12].upper())
        code.setObjectName("corridorId")
        code.setPlaceholderText("唯一编号，例如 COR-JINGHU-DOWN")
        code.setReadOnly(bool(ident))
        form.addRow("通道编号", code)
        name = QLineEdit(route.get("name", ident) if route else "")
        name.setObjectName("corridorName")
        form.addRow("通道名称", name)
        manual_name = [bool(route)]
        name.textEdited.connect(lambda: manual_name.__setitem__(0, True))
        policy = QComboBox()
        policy.addItem("唯一径路 · 有歧义时选择 RS 区间", "strict")
        form.addRow("拼接方式", policy)
        source = (
            "已导入的全国铁路库"
            if (self.editor.directory / "rail.sqlite").exists()
            else "尚未导入全国铁路库"
        )
        form.addRow(
            text_label(
                "基础设施来源："
                + source
                + "。正式通道只接受唯一径路或明确选择的 RS 区间。",
                wrap=True,
            )
        )
        form.addRow(
            text_label(
                "每行：起点 → 铁路线 → 可选 RS 端点区间 → 终点。唯一径路可不选 RS；多股道或分支必须逐段选择。相邻行共用端点；停站和站台仍由车次时刻表定义。",
                wrap=True,
            )
        )
        sequence = (
            (route.get("sequence") or library.describe(route["path"])) if route else []
        )
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(
            ["起点 / 换线端点", "铁路线（稳定编号）", "精确 RS 区间（有歧义时必选）", "终点 / 换线端点"]
        )
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        def suggest_name():
            if manual_name[0] or not table.rowCount():
                return
            start = table.cellWidget(0, 0)
            end = table.cellWidget(table.rowCount() - 1, 3)
            if (
                start
                and end
                and start.currentData() in library.nodes
                and end.currentData() in library.nodes
            ):
                name.setText(
                    library.nodes[start.currentData()]
                    + " → "
                    + library.nodes[end.currentData()]
                    + " · 单向通道"
                )

        def add_row(a=None, line=None, section=None, b=None):
            row = table.rowCount()
            table.insertRow(row)
            choices = {}
            for col, value in [(0, a), (1, line), (2, section), (3, b)]:
                if col == 1:

                    def search(query):
                        return [
                            (r["id"], r["name"]) for r in library.search_lines(query)
                        ]

                    label = (
                        library.lines[value]["name"] if value in library.lines else ""
                    )
                elif col == 2:

                    def search(query, choices=choices):
                        line_id = choices.get(1).currentData() if choices.get(1) else None
                        return [
                            (item["id"], item["name"])
                            for item in library.search_sections(query, line_id)
                        ]

                    if value:
                        try:
                            label = library.section(value, line)["name"]
                        except (ValueError, KeyError):
                            label = ""
                    else:
                        label = ""
                else:

                    def search(query, choices=choices):
                        return library.search_nodes(
                            query,
                            choices.get(1).currentData() if choices.get(1) else None,
                        )

                    label = (
                        f"{library.nodes[value]} · {value}"
                        if value in library.nodes
                        else ""
                    )
                combo = SearchChoice(
                    search,
                    "搜索线路名称 / 编号"
                    if col == 1
                    else "搜索 RS 稳定区间"
                    if col == 2
                    else "搜索车站 / 线路所 / 道岔编号",
                    value,
                    label,
                )
                choices[col] = combo
                table.setCellWidget(row, col, combo)
                combo.currentIndexChanged.connect(suggest_name)
            table.setRowHeight(row, 52)
            suggest_name()

        for index in range(1, len(sequence), 2):
            add_row(
                sequence[index - 1]["node_id"],
                sequence[index]["line_id"],
                sequence[index].get("section_id"),
                sequence[index + 1]["node_id"],
            )
        if not sequence:
            add_row()
        form.addRow(table)
        actions = QHBoxLayout()
        add = QPushButton("添加线路组合段")
        add.clicked.connect(
            lambda: add_row(
                table.cellWidget(table.rowCount() - 1, 3).currentData()
                if table.rowCount()
                else None,
                None,
                None,
            )
        )
        remove = QPushButton("删除所选组合段")
        remove.clicked.connect(
            lambda: (
                table.removeRow(table.currentRow()) if table.currentRow() >= 0 else None
            )
        )
        actions.addWidget(add)
        actions.addWidget(remove)
        up = QPushButton("上移组合段")
        down = QPushButton("下移组合段")

        def move(delta):
            row = table.currentRow()
            target = row + delta
            if row < 0 or not 0 <= target < table.rowCount():
                return
            # Rebuild the two rows, preserving values without moving Qt-owned widgets.
            values = []
            for index in range(table.rowCount()):
                entries = []
                for col in range(4):
                    combo = table.cellWidget(index, col)
                    value = combo.currentData()
                    if combo.currentText() != combo.itemText(combo.currentIndex()):
                        value = combo.currentText().strip()
                        if col in (0, 3) and value.isdigit():
                            value = int(value)
                    if value is not None and (
                        (col == 1 and value not in library.lines)
                        or (col in (0, 3) and value not in library.nodes)
                    ):
                        self.note.setText("请先选择有效端点和铁路线，再移动组合段。")
                        return
                    entries.append(value)
                values.append(entries)
            values[row], values[target] = values[target], values[row]
            table.setRowCount(0)
            for a, line, section, b in values:
                add_row(a, line, section, b)
            table.selectRow(target)

        up.clicked.connect(lambda: move(-1))
        down.clicked.connect(lambda: move(1))
        actions.addWidget(up)
        actions.addWidget(down)
        form.addRow(actions)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("应用通道")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.rejected.connect(dialog.reject)

        def accept():
            try:
                if not code.text().strip():
                    code.setText("COR-" + uuid4().hex[:12].upper())
                if not name.text().strip():
                    manual_name[0] = False
                    suggest_name()
                if not table.rowCount():
                    raise ValueError("至少添加一行起点—铁路线—终点")
                result = []
                for row in range(table.rowCount()):
                    values = []
                    for col in range(4):
                        combo = table.cellWidget(row, col)
                        value = combo.currentData()
                        if combo.currentText() != combo.itemText(combo.currentIndex()):
                            value = combo.currentText().strip()
                            if col in (0, 3) and value.isdigit():
                                value = int(value)
                        values.append(value)
                    a, line, section, b = values
                    if a is None or line is None or b is None:
                        raise ValueError(f"第 {row + 1} 行：请选择起点、铁路线和终点")
                    if row and result[-1]["node_id"] != a:
                        raise ValueError("相邻组合段必须共用同一个端点")
                    if row == 0:
                        result.append({"kind": "endpoint", "node_id": a})
                    line_entry = {"kind": "line", "line_id": line}
                    if section:
                        line_entry["section_id"] = section
                    result.extend([line_entry, {"kind": "endpoint", "node_id": b}])
                if not name.text().strip():
                    name.setText(library.nodes[result[0]["node_id"]] + " → " + library.nodes[result[-1]["node_id"]] + " · 单向通道")
                payload = {
                    "schema": "railscope.rail-corridors.v2",
                    "source": "用户编辑端点—线路组合；非实际联锁进路",
                    "required_capabilities": [],
                    "extensions": {},
                    "corridors": [
                        {
                            "id": code.text().strip(),
                            "name": name.text().strip(),
                            "sequence": result,
                            "extensions": deepcopy(route["extensions"])
                            if route
                            else {},
                        }
                    ],
                }
                payload["corridors"][0]["extensions"][RESOLUTION_KEY] = {
                    "policy": policy.currentData()
                }
                self.editor.merge_corridors(payload, interactive=True)
                dialog.accept()
                self.refresh()
                for index in range(self.tree.topLevelItemCount()):
                    item = self.tree.topLevelItem(index)
                    if item.data(0, Qt.ItemDataRole.UserRole) == code.text().strip():
                        self.tree.setCurrentItem(item)
                        self.tree.scrollToItem(item)
                        break
                self.note.setText(
                    "通道已应用。点击目录可在地图查看；使用「保存通道与国铁车次」保存到本机。"
                )
                self.selected.emit(code.text().strip(), "")
            except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as error:
                QMessageBox.warning(dialog, "通道未修改", str(error))

        buttons.accepted.connect(accept)
        form.addRow(buttons)
        dialog.exec()
