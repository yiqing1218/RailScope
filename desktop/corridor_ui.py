"""Reusable corridor catalog integrated into the national railway running panel."""

from copy import deepcopy
import sqlite3
from uuid import uuid4
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
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
    QCheckBox,
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
        self.lineEdit().setAlignment(Qt.AlignmentFlag.AlignLeft)
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
        self.load_choices()
        super().showPopup()

    def load_choices(self):
        if self.count() and not getattr(self, "_choices_dirty", False):
            return
        value, label = self.currentData(), self.currentText()
        self.blockSignals(True)
        try:
            self.clear()
            for key, text in self.search(""):
                self.addItem(text if isinstance(key, str) else f"{text} · {key}", key)
            index = self.findData(value) if value is not None else -1
            if value is not None and index < 0:
                self.addItem(label, value)
                index = self.count() - 1
            self.setCurrentIndex(index)
            self._choices_dirty = False
        finally:
            self.blockSignals(False)

    def wheelEvent(self, event):
        # The endpoint picker can be stepped through without opening its menu.
        self.load_choices()
        if self.count():
            step = -1 if event.angleDelta().y() > 0 else 1
            current = self.currentIndex()
            self.setCurrentIndex(max(0, min(self.count() - 1, current + step)))
            event.accept()
        else:
            super().wheelEvent(event)


