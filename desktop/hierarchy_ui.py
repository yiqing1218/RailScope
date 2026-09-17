"""Native province/city/line reparenting dialog with staged, atomic saves."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

try:
    from .components import text_label
    from .hierarchy import label_order
except ImportError:
    from components import text_label
    from hierarchy import label_order


class HierarchyDialog(QDialog):
    def __init__(self, model, parent=None, selected_ids=None):
        super().__init__(parent)
        self.model = model.clone()
        self.setWindowTitle("目录层级设置")
        self.resize(920, 620)
        self.setMinimumSize(740, 520)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(12)
        outer.addWidget(text_label("目录层级设置", "selectedTitle"))
        outer.addWidget(
            text_label(
                "省 / 市 / 线路或工程 · 只改变目录组织，不修改 OSM 属性、颜色或地图坐标。",
                wrap=True,
            )
        )
        body = QHBoxLayout()
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索城市、线路名、工程或 OSM ID")
        self.search.setAccessibleName("筛选目录")
        self.search.textChanged.connect(self.filter_tree)
        ll.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["当前目录", "关系数"])
        self.tree.setColumnWidth(0, 310)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.itemSelectionChanged.connect(self.selection_changed)
        ll.addWidget(self.tree)
        body.addWidget(left, 3)
        right = QFrame()
        right.setObjectName("card")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(16, 16, 16, 16)
        rl.setSpacing(12)
        self.summary = text_label("选择左侧条目", "panelTitle", True)
        rl.addWidget(self.summary)
        self.original = text_label("", wrap=True)
        rl.addWidget(self.original)
        form = QFormLayout()
        form.setSpacing(12)
        self.province = QComboBox()
        self.province.setEditable(True)
        self.province.setAccessibleName("目标省级目录")
        self.city = QComboBox()
        self.city.setEditable(True)
        self.city.setAccessibleName("目标城市目录")
        self.label = QLineEdit()
        self.label.setAccessibleName("线路目录显示名")
        self.province.setMinimumWidth(220)
        self.city.setMinimumWidth(220)
        self.province.currentTextChanged.connect(self.refresh_city_options)
        form.addRow("省级目录", self.province)
        form.addRow("城市目录", self.city)
        form.addRow("线路显示名", self.label)
        rl.addLayout(form)
        rl.addWidget(
            text_label(
                "可选择已有目录，或直接输入新名称。多选 / 选中省市会批量调整下级线路；空白字段保留原值。",
                wrap=True,
            )
        )
        self.apply_button = QPushButton("调整所选条目")
        self.apply_button.setObjectName("primary")
        self.apply_button.clicked.connect(self.move_selected)
        rl.addWidget(self.apply_button)
        self.reset_button = QPushButton("所选条目恢复自动归类")
        self.reset_button.clicked.connect(self.reset_selected)
        rl.addWidget(self.reset_button)
        self.message = text_label(
            "调整后先在左侧预览，保存后主界面立即更新。", "muted", True
        )
        rl.addWidget(self.message)
        rl.addStretch()
        body.addWidget(right, 2)
        outer.addLayout(body, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存并应用")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.save_and_accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.rebuild(set(selected_ids or []))

    def ids(self):
        return set().union(
            *(
                set(i.data(0, Qt.ItemDataRole.UserRole) or [])
                for i in self.tree.selectedItems()
            )
        )

    def rebuild(self, selected_ids=None):
        selected_ids = set(selected_ids or [])
        self.tree.blockSignals(True)
        self.tree.clear()
        self.items = []
        for province, cities in sorted(self.model.grouped().items()):
            p = QTreeWidgetItem([province, ""])
            self.tree.addTopLevelItem(p)
            self.items.append(p)
            pids = set()
            for city, lines in sorted(cities.items()):
                c = QTreeWidgetItem([city, ""])
                p.addChild(c)
                self.items.append(c)
                cids = set()
                for label, routes in sorted(
                    lines.items(), key=lambda entry: label_order(entry[0])
                ):
                    ids = {r["osm_relation_id"] for r in routes}
                    cids.update(ids)
                    leaf = QTreeWidgetItem([label, str(len(ids))])
                    c.addChild(leaf)
                    self.items.append(leaf)
                    leaf.setData(0, Qt.ItemDataRole.UserRole, sorted(ids))
                    leaf.setToolTip(
                        0,
                        "\n".join(
                            str(r["osm_relation_id"]) + " · " + r["name"]
                            for r in routes
                        ),
                    )
                    color = QPixmap(12, 12)
                    color.fill(QColor(routes[0].get("display_color") or "#718096"))
                    leaf.setIcon(0, QIcon(color))
                    if selected_ids & ids:
                        leaf.setSelected(True)
                        c.setExpanded(True)
                        p.setExpanded(True)
                c.setData(0, Qt.ItemDataRole.UserRole, sorted(cids))
                c.setText(1, str(len(cids)))
                pids.update(cids)
            p.setData(0, Qt.ItemDataRole.UserRole, sorted(pids))
            p.setText(1, str(len(pids)))
        self.tree.blockSignals(False)
        provinces = {r[1] for r in self.model.regions} | set(self.model.grouped())
        self.province.blockSignals(True)
        self.province.clear()
        self.province.addItem("")
        self.province.addItems(sorted(provinces))
        self.province.blockSignals(False)
        self.selection_changed()
        self.filter_tree(self.search.text())
        selected = self.tree.selectedItems()
        if selected:
            self.tree.scrollToItem(selected[0])

    def selection_changed(self):
        ids = self.ids()
        self.apply_button.setEnabled(bool(ids))
        self.reset_button.setEnabled(bool(ids))
        routes = [self.model.lookup[rid] for rid in sorted(ids)]
        values = [self.model.parent(r) for r in routes]
        self.summary.setText(
            f"已选择 {len(self.tree.selectedItems())} 个条目 · {len(ids)} 个关系"
        )
        if not values:
            self.original.setText("可以选择一条线路，也可以选择整个城市或省。")
        else:
            automatic = {" / ".join(self.model.automatic(r)) for r in routes}
            names = "；".join(sorted(automatic)[:3])
            self.original.setText(
                "自动归类：" + names + ("…" if len(automatic) > 3 else "")
            )
        common = lambda index: (
            next(iter({v[index] for v in values}))
            if values and len({v[index] for v in values}) == 1
            else ""
        )
        self.province.setCurrentText(common(0))
        self.refresh_city_options()
        self.city.setCurrentText(common(1))
        self.label.setText(common(2))
        self.label.setPlaceholderText("留空保留各线路显示名")
        self._form_baseline = (
            self.province.currentText().strip(),
            self.city.currentText().strip(),
            self.label.text().strip(),
        )

    def refresh_city_options(self, *_):
        old = self.city.currentText()
        province = self.province.currentText()
        cities = {r[0] for r in self.model.regions if not province or r[1] == province}
        for p, entries in self.model.grouped().items():
            if not province or p == province:
                cities.update(entries)
        self.city.blockSignals(True)
        self.city.clear()
        self.city.addItem("")
        self.city.addItems(sorted(cities))
        self.city.setCurrentText(old)
        self.city.blockSignals(False)

    def move_selected(self):
        ids = self.ids()
        if not ids:
            return
        values = [box.currentText().strip() for box in (self.province, self.city)] + [
            self.label.text().strip()
        ]
        if not any(values):
            self.message.setText("至少填写一个目标字段")
            return
        try:
            self.model.set_parent(ids, *[v or None for v in values])
            self.rebuild(ids)
            self.message.setText("目录已调整，点击“保存并应用”生效；可取消放弃。")
        except ValueError as error:
            self.message.setText(str(error))

    def reset_selected(self):
        ids = self.ids()
        self.model.reset(ids)
        self.rebuild(ids)
        self.message.setText("所选条目已恢复自动归类，保存后生效。")

    def filter_tree(self, text):
        query = text.strip().casefold()

        def visit(item, inherited=False):
            own = (
                inherited or query in (item.text(0) + " " + item.toolTip(0)).casefold()
            )
            children = [visit(item.child(i), own) for i in range(item.childCount())]
            found = own or any(children)
            item.setHidden(not found)
            if query and found and item.childCount():
                item.setExpanded(True)
            return found

        for index in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(index))

    def save_and_accept(self):
        # Apply edits typed in the form even if the preview button was skipped.
        ids = self.ids()
        values = [
            self.province.currentText().strip(),
            self.city.currentText().strip(),
            self.label.text().strip(),
        ]
        try:
            if ids and any(values) and tuple(values) != self._form_baseline:
                self.model.set_parent(ids, *[v or None for v in values])
            self.model.save()
            self.accept()
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "无法保存目录设置", str(error))
