"""Independent train-number workspace backed by explicit cross-line paths."""

from copy import deepcopy
import json
from pathlib import Path
from PySide6.QtWidgets import QFileDialog, QMessageBox

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
        if Path(path).exists():
            try:
                self.apply_payload(read_plan(path))
            except (ValueError, OSError, KeyError, TypeError) as error:
                self.message.setText("国铁计划未载入：" + str(error))

    def apply_payload(self, payload):
        try:
            from .rail_store import load_edges
        except ImportError:
            from rail_store import load_edges
        if not (self.directory / "rail.sqlite").exists():
            raise ValueError("请先提取国铁基础设施（包含视窗索引）")
        required = [
            leg["edge_id"]
            for train in payload.get("trains", [])
            for leg in train.get("path", [])
        ]
        edges = load_edges(self.directory, required)
        if (self.directory / "rail_points.geojson").exists():
            self.graph["points"] = json.loads(
                (self.directory / "rail_points.geojson").read_text(encoding="utf-8")
            )["features"]
        plan, lines = compile_rail_plan(
            payload, edges, self.graph["points"], self.platforms
        )
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
            self.apply_payload(read_plan(path))
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
