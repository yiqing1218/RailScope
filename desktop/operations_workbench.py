"""Six workbench entry groups over shared domain services and existing QActions."""

from copy import copy, deepcopy
from dataclasses import replace
from html import escape
import json
from pathlib import Path
import shutil
import sqlite3
from time import monotonic

from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QPixmap, QTextDocument, QImage, QPageSize
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QDialog,
    QFormLayout,
    QLineEdit,
    QComboBox,
    QDialogButtonBox,
    QMessageBox,
    QFileDialog,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QDoubleSpinBox,
    QSpinBox,
    QDateEdit,
    QSlider,
    QTabWidget,
    QScrollArea,
)
from railscope.domain import Vehicle
from railscope.identity import IdentityRegistry, new_id
from railscope.workspace import WorkspaceObjects
from railscope.services.analysis import (
    section_statistics,
    station_timetable,
    station_state,
    station_track_intervals,
    corridor_diagram_svg,
    corridor_archive,
)
from railscope.services.vehicles import vehicle_duties


def guarded(parent, title, callback):
    try:
        return callback()
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as error:
        QMessageBox.warning(parent, title, str(error))


def table_widget(headers, rows):
    table = QTableWidget(len(rows), len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.horizontalHeader().setSectionResizeMode(
        QHeaderView.ResizeMode.ResizeToContents
    )
    table.horizontalHeader().setStretchLastSection(True)
    for i, row in enumerate(rows):
        for j, value in enumerate(row):
            table.setItem(
                i, j, QTableWidgetItem("未知" if value is None else str(value))
            )
    return table


def clock_text(value):
    if value is None:
        return "—"
    value = int(value)
    return f"{value // 3600:02d}:{value % 3600 // 60:02d}:{value % 60:02d}"


class Workbench:
    def __init__(self, desk, workspace_path, session):
        self.desk, self.session = desk, session
        self.store = WorkspaceObjects(workspace_path)
        self.dialogs = []
        self.vehicle_tree = None
        self.vehicle_catalog = None

    def repo(self):
        repo = self.desk.rail_operations.domain_repo
        if repo is None:
            raise ValueError("请先建立或导入国铁通道和车次计划")
        repo.vehicles = self.store.collection("vehicles")
        return repo

    def page(self, index):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 5, 0)
        if index == 2:
            try:
                from .china_emu_ui import ReferencePanel
            except ImportError:
                from china_emu_ui import ReferencePanel
            tabs = QTabWidget()
            layout.addWidget(tabs)
            catalog = ReferencePanel(body, vehicle_only=True, create_vehicle=lambda p: self.edit_vehicle(template=p))
            self.vehicle_catalog, self.vehicle_tabs = catalog, tabs
            tabs.addTab(catalog, f"车型资料（{catalog.objects.rowCount()}）")
            units = QWidget()
            tabs.addTab(units, "具体车辆 / 担当")
            layout = QVBoxLayout(units)
            self.vehicle_tree = QTreeWidget()
            self.vehicle_tree.setHeaderLabels(["车辆 / 线路"])
            self.vehicle_tree.itemDoubleClicked.connect(
                lambda item, column: (
                    self.edit_vehicle(item.data(0, Qt.ItemDataRole.UserRole))
                    if item.data(0, Qt.ItemDataRole.UserRole)
                    else None
                )
            )
            layout.addWidget(self.vehicle_tree)
            self.button(layout, "新建车辆", lambda: self.edit_vehicle())
            self.button(
                layout, "编辑车辆", lambda: self.edit_vehicle(self.selected_vehicle())
            )
            self.button(layout, "分配当前车次", self.assign_vehicle)
            self.button(layout, "查看车辆担当 / 交路", self.show_duties)
            self.refresh_vehicles()
        elif index == 3:
            for name, callback in [
                ("通道列车运行图", self.show_corridor_diagram),
                ("跨通道区间统计", self.show_section_statistics),
                ("车站时刻表与股道状态", self.show_station),
                ("站场平面图 / 专业导出", self.desk.export_station_schematic),
                ("导出通道档案", self.export_corridor_archive),
            ]:
                self.button(layout, name, callback)
            self.action_buttons(layout, "analysis")
        elif index == 4:
            self.button(layout, "编辑选中对象信息", self.desk.edit_selected_metadata)
            self.button(layout, "编辑基础设施历史时间", self.desk.history.edit_selected)
            self.button(
                layout, "为停靠车次选择站台与站内进路", self.configure_station_route
            )
            self.action_buttons(layout, "edit")
        else:
            self.action_buttons(layout, "exchange")
        layout.addStretch()
        scroll.setWidget(body)
        return scroll

    def button(self, layout, title, callback):
        button = QPushButton(title)
        button.setMinimumHeight(34)
        button.clicked.connect(lambda: guarded(self.desk, title, callback))
        layout.addWidget(button)
        return button

    def action_buttons(self, layout, kind):
        seen = set()

        def collect(menu, path):
            for action in menu.actions():
                if action.menu():
                    collect(action.menu(), path + [action.text().replace("&", "")])
                    continue
                title = action.text().replace("&", "")
                exchange = any(
                    word in title
                    for word in (
                        "导入",
                        "导出",
                        "下载",
                        "加载文件",
                        "模板",
                        "截图",
                        "保存",
                        "另存为",
                        "从全国 OSM 建立",
                    )
                )
                edit = path[0] == "编辑" or any(
                    word in title
                    for word in (
                        "编辑",
                        "样式",
                        "新增",
                        "新建",
                        "删除",
                        "合并",
                        "移动",
                        "撤销",
                        "重做",
                        "归档",
                        "恢复",
                    )
                )
                analysis = any(
                    word in title
                    for word in ("分析", "审计", "验证", "统计", "运行图", "站场示意")
                )
                eligible = (
                    exchange
                    if kind == "exchange"
                    else edit and not exchange
                    if kind == "edit"
                    else analysis and not edit
                )
                if (
                    eligible
                    and title
                    and not action.isSeparator()
                    and title not in seen
                ):
                    seen.add(title)
                    button = self.button(layout, title, action.trigger)
                    button.setToolTip(" / ".join(path))
                    button.setEnabled(action.isEnabled())
                    action.changed.connect(
                        lambda a=action, b=button: b.setEnabled(a.isEnabled())
                    )

        for action in self.desk.menuBar().actions():
            if action.menu():
                collect(action.menu(), [action.text().replace("&", "")])

    def show_dialog(self, dialog):
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.dialogs.append(dialog)
        dialog.destroyed.connect(
            lambda: self.dialogs.remove(dialog) if dialog in self.dialogs else None
        )
        dialog.show()
        dialog.raise_()

    def selected_vehicle(self):
        item = self.vehicle_tree.currentItem() if self.vehicle_tree else None
        ident = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if not ident:
            raise ValueError("请选择具体车辆")
        return ident

    def refresh_vehicles(self):
        if not self.vehicle_tree:
            return
        if self.vehicle_catalog:
            self.vehicle_catalog.refresh_profiles()
            self.vehicle_tabs.setTabText(0,f"车型资料（{sum(p['kind']=='vehicle' for p in self.vehicle_catalog.profiles)}）")
        self.vehicle_tree.clear()
        groups = {}
        repo = getattr(self.desk.rail_operations, "domain_repo", None)
        names = {line.id: line.name for line in repo.lines.values()} if repo else {}
        for mode, title in [("metro", "地铁车辆（上海）"), ("rail", "铁路车辆")]:
            root = QTreeWidgetItem([title])
            self.vehicle_tree.addTopLevelItem(root)
            groups[mode, None] = root
        for vehicle in self.store.collection("vehicles").values():
            key = vehicle.mode, vehicle.infrastructure_line_id
            if key not in groups:
                line = names.get(
                    vehicle.infrastructure_line_id,
                    vehicle.parameters.get("line_name")
                    or vehicle.infrastructure_line_id,
                )
                group = QTreeWidgetItem([str(line)])
                groups[vehicle.mode, None].addChild(group)
                groups[key] = group
            item = QTreeWidgetItem(
                [vehicle.name + " · " + (vehicle.model or vehicle.code or vehicle.id)]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, vehicle.id)
            groups[key].addChild(item)
        self.vehicle_tree.expandToDepth(1)

    def edit_vehicle(self, ident=None, template=None):
        value = self.store.collection("vehicles").get(ident) or Vehicle(
            new_id("VEH"), ""
        )
        if template:
            try:
                from .reference_integration import vehicle_from_profile
            except ImportError:
                from reference_integration import vehicle_from_profile
            value = vehicle_from_profile(value, template)
        dialog = QDialog(self.desk)
        dialog.setWindowTitle("车辆档案")
        dialog.resize(500, 620)
        form = QFormLayout(dialog)
        form.addRow("稳定编号", QLabel(value.id))
        name, model, code = (
            QLineEdit(value.name),
            QLineEdit(value.model),
            QLineEdit(value.code),
        )
        mode = QComboBox()
        mode.addItem("铁路车辆", "rail")
        mode.addItem("地铁车辆（上海）", "metro")
        mode.setCurrentIndex(max(0, mode.findData(value.mode)))
        line = QComboBox()
        line.addItem("未分配线路", None)
        repo = getattr(self.desk.rail_operations, "domain_repo", None)
        if repo:
            for obj in repo.lines.values():
                line.addItem("国铁 · " + obj.name, obj.id)
        registry = IdentityRegistry(self.store.path)
        for obj in self.desk.shanghai_lines:
            line.addItem(
                "上海地铁 · " + obj["name"],
                registry.resolve_alias(
                    "line", "metro/" + obj["id"], "IL"
                ),
            )
        if (
            value.infrastructure_line_id
            and line.findData(value.infrastructure_line_id) < 0
        ):
            line.addItem(
                value.parameters.get("line_name", value.infrastructure_line_id),
                value.infrastructure_line_id,
            )
        line.setCurrentIndex(max(0, line.findData(value.infrastructure_line_id)))
        for label, widget in [
            ("名称", name),
            ("制式", mode),
            ("车型 / 型号", model),
            ("车辆代码", code),
            ("所属线路", line),
        ]:
            form.addRow(label, widget)
        parameters = QPlainTextEdit(
            json.dumps(value.parameters, ensure_ascii=False, indent=2)
        )
        parameters.setPlaceholderText("JSON 参数，如定员、编组、车长、最高速度")
        form.addRow("专业参数", parameters)
        photo = QLabel()
        photo.setMinimumHeight(120)
        asset = [value.photo_asset]
        chosen = [None]
        if value.photo_asset:
            photo.setPixmap(
                QPixmap(str(self.store.path.parent / value.photo_asset)).scaled(
                    350, 150, Qt.AspectRatioMode.KeepAspectRatio
                )
            )
        form.addRow("车辆图片", photo)
        upload = QPushButton("选择图片…")
        form.addRow(upload)

        def choose():
            path, _ = QFileDialog.getOpenFileName(
                dialog, "车辆图片", "", "PNG / JPEG / WEBP (*.png *.jpg *.jpeg *.webp)"
            )
            if path:
                if Path(path).stat().st_size > 16 * 1024 * 1024:
                    raise ValueError("车辆图片不得大于 16 MB")
                image = QImage(path)
                if image.isNull():
                    raise ValueError("图片无法读取")
                chosen[0] = path
                photo.setPixmap(
                    QPixmap.fromImage(image).scaled(
                        350, 150, Qt.AspectRatioMode.KeepAspectRatio
                    )
                )

        upload.clicked.connect(lambda: guarded(dialog, "图片未加载", choose))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(buttons)
        buttons.rejected.connect(dialog.reject)

        def save():
            params = json.loads(parameters.toPlainText() or "{}")
            if not isinstance(params, dict):
                raise ValueError("车辆参数必须是 JSON 对象")
            params["line_name"] = (
                line.currentText().split(" · ", 1)[-1] if line.currentData() else ""
            )
            candidate = replace(
                value,
                name=name.text().strip(),
                model=model.text().strip(),
                code=code.text().strip(),
                mode=mode.currentData(),
                infrastructure_line_id=line.currentData(),
                parameters=params,
            )
            from railscope.services.vehicles import validate_vehicle

            validate_vehicle(candidate)
            if repo:
                from railscope.services.vehicles import validate_vehicle_assignment

                validation_repo = copy(repo)
                validation_repo.vehicles = self.store.collection("vehicles")
                validation_repo.vehicles[candidate.id] = candidate
                validate_vehicle_assignment(validation_repo, candidate.id)
            metro_references = [
                t
                for t in self.desk.operations.plan.trains
                if t.get("extensions", {})
                .get("railscope.org/vehicle", {})
                .get("vehicle_id")
                == candidate.id
            ]
            if metro_references and candidate.mode != "metro":
                raise ValueError("车辆已被地铁车次引用，不能改成铁路制式")
            if chosen[0]:
                asset[0] = (
                    "assets/vehicles/"
                    + value.id
                    + "-"
                    + new_id("IMG")
                    + Path(chosen[0]).suffix.lower()
                )
                target = self.store.path.parent / asset[0]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(chosen[0], target)
            self.store.put("vehicles", replace(candidate, photo_asset=asset[0]))
            self.refresh_vehicles()
            dialog.accept()

        buttons.accepted.connect(lambda: guarded(dialog, "车辆未保存", save))
        dialog.exec()

    def assign_vehicle(self):
        ident = self.selected_vehicle()
        vehicle = self.store.collection("vehicles")[ident]
        editor = (
            self.desk.rail_operations
            if vehicle.mode == "rail"
            else self.desk.operations
        )
        if not editor.selected_train:
            raise ValueError("请先在运行模块选中一趟车次")
        if vehicle.mode == "rail":
            before = editor.document()
            payload = deepcopy(before)
            train = next(
                t for t in payload["trains"] if t["id"] == editor.selected_train
            )
            train.setdefault("extensions", {})["railscope.org/vehicle"] = {
                "vehicle_id": ident
            }
            editor.accept_batch(payload, before, editor.selected_train)
        else:
            before = editor.snapshot_state()
            train = editor.plan.train(editor.selected_train)
            old = deepcopy(train)
            try:
                if not train.get("cycle_id"):
                    train["vehicle_id"] = ident
                train.setdefault("extensions", {})["railscope.org/vehicle"] = {
                    "vehicle_id": ident
                }
                duties = sorted(
                    (t["stops"][0]["departure_s"], t["stops"][-1]["arrival_s"])
                    for t in editor.plan.trains
                    if t.get("extensions", {})
                    .get("railscope.org/vehicle", {})
                    .get("vehicle_id")
                    == ident
                )
                if any(a[1] > b[0] for a, b in zip(duties, duties[1:])):
                    raise ValueError("该车辆的担当时间重叠")
                editor.plan.validate()
            except Exception:
                train.clear()
                train.update(old)
                raise
            editor.undo_stack.append(before)
            editor.redo_stack.clear()
            editor.changed("已分配真实车辆")
        editor.save()
        self.desk.statusBar().showMessage("车次已引用车辆 " + vehicle.name, 5000)

    def show_duties(self):
        ident = self.selected_vehicle()
        vehicle = self.store.collection("vehicles")[ident]
        if vehicle.mode == "rail":
            rows = [
                (
                    r["service_date"],
                    r["train_number"],
                    clock_text(r["start_s"]),
                    clock_text(r["end_s"]),
                    r["origin_station_id"],
                    r["destination_station_id"],
                )
                for r in vehicle_duties(self.repo(), ident)
            ]
        else:
            plan = self.desk.operations.plan
            rows = [
                (
                    getattr(plan, "service_date", "计划日期"),
                    t["id"],
                    clock_text(t["stops"][0]["departure_s"]),
                    clock_text(t["stops"][-1]["arrival_s"]),
                    t["stops"][0]["station_id"],
                    t["stops"][-1]["station_id"],
                )
                for t in sorted(plan.trains, key=lambda t: t["stops"][0]["departure_s"])
                if t.get("extensions", {})
                .get("railscope.org/vehicle", {})
                .get("vehicle_id")
                == ident
            ]
        dialog = QDialog(self.desk)
        dialog.setWindowTitle(vehicle.name + " · 车辆担当")
        dialog.resize(1000, 500)
        layout = QVBoxLayout(dialog)
        layout.addWidget(
            table_widget(["日期", "车次", "出发", "到达", "起点", "终点"], rows)
        )
        layout.addWidget(
            QLabel("担当从车次引用反向派生。跨车次未定义的回库 / 空驶不会自动补造。")
        )
        self.show_dialog(dialog)

    def choose_corridor(self):
        repo = self.repo()
        if not repo.corridors:
            raise ValueError("没有可分析的完整通道")
        dialog = QDialog(self.desk)
        dialog.setWindowTitle("选择完整通道")
        form = QFormLayout(dialog)
        combo = QComboBox()
        for value in repo.corridors.values():
            combo.addItem(value.name, value.id)
        form.addRow(combo)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        return (
            (repo, combo.currentData())
            if dialog.exec() == QDialog.DialogCode.Accepted
            else (None, None)
        )

    def show_corridor_diagram(self):
        repo, ident = self.choose_corridor()
        if not ident:
            return
        dialog = QDialog(self.desk)
        dialog.setWindowTitle("通道列车运行图 · " + repo.corridors[ident].name)
        dialog.resize(1150, 760)
        layout = QVBoxLayout(dialog)
        row = QHBoxLayout()
        day = QDateEdit(
            QDate.fromString(
                self.desk.rail_operations.rail_payload["service_date"], "yyyy-MM-dd"
            )
        )
        day.setCalendarPopup(True)
        width = QDoubleSpinBox()
        width.setRange(0.1, 20)
        width.setValue(1.5)
        font = QSpinBox()
        font.setRange(6, 72)
        font.setValue(12)
        row.addWidget(QLabel("服务日期"))
        row.addWidget(day)
        row.addWidget(QLabel("线宽"))
        row.addWidget(width)
        row.addWidget(QLabel("全部标注字号"))
        row.addWidget(font)
        layout.addLayout(row)
        view = QSvgWidget()
        view.setMinimumHeight(500)
        layout.addWidget(view, 1)
        svg = [""]

        def refresh():
            svg[0] = corridor_diagram_svg(
                repo,
                ident,
                day.date().toString("yyyy-MM-dd"),
                width.value(),
                font.value(),
            )
            view.load(svg[0].encode("utf-8"))

        refresh()
        width.valueChanged.connect(refresh)
        font.valueChanged.connect(refresh)
        day.dateChanged.connect(refresh)

        def export():
            from station_diagram_render import write_diagram
            from station_diagram_layout import DiagramOptions

            path, _ = QFileDialog.getSaveFileName(
                dialog, "导出通道运行图", "", "SVG (*.svg);;PNG (*.png);;PDF (*.pdf)"
            )
            if path:
                write_diagram(path, svg[0], DiagramOptions())

        self.button(layout, "导出 SVG / PNG / PDF", export)
        self.show_dialog(dialog)

    def show_section_statistics(self):
        repo, ident = self.choose_corridor()
        if not ident:
            return
        corridor = repo.corridors[ident]
        dialog = QDialog(self.desk)
        dialog.setWindowTitle("跨通道物理区间统计")
        dialog.resize(1100, 650)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        first, last = QComboBox(), QComboBox()
        for r in corridor.edge_refs:
            label = f"{r.sequence} · {r.edge_id} · {r.start_distance_m / 1000:.3f}—{r.end_distance_m / 1000:.3f} km"
            first.addItem(label, r.sequence - 1)
            last.addItem(label, r.sequence - 1)
        last.setCurrentIndex(last.count() - 1)
        start, end = QLineEdit("00:00"), QLineEdit("24:00")
        day = QLineEdit(self.desk.rail_operations.rail_payload["service_date"])
        for label, control in [
            ("区间首 edge", first),
            ("区间末 edge", last),
            ("服务日期", day),
            ("进入时间起点", start),
            ("进入时间终点（不含）", end),
        ]:
            form.addRow(label, control)
        layout.addLayout(form)
        summary = QLabel()
        summary.setWordWrap(True)
        layout.addWidget(summary)
        table = table_widget(
            ["车次", "方向", "类型", "客货", "进入", "离开", "运行秒", "速度 km/h"], []
        )
        layout.addWidget(table, 1)
        result = [None]

        def calculate():
            from operating import parse_time

            a, b = first.currentData(), last.currentData()
            if a > b:
                raise ValueError("首 edge 必须先于末 edge")
            result[0] = section_statistics(
                repo,
                corridor.edge_refs[a : b + 1],
                day.text().strip(),
                parse_time(start.text()),
                parse_time(end.text()),
            )
            data = result[0]
            summary.setText(
                f"通过 {data['count']} 次；每小时密度 {data['density_per_hour']:.2f}；平均速度 {data['average_speed_kmh']} km/h；平均运行 {data['average_runtime_s']} 秒。方向 {data['by_direction']}；车种 {data['by_category']}；客货 {data['by_traffic_type']}；未解析车次 {len(data['unresolved_run_ids'])}。仅统计当前导入 / 自建数据。"
            )
            table.setRowCount(len(data["passages"]))
            for i, r in enumerate(data["passages"]):
                values = [
                    r["train_number"],
                    "正向" if r["direction"] == "forward" else "反向",
                    r["category"],
                    r["traffic_type"],
                    clock_text(r["enter_s"]),
                    clock_text(r["leave_s"]),
                    round(r["runtime_s"], 2),
                    None if r["speed_kmh"] is None else round(r["speed_kmh"], 2),
                ]
                for j, value in enumerate(values):
                    table.setItem(
                        i, j, QTableWidgetItem("未知" if value is None else str(value))
                    )

        self.button(layout, "统计所有通道在此物理区间的车次", calculate)

        def export():
            if result[0] is None:
                calculate()
            path, _ = QFileDialog.getSaveFileName(
                dialog, "导出区间统计", "", "JSON (*.json)"
            )
            if path:
                Path(path).write_text(
                    json.dumps(result[0], ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

        self.button(layout, "导出统计结果", export)
        calculate()
        self.show_dialog(dialog)

    def station_id(self, repo):
        props = self.desk.selected_data.get("properties", {})
        record = self.desk._rail_station_record_for_feature(self.desk.selected_data)
        source = record.get("id") if isinstance(record, dict) else None
        candidates = {
            str(props.get(key, ""))
            for key in (
                "station_id",
                "infrastructure_id",
                "station_source_id",
                "station_key",
                "osm_node_id",
            )
        }
        if source:
            candidates.add(str(source))
        for station in repo.stations.values():
            if station.id in candidates or any(
                alias in candidates
                or alias.removeprefix("node/") in candidates
                or alias.replace("osm:node:", "node/") in candidates
                or alias.removeprefix("osm:node:") in candidates
                for alias in station.source_member_ids
            ):
                return station.id
        return None

    def show_station(self, station_id=None):
        from railscope.repository import RailRepository

        repo = self.desk.rail_operations.domain_repo or RailRepository()
        station_id = station_id or self.station_id(repo)
        if not station_id and self.desk._rail_station_record_for_feature(
            self.desk.selected_data
        ):
            selected = self.desk.selected_station_tracks()
            if selected is None:
                return
            station_repo, _, _ = selected
            repo = copy(repo)
            for kind in ("stations", "nodes", "edges", "station_tracks"):
                setattr(
                    repo, kind, {**getattr(repo, kind), **getattr(station_repo, kind)}
                )
            station_id = next(iter(station_repo.stations), None)
        if not station_id:
            if not repo.stations:
                raise ValueError("当前运行计划没有可查询的车站")
            dialog = QDialog(self.desk)
            dialog.setWindowTitle("选择车站")
            form = QFormLayout(dialog)
            combo = QComboBox()
            for station in repo.stations.values():
                combo.addItem(station.name, station.id)
            form.addRow(combo)
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok
                | QDialogButtonBox.StandardButton.Cancel
            )
            form.addRow(buttons)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            station_id = combo.currentData()
        StationRuntime(self, repo, station_id).show_nonmodal()

    def configure_station_route(self):
        editor = self.desk.rail_operations
        if not editor.selected_train:
            raise ValueError("请在运行模块选中需要停靠的车次，并在地图中选择其车站")
        selected = self.desk.selected_station_tracks()
        if selected is None:
            return
        station_repo, rows, context = selected
        return configure_route(self, editor, station_repo, rows, context)

    def locate_selected(self):
        geometry = self.desk.selected_data.get("geometry", {})
        points = []

        def visit(value):
            if value and isinstance(value[0], (int, float)):
                points.append(value)
            else:
                for child in value:
                    visit(child)

        visit(geometry.get("coordinates", []))
        if not points:
            raise ValueError("当前对象没有可定位的空间几何")
        if len(points) == 1:
            self.desk.map.call("focus", *points[0][:2], 14)
        else:
            self.desk.map.call(
                "focusBounds",
                [
                    [min(p[0] for p in points), min(p[1] for p in points)],
                    [max(p[0] for p in points), max(p[1] for p in points)],
                ],
            )

    def refresh_details(self, rows):
        views = self.desk.detail_views
        views["地图"].set_rows(
            [
                (key, value)
                for key, value in rows
                if any(word in key for word in ("编号", "来源", "位置", "经度", "纬度"))
            ]
            or [("空间引用", "选中对象的实际地图几何")]
        )
        views["基础设施"].set_rows(
            [
                (key, value)
                for key, value in rows
                if any(
                    word in key
                    for word in (
                        "线",
                        "车站",
                        "速度",
                        "轨",
                        "设施",
                        "电气",
                        "里程",
                        "等级",
                        "站台",
                        "道岔",
                        "用途",
                        "类型",
                    )
                )
            ]
            or [("专业属性", "详见概览与全部属性")]
        )
        views["运行"].set_rows(
            [
                ("关联模型", "Station / Corridor / TrainRun / StopTime"),
                ("站台与进路", "属于具体车次的停站计划"),
                ("时刻表来源", "当前导入与自建车次，共享目录中的同一车站对象"),
            ]
        )
        views["统计"].set_rows(
            [
                ("统计口径", "完整物理区间；跨所有已导入通道"),
                ("客货类型", "未明确标注的车次保持未知"),
            ]
        )
        self.refresh_history_details()

    def refresh_history_details(self):
        views = self.desk.detail_views
        props = self.desk.selected_data.get("properties", {})
        from railscope.services.history import lifecycle_state

        history = self.desk.history
        layer = self.desk.selected_data.get("layer", "")
        mode = (
            "metro"
            if layer
            in ("metro", "stations", "construction", "areas-fill", "areas-outline")
            else "road"
            if layer.startswith("road")
            else "other"
            if layer.startswith("imported")
            else "rail"
        )
        value = history.effective_for_feature(props, mode)
        views["历史"].set_rows(
            [
                (
                    "开始建设",
                    value.construction_started
                    if value and value.construction_started
                    else "未知",
                ),
                ("开通运营", value.opened if value and value.opened else "未知"),
                (
                    "结束运营",
                    value.closed
                    if value and value.closed
                    else "未记录（已知运营时间可延续至未来）",
                ),
                ("查看日期", self.session.day),
                ("有效状态", lifecycle_state(value, self.session.day)),
                *(("开通记录 · " + event.get('scope','车站'),
                   event['date'] + ' · ' + '、'.join(event.get('lines', [])))
                  for event in (value.provenance.get('events', []) if value else [])),
                *(((("资料来源", value.provenance.get('source_url','')),
                   ("核验状态", value.verification_status))) if value and value.provenance else ()),
            ]
        )

    def show_line_technical(self):
        from line_metadata import RAIL_LINE_FIELDS, source_line_attributes
        from property_overview import PropertyOverview

        props = self.desk.selected_data.get("properties", {})
        kind = "metro" if self.desk.selected_data.get("layer") == "metro" else "rail"
        values = source_line_attributes(
            props, kind, props.get("technical_attributes", {})
        )
        dialog = QDialog(self.desk)
        dialog.setWindowTitle("线路档案与技术视图")
        dialog.resize(800, 650)
        layout = QVBoxLayout(dialog)
        overview = PropertyOverview()
        overview.set_rows(
            [
                (label, values.get(key) or "未知")
                for key, label, hint in RAIL_LINE_FIELDS
            ]
        )
        layout.addWidget(overview, 1)
        repo = getattr(self.desk.rail_operations, "domain_repo", None)
        if repo:
            source = props.get("line_id")
            line_id = (
                source
                if source in repo.lines
                else self.desk.rail_operations.domain_bindings.get("lines", {}).get(
                    source
                )
            )
            edges = (
                [e for e in repo.edges.values() if e.infrastructure_line_id == line_id]
                if line_id
                else []
            )
            layout.addWidget(
                table_widget(
                    [
                        "物理区间",
                        "长度 m",
                        "轨道类型",
                        "电气化",
                        "桥 / 隧",
                        "坡度 / 限速",
                    ],
                    [
                        (
                            e.id,
                            round(e.length_m, 2),
                            e.track_role,
                            e.source_tags.get("electrified"),
                            e.source_tags.get("bridge") or e.source_tags.get("tunnel"),
                            e.source_tags.get("incline")
                            or e.source_tags.get("maxspeed"),
                        )
                        for e in edges
                    ],
                )
            )
        layout.addWidget(
            QLabel(
                "线路基础档案与已载入真实区间技术属性。未提供的坡度、限速、桥隧等不推测。"
            )
        )
        self.show_dialog(dialog)

    def export_corridor_archive(self):
        repo, ident = self.choose_corridor()
        if not ident:
            return
        if not self.desk.map.is_ready:
            raise ValueError("地图尚未就绪")
        if getattr(self.desk, "_capture_path", None) or getattr(
            self.desk, "_archive_capture", None
        ):
            raise ValueError("另一个地图导出尚在进行")
        path, file_filter = QFileDialog.getSaveFileName(
            self.desk, "导出通道档案", "", "PDF (*.pdf);;HTML (*.html)"
        )
        if not path:
            return
        if not Path(path).suffix:
            path += ".pdf" if "PDF" in file_filter else ".html"
        if Path(path).suffix.lower() not in {".pdf", ".html"}:
            raise ValueError("通道档案仅支持 PDF / HTML")
        archive = corridor_archive(repo, ident)
        features = []
        for ref in repo.corridors[ident].edge_refs:
            edge = repo.edges[ref.edge_id]
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "corridor_id": ident,
                        "network_edge_id": edge.id,
                        "color": "#a52432",
                    },
                    "geometry": {
                        "type": "LineString",
                        "coordinates": edge.coordinates
                        if ref.forward
                        else tuple(reversed(edge.coordinates)),
                    },
                }
            )
        self.desk.map.call(
            "setRailPlan", {"type": "FeatureCollection", "features": features}
        )
        coords = [
            point
            for feature in features
            for point in feature["geometry"]["coordinates"]
        ]
        self.desk.map.call(
            "focusBounds",
            [
                [min(p[0] for p in coords), min(p[1] for p in coords)],
                [max(p[0] for p in coords), max(p[1] for p in coords)],
            ],
        )

        def captured(data):
            if not data.startswith("data:image/png;base64,"):
                raise ValueError("通道地图截图失败：" + data[:200])
            itinerary = (
                " → ".join(escape(item["name"]) for item in archive["itinerary"])
                or "按下列完整物理边序列运行"
            )
            stations = " → ".join(escape(item["name"]) for item in archive["stations"])
            points = (
                " → ".join(
                    escape(item["name"]) for item in archive["operational_points"]
                )
                or "没有已录入的途经线路所 / 控制点"
            )
            edge_rows = "".join(
                "<tr><td>"
                + escape(r["id"])
                + "</td><td>"
                + r["direction"]
                + "</td><td>"
                + escape(r["line_id"] or "未知")
                + "</td><td>"
                + f"{r['start_m'] / 1000:.3f}—{r['end_m'] / 1000:.3f}"
                + "</td></tr>"
                for r in archive["edges"]
            )
            html = f'<html><head><meta charset="utf-8"/></head><body><h1>{escape(archive["name"])} · 通道档案</h1><p>{escape(archive["id"])} · {escape(archive["verification_status"])} · {archive["length_m"] / 1000:.3f} km</p><img width="680" src="{data}"/><p>实际地图截图；© OpenStreetMap contributors</p><div style="page-break-before:always"><h2>端点—线路序列</h2><p>{itinerary}</p><h2>途经车站</h2><p>{stations}</p><h2>途经线路所 / 控制点</h2><p>{points}</p><h2>完整有向物理路径</h2><table border="1"><tr><th>区间编号</th><th>方向</th><th>线路编号</th><th>累计 km</th></tr>{edge_rows}</table><p>来源：{escape(archive["source_id"] or "未记录")}；自动参考路径不代表经核验的调度进路。</p></div></body></html>'
            if Path(path).suffix.lower() == ".pdf":
                from PySide6.QtPrintSupport import QPrinter

                document = QTextDocument()
                document.setHtml(html)
                printer = QPrinter()
                printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
                printer.setOutputFileName(path)
                printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
                document.print_(printer)
                if not Path(path).exists() or Path(path).stat().st_size < 500:
                    raise OSError("PDF 未成功写入")
            else:
                Path(path).write_text(html, encoding="utf-8")
            self.desk.statusBar().showMessage("通道档案已导出：" + path, 6000)

        def completed(data):
            try:
                guarded(self.desk, "通道档案未导出", lambda: captured(data))
            finally:
                self.desk.rail_operations.push_corridors()

        self.desk._archive_capture = completed
        self.desk.map.call("captureMap", True)


