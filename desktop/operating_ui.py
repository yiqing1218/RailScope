"""Single-workspace timetable/diagram editor; no external operational control."""

from pathlib import Path
from time import monotonic
from shiboken6 import isValid

from PySide6.QtCore import QPointF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QScrollArea,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QInputDialog,
    QSizePolicy,
    QMenu,
    QToolButton,
    QTreeWidgetItem,
    QApplication,
)

try:
    from .components import Switch, switch_row, text_label, GrowingTree, Fold
    from .geometry import interpolate
    from .operating import parse_time, format_time
except ImportError:
    from components import Switch, switch_row, text_label, GrowingTree, Fold
    from geometry import interpolate
    from operating import parse_time, format_time


class TimeHandle(QGraphicsEllipseItem):
    def __init__(self, editor, train_id, index, kind, x, y, color):
        super().__init__(-4, -4, 8, 8)
        self.editor, self.train_id, self.index, self.kind = (
            editor,
            train_id,
            index,
            kind,
        )
        self.fixed_y = y
        self.setBrush(QColor(color))
        self.setPen(QPen(QColor("white"), 1))
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setZValue(4)
        self.setPos(x, y)
        self.setToolTip("水平拖动到发时刻，松开提交；15 秒吸附")

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            return QPointF(max(self.editor.plot_left, value.x()), self.fixed_y)
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        time = (
            round((self.x() - self.editor.plot_left) / self.editor.time_scale / 15) * 15
            + self.editor.start_time
        )
        train = self.editor.plan.train(self.train_id)
        stop = train["stops"][self.index]
        arrival = time if self.kind == "arrival_s" else stop["arrival_s"]
        departure = time if self.kind == "departure_s" else stop["departure_s"]
        # Scene items must survive the current mouse event; redraw next turn.
        QTimer.singleShot(
            0,
            lambda: self.editor.commit_stop(
                self.train_id, self.index, arrival, departure
            ),
        )


class DiagramView(QGraphicsView):
    resized = Signal()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()


