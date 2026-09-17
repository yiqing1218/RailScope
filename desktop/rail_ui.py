"""Independent train-number workspace backed by explicit cross-line paths."""

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QMessageBox,
    QTableWidgetItem,
    QDialog,
    QFormLayout,
    QLineEdit,
    QComboBox,
    QDialogButtonBox,
)

try:
    from .operating_ui import OperationsEditor
    from .operating import Plan, read_plan
    from .rail import (
        compile_rail_plan,
        expanded_document,
        shared_document,
        validate_corridors,
    )
    from .operating import strict_fields
    from .rail_tables import merge_csv, export_csv
    from .operating import parse_time, format_time
except ImportError:
    from operating_ui import OperationsEditor
    from operating import Plan, read_plan
    from rail import (
        compile_rail_plan,
        expanded_document,
        shared_document,
        validate_corridors,
    )
    from operating import strict_fields
    from rail_tables import merge_csv, export_csv
    from operating import parse_time, format_time


class RailMap:
    def __init__(self, map_view):
        self.view = map_view
        self.bridge = map_view.bridge

    @property
    def is_ready(self):
        return self.view.is_ready

    def call(self, method, *args):
        method = {
            "setOperatingVehicles": "setRailOperatingVehicles",
            "setVehicleAppearance": "setRailVehicleAppearance",
        }.get(method, method)
        if method == "setVisibility" and args[0] == "vehicles":
            args = ("railVehicles", *args[1:])
        self.view.call(method, *args)


