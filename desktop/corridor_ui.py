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
        self.currentIndexChanged.connect(self.reveal_name)
        self.lineEdit().editingFinished.connect(self.reveal_name)
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
        self.reveal_name()

    def reveal_name(self, *args):
        self.setToolTip(self.currentText())
        self.lineEdit().setCursorPosition(0)

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
        self.reveal_name()

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


class CorridorSequenceTable(QTableWidget):
    """Point + outgoing line rows, with one final point and the v2 wire format."""

    changed = Signal()

    def __init__(self, library, parent=None):
        super().__init__(0, 2, parent)
        self.library = library
        self.physical = False
        self._updating = False
        self.setHorizontalHeaderLabels(["点（起点 / 换线点 / 终点）", "线（从本行点到下一行点，终点留空）"])
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

    def options(self):
        return {"physical": self.physical} if hasattr(self.library, "workspace") else {}

    def label(self, column, value):
        if value is None:
            return ""
        if column == 1:
            return self.library.lines[value]["name"] if value in self.library.lines else str(value)
        return self.library.endpoint_label(value)

    def point_choices(self, row, query):
        line = self.cellWidget(row, 1).currentData()
        if row:
            start, previous_line = [self.cellWidget(row - 1, c).currentData() for c in (0, 1)]
            if start is None or previous_line is None:
                return []
            choices = self.library.reachable_nodes(start, previous_line, query, limit=1000, **self.options())
        else:
            choices = self.library.search_endpoints(query, line_id=line, **self.options())
        if line:
            choices = [(key, label) for key, label in choices if line in {
                value["id"] for value in self.library.connected_lines(key, **self.options())}]
        return choices[:100]

    def line_choices(self, row, query):
        point = self.cellWidget(row, 0).currentData()
        records = (self.library.connected_lines(point, query, **self.options()) if point is not None
                   else self.library.search_lines(query))
        return [(record["id"], record["name"]) for record in records]

    def add_point(self, point=None, line=None, section=None):
        row = self.rowCount()
        self.insertRow(row)
        for column, value in ((0, point), (1, line)):
            # Find the current row from the widget; row deletion/reordering must
            # not leave callbacks referring to old numerical positions.
            combo = SearchChoice(lambda q: [], "搜索车站 / 线路所" if column == 0 else "搜索线路（可先选线）", value, self.label(column, value))
            self.setCellWidget(row, column, combo)
            combo.search = lambda q, c=column, w=combo: (self.point_choices if c == 0 else self.line_choices)(self.widget_row(w), q)
            combo.currentIndexChanged.connect(lambda _, c=column, w=combo: self.selection_changed(self.widget_row(w), c))
            combo._choices_dirty = True
        self.cellWidget(row, 1).setProperty("sectionId", section)
        self.setRowHeight(row, 52)

    def widget_row(self, widget):
        return next((r for r in range(self.rowCount()) if widget in [self.cellWidget(r, c) for c in (0, 1)]), -1)

    def assign(self, row, column, value, notify=True):
        combo = self.cellWidget(row, column)
        combo.blockSignals(True)
        combo.clear()
        if value is not None:
            combo.addItem(self.label(column, value), value)
        else:
            combo.setCurrentIndex(-1)
        combo._choices_dirty = True
        combo.blockSignals(False)
        combo.reveal_name()
        if notify:
            self.selection_changed(row, column)

    def selection_changed(self, row, column):
        if self._updating or row < 0:
            return
        self._updating = True
        try:
            self.cellWidget(row, 1).setProperty("sectionId", None)
            if column == 0 and self.cellWidget(row, 0).currentData() is not None:
                selected_line = self.cellWidget(row, 1).currentData()
                if selected_line and selected_line not in dict(self.line_choices(row, "")):
                    self.assign(row, 1, None, notify=False)
            if column == 0 and row:
                self.cellWidget(row - 1, 1).setProperty("sectionId", None)
            if self.cellWidget(self.rowCount() - 1, 1).currentData() is not None:
                self.add_point()
            for r in range(row, self.rowCount()):
                for c in (0, 1):
                    self.cellWidget(r, c)._choices_dirty = True
                if r == row and column == 0:
                    continue
                point = self.cellWidget(r, 0).currentData()
                if point is not None and r:
                    # Query by the selected name so the first 100 other stations
                    # cannot invalidate a distant selected station.
                    allowed = dict(self.point_choices(r, self.library.endpoint_label(point)))
                    if point not in allowed:
                        self.assign(r, 0, None, notify=False)
                        self.cellWidget(max(0, r - 1), 1).setProperty("sectionId", None)
                if r and self.cellWidget(r, 0).currentData() is None and self.cellWidget(r, 1).currentData() is not None:
                    choices = self.point_choices(r, "")
                    stations = [choice for choice in choices if str(choice[0]).startswith("station:")]
                    choices = stations or choices
                    if len(choices) == 1:
                        self.assign(r, 0, choices[0][0], notify=False)
                    elif not choices and hasattr(self.library, "common_transfer_endpoint"):
                        start, previous = [self.cellWidget(r - 1, c).currentData() for c in (0, 1)]
                        if start is not None and previous is not None:
                            inferred = self.library.common_transfer_endpoint(start, previous, self.cellWidget(r, 1).currentData())
                            if inferred is not None:
                                self.assign(r, 0, inferred, notify=False)
        finally:
            self._updating = False
        self.changed.emit()

    def set_sequence(self, sequence):
        self._updating = True
        try:
            self.setRowCount(0)
            for index in range(0, len(sequence), 2):
                line = sequence[index + 1] if index + 1 < len(sequence) else {}
                self.add_point(sequence[index]["node_id"], line.get("line_id"), line.get("section_id"))
            if not sequence:
                self.add_point(); self.add_point()
        finally:
            self._updating = False

    def sequence(self):
        if self.rowCount() < 2:
            raise ValueError("请至少填写起点、线路和下一行终点")
        result = []
        for row in range(self.rowCount()):
            point, line = [self.cellWidget(row, c) for c in (0, 1)]
            value = point.currentData()
            if value is None or point.currentText() != point.itemText(point.currentIndex()):
                raise ValueError(f"第 {row + 1} 行：请从候选中选择有效点")
            result.append({"kind": "endpoint", "node_id": value})
            if row == self.rowCount() - 1:
                if line.currentData() is not None or line.currentText().strip():
                    raise ValueError("终点行的线路应留空；继续运行请添加下一行")
                continue
            if line.currentData() is None or line.currentText() != line.itemText(line.currentIndex()):
                raise ValueError(f"第 {row + 1} 行：请从候选中选择线路")
            entry = {"kind": "line", "line_id": line.currentData()}
            if line.property("sectionId"):
                entry["section_id"] = line.property("sectionId")
            result.append(entry)
        return result

    def remove_point(self, row):
        if row < 0:
            return
        self.removeRow(row)
        if not self.rowCount():
            self.add_point(); self.add_point()
        self.selection_changed(max(0, row - 1), 1)

    def move_point(self, delta):
        row = self.currentRow(); target = row + delta
        if row < 0 or not 0 <= target < self.rowCount():
            return
        values = [(self.cellWidget(r, 0).currentData(), self.cellWidget(r, 1).currentData(),
                   self.cellWidget(r, 1).property("sectionId")) for r in range(self.rowCount())]
        values[row], values[target] = values[target], values[row]
        self.setRowCount(0)
        for point, line, section in values:
            self.add_point(point, line, section)
        self.selection_changed(min(row, target), 1)
        self.selectRow(target)


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
        remove = QPushButton('删除所选通道')
        remove.clicked.connect(self.delete_selected)
        layout.addWidget(remove)
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
        menu.addAction('编辑通道…', lambda: self.edit_table(route_id))
        menu.addAction('删除通道', lambda: self.delete_selected(route_id))
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

    def delete_selected(self, ident=None):
        item = self.tree.currentItem()
        if not isinstance(ident, str):
            ident = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if not ident:
            self.note.setText('请先选择要删除的通道')
            return
        try:
            self.editor.delete_corridor(ident)
            self.refresh()
            self.note.setText('通道已删除，可在编辑菜单撤销')
        except ValueError as error:
            QMessageBox.warning(self, '通道未删除', str(error))

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
        policy.setObjectName("corridorResolutionPolicy")
        policy.addItem("自动选择可走通的参考路径（默认，可手工调整）", "auto")
        policy.addItem("严格唯一径路 · 有歧义时手工选择", "strict")
        saved_resolution = route.get("extensions", {}).get(RESOLUTION_KEY, {}) if route else {}
        if saved_resolution.get("policy") in ("strict", "mainline"):
            policy.setCurrentIndex(1)
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
                + "。默认自动选择连续参考路径，并补齐换线站前后的真实连接轨；需要时可改端点或指定物理区间。",
                wrap=True,
            )
        )
        form.addRow(
            text_label(
                "每行填写“点＋线”，末行只填终点。下一行的点必须在上一行线路上；也可以先选下一行线路，再从两条线路的共同站点中选择换线点。自动补齐站内连接轨，停站和站台仍由车次定义。",
                wrap=True,
            )
        )
        sequence = (
            (route.get("sequence") or library.describe(route["path"])) if route else []
        )
        saved_selection = saved_resolution.get("selection", {})
        if saved_resolution.get("policy") == "auto" and sequence == saved_selection.get("resolved_sequence"):
            sequence = saved_selection.get("requested_sequence", sequence)
        table = CorridorSequenceTable(library)
        table.setObjectName("corridorSequence")
        physical = QCheckBox("手工调整：显示道岔和轨道节点，指定所走股道或中间端点")
        physical.setObjectName("corridorPhysicalEndpoints")
        physical.setEnabled(hasattr(library, "workspace"))
        form.addRow(physical)

        def physical_changed(on):
            table.physical = on
            for row in range(table.rowCount()):
                for col in (0, 1):
                    table.cellWidget(row, col)._choices_dirty = True

        physical.toggled.connect(physical_changed)

        def suggest_name():
            if manual_name[0] or table.rowCount() < 2:
                return
            start = table.cellWidget(0, 0).currentData()
            end = table.cellWidget(table.rowCount() - 1, 0).currentData()
            if start is not None and end is not None:
                name.setText(library.endpoint_label(start) + " → " + library.endpoint_label(end) + " · 单向通道")

        table.changed.connect(suggest_name)
        table.set_sequence(sequence)
        form.addRow(table)
        if route and saved_selection.get("resolved_sequence") != saved_selection.get("requested_sequence"):
            expand = QPushButton("展开已保存的实际轨道路径，手工调整连接轨…")
            expand.setObjectName("corridorExpandResolvedPath")
            def expand_saved():
                physical.setChecked(True)
                table.set_sequence(route.get("sequence", []))
            expand.clicked.connect(expand_saved)
            form.addRow(expand)
        actions = QHBoxLayout()
        add = QPushButton("添加下一行")
        add.clicked.connect(lambda: table.add_point())
        remove = QPushButton("删除所选行")
        remove.clicked.connect(lambda: table.remove_point(table.currentRow()))
        actions.addWidget(add)
        actions.addWidget(remove)
        choose_section = QPushButton("选择本行物理区间…")

        def select_section():
            row = table.currentRow() if table.currentRow() >= 0 else 0
            line = table.cellWidget(row, 1).currentData()
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
                "搜索区间、道岔或端点编号")
            direction = QComboBox()
            direction.addItems(["区间起点 → 终点", "区间终点 → 起点"])
            layout.addRow("物理区间", choice)
            layout.addRow("方向", direction)
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
                physical.setChecked(True)
                if row:
                    previous_point, previous_line = [table.cellWidget(row - 1, c).currentData() for c in (0, 1)]
                    library.resolve([{"kind": "endpoint", "node_id": previous_point},
                                     {"kind": "line", "line_id": previous_line},
                                     {"kind": "endpoint", "node_id": a}], policy.currentData())
                table.assign(row, 0, a, notify=False)
                if row + 1 == table.rowCount():
                    table.add_point()
                table.assign(row + 1, 0, b)
                table.cellWidget(row, 1).setProperty("sectionId", section["id"])
                table.cellWidget(row, 1).setToolTip("已指定物理区间：" + section["id"])
            except (ValueError, KeyError) as error:
                QMessageBox.warning(dialog, "区间未选择", str(error))

        choose_section.clicked.connect(select_section)
        actions.addWidget(choose_section)
        up, down = QPushButton("上移行"), QPushButton("下移行")
        up.clicked.connect(lambda: table.move_point(-1))
        down.clicked.connect(lambda: table.move_point(1))
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
                result = table.sequence()
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
                    **saved_resolution,
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
