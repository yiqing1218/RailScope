"""Full/short patterns, editable round-trip template and fleet in one dialog."""

from copy import deepcopy
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QComboBox,
    QLineEdit,
    QSpinBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QLabel,
    QMessageBox,
    QHeaderView,
)

try:
    from .operating import Plan, parse_time, format_time
except ImportError:
    from operating import Plan, parse_time, format_time


class CycleDialog(QDialog):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.line = editor.current_line()
        self.setWindowTitle("大小交路 · 循环模板与车辆投放")
        self.resize(960, 720)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "一套交路 = 往返循环模板 + 车辆投放。大小交路可并存，分别生成；同一车辆不允许重叠。"
            )
        )
        self.saved = QComboBox()
        self.saved.addItem("新建交路", None)
        for cycle in editor.plan.cycles:
            if cycle["line_id"] == self.line["id"]:
                self.saved.addItem(cycle["id"], cycle)
        layout.addWidget(self.saved)
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)
        template = QDialog()
        form = QFormLayout(template)
        self.name = QLineEdit(self.line["id"] + "/full")
        self.first, self.last = QComboBox(), QComboBox()
        for station in self.line["stations"]:
            self.first.addItem(station["name"])
            self.last.addItem(station["name"])
        self.last.setCurrentIndex(len(self.line["stations"]) - 1)
        self.turnback = QSpinBox()
        self.turnback.setRange(1, 3600)
        self.turnback.setValue(120)
        form.addRow("交路编号（大小交路使用不同编号）", self.name)
        form.addRow("折返起点", self.first)
        form.addRow("折返终点", self.last)
        form.addRow("每端折返等待 / 秒", self.turnback)
        self.template = QTableWidget(0, 5)
        self.template.setHorizontalHeaderLabels(
            ["方向", "车站", "相对到达 / 秒", "相对发车 / 秒", "里程 / m"]
        )
        self.template.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        form.addRow(self.template)
        form.addRow(
            QLabel("首站相对到达 = 0；可逐站编辑时间。实际折返轨道及信号进路尚未校验。")
        )
        tabs.addTab(template, "① 大小交路与固定循环")
        fleet = QDialog()
        fl = QVBoxLayout(fleet)
        row = QHBoxLayout()
        self.count = QSpinBox()
        self.count.setRange(1, 500)
        self.count.setValue(6)
        self.start = QLineEdit("07:00:00")
        self.end = QLineEdit("09:00:00")
        fill = QPushButton("按循环均匀投放（可逐车修改）")
        fill.clicked.connect(self.fill_fleet)
        for widget in (
            QLabel("车辆数"),
            self.count,
            QLabel("开始"),
            self.start,
            QLabel("结束"),
            self.end,
            fill,
        ):
            row.addWidget(widget)
        fl.addLayout(row)
        self.fleet = QTableWidget(0, 3)
        self.fleet.setHorizontalHeaderLabels(
            ["车辆编号", "开始 HH:MM:SS", "结束 HH:MM:SS"]
        )
        self.fleet.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        fl.addWidget(self.fleet)
        fl.addWidget(
            QLabel(
                "仅生成时间窗内完整往返循环；不足一个循环会拒绝。不会裁剪成瞬移或半途消失的车次。"
            )
        )
        tabs.addTab(fleet, "② 车辆数量与各车时间窗")
        self.preview = QLabel("生成前先预览。现有其他交路不会被删除。")
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)
        actions = QHBoxLayout()
        preview = QPushButton("校验并预览")
        preview.clicked.connect(self.check)
        apply = QPushButton("生成运行表 / 运行图")
        apply.clicked.connect(self.apply)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        for button in (preview, apply, cancel):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.first.currentIndexChanged.connect(self.reset_template)
        self.last.currentIndexChanged.connect(self.reset_template)
        self.saved.currentIndexChanged.connect(self.load_saved)
        self.reset_template()
        self.fill_fleet()

    def reset_template(self):
        try:
            cycle = self.editor.plan.make_cycle(
                self.line["id"],
                self.name.text(),
                self.first.currentIndex(),
                self.last.currentIndex(),
                self.turnback.value(),
            )
            self.set_template(cycle)
        except ValueError as error:
            self.preview.setText(str(error))

    def set_template(self, cycle):
        self.base = deepcopy(cycle)
        self.template.setRowCount(0)
        names = {s["id"]: s["name"] for s in self.line["stations"]}
        for key in ("outbound", "inbound"):
            for stop in cycle[key]:
                row = self.template.rowCount()
                self.template.insertRow(row)
                for col, value in enumerate(
                    (
                        "去程" if key == "outbound" else "回程",
                        names[stop["station_id"]],
                        stop["arrival_s"],
                        stop["departure_s"],
                        round(stop["distance_m"], 2),
                    )
                ):
                    item = QTableWidgetItem(str(value))
                    if col not in (2, 3):
                        item.setFlags(
                            item.flags()
                            & ~__import__(
                                "PySide6.QtCore", fromlist=["Qt"]
                            ).Qt.ItemFlag.ItemIsEditable
                        )
                    self.template.setItem(row, col, item)

    def cycle(self):
        if not hasattr(self, "base"):
            raise ValueError("请选择有效交路端点")
        cycle = deepcopy(self.base)
        cycle["id"] = self.name.text().strip()
        cycle["turnback_s"] = self.turnback.value()
        row = 0
        for key in ("outbound", "inbound"):
            for stop in cycle[key]:
                stop["arrival_s"] = int(self.template.item(row, 2).text())
                stop["departure_s"] = int(self.template.item(row, 3).text())
                row += 1
        self.editor.plan.validate_cycle(cycle)
        return cycle

    def fill_fleet(self):
        try:
            cycle = self.cycle()
            duration = (
                sum(cycle[k][-1]["departure_s"] for k in ("outbound", "inbound"))
                + 2 * cycle["turnback_s"]
            )
            start, end = parse_time(self.start.text()), parse_time(self.end.text())
            self.fleet.setRowCount(self.count.value())
            for row in range(self.count.value()):
                values = (
                    cycle["id"] + f"-V{row + 1:03d}",
                    format_time(start + round(duration * row / self.count.value())),
                    format_time(end),
                )
                for col, value in enumerate(values):
                    self.fleet.setItem(row, col, QTableWidgetItem(value))
        except ValueError as error:
            self.preview.setText(str(error))

    def load_saved(self):
        cycle = self.saved.currentData()
        if not cycle:
            return
        self.name.setText(cycle["id"])
        self.turnback.setValue(cycle["turnback_s"])
        ids = [s["id"] for s in self.line["stations"]]
        self.first.setCurrentIndex(ids.index(cycle["outbound"][0]["station_id"]))
        self.last.setCurrentIndex(ids.index(cycle["outbound"][-1]["station_id"]))
        self.set_template(cycle)
        vehicles = [
            v for v in self.editor.plan.vehicles if v["cycle_id"] == cycle["id"]
        ]
        self.count.setValue(max(1, len(vehicles)))
        self.fleet.setRowCount(len(vehicles))
        for row, vehicle in enumerate(vehicles):
            for col, value in enumerate(
                (
                    vehicle["id"],
                    format_time(vehicle["start_s"]),
                    format_time(vehicle["end_s"]),
                )
            ):
                self.fleet.setItem(row, col, QTableWidgetItem(value))

    def candidate(self):
        cycle = self.cycle()
        vehicles = [
            {
                "id": self.fleet.item(r, 0).text().strip(),
                "start_s": parse_time(self.fleet.item(r, 1).text()),
                "end_s": parse_time(self.fleet.item(r, 2).text()),
            }
            for r in range(self.fleet.rowCount())
        ]
        plan = Plan(list(self.editor.plan.lines.values()), self.editor.plan.system)
        plan.restore(self.editor.plan.snapshot())
        # Bootstrap examples are not a second fleet once a real user cycle exists.
        plan.trains = [
            t
            for t in plan.trains
            if not (t["line_id"] == cycle["line_id"] and t["id"].startswith("DEMO-"))
        ]
        trips = plan.generate_fleet(cycle, vehicles)
        self.preview.setText(
            f"校验通过：{len(vehicles)} 辆车 · {len(trips)} 个单程 · {len(trips) // 2} 次往返。生成后可以逐站编辑，模板不会覆盖你的编辑，除非再次生成。"
        )
        return plan

    def check(self):
        try:
            self.candidate()
        except (ValueError, AttributeError) as error:
            self.preview.setText("未通过：" + str(error))

    def apply(self):
        try:
            plan = self.candidate()
            self.editor.checkpoint()
            self.editor.plan.restore(plan.snapshot())
            self.editor.changed("已生成车辆循环计划，可撤销；请保存")
            self.accept()
        except (ValueError, AttributeError) as error:
            QMessageBox.warning(self, "不能生成", str(error))