class RailEditor(OperationsEditor):
    def __init__(self, map_view, directory, path):
        self.directory = Path(directory)
        self.graph = {"edges": [], "points": []}
        self.platforms = (
            json.loads(
                (self.directory / "rail_platforms.geojson").read_text(encoding="utf-8")
            )["features"]
            if (self.directory / "rail_platforms.geojson").exists()
            else []
        )
        self.rail_payload = None
        super().__init__(Plan([], "rail"), RailMap(map_view), [], path)
        self.time_scale = 0.055
        self.time_grid_s = 900
        self.plot_left = 170
        if Path(path).exists():
            try:
                self.apply_payload(read_plan(path))
            except (ValueError, OSError, KeyError, TypeError) as error:
                self.message.setText("国铁计划未载入：" + str(error))
        if self.rail_payload is None:
            reference = json.loads(
                (Path(__file__).parent / "examples/g1-reference.json").read_text(
                    encoding="utf-8"
                )
            )
            self.apply_payload(reference["plan"])
            self.message.setText(
                "内置 G1 · 7站参考时刻 · 已关闭运行；选择车次查看独立时刻表 / 运行图。"
            )

    def play(self):
        if not self.plan.trains:
            self.message.setText("请在国铁运行菜单导入车次计划，或手动新增车次。")
            return
        super().play()

    def apply_payload(self, payload, show_route=False):
        original_payload = shared_document(payload)
        payload = expanded_document(payload)
        try:
            from .rail_store import load_edges
        except ImportError:
            from rail_store import load_edges
        required = [
            leg["edge_id"]
            for route in original_payload["routes"]
            for leg in route["path"]
        ]
        reference = json.loads(
            (Path(__file__).parent / "examples/g1-reference.json").read_text(
                encoding="utf-8"
            )
        )
        reference_mode = (
            payload.get("extensions", {})
            .get("railscope.org/reference", {})
            .get("path_status")
            == "connected_osm_reference_not_dispatch_route"
        )
        reference_edges = (
            {e["id"]: e for e in reference["edges"]} if reference_mode else {}
        )
        bundled = [
            reference_edges[key]
            for key in dict.fromkeys(required)
            if key in reference_edges
        ]
        missing = [key for key in required if key not in reference_edges]
        if missing and not (self.directory / "rail.sqlite").exists():
            raise ValueError("此计划包含便携 G1 示例之外的区间，请先提取国铁基础设施")
        edges = bundled + (load_edges(self.directory, missing) if missing else [])
        points = reference["points"] if reference_mode else []
        if not reference_mode and (self.directory / "rail.sqlite").exists():
            stop_ids = {s["node_id"] for t in payload["trains"] for s in t["stops"]}
            with sqlite3.connect(str(self.directory / "rail.sqlite")) as db:
                for ident in stop_ids:
                    for row in db.execute(
                        "SELECT data FROM features WHERE kind='railPoints' AND json_extract(data,'$.properties.osm_node_id')=?",
                        (ident,),
                    ):
                        points.append(json.loads(row[0]))
        validate_corridors(original_payload["routes"], edges)
        plan, lines = compile_rail_plan(payload, edges, points, self.platforms)
        self.pause()
        self.set_enabled(False)
        self.graph["points"] = points
        self.graph["edges"] = edges
        for profile, train, shared_train in zip(
            lines, payload["trains"], original_payload["trains"]
        ):
            profile["rail_path"] = deepcopy(train["path"])
            profile["corridor_id"] = shared_train["route_id"]
            route = next(
                r
                for r in original_payload["routes"]
                if r["id"] == shared_train["route_id"]
            )
            route.setdefault(
                "name",
                profile["stations"][0]["name"]
                + " → "
                + profile["stations"][-1]["name"]
                + " · 单向参考通道",
            )
            route.setdefault("track_changes", [])
        self.plan = plan
        self.base_lines = lines
        self.rail_payload = original_payload
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.line_combo.blockSignals(True)
        self.line_combo.clear()
        for line in lines:
            self.line_combo.addItem(line["name"], line["id"])
        self.line_combo.blockSignals(False)
        self.line_changed()
        self.changed("国铁径路与时刻已校验；这是时刻仿真，不是联锁安全校验")
        self.clock = min(
            (t["stops"][0]["departure_s"] for t in plan.trains), default=25200
        )
        if hasattr(self, "time_input"):
            self.time_input.setText(
                f"{int(self.clock) // 3600:02}:{int(self.clock) % 3600 // 60:02}:{int(self.clock) % 60:02}"
            )
        self.update_sidebar(0)
        if show_route:
            self.show_reference_route()

    def show_reference_route(self):
        features = []
        drawn_paths, drawn_stations = set(), set()
        selected = self.current_line()
        corridor_id = selected.get("corridor_id") if selected else None
        route = next(
            (
                r
                for r in (self.rail_payload or {}).get("routes", [])
                if r["id"] == corridor_id
            ),
            None,
        )
        trains = [
            t["id"]
            for t in self.plan.trains
            if self.plan.lines[t["line_id"]].get("corridor_id") == corridor_id
        ]
        for line in self.base_lines:
            if line.get("corridor_id") != corridor_id:
                continue
            props = {
                "name": route.get("name", corridor_id) if route else "单向参考通道",
                "source": self.rail_payload["source"],
                "train_id": line["ref"],
                "corridor_id": corridor_id,
                "train_ids": trains,
                "track_changes": (route or {}).get("track_changes", []),
            }
            key = tuple((leg["edge_id"], leg["direction"]) for leg in line["rail_path"])
            if key not in drawn_paths:
                drawn_paths.add(key)
                features.append(
                    {
                        "type": "Feature",
                        "properties": props,
                        "geometry": {
                            "type": "LineString",
                            "coordinates": line["path"]["coordinates"],
                        },
                    }
                )
            for station in line["stations"]:
                from_coords = line["path"]["coordinates"][
                    line["path"]["cumulative"].index(station["distance_m"])
                ]
                station_key = (station["id"], tuple(from_coords))
                if station_key in drawn_stations:
                    continue
                drawn_stations.add(station_key)
                features.append(
                    {
                        "type": "Feature",
                        "properties": {**props, "name": station["name"]},
                        "geometry": {"type": "Point", "coordinates": from_coords},
                    }
                )
        self.map.call(
            "setRailPlan", {"type": "FeatureCollection", "features": features}
        )
        if hasattr(self, "route_switch"):
            self.route_switch.blockSignals(True)
            self.route_switch.setChecked(True)
            self.route_switch.blockSignals(False)
        self.locate_current_line()

    def load_g1_example(self):
        reference = json.loads(
            (Path(__file__).parent / "examples/g1-reference.json").read_text(
                encoding="utf-8"
            )
        )
        self.apply_payload(reference["plan"], show_route=True)
        self.tabs.setCurrentIndex(0)
        self.diagram.fitInView(
            self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio
        )
        self.message.setText(
            "G1 · 7站 · 06:30—11:24 · 公开资料参考，非12306实时/实际股道；可编辑到发时刻，按开始仿真才运行。"
        )

    def reset(self):
        self.clock = min(
            (t["stops"][0]["departure_s"] for t in self.plan.trains), default=25200
        )
        self.update_sidebar(0)
        self.push_positions()

    def locate_current_line(self):
        line = self.current_line()
        if line and line["path"]:
            xs, ys = zip(*line["path"]["coordinates"])
            self.map.call(
                "fit", [[min(xs), min(ys)], [max(xs), max(ys)]], "国铁 · " + line["ref"]
            )

    def sidebar(self):
        try:
            from .components import Switch, switch_row
        except ImportError:
            from components import Switch, switch_row
        body = super().sidebar()
        self.route_switch = Switch(False)
        self.route_switch.toggled.connect(self.set_reference_visible)
        body.widget().layout().insertWidget(
            2, switch_row("共享运行通道 / 控制点", self.route_switch)
        )
        self.time_input.setText(format_time(self.clock))
        return body

    def set_reference_visible(self, on):
        if on and self.plan.trains:
            self.show_reference_route()
        else:
            self.map.call("setVisibility", "railPlan", False)
            if on:
                self.message.setText("请先导入国铁车次计划。")
                self.route_switch.blockSignals(True)
                self.route_switch.setChecked(False)
                self.route_switch.blockSignals(False)

    def document(self):
        if self.rail_payload is None:
            raise ValueError("请先导入国铁车次计划和实际基础设施")
        self.plan.validate()
        payload = expanded_document(self.rail_payload)
        payload["trains"] = []
        for train in self.plan.trains:
            path = deepcopy(self.plan.lines[train["line_id"]]["rail_path"])
            if train["direction"] == "reverse":
                path = [
                    {
                        "edge_id": leg["edge_id"],
                        "direction": "reverse"
                        if leg["direction"] == "forward"
                        else "forward",
                    }
                    for leg in path[::-1]
                ]
            stops = []
            for stop in train["stops"]:
                original = deepcopy(
                    stop.get("extensions", {}).get("railscope.org/rail-stop", {})
                )
                original.update(
                    node_id=int(stop["station_id"]),
                    arrival_s=stop["arrival_s"],
                    departure_s=stop["departure_s"],
                )
                stops.append(original)
            payload["trains"].append(
                {
                    "id": train["id"],
                    "path": path,
                    "stops": stops,
                    "extensions": train.get("extensions", {}),
                }
            )
        compile_rail_plan(
            payload, self.graph["edges"], self.graph["points"], self.platforms
        )
        result = shared_document(payload)
        old_routes = {r["id"]: deepcopy(r) for r in self.rail_payload.get("routes", [])}
        generated = {r["id"]: r for r in result["routes"]}
        used_new = set()
        for train in result["trains"]:
            profile = self.plan.lines["rail/" + train["id"]]
            old = old_routes.get(profile.get("corridor_id"))
            if old and old["path"] == generated[train["route_id"]]["path"]:
                train["route_id"] = old["id"]
            else:
                used_new.add(train["route_id"])
        result["routes"] = list(old_routes.values()) + [
            r
            for key, r in generated.items()
            if key in used_new and key not in old_routes
        ]
        return result

    def variant_changed(self):
        super().variant_changed()
        if (
            hasattr(self, "route_switch")
            and self.route_switch.isChecked()
            and self.rail_payload
        ):
            self.show_reference_route()

    def corridors_document(self):
        payload = self.document()
        return {
            "schema": "railscope.rail-corridors.v1",
            "source": payload["source"],
            "required_capabilities": [],
            "extensions": deepcopy(payload["extensions"]),
            "corridors": deepcopy(payload["routes"]),
        }

    def show_corridor(self, corridor_id, train_id=""):
        route = next(r for r in self.rail_payload["routes"] if r["id"] == corridor_id)
        trains = [
            t
            for t in self.plan.trains
            if self.plan.lines[t["line_id"]].get("corridor_id") == corridor_id
        ]
        if trains:
            ident = (
                train_id
                if any(t["id"] == train_id for t in trains)
                else trains[0]["id"]
            )
            self.line_combo.setCurrentIndex(self.line_combo.findData("rail/" + ident))
            self.show_reference_route()
            return
        lookup = {e["id"]: e for e in self.graph["edges"]}
        first, last = route["path"][0], route["path"][-1]
        start = lookup[first["edge_id"]][
            "from_node" if first["direction"] == "forward" else "to_node"
        ]
        end = lookup[last["edge_id"]][
            "to_node" if last["direction"] == "forward" else "from_node"
        ]
        # Compile an ephemeral preview profile, never add a vehicle or save GIS data.
        payload = {
            "schema": "railscope.rail-plan.v1",
            "service_date": self.rail_payload["service_date"],
            "timezone": "Asia/Shanghai",
            "source": self.rail_payload["source"],
            "extensions": {},
            "required_capabilities": [],
            "trains": [
                {
                    "id": "__preview__",
                    "path": route["path"],
                    "extensions": {},
                    "stops": [
                        {"node_id": start, "arrival_s": 0, "departure_s": 0},
                        {"node_id": end, "arrival_s": 100, "departure_s": 100},
                    ],
                }
            ],
        }
        _, lines = compile_rail_plan(payload, self.graph["edges"], self.graph["points"])
        coords = lines[0]["path"]["coordinates"]
        self.map.call("setRunSystem", "rail")
        self.map.call(
            "setRailPlan",
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "name": route.get("name", corridor_id),
                            "corridor_id": corridor_id,
                            "train_ids": [],
                            "track_changes": route.get("track_changes", []),
                            "source": self.rail_payload["source"],
                        },
                        "geometry": {"type": "LineString", "coordinates": coords},
                    }
                ],
            },
        )
        xs, ys = zip(*coords)
        self.map.call(
            "fit",
            [[min(xs), min(ys)], [max(xs), max(ys)]],
            route.get("name", corridor_id),
        )
        self.route_switch.blockSignals(True)
        self.route_switch.setChecked(True)
        self.route_switch.blockSignals(False)

    def merge_corridors(self, value):
        strict_fields(
            value,
            {"schema", "source", "required_capabilities", "extensions", "corridors"},
            set(),
            "运行通道目录",
        )
        if (
            value["schema"] != "railscope.rail-corridors.v1"
            or value["required_capabilities"] != []
            or not isinstance(value["source"], str)
            or not isinstance(value["corridors"], list)
        ):
            raise ValueError("运行通道目录版本、来源或能力声明无效")
        before = self.document()
        payload = deepcopy(before)
        routes = {r["id"]: r for r in payload["routes"]}
        for route in value["corridors"]:
            strict_fields(
                route,
                {"id", "path", "extensions"},
                {"name", "track_changes"},
                "单向运行通道",
            )
            if route["id"] in routes and route["path"] != routes[route["id"]]["path"]:
                raise ValueError("已有通道物理径路不能覆盖；新径路请使用新的通道 ID")
            routes[route["id"]] = deepcopy(route)
            routes[route["id"]]["extensions"].setdefault(
                "railscope.org/provenance", {"source": value["source"]}
            )
        if len({r["id"] for r in value["corridors"]}) != len(value["corridors"]):
            raise ValueError("导入通道 ID 重复")
        payload["routes"] = list(routes.values())
        # Preserve imported provenance and arbitrary namespaced metadata.
        metadata = deepcopy(value["extensions"])
        metadata.pop("railscope.org/corridor-import", None)
        payload["extensions"]["railscope.org/corridor-import"] = {
            "source": value["source"],
            "extensions": metadata,
        }
        self.accept_batch(payload, before)

    def import_corridors(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入单向国铁运行通道",
            str(Path(self.path).parent),
            "RailScope 通道 JSON (*.json)",
        )
        if path:
            try:
                self.merge_corridors(read_plan(path))
            except (ValueError, OSError, KeyError, TypeError) as error:
                QMessageBox.warning(self, "通道整批未导入", str(error))

    def export_corridors(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出单向国铁运行通道",
            str(Path(self.path).parent / "rail-corridors.json"),
            "RailScope 通道 JSON (*.json)",
        )
        if path:
            try:
                temporary = Path(path).with_suffix(".json.tmp")
                temporary.write_text(
                    json.dumps(self.corridors_document(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                temporary.replace(path)
            except (ValueError, OSError) as error:
                QMessageBox.warning(self, "通道导出失败", str(error))

    def snapshot_state(self):
        return self.document()

    def restore_state(self, snapshot):
        history, future = list(self.undo_stack), list(self.redo_stack)
        selected = self.selected_line
        clock, enabled, playing = self.clock, self.enabled, self.playing
        route_on = hasattr(self, "route_switch") and self.route_switch.isChecked()
        self.apply_payload(snapshot, show_route=route_on)
        self.undo_stack, self.redo_stack = history, future
        index = self.line_combo.findData(selected)
        if index >= 0:
            self.line_combo.setCurrentIndex(index)
        self.clock = clock
        self.set_enabled(enabled)
        self.playing = playing

    def refresh_table(self):
        super().refresh_table()
        self.table.blockSignals(True)
        self.table.setColumnCount(11)
        self.table.setHorizontalHeaderLabels(
            [
                "车次",
                "方向",
                "站序",
                "车站 / 线路所",
                "到达",
                "发车",
                "停站 s",
                "原股道",
                "目标股道",
                "道岔节点",
                "变道时刻",
            ]
        )
        for column, width in enumerate((75, 45, 40, 105, 80, 80, 55, 70, 70, 95, 80)):
            self.table.setColumnWidth(column, width)
        row = 0
        for train in self.displayed_trains():
            corridor = next(
                (
                    r
                    for r in (self.rail_payload or {}).get("routes", [])
                    if r["id"] == self.plan.lines[train["line_id"]].get("corridor_id")
                ),
                {},
            )
            shared_changes = {
                c["node_id"]: c for c in corridor.get("track_changes", [])
            }
            for index, stop in enumerate(train["stops"]):
                change = (
                    stop.get("extensions", {})
                    .get("railscope.org/rail-stop", {})
                    .get("track_change", {})
                )
                for column, field in enumerate(
                    ("from_track", "to_track", "via_node", "time"), 7
                ):
                    shared = shared_changes.get(int(stop["station_id"]), {})
                    value = (
                        change.get(field, "")
                        if field == "time"
                        else shared.get(field, change.get(field, ""))
                    )
                    item = QTableWidgetItem("" if value is None else str(value))
                    item.setData(Qt.ItemDataRole.UserRole, (train["id"], index))
                    if column < 10:
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    item.setToolTip(
                        "股道与位置来自共享通道（通道面板编辑）；执行时刻属于本车次。未知留空，尚不执行实际联锁 / 变道"
                    )
                    self.table.setItem(row, column, item)
                row += 1
        self.table.blockSignals(False)

    def table_changed(self, item):
        if self.loading or item.column() < 7:
            return super().table_changed(item)
        if item.column() < 10:
            self.message.setText(
                "股道与道岔位置在左侧「通道」中统一编辑，所有引用车次共享"
            )
            self.refresh_table()
            return
        train_id, index = item.data(Qt.ItemDataRole.UserRole)
        original = self.plan.train(train_id)["stops"][index]["extensions"][
            "railscope.org/rail-stop"
        ]
        before = self.snapshot_state()
        previous = deepcopy(original)
        change = original.setdefault(
            "track_change",
            {k: "" for k in ("from_track", "to_track", "via_node", "time")},
        )
        change[("from_track", "to_track", "via_node", "time")[item.column() - 7]] = (
            item.text().strip()
        )
        try:
            self.document()
            self.undo_stack.append(before)
            self.redo_stack.clear()
            self.changed("已更新变道预留信息；不代表实际股道校验通过")
        except ValueError as error:
            original.clear()
            original.update(previous)
            self.message.setText(str(error))
            self.refresh_table()

    def add_train(self):
        payload = self.document()
        if not payload["routes"]:
            self.message.setText("请先导入单向运行通道")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("新增国铁车次 · 复用既有径路")
        form = QFormLayout(dialog)
        ident = QLineEdit()
        corridor = QComboBox()
        for route in payload["routes"]:
            corridor.addItem(route.get("name", route["id"]), route["id"])
        template = QComboBox()
        first = QLineEdit(format_time(self.clock))
        end = QLineEdit(format_time(min(172799, self.clock + 3600)))

        def choose_corridor():
            template.clear()
            for train in payload["trains"]:
                if train["route_id"] == corridor.currentData():
                    template.addItem(train["id"] + " · 已有时刻模板", train["id"])
            template.addItem("仅两端控制点（经停请用 CSV 补充）", None)

        corridor.currentIndexChanged.connect(choose_corridor)
        choose_corridor()
        form.addRow("新车次（唯一，一车次一列车）", ident)
        form.addRow("共享单向运行通道", corridor)
        form.addRow("时刻模板", template)
        form.addRow("首站发车", first)
        form.addRow("终点到达（仅两端模式）", end)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            if template.currentData():
                self.add_train_number(
                    ident.text().strip(),
                    template.currentData(),
                    parse_time(first.text()),
                )
            else:
                self.add_train_on_corridor(
                    ident.text().strip(),
                    corridor.currentData(),
                    parse_time(first.text()),
                    parse_time(end.text()),
                )
        except ValueError as error:
            QMessageBox.warning(self, "车次未添加", str(error))

    def add_train_number(self, ident, template_id, first_departure):
        before = self.document()
        if not ident or any(t["id"] == ident for t in before["trains"]):
            raise ValueError("车次为空或重复；同一车次只能对应一列车")
        template = deepcopy(next(t for t in before["trains"] if t["id"] == template_id))
        template["id"] = ident
        template["extensions"]["railscope.org/provenance"] = {
            "source": "用户新增车次，引用既有运行通道与时刻模板；非官方计划"
        }
        delta = first_departure - template["stops"][0]["departure_s"]
        for stop in template["stops"]:
            stop["arrival_s"] += delta
            stop["departure_s"] += delta
            change = stop.get("track_change", {})
            if change.get("time"):
                seconds = parse_time(change["time"]) + delta
                if not 0 <= seconds < 172800:
                    raise ValueError("变道时刻平移后超出 48 小时范围")
                change["time"] = format_time(seconds)
        payload = deepcopy(before)
        payload["trains"].append(template)
        self.accept_batch(payload, before, ident)

    def add_train_on_corridor(self, ident, corridor_id, first_departure, last_arrival):
        before = self.document()
        if not ident or any(t["id"] == ident for t in before["trains"]):
            raise ValueError("车次为空或重复")
        route = next(r for r in before["routes"] if r["id"] == corridor_id)
        lookup = {e["id"]: e for e in self.graph["edges"]}
        first, last = route["path"][0], route["path"][-1]
        start = lookup[first["edge_id"]][
            "from_node" if first["direction"] == "forward" else "to_node"
        ]
        end = lookup[last["edge_id"]][
            "to_node" if last["direction"] == "forward" else "from_node"
        ]
        payload = deepcopy(before)
        payload["trains"].append(
            {
                "id": ident,
                "route_id": corridor_id,
                "stops": [
                    {
                        "node_id": start,
                        "arrival_s": first_departure,
                        "departure_s": first_departure,
                    },
                    {
                        "node_id": end,
                        "arrival_s": last_arrival,
                        "departure_s": last_arrival,
                    },
                ],
                "extensions": {
                    "railscope.org/provenance": {
                        "source": "用户手动两端控制点计划；非官方，中间通过时刻为插值"
                    }
                },
            }
        )
        self.accept_batch(payload, before, ident)

    def accept_batch(self, payload, before=None, selected=None):
        before = self.document() if before is None else before
        history = self.undo_stack + [before]
        route_on = hasattr(self, "route_switch") and self.route_switch.isChecked()
        self.apply_payload(
            payload, show_route=route_on
        )  # Validate the whole batch before replacement.
        self.undo_stack, self.redo_stack = history[-50:], []
        if selected:
            self.line_combo.setCurrentIndex(
                self.line_combo.findData("rail/" + selected)
            )
        self.changed("车次已添加；复用共享轨道径路，可逐站编辑时刻")

    def import_table(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "批量导入国铁车次表", str(Path(self.path).parent), "UTF-8 CSV (*.csv)"
        )
        if path:
            try:
                self.accept_batch(
                    merge_csv(
                        Path(path).read_text(encoding="utf-8-sig"), self.document()
                    )
                )
            except (ValueError, OSError, KeyError, TypeError) as error:
                QMessageBox.warning(self, "整批未导入，原计划保留", str(error))

    def export_table(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出国铁车次表 / 导入模板",
            str(Path(self.path).parent / "rail-trains.csv"),
            "UTF-8 CSV (*.csv)",
        )
        if path:
            try:
                Path(path).write_text(export_csv(self.document()), encoding="utf-8-sig")
            except (ValueError, OSError) as error:
                QMessageBox.warning(self, "导出失败", str(error))

    def write(self, path):
        payload = self.document()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)

    def save(self):
        try:
            self.write(self.path)
            self.message.setText("国铁车次计划已保存")
        except (ValueError, OSError) as error:
            self.message.setText("未保存：" + str(error))

    def import_plan(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入国铁车次与跨线径路",
            str(Path(self.path).parent),
            "RailScope JSON (*.json)",
        )
        if not path:
            return
        try:
            self.apply_payload(read_plan(path), show_route=True)
        except (ValueError, OSError, KeyError, TypeError) as error:
            QMessageBox.warning(self, "国铁计划导入失败", str(error))

    def export_plan(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出国铁车次计划",
            str(Path(self.path).parent / "rail-plan.json"),
            "RailScope JSON (*.json)",
        )
        if path:
            try:
                self.write(path)
            except (ValueError, OSError) as error:
                QMessageBox.warning(self, "导出失败", str(error))

    def open_cycles(self):
        QMessageBox.information(
            self,
            "国铁运行模型",
            "国铁按车次及连续跨线径路建图，不使用地铁往返循环。请导入明确区间、方向、经停/通过节点和到发时刻的国铁计划。",
        )
