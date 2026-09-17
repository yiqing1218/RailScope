"""Single-workspace rail corridor catalog, independent from layer and running panels."""

from copy import deepcopy
from PySide6.QtCore import Qt, Signal
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
    QTableWidgetItem,
    QDialogButtonBox,
    QMessageBox,
    QHeaderView,
)

try:
    from .components import GrowingTree, text_label
except ImportError:
    from components import GrowingTree, text_label


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
            text_label("固定轨道 → 单向运行通道 → 多个车次", "muted", True)
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
        self.tree.setHeaderHidden(True)
        self.tree.itemClicked.connect(self.choose)
        layout.addWidget(self.tree)
        edit = QPushButton("编辑通道名称 / 变道信息…")
        edit.clicked.connect(self.edit_selected)
        layout.addWidget(edit)
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
            root.setToolTip(
                0,
                f"{route['id']}\n{len(route['path'])} 个真实物理区间 · 单向 · 变道信息待核对",
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

    def save(self):
        self.editor.save()
        self.note.setText(self.editor.message.text())

    def edit_selected(self):
        item = self.tree.currentItem()
        if not item:
            self.note.setText("请先在目录中选择一个运行通道")
            return
        ident = item.data(0, Qt.ItemDataRole.UserRole)
        payload = self.editor.corridors_document()
        route = next(r for r in payload["corridors"] if r["id"] == ident)
        dialog = QDialog(self)
        dialog.setWindowTitle("编辑单向运行通道 · 不改变已有物理径路")
        dialog.resize(840, 500)
        form = QFormLayout(dialog)
        name = QLineEdit(route.get("name", ident))
        form.addRow("通道名称", name)
        form.addRow(
            text_label(
                "股道留空，道岔节点留空表示未知。位置必须在通道上；尚不执行真实联锁或变道。",
                wrap=True,
            )
        )
        changes = route.get("track_changes", [])
        table = QTableWidget(len(changes), 4)
        table.setHorizontalHeaderLabels(
            ["变道位置 OSM 节点", "原股道", "目标股道", "道岔 OSM 节点"]
        )
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for row, change in enumerate(changes):
            for col, field in enumerate(
                ("node_id", "from_track", "to_track", "via_node")
            ):
                table.setItem(
                    row,
                    col,
                    QTableWidgetItem(
                        "" if change[field] is None else str(change[field])
                    ),
                )
            table.item(row, 0).setData(
                Qt.ItemDataRole.UserRole, deepcopy(change.get("extensions", {}))
            )
        form.addRow(table)
        actions = QHBoxLayout()
        add = QPushButton("添加变道位置")
        add.clicked.connect(lambda: table.insertRow(table.rowCount()))
        remove = QPushButton("删除所选位置")
        remove.clicked.connect(
            lambda: (
                table.removeRow(table.currentRow()) if table.currentRow() >= 0 else None
            )
        )
        actions.addWidget(add)
        actions.addWidget(remove)
        form.addRow(actions)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.rejected.connect(dialog.reject)

        def accept():
            try:
                route["name"] = name.text().strip()
                result = []
                for row in range(table.rowCount()):
                    values = [
                        table.item(row, col).text().strip()
                        if table.item(row, col)
                        else ""
                        for col in range(4)
                    ]
                    if not values[0].isdigit() or (
                        values[3] and not values[3].isdigit()
                    ):
                        raise ValueError("变道位置须为整数节点，道岔节点可空")
                    ext = (
                        table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                        if table.item(row, 0)
                        else {}
                    )
                    result.append(
                        {
                            "node_id": int(values[0]),
                            "from_track": values[1],
                            "to_track": values[2],
                            "via_node": int(values[3]) if values[3] else None,
                            "extensions": ext or {},
                        }
                    )
                route["track_changes"] = result
                payload["corridors"] = [route]
                self.editor.merge_corridors(payload)
                dialog.accept()
                self.refresh()
            except (ValueError, KeyError, TypeError, OSError) as error:
                QMessageBox.warning(dialog, "通道未修改", str(error))

        buttons.accepted.connect(accept)
        form.addRow(buttons)
        dialog.exec()
