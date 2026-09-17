"""Independent train-number workspace backed by explicit cross-line paths."""

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox, QPushButton

try:
    from .operating_ui import OperationsEditor
    from .operating import Plan, read_plan
    from .rail import compile_rail_plan
except ImportError:
    from operating_ui import OperationsEditor
    from operating import Plan, read_plan
    from rail import compile_rail_plan


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
        if not self.plan.trains:
            self.message.setText(
                "尚未载入国铁车次。点击「载入 G1 示例」查看七站时刻表，或从国铁运行菜单导入自己的计划。"
            )

    def play(self):
        if not self.plan.trains:
            self.message.setText("请先载入 G1 示例，或在国铁运行菜单导入车次计划。")
            return
        super().play()

    def apply_payload(self, payload, show_route=False):
        try:
            from .rail_store import load_edges
        except ImportError:
            from rail_store import load_edges
        required = [
            leg["edge_id"]
            for train in payload.get("trains", [])
            for leg in train.get("path", [])
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
        plan, lines = compile_rail_plan(payload, edges, points, self.platforms)
        self.pause()
        self.set_enabled(False)
        self.graph["points"] = points
        self.graph["edges"] = edges
        for profile, train in zip(lines, payload["trains"]):
            profile["rail_path"] = deepcopy(train["path"])
        self.plan = plan
        self.base_lines = lines
        self.rail_payload = deepcopy(payload)
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
        for line in self.base_lines:
            props = {
                "name": line["ref"] + " · 国铁参考径路",
                "source": self.rail_payload["source"],
                "train_id": line["ref"],
            }
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
            2, switch_row("参考径路 / 经停站", self.route_switch)
        )
        load = QPushButton("载入 G1 演示 · 北京南—上海虹桥")
        load.setObjectName("primary")
        load.clicked.connect(self.g1_requested.emit)
        body.widget().layout().insertWidget(0, load)
        return body

    def set_reference_visible(self, on):
        if on and self.plan.trains:
            self.show_reference_route()
        else:
            self.map.call("setVisibility", "railPlan", False)
            if on:
                self.message.setText("请先载入 G1 示例或导入国铁车次计划。")
                self.route_switch.blockSignals(True)
                self.route_switch.setChecked(False)
                self.route_switch.blockSignals(False)

    def document(self):
        if self.rail_payload is None:
            raise ValueError("请先导入国铁车次计划和实际基础设施")
        self.plan.validate()
        payload = deepcopy(self.rail_payload)
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
        return payload

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
