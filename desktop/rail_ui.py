"""Independent train-number workspace backed by explicit cross-line paths."""

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QMessageBox,
    QTableWidgetItem,
    QDialog,
    QFormLayout,
    QLineEdit,
    QComboBox,
    QDialogButtonBox,
    QTableWidget,
    QHeaderView,
    QPlainTextEdit,
    QLabel,
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
    from .rail_lines import resolution_policy, RESOLUTION_KEY
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
    from rail_lines import resolution_policy, RESOLUTION_KEY


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
    names_changed = Signal()

    def __init__(self, map_view, directory, path):
        self.directory = Path(directory).resolve()
        self.graph = {"edges": [], "points": []}
        self.platforms = (
            json.loads(
                (self.directory / "rail_platforms.geojson").read_text(encoding="utf-8")
            )["features"]
            if (self.directory / "rail_platforms.geojson").exists()
            else []
        )
        if (self.directory / "rail.sqlite").exists():
            with sqlite3.connect(str(self.directory / "rail.sqlite")) as db:
                self.platforms = [
                    json.loads(raw)
                    for (raw,) in db.execute(
                        "SELECT data FROM features WHERE kind='railPlatforms'"
                    )
                ]
        self.rail_payload = None
        self.visible_corridors = set()
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
            self.apply_payload(self.initial_g1_payload())
            self.message.setText(
                "内置 G1 · 7站参考时刻 · 已关闭运行；选择车次查看独立时刻表 / 运行图。"
            )

    def initial_g1_payload(self):
        if (self.directory / "rail.sqlite").exists():
            try:
                try:
                    from .rail_assembly import assemble_jinghu
                except ImportError:
                    from rail_assembly import assemble_jinghu
                payload, _ = assemble_jinghu(self.directory)
                return payload
            except (ValueError, sqlite3.Error):
                # Partial regional databases may not contain the Jinghu example.
                pass
        reference = json.loads(
            (Path(__file__).parent / "examples/g1-reference.json").read_text(
                encoding="utf-8"
            )
        )
        return reference["plan"]

    def play(self):
        if not self.plan.trains:
            self.message.setText("请在国铁运行菜单导入车次计划，或手动新增车次。")
            return
        super().play()

    def apply_payload(self, payload, show_route=False):
        original_payload = shared_document(payload)
        for train in original_payload["trains"]:
            for section in train.get("station_paths", []):
                if "sequence" in section:
                    strict_fields(
                        section,
                        {"from_node", "to_node", "sequence", "extensions"},
                        {"path"},
                        "车次站场径路",
                    )
                    section["path"] = self.line_library().resolve(
                        section["sequence"], resolution_policy(section["extensions"])
                    )
        payload = expanded_document(original_payload)
        try:
            from .rail_store import load_edges
        except ImportError:
            from rail_store import load_edges
        required = [
            leg["edge_id"]
            for route in original_payload["routes"]
            for leg in route["path"]
        ]
        required.extend(
            leg["edge_id"]
            for train in original_payload["trains"]
            for section in train.get("station_paths", [])
            for leg in section["path"]
        )
        reference_mode = (
            payload.get("extensions", {})
            .get("railscope.org/reference", {})
            .get("path_status")
            == "connected_osm_reference_not_dispatch_route"
        )
        edges = (
            load_edges(self.directory, required)
            if (self.directory / "rail.sqlite").exists()
            else []
        )
        found = {e["id"] for e in edges}
        missing = set(required) - found
        points = []
        if missing and reference_mode:
            reference = json.loads(
                (Path(__file__).parent / "examples/g1-reference.json").read_text(
                    encoding="utf-8"
                )
            )
            edges.extend(e for e in reference["edges"] if e["id"] in missing)
            points = reference["points"]
            missing -= {e["id"] for e in edges}
        if missing:
            raise ValueError("铁路库缺少所引用的物理区间，请先导入对应基础设施")
        mappings = (
            payload.get("extensions", {})
            .get("railscope.org/assembly", {})
            .get("stations", [])
        )
        if (
            not mappings
            and reference_mode
            and not missing
            and (self.directory / "rail.sqlite").exists()
        ):
            try:
                try:
                    from .rail_assembly import assemble_jinghu
                except ImportError:
                    from rail_assembly import assemble_jinghu
                assembled, _ = assemble_jinghu(self.directory)
                assembly = assembled["extensions"]["railscope.org/assembly"]
                # Migrate source-station labels only; never replace a saved user's path or times.
                original_payload["extensions"]["railscope.org/assembly"] = deepcopy(
                    assembly
                )
                mappings = assembly["stations"]
            except (ValueError, sqlite3.Error):
                pass
        if (self.directory / "rail.sqlite").exists():
            stop_ids = {s["node_id"] for t in payload["trains"] for s in t["stops"]}
            stop_ids.update(mapping["source_station_node"] for mapping in mappings)
            with sqlite3.connect(str(self.directory / "rail.sqlite")) as db:
                for ident in stop_ids:
                    for row in db.execute(
                        "SELECT data FROM features WHERE kind='railPoints' AND json_extract(data,'$.properties.osm_node_id')=?",
                        (ident,),
                    ):
                        points.append(json.loads(row[0]))
        if mappings:
            coordinates = {
                node: coord
                for edge in edges
                for node, coord in zip(edge["node_ids"], edge["coordinates"])
            }
            for mapping in mappings:
                node = mapping["anchor_node"]
                if node in coordinates:
                    points.append(
                        {
                            "type": "Feature",
                            "properties": {
                                "osm_node_id": node,
                                "name": mapping["station_name"],
                                "source_station_node": mapping["source_station_node"],
                                "anchor_offset_m": mapping["offset_m"],
                                "infrastructure_id": "node/"
                                + str(mapping["source_station_node"]),
                            },
                            "geometry": {
                                "type": "Point",
                                "coordinates": coordinates[node],
                            },
                        }
                    )
        validate_corridors(original_payload["routes"], edges)
        plan, lines = compile_rail_plan(payload, edges, points, self.platforms)
        self.pause()
        self.set_enabled(False)
        self.graph["points"] = points
        self.graph["edges"] = edges
        self.shared_station_features = {
            p["properties"]["osm_node_id"]: p for p in points if "geometry" in p
        }
        self.visible_corridors.intersection_update(
            r["id"] for r in original_payload["routes"]
        )
        for profile, train, shared_train in zip(
            lines, payload["trains"], original_payload["trains"]
        ):
            profile["rail_path"] = deepcopy(profile["resolved_rail_path"])
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
        self.push_corridors()
        if show_route:
            self.show_reference_route()

    def show_reference_route(self):
        selected = self.current_line()
        if selected:
            self.visible_corridors.add(selected["corridor_id"])
        self.push_corridors()
        if hasattr(self, "route_switch"):
            self.route_switch.blockSignals(True)
            self.route_switch.setChecked(bool(self.visible_corridors))
            self.route_switch.blockSignals(False)
        self.locate_current_line()

    def set_corridor_visible(self, ident, on):
        if on:
            self.visible_corridors.add(ident)
        else:
            self.visible_corridors.discard(ident)
        self.push_corridors()
        if hasattr(self, "route_switch"):
            self.route_switch.blockSignals(True)
            self.route_switch.setChecked(bool(self.visible_corridors))
            self.route_switch.blockSignals(False)
        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, self.updated.emit)

    def push_corridors(self):
        features = []
        drawn_paths, drawn_stations = set(), set()
        lookup = {edge["id"]: edge for edge in self.graph["edges"]}
        node_coordinates = {
            node: coord
            for edge in self.graph["edges"]
            for node, coord in zip(
                edge.get("node_ids", [edge["from_node"], edge["to_node"]]),
                edge["coordinates"],
            )
        }
        for route in (self.rail_payload or {}).get("routes", []):
            corridor_id = route["id"]
            if corridor_id not in self.visible_corridors:
                continue
            trains = [
                t["id"]
                for t in self.plan.trains
                if self.plan.lines[t["line_id"]].get("corridor_id") == corridor_id
            ]
            props = {
                "name": route.get("name", corridor_id) if route else "单向参考通道",
                "source": self.rail_payload["source"],
                "train_id": trains[0] if trains else "",
                "corridor_id": corridor_id,
                "train_ids": trains,
                "track_changes": (route or {}).get("track_changes", []),
            }
            coords = []
            for leg in route["path"]:
                part = lookup[leg["edge_id"]]["coordinates"]
                if leg["direction"] == "reverse":
                    part = part[::-1]
                coords.extend(part if not coords else part[1:])
            key = tuple((leg["edge_id"], leg["direction"]) for leg in route["path"])
            if key not in drawn_paths:
                drawn_paths.add(key)
                features.append(
                    {
                        "type": "Feature",
                        "properties": props,
                        "geometry": {
                            "type": "LineString",
                            "coordinates": coords,
                        },
                    }
                )
            stop_ids = {
                s["node_id"]
                for train in self.rail_payload["trains"]
                if train["route_id"] == corridor_id
                for s in train["stops"]
            }
            for station in self.graph["points"]:
                data = station["properties"]
                if data["osm_node_id"] not in stop_ids:
                    continue
                source_node = data.get("source_station_node", data["osm_node_id"])
                shared = self.shared_station_features.get(source_node, station)
                station_key = "node/" + str(source_node)
                if station_key in drawn_stations:
                    continue
                drawn_stations.add(station_key)
                features.append(
                    {
                        "type": "Feature",
                        "properties": {
                            **props,
                            **shared["properties"],
                            "infrastructure_id": station_key,
                        },
                        "geometry": shared.get(
                            "geometry",
                            {
                                "type": "Point",
                                "coordinates": node_coordinates[data["osm_node_id"]],
                            },
                        ),
                    }
                )
        self.map.call(
            "setRailPlan", {"type": "FeatureCollection", "features": features}
        )
        self.map.call("setVisibility", "railPlan", bool(features))

    def load_g1_example(self):
        self.apply_payload(self.initial_g1_payload(), show_route=True)
        self.tabs.setCurrentIndex(0)
        self.refresh_diagram()
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
        payload = deepcopy(self.rail_payload)
        payload["trains"] = []
        for train in self.plan.trains:
            profile = self.plan.lines[train["line_id"]]
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
                    "route_id": profile["corridor_id"],
                    "stops": stops,
                    "extensions": train.get("extensions", {}),
                }
            )
            if profile.get("station_paths"):
                payload["trains"][-1]["station_paths"] = deepcopy(
                    profile["station_paths"]
                )
        compile_rail_plan(
            payload, self.graph["edges"], self.graph["points"], self.platforms
        )
        return payload

    def variant_changed(self):
        super().variant_changed()
        if (
            hasattr(self, "route_switch")
            and self.route_switch.isChecked()
            and self.rail_payload
        ):
            self.show_reference_route()

    def corridors_document(self, table=False):
        payload = self.document()
        if not table:
            return {
                "schema": "railscope.rail-corridors.v1",
                "source": payload["source"],
                "required_capabilities": [],
                "extensions": deepcopy(payload["extensions"]),
                "corridors": deepcopy(payload["routes"]),
            }
        try:
            from .rail_lines import RailLineLibrary
        except ImportError:
            from rail_lines import RailLineLibrary
        library = RailLineLibrary(self.graph["edges"], self.graph["points"])
        corridors = []
        for route in payload["routes"]:
            sequence = route.get("sequence") or library.describe(route["path"])
            if (
                library.resolve(sequence, resolution_policy(route["extensions"]))
                != route["path"]
            ):
                raise ValueError("通道存在歧义，请在通道表格中补充端点")
            extensions = deepcopy(route["extensions"])
            if route.get("track_changes"):
                extensions["railscope.org/legacy-track-changes"] = deepcopy(
                    route["track_changes"]
                )
            corridors.append(
                {
                    "id": route["id"],
                    "name": route.get("name", route["id"]),
                    "sequence": sequence,
                    "extensions": extensions,
                }
            )
        return {
            "schema": "railscope.rail-corridors.v2",
            "source": payload["source"],
            "required_capabilities": [],
            "extensions": deepcopy(payload["extensions"]),
            "corridors": corridors,
        }

    def line_library(self, interactive=False):
        try:
            from .rail_lines import RailLineLibrary
            from .rail_line_store import (
                DiskRailLineLibrary,
                build_line_index,
                fingerprint,
                index_ready,
            )
            from .background_work import prepare_with_progress
        except ImportError:
            from rail_lines import RailLineLibrary
            from rail_line_store import (
                DiskRailLineLibrary,
                build_line_index,
                fingerprint,
                index_ready,
            )
            from background_work import prepare_with_progress
        edges = {e["id"]: e for e in self.graph["edges"]}
        points = list(self.graph["points"])
        database = (self.directory / "rail.sqlite").resolve()
        stamp = database.stat().st_mtime_ns if database.exists() else None
        signature = (str(database), stamp, tuple(edges))
        if getattr(self, "_line_library_signature", None) == signature:
            return self._line_library
        names_path = Path(self.path).parent / "rail_line_names.json"
        names = (
            json.loads(names_path.read_text(encoding="utf-8"))
            if names_path.exists()
            else {}
        )
        if database.exists():
            index = database.with_name("rail_lines.sqlite")
            extras = list(edges.values())
            if not index_ready(index, fingerprint(database, extras)):

                def build(progress):
                    return build_line_index(database, index, extras, points, progress)

                if interactive:
                    prepare_with_progress(self, "准备铁路命名与端点目录", build)
                else:
                    build(lambda text: None)
            self._line_library = DiskRailLineLibrary(index, names)
        else:
            self._line_library = RailLineLibrary(list(edges.values()), points, names)
        self._line_library_signature = signature
        return self._line_library

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
        self.set_corridor_visible(corridor_id, True)
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
        xs, ys = zip(*coords)
        self.map.call(
            "fit",
            [[min(xs), min(ys)], [max(xs), max(ys)]],
            route.get("name", corridor_id),
        )
        if hasattr(self, "route_switch"):
            self.route_switch.blockSignals(True)
            self.route_switch.setChecked(True)
            self.route_switch.blockSignals(False)

    def merge_corridors(self, value, interactive=False):
        strict_fields(
            value,
            {"schema", "source", "required_capabilities", "extensions", "corridors"},
            set(),
            "运行通道目录",
        )
        if (
            value["schema"]
            not in ("railscope.rail-corridors.v1", "railscope.rail-corridors.v2")
            or value["required_capabilities"] != []
            or not isinstance(value["source"], str)
            or not isinstance(value["corridors"], list)
        ):
            raise ValueError("运行通道目录版本、来源或能力声明无效")
        before = self.document()
        payload = deepcopy(before)
        routes = {r["id"]: r for r in payload["routes"]}
        incoming = deepcopy(value["corridors"])
        if value["schema"] == "railscope.rail-corridors.v2":
            library = self.line_library(interactive=interactive)
            for route in incoming:
                strict_fields(
                    route,
                    {"id", "name", "sequence", "extensions"},
                    set(),
                    "端点—线路通道",
                )
                # Resolve from the infrastructure every time, including G1 notation.
                policy = resolution_policy(route["extensions"])
                if interactive and hasattr(library, "connect"):
                    try:
                        from .background_work import prepare_with_progress
                    except ImportError:
                        from background_work import prepare_with_progress

                    def resolve(report):
                        report("校验端点并组合既有物理线路…")
                        result = library.resolve(route["sequence"], policy)
                        report("物理线路组合已完成")
                        return result

                    route["path"] = prepare_with_progress(self, "校验单向通道", resolve)
                else:
                    route["path"] = library.resolve(route["sequence"], policy)
                route["extensions"][RESOLUTION_KEY] = {
                    **route["extensions"].get(RESOLUTION_KEY, {}),
                    "policy": policy,
                    "data_source": "railway_database"
                    if (self.directory / "rail.sqlite").exists()
                    else "portable_reference",
                    "edge_count": len(route["path"]),
                    "geometry_status": "assembled_geometry_not_dispatch_route",
                }
                legacy = route["extensions"].get("railscope.org/legacy-track-changes")
                if legacy is not None:
                    route["track_changes"] = deepcopy(legacy)
        for route in incoming:
            strict_fields(
                route,
                {"id", "path", "extensions"},
                {"name", "track_changes", "sequence"},
                "单向运行通道",
            )
            if route["id"] in routes and route["path"] != routes[route["id"]]["path"]:
                raise ValueError("已有通道物理径路不能覆盖；新径路请使用新的通道 ID")
            routes[route["id"]] = {**routes.get(route["id"], {}), **deepcopy(route)}
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
            "RailScope 通道 (*.json *.csv)",
        )
        if path:
            try:
                if Path(path).suffix.lower() == ".csv":
                    try:
                        from .rail_lines import import_corridor_csv
                    except ImportError:
                        from rail_lines import import_corridor_csv
                    value = import_corridor_csv(
                        Path(path).read_text(encoding="utf-8-sig")
                    )
                else:
                    value = read_plan(path)
                self.line_library(interactive=True)
                self.merge_corridors(value, interactive=True)
            except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as error:
                QMessageBox.warning(self, "通道整批未导入", str(error))

    def export_corridors(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出单向国铁运行通道",
            str(Path(self.path).parent / "rail-corridors.json"),
            "RailScope 通道 JSON (*.json);;通道表格 CSV (*.csv)",
        )
        if path:
            try:
                temporary = Path(path).with_suffix(".json.tmp")
                value = self.corridors_document(table=True)
                if Path(path).suffix.lower() == ".csv":
                    try:
                        from .rail_lines import export_corridor_csv
                    except ImportError:
                        from rail_lines import export_corridor_csv
                    text = export_corridor_csv(value)
                else:
                    text = json.dumps(value, ensure_ascii=False, indent=2)
                temporary.write_text(
                    text,
                    encoding="utf-8-sig"
                    if Path(path).suffix.lower() == ".csv"
                    else "utf-8",
                )
                temporary.replace(path)
            except (ValueError, OSError) as error:
                QMessageBox.warning(self, "通道导出失败", str(error))

    def snapshot_state(self):
        return self.document()

    def organize_lines(self):
        try:
            library = self.line_library(interactive=True)
        except (OSError, ValueError, sqlite3.Error) as error:
            QMessageBox.warning(self, "铁路线命名目录未打开", str(error))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("铁路线命名 · 稳定编号与可编辑名称")
        dialog.resize(1100, 700)
        dialog.setStyleSheet(
            "QDialog { background: #f5f9fa; } QTableWidget { background: white; border: 1px solid #d4e2e7; border-radius: 8px; gridline-color: #e5eef1; } QHeaderView::section { background: #e7f3f1; padding: 10px; border: 0; color: #245c60; }"
        )
        form = QFormLayout(dialog)
        form.setContentsMargins(20, 18, 20, 18)
        form.setVerticalSpacing(12)
        from PySide6.QtWidgets import QLabel

        form.addRow(
            QLabel(
                "编号不随改名变化；通道引用编号，不引用显示文字。原始 OSM 标签不修改。未命名轨道不猜测所属线路。"
            )
        )
        from PySide6.QtWidgets import QHBoxLayout, QPushButton
        from PySide6.QtCore import QTimer

        search = QLineEdit()
        search.setPlaceholderText("搜索线路名称或稳定编号（每页 100 条）")
        form.addRow(search)
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(
            ["稳定线路编号", "OSM 原名", "可编辑规范名称", "物理区间数量"]
        )
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        keys, edits, page = [], {}, [0]

        def remember():
            for row, key in enumerate(keys):
                value = table.item(row, 2).text().strip()
                original = library.names.get(key, library.lines[key]["source_name"])
                if value != original:
                    edits[key] = value
                else:
                    edits.pop(key, None)

        def refresh(reset=False):
            remember()
            if reset:
                page[0] = 0
            records = library.search_lines(
                search.text().strip(), limit=100, offset=page[0] * 100
            )
            keys[:] = [record["id"] for record in records]
            table.setRowCount(len(keys))
            for row, record in enumerate(records):
                key = record["id"]
                values = (
                    key,
                    record["source_name"],
                    edits.get(key, library.names.get(key, record["source_name"])),
                    str(record.get("edge_count", len(record.get("edge_ids", [])))),
                )
                for col, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setToolTip(value)
                    if col != 2:
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    table.setItem(row, col, item)
            previous.setEnabled(page[0] > 0)
            following.setEnabled(len(records) == 100)
            counter.setText(
                f"第 {page[0] + 1} 页 · 本页 {len(records)} 条 · 名称修改跨页保留"
            )

        form.addRow(table)
        navigation = QHBoxLayout()
        previous, following, counter = (
            QPushButton("上一页"),
            QPushButton("下一页"),
            QLabel(),
        )
        for widget in (previous, counter, following):
            navigation.addWidget(widget)
        form.addRow(navigation)

        def turn(delta):
            page[0] += delta
            refresh()

        previous.clicked.connect(lambda: turn(-1))
        following.clicked.connect(lambda: turn(1))
        debounce = QTimer(dialog)
        debounce.setSingleShot(True)
        debounce.setInterval(250)
        debounce.timeout.connect(lambda: refresh(True))
        search.textChanged.connect(lambda: debounce.start())
        refresh()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存命名")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.rejected.connect(dialog.reject)

        def save():
            try:
                remember()
                self.save_line_names(edits)
                dialog.accept()
            except (OSError, ValueError) as error:
                QMessageBox.warning(dialog, "命名未保存", str(error))

        buttons.accepted.connect(save)
        form.addRow(buttons)
        dialog.exec()

    def save_line_names(self, names):
        library = self.line_library()
        if any(
            key not in library.lines or not isinstance(name, str) or not name.strip()
            for key, name in names.items()
        ):
            raise ValueError("编号不存在或名称为空")
        path = Path(self.path).parent / "rail_line_names.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps({**library.names, **names}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
        library.names.update(names)
        if isinstance(library.lines, dict):
            for key, name in names.items():
                library.lines[key]["name"] = name + " · " + key
        if hasattr(library, "changed_way_names"):
            way_names = library.changed_way_names(library.names)
        else:
            way_names = {}
            for edge in library.edges.values():
                key = library.edge_lines[edge["id"]]
                way = edge["id"].split(":")[0].removeprefix("w")
                way_names[way] = (
                    library.names.get(key, library.lines[key]["source_name"])
                    + " · "
                    + key
                )
        target = Path(self.path).parent / "rail_way_names.json"
        temp = target.with_suffix(".json.tmp")
        temp.write_text(json.dumps(way_names, ensure_ascii=False), encoding="utf-8")
        temp.replace(target)
        self.names_changed.emit()
        self.updated.emit()

    def export_line_library(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出铁路命名与可复用端点分段",
            str(Path(self.path).parent / "rail-lines.json"),
            "铁路目录 (*.json)",
        )
        if path:
            try:
                library = self.line_library(interactive=True)
                if hasattr(library, "write_export"):
                    try:
                        from .background_work import prepare_with_progress
                    except ImportError:
                        from background_work import prepare_with_progress
                    prepare_with_progress(
                        self,
                        "导出铁路命名与端点分段",
                        lambda report: library.write_export(path, report),
                    )
                    return
                value = {
                    "schema": "railscope.rail-lines.v1",
                    "lines": [
                        {
                            "id": key,
                            "name": record["name"],
                            "source_name": record["source_name"],
                            "edge_count": len(record["edge_ids"]),
                        }
                        for key, record in sorted(library.lines.items())
                    ],
                    "endpoints": [
                        {"node_id": key, "name": name}
                        for key, name in sorted(library.nodes.items())
                    ],
                    "sections": library.sections(),
                }
                temporary = Path(path).with_suffix(".json.tmp")
                temporary.write_text(
                    json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                temporary.replace(path)
            except (ValueError, OSError, sqlite3.Error) as error:
                QMessageBox.warning(self, "铁路目录未导出", str(error))

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
        self.table.setColumnCount(12)
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
                "站台 OSM 编号",
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
                    value = change.get(field, shared.get(field, ""))
                    item = QTableWidgetItem("" if value is None else str(value))
                    item.setData(Qt.ItemDataRole.UserRole, (train["id"], index))
                    item.setToolTip(
                        "本车次的停靠股道 / 道岔 / 时刻。未知留空；文字字段不改变动画径路，实体轨道在「编辑车次站场径路」中定义。"
                    )
                    self.table.setItem(row, column, item)
                platform_data = stop.get("extensions", {}).get(
                    "railscope.org/rail-stop", {}
                )
                platform = platform_data.get(
                    "platform_ref", platform_data.get("platform_id", "")
                )
                item = QTableWidgetItem(str(platform))
                item.setData(Qt.ItemDataRole.UserRole, (train["id"], index))
                item.setToolTip(
                    "本车次的真实站台：way/编号 或 relation/编号；兼容旧整数 Way 编号。未知留空，不代表联锁验证。"
                )
                self.table.setItem(row, 11, item)
                row += 1
        self.table.blockSignals(False)

    def table_changed(self, item):
        if self.loading or item.column() < 7:
            return super().table_changed(item)
        train_id, index = item.data(Qt.ItemDataRole.UserRole)
        original = self.plan.train(train_id)["stops"][index]["extensions"][
            "railscope.org/rail-stop"
        ]
        before = self.snapshot_state()
        previous = deepcopy(original)
        try:
            if item.column() == 11:
                value = item.text().strip()
                original.pop("platform_id", None)
                original.pop("platform_ref", None)
                if value:
                    if value.startswith(("way/", "relation/")):
                        kind, number = value.split("/", 1)
                        if not number.isdigit():
                            raise ValueError("站台引用须为 way/整数 或 relation/整数")
                        original["platform_ref"] = kind + "/" + str(int(number))
                    else:
                        original["platform_id"] = int(value)
            else:
                change = original.setdefault(
                    "track_change",
                    {k: "" for k in ("from_track", "to_track", "via_node", "time")},
                )
                change[
                    ("from_track", "to_track", "via_node", "time")[item.column() - 7]
                ] = item.text().strip()
            self.document()
            self.undo_stack.append(before)
            self.redo_stack.clear()
            self.changed("已更新变道预留信息；不代表实际股道校验通过")
        except ValueError as error:
            original.clear()
            original.update(previous)
            self.message.setText(str(error))
            self.refresh_table()

    def edit_station_paths(self):
        line = self.current_line()
        if not line:
            return
        ident = line["ref"]
        dialog = QDialog(self)
        dialog.setWindowTitle(ident + " · 站场进出轨道（仅本车次）")
        dialog.resize(880, 600)
        form = QFormLayout(dialog)
        hint = QLabel(
            "每段填写 from_node、to_node、sequence、extensions。端点必须是通道上的已切分节点；按端点—线路组合解析现有轨道，不生成新几何。停靠节点必须位于解析后的车次径路上。空数组表示沿用通道主线。"
        )
        hint.setWordWrap(True)
        form.addRow(hint)
        text = QPlainTextEdit(
            json.dumps(line.get("station_paths", []), ensure_ascii=False, indent=2)
        )
        form.addRow(text)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.rejected.connect(dialog.reject)

        def accept():
            try:
                sections = json.loads(text.toPlainText())
                if not isinstance(sections, list):
                    raise ValueError("站场径路必须是数组")
                library = self.line_library(interactive=True)
                for section in sections:
                    strict_fields(
                        section,
                        {"from_node", "to_node", "sequence", "extensions"},
                        {"path"},
                        "车次站场径路",
                    )
                    section["path"] = library.resolve(
                        section["sequence"], resolution_policy(section["extensions"])
                    )
                before = self.document()
                payload = deepcopy(before)
                train = next(t for t in payload["trains"] if t["id"] == ident)
                train["station_paths"] = sections
                self.accept_batch(payload, before)
                dialog.accept()
            except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as error:
                QMessageBox.warning(dialog, "站場径路未修改", str(error))

        buttons.accepted.connect(accept)
        form.addRow(buttons)
        dialog.exec()

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