class OperationsEditor(QFrame):
    updated = Signal()
    expand_requested = Signal()
    closed = Signal()
    workspace_requested = Signal()

    def __init__(self, plan, map_view, lines, path):
        super().__init__()
        self.setObjectName("panel")
        self.setMaximumWidth(1120)
        self.plan = plan
        self.map = map_view
        self.base_lines = lines
        self.path = path
        self.clock = 25200.0
        self.playing = False
        self.enabled = False
        self.appearance = {"size": 14, "style": "glow"}
        self.speed = 30
        self.loading = False
        self.selected_line = lines[0]["id"] if lines else None
        self.selected_train = None
        self.undo_stack = []
        self.redo_stack = []
        self.current_vehicle_features = []
        self.hidden_trains = set()
        self._visibility_timer = QTimer(self)
        self._visibility_timer.setSingleShot(True)
        self._visibility_timer.timeout.connect(self.refresh_vehicle_tree)
        self._ticks = 0
        self._last_tick = monotonic()
        self.plot_left = 125
        self.time_scale = 0.35
        self.time_grid_s = 300
        self.start_time = 24900
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        chrome = QWidget()
        chrome.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        controls = QVBoxLayout(chrome)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        header = QHBoxLayout()
        self.workspace_title = text_label(
            "国铁 · 车次运行工作台"
            if plan.system == "rail"
            else "地铁 · 交路运行工作台",
            "panelTitle",
        )
        header.addWidget(self.workspace_title)
        header.addStretch()
        expand = QPushButton("放大编辑区")
        expand.clicked.connect(self.expand_requested.emit)
        header.addWidget(expand)
        close = QPushButton("收起")
        close.clicked.connect(lambda: (self.hide(), self.closed.emit()))
        header.addWidget(close)
        controls.addLayout(header)
        tools = header
        self.line_combo = QComboBox()
        self.line_combo.setMaximumWidth(230)
        self.line_combo.setMinimumWidth(110)
        for line in lines:
            self.line_combo.addItem(line["name"], line["id"])
        self.line_combo.currentIndexChanged.connect(self.line_changed)
        tools.insertWidget(1, self.line_combo)
        self.variant_combo = QComboBox()
        self.variant_combo.setMinimumWidth(140)
        self.variant_combo.currentIndexChanged.connect(self.variant_changed)
        self.variant_combo.setMaximumWidth(420)
        tools.insertWidget(2, self.variant_combo, 1)
        if plan.system == "rail":
            self.variant_combo.hide()
            self.line_combo.setAccessibleName("国铁车次：一车次一时刻表与运行图")
        actions = QHBoxLayout()
        editing = QMenu(self)
        for title, method in [
            ("大小交路 / 车辆循环…", self.open_cycles),
            ("新增车次" if plan.system == "rail" else "新增列车", self.add_train),
            ("整车平移", self.shift_train),
            ("删除列车", self.delete_train),
            ("撤销", self.undo),
            ("重做", self.redo),
            ("保存", self.save),
        ]:
            if plan.system == "rail" and title.startswith("大小交路"):
                continue
            if title in ("整车平移", "删除列车", "撤销", "重做"):
                editing.addAction(title, method)
                continue
            button = QPushButton(title)
            button.clicked.connect(method)
            actions.addWidget(button)
        more = QToolButton()
        more.setText("编辑 ▾")
        more.setMenu(editing)
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        actions.addWidget(more)
        self.train_combo = QComboBox()
        self.train_combo.currentIndexChanged.connect(self.train_changed)
        selection = actions
        train_caption = QLabel("车次")
        train_caption.setFixedWidth(36)
        selection.addWidget(train_caption)
        self.train_combo.setMaximumWidth(240)
        selection.addWidget(self.train_combo)
        selection.addStretch()
        if plan.system == "rail":
            train_caption.hide()
            self.train_combo.hide()
        self.message = text_label("编辑到发时刻；运行图节点可水平拖动。", "muted")
        self.message.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        controls.addLayout(actions)
        layout.addWidget(chrome)
        self.tabs = QTabWidget()
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["车次", "方向", "站序", "车站", "到达", "发车", "停站 s"]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        for column, width in enumerate((135, 55, 40, 105, 85, 85, 60)):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.verticalHeader().hide()
        self.table.itemChanged.connect(self.table_changed)
        self.tabs.addTab(
            self.table, "国铁时刻表" if plan.system == "rail" else "地铁运行表"
        )
        self.scene = QGraphicsScene()
        self.diagram = DiagramView(self.scene)
        self.diagram.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self._diagram_timer = QTimer(self)
        self._diagram_timer.setSingleShot(True)
        self._diagram_timer.timeout.connect(self.refresh_diagram)
        self.diagram.resized.connect(
            lambda: (
                self._diagram_timer.start(20) if self.plan.system == "rail" else None
            )
        )
        self.diagram.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.diagram.setBackgroundBrush(QColor("#fbfcfd"))
        self.tabs.addTab(self.diagram, "运行图 · 时间 / 里程")
        layout.addWidget(self.tabs, 1)
        self.status = text_label("", wrap=True)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        footer = QHBoxLayout()
        footer.addWidget(self.status, 1)
        footer.addWidget(self.message, 2)
        layout.addLayout(footer)
        self.setMinimumHeight(340)
        self.populate_variants()
        self.refresh()
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        map_view.bridge.initialized.connect(self.map_ready)

    def map_ready(self):
        self.map.call("setVehicleAppearance", self.appearance)
        self.push_positions()

    def sidebar(self):
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 5, 0)
        layout.setSpacing(12)
        card = QFrame()
        card.setObjectName("demoCard")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 16, 14, 16)
        cl.addWidget(text_label("计划仿真", "panelTitle", True))
        cl.addWidget(
            text_label(
                "当前方案：国铁车次与跨线径路"
                if self.plan.system == "rail"
                else "当前方案：地铁交路与车辆循环 · 非官方计划",
                "muted",
                True,
            )
        )
        self.enabled_switch = Switch(False)
        self.enabled_switch.toggled.connect(self.set_enabled)
        cl.addWidget(switch_row("开启运行展示", self.enabled_switch))
        self.session_label = text_label("已关闭 · 地图浏览模式", "muted", True)
        cl.addWidget(self.session_label)
        self.clock_label = text_label(format_time(self.clock), "metricValue")
        cl.addWidget(self.clock_label)
        self.count_label = text_label("", wrap=True)
        cl.addWidget(self.count_label)
        self.play_button = QPushButton("开始仿真")
        self.play_button.setObjectName("primary")
        self.play_button.clicked.connect(
            lambda: self.pause() if self.playing else self.play()
        )
        cl.addWidget(self.play_button)
        reset = QPushButton(
            "回到始发时刻" if self.plan.system == "rail" else "回到 07:00:00"
        )
        reset.clicked.connect(self.reset)
        cl.addWidget(reset)
        layout.addWidget(card)
        time_row = QHBoxLayout()
        self.time_input = QLineEdit("07:00:00")
        self.time_input.setAccessibleName("仿真时刻")
        time_row.addWidget(self.time_input)
        jump = QPushButton("跳转")
        jump.clicked.connect(self.jump)
        self.time_input.returnPressed.connect(self.jump)
        time_row.addWidget(jump)
        layout.addLayout(time_row)
        self.speed_label = text_label("仿真速度  ×30")
        layout.addWidget(self.speed_label)
        speed = QSlider(Qt.Orientation.Horizontal)
        speed.setRange(1, 100)
        speed.setValue(30)
        speed.valueChanged.connect(self.set_speed)
        layout.addWidget(speed)
        vehicles = Switch(False)
        vehicles.toggled.connect(
            lambda on: self.map.call("setVisibility", "vehicles", on)
        )
        layout.addWidget(switch_row("车辆图层", vehicles))
        self.vehicle_switch = vehicles
        self.vehicle_tree = GrowingTree()
        self.vehicle_tree.setColumnCount(2)
        self.vehicle_tree.setHeaderHidden(True)
        self.vehicle_tree.header().setStretchLastSection(False)
        self.vehicle_tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.vehicle_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.vehicle_tree.setColumnWidth(1, 62)
        layout.addWidget(
            Fold(
                "列车可见性 · 按车次类型"
                if self.plan.system == "rail"
                else "列车可见性 · 按线路",
                self.vehicle_tree,
                expanded=False,
            )
        )
        self.refresh_vehicle_tree()
        layout.addWidget(text_label("列车标记", "sectionLabel"))
        self.marker_style = QComboBox()
        for label, value in (
            ("光晕圆点", "glow"),
            ("空心圆环", "ring"),
            ("列车图标", "train"),
        ):
            if self.plan.system == "rail" and value == "train":
                continue
            self.marker_style.addItem(label, value)
        self.marker_style.currentIndexChanged.connect(self.change_appearance)
        layout.addWidget(self.marker_style)
        self.marker_size_label = text_label("图标大小  14 px", "muted")
        layout.addWidget(self.marker_size_label)
        self.marker_size = QSlider(Qt.Orientation.Horizontal)
        self.marker_size.setRange(8, 40)
        self.marker_size.setValue(14)
        self.marker_size.setAccessibleName("列车图标大小")
        self.marker_size.valueChanged.connect(self.change_appearance)
        layout.addWidget(self.marker_size)
        show = QPushButton(
            "打开国铁时刻表 / 运行图"
            if self.plan.system == "rail"
            else "打开地铁运行表 / 运行图"
        )
        show.clicked.connect(self.workspace_requested.emit)
        layout.addWidget(show)
        locate = QPushButton("定位当前运行线路")
        locate.clicked.connect(self.locate_current_line)
        layout.addWidget(locate)
        layout.addWidget(text_label("计划导入、导出请使用运行菜单。", wrap=True))
        layout.addWidget(
            text_label(
                "国铁按车次与跨线径路独立建图。G1 为公开时刻与 OSM 几何参考，不代表实际调度进路。"
                if self.plan.system == "rail"
                else "地铁按交路与车辆循环独立建图；初始时刻为可编辑演示，不连接真实调度系统。",
                wrap=True,
            )
        )
        layout.addStretch()
        self.update_sidebar(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        return scroll

    def current_line(self):
        return self.plan.lines.get(self.selected_line)

    def populate_variants(self):
        base = self.line_combo.currentData()
        self.variant_combo.blockSignals(True)
        self.variant_combo.clear()
        line = self.plan.lines.get(base)
        if not line:
            self.variant_combo.blockSignals(False)
            return
        self.variant_combo.addItem(
            "完整主路径 · " + str(len(line["stations"])) + " 站", base
        )
        for variant in line["variants"]:
            key = base + "@" + str(variant["relation_id"])
            self.variant_combo.addItem(
                variant["source_name"] + " · " + str(len(variant["stations"])) + " 站",
                key,
            )
        self.variant_combo.blockSignals(False)

    def line_changed(self):
        self.populate_variants()
        self.variant_changed()

    def variant_changed(self):
        self.selected_line = self.variant_combo.currentData()
        self.selected_train = None
        self.refresh()
        self.locate_current_line()

    def locate_current_line(self):
        line = self.current_line()
        if line and line["path"]:
            coords = line["path"]["coordinates"]
            xs, ys = zip(*coords)
            self.map.call(
                "fit",
                [[min(xs), min(ys)], [max(xs), max(ys)]],
                "上海 · " + line["name"],
            )

    def train_changed(self):
        self.selected_train = self.train_combo.currentData()
        self.refresh_table()
        self.refresh_diagram()

    def refresh(self):
        trains = [t for t in self.plan.trains if t["line_id"] == self.selected_line]
        if self.selected_train not in {t["id"] for t in trains}:
            self.selected_train = None
        if self.plan.system == "rail" and trains:
            self.selected_train = trains[0]["id"]
        self.train_combo.blockSignals(True)
        self.train_combo.clear()
        self.train_combo.addItem("全部车次", None)
        for train in trains:
            self.train_combo.addItem(train["id"], train["id"])
        if self.selected_train:
            self.train_combo.setCurrentIndex(
                max(0, self.train_combo.findData(self.selected_train))
            )
        self.train_combo.blockSignals(False)
        self.refresh_table()
        self.refresh_diagram()
        self.update_sidebar(0)
        self.refresh_vehicle_tree()
        self.status.setText(
            f"{len(self.plan.trains)} 车次 · 当前 {len(trains)} 车次 · 未验证联锁"
        )

    def set_trains_visible(self, ids, on):
        self.hidden_trains.difference_update(ids) if on else self.hidden_trains.update(
            ids
        )
        self._visibility_timer.start(0)
        self.push_positions()

    def refresh_vehicle_tree(self):
        if not hasattr(self, "vehicle_tree") or not isValid(self.vehicle_tree):
            return
        tree = self.vehicle_tree
        expanded = {
            tree.topLevelItem(i).text(0)
            for i in range(tree.topLevelItemCount())
            if tree.topLevelItem(i).isExpanded()
        }
        tree.clear()
        groups = {}
        for train in self.plan.trains:
            label = (
                (train["id"][0].upper() + " 字头车次")
                if self.plan.system == "rail"
                else self.plan.lines[train["line_id"]]["name"]
            )
            groups.setdefault(label, []).append(train)
        for label, trains in [("全部列车", self.plan.trains), *sorted(groups.items())]:
            ids = {t["id"] for t in trains}
            parent = QTreeWidgetItem(tree, [label])
            control = Switch(bool(ids - self.hidden_trains))
            control.setMixed(
                bool(ids & self.hidden_trains) and bool(ids - self.hidden_trains)
            )
            control.toggled.connect(
                lambda on, keys=ids: self.set_trains_visible(keys, on)
            )
            tree.setItemWidget(parent, 1, control)
            parent.setExpanded(label in expanded)
            if label == "全部列车":
                continue
            for train in trains:
                child = QTreeWidgetItem(parent, [train["id"]])
                switch = Switch(train["id"] not in self.hidden_trains)
                switch.toggled.connect(
                    lambda on, key=train["id"]: self.set_trains_visible({key}, on)
                )
                tree.setItemWidget(child, 1, switch)
        tree.schedule_height()

    def displayed_trains(self):
        return [
            t
            for t in self.plan.trains
            if t["line_id"] == self.selected_line
            and (not self.selected_train or t["id"] == self.selected_train)
        ]

    def refresh_table(self):
        self.loading = True
        self.table.blockSignals(True)
        trains = self.displayed_trains()
        self.table.setRowCount(sum(len(t["stops"]) for t in trains))
        row = 0
        stations = {
            s["id"]: s["name"] for s in (self.current_line() or {}).get("stations", [])
        }
        for train in trains:
            for index, stop in enumerate(train["stops"]):
                values = [
                    train["id"],
                    "正向" if train["direction"] == "forward" else "反向",
                    index + 1,
                    stations[stop["station_id"]],
                    format_time(stop["arrival_s"]),
                    format_time(stop["departure_s"]),
                    stop["departure_s"] - stop["arrival_s"],
                ]
                for column, value in enumerate(values):
                    item = QTableWidgetItem(str(value))
                    item.setData(Qt.ItemDataRole.UserRole, (train["id"], index))
                    if column not in (4, 5):
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    else:
                        item.setForeground(QColor("#0c776f"))
                    self.table.setItem(row, column, item)
                self.table.setRowHeight(row, 32)
                row += 1
        self.table.blockSignals(False)
        self.loading = False

    def table_changed(self, item):
        if self.loading or item.column() not in (4, 5):
            return
        train_id, index = item.data(Qt.ItemDataRole.UserRole)
        stop = self.plan.train(train_id)["stops"][index]
        try:
            value = parse_time(item.text())
            self.commit_stop(
                train_id,
                index,
                value if item.column() == 4 else stop["arrival_s"],
                value if item.column() == 5 else stop["departure_s"],
            )
        except ValueError as error:
            self.message.setText("未应用：" + str(error))
            self.refresh_table()

    def checkpoint(self):
        self.undo_stack.append(self.snapshot_state())
        self.undo_stack = self.undo_stack[-50:]
        self.redo_stack.clear()

    def commit_stop(self, train_id, index, arrival, departure):
        before = self.snapshot_state()
        try:
            self.plan.edit_stop(train_id, index, int(arrival), int(departure))
            self.undo_stack.append(before)
            self.redo_stack.clear()
            self.changed("已修改 " + train_id + "，车辆按新时刻运行")
        except ValueError as error:
            self.message.setText("未应用：" + str(error))
            self.refresh_table()
            self.refresh_diagram()

    def changed(self, message):
        self.undo_stack = self.undo_stack[-50:]
        self.message.setText(message)
        self.status.setToolTip(message)
        self.refresh()
        self.push_positions()
        self.updated.emit()

    def shift_train(self):
        if not self.selected_train:
            self.message.setText("请先选择一列车")
            return
        seconds, accepted = QInputDialog.getInt(
            self,
            "整车时刻平移",
            "调整秒数（负数提前，正数延后）",
            300,
            -86400,
            86400,
            15,
        )
        if not accepted:
            return
        before = self.snapshot_state()
        try:
            self.plan.shift_train(self.selected_train, seconds)
            self.undo_stack.append(before)
            self.redo_stack.clear()
            self.changed("已平移整列车的所有到发时刻")
        except ValueError as error:
            self.message.setText("未应用：" + str(error))

    def add_train(self):
        line = self.current_line()
        if not line or not line.get("path"):
            self.message.setText("请先导入有效线路数据")
            return
        number = 1
        while any(
            t["id"] == "USER-" + line["ref"] + "-" + str(number)
            for t in self.plan.trains
        ):
            number += 1
        dialog = QDialog(self)
        dialog.setWindowTitle("新增列车计划 · 非官方仿真")
        form = QFormLayout(dialog)
        number_input = QLineEdit("USER-" + line["ref"] + "-" + str(number))
        form.addRow("车次编号", number_input)
        direction = QComboBox()
        direction.addItem("正向（里程递增）", "forward")
        direction.addItem("反向（里程递减）", "reverse")
        form.addRow("方向", direction)
        departure = QLineEdit(format_time(int(self.clock) + 60))
        form.addRow("首站到达", departure)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        before = self.snapshot_state()
        try:
            train = self.plan.add_train(
                self.selected_line,
                number_input.text(),
                parse_time(departure.text()),
                direction.currentData(),
            )
            self.undo_stack.append(before)
            self.redo_stack.clear()
            self.selected_train = train["id"]
            self.changed("已新增车次，可在运行表编辑到发时刻")
        except ValueError as error:
            self.message.setText(str(error))

    def delete_train(self):
        if not self.selected_train:
            self.message.setText("请先在车次下拉框选择一列车")
            return
        self.checkpoint()
        self.plan.trains = [
            t for t in self.plan.trains if t["id"] != self.selected_train
        ]
        self.selected_train = None
        self.changed("已删除，可撤销")

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(self.snapshot_state())
            self.restore_state(self.undo_stack.pop())
            self.changed("已撤销")

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self.snapshot_state())
            self.restore_state(self.redo_stack.pop())
            self.changed("已重做")

    def snapshot_state(self):
        return self.plan.snapshot()

    def restore_state(self, snapshot):
        self.plan.restore(snapshot)

    def save(self):
        try:
            self.plan.save(self.path)
            self.message.setText("已保存本地运行计划")
        except (OSError, ValueError) as error:
            self.message.setText("保存失败：" + str(error))

    def import_plan(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入运行计划", str(Path(self.path).parent), "运行计划 (*.json)"
        )
        if path:
            before = self.plan.snapshot()
            try:
                self.plan.load(path)
                self.undo_stack.append(before)
                self.redo_stack.clear()
                self.changed("已导入运行计划")
            except (OSError, ValueError, KeyError, TypeError) as error:
                QMessageBox.warning(self, "导入失败", str(error))

    def export_plan(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出运行计划",
            str(Path(self.path).parent / "shanghai-plan.json"),
            "运行计划 (*.json)",
        )
        if path:
            try:
                self.plan.save(path)
                self.message.setText("已导出运行计划")
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, "导出失败", str(error))

    def play(self):
        self.set_enabled(True)
        self.playing = True
        self._last_tick = monotonic()
        self.update_sidebar(0)
        self.push_positions()

    def pause(self):
        self.playing = False
        self.update_sidebar(0)
        self.push_positions()

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        if not self.enabled:
            self.playing = False
        elif hasattr(self, "vehicle_switch"):
            self.vehicle_switch.setChecked(True)
        self._last_tick = monotonic()
        if hasattr(self, "enabled_switch"):
            self.enabled_switch.blockSignals(True)
            self.enabled_switch.setChecked(self.enabled)
            self.enabled_switch.blockSignals(False)
        self.push_positions()
        self.updated.emit()

    def change_appearance(self):
        if not hasattr(self, "marker_size"):
            return
        self.appearance = {
            "size": self.marker_size.value(),
            "style": self.marker_style.currentData(),
        }
        self.marker_size_label.setText(f"图标大小  {self.appearance['size']} px")
        self.map.call("setVehicleAppearance", self.appearance)

    def reset(self):
        self.clock = 25200
        self.update_sidebar(0)
        self.push_positions()

    def jump(self):
        try:
            self.clock = parse_time(self.time_input.text())
            self.update_sidebar(0)
            self.push_positions()
        except ValueError as error:
            self.message.setText(str(error))

    def set_speed(self, value):
        self.speed = value
        self.speed_label.setText("仿真速度  ×" + str(value))

    def tick(self):
        now = monotonic()
        elapsed = now - self._last_tick
        self._last_tick = now
        if self.enabled and self.playing:
            self.clock = min(172799, self.clock + self.speed * elapsed)
        if self.clock >= 172799:
            self.playing = False
        self.push_positions()
        self._ticks += 1
        if self._ticks % 5 == 0:
            self.updated.emit()
        if hasattr(self, "cursor_line") and self.cursor_line:
            x = self.plot_left + (self.clock - self.start_time) * self.time_scale
            self.cursor_line.setLine(x, 30, x, self.diagram_height)

    def push_positions(self):
        if not self.map.is_ready:
            return
        features = []
        for train, position in (
            self.plan.vehicle_positions(self.clock) if self.enabled else []
        ):
            if position and train["id"] not in self.hidden_trains:
                line = self.plan.lines[train["line_id"]]
                features.append(
                    {
                        "type": "Feature",
                        "properties": {
                            "vehicle_id": train.get("vehicle_id", train["id"]),
                            "trip_id": train["id"],
                            "name": train["id"] + " · " + line["name"],
                            "line_ref": line["ref"],
                            "route_relation_id": line["relation_id"],
                            "state": position["state"],
                            "distance_m": position["distance_m"],
                            "distance_km": round(position["distance_m"] / 1000, 3),
                            "simulation_time": format_time(self.clock),
                            "source": train["source"],
                            "display_color": line["color"],
                            **(
                                {"corridor_id": line["corridor_id"]}
                                if line.get("corridor_id")
                                else {}
                            ),
                        },
                        "geometry": {
                            "type": "Point",
                            "coordinates": interpolate(
                                line["path"], position["distance_m"]
                            ),
                        },
                    }
                )
        self.current_vehicle_features = features
        self.map.call(
            "setOperatingVehicles",
            {"type": "FeatureCollection", "features": features},
            self.clock,
            self.playing,
        )
        self.update_sidebar(len(features))

    def open_cycles(self):
        if not self.current_line() or not self.current_line().get("path"):
            self.message.setText("请先导入有连续几何与站序的线路")
            return
        try:
            from .cycle_ui import CycleDialog
        except ImportError:
            from cycle_ui import CycleDialog
        CycleDialog(self).exec()

    def update_sidebar(self, active):
        if hasattr(self, "clock_label") and isValid(self.clock_label):
            self.clock_label.setText(format_time(self.clock))
            self.count_label.setText(
                f"{len(self.base_lines)} 条线路 · {len(self.plan.trains)} 列车计划 · {active} 列车在运行"
            )
            self.play_button.setText("暂停仿真" if self.playing else "开始仿真")
            self.session_label.setText(
                "运行中 · 按运行表推进"
                if self.playing
                else "已暂停 · 保留列车位置"
                if self.enabled
                else "已关闭 · 地图浏览模式"
            )

    def refresh_diagram(self):
        if QApplication.mouseButtons() != Qt.MouseButton.NoButton:
            self._diagram_timer.start(80)
            return  # Never destroy a time handle during an active drag.
        self.diagram.resetTransform()
        self.scene.clear()
        self.cursor_line = None
        line = self.current_line()
        if not line or not line["path"]:
            return
        trains = self.displayed_trains()
        stations = line["stations"]
        self.diagram_height = max(
            400, len(stations) * 22, self.diagram.viewport().height() - 30
        )
        earliest = min((t["stops"][0]["arrival_s"] for t in trains), default=25200)
        latest = max((t["stops"][-1]["departure_s"] for t in trains), default=28800)
        self.start_time = max(0, earliest - 300)
        if self.plan.system == "rail":
            available = max(800, self.diagram.viewport().width() - 4)
            self.time_scale = (available - self.plot_left - 32) / max(
                600, latest - self.start_time + 300
            )
        width = max(
            800,
            (latest - self.start_time + 300) * self.time_scale
            + self.plot_left
            + (32 if self.plan.system == "rail" else 50),
        )
        self.scene.setSceneRect(0, 0, width, self.diagram_height + 30)
        length = line["path"]["length_m"]

        def y(distance):
            return 40 + distance / length * (self.diagram_height - 55)

        for station in stations:
            yy = y(station["distance_m"])
            self.scene.addLine(
                self.plot_left, yy, width, yy, QPen(QColor("#e1e8ec"), 1)
            )
            label = station["name"]
            if self.plan.system == "rail":
                label += f"  {station['distance_m'] / 1000:.1f} km"
            text = self.scene.addText(label)
            text.setTextWidth(self.plot_left - 5)
            text.setDefaultTextColor(QColor("#536875"))
            text.setPos(0, yy - 10)
        grid = self.time_grid_s
        if self.plan.system == "rail":
            for candidate in (900, 1800, 3600, 7200, 14400, 28800, 86400):
                grid = candidate
                if grid * self.time_scale >= 72:
                    break
        for time in range(
            int(self.start_time // grid) * grid, int(latest) + grid * 2, grid
        ):
            xx = self.plot_left + (time - self.start_time) * self.time_scale
            if xx < self.plot_left or xx > width - 24:
                continue
            self.scene.addLine(
                xx, 30, xx, self.diagram_height, QPen(QColor("#e1e8ec"), 1)
            )
            text = self.scene.addText(format_time(time)[:5])
            text.setPos(xx - 20, 0)
            text.setDefaultTextColor(QColor("#536875"))
        for train in trains:
            color = line["color"] if train["direction"] == "forward" else "#327a8b"
            path = QPainterPath()
            for index, stop in enumerate(train["stops"]):
                yy = y(stop["distance_m"])
                for kind in ("arrival_s", "departure_s"):
                    xx = (
                        self.plot_left
                        + (stop[kind] - self.start_time) * self.time_scale
                    )
                    if index == 0 and kind == "arrival_s":
                        path.moveTo(xx, yy)
                    else:
                        path.lineTo(xx, yy)
                    self.scene.addItem(
                        TimeHandle(self, train["id"], index, kind, xx, yy, color)
                    )
            curve = self.scene.addPath(path, QPen(QColor(color), 2))
            curve.setToolTip(
                train["id"]
                + " · "
                + ("正向" if train["direction"] == "forward" else "反向")
            )
            text = self.scene.addText(train["id"])
            text.setDefaultTextColor(QColor(color))
            text.setPos(path.pointAtPercent(0.5) + QPointF(6, -18))
        self.cursor_line = self.scene.addLine(
            0,
            30,
            0,
            self.diagram_height,
            QPen(QColor("#0c776f"), 1, Qt.PenStyle.DashLine),
        )
        self.cursor_line.setZValue(2)