class StationRuntime(QDialog):
    def __init__(self, workbench, repo, station_id):
        super().__init__(workbench.desk)
        self.workbench, self.repo, self.station_id = workbench, repo, station_id
        self.setWindowTitle(repo.stations[station_id].name + " · 时刻表 / 站场 / 占用")
        self.resize(1150, 800)
        layout = QVBoxLayout(self)
        self.day = QLineEdit(workbench.session.day)
        self.time_label = QLabel()
        row = QHBoxLayout()
        row.addWidget(QLabel("全局服务日期"))
        row.addWidget(self.day)
        row.addWidget(self.time_label)
        layout.addLayout(row)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 172799)
        self.slider.setValue(int(workbench.session.seconds))
        layout.addWidget(self.slider)
        self.slider.valueChanged.connect(workbench.session.set_seconds)
        self._last_render = 0
        self._last_second = None
        workbench.session.changed.connect(self.update_time)
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)
        self.table = table_widget(
            ["车次", "到达", "发车", "停靠 / 通过", "站台", "股道", "进路状态"], []
        )
        tabs.addTab(self.table, "车站时刻表")
        self.occupation = QSvgWidget()
        tabs.addTab(self.occupation, "股道时间图")
        self.plan = QSvgWidget()
        tabs.addTab(self.plan, "站场实时状态")
        self.base_svg = None
        self.plan_layout = None
        self.station_repo = None
        try:
            from station_tracks import load_station_tracks
            from station_diagram_layout import DiagramOptions, build_layout
            from station_diagram_render import render_svg

            station = repo.stations[station_id]
            source = next(
                (
                    s.replace("osm:node:", "node/")
                    for s in station.source_member_ids
                    if s.startswith(("osm:node:", "node/"))
                ),
                None,
            )
            if source:
                station_repo, rows, context = load_station_tracks(
                    workbench.desk.rail_catalog_widget.directory,
                    workbench.desk.rail_operations.workspace_identity_path,
                    {"name": station.name, "station_source_id": source},
                    workbench.desk.rail_catalog_widget.overrides,
                )
                self.station_repo = station_repo
                options = DiagramOptions(width=1400, label_size=18, title_size=28)
                self.plan_layout = build_layout(station_repo, context, options)
                self.base_svg = render_svg(station_repo, context, options, layout=self.plan_layout)
        except (ValueError, KeyError, OSError, sqlite3.Error) as error:
            layout.addWidget(QLabel("站场拓扑 / 真实站台未齐备：" + str(error)))
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        def set_service_day():
            try:
                workbench.session.set_day(self.day.text().strip())
            finally:
                self.day.setText(workbench.session.day)

        self.day.editingFinished.connect(
            lambda: guarded(self, "日期无效", set_service_day)
        )
        workbench.session.calendar_changed.connect(self.calendar_changed)
        workbench.desk.rail_operations.updated.connect(self.plan_changed)
        workbench.desk.rail_operations.station_labels_changed.connect(self.labels_changed)
        self.refresh()
        self.update_time()
        self.table.cellDoubleClicked.connect(self.jump_row)

    def show_nonmodal(self):
        self.workbench.show_dialog(self)

    def plan_changed(self):
        from railscope.repository import RailRepository

        latest = self.workbench.desk.rail_operations.domain_repo or RailRepository()
        if self.station_id not in latest.stations:
            latest = copy(latest)
            for kind in ("stations", "nodes", "edges", "station_tracks"):
                existing = {
                    key: value
                    for key, value in getattr(self.repo, kind).items()
                    if kind in ("stations", "nodes", "edges")
                    or value.station_id == self.station_id
                }
                setattr(latest, kind, {**existing, **getattr(latest, kind)})
        self.repo = latest
        self.setWindowTitle(
            self.repo.stations[self.station_id].name + " · 时刻表 / 站场 / 占用"
        )
        self.calendar_changed()

    def labels_changed(self, names):
        editor = self.workbench.desk.rail_operations
        source_ids = editor.domain_bindings.get('station_sources', {})
        if self.station_id not in {source_ids.get(key.removeprefix('station:')) for key in names}:
            return
        latest = editor.domain_repo.stations.get(self.station_id)
        if latest:
            self.repo.stations[self.station_id] = latest
            self.setWindowTitle(latest.name + ' · 时刻表 / 站场 / 占用')

    def calendar_changed(self):
        self.day.setText(self.workbench.session.day)
        self.refresh()
        self._last_render = 0
        self._last_second = None
        self.update_time()

    def refresh(self):
        self.rows = station_timetable(
            self.repo, self.station_id, self.day.text().strip()
        )
        self.table.setRowCount(len(self.rows))
        for i, row in enumerate(self.rows):
            platform = self.repo.platforms.get(row["platform_id"])
            track = self.repo.station_tracks.get(row["station_track_id"])
            values = [
                row["train_number"],
                clock_text(row["arrival_s"]),
                clock_text(row["departure_s"]),
                row["stop_type"],
                platform.name if platform else None,
                track.name if track else None,
                row["route_status"],
            ]
            for j, value in enumerate(values):
                self.table.setItem(
                    i, j, QTableWidgetItem("未指定" if value is None else str(value))
                )
        occupied = station_track_intervals(
            self.repo, self.station_id, self.day.text().strip()
        )
        tracks = sorted({r["station_track_id"] for r in occupied})
        start = min((r["arrival_s"] for r in occupied), default=0)
        end = max((r["departure_s"] for r in occupied), default=start + 3600)
        end = max(start + 3600, end)
        height = max(600, 100 + len(tracks) * 40)
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{height}"><rect width="100%" height="100%" fill="white"/><g font-family="Microsoft YaHei" font-size="14">'
        ]
        for i, key in enumerate(tracks):
            y = 45 + i * 40
            parts.append(
                f'<text x="8" y="{y + 20}">{escape(self.repo.station_tracks[key].name)}</text>'
            )
            for r in occupied:
                if r["station_track_id"] != key:
                    continue
                x = 180 + (r["arrival_s"] - start) / (end - start) * 950
                length = (r["departure_s"] - r["arrival_s"]) / (end - start) * 950
                parts.append(
                    f'<rect x="{x:.2f}" y="{y}" width="{max(2, length):.2f}" height="22" fill="#a52432"/><text x="{x:.2f}" y="{y - 3}">{escape(r["train_number"])}</text>'
                )
        parts.append(
            f'<text x="180" y="{height - 20}">{clock_text(start)} — {clock_text(end)} · 真实路径与时刻表的车头占用参考；未计列车尾部及联锁释放</text></g></svg>'
        )
        self.occupation.load("".join(parts).encode("utf-8"))

    def update_time(self):
        time = self.workbench.session.seconds
        self.time_label.setText("全局时间 " + clock_text(time))
        self.slider.blockSignals(True)
        self.slider.setValue(int(time))
        self.slider.blockSignals(False)
        now = monotonic()
        if self._last_render and (
            int(time) == self._last_second or now - self._last_render < 0.2
        ):
            return
        self._last_render = now
        self._last_second = int(time)
        state = station_state(self.repo, self.station_id, self.day.text().strip(), time)
        self.status.setText(
            "当前本站列车："
            + ("、".join(r["train_number"] for r in state["trains"]) or "无")
            + "；已指定股道 "
            + str(len(state["tracks"]))
            + "；未知站台与未核验进路不会补造。"
        )
        if self.base_svg and self.plan_layout:
            overlay = []
            for track_id, track_state in state["tracks"].items():
                track = self.repo.station_tracks[track_id]
                for ref in track.edge_refs:
                    drawing = self.plan_layout.edges.get(ref.edge_id)
                    if drawing:
                        points = " ".join(f"{x:.2f},{y:.2f}" for x, y in drawing.points)
                        color = (
                            "#a52432"
                            if track_state["state"] == "occupied"
                            else "#187d70"
                        )
                        overlay.append(
                            f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="4" opacity=".7"/>'
                        )
            for row in state["trains"]:
                position = self.plan_layout.project(row["coordinate"])
                route = self.repo.station_routes.get(row["station_route_id"])
                if route:
                    for ref in route.edge_refs:
                        drawing = self.plan_layout.edges.get(ref.edge_id)
                        if drawing:
                            points = " ".join(
                                f"{x:.2f},{y:.2f}" for x, y in drawing.points
                            )
                            overlay.append(
                                f'<polyline points="{points}" fill="none" stroke="#df9532" stroke-width="6" opacity=".75"/>'
                            )
                overlay.append(
                    f'<circle cx="{position[0]:.2f}" cy="{position[1]:.2f}" r="8" fill="#a52432"/><text x="{position[0] + 10:.2f}" y="{position[1] - 8:.2f}" font-size="18">{escape(row["train_number"])}</text>'
                )
            self.plan.load(
                self.base_svg.replace("</svg>", "".join(overlay) + "</svg>").encode(
                    "utf-8"
                )
            )

    def jump_row(self, row, column):
        value = self.rows[row]
        time = (
            value["arrival_s"]
            if value["arrival_s"] is not None
            else value["departure_s"]
        )
        self.workbench.session.set_seconds(time)
        station = self.repo.stations[self.station_id]
        self.workbench.desk.map.call("focus", station.lon, station.lat, 14)