class CorridorPanel(QWidget):
    selected = Signal(str, str)

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        layout = QVBoxLayout(self)
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
        self.tree.itemDoubleClicked.connect(self.open_item_editor)
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
            tuple(sorted(self.editor.hidden_trains)),
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
                train_switch = Switch(train["id"] not in self.editor.hidden_trains)
                train_switch.toggled.connect(
                    lambda on, ident=train["id"]: self.editor.set_trains_visible({ident}, on)
                )
                self.tree.setItemWidget(child, 1, train_switch)
        self.tree.schedule_height()

    def choose(self, item, column):
        self.selected.emit(
            item.data(0, Qt.ItemDataRole.UserRole),
            item.data(0, Qt.ItemDataRole.UserRole + 1) or "",
        )

    def open_item_editor(self, item, column):
        train_id = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if train_id:
            self.editor.line_combo.setCurrentIndex(
                self.editor.line_combo.findData("rail/" + train_id)
            )
            self.editor.edit_train_stops()
        else:
            self.edit_table(item.data(0, Qt.ItemDataRole.UserRole))

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
            "QDialog { background: #f5f9fa; } QTableWidget { background: white; border: 1px solid #d4e2e7; border-radius: 8px; gridline-color: #e5eef1; } QHeaderView::section { background: #e7f3f1; padding: 10px; border: 0; color: #245c60; } QComboBox { text-align: left; }"
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
        policy.addItem("唯一径路 · 有歧义时继续添加中间端点", "strict")
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
                + "。正式通道只接受由所选端点和线路唯一确定的连续径路。",
                wrap=True,
            )
        )
        form.addRow(
            text_label(
                "先选起点，再从与该点相连的线路中选一条，随后只列出该线路上可到达的车站或线路所。到达换线点后继续选择与它相连的下一条线路。内部端点分段自动展开，停站和站台仍由车次时刻表定义。",
                wrap=True,
            )
        )
        sequence = (
            (route.get("sequence") or library.describe(route["path"])) if route else []
        )
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(
            ["起点 / 换线端点", "从该点可选的铁路线", "该线路上可到达的下一端点"]
        )
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        physical = QCheckBox("手工轨道端点：显示道岔和真实轨道节点，用于消除多径路歧义")
        physical.setObjectName("corridorPhysicalEndpoints")
        physical.setEnabled(hasattr(library, "workspace"))
        form.addRow(physical)
        physical.toggled.connect(lambda: [setattr(table.cellWidget(row, col), "_choices_dirty", True)
                                          for row in range(table.rowCount()) for col in (0, 2)])
        conflicts = getattr(getattr(library, "workspace", None), "conflicts", [])
        if conflicts:
            form.addRow(text_label(f"有 {len(conflicts)} 个工作区组合缺少源线路成员，请拆分这些组合并重新核对归属。", wrap=True))

        def suggest_name():
            if manual_name[0] or not table.rowCount():
                return
            start = table.cellWidget(0, 0)
            end = table.cellWidget(table.rowCount() - 1, 2)
            if (
                start
                and end
                and library.endpoint_nodes(start.currentData())
                and library.endpoint_nodes(end.currentData())
            ):
                name.setText(
                    library.endpoint_label(start.currentData())
                    + " → "
                    + library.endpoint_label(end.currentData())
                    + " · 单向通道"
                )

        def add_row(a=None, line=None, section=None, b=None):
            row = table.rowCount()
            table.insertRow(row)
            choices = {}
            for col, value in [(0, a), (1, line), (2, b)]:
                if col == 1:

                    def search(query, choices=choices):
                        endpoint = choices.get(0).currentData() if choices.get(0) else None
                        if endpoint is None:
                            return [
                                (r["id"], r["name"])
                                for r in library.search_lines(query)
                            ] if row else []
                        return [
                            (r["id"], r["name"])
                            for r in library.connected_lines(endpoint, query)
                        ]

                    label = (
                        library.lines[value]["name"] if value in library.lines else ""
                    )
                elif col == 2:

                    def search(query, choices=choices):
                        start = choices.get(0).currentData() if choices.get(0) else None
                        selected_line = choices.get(1).currentData() if choices.get(1) else None
                        if start is None or selected_line is None:
                            return []
                        return library.reachable_nodes(
                            start,
                            selected_line,
                            query,
                            **({"physical": physical.isChecked()} if hasattr(library, "workspace") else {}),
                        )

                    label = (
                        (
                            library.endpoint_choice_label(value)
                            if hasattr(library, "endpoint_choice_label")
                            else library.endpoint_label(value)
                        )
                        if value is not None and library.endpoint_nodes(value)
                        else ""
                    )
                else:

                    def search(query):
                        return library.search_endpoints(query, **(
                            {"physical": physical.isChecked()} if hasattr(library, "workspace") else {}
                        ))

                    label = (
                        (
                            library.endpoint_choice_label(value)
                            if hasattr(library, "endpoint_choice_label")
                            else library.endpoint_label(value)
                        )
                        if value is not None and library.endpoint_nodes(value)
                        else ""
                    )
                combo = SearchChoice(
                    search,
                    "搜索线路名称 / 编号"
                    if col == 1
                    else "搜索车站 / 线路所 / 端点编号",
                    value,
                    label,
                )
                choices[col] = combo
                table.setCellWidget(row, col, combo)
                combo.currentIndexChanged.connect(suggest_name)
            start_choice, line_choice, end_choice = (
                choices[0], choices[1], choices[2]
            )

            def clear_choice(combo):
                combo.blockSignals(True)
                combo.clear()
                combo.setCurrentIndex(-1)
                combo.blockSignals(False)

            def start_changed():
                line_choice.setProperty("sectionId", None)
                clear_choice(line_choice)
                clear_choice(end_choice)

            def line_changed():
                line_choice.setProperty("sectionId", None)
                clear_choice(end_choice)
                if row and hasattr(library, "common_transfer_endpoint"):
                    previous_start = table.cellWidget(row - 1, 0).currentData()
                    previous_line = table.cellWidget(row - 1, 1).currentData()
                    previous_end = table.cellWidget(row - 1, 2)
                    next_line = line_choice.currentData()
                    if (previous_end.currentData() is None and previous_start is not None
                            and previous_line is not None and next_line is not None):
                        inferred = library.common_transfer_endpoint(previous_start, previous_line, next_line)
                        if inferred is not None:
                            label = library.endpoint_choice_label(inferred)
                            previous_end.addItem(label, inferred)
                            previous_end.setCurrentIndex(previous_end.count() - 1)

            start_choice.currentIndexChanged.connect(start_changed)
            line_choice.currentIndexChanged.connect(line_changed)
            end_choice.currentIndexChanged.connect(lambda: line_choice.setProperty("sectionId", None))
            line_choice.setProperty("sectionId", section)
            if section:
                line_choice.setToolTip("已指定物理区间：" + section)
            if row:
                previous_end = table.cellWidget(row - 1, 2)

                def sync_start():
                    value = previous_end.currentData()
                    selected_line = line_choice.currentData()
                    start_choice.blockSignals(True)
                    start_choice.clear()
                    if value is not None and library.endpoint_nodes(value):
                        start_choice.addItem(library.endpoint_label(value), value)
                        start_choice.setCurrentIndex(0)
                    else:
                        start_choice.setCurrentIndex(-1)
                    start_choice.blockSignals(False)
                    if selected_line is None or value is None or selected_line not in {
                        candidate["id"] for candidate in library.connected_lines(value)
                    }:
                        start_changed()

                previous_end.currentIndexChanged.connect(sync_start)
                start_choice.setEnabled(False)
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
                table.cellWidget(table.rowCount() - 1, 2).currentData()
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
        choose_section = QPushButton("选择本行物理区间…")

        def select_section():
            row = table.currentRow() if table.currentRow() >= 0 else table.rowCount() - 1
            if row < 0:
                return
            line_choice = table.cellWidget(row, 1)
            line = line_choice.currentData()
            if line not in library.lines:
                QMessageBox.information(dialog, "选择物理区间", "请先为这一行选择铁路线。")
                return
            picker = QDialog(dialog)
            picker.setWindowTitle("选择真实物理区间与方向")
            picker.resize(780, 220)
            layout = QFormLayout(picker)
            choice = SearchChoice(
                lambda query: [(entry["id"], entry["name"] + " · " + entry["id"])
                               for entry in library.search_sections(query, line)],
                "搜索区间、道岔或端点编号",
            )
            direction = QComboBox()
            direction.addItems(["区间起点 → 终点", "区间终点 → 起点"])
            layout.addRow("物理区间", choice)
            layout.addRow("方向", direction)
            layout.addRow(text_label("按实际拓扑决策点分段；选择后本行使用区间的真实起终点。", wrap=True))
            controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            controls.accepted.connect(picker.accept)
            controls.rejected.connect(picker.reject)
            layout.addRow(controls)
            if picker.exec() != QDialog.DialogCode.Accepted:
                return
            try:
                section = library.section(choice.currentData(), line)
                a, b = section["from_node"], section["to_node"]
                if direction.currentIndex():
                    a, b = b, a
                if row:
                    previous = table.cellWidget(row - 1, 2).currentData()
                    if a not in library.endpoint_nodes(previous):
                        raise ValueError("所选区间起点与上一行终点不一致，请先调整上一行或选择其他方向。")

                def assign(combo, value, label):
                    combo.blockSignals(True)
                    combo.clear()
                    combo.addItem(label, value)
                    combo.setCurrentIndex(0)
                    combo.blockSignals(False)

                if row:
                    assign(table.cellWidget(row - 1, 2), a, library.endpoint_label(a))
                assign(table.cellWidget(row, 0), a, library.endpoint_label(a))
                end = table.cellWidget(row, 2)
                assign(end, b, library.endpoint_label(b))
                end.currentIndexChanged.emit(0)
                line_choice.setProperty("sectionId", section["id"])
                line_choice.setToolTip("已指定物理区间：" + section["id"])
            except (ValueError, KeyError) as error:
                QMessageBox.warning(dialog, "区间未选择", str(error))

        choose_section.clicked.connect(select_section)
        actions.addWidget(choose_section)
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
                for col in range(3):
                    combo = table.cellWidget(index, col)
                    value = combo.currentData()
                    if combo.currentText() != combo.itemText(combo.currentIndex()):
                        value = combo.currentText().strip()
                        if col in (0, 2) and value.isdigit():
                            value = int(value)
                    if value is not None and (
                        (col == 1 and value not in library.lines)
                        or (col in (0, 2) and not library.endpoint_nodes(value))
                    ):
                        self.note.setText("请先选择有效端点和铁路线，再移动组合段。")
                        return
                    entries.append(value)
                values.append((*entries, table.cellWidget(index, 1).property("sectionId")))
            values[row], values[target] = values[target], values[row]
            table.setRowCount(0)
            for a, line, b, section in values:
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
                for row in range(1, table.rowCount()):
                    previous_end = table.cellWidget(row - 1, 2)
                    if previous_end.currentData() is not None:
                        continue
                    first_start = table.cellWidget(row - 1, 0).currentData()
                    first_line = table.cellWidget(row - 1, 1).currentData()
                    next_line = table.cellWidget(row, 1).currentData()
                    if first_start is not None and first_line is not None and next_line is not None and hasattr(library, "common_transfer_endpoint"):
                        shared = library.common_transfer_endpoint(first_start, first_line, next_line)
                        if shared is None:
                            raise ValueError(f"第 {row}、{row + 1} 行换线点不唯一或未连通，请手动选择终点")
                        previous_end.addItem(library.endpoint_choice_label(shared), shared)
                        previous_end.setCurrentIndex(previous_end.count() - 1)
                result = []
                for row in range(table.rowCount()):
                    values = []
                    for col in range(3):
                        combo = table.cellWidget(row, col)
                        value = combo.currentData()
                        if combo.currentText() != combo.itemText(combo.currentIndex()):
                            value = combo.currentText().strip()
                            if col in (0, 2) and value.isdigit():
                                value = int(value)
                        values.append(value)
                    a, line, b = values
                    if a is None or line is None or b is None:
                        raise ValueError(f"第 {row + 1} 行：请选择起点、铁路线和终点")
                    if row and result[-1]["node_id"] != a:
                        raise ValueError("相邻组合段必须共用同一个端点")
                    if row == 0:
                        result.append({"kind": "endpoint", "node_id": a})
                    line_entry = {"kind": "line", "line_id": line}
                    section_id = table.cellWidget(row, 1).property("sectionId")
                    if section_id:
                        line_entry["section_id"] = section_id
                    result.extend([
                        line_entry,
                        {"kind": "endpoint", "node_id": b},
                    ])
                if not name.text().strip():
                    name.setText(library.endpoint_label(result[0]["node_id"]) + " → " + library.endpoint_label(result[-1]["node_id"]) + " · 单向通道")
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
                applied = next((route for route in self.editor.rail_payload["routes"] if route["id"] == code.text().strip()), None)
                switches = library.switches_on_path(applied["path"]) if applied and hasattr(library, "switches_on_path") else []
                dialog.accept()
                self.refresh()
                for index in range(self.tree.topLevelItemCount()):
                    item = self.tree.topLevelItem(index)
                    if item.data(0, Qt.ItemDataRole.UserRole) == code.text().strip():
                        self.tree.setCurrentItem(item)
                        self.tree.scrollToItem(item)
                        break
                switch_note = (
                    f"；物理路径经过 {len(switches)} 个真实道岔："
                    + "、".join(
                        f"SW-{entry['source_node_id']}"
                        + (f"（{entry['signal_box']}）" if entry['signal_box'] else "")
                        for entry in switches[:8]
                    )
                    + ("…" if len(switches) > 8 else "")
                ) if switches else "；物理路径未经过已索引道岔"
                self.note.setText("通道已应用" + switch_note + "。可点击目录查看并保存。")
                self.selected.emit(code.text().strip(), "")
            except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as error:
                QMessageBox.warning(dialog, "通道未修改", str(error))

        buttons.accepted.connect(accept)
        form.addRow(buttons)
        dialog.exec()