def configure_route(workbench, editor, station_repo, rows, context):
    from railscope.services.station_routing import resolve_station_route
    from station_tracks import track_overrides

    base = workbench.repo()
    run_id = editor.domain_bindings["train_runs"][editor.selected_train]
    run = base.train_runs[run_id]
    station = next(iter(station_repo.stations.values()))
    stop_matches = [s for s in base.stops_for(run_id) if s.station_id == station.id]
    if len(stop_matches) != 1:
        raise ValueError("选中车站必须是当前车次唯一的一次停靠站；请先在停站表中添加它")
    stop = stop_matches[0]
    if (
        stop.arrival_time_s is None
        or stop.departure_time_s is None
        or stop.departure_time_s <= stop.arrival_time_s
    ):
        raise ValueError("该车次在此站未定义停车，请先填写到发时刻")
    corridor = base.corridors[run.corridor_id]
    node_ids = [corridor.origin_node_id] + [
        base.edges[r.edge_id].to_node_id
        if r.forward
        else base.edges[r.edge_id].from_node_id
        for r in corridor.edge_refs
    ]
    common = [(i, key) for i, key in enumerate(node_ids) if key in station_repo.nodes]
    if len(common) < 2:
        raise ValueError(
            "站区已载入拓扑没有两个可作为通道入口、出口的真实边界；请扩展站场拓扑或手工检查"
        )
    dialog = QDialog(workbench.desk)
    dialog.setWindowTitle("车次站台与真实站内进路（自动参考）")
    dialog.resize(700, 430)
    form = QFormLayout(dialog)
    track = QComboBox()
    for item in station_repo.station_tracks.values():
        track.addItem(item.name + " · " + item.id, item.id)
    platform = QComboBox()
    platform.addItem("站台未录入，明确指定股道", None)
    for feature in context:
        p = feature.get("properties", {})
        tags = p.get("way_tags", {})
        if (
            p.get("kind") != "platform"
            and tags.get("railway") != "platform"
            and "platform" not in str(p.get("asset_kind", ""))
            and p.get("boundary_kind") != "platform"
        ):
            continue
        source = p.get("infrastructure_id") or (
            "way/" + str(p["osm_way_id"])
            if "osm_way_id" in p
            else "relation/" + str(p["osm_relation_id"])
            if "osm_relation_id" in p
            else None
        )
        if source:
            platform.addItem(str(p.get("name") or tags.get("ref") or source), source)
    entry, exit = QComboBox(), QComboBox()
    for i, key in common:
        label = f"通道节点 {i + 1} · {key}"
        entry.addItem(label, key)
        exit.addItem(label, key)
    exit.setCurrentIndex(exit.count() - 1)
    for label, control in [
        ("实际站台", platform),
        ("站台关联股道", track),
        ("进站通道边界", entry),
        ("出站通道边界", exit),
    ]:
        form.addRow(label, control)
    info = QLabel(
        "从实际载入的站区轨道寻找经过所选股道的连续进出站路径。平台与股道关联由本次人工选择确认；自动路径保留全部边选择并标记为参考，不代表联锁进路。"
    )
    info.setWordWrap(True)
    form.addRow(info)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
    )
    form.addRow(buttons)
    buttons.rejected.connect(dialog.reject)

    def save():
        if node_ids.index(entry.currentData()) >= node_ids.index(exit.currentData()):
            raise ValueError("入口必须先于出口")
        resolution = resolve_station_route(
            station_repo,
            station.id,
            entry.currentData(),
            exit.currentData(),
            track.currentData(),
        )
        if resolution["status"] == "unresolved":
            raise ValueError(resolution["reason"])
        before = editor.document()
        payload = deepcopy(before)
        route_id = new_id("SR")

        # Source aliases live only in the compatibility DTO; canonical IDs are
        # assigned by the shared adapter when the validated plan is published.
        def source_node(key):
            values = station_repo.nodes[key].source_node_ids
            if len(values) != 1:
                raise ValueError("站区节点没有唯一来源绑定")
            return values[0]

        legs = [
            {
                "edge_id": station_repo.edges[r.edge_id].source_id,
                "direction": "forward" if r.forward else "reverse",
            }
            for r in resolution["edge_refs"]
        ]
        source_station = next(
            s.replace("osm:node:", "node/")
            for s in station.source_member_ids
            if s.startswith(("node/", "osm:node:"))
        )
        payload.setdefault("station_routes", []).append(
            {
                "id": route_id,
                "station_id": str(next(iter(stop_matches)).station_id),
                "entry_node_id": source_node(entry.currentData()),
                "exit_node_id": source_node(exit.currentData()),
                "edge_refs": legs,
                "source": "station_topology",
                "verification_status": "automatic_reference",
                "provenance": {
                    "station_source": source_station,
                    "selected_track_id": track.currentData(),
                },
            }
        )
        # Adapter accepts station source aliases; keep ownership explicit.
        target = next(t for t in payload["trains"] if t["id"] == editor.selected_train)[
            "stops"
        ][stop.sequence - 1]
        payload["station_routes"][-1]["station_id"] = str(target["node_id"])
        selected_track = station_repo.station_tracks[track.currentData()]
        member = selected_track.edge_refs[len(selected_track.edge_refs) // 2]
        source_edge = station_repo.edges[member.edge_id].source_id
        target.update(station_track_id=source_edge, station_route_id=route_id)
        target.setdefault("extensions", {}).pop("railscope.org/track-position", None)
        target.pop("platform_id", None)
        target.pop("platform_ref", None)
        if platform.currentData():
            target["platform_ref"] = platform.currentData()
        # Persist the actual selected station tracks via the existing override
        # layer, so the adapter sees the same stable StationTrack references.
        workbench.desk.rail_catalog_widget._save_local_overrides(
            track_overrides(station_repo, rows)
        )
        editor.accept_batch(payload, before, editor.selected_train)
        if platform.currentData():
            run_id = editor.domain_bindings["train_runs"][editor.selected_train]
            saved_stop = editor.domain_repo.stops_for(run_id)[stop.sequence - 1]
            platform_entity = editor.domain_repo.platforms[saved_stop.platform_id]
            workbench.store.put(
                "platforms",
                replace(
                    platform_entity,
                    name=platform.currentText(),
                    station_track_ids=tuple(
                        sorted({*platform_entity.station_track_ids, selected_track.id})
                    ),
                    verification_status="user_defined",
                ),
            )
        editor.save()
        dialog.accept()

    buttons.accepted.connect(lambda: guarded(dialog, "站内进路未保存", save))
    dialog.exec()
