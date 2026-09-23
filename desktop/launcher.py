"""RailScope single-window desktop GIS workbench."""

from __future__ import annotations

import argparse
import base64
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3
import sys
import threading
from urllib.parse import unquote, urlsplit, parse_qs

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from PySide6.QtCore import QObject, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QColor, QCursor, QIcon, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QCompleter,
    QDialog,
    QFileDialog,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QInputDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from components import Fold, Switch, THEME, switch_row, text_label, GrowingTree
from geometry import build_demo_path
from hierarchy import Hierarchy, label_order
from hierarchy_ui import HierarchyDialog
from metro_data import (
    associate_station_areas,
    build_shanghai_lines,
    display_stations,
    display_station_areas,
    iter_geojson_features,
)
from operating import Plan
from operating_ui import OperationsEditor
from bootstrap import ensure_assets
from data_install import active_directory, active_rail_directory
from rail_ui import RailEditor
from rail_style_ui import load_styles as load_rail_styles, RailStyleDialog
from rail_point_style_ui import load as load_rail_point_styles, RailPointStyleDialog
from metro_style_ui import load_styles as load_metro_styles, MetroStyleDialog
from line_metadata import FIELD_LABELS, source_line_attributes
from line_metadata_ui import LineMetadataDialog
from rail_categories import TRACK_TYPES
from corridor_ui import CorridorPanel
from rail_connection_ui import StationConnectionSelector
from layer_state import initial_visibility, editor_sizes
from map_commands import MapCommands
from data_install_ui import DataDownloadDialog
from railscope.demo import load_demo
from railscope.services.topology import validate_topology
from railscope.services.stations import StationRegistry
from catalog_metadata import (
    CatalogOverrides,
    metro_station_directory,
    station_type,
    STATION_TYPES,
    STATION_OVERVIEW_FIELDS,
    station_overview,
)

EMPTY = {"type": "FeatureCollection", "features": []}
DATA = active_directory(ROOT)
# City names and provinces are UI grouping metadata, never written into OSM facts.
REGIONS = [
    ("北京", "北京市", 116.40, 39.90),
    ("上海", "上海市", 121.47, 31.23),
    ("天津", "天津市", 117.20, 39.13),
    ("重庆", "重庆市", 106.55, 29.56),
    ("广州", "广东省", 113.26, 23.13),
    ("深圳", "广东省", 114.06, 22.55),
    ("佛山", "广东省", 113.12, 23.02),
    ("东莞", "广东省", 113.75, 23.02),
    ("成都", "四川省", 104.07, 30.67),
    ("武汉", "湖北省", 114.31, 30.59),
    ("南京", "江苏省", 118.80, 32.06),
    ("苏州", "江苏省", 120.58, 31.30),
    ("无锡", "江苏省", 120.31, 31.49),
    ("常州", "江苏省", 119.97, 31.81),
    ("徐州", "江苏省", 117.18, 34.26),
    ("南通", "江苏省", 120.89, 31.98),
    ("杭州", "浙江省", 120.16, 30.28),
    ("宁波", "浙江省", 121.55, 29.87),
    ("温州", "浙江省", 120.70, 28.00),
    ("绍兴", "浙江省", 120.58, 30.00),
    ("郑州", "河南省", 113.63, 34.75),
    ("洛阳", "河南省", 112.45, 34.62),
    ("长沙", "湖南省", 112.94, 28.23),
    ("西安", "陕西省", 108.94, 34.34),
    ("青岛", "山东省", 120.38, 36.07),
    ("济南", "山东省", 117.12, 36.65),
    ("昆明", "云南省", 102.83, 24.88),
    ("南昌", "江西省", 115.86, 28.68),
    ("福州", "福建省", 119.30, 26.08),
    ("厦门", "福建省", 118.09, 24.48),
    ("沈阳", "辽宁省", 123.43, 41.81),
    ("大连", "辽宁省", 121.62, 38.92),
    ("长春", "吉林省", 125.32, 43.82),
    ("哈尔滨", "黑龙江省", 126.64, 45.76),
    ("石家庄", "河北省", 114.51, 38.04),
    ("唐山", "河北省", 118.18, 39.63),
    ("合肥", "安徽省", 117.23, 31.82),
    ("芜湖", "安徽省", 118.38, 31.33),
    ("太原", "山西省", 112.55, 37.87),
    ("兰州", "甘肃省", 103.83, 36.06),
    ("呼和浩特", "内蒙古自治区", 111.75, 40.84),
    ("包头", "内蒙古自治区", 109.84, 40.66),
    ("乌鲁木齐", "新疆维吾尔自治区", 87.62, 43.83),
    ("贵阳", "贵州省", 106.63, 26.65),
    ("南宁", "广西壮族自治区", 108.37, 22.82),
    ("柳州", "广西壮族自治区", 109.43, 24.33),
    ("香港", "香港特别行政区", 114.17, 22.32),
    ("澳门", "澳门特别行政区", 113.55, 22.20),
    ("台北", "台湾省", 121.57, 25.03),
    ("桃园", "台湾省", 121.30, 24.99),
    ("台中", "台湾省", 120.68, 24.15),
    ("高雄", "台湾省", 120.30, 22.63),
    ("三亚", "海南省", 109.51, 18.25),
]


def read_json(path, fallback=None):
    return (
        json.loads(path.read_text(encoding="utf-8"))
        if path.exists()
        else (fallback if fallback is not None else {})
    )


def frame(name="card"):
    widget = QFrame()
    widget.setObjectName(name)
    return widget


def logo_pixmap():
    pixmap = QPixmap(40, 40)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#0c776f"))
    p.drawRoundedRect(0, 0, 40, 40, 11, 11)
    p.setPen(QPen(QColor("white"), 2.2))
    p.drawLine(13, 9, 10, 31)
    p.drawLine(27, 9, 30, 31)
    for y in (14, 20, 26):
        p.drawLine(12, y, 28, y)
    p.end()
    return pixmap


class LocalHandler(SimpleHTTPRequestHandler):
    """Serve only the map assets and local GIS files needed by this app."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, *_):
        pass

    def valid_host(self):
        host = self.headers.get("Host", "").lower()
        port = self.server.server_port
        return host in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        super().end_headers()

    def permitted(self):
        path = unquote(urlsplit(self.path).path)
        allowed = (
            (
                path.startswith("/desktop/assets/")
                and Path(path).suffix in {".html", ".css", ".js"}
            )
            or path
            in {
                "/desktop/vendor/maplibre-gl.mjs",
                "/desktop/vendor/maplibre-gl-shared.mjs",
                "/desktop/vendor/maplibre-gl-worker.mjs",
                "/desktop/vendor/maplibre-gl.css",
            }
            or (path.startswith("/data/processed/osm/") and path.endswith(".geojson"))
            or path == "/data/raw/osm/beijing_highspeed.geojson"
        )
        return allowed and ".." not in path and "\\" not in path

    def do_HEAD(self):
        if not self.valid_host() or not self.permitted():
            self.send_error(404)
            return
        super().do_HEAD()

    def do_GET(self):
        if not self.valid_host():
            self.send_error(421)
            return
        path = urlsplit(self.path).path
        if path == "/api/rail":
            try:
                from rail_store import viewport

                query = parse_qs(urlsplit(self.path).query)
                selection = {}
                for query_name in ("sections", "ways", "groups"):
                    if query_name in query:
                        value = json.loads(query[query_name][0])
                        if not isinstance(value, list):
                            raise ValueError("线路选择无效")
                        selection[query_name] = value
                payload = json.dumps(
                    viewport(
                        active_rail_directory(ROOT),
                        query["kind"][0],
                        [float(v) for v in query["bbox"][0].split(",")],
                        float(query["zoom"][0]),
                        selection or None,
                    ),
                    ensure_ascii=False,
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except (ValueError, KeyError, OSError):
                self.send_error(400)
            return
        if path == "/config.json":
            payload = json.dumps(self.server.config, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if not self.permitted():
            self.send_error(404)
            return
        super().do_GET()


class Bridge(QObject):
    selected = Signal(str)
    selections = Signal(str)
    initialized = Signal()
    dataLoaded = Signal()
    progress = Signal(float, float, bool)
    state = Signal(bool)
    camera = Signal(float, float, float)
    base = Signal(str, bool)
    error = Signal(str)
    notice = Signal(str)
    screenshot = Signal(str)

    @Slot(str)
    def imageCaptured(self, data):
        self.screenshot.emit(data)

    @Slot(str)
    def featureSelected(self, data):
        self.selected.emit(data)

    @Slot(str)
    def featuresSelected(self, data):
        self.selections.emit(data)

    @Slot()
    def ready(self):
        self.initialized.emit()

    @Slot()
    def dataReady(self):
        self.dataLoaded.emit()

    @Slot(float, float, bool)
    def demoProgress(self, distance, length, running):
        self.progress.emit(distance, length, running)

    @Slot(bool)
    def demoState(self, running):
        self.state.emit(running)

    @Slot(float, float, float)
    def cameraChanged(self, lon, lat, zoom):
        self.camera.emit(lon, lat, zoom)

    @Slot(str, bool)
    def baseChanged(self, kind, vector):
        self.base.emit(kind, vector)

    @Slot(str)
    def mapError(self, error):
        self.error.emit(error)


class MapPage(QWebEnginePage):
    def __init__(self, parent):
        super().__init__(parent)
        self.console_errors = []
        self.runtime_errors = []

    def javaScriptConsoleMessage(self, level, message, line, source):
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            self.console_errors.append(message)
            del self.console_errors[:-100]

    def errors(self):
        return [*self.runtime_errors, *self.console_errors]


class MapView(QWebEngineView):
    def __init__(self, server):
        super().__init__()
        self.setPage(MapPage(self))
        self.bridge = Bridge(self)
        self.bridge.error.connect(self.page().runtime_errors.append)
        self.channel = QWebChannel(self.page())
        self.channel.registerObject("bridge", self.bridge)
        self.page().setWebChannel(self.channel)
        self.commands = MapCommands()
        self.command_in_flight = False
        self.is_ready = False
        self.renderer_restarts = 0
        self.bridge.initialized.connect(self._ready)
        self.page().renderProcessTerminated.connect(self._renderer_terminated)
        self.load(
            QUrl(f"http://127.0.0.1:{server.server_port}/desktop/assets/map.html")
        )

    def call(self, method, *args):
        code = (
            f"window.railscope.{method}("
            + ",".join(json.dumps(arg, ensure_ascii=False) for arg in args)
            + ")"
        )
        self.commands.put(method, args, code)
        self._dispatch()

    def _dispatch(self):
        if not self.is_ready or self.command_in_flight:
            return
        code = self.commands.pop()
        if code is not None:
            self.command_in_flight = True
            self.page().runJavaScript(code, self._completed)

    def _completed(self, result):
        self.command_in_flight = False
        self._dispatch()

    def _ready(self):
        self.is_ready = True
        self._dispatch()

    def _renderer_terminated(self, _status, exit_code):
        self.is_ready = False
        self.command_in_flight = False
        self.renderer_restarts += 1
        message = f"地图渲染进程异常退出（代码 {exit_code}）"
        if self.renderer_restarts <= 2:
            self.bridge.notice.emit(message + "，正在恢复地图…")
            QTimer.singleShot(750, self.reload)
        else:
            self.bridge.error.emit(message + "；已停止自动重试，请保存计划后重启软件。")


class Desk(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RailScope · 轨道交通工作台")
        self.setWindowIcon(QIcon(logo_pixmap()))
        self.resize(1600, 980)
        self.setMinimumSize(1050, 720)
        self.repo = load_demo()
        self.catalog = read_json(
            DATA / "china_metro_route_catalog.json", {"routes": []}
        )["routes"]
        self.manifest = read_json(DATA / "china_metro_import_manifest.json")
        shanghai_relations = {
            route["osm_relation_id"]
            for route in self.catalog
            if "上海" in str(route.get("network", ""))
        }
        features = []
        self.route_centers = {}
        self.route_bounds = {}
        route_file = DATA / "china_metro_routes.geojson"
        for feature in iter_geojson_features(route_file) if route_file.exists() else []:
            relation = feature["properties"]["route_relation_id"]
            coordinates = feature["geometry"]["coordinates"]
            self.route_centers.setdefault(relation, coordinates[0])
            xs, ys = zip(*coordinates)
            bounds = self.route_bounds.setdefault(
                relation, [min(xs), min(ys), max(xs), max(ys)]
            )
            bounds[:] = [
                min(bounds[0], min(xs)),
                min(bounds[1], min(ys)),
                max(bounds[2], max(xs)),
                max(bounds[3], max(ys)),
            ]
            if relation in shanghai_relations:
                features.append(feature)
        stations = read_json(DATA / "china_metro_stations.geojson", EMPTY)["features"]
        self.station_registry = StationRegistry(ROOT / "data/user_settings/workspace.sqlite")
        self.display_stations = {
            "type": "FeatureCollection",
            "features": display_stations(stations, registry=self.station_registry),
        }
        self.station_areas = {
            "type": "FeatureCollection",
            "features": associate_station_areas(
                read_json(DATA / "china_metro_station_areas.geojson", EMPTY)[
                    "features"
                ],
                stations,
            ),
        }
        self.station_areas["features"] = display_station_areas(
            self.station_areas["features"], stations, registry=self.station_registry
        )
        self.shanghai_lines = build_shanghai_lines(self.catalog, features, stations)
        self.plan = Plan(self.shanghai_lines)
        for line in self.shanghai_lines:
            if line["path"] and len(line["stations"]) > 1:
                self.plan.add_train(
                    line["id"], "DEMO-" + line["ref"] + "-01", 25200, "forward"
                )
                self.plan.add_train(
                    line["id"], "DEMO-" + line["ref"] + "-02", 25260, "reverse"
                )
            for variant in line["variants"]:
                key = line["id"] + "@" + str(variant["relation_id"])
                self.plan.lines[key] = {
                    **line,
                    **variant,
                    "id": key,
                    "name": line["name"] + " · " + variant["source_name"],
                }
        self.plan_path = ROOT / "data/processed/operations/shanghai_plan.json"
        self.plan_load_error = ""
        if self.plan_path.exists():
            try:
                self.plan.load(self.plan_path)
            except (ValueError, KeyError, TypeError, OSError) as error:
                self.plan_load_error = str(error)
        self.construction = read_json(DATA / "china_metro_construction.geojson", EMPTY)
        self.construction_catalog = []
        for feature in self.construction["features"]:
            props = feature["properties"]
            tags = props.get("way_tags", {})
            engineering = (
                tags.get("project:name")
                or tags.get("construction:name")
                or tags.get("project")
            )
            name = engineering or (props.get("line_name") or "未分类") + " · 在建工程"
            self.construction_catalog.append(
                {
                    "osm_relation_id": -props["osm_way_id"],
                    "name": name,
                    "ref": name,
                    "network": props.get("network"),
                    "operator": props.get("operator"),
                    "display_color": "#475569",
                    "relation_tags": tags,
                    "construction": True,
                    "engineering_name_explicit": bool(engineering),
                }
            )
        for feature in self.construction["features"]:
            self.route_centers[-feature["properties"]["osm_way_id"]] = feature[
                "geometry"
            ]["coordinates"][0]
        self.demo_error = ""
        try:
            self.demo = build_demo_path(features)
        except ValueError as error:
            self.demo_error = str(error)
            self.demo = {
                "coordinates": [],
                "cumulative": [],
                "length_m": 0,
                "relation_id": 199200,
            }
        del features
        self.visible_lines = set()
        self.flags = initial_visibility()
        self.switches = {}
        self.tree_switches = {}
        self.imported = dict(EMPTY)
        self.selected_data = {}
        self.selected_features = []
        self.running = False
        self.distance = 0.0
        self.route_lookup = {r["osm_relation_id"]: r for r in self.catalog}
        self.hierarchy = Hierarchy(
            [*self.catalog, *self.construction_catalog],
            self.route_centers,
            REGIONS,
            ROOT / "data/user_settings/layer_hierarchy.json",
        )
        self.hierarchy_load_error = ""
        try:
            self.hierarchy.load()
        except (ValueError, OSError) as error:
            self.hierarchy_load_error = str(error)
        self.metro_station_overrides = CatalogOverrides(
            ROOT / "data/user_settings/metro_station_catalog.json",
            "railscope.metro-station-catalog.v1",
        )
        try:
            self.metro_station_overrides.load()
        except (ValueError, OSError) as error:
            self.hierarchy_load_error = (
                self.hierarchy_load_error + "\n" if self.hierarchy_load_error else ""
            ) + str(error)
        self.metro_line_overrides = CatalogOverrides(
            ROOT / "data/user_settings/metro_line_catalog.json",
            "railscope.metro-line-catalog.v1",
        )
        try:
            self.metro_line_overrides.load()
        except (ValueError, OSError) as error:
            self.hierarchy_load_error = (
                self.hierarchy_load_error + "\n" if self.hierarchy_load_error else ""
            ) + str(error)
        self.station_exclusions = set()
        self.station_direct_visible = set()
        self.station_directory = {}
        self.station_lookup = {}
        self.config = self.make_config()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), LocalHandler)
        self.server.config = self.config
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.map = MapView(self.server)
        self.operations = OperationsEditor(
            self.plan, self.map, self.shanghai_lines, self.plan_path
        )
        self.rail_operations = RailEditor(
            self.map,
            active_rail_directory(ROOT),
            ROOT / "data/processed/operations/rail_plan.json",
            ROOT / "data/user_settings/rail_catalog.json",
        )
        self.build()
        self.switches["vehicles"] = self.operations.vehicle_switch
        self.operations.vehicle_switch.toggled.connect(
            lambda on: self.set_flag("vehicles", on)
        )
        self.operations.updated.connect(self.refresh_operating_selection)
        self.rail_operations.updated.connect(self.refresh_operating_selection)
        self.connect_map()
        self.map.bridge.initialized.connect(self.restore_map_state)
        self.setStyleSheet(THEME)
        if self.hierarchy_load_error:
            QTimer.singleShot(
                0,
                lambda: QMessageBox.warning(
                    self,
                    "目录设置未载入",
                    "已使用自动归类，原设置文件未覆盖。\n" + self.hierarchy_load_error,
                ),
            )

    def edit_rail_styles(self):
        path = ROOT / "data/user_settings/rail_styles.json"
        dialog = RailStyleDialog(load_rail_styles(path), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            value = dialog.value()
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".json.tmp")
                temporary.write_text(
                    json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                temporary.replace(path)
                self.config["railStyles"] = value
                self.map.call("setRailStyles", value)
            except OSError as error:
                QMessageBox.warning(self, "铁路样式未保存", str(error))

    def edit_metro_styles(self):
        path = ROOT / "data/user_settings/metro_styles.json"
        dialog = MetroStyleDialog(load_metro_styles(path), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            value = dialog.value()
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".json.tmp")
                temporary.write_text(
                    json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                temporary.replace(path)
                self.config["metroStyles"] = value
                self.map.call("setMetroStyles", value)
            except OSError as error:
                QMessageBox.warning(self, "地铁线路样式未保存", str(error))

    def edit_rail_point_styles(self):
        path = ROOT / "data/user_settings/rail_point_styles.json"
        dialog = RailPointStyleDialog(load_rail_point_styles(path), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            value = dialog.value()
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".json.tmp")
                temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(path)
                self.config["railPointStyles"] = value
                self.map.call("setRailPointStyles", value)
            except OSError as error:
                QMessageBox.warning(self, "站点样式未保存", str(error))

    def make_config(self):
        sources = {
            key: "/" + (DATA / name).relative_to(ROOT).as_posix()
            if (DATA / name).exists()
            else EMPTY
            for key, name in {
                "metro": "china_metro_routes.geojson",
                "stations": "china_metro_stations.geojson",
                "areas": "china_metro_station_areas.geojson",
                "construction": "china_metro_construction.geojson",
            }.items()
        }
        rail = ROOT / "data" / "raw" / "osm" / "beijing_highspeed.geojson"
        sources["areas"] = self.station_areas
        # Keep full raw members in Python/registry; do not clone each of them
        # into Chromium and its GeoJSON workers just to draw a station marker.
        sources["stations"] = {"type": "FeatureCollection", "features": [
            {**feature, "properties": {key: value for key, value in feature["properties"].items() if key != "source_members"}}
            for feature in self.display_stations["features"]
        ]}
        sources["construction"] = self.construction
        sources["rail"] = (
            "/data/raw/osm/beijing_highspeed.geojson" if rail.exists() else EMPTY
        )
        rail_data = active_rail_directory(ROOT)
        for key, name in [
            ("rail", "rail_tracks.geojson"),
            ("railPoints", "rail_points.geojson"),
            ("railPlatforms", "rail_platforms.geojson"),
            ("railStationAreas", "rail_station_areas.geojson"),
        ]:
            if (rail_data / "rail.sqlite").exists():
                sources[key] = EMPTY
            elif key != "rail":
                sources[key] = EMPTY
        sources["railVehicles"] = EMPTY
        sources["railSignalBoxes"] = EMPTY
        roads = [
            {
                "type": "Feature",
                "properties": {"id": e.id, "name": e.id, "source": "演示道路参考"},
                "geometry": {"type": "LineString", "coordinates": e.coordinates},
            }
            for e in self.repo.edges.values()
            if e.mode == "road"
        ]
        sources["road"] = {"type": "FeatureCollection", "features": roads}
        sources["imported"] = EMPTY
        return {
            "sources": sources,
            "railStyles": load_rail_styles(ROOT / "data/user_settings/rail_styles.json"),
            "railPointStyles": load_rail_point_styles(ROOT / "data/user_settings/rail_point_styles.json"),
            "metroStyles": load_metro_styles(ROOT / "data/user_settings/metro_styles.json"),
            "railViewport": (rail_data / "rail.sqlite").exists(),
            "visibleIds": sorted(r for r in self.visible_lines if r > 0),
            "constructionIds": sorted(-r for r in self.visible_lines if r < 0),
            "demo": self.demo,
            "cities": [
                {"name": city, "center": [lon, lat]} for city, _, lon, lat in REGIONS
            ],
            "routeBounds": self.route_bounds,
            "lineViews": [
                {
                    "name": self.hierarchy.parent(route)[1]
                    + " · "
                    + self.hierarchy.parent(route)[2]
                    + " · "
                    + route["name"],
                    "city": self.hierarchy.parent(route)[1],
                    "bounds": self.route_bounds[route["osm_relation_id"]],
                }
                for route in self.catalog
                if route["osm_relation_id"] in self.route_bounds
            ],
        }

    def build(self):
        self.build_menus()
        root = QWidget()
        root.setObjectName("workspace")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self.header())
        workspace = QHBoxLayout()
        workspace.setContentsMargins(12, 12, 12, 12)
        workspace.setSpacing(10)
        workspace.addWidget(self.rail())
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.left = self.sidebar()
        self.right = self.inspector()
        self.splitter.addWidget(self.left)
        self.map_stack = QSplitter(Qt.Orientation.Vertical)
        self.map.setMinimumHeight(200)
        self.map_stack.addWidget(self.map)
        self.editor_hosts = []
        for editor in (self.operations, self.rail_operations):
            host = QWidget()
            row = QHBoxLayout(host)
            row.setContentsMargins(8, 0, 8, 0)
            row.addStretch()
            row.addWidget(editor, 1)
            row.addStretch()
            editor.closed.connect(host.hide)
            host.hide()
            self.editor_hosts.append(host)
            self.map_stack.addWidget(host)
        self.operations.workspace_requested.connect(self.open_metro_operations)
        self.rail_operations.workspace_requested.connect(self.open_rail_operations)
        self.rail_operations.hide()
        self.map_stack.setSizes([350, 500, 0])
        self.operations.expand_requested.connect(
            lambda: self.resize_run_editor(0, expanded=True)
        )
        self.rail_operations.expand_requested.connect(
            lambda: self.resize_run_editor(1, expanded=True)
        )
        self.operations.hide()
        self.splitter.addWidget(self.map_stack)
        self.splitter.addWidget(self.right)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([310, 910, 320])
        workspace.addWidget(self.splitter, 1)
        outer.addLayout(workspace, 1)
        self.setCentralWidget(root)
        self.build_status()

    def add_action(self, menu, text, callback, shortcut=None):
        action = QAction(text, self)
        action.triggered.connect(lambda checked=False: callback())
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        menu.addAction(action)
        return action

    def save_workspace(self):
        """Flush independent plans; catalog overrides are written on each edit."""
        self.operations.save()
        self.rail_operations.save()
        self.hierarchy.save()
        self.statusBar().showMessage("工作区已保存", 5000)

    def save_workspace_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "工作区另存为",
            str(ROOT / "data/logs/railscope-workspace.json"),
            "RailScope JSON (*.json)",
        )
        if not path:
            return
        payload = {
            "schema": "railscope.workspace.v1",
            "metro_line_catalog": self.metro_line_overrides.values,
            "metro_station_catalog": self.metro_station_overrides.values,
            "rail_catalog": self.rail_catalog_widget.local_overrides,
            "metro_plan": self.plan.snapshot(),
            "rail_plan": self.rail_operations.document(),
        }
        temporary = Path(path).with_suffix(Path(path).suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def export_catalog_overrides(self, kind):
        labels = {
            "rail-lines": "铁路线目录",
            "rail-stations": "铁路站目录",
            "metro-lines": "地铁线路目录",
            "metro-stations": "地铁站目录",
        }
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出" + labels[kind],
            str(ROOT / "data/logs" / (kind + ".json")),
            "JSON (*.json)",
        )
        if not path:
            return
        if kind.startswith("rail-"):
            station = kind == "rail-stations"
            values = {
                key: value for key, value in self.rail_catalog_widget.local_overrides.items()
                if key.startswith("station:") == station
            }
        else:
            values = dict(
                self.metro_line_overrides.values
                if kind == "metro-lines"
                else self.metro_station_overrides.values
            )
        payload = {
            "schema": "railscope.catalog-exchange.v1",
            "kind": kind,
            "overrides": values,
        }
        temporary = Path(path).with_suffix(Path(path).suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def import_catalog_overrides(self, kind):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入目录分类", "", "JSON (*.json)"
        )
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            if payload.get("schema") != "railscope.catalog-exchange.v1" or payload.get("kind") != kind or not isinstance(payload.get("overrides"), dict):
                raise ValueError("目录文件类型或格式不匹配")
            values = payload["overrides"]
            if kind.startswith("rail-"):
                self.rail_catalog_widget._save_local_overrides(values)
                self.rail_catalog_widget.populate()
                self.rail_catalog_widget.populate_station_tree()
                self.refresh_signal_boxes()
            else:
                store = self.metro_line_overrides if kind == "metro-lines" else self.metro_station_overrides
                store.update_many(values)
                self.refresh_hierarchy()
            self.load_status.setText("  目录文件已导入并应用；原始 OSM 数据未修改")
        except (OSError, ValueError, TypeError) as error:
            QMessageBox.warning(self, "目录未导入", str(error))

    def build_menus(self):
        bar = self.menuBar()
        file = bar.addMenu("文件")
        self.add_action(file, "保存工作区", self.save_workspace, "Ctrl+S")
        self.add_action(file, "工作区另存为…", self.save_workspace_as, "Ctrl+Shift+S")
        file.addSeparator()
        self.add_action(file, "导入 GeoJSON…", self.import_file, "Ctrl+I")
        self.add_action(file, "导入国铁运行通道…", self.rail_operations.import_corridors)
        self.add_action(file, "导出国铁运行通道…", self.rail_operations.export_corridors)
        self.add_action(file, "导出当前可见图层…", self.export_visible, "Ctrl+E")
        shots = file.addMenu("导出截图")
        self.add_action(shots, "导出当前地图 PNG…", self.capture_map)
        self.add_action(shots, "导出完整运行图 PNG…", self.capture_diagram)
        file.addSeparator()
        self.add_action(file, "退出", self.close, "Alt+F4")
        edit = bar.addMenu("编辑")
        self.add_action(edit, "撤销目录调整", lambda: self.rail_catalog_widget.undo_catalog(), "Ctrl+Z")
        self.add_action(edit, "重做目录调整", lambda: self.rail_catalog_widget.redo_catalog(), "Ctrl+Y")
        edit.addSeparator()
        self.add_action(edit, "单击选择工具", lambda: self.set_map_selection_mode("click"), "V")
        self.add_action(edit, "框选工具", lambda: self.set_map_selection_mode("box"), "B")
        self.add_action(edit, "移动所选对象到目录…", self.move_map_selection)
        self.add_action(edit, "重命名所选对象…", self.rename_map_selection)
        self.add_action(edit, "归档所选对象", self.archive_map_selection)
        edit.addSeparator()
        self.add_action(edit, "地铁线路目录整理…", self.edit_hierarchy)
        self.add_action(
            edit,
            "国铁线路目录整理…",
            lambda: self.rail_catalog_widget.organize(),
        )
        self.add_action(
            edit, "国铁物理线路规范命名…", self.rail_operations.organize_lines
        )
        map_menu = bar.addMenu("显示")
        self.add_action(map_menu, "打开图层控制", lambda: self.open_sidebar(0))
        styles = map_menu.addMenu("线路显示样式")
        self.add_action(styles, "地铁线路样式…", self.edit_metro_styles)
        self.add_action(styles, "国铁线路样式…", self.edit_rail_styles)
        self.add_action(styles, "车站与线路所/道岔样式…", self.edit_rail_point_styles)
        map_menu.addSeparator()
        self.add_action(map_menu, "显示全部地铁线路", lambda: self.set_all_lines(True))
        self.add_action(map_menu, "隐藏全部地铁线路", lambda: self.set_all_lines(False))
        self.add_action(map_menu, "清除导入图层", self.clear_imported)
        map_menu.addSeparator()
        self.add_action(map_menu, "定位全国路网", lambda: self.map.call("focusChina"))
        self.add_action(map_menu, "定位上海 1 号线", lambda: self.map.call("focusDemo"))
        run_menu = bar.addMenu("运行")
        run = run_menu.addMenu("地铁运行")
        self.add_action(
            run, "交路、车辆循环与投放…", self.operations.open_cycles
        )
        self.add_action(
            run, "导出线路与车站参考目录…", self.export_plan_references
        )
        self.add_action(run, "打开运行工作台", self.open_metro_operations)
        self.add_action(
            run,
            "开始地铁仿真",
            lambda: (self.open_metro_operations(), self.operations.play()),
        )
        self.add_action(run, "暂停地铁仿真", self.pause_demo)
        self.add_action(run, "关闭运行展示", lambda: self.operations.set_enabled(False))
        self.add_action(run, "回到始发时刻", self.reset_demo)
        self.add_action(run, "保存地铁计划", self.operations.save)
        self.add_action(
            run,
            "导入地铁计划…",
            lambda: (self.open_metro_operations(), self.operations.import_plan()),
        )
        self.add_action(run, "导出地铁运行计划…", self.operations.export_plan)
        rail_run = run_menu.addMenu("国铁运行")
        self.add_action(
            rail_run, "批量导入国铁车次表（CSV）…", self.rail_operations.import_table
        )
        self.add_action(
            rail_run, "导出国铁车次表 / CSV 模板…", self.rail_operations.export_table
        )
        self.add_action(
            rail_run, "打开车次运行工作台", lambda: self.open_rail_operations()
        )
        self.add_action(
            rail_run,
            "开始国铁仿真",
            lambda: (self.open_rail_operations(), self.rail_operations.play()),
        )
        self.add_action(rail_run, "暂停国铁仿真", self.rail_operations.pause)
        self.add_action(
            rail_run, "关闭国铁运行展示", lambda: self.rail_operations.set_enabled(False)
        )
        self.add_action(
            rail_run,
            "导入国铁车次与径路…",
            lambda: (self.open_rail_operations(), self.rail_operations.import_plan()),
        )
        self.add_action(rail_run, "导出国铁运行计划…", self.rail_operations.export_plan)
        self.add_action(rail_run, "保存国铁计划", self.rail_operations.save)
        self.add_action(
            rail_run, "导出国铁物理区间参考目录（供 AI）…", self.export_rail_references
        )
        run_menu.addSeparator()
        self.add_action(run_menu, "保存当前运行计划", lambda: (
            self.rail_operations if self.run_mode.currentIndex() == 1 else self.operations
        ).save())
        data = bar.addMenu("数据")
        for kind, title in (
            ("rail-lines", "铁路线数据"),
            ("rail-stations", "铁路站数据"),
            ("metro-lines", "地铁线路数据"),
            ("metro-stations", "地铁站数据"),
        ):
            submenu = data.addMenu(title)
            self.add_action(submenu, "导入目录分类…", lambda k=kind: self.import_catalog_overrides(k))
            self.add_action(submenu, "导出目录分类…", lambda k=kind: self.export_catalog_overrides(k))
        corridor_data = data.addMenu("通道数据")
        self.add_action(corridor_data, "导入…", self.rail_operations.import_corridors)
        self.add_action(corridor_data, "导出…", self.rail_operations.export_corridors)
        train_data = data.addMenu("车次数据")
        self.add_action(train_data, "批量导入 CSV…", self.rail_operations.import_table)
        self.add_action(train_data, "导出 CSV / 模板…", self.rail_operations.export_table)
        metro_run_data = data.addMenu("地铁运行数据")
        self.add_action(metro_run_data, "导入…", self.operations.import_plan)
        self.add_action(metro_run_data, "导出…", self.operations.export_plan)
        data.addSeparator()
        self.add_action(data, "下载或更新全国地铁数据…", self.open_data_download)
        self.add_action(data, "下载或更新全国铁路数据…", self.open_rail_download)
        self.add_action(
            data, "刷新铁路目录", lambda: self.rail_catalog_widget.refresh_catalog()
        )
        self.add_action(data, "检查全国地铁车站建筑覆盖…", self.audit_station_boundaries)
        self.add_action(
            data, "检查全国铁路站区与站台覆盖…", self.audit_rail_boundaries
        )
        self.add_action(data, "查看数据概览", self.show_data_summary)
        topology = bar.addMenu("工具")
        self.add_action(topology, "单击选择", lambda: self.set_map_selection_mode("click"))
        self.add_action(topology, "框选对象", lambda: self.set_map_selection_mode("box"))
        self.add_action(topology, "清除地图选择", lambda: self.map.call("clearSelection"))
        self.add_action(topology, "由所选道岔新建线路所…", self.create_signal_box_from_selection)
        topology.addSeparator()
        self.add_action(topology, "打开国铁运行通道编排", lambda: self.open_sidebar(2))
        self.add_action(
            topology,
            "导出铁路拓扑图（端点—线路—端点）…",
            self.rail_operations.export_line_library,
        )
        self.add_action(topology, "校验基础设施连通性", self.show_topology)
        view = bar.addMenu("窗口")
        self.add_action(view, "展开 / 收起左侧栏", self.toggle_left)
        self.add_action(view, "展开 / 收起对象详情", self.toggle_right)
        view.addSeparator()
        self.overlay_actions = {}
        for key, title, default in (
            ("title", "地图标题卡片", False),
            ("tools", "地图导航工具", True),
            ("legend", "地图图例", True),
            ("scale", "比例尺", True),
        ):
            action = QAction(title, self)
            action.setCheckable(True)
            action.setChecked(default)
            action.toggled.connect(
                lambda on, key=key: self.map.call("setOverlay", key, on)
            )
            view.addAction(action)
            self.overlay_actions[key] = action
        view.addSeparator()
        self.add_action(
            view, "搜索线路或站点", lambda: self.search.setFocus(), "Ctrl+F"
        )
        self.add_action(
            view,
            "全屏",
            lambda: self.showNormal() if self.isFullScreen() else self.showFullScreen(),
            "F11",
        )
        help = bar.addMenu("帮助")
        self.add_action(help, "图层与数据说明", self.show_data_summary)
        self.add_action(help, "车次导入指南", self.show_train_import_guide)
        self.add_action(help, "下载车次导入模板…", self.rail_operations.export_template)
        self.add_action(help, "运行计划交换标准 / AI 编写说明", self.show_plan_standard)
        self.map.bridge.screenshot.connect(self.save_map_capture)

    def show_plan_standard(self):
        from PySide6.QtWidgets import QTextBrowser

        dialog = QDialog(self)
        dialog.setWindowTitle("运行计划交换标准")
        dialog.resize(1000, 760)
        layout = QVBoxLayout(dialog)
        browser = QTextBrowser()
        browser.setMarkdown(
            (ROOT / "docs/OPERATING_PLAN_STANDARD.md").read_text(encoding="utf-8")
        )
        layout.addWidget(browser)
        dialog.exec()

    def audit_station_boundaries(self):
        from boundary_ui import BoundaryDialog

        stations = read_json(DATA / "china_metro_stations.geojson", EMPTY)["features"]
        dialog = BoundaryDialog(stations, self.station_areas["features"], self)
        dialog.located.connect(self.locate_station_record)
        dialog.exec()

    def show_train_import_guide(self):
        from PySide6.QtWidgets import QTextBrowser

        dialog = QDialog(self)
        dialog.setWindowTitle("国铁车次导入指南")
        dialog.resize(900, 700)
        layout = QVBoxLayout(dialog)
        browser = QTextBrowser()
        browser.setMarkdown((ROOT / "docs/TRAIN_IMPORT_GUIDE.md").read_text(encoding="utf-8"))
        layout.addWidget(browser)
        download = QPushButton("下载导入模板…")
        download.clicked.connect(self.rail_operations.export_template)
        layout.addWidget(download)
        dialog.exec()

    def locate_station_record(self, record):
        self.visible_lines.update(record.get("route_relation_ids", []))
        self.sync_tree_switches()
        self.send_directory_filter()
        self.set_flag("stations", True)
        self.select_metro_tree_item(record.get("route_relation_ids", []))
        self.open_sidebar(0)
        self.map.call("focus", *record["coordinates"], 16, record["name"])
        self.display_feature({"layer": "stations", "properties": record})

    def audit_rail_boundaries(self):
        from boundary_ui import BoundaryDialog

        path = active_rail_directory(ROOT) / "rail_boundary_coverage.json"
        if not path.is_file():
            QMessageBox.information(
                self, "尚无铁路轮廓报告", "请先通过文件菜单下载 / 提取全国铁路数据。"
            )
            return
        dialog = BoundaryDialog([], [], self, records=read_json(path, []))
        dialog.setWindowTitle("全国真实铁路站区 / 站台 · 缺失与覆盖清单")
        dialog.exec()

    def export_plan_references(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出地铁计划参考目录",
            str(ROOT / "data/logs/metro-reference.json"),
            "JSON (*.json)",
        )
        if not path:
            return
        references = {
            "system": "metro",
            "distance_unit": "m",
            "lines": [
                {
                    "id": line["id"],
                    "name": line["name"],
                    "relation_id": line["relation_id"],
                    "stations": line["stations"],
                }
                for line in self.plan.lines.values()
                if line.get("path")
            ],
            "notice": "编号与里程必须照此引用；不是官方运行时刻表",
        }
        try:
            Path(path).write_text(
                json.dumps(references, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as error:
            QMessageBox.warning(self, "不能导出", str(error))

    def export_rail_references(self):
        import shutil

        source = active_rail_directory(ROOT) / "rail_graph.json"
        if not source.exists():
            QMessageBox.information(
                self, "尚无国铁参考目录", "请先使用数据源菜单下载 / 提取国铁基础设施"
            )
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出国铁物理区间（全国目录较大，AI 应选取相关区间）",
            str(ROOT / "data/logs/rail-reference.json"),
            "JSON (*.json)",
        )
        if path:
            try:
                if Path(path).resolve() != source.resolve():
                    shutil.copy2(source, path)
            except OSError as error:
                QMessageBox.warning(self, "无法导出", str(error))

    def capture_map(self):
        if not self.map.is_ready:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存当前地图截图", str(ROOT / "data/logs/map.png"), "PNG (*.png)"
        )
        if path:
            self._capture_path = path
            self.map.call("captureMap")

    def save_map_capture(self, data):
        if not getattr(self, "_capture_path", None):
            return
        try:
            if not data.startswith("data:image/png;base64,"):
                raise ValueError("地图截图未成功，请等待底图加载后重试")
            image = base64.b64decode(data.split(",", 1)[1], validate=True)
            Path(self._capture_path).write_bytes(image)
            self.load_status.setText("  地图截图已保存：" + self._capture_path)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "截图失败", str(error))
        finally:
            self._capture_path = None

    def capture_diagram(self):
        from PySide6.QtGui import QImage, QPainter

        editor = (
            self.rail_operations
            if self.run_mode.currentIndex() == 1
            else self.operations
        )
        rect = editor.scene.sceneRect()
        if rect.width() * rect.height() > 60_000_000:
            QMessageBox.warning(self, "截图过大", "请先选择车次或缩小运行图时间范围")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存完整运行图", str(ROOT / "data/logs/diagram.png"), "PNG (*.png)"
        )
        if not path:
            return
        image = QImage(
            max(1, int(rect.width())),
            max(1, int(rect.height())),
            QImage.Format.Format_ARGB32,
        )
        image.fill(QColor("white"))
        painter = QPainter(image)
        editor.scene.render(painter)
        painter.end()
        if not image.save(path):
            QMessageBox.warning(self, "截图失败", "无法写入 PNG 文件")

    def header(self):
        header = frame("header")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(22, 13, 22, 13)
        layout.setSpacing(12)
        logo = QLabel()
        logo.setPixmap(logo_pixmap())
        layout.addWidget(logo)
        layout.addWidget(text_label("RailScope", "brand"))
        title = QVBoxLayout()
        title.setSpacing(2)
        title.addWidget(QLabel("轨道交通工作台"))
        title.addWidget(text_label("全国路网 / 单一地图工作区", "subheading"))
        layout.addLayout(title)
        layout.addStretch()
        self.search_type = QComboBox()
        self.search_type.addItems(
            ["全部", "城市", "地铁线路", "地铁站", "铁路线", "车站及线路所"]
        )
        self.search_type.setMinimumWidth(105)
        layout.addWidget(self.search_type)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索名称或稳定编号   Ctrl+F")
        self.search.setMinimumWidth(260)
        self.search.setMaximumWidth(345)
        self.search.returnPressed.connect(self.search_map)
        completer = QCompleter(
            [city for city, *_ in REGIONS] + ["上海 1 号线"], self.search
        )
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.search.setCompleter(completer)
        layout.addWidget(self.search)
        locate = QToolButton()
        locate.setText("定位 ▾")
        locate.setToolTip("按城市、线路或经纬度定位")
        locate.clicked.connect(lambda: self.map.call("showLocationPanel"))
        layout.addWidget(locate)
        layout.addWidget(text_label("本地 OSM 数据", "badge"))
        return header

    def rail(self):
        rail = frame("rail")
        rail.setFixedWidth(54)
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(8)
        self.map_rail = self.rail_button(
            "图层", "展开地图图层，再次点击收起左侧栏", lambda: self.open_or_toggle(0)
        )
        self.run_rail = self.rail_button(
            "运行", "展开运行控制", lambda: self.open_or_toggle(1)
        )
        layout.addWidget(self.map_rail)
        layout.addWidget(self.run_rail)
        layout.addStretch()
        self.detail_rail = self.rail_button(
            "详情", "展开或收起右侧对象详情", self.toggle_right
        )
        self.detail_rail.setChecked(True)
        layout.addWidget(self.detail_rail)
        self.map_rail.setChecked(True)
        return rail

    def rail_button(self, text, tooltip, callback, checkable=True):
        button = QToolButton()
        button.setObjectName("railButton")
        button.setText(text)
        button.setFixedSize(44, 44)
        button.setToolTip(tooltip)
        button.setCheckable(checkable)
        button.clicked.connect(callback)
        return button

    def panel_top(self, layout, title, subtitle, collapse):
        row = QHBoxLayout()
        captions = QVBoxLayout()
        captions.setSpacing(3)
        title_label = text_label(title, "panelTitle")
        subtitle_label = text_label(subtitle)
        captions.addWidget(title_label)
        captions.addWidget(subtitle_label)
        if title == "图层控制":
            self.side_title, self.side_subtitle = title_label, subtitle_label
        row.addLayout(captions, 1)
        button = QToolButton()
        button.setArrowType(
            Qt.ArrowType.LeftArrow if title == "图层控制" else Qt.ArrowType.RightArrow
        )
        button.setToolTip("收起侧栏；通过左侧固定工具栏重新展开")
        button.clicked.connect(collapse)
        row.addWidget(button)
        layout.addLayout(row)

    def sidebar(self):
        panel = frame("panel")
        panel.setMinimumWidth(285)
        panel.setMaximumWidth(420)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(14)
        self.panel_top(layout, "图层控制", "按要素与线路组织地图", self.toggle_left)
        self.side_pages = QStackedWidget()
        self.side_pages.addWidget(self.map_controls())
        self.side_pages.addWidget(self.run_controls())
        layout.addWidget(self.side_pages, 1)
        return panel

    def new_switch(self, key, title, subtitle=""):
        control = Switch(self.flags[key])
        control.toggled.connect(lambda on, k=key: self.set_flag(k, on))
        self.switches[key] = control
        return switch_row(title, control, subtitle)

    def map_controls(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 5, 0)
        layout.setSpacing(10)
        layout.addWidget(
            Fold("地铁", self.metro_controls(), count=f"{len(self.catalog)} 个关系")
        )
        layout.addWidget(Fold("公路", self.reference_controls("road"), expanded=False))
        layout.addWidget(Fold("国铁", self.rail_controls(), expanded=False))
        layout.addStretch()
        layout.addWidget(Fold("底图", self.base_controls(), expanded=False))
        scroll.setWidget(body)
        return scroll

    def base_controls(self):
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 0, 8, 6)
        layout.setSpacing(5)
        self.base_combo = QComboBox()
        self.base_combo.addItems(["标准地图", "卫星影像 · 10 m", "行政区划"])
        self.base_combo.currentIndexChanged.connect(
            lambda index: self.map.call(
                "setBase", ("standard", "satellite", "admin")[index]
            )
        )
        layout.addWidget(self.base_combo)
        details = QWidget()
        detail_layout = QVBoxLayout(details)
        detail_layout.setContentsMargins(6, 0, 6, 3)
        self.base_switches = {}
        for title, key in [
            ("道路信息", "roads"),
            ("行政区划与地名", "admin"),
            ("其他文字与 POI", "labels"),
            ("建筑信息", "buildings"),
        ]:
            switch = Switch(True)
            switch.toggled.connect(
                lambda on, k=key: self.map.call("setBaseDetail", k, on)
            )
            switch.setEnabled(False)
            self.base_switches[key] = switch
            detail_layout.addWidget(switch_row(title, switch))
        layout.addWidget(Fold("信息显示", details, expanded=False, count="4 项"))
        self.base_hint = text_label("底图连接中…", wrap=True)
        layout.addWidget(self.base_hint)
        return body

    def metro_controls(self):
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 0, 8, 6)
        layout.setSpacing(5)
        layout.addWidget(self.new_switch("metro", "运营线路", "保留 OSM 原始线路颜色"))
        layout.addWidget(self.new_switch("stations", "地铁站", "POI 与真实 OSM 站区面"))
        layout.addWidget(self.new_switch("construction", "在建线路", "深灰色虚线"))
        self.metro_catalog_tabs = QTabWidget()
        self.metro_line_page = QWidget()
        line_layout = QVBoxLayout(self.metro_line_page)
        line_layout.setContentsMargins(0, 0, 0, 0)
        line_layout.addWidget(text_label("地铁线路目录", "sectionLabel"))
        self.line_search = QLineEdit()
        self.line_search.setPlaceholderText("筛选城市 / 线路")
        self.line_search.textChanged.connect(self.filter_tree)
        line_layout.addWidget(self.line_search)
        self.tree = GrowingTree()
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(14)
        self.tree.setUniformRowHeights(True)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(1, 62)
        self.populate_tree()
        self.tree.itemDoubleClicked.connect(self.focus_tree_item)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.metro_context_menu)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        line_layout.addWidget(self.tree)
        self.line_count = text_label("")
        line_layout.addWidget(self.line_count)
        self.metro_catalog_tabs.addTab(self.metro_line_page, "地铁线路目录")
        self.metro_station_page = QWidget()
        station_layout = QVBoxLayout(self.metro_station_page)
        station_layout.setContentsMargins(0, 0, 0, 0)
        station_layout.addWidget(text_label("地铁站目录", "sectionLabel"))
        self.station_tree = GrowingTree()
        self.station_tree.setColumnCount(2)
        self.station_tree.setHeaderHidden(True)
        self.station_tree.setRootIsDecorated(True)
        self.station_tree.setIndentation(14)
        self.station_tree.setUniformRowHeights(True)
        self.station_tree.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.station_tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.station_tree.header().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Fixed
        )
        self.station_tree.setColumnWidth(1, 62)
        self.station_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.station_tree.itemDoubleClicked.connect(self.focus_station_tree_item)
        self.station_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.station_tree.customContextMenuRequested.connect(self.metro_station_context_menu)
        station_layout.addWidget(self.station_tree)
        self.station_count = text_label("")
        station_layout.addWidget(self.station_count)
        self.metro_catalog_tabs.addTab(self.metro_station_page, "地铁站目录")
        layout.addWidget(self.metro_catalog_tabs)
        self.populate_station_tree()
        self.update_count()
        return body

    def route_city(self, route):
        return self.hierarchy.parent(route)[:2]

    def metro_context_menu(self, position):
        from rail_catalog_ui import add_folder_move_menu

        item = self.tree.itemAt(position)
        if not item:
            return
        selected = self.tree.selectedItems()
        if item not in selected:
            self.tree.clearSelection()
            item.setSelected(True)
            self.tree.setCurrentItem(item)
            selected = [item]
        ids = set().union(*(self.item_ids(value) for value in selected))
        if not ids:
            return
        menu = QMenu(self)
        menu.addAction(
            "编辑名称与目录…", lambda: self.edit_hierarchy(ids)
        ).setEnabled(len(ids) == 1)
        if len(ids) > 1:
            menu.addAction(
                "批量添加名称前缀…", lambda: self.prefix_metro_line_names(ids)
            )
        move = menu.addMenu("移动到")
        destinations = {
            self.hierarchy.parent(route)[:2]
            for route in self.hierarchy.routes
        }
        add_folder_move_menu(
            move, destinations, lambda path: self.move_metro_lines(ids, path)
        )
        visible = bool(ids & self.visible_lines)
        menu.addAction(
            "隐藏" if visible else "显示",
            lambda: self.toggle_metro_line_ids(ids, not visible),
        )
        menu.addAction("定位到此线路 / 分组", lambda: self.focus_tree_item(item, 0))
        archived = all(
            self.metro_line_overrides.values.get(str(value), {}).get(
                "archived", False
            )
            for value in ids
        )
        menu.addAction(
            "取消归档 / 恢复" if archived else "归档",
            lambda: self.archive_metro_lines(ids, not archived),
        )
        menu.exec(self.tree.viewport().mapToGlobal(position))
        menu.deleteLater()

    def edit_hierarchy(self, ids=None):
        if ids is None:
            selected = self.tree.selectedItems()
            ids = set().union(*(self.item_ids(item) for item in selected)) if selected else set()
        dialog = HierarchyDialog(self.hierarchy, self, ids)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.hierarchy.overrides = dialog.model.overrides
        self.refresh_hierarchy(ids)
        self.load_status.setText("  目录层级已保存 · 原始 OSM 数据与显示开关保持不变")

    def move_metro_lines(self, relation_ids, path):
        if len(path) < 2:
            raise ValueError("请选择省级和市级目录")
        self.hierarchy.set_parent(
            set(relation_ids), province=str(path[0]), city=str(path[1])
        )
        self.hierarchy.save()
        self.refresh_hierarchy(relation_ids)

    def toggle_metro_line_ids(self, relation_ids, on):
        relation_ids = set(relation_ids)
        self.visible_lines.update(relation_ids) if on else self.visible_lines.difference_update(
            relation_ids
        )
        if on:
            if any(value > 0 for value in relation_ids):
                self.set_flag("metro", True)
            if any(value < 0 for value in relation_ids):
                self.set_flag("construction", True)
        self.sync_tree_switches()
        self.send_directory_filter()
        self.update_count()

    def archive_metro_lines(self, relation_ids, archived=True):
        relation_ids = set(relation_ids)
        self.metro_line_overrides.update_many({
            str(value): {"archived": bool(archived)} for value in relation_ids
        })
        if archived:
            self.visible_lines.difference_update(relation_ids)
        self.refresh_hierarchy(relation_ids)
        self.send_directory_filter()

    def prefix_metro_line_names(self, relation_ids):
        prefix, accepted = QInputDialog.getText(
            self, "批量重命名地铁线路", "为所选线路添加名称前缀"
        )
        prefix = prefix.strip()
        if not accepted or not prefix:
            return
        self.metro_line_overrides.update_many({
            str(value): {
                "display_name": prefix
                + (
                    self.metro_line_overrides.values.get(str(value), {}).get(
                        "display_name"
                    )
                    or self.hierarchy.parent(self.route_lookup[value])[2]
                )
            }
            for value in relation_ids
            if value in self.route_lookup
        })
        self.refresh_hierarchy(relation_ids)

    def refresh_hierarchy(self, selected_ids=None):
        expanded = {item.text(0) for item in self.tree_items if item.isExpanded()}
        self.populate_tree()
        self.sync_tree_switches()
        for item in self.tree_items:
            if item.text(0) in expanded:
                item.setExpanded(True)
            if not item.childCount() and set(selected_ids or []) & self.item_ids(item):
                self.tree.setCurrentItem(item)
                item.parent().setExpanded(True)
                item.parent().parent().setExpanded(True)
        self.filter_tree(self.line_search.text())
        self.populate_station_tree()
        self.update_count()

    def populate_station_tree(self):
        self.station_directory, self.station_lookup = metro_station_directory(
            self.display_stations["features"],
            self.catalog,
            self.hierarchy,
            self.metro_station_overrides.values,
        )
        self.station_tree.clear()
        self.station_tree_items = []
        self.station_tree_switches = {}
        aliases = 0
        for province, cities in sorted(self.station_directory.items()):
            province_item = QTreeWidgetItem([province, ""])
            self.station_tree.addTopLevelItem(province_item)
            province_ids = set()
            for city, lines in sorted(cities.items()):
                city_item = QTreeWidgetItem([city, ""])
                province_item.addChild(city_item)
                city_ids = set()
                for line, stations in sorted(lines.items(), key=lambda item: label_order(item[0])):
                    line_item = QTreeWidgetItem([line, ""])
                    city_item.addChild(line_item)
                    line_ids = set()
                    for station in stations:
                        aliases += 1
                        leaf = QTreeWidgetItem([station["name"], ""])
                        leaf.setData(0, Qt.ItemDataRole.UserRole, [station["id"]])
                        leaf.setToolTip(
                            0,
                            f"线路站点对象：{station['id']}\n"
                            f"物理车站实体：{station['physical_station_id']}\n"
                            f"线路关系：{', '.join(map(str, station['route_relation_ids']))}",
                        )
                        line_item.addChild(leaf)
                        self.install_station_switch(leaf, {station["id"]})
                        line_ids.add(station["id"])
                    line_item.setText(0, f"{line} · {len(stations)} 站")
                    self.install_station_switch(line_item, line_ids)
                    city_ids.update(line_ids)
                self.install_station_switch(city_item, city_ids)
                province_ids.update(city_ids)
            self.install_station_switch(province_item, province_ids)
        self.station_count.setText(
            f"{len(self.station_lookup):,} 个唯一站点实体 · {aliases:,} 个线路目录入口"
        )
        self.station_tree.schedule_height()

    def effective_station_ids(self):
        if not self.flags.get("stations"):
            return set()
        visible_routes = {value for value in self.visible_lines if value > 0}
        return {
            station_id
            for station_id, record in self.station_lookup.items()
            if (station_id in self.station_direct_visible
                or visible_routes.intersection(record["route_relation_ids"]))
            and station_id not in self.station_exclusions
            and not record.get("archived", False)
        }

    def install_station_switch(self, item, station_ids):
        item.setData(0, Qt.ItemDataRole.UserRole, sorted(station_ids))
        visible = self.effective_station_ids()
        active = len(station_ids & visible)
        control = Switch(active > 0)
        control.setMixed(0 < active < len(station_ids))
        control.toggled.connect(
            lambda on, node=item: self.station_tree_toggled(node, on)
        )
        self.station_tree.setItemWidget(item, 1, control)
        self.station_tree_items.append(item)
        self.station_tree_switches[id(item)] = control

    def station_tree_toggled(self, item, on):
        station_ids = set(item.data(0, Qt.ItemDataRole.UserRole) or [])
        if on:
            self.station_exclusions.difference_update(station_ids)
            self.station_direct_visible.update(station_ids)
            self.set_flag("stations", True)
        else:
            self.station_exclusions.update(station_ids)
            self.station_direct_visible.difference_update(station_ids)
        self.sync_station_tree_switches()
        self.send_directory_filter()

    def sync_station_tree_switches(self):
        visible = self.effective_station_ids()
        for item in getattr(self, "station_tree_items", []):
            ids = set(item.data(0, Qt.ItemDataRole.UserRole) or [])
            active = len(ids & visible)
            control = self.station_tree_switches[id(item)]
            control.blockSignals(True)
            control.setChecked(active > 0)
            control.setMixed(0 < active < len(ids))
            control.blockSignals(False)

    def focus_station_tree_item(self, item, column):
        if column != 0:
            return
        station_ids = set(item.data(0, Qt.ItemDataRole.UserRole) or [])
        station = next(
            (self.station_lookup[value] for value in station_ids if value in self.station_lookup),
            None,
        )
        if station and station["coordinates"]:
            self.map.call("focus", *station["coordinates"], 15, station["name"])
            self.display_feature(
                {
                    "layer": "stations",
                    "properties": station.get("properties", station),
                    "geometry": {"type": "Point", "coordinates": station["coordinates"]},
                }
            )

    def select_metro_station_item(self, station_id):
        candidates = [
            item
            for item in getattr(self, "station_tree_items", [])
            if not item.childCount()
            and any(
                value == station_id
                or self.station_lookup.get(value, {}).get("physical_station_id") == station_id
                for value in set(item.data(0, Qt.ItemDataRole.UserRole) or [])
            )
        ]
        if not candidates:
            return False
        item = candidates[0]
        self.metro_catalog_tabs.setCurrentWidget(self.metro_station_page)
        ancestor = item.parent()
        while ancestor:
            ancestor.setExpanded(True)
            ancestor = ancestor.parent()
        self.station_tree.setCurrentItem(item)
        self.station_tree.scrollToItem(item)
        return True

    def metro_station_context_menu(self, position):
        from rail_catalog_ui import add_folder_move_menu

        item = self.station_tree.itemAt(position)
        if item is None:
            return
        if item not in self.station_tree.selectedItems():
            self.station_tree.clearSelection()
            self.station_tree.setCurrentItem(item)
        station_ids = set().union(*(
            set(value.data(0, Qt.ItemDataRole.UserRole) or [])
            for value in self.station_tree.selectedItems()
        ))
        if not station_ids:
            return
        menu = QMenu(self)
        edit = menu.addAction(
            "编辑名称与目录…",
            lambda: self.edit_metro_station_by_id(next(iter(station_ids))),
        )
        edit.setEnabled(len(station_ids) == 1)
        move = menu.addMenu("移动到")
        paths = {
            (province, city, line)
            for province, cities in self.station_directory.items()
            if province != "已归档"
            for city, lines in cities.items()
            for line in lines
        }
        add_folder_move_menu(
            move,
            paths,
            lambda path: self.move_metro_stations(station_ids, path),
        )
        visible = bool(station_ids & self.effective_station_ids())
        menu.addAction(
            "隐藏" if visible else "显示",
            lambda: self.toggle_metro_station_ids(station_ids, not visible),
        )
        archived = all(
            self.metro_station_overrides.values.get(value, {}).get("archived", False)
            for value in station_ids
        )
        menu.addAction(
            "取消归档 / 恢复" if archived else "归档",
            lambda: self.archive_metro_stations(station_ids, not archived),
        )
        menu.exec(self.station_tree.viewport().mapToGlobal(position))
        menu.deleteLater()

    def toggle_metro_station_ids(self, station_ids, on):
        if on:
            self.station_exclusions.difference_update(station_ids)
            self.station_direct_visible.update(station_ids)
            self.set_flag("stations", True)
        else:
            self.station_exclusions.update(station_ids)
            self.station_direct_visible.difference_update(station_ids)
        self.sync_station_tree_switches()
        self.send_directory_filter()

    def move_metro_stations(self, station_ids, path):
        if len(path) < 3:
            raise ValueError("地铁站目录需要省、市、线路三级位置")
        self.metro_station_overrides.update_many({
            value: {"folder_path": list(path)} for value in station_ids
        })
        self.populate_station_tree()
        self.send_directory_filter()

    def archive_metro_stations(self, station_ids, archived=True):
        self.metro_station_overrides.update_many({
            value: {"archived": bool(archived)} for value in station_ids
        })
        self.populate_station_tree()
        self.send_directory_filter()

    def edit_metro_station_by_id(self, station_id):
        record = self.station_lookup.get(station_id)
        if not record:
            return
        self.edit_selected_metadata({
            "layer": "stations",
            "properties": {
                **record.get("properties", {}),
                "catalog_id": station_id,
                "infrastructure_id": record["physical_station_id"],
                "display_name": record["name"],
            },
            "geometry": {"type": "Point", "coordinates": record["coordinates"]},
        })

    def populate_tree(self):
        groups = {}
        for route in self.hierarchy.routes:
            relation = route["osm_relation_id"]
            province, city, label = self.hierarchy.parent(route)
            custom = self.metro_line_overrides.values.get(str(relation), {})
            label = custom.get("display_name") or label
            if custom.get("archived", False):
                province, city = "已归档", province + " / " + city
                self.visible_lines.discard(relation)
            groups.setdefault(province, {}).setdefault(city, {}).setdefault(
                label, []
            ).append(route)
        self.tree.clear()
        self.tree_switches.clear()
        self.tree_items = []
        self.leaf_count = 0
        for province, cities in sorted(groups.items()):
            province_item = QTreeWidgetItem([province, ""])
            self.tree.addTopLevelItem(province_item)
            for city, lines in sorted(cities.items()):
                city_item = QTreeWidgetItem([f"{city}  ·  {len(lines)} 线", ""])
                province_item.addChild(city_item)
                for title, routes in sorted(
                    lines.items(),
                    key=lambda item: label_order(item[0]),
                ):
                    leaf = QTreeWidgetItem([title, ""])
                    swatch = QPixmap(12, 12)
                    swatch.fill(QColor(routes[0].get("display_color") or "#718096"))
                    leaf.setIcon(0, QIcon(swatch))
                    city_item.addChild(leaf)
                    leaf.setToolTip(0, "\n".join(route["name"] for route in routes))
                    self.install_tree_switch(
                        leaf, {route["osm_relation_id"] for route in routes}
                    )
                    self.leaf_count += 1
                ids = set().union(
                    *(
                        self.item_ids(city_item.child(i))
                        for i in range(city_item.childCount())
                    )
                )
                self.install_tree_switch(city_item, ids)
                city_item.setExpanded(False)
            ids = set().union(
                *(
                    self.item_ids(province_item.child(i))
                    for i in range(province_item.childCount())
                )
            )
            self.install_tree_switch(province_item, ids)

    def item_ids(self, item):
        return set(item.data(0, Qt.ItemDataRole.UserRole) or [])

    def install_tree_switch(self, item, ids):
        item.setData(0, Qt.ItemDataRole.UserRole, sorted(ids))
        control = Switch(bool(ids & self.visible_lines))
        control.setMixed(
            bool(ids & self.visible_lines) and not ids <= self.visible_lines
        )
        control.setAccessibleName(item.text(0) + " 显示开关")
        control.toggled.connect(lambda on, node=item: self.tree_toggled(node, on))
        self.tree.setItemWidget(item, 1, control)
        self.tree_switches[id(item)] = control
        self.tree_items.append(item)

    def tree_toggled(self, item, on):
        ids = self.item_ids(item)
        self.visible_lines.update(ids) if on else self.visible_lines.difference_update(
            ids
        )
        if on:
            if any(i > 0 for i in ids):
                self.set_flag("metro", True)
            if any(i < 0 for i in ids):
                self.set_flag("construction", True)
        self.sync_tree_switches()
        self.send_directory_filter()
        self.update_count()

    def sync_tree_switches(self):
        for item in self.tree_items:
            ids = self.item_ids(item)
            active = len(ids & self.visible_lines)
            control = self.tree_switches[id(item)]
            control.blockSignals(True)
            control.setChecked(active > 0)
            control.setMixed(0 < active < len(ids))
            control.blockSignals(False)

    def filter_tree(self, text):
        query = text.strip().lower()
        self.tree.set_filter_active(bool(query))
        self.station_tree.set_filter_active(bool(query))

        def match(item, inherited=False):
            own = inherited or query in item.text(0).lower()
            children = [match(item.child(i), own) for i in range(item.childCount())]
            found = own or any(children)
            item.setHidden(not found)
            if query and found and item.childCount():
                item.setExpanded(True)
            return found

        for index in range(self.tree.topLevelItemCount()):
            match(self.tree.topLevelItem(index))
        self.tree.schedule_height()
        for index in range(self.station_tree.topLevelItemCount()):
            match(self.station_tree.topLevelItem(index))
        self.station_tree.schedule_height()

    def focus_tree_item(self, item, _column):
        ids = self.item_ids(item)
        if 199200 in ids:
            self.map.call("focusDemo")
        else:
            point = next(
                (
                    self.route_centers[relation]
                    for relation in ids
                    if relation in self.route_centers
                ),
                None,
            )
            if point:
                self.map.call("focus", *point, 11)
        routes = [self.route_lookup[relation] for relation in ids if relation in self.route_lookup]
        if len(routes) == 1:
            self.display_feature({"layer": "metro", "properties": routes[0]})
        elif routes:
            self.display_feature(
                {
                    "layer": "metro",
                    "properties": {
                        "name": item.text(0).split(" · ", 1)[0],
                        "kind": "目录分组",
                        "line_count": len(routes),
                        "line_names": [route.get("name", "") for route in routes],
                    },
                }
            )

    def select_metro_tree_item(self, relation_ids):
        relation_ids = {int(value) for value in relation_ids if value is not None}
        candidates = [
            item
            for item in self.tree_items
            if not item.childCount() and relation_ids & self.item_ids(item)
        ]
        if not candidates:
            return False
        item = candidates[0]
        self.metro_catalog_tabs.setCurrentWidget(self.metro_line_page)
        if self.line_search.text():
            self.line_search.clear()
        ancestor = item.parent()
        while ancestor:
            ancestor.setExpanded(True)
            ancestor = ancestor.parent()
        self.tree.setCurrentItem(item)
        self.tree.scrollToItem(item)
        return True

    def update_count(self):
        self.line_count.setText(
            f"{self.leaf_count} 条线路 / 工程 · {sum(r > 0 for r in self.visible_lines)} 个线路关系可见"
        )

    def send_directory_filter(self):
        self.map.call("setLines", sorted(r for r in self.visible_lines if r > 0))
        self.map.call(
            "setConstruction", sorted(-r for r in self.visible_lines if r < 0)
        )
        self.map.call("setMetroStations", sorted(self.effective_station_ids()))
        self.sync_station_tree_switches()

    def reference_controls(self, kind):
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 0, 8, 8)
        layout.addWidget(
            self.new_switch(kind, "道路参考" if kind == "road" else "高铁轨道")
        )
        if kind == "rail":
            path = ROOT / "data/raw/osm/beijing_highspeed.geojson"
            count = len(read_json(path, EMPTY)["features"])
            layout.addWidget(
                text_label(f"北京周边 OSM 数据 · {count} 个轨道要素", wrap=True)
            )
        else:
            count = len(self.config["sources"]["road"]["features"])
            layout.addWidget(
                text_label(f"本地参考图层 · {count} 个道路要素", wrap=True)
            )
        layout.addWidget(
            text_label("可通过「文件 → 导入」添加其他区域数据。", wrap=True)
        )
        return body

    def run_controls(self):
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        self.run_mode = QComboBox()
        self.run_mode.addItems(["地铁 · 交路 / 车辆循环", "国铁 · 车次 / 跨线径路"])
        layout.addWidget(self.run_mode)
        self.run_pages = QStackedWidget()
        self.run_pages.addWidget(self.operations.sidebar())
        self.rail_run_split = QSplitter(Qt.Orientation.Vertical)
        rail_sidebar = self.rail_operations.sidebar()
        self.rail_operations.vehicle_tree.parentWidget().hide()
        self.rail_run_split.addWidget(rail_sidebar)
        self.corridor_panel = CorridorPanel(self.rail_operations)
        self.corridor_panel.selected.connect(self.show_corridor)
        self.rail_run_split.addWidget(self.corridor_panel)
        self.rail_run_split.setSizes([430, 500])
        self.run_pages.addWidget(self.rail_run_split)
        layout.addWidget(self.run_pages)
        self.run_mode.currentIndexChanged.connect(self.change_run_mode)
        return body

    def change_run_mode(self, index):
        self.operations.pause()
        self.rail_operations.pause()
        self.run_pages.setCurrentIndex(index)
        self.map.call("setRunSystem", "rail" if index == 1 else "metro")
        self.map.call(
            "setVisibility",
            "vehicles",
            index == 0 and self.operations.vehicle_switch.isChecked(),
        )
        self.map.call(
            "setVisibility",
            "railVehicles",
            index == 1 and self.rail_operations.vehicle_switch.isChecked(),
        )
        self.map.call(
            "setVisibility",
            "railPlan",
            index == 1 and self.rail_operations.route_switch.isChecked(),
        )
        self.operations.setVisible(self.side_pages.currentIndex() == 1 and index == 0)
        self.rail_operations.setVisible(
            self.side_pages.currentIndex() == 1 and index == 1
        )
        for i, host in enumerate(self.editor_hosts):
            host.setVisible(self.side_pages.currentIndex() == 1 and index == i)
        if self.side_pages.currentIndex() == 1:
            self.resize_run_editor(index)

    def resize_run_editor(self, index, expanded=False):
        self.map_stack.setSizes(editor_sizes(self.map_stack.height(), index, expanded))
        if index == 1:
            QTimer.singleShot(
                0,
                self.rail_operations.refresh_diagram,
            )

            def refocus_rail():
                if (
                    self.run_mode.currentIndex() == 1
                    and self.rail_operations.isVisible()
                    and self.rail_operations.route_switch.isChecked()
                ):
                    self.rail_operations.locate_current_line()

            QTimer.singleShot(150, refocus_rail)

    def open_rail_operations(self):
        self.open_sidebar(1)
        self.run_mode.setCurrentIndex(1)
        self.change_run_mode(1)

    def open_metro_operations(self):
        self.open_sidebar(1)
        self.run_mode.setCurrentIndex(0)
        self.change_run_mode(0)

    def rail_controls(self):
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 0, 8, 6)
        layout.setSpacing(5)
        layout.addWidget(self.new_switch("rail", "铁路线"))
        for key, title in [
            ("railConstruction", "在建铁路"),
            ("railStations", "车站及线路所"),
        ]:
            layout.addWidget(self.new_switch(key, title))
        layout.addWidget(
            text_label("保留每股道的原始属性；几台几线未标注时不猜测。", wrap=True)
        )
        from rail_catalog_ui import RailCatalog

        self.rail_catalog_widget = RailCatalog(
            active_rail_directory(ROOT),
            ROOT / "data/user_settings/rail_catalog.json",
            self.map,
            regions=REGIONS,
        )
        layout.addWidget(self.rail_catalog_widget)
        names_path = self.rail_operations.path.parent / "rail_way_names.json"
        self.rail_catalog_widget.reload_names(names_path)
        self.rail_operations.names_changed.connect(
            lambda: self.rail_catalog_widget.reload_names(names_path)
        )
        self.rail_catalog_widget.line_names_changed.connect(
            self.rail_operations.save_line_names
        )
        self.rail_catalog_widget.metadata_changed.connect(
            self.rail_operations.invalidate_line_library
        )
        self.rail_catalog_widget.metadata_changed.connect(self.refresh_signal_boxes)
        self.rail_catalog_widget.station_edit_requested.connect(
            self.edit_rail_station_metadata
        )
        self.rail_catalog_widget.feature_activated.connect(self.display_feature)
        self.rail_catalog_widget.enabled_requested.connect(
            self.enable_rail_from_catalog
        )
        self.rail_catalog_widget.station_enabled_requested.connect(
            lambda _group: self.set_flag("railStations", True)
        )
        self.rail_catalog_widget.station_partial_changed.connect(
            self.set_rail_station_partial
        )
        return body

    def refresh_signal_boxes(self):
        if hasattr(self, "rail_catalog_widget"):
            self.map.call(
                "setRailSignalBoxes", self.rail_catalog_widget.signal_box_geojson()
            )

    def enable_rail_from_catalog(self):
        self.flags["rail"] = True
        control = self.switches.get("rail")
        if control and not control.isChecked():
            control.blockSignals(True)
            control.setChecked(True)
            control.blockSignals(False)
        self.map.call("setVisibility", "rail", True)

    def set_rail_station_partial(self, group, on):
        key = "railStations"
        control = self.switches.get(key)
        self.flags[key] = bool(on)
        if control:
            control.blockSignals(True)
            control.setChecked(bool(on))
            control.setMixed(bool(on))
            control.blockSignals(False)
        self.map.call("setVisibility", key, bool(on))

    def open_rail_download(self):
        self.open_data_download()
        if not self.data_download.worker or not self.data_download.worker.isRunning():
            self.data_download.kind.setCurrentIndex(1)

    def inspector(self):
        panel = frame("panel")
        panel.setMinimumWidth(275)
        panel.setMaximumWidth(450)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(14)
        self.panel_top(layout, "对象详情", "点击地图要素查看属性", self.toggle_right)
        self.selected_title = text_label("选择地图对象", "selectedTitle", True)
        layout.addWidget(self.selected_title)
        self.selected_type = text_label("未选择", "badge")
        self.selected_type.setMaximumWidth(200)
        layout.addWidget(self.selected_type)
        self.detail_tabs = QTabWidget()
        self.properties = QTableWidget(0, 2)
        self.properties.setHorizontalHeaderLabels(["属性", "值"])
        self.properties.horizontalHeader().hide()
        self.properties.verticalHeader().hide()
        self.properties.setShowGrid(False)
        self.properties.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.properties.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.properties.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.properties.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.raw = QPlainTextEdit()
        self.raw.setReadOnly(True)
        self.detail_tabs.addTab(self.properties, "概览")
        self.detail_tabs.addTab(self.raw, "全部属性")
        layout.addWidget(self.detail_tabs, 1)
        copy = QPushButton("复制对象属性")
        copy.clicked.connect(
            lambda: QApplication.clipboard().setText(self.raw.toPlainText())
        )
        edit_metadata = QPushButton("编辑对象信息")
        edit_metadata.clicked.connect(self.edit_selected_metadata)
        self.edit_metadata_button = edit_metadata
        layout.addWidget(edit_metadata)
        layout.addWidget(copy)
        source_card = frame("card")
        source_layout = QVBoxLayout(source_card)
        source_layout.setContentsMargins(12, 11, 12, 11)
        source_layout.addWidget(text_label("数据与版权", "sectionLabel"))
        source_layout.addWidget(
            text_label(
                "© OpenStreetMap contributors\n站区面只来自 OSM 实际多边形。", wrap=True
            )
        )
        layout.addWidget(source_card)
        return panel

    def build_status(self):
        status = QStatusBar()
        self.setStatusBar(status)
        self.load_status = text_label("  正在载入地图…")
        status.addWidget(self.load_status, 1)
        self.camera_status = text_label("全国路网 · WGS84")
        status.addPermanentWidget(self.camera_status)
        status.addPermanentWidget(
            text_label(
                f"  {len(self.catalog)} 个线路关系  ·  {self.manifest.get('metro_station_features_written', 0):,} 个站点  "
            )
        )

    def connect_map(self):
        bridge = self.map.bridge
        bridge.selected.connect(self.select_map_feature)
        bridge.selections.connect(self.map_selection_changed)
        bridge.dataLoaded.connect(
            lambda: self.load_status.setText(
                "  本地路网已载入 · 地图就绪"
                if self.catalog
                else "  地图就绪 · 尚未下载地铁数据，请使用「数据源 → 自动下载全国地铁」"
            )
        )
        bridge.state.connect(self.demo_state)
        bridge.camera.connect(
            lambda lon, lat, zoom: self.camera_status.setText(
                f"{lon:.3f} E  /  {lat:.3f} N  ·  Z {zoom:.1f}"
            )
        )
        bridge.base.connect(self.base_changed)
        bridge.error.connect(
            lambda error: self.load_status.setText("  地图载入失败：" + error)
        )
        bridge.notice.connect(lambda text: self.load_status.setText("  " + text))

    def restore_map_state(self):
        """Replay bounded Python-owned UI state after a renderer restart."""
        self.map.call(
            "setBase", ("standard", "satellite", "admin")[self.base_combo.currentIndex()]
        )
        for key, control in self.base_switches.items():
            self.map.call("setBaseDetail", key, control.isChecked())
        for key, action in self.overlay_actions.items():
            self.map.call("setOverlay", key, action.isChecked())
        self.send_directory_filter()
        self.rail_catalog_widget.send_visibility(False)
        self.refresh_signal_boxes()
        for key, enabled in self.flags.items():
            self.map.call("setVisibility", key, enabled)
        self.map.call("imported", self.imported)

    def map_selection_changed(self, data):
        try:
            values = json.loads(data) if isinstance(data, str) else data
        except (TypeError, ValueError):
            values = []
        self.selected_features = values if isinstance(values, list) else []
        if len(self.selected_features) > 1:
            self.load_status.setText(f"  地图已选择 {len(self.selected_features)} 个对象，可使用编辑或工具菜单批量处理")

    def set_map_selection_mode(self, mode):
        self.map.call("setSelectionMode", mode)
        self.load_status.setText(
            "  框选工具已启用：在地图上拖出矩形，Ctrl 可追加选择"
            if mode == "box"
            else "  单击选择工具已启用：Ctrl + 单击可多选"
        )

    def selected_switch_ids(self):
        return sorted({
            int(feature.get("properties", {}).get("osm_node_id"))
            for feature in self.selected_features
            if feature.get("properties", {}).get("kind") == "switch"
            and str(feature.get("properties", {}).get("osm_node_id", "")).isdigit()
        })

    def create_signal_box_from_selection(self):
        switch_ids = self.selected_switch_ids()
        try:
            candidates = self.rail_catalog_widget.signal_box_line_candidates(switch_ids)
        except (ValueError, OSError, sqlite3.Error) as error:
            QMessageBox.information(self, "不能新建线路所", str(error))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("由所选道岔新建线路所")
        dialog.resize(620, 440)
        layout = QVBoxLayout(dialog)
        layout.addWidget(text_label(f"已选择 {len(switch_ids)} 个真实道岔节点", "sectionLabel"))
        name = QLineEdit()
        name.setPlaceholderText("输入唯一、可读的线路所名称")
        layout.addWidget(name)
        table = QTableWidget(len(candidates), 2)
        table.setHorizontalHeaderLabels(["经过线路", "稳定编号"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        for row, candidate in enumerate(candidates):
            item = QTableWidgetItem(candidate["name"])
            item.setData(Qt.ItemDataRole.UserRole, candidate["line_id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            table.setItem(row, 0, item)
            table.setItem(row, 1, QTableWidgetItem(candidate["line_id"]))
        layout.addWidget(table)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("创建线路所")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        line_ids = [
            table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            for row in range(table.rowCount())
            if table.item(row, 0).checkState() == Qt.CheckState.Checked
        ]
        try:
            ident = self.rail_catalog_widget.create_signal_box(
                name.text(), switch_ids, line_ids
            )
            self.set_flag("railStations", True)
            self.refresh_signal_boxes()
            self.open_sidebar(0)
            self.load_status.setText(f"  已创建线路所 {name.text().strip()} · {ident}")
        except (ValueError, OSError, sqlite3.Error) as error:
            QMessageBox.warning(self, "线路所未创建", str(error))

    def _selected_catalog_objects(self):
        rail_lines, rail_stations, metro_lines, metro_stations = set(), set(), set(), set()
        for feature in self.selected_features:
            props, layer = feature.get("properties", {}), feature.get("layer", "")
            group = props.get("catalog_group_id")
            if group in self.rail_catalog_widget.catalog:
                rail_lines.add(group)
            if layer in ("rail-points", "rail-detail-points"):
                node = props.get("osm_node_id")
                if node is not None:
                    key = self.rail_catalog_widget.station_owner_for_node(node)
                    if key:
                        rail_stations.add(key)
            if layer.startswith("rail-signal-box") and props.get("infrastructure_id"):
                rail_stations.add(str(props["infrastructure_id"]))
            relation = props.get("route_relation_id")
            if relation in self.route_lookup:
                metro_lines.add(int(relation))
            physical = props.get("infrastructure_id") or props.get("station_id")
            if physical:
                metro_stations.update(
                    key for key, record in self.station_lookup.items()
                    if record.get("physical_station_id") == str(physical)
                )
        return rail_lines, rail_stations, metro_lines, metro_stations

    def move_map_selection(self):
        from rail_catalog_ui import add_folder_move_menu

        rail_lines, rail_stations, metro_lines, metro_stations = self._selected_catalog_objects()
        kinds = sum(bool(value) for value in (rail_lines, rail_stations, metro_lines, metro_stations))
        if kinds != 1:
            QMessageBox.information(self, "请选择同类对象", "批量移动需要只选择铁路线、铁路站、地铁线或地铁站中的一类。")
            return
        menu = QMenu(self)
        if metro_lines:
            destinations = {
                self.hierarchy.parent(route)[:2]
                for route in self.hierarchy.routes
            }
            add_folder_move_menu(
                menu,
                destinations,
                lambda path: self.move_metro_lines(metro_lines, path),
            )
        elif rail_lines:
            add_folder_move_menu(
                menu,
                self.rail_catalog_widget.line_destination_paths(),
                lambda path: self.rail_catalog_widget.save_overrides({
                    key: {"folder_path": path} for key in rail_lines
                }),
            )
        elif rail_stations:
            add_folder_move_menu(
                menu,
                self.rail_catalog_widget.station_destination_paths(),
                lambda path: self.rail_catalog_widget.save_station_changes(
                    rail_stations, folder_path=path
                ),
            )
        else:
            paths = {
                (province, city, line)
                for province, cities in self.station_directory.items()
                if province != "已归档"
                for city, lines in cities.items()
                for line in lines
            }
            add_folder_move_menu(
                menu, paths, lambda path: self.move_metro_stations(metro_stations, path)
            )
        menu.exec(QCursor.pos())
        menu.deleteLater()

    def archive_map_selection(self):
        rail_lines, rail_stations, _metro_lines, metro_stations = self._selected_catalog_objects()
        if rail_lines:
            self.rail_catalog_widget.save_overrides({key: {"archived": True} for key in rail_lines})
        if rail_stations:
            self.rail_catalog_widget.save_station_changes(rail_stations, archived=True)
        if metro_stations:
            self.archive_metro_stations(metro_stations, True)
        if _metro_lines:
            self.archive_metro_lines(_metro_lines, True)

    def rename_map_selection(self):
        rail_lines, rail_stations, metro_lines, metro_stations = self._selected_catalog_objects()
        total = sum(map(len, (rail_lines, rail_stations, metro_lines, metro_stations)))
        if total == 1:
            self.edit_selected_metadata()
            return
        if total < 2:
            QMessageBox.information(self, "尚未选择对象", "请先在地图上单击、Ctrl 多选或框选对象。")
            return
        prefix, ok = QInputDialog.getText(self, "批量重命名", "为所选对象添加名称前缀")
        if not ok or not prefix.strip():
            return
        prefix = prefix.strip()
        if rail_lines:
            self.rail_catalog_widget.save_overrides({
                key: {"display_name": prefix + self.rail_catalog_widget.display_name(key)}
                for key in rail_lines
            })
        if rail_stations:
            self.rail_catalog_widget.prefix_station_names(rail_stations, prefix)
        if metro_stations:
            self.metro_station_overrides.update_many({
                key: {"display_name": prefix + self.station_lookup[key]["name"]}
                for key in metro_stations
            })
            self.populate_station_tree()
        if metro_lines:
            self.metro_line_overrides.update_many({
                str(key): {"display_name": prefix + self.hierarchy.parent(self.route_lookup[key])[2]}
                for key in metro_lines
            })
        self.map.call("setRailStyles", self.config["railStyles"])
        self.change_run_mode(self.run_mode.currentIndex())

    def base_changed(self, kind, vector):
        index = ("standard", "satellite", "admin").index(kind)
        self.base_combo.blockSignals(True)
        self.base_combo.setCurrentIndex(index)
        self.base_combo.blockSignals(False)
        enabled = vector and not kind.startswith("satellite")
        for control in self.base_switches.values():
            control.setEnabled(enabled)
        if kind == "satellite":
            self.base_hint.setText(
                "EOX Sentinel-2 · 10 米像素 · 按需加载 · 免费限非商业用途，保留署名；不是亚米级影像"
            )
        elif vector:
            self.base_hint.setText("矢量底图 · 道路、行政、文字和建筑可分别开关")
        else:
            self.base_hint.setText("OSM 栅格底图 · 矢量图源暂不可用")

    def show_corridor(self, corridor_id, train_id):
        self.run_mode.setCurrentIndex(1)
        self.change_run_mode(1)
        self.rail_operations.show_corridor(corridor_id, train_id)

    def open_sidebar(self, index):
        if index == 2:
            self.open_rail_operations()
            return
        self.left.show()
        self.side_pages.setCurrentIndex(index)
        self.map_rail.setChecked(index == 0)
        self.run_rail.setChecked(index == 1)
        self.side_title.setText(["图层控制", "运行控制", "国铁运行通道"][index])
        self.side_subtitle.setText(
            ["按要素与线路组织地图", "列车展示与车辆图层", "共享单向径路与股道衔接"][
                index
            ]
        )
        self.operations.setVisible(index == 1 and self.run_mode.currentIndex() == 0)
        self.rail_operations.setVisible(
            index == 1 and self.run_mode.currentIndex() == 1
        )
        for i, host in enumerate(self.editor_hosts):
            host.setVisible(index == 1 and self.run_mode.currentIndex() == i)
        if index == 1:
            self.resize_run_editor(self.run_mode.currentIndex())

    def open_or_toggle(self, index):
        if self.left.isVisible() and self.side_pages.currentIndex() == index:
            self.toggle_left()
        else:
            self.open_sidebar(index)

    def toggle_left(self):
        self.left.setVisible(not self.left.isVisible())
        self.map_rail.setChecked(
            self.left.isVisible() and self.side_pages.currentIndex() == 0
        )
        self.run_rail.setChecked(
            self.left.isVisible() and self.side_pages.currentIndex() == 1
        )

    def toggle_right(self):
        self.right.setVisible(not self.right.isVisible())
        self.detail_rail.setChecked(self.right.isVisible())

    def set_flag(self, key, on):
        self.flags[key] = on
        control = self.switches.get(key)
        if control and control.isChecked() != on:
            control.blockSignals(True)
            control.setChecked(on)
            control.blockSignals(False)
        self.map.call("setVisibility", key, on)
        if key == "metro":
            operating = {route["osm_relation_id"] for route in self.catalog}
            if on and not (self.visible_lines & operating):
                self.visible_lines.update(operating)
            elif not on:
                self.visible_lines.difference_update(operating)
            self.sync_tree_switches()
            self.send_directory_filter()
        elif key == "construction":
            projects = {route["osm_relation_id"] for route in self.construction_catalog}
            if on and not (self.visible_lines & projects):
                self.visible_lines.update(projects)
            elif not on:
                self.visible_lines.difference_update(projects)
            self.sync_tree_switches()
            self.send_directory_filter()
        elif key == "stations":
            self.sync_station_tree_switches()
            self.send_directory_filter()
        elif key in ("rail", "railConstruction"):
            self.rail_catalog_widget.set_line_master(
                "operating" if key == "rail" else "construction", on
            )
        elif key == "railStations":
            self.rail_catalog_widget.set_station_master("station", on)

    def set_all_lines(self, on):
        self.visible_lines = (
            {r["osm_relation_id"] for r in [*self.catalog, *self.construction_catalog]}
            if on
            else set()
        )
        # Keep the master switches and actual map layers consistent with this
        # legacy menu action.
        for key in ("metro", "construction"):
            self.flags[key] = bool(on)
            control = self.switches.get(key)
            if control:
                control.blockSignals(True)
                control.setChecked(bool(on))
                control.blockSignals(False)
            self.map.call("setVisibility", key, bool(on))
        self.sync_tree_switches()
        self.send_directory_filter()
        self.update_count()

    def select_map_feature(self, data):
        feature = json.loads(data) if isinstance(data, str) else data
        props = feature.get("properties", {})
        layer = feature.get("layer", "")
        if props.get("corridor_id") and layer in (
            "rail-vehicles",
            "rail-vehicle-symbols",
            "rail-vehicle-labels",
            "rail-plan-path",
            "rail-plan-stations",
            "rail-plan-labels",
        ):
            train_id = (
                props.get("trip_id") or props.get("train_id", "")
                if layer != "rail-plan-path"
                else ""
            )
            self.open_sidebar(2)
            self.corridor_panel.focus_item(props["corridor_id"], train_id)
            self.rail_operations.show_corridor(props["corridor_id"], train_id)
        elif layer in ("rail", "rail-stripes", "rail-construction") and props.get(
            "osm_way_id"
        ) is not None:
            self.open_sidebar(0)
            self.rail_catalog_widget.select_way(
                props["osm_way_id"],
                section_id=props.get("section_id"),
                group_id=props.get("catalog_group_id"),
            )
        elif layer in ("rail-points", "rail-detail-points") and props.get(
            "osm_node_id"
        ) is not None:
            self.open_sidebar(0)
            self.rail_catalog_widget.select_station(props["osm_node_id"])
        elif layer in ("rail-signal-box-fill", "rail-signal-box-outline", "rail-signal-box-symbol"):
            self.open_sidebar(0)
            self.rail_catalog_widget.select_station_record(
                str(props.get("infrastructure_id", ""))
            )
        elif layer in ("rail-platform-fill", "rail-platform-outline", "rail-station-fill", "rail-station-outline"):
            associated = props.get("associated_station_ids") or []
            if isinstance(associated, str):
                try:
                    associated = json.loads(associated)
                except ValueError:
                    associated = []
            if len(associated) == 1:
                self.open_sidebar(0)
                self.rail_catalog_widget.select_station(associated[0])
        else:
            relation_ids = props.get("route_relation_ids", [])
            if isinstance(relation_ids, str):
                try:
                    relation_ids = json.loads(relation_ids)
                except ValueError:
                    relation_ids = []
            relation_ids = list(relation_ids) if isinstance(relation_ids, list) else []
            relation = props.get("route_relation_id")
            if relation is not None:
                relation_ids.insert(0, relation)
            if relation_ids:
                self.open_sidebar(0)
                self.select_metro_tree_item(relation_ids)
            station_id = props.get("infrastructure_id") or props.get("station_id")
            if station_id:
                self.open_sidebar(0)
                self.select_metro_station_item(str(station_id))
        self.display_feature(feature)

    def display_feature(self, data):
        feature = json.loads(data) if isinstance(data, str) else data
        props = dict(feature.get("properties", {}))
        if props.get("corridor_id"):
            route = next(
                (
                    r
                    for r in (self.rail_operations.rail_payload or {}).get("routes", [])
                    if r["id"] == props["corridor_id"]
                ),
                None,
            )
            if route:
                props["corridor_name"] = route.get("name", route["id"])
                props["shared_trains"] = ", ".join(
                    t["id"]
                    for t in self.rail_operations.plan.trains
                    if self.rail_operations.plan.lines[t["line_id"]].get("corridor_id")
                    == route["id"]
                )
                props["track_changes"] = route.get("track_changes", [])
        relation = props.get("route_relation_id", props.get("osm_relation_id"))
        if relation in self.route_lookup:
            route = self.route_lookup[relation]
            props["route_relation_id"] = relation
            props["relation_tags"] = route.get("relation_tags", {})
            props["relation_members"] = route.get("members", [])
            for key in (
                "name",
                "network",
                "operator",
                "license",
                "attribution",
                "color_source",
                "color_raw",
            ):
                if key not in props:
                    props[key] = route.get(key)
            custom = self.metro_line_overrides.values.get(str(relation), {})
            props["display_name"] = custom.get("display_name") or self.hierarchy.parent(route)[2]
            props["folder_path"] = list(self.hierarchy.parent(route))
            props.update(
                source_line_attributes(
                    props, "metro", custom.get("technical_attributes", {})
                )
            )
        for key, value in props.items():
            if isinstance(value, str) and value[:1] in ("{", "["):
                try:
                    props[key] = json.loads(value)
                except ValueError:
                    pass
        rail_group = props.get("catalog_group_id")
        if rail_group in self.rail_catalog_widget.catalog:
            catalog_meta = self.rail_catalog_widget.meta(rail_group)
            props["display_name"] = self.rail_catalog_widget.display_name(rail_group)
            props["track_type"] = catalog_meta.get(
                "track_type", props.get("track_type", "未确认类型")
            )
            props["folder_path"] = list(self.rail_catalog_widget.parents(rail_group))
            props.update(
                source_line_attributes(
                    props,
                    "rail",
                    catalog_meta.get("technical_attributes", {}),
                )
            )
        merged_groups = props.get("merged_catalog_ids", [])
        if merged_groups:
            first = next(
                (
                    self.rail_catalog_widget.meta(key)
                    for key in merged_groups
                    if key in self.rail_catalog_widget.catalog
                ),
                {},
            )
            props.update(
                source_line_attributes(
                    {**first, **props},
                    "rail",
                    first.get("technical_attributes", {}),
                )
            )
        rail_station_layers = ("rail-points", "rail-detail-points", "rail-platform-fill", "rail-platform-outline", "rail-station-fill", "rail-station-outline")
        if feature.get("layer") in rail_station_layers:
            if "osm_node_id" not in props:
                associated = props.get("associated_station_ids") or []
                if isinstance(associated, str):
                    try:
                        associated = json.loads(associated)
                    except ValueError:
                        associated = []
                if len(associated) == 1:
                    props["osm_node_id"] = associated[0]
            props.setdefault(
                "station_type",
                station_type(props.get("node_tags", {}), props.get("kind", "")),
            )
            osm_node_id = props.get("osm_node_id")
            record = self.rail_catalog_widget.station_record_by_id.get(
                f"node/{osm_node_id}"
            )
            if record:
                if feature.get("layer") in ("rail-platform-fill", "rail-platform-outline", "rail-station-fill", "rail-station-outline"):
                    props["name"] = record["name"]
                    props["display_name"] = record["name"]
                station_custom = self.rail_catalog_widget.overrides.get("station:" + record["id"], {})
                folder = station_custom.get("folder_path")
                props["province"] = folder[0] if isinstance(folder, list) and folder else record["province"]
                props["city"] = (folder[1] if len(folder) > 1 else "") if isinstance(folder, list) and folder else record["city"]
                props["folder_path"] = list(folder) if isinstance(folder, list) and folder else [record["province"], record["city"]]
                props["line_ids"] = record["line_ids"]
                props["line_names"] = record["line_names"]
                props["station_overview"] = station_overview(
                    props,
                    record,
                    station_custom,
                )
        feature["properties"] = props
        self.selected_data = feature
        self._last_operating_detail = None
        if props.get("line_name") and props.get("from_name") and props.get("to_name"):
            title = f"{props['line_name']} · {props['from_name']}→{props['to_name']}"
        else:
            title = next(
                (
                    str(props[key])
                    for key in (
                        "display_name",
                        "source_name",
                        "name",
                        "line_name",
                        "vehicle_id",
                        "ref",
                    )
                    if props.get(key)
                ),
                "地图对象",
            )
        title = re.sub(
            r"\s*·\s*(?:RS|RL|IL|ST|NE|NN|SA|MS)-[A-Za-z0-9_-]+\s*$", "", title
        )
        layers = {
            "rail-points": "国铁车站 / 线路所 / 道岔",
            "rail-platform-fill": "国铁真实站台面",
            "rail-platform-outline": "国铁站台轮廓",
            "rail-construction": "在建国铁轨道",
            "rail-vehicles": "国铁车次",
            "rail-vehicle-symbols": "国铁车次",
            "rail-plan-path": "国铁参考径路（非实际联锁进路）",
            "rail-plan-stations": "国铁参考经停站",
            "metro": "地铁线路",
            "stations": "地铁站 POI",
            "areas-fill": "地铁站区多边形",
            "construction": "在建线路",
            "vehicles": "演示列车",
            "vehicles-symbol": "演示列车",
            "rail": "国铁物理轨道",
            "road": "道路参考",
        }
        self.selected_title.setText(title)
        self.selected_type.setText(
            layers.get(feature.get("layer"), feature.get("layer", "地图对象"))
        )
        rows = []
        translated = {
            "corridor_id": "单向运行通道编号",
            "corridor_name": "运行通道",
            "shared_trains": "共用通道的车次",
            "boundary_kind": "边界类型",
            "mode_source": "关联依据 / 复核状态",
            "retained_previous_snapshot": "保留旧快照（可能过时）",
            "ref": "线路编号",
            "network": "所属网络",
            "operator": "运营方",
            "source": "数据来源",
            "osm_way_id": "开放街图轨道编号",
            "osm_node_id": "开放街图节点编号",
            "route_relation_id": "开放街图关系编号",
            "state": "运行状态",
            "vehicle_id": "车辆编号",
            "distance_km": "已行驶里程（千米）",
            "speed_multiplier": "演示速度",
            "color_raw": "原始颜色",
            "color_source": "颜色来源",
            "infrastructure_id": "稳定基础设施编号",
            "station_id": "唯一车站编号",
            "area_id": "真实轮廓编号",
            "network_edge_id": "物理轨道段编号",
            "section_id": "端点线段编号",
            "catalog_group_id": "目录对象编号",
            "line_id": "物理线路编号",
            "from_node": "起端点编号",
            "from_name": "起端点",
            "to_node": "终端点编号",
            "to_name": "终端点",
            "track_type": "轨道类型",
            "kind": "对象种类",
            "station_type": "车站类型",
            "line_names": "接轨线路",
            "station_names": "途经站点",
            "connected_line_names": "联络 / 相接线路",
            "verification_status": "核验状态",
            "confidence": "置信度",
            "association_source": "关联依据",
            "geometry_source": "几何来源",
            "province": "省级行政区",
            "city": "城市",
            "line_count": "线路数量",
            "section_count": "端点线段数量",
            "edge_count": "物理轨道段数量",
            "merged_catalog_ids": "合并的目录对象编号",
            "folder_path": "目录位置",
            "line_ids": "接轨线路编号",
            "name": "名称",
            "display_name": "显示名称",
            "source_name": "原始名称",
            "line_name": "线路名称",
            **FIELD_LABELS,
        }
        for key in translated:
            if key in props and props[key] is not None:
                rows.append((translated[key], str(props[key])))
        for key, value in props.get("station_overview", {}).items():
            label = dict(STATION_OVERVIEW_FIELDS).get(key, key)
            if not any(existing == label for existing, _value in rows):
                rows.append((label, str(value)))
        tags = {**props.get("way_tags", {}), **props.get("relation_tags", {})}
        for key, title in [
            ("from", "起点"),
            ("to", "终点"),
            ("distance", "标注距离"),
            ("gauge", "轨距（毫米）"),
            ("voltage", "电压（伏）"),
            ("frequency", "频率（赫兹）"),
            ("electrified", "电气化"),
            ("maxspeed", "最高速度"),
            ("opening_date", "开通日期"),
            ("start_date", "启用日期"),
            ("tunnel", "隧道"),
            ("bridge", "桥梁"),
            ("railway:cbtc", "信号系统"),
            ("website", "官方网站"),
            ("wikipedia", "维基百科页面"),
            ("wikidata", "维基数据编号"),
        ]:
            if tags.get(key):
                rows.append((title, str(tags[key])))
        if props.get("relation_members"):
            rows.append(("开放街图关系成员数量", str(len(props["relation_members"]))))
        unknown = {}
        for key, value in props.items():
            if value is None or key in translated or key in ("way_tags", "relation_tags", "relation_members", "station_overview"):
                continue
            unknown[key] = value
        if unknown:
            rows.append(("其他原始属性", json.dumps(unknown, ensure_ascii=False, separators=(",", ":"))))
        if not rows:
            rows = [("对象信息", "暂无可显示的已翻译属性")]
        self.set_property_rows(rows)
        self.raw.setPlainText(json.dumps(feature, ensure_ascii=False, indent=2))
        self.right.show()
        self.detail_rail.setChecked(True)

    def edit_rail_station_metadata(self, station_id):
        record = self.rail_catalog_widget.station_record_by_id.get(station_id)
        if record is None:
            self.rail_catalog_widget.station_query = station_id.split("/", 1)[-1]
            self.rail_catalog_widget.populate_station_tree()
            record = self.rail_catalog_widget.station_record_by_id.get(station_id)
        if record is None:
            QMessageBox.information(self, "不可编辑", "车站目录中不存在该对象。")
            return
        props = {
            **record.get("properties", {}),
            "osm_node_id": record["osm_node_id"],
            "display_name": record["name"],
            "station_type": record["station_type"],
        }
        self.edit_selected_metadata({"layer": "rail-points", "properties": props})

    def edit_line_metadata(self, feature, kind, relation=None, rail_groups=None):
        props = feature.get("properties", {})
        rail_groups = [
            key
            for key in (rail_groups or [])
            if key in self.rail_catalog_widget.catalog
        ]
        if kind == "metro":
            route = self.route_lookup[relation]
            current_path = list(self.hierarchy.parent(route))
            custom = self.metro_line_overrides.values.get(str(relation), {})
            display_name = custom.get("display_name") or current_path[2]
            attributes = source_line_attributes(
                {**route, **props}, "metro", custom.get("technical_attributes", {})
            )
            dialog = LineMetadataDialog(
                "metro", display_name, current_path, attributes, parent=self
            )
        else:
            if not rail_groups:
                return
            primary = self.rail_catalog_widget.meta(rail_groups[0])
            current_path = list(self.rail_catalog_widget.parents(rail_groups[0]))
            display_name = props.get("display_name") or self.rail_catalog_widget.display_name(
                rail_groups[0]
            )
            attributes = source_line_attributes(
                {**primary, **props},
                "rail",
                primary.get("technical_attributes", {}),
            )
            dialog = LineMetadataDialog(
                "rail",
                display_name,
                current_path,
                attributes,
                TRACK_TYPES,
                primary.get("track_type", "未确认类型"),
                self,
            )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            value = dialog.values()
            if not value["display_name"]:
                raise ValueError("显示名称不能为空")
            required_levels = 3 if kind == "metro" else 1
            if len(value["folder_path"]) < required_levels:
                raise ValueError(
                    "地铁线路目录需要填写完整的省、市、线路三级路径"
                    if kind == "metro"
                    else "国铁线路目录至少需要一级文件夹"
                )
            if kind == "metro":
                self.hierarchy.set_parent(
                    {int(relation)},
                    value["folder_path"][0],
                    value["folder_path"][1],
                    value["folder_path"][2],
                )
                self.hierarchy.save()
                self.metro_line_overrides.update(
                    str(relation),
                    display_name=value["display_name"],
                    technical_attributes=value["technical_attributes"],
                )
                self.refresh_hierarchy({int(relation)})
            else:
                changed = {}
                if value["display_name"] != display_name:
                    changed["display_name"] = value["display_name"]
                if value["folder_path"] != list(current_path):
                    changed["folder_path"] = value["folder_path"]
                if value["track_type"] != primary.get("track_type", "未确认类型"):
                    changed["track_type"] = value["track_type"]
                if value["technical_attributes"] != attributes:
                    changed["technical_attributes"] = value["technical_attributes"]
                changes = {
                    key: dict(changed)
                    for key in rail_groups
                }
                if changed:
                    self.rail_catalog_widget.save_overrides(changes)
                if "display_name" in changed:
                    line_names = {
                        self.rail_catalog_widget.catalog[key].get("line_id"): value[
                            "display_name"
                        ]
                        for key in rail_groups
                        if self.rail_catalog_widget.catalog[key].get("line_id")
                    }
                    if line_names:
                        self.rail_catalog_widget.line_names_changed.emit(line_names)
            self.load_status.setText("  线路概览已保存到工作区；原始 OSM 数据未修改")
            self.display_feature(feature)
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "线路信息未保存", str(error))

    def edit_selected_metadata(self, feature=None):
        if not isinstance(feature, dict):
            feature = self.selected_data
        if not feature:
            return
        from PySide6.QtWidgets import QDialogButtonBox, QFormLayout

        props = feature.get("properties", {})
        layer = feature.get("layer", "")
        relation = props.get("route_relation_id", props.get("osm_relation_id"))
        if relation in self.route_lookup and layer == "metro":
            self.edit_line_metadata(feature, "metro", relation=relation)
            return
        merged_groups = props.get("merged_catalog_ids", [])
        rail_group = props.get("catalog_group_id")
        rail_groups = merged_groups or ([rail_group] if rail_group else [])
        if rail_groups and layer in ("rail", "rail-construction"):
            self.edit_line_metadata(feature, "rail", rail_groups=rail_groups)
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("编辑工作区名称与目录 · 原始 OSM 数据保留")
        form = QFormLayout(dialog)
        name = QLineEdit(
            str(props.get("display_name") or props.get("source_name") or props.get("name") or "")
        )
        province = QLineEdit()
        city = QLineEdit()
        folder = QLineEdit()
        station_kind = QComboBox()
        station_kind.addItems(STATION_TYPES)
        station_overview_controls = {}
        station_custom = None
        record = None
        form.addRow("显示名称", name)
        form.addRow("省级目录", province)
        form.addRow("市级目录", city)
        form.addRow("线路 / 分类目录", folder)
        station_id = str(
            props.get("catalog_id")
            or props.get("infrastructure_id")
            or props.get("station_id")
            or ""
        )
        rail_node = props.get("osm_node_id") if layer in ("rail-points", "rail-detail-points", "rail-platform-fill", "rail-platform-outline", "rail-station-fill", "rail-station-outline") else None
        rail_station_id = (
            str(props.get("infrastructure_id"))
            if layer in ("rail-signal-box-fill", "rail-signal-box-outline", "rail-signal-box-symbol")
            and props.get("infrastructure_id")
            else None
        )
        if relation in self.route_lookup:
            current = self.hierarchy.parent(self.route_lookup[relation])
            province.setText(current[0])
            city.setText(current[1])
            folder.setText(current[2])
        elif station_id in self.station_lookup:
            record = self.station_lookup[station_id]
            custom = self.metro_station_overrides.values.get(station_id, {})
            path = custom.get("folder_path")
            if isinstance(path, list) and len(path) >= 3:
                province.setText(path[0])
                city.setText(path[1])
                folder.setText(path[2])
            elif record["route_relation_ids"]:
                route = self.route_lookup.get(record["route_relation_ids"][0])
                if route:
                    current = self.hierarchy.parent(route)
                    province.setText(current[0])
                    city.setText(current[1])
                    folder.setText(current[2])
        elif rail_group in self.rail_catalog_widget.catalog:
            current = self.rail_catalog_widget.parents(rail_group)
            province.setText(current[0] if current else "全国铁路线")
            city.setText(current[1] if len(current) > 1 else "")
            folder.setText(current[2] if len(current) > 2 else "")
            rail_track_type = QComboBox()
            rail_track_type.addItems(TRACK_TYPES)
            rail_track_type.setCurrentText(
                self.rail_catalog_widget.meta(rail_group).get(
                    "track_type", "未确认类型"
                )
            )
            form.addRow("轨道类型", rail_track_type)
        elif rail_node is not None or rail_station_id is not None:
            if rail_node is not None:
                self.rail_catalog_widget.select_station(rail_node)
                record = next(
                    (r for r in self.rail_catalog_widget.station_records if r["osm_node_id"] == rail_node),
                    None,
                )
            else:
                record = self.rail_catalog_widget.station_record_by_id.get(rail_station_id)
            if record:
                province.setText(record["province"])
                city.setText(record["city"])
                station_kind.setCurrentText(record["station_type"])
                form.addRow("车站类型", station_kind)
                connection_selector = StationConnectionSelector(
                    self.rail_operations.line_library(),
                    "station:" + record["id"],
                )
                form.addRow("接轨线路", connection_selector)
                custom = self.rail_catalog_widget.overrides.get(
                    "station:" + record["id"], {}
                )
                overview = station_overview(props, record, custom)
                for key, label in STATION_OVERVIEW_FIELDS:
                    control = QLineEdit(overview.get(key, ""))
                    if key == "region":
                        control.setReadOnly(True)
                        control.setToolTip("所属地区随目录的前两级自动更新")
                    form.addRow(label, control)
                    station_overview_controls[key] = control
                station_custom = QPlainTextEdit()
                station_custom.setPlaceholderText("每行填写：属性名=属性值")
                station_custom.setPlainText(
                    "\n".join(
                        f"{key}={value}"
                        for key, value in custom.get("custom_attributes", {}).items()
                    )
                )
                station_custom.setMaximumHeight(100)
                form.addRow("自定义属性", station_custom)
                dialog.resize(800, 900)
        else:
            QMessageBox.information(self, "不可编辑", "此对象没有可编辑的工作区目录元数据。")
            return
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        def save():
            try:
                if relation in self.route_lookup:
                    self.hierarchy.set_parent({int(relation)}, province.text(), city.text(), folder.text())
                    self.hierarchy.save()
                    self.refresh_hierarchy({int(relation)})
                elif station_id in self.station_lookup:
                    self.metro_station_overrides.update(
                        station_id,
                        display_name=name.text().strip(),
                        folder_path=[province.text(), city.text(), folder.text()],
                    )
                    self.populate_station_tree()
                elif rail_group in self.rail_catalog_widget.catalog:
                    path = [value for value in (province.text(), city.text(), folder.text()) if value.strip()]
                    display_name = name.text().strip()
                    if not display_name:
                        raise ValueError("名称不能为空")
                    self.rail_catalog_widget.save_overrides(
                        {
                            rail_group: {
                                "display_name": display_name,
                                "folder_path": path,
                                "track_type": rail_track_type.currentText(),
                            }
                        }
                    )
                    line_id = self.rail_catalog_widget.catalog[rail_group].get("line_id")
                    if line_id:
                        self.rail_operations.save_line_names({line_id: display_name})
                elif (rail_node is not None or rail_station_id is not None) and record:
                    connections = connection_selector.connections()
                    custom_attributes = {}
                    for line in station_custom.toPlainText().splitlines():
                        if not line.strip():
                            continue
                        separator = "=" if "=" in line else "：" if "：" in line else None
                        if not separator:
                            raise ValueError("自定义属性每行应为“属性名=属性值”")
                        key, value = line.split(separator, 1)
                        custom_attributes[key.strip()] = value.strip()
                    self.rail_catalog_widget.save_station_override(
                        record["id"],
                        name.text(),
                        [
                            value.strip()
                            for value in (province.text(), city.text(), folder.text())
                            if value.strip()
                        ],
                        station_kind.currentText(),
                        connections,
                        {
                            key: control.text()
                            for key, control in station_overview_controls.items()
                        },
                        custom_attributes,
                    )
                dialog.accept()
                self.load_status.setText("  工作区目录已更新；原始 OSM 属性和稳定编号未修改")
                self.display_feature(feature)
            except (ValueError, OSError) as error:
                QMessageBox.warning(dialog, "目录修改未保存", str(error))

        buttons.accepted.connect(save)
        dialog.exec()

    def set_property_rows(self, rows):
        self.properties.setRowCount(len(rows))
        for row, (key, value) in enumerate(rows):
            left = QTableWidgetItem(key)
            left.setForeground(QColor("#536875"))
            self.properties.setItem(row, 0, left)
            right = QTableWidgetItem(value)
            right.setToolTip(value)
            self.properties.setItem(row, 1, right)
            self.properties.setRowHeight(row, 37)

    def _show_demo_details(self):
        if self.demo_error:
            self.display_feature(
                {
                    "layer": "数据状态",
                    "properties": {
                        "name": "需要加载线路数据",
                        "source": self.demo_error,
                    },
                }
            )
            return
        self.display_feature(
            {
                "layer": "vehicles",
                "properties": {
                    "name": "上海 1 号线 · DEMO-1-01",
                    "vehicle_id": "DEMO-1-01",
                    "ref": "1",
                    "operator": "上海地铁",
                    "state": "计划仿真",
                    "source": "用户可编辑演示计划，非官方时刻表",
                    "distance_km": "0.00",
                    "route_relation_id": 199200,
                },
            }
        )

    def refresh_operating_selection(self):
        if (
            self.selected_data.get("layer")
            not in ("vehicles", "vehicles-symbol", "rail-vehicles", "rail-vehicle-symbols")
            or not self.right.isVisible()
        ):
            return
        vehicle_id = self.selected_data["properties"].get("vehicle_id")
        editor = (
            self.rail_operations
            if self.selected_data.get("layer") in ("rail-vehicles", "rail-vehicle-symbols")
            else self.operations
        )
        feature = next(
            (
                f
                for f in editor.current_vehicle_features
                if f["properties"]["vehicle_id"] == vehicle_id
            ),
            None,
        )
        if feature:
            properties = self.selected_data["properties"]
            properties.update(feature["properties"])
            signature = (
                vehicle_id,
                properties.get("distance_km"),
                properties.get("state"),
                properties.get("simulation_time"),
            )
            if signature == getattr(self, "_last_operating_detail", None):
                return
            self._last_operating_detail = signature
            for row in range(self.properties.rowCount()):
                title = self.properties.item(row, 0).text()
                if title == "已行驶里程（千米）":
                    self.properties.item(row, 1).setText(str(properties["distance_km"]))
                if title == "运行状态":
                    self.properties.item(row, 1).setText(properties["state"])
            if self.detail_tabs.currentIndex() == 1:
                raw = json.dumps(self.selected_data, ensure_ascii=False, indent=2)
                if self.raw.toPlainText() != raw:
                    self.raw.setPlainText(raw)
        elif not editor.enabled:
            self.selected_data = {}
            self.selected_title.setText("选择地图对象")
            self.selected_type.setText("运行展示已关闭")
            self.properties.setRowCount(0)
            self.raw.clear()

    def show_data_summary(self):
        self.display_feature(
            {
                "layer": "数据源概览",
                "properties": {
                    "name": "全国城市轨道数据",
                    "source": "OpenStreetMap · ODbL 1.0",
                },
            }
        )
        self.set_property_rows(
            [
                ("线路关系", f"{len(self.catalog):,}"),
                (
                    "轨道要素",
                    f"{self.manifest.get('line_member_features_written', 0):,}",
                ),
                (
                    "地铁站 POI",
                    f"{self.manifest.get('metro_station_features_written', 0):,}",
                ),
                (
                    "真实站区面",
                    f"{len(self.station_areas['features']):,}",
                ),
                (
                    "已关联线路的站区",
                    str(
                        sum(
                            bool(f["properties"].get("route_relation_ids"))
                            for f in self.station_areas["features"]
                        )
                    ),
                ),
                (
                    "站区缺失说明",
                    "仅显示 OSM 已绘制的实际多边形；未绘制的车站只显示 POI，不推测边界",
                ),
                (
                    "在建轨道",
                    str(
                        read_json(DATA / "china_metro_construction_manifest.json").get(
                            "construction_way_features_written", 0
                        )
                    ),
                ),
                ("来源许可", "ODbL 1.0"),
            ]
        )
        self.raw.setPlainText(json.dumps(self.manifest, ensure_ascii=False, indent=2))

    def open_data_download(self):
        if not hasattr(self, "data_download"):
            self.data_download = DataDownloadDialog(ROOT, self)
            self.data_download.reload_requested.connect(self.reload_imported_data)
        self.data_download.show()
        self.data_download.raise_()

    def reload_imported_data(self):
        if self.data_download.worker and self.data_download.worker.isRunning():
            return
        answer = QMessageBox.question(
            self,
            "载入地铁数据",
            "将重建当前地图工作区。请确认已保存运行计划，继续载入？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        global DATA
        DATA = active_directory(ROOT)
        window = Desk()
        QApplication.instance().workbench = window
        window.show()
        self.close()

    def show_topology(self):
        report = validate_topology(self.repo)
        self.display_feature(
            {
                "layer": "拓扑校验",
                "properties": {
                    "name": "基础设施拓扑校验",
                    "source": "本地演示基础设施",
                },
            }
        )
        self.set_property_rows([(key, str(value)) for key, value in report.items()])
        self.raw.setPlainText(json.dumps(report, ensure_ascii=False, indent=2))

    def demo_state(self, running):
        self.running = running
        if hasattr(self, "play_button"):
            self.play_button.setText("暂停演示" if running else "开始演示")

    def play_demo(self):
        self.operations.play()

    def pause_demo(self):
        self.operations.pause()

    def reset_demo(self):
        self.operations.reset()

    def search_map(self):
        query = self.search.text().strip()
        if not query:
            self.rail_catalog_widget.set_query("", self.search_type.currentText())
            self.line_search.clear()
            self.load_status.setText("  已清除目录筛选")
            return
        kind = self.search_type.currentText()
        for city, province, lon, lat in REGIONS if kind in ("全部", "城市") else []:
            if query in (city, city + "市"):
                self.map.call("focus", lon, lat, 11, city + " · 轨道交通")
                self.rail_catalog_widget.set_query(query, "铁路车站")
                self.line_search.setText(city)
                self.open_sidebar(0)
                return
        if kind in ("全部", "铁路线", "车站及线路所"):
            self.open_sidebar(0)
            self.rail_catalog_widget.set_query(query, kind)
            if kind != "全部":
                self.load_status.setText(f"  已在{kind}目录中筛选「{query}」")
                return
        if kind in ("全部", "地铁线路") and "上海" in query and "1" in query:
            self.map.call("focusDemo")
            self._show_demo_details()
            return
        result = next(
            (
                station for station in self.display_stations["features"]
                if query in station["properties"].get("name", "")
            ),
            None,
        ) if kind in ("全部", "地铁站") else None
        if result:
            self.map.call("focus", *result["geometry"]["coordinates"], 15)
            self.display_feature(
                {"layer": "stations", "properties": result["properties"]}
            )
            self.select_metro_station_item(result["properties"]["infrastructure_id"])
        else:
            self.open_sidebar(0)
            self.line_search.setText(query)
            self.load_status.setText(f"  已在目录中筛选「{query}」")

    def import_file(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, "导入 GeoJSON", str(ROOT / "data"), "GeoJSON (*.geojson *.json)"
        )
        if not filename:
            return
        try:
            payload = read_json(Path(filename))
            if payload.get("type") != "FeatureCollection" or not isinstance(
                payload.get("features"), list
            ):
                raise ValueError("需要一个 GeoJSON FeatureCollection")
            self.imported = {
                "type": "FeatureCollection",
                "features": [*self.imported["features"], *payload["features"]],
            }
            self.map.call("imported", self.imported)
            self.set_flag("imported", True)
            self.load_status.setText(
                f"  已导入 {len(payload['features']):,} 个要素 · {Path(filename).name}"
            )
            self.display_feature(
                {
                    "layer": "导入图层",
                    "properties": {
                        "name": Path(filename).name,
                        "source": filename,
                        "要素数量": len(payload["features"]),
                    },
                }
            )
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "无法导入", str(error))

    def clear_imported(self):
        self.imported = dict(EMPTY)
        self.map.call("imported", self.imported)
        self.load_status.setText("  已清除手动导入图层")

    def visible_features(self):
        features = [*self.imported["features"]] if self.flags["imported"] else []
        if self.flags["metro"]:
            features += [
                f
                for f in read_json(DATA / "china_metro_routes.geojson", EMPTY)[
                    "features"
                ]
                if f["properties"]["route_relation_id"] in self.visible_lines
            ]
        if self.flags["stations"]:
            features += [
                f
                for f in read_json(DATA / "china_metro_stations.geojson", EMPTY)[
                    "features"
                ]
                if any(
                    relation in self.visible_lines
                    for relation in f["properties"].get("route_relation_ids", [])
                )
            ]
            features += [
                f
                for f in self.station_areas["features"]
                if any(
                    r in self.visible_lines
                    for r in f["properties"].get("route_relation_ids", [])
                )
            ]
        if self.flags["construction"]:
            features += [
                f
                for f in self.construction["features"]
                if -f["properties"]["osm_way_id"] in self.visible_lines
            ]
        if self.flags["rail"]:
            features += read_json(
                ROOT / "data/raw/osm/beijing_highspeed.geojson", EMPTY
            )["features"]
        if self.flags["road"]:
            features += self.config["sources"]["road"]["features"]
        if self.flags["vehicles"]:
            features += [
                f
                for f in self.operations.current_vehicle_features
                if f["properties"]["route_relation_id"] in self.visible_lines
            ]
        return features

    def export_visible(self):
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "导出可见图层",
            str(ROOT / "data" / "RailScope-export.geojson"),
            "GeoJSON (*.geojson)",
        )
        if not filename:
            return
        try:
            features = self.visible_features()
            Path(filename).write_text(
                json.dumps(
                    {"type": "FeatureCollection", "features": features},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            self.load_status.setText(
                f"  已导出 {len(features):,} 个要素 · {Path(filename).name}"
            )
        except OSError as error:
            QMessageBox.warning(self, "无法导出", str(error))

    def closeEvent(self, event):
        if (
            hasattr(self, "data_download")
            and self.data_download.worker
            and self.data_download.worker.isRunning()
        ):
            self.open_data_download()
            QMessageBox.information(
                self,
                "数据任务仍在运行",
                "请先暂停下载并等待停止，或等待导入完成，再退出软件。可以收起下载工具继续浏览地图。",
            )
            event.ignore()
            return
        self.server.shutdown()
        self.server.server_close()
        super().closeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if (
            hasattr(self, "operations")
            and self.operations.isVisible()
            and self.width() < 1250
            and hasattr(self, "right")
        ):
            self.right.hide()
            self.detail_rail.setChecked(False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshot")
    parser.add_argument("--smoke-report")
    parser.add_argument("--verify-interactions", action="store_true")
    parser.add_argument("--verify-bases", action="store_true")
    parser.add_argument("--verify-hierarchy", action="store_true")
    parser.add_argument("--verify-hefei", action="store_true")
    parser.add_argument("--verify-rail", action="store_true")
    args = parser.parse_args()
    ensure_assets(ROOT)
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    window = Desk()
    window.show()
    if args.verify_hefei:

        def show_hefei():
            features = [
                f
                for f in window.construction["features"]
                if f["properties"]["line_name"] == "合肥轨道交通S1线"
            ]
            if not features:
                return
            coords = [p for f in features for p in f["geometry"]["coordinates"]]
            xs, ys = zip(*coords)
            window.map.call(
                "fit", [[min(xs), min(ys)], [max(xs), max(ys)]], "合肥 · S1 机场线"
            )
            picked = next(
                (f for f in features if f["properties"]["osm_way_id"] == 859388432),
                features[0],
            )
            window.display_feature(
                {"layer": "construction", "properties": picked["properties"]}
            )

        window.map.bridge.initialized.connect(show_hefei)
    if args.screenshot:
        window._capture_path = str(
            Path(args.screenshot).with_name(Path(args.screenshot).stem + ".map.png")
        )
        QTimer.singleShot(20000, lambda: window.map.call("captureMap"))

        def capture():
            def grab_workspace():
                # Never capture desktop pixels: other applications may cover this window.
                return window.grab()

            def complete(payload):
                state = json.loads(payload) if payload else None
                # Keep the complete workspace visible in the verification image.
                checks = {
                    "single_workspace": not hasattr(window, "pages"),
                    "sidebar_reopen": False,
                    "switch_has_thumb": isinstance(window.switches["metro"], Switch),
                    "continuous_demo_km": round(window.demo["length_m"] / 1000, 2),
                    "startup_simulation_disabled": not window.operations.enabled
                    and not window.operations.playing
                    and state
                    and state["activeVehicles"] == 0,
                    "startup_no_shanghai_card": state and state["titleHidden"],
                    "no_line1_toolbar_button": state and not state["hasLine1Button"],
                    "only_one_satellite_choice": window.base_combo.count() == 3,
                    "startup_base_map_only": state
                    and not any(state["visibility"].values())
                    and not window.visible_lines
                    and not window.rail_catalog_widget.visible,
                    "startup_directory_switches_off": not any(
                        s.isChecked() for s in window.tree_switches.values()
                    ),
                    "sidebar_no_horizontal_overflow": window.side_pages.widget(0)
                    .horizontalScrollBarPolicy()
                    == Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
                }
                screenshot_path = Path(args.screenshot)
                grab_workspace().save(
                    str(
                        screenshot_path.with_name(screenshot_path.stem + "-startup.png")
                    )
                )
                if args.verify_hefei:
                    window.set_all_lines(True)
                    window.set_flag("construction", True)
                    from repair_hefei_s1 import component_count

                    features = [
                        f
                        for f in window.construction["features"]
                        if f["properties"]["line_name"] == "合肥轨道交通S1线"
                    ]
                    ids = {f["properties"]["osm_way_id"] for f in features}
                    checks["hefei_s1_gap_ways_imported"] = {
                        1055692403,
                        1463181483,
                        1055692404,
                        859388432,
                    } <= ids
                    checks["hefei_s1_original_geometry_connected"] = (
                        component_count(features) == 1
                    )
                    checks["hefei_s1_directory_controls_all_segments"] = all(
                        -wid in window.visible_lines for wid in ids
                    )
                window.toggle_left()
                window.open_sidebar(0)
                window.toggle_right()
                window.toggle_right()
                checks["sidebar_reopen"] = (
                    window.left.isVisible() and window.right.isVisible()
                )

                def finish():
                    nonlocal state
                    if args.verify_rail and "g1_running_diagram" not in checks:
                        for key in list(window.flags):
                            window.set_flag(key, False)
                        editor = window.rail_operations
                        editor.load_g1_example()
                        window.open_rail_operations()
                        editor.show()
                        app.processEvents()
                        checks["g1_timetable_visible"] = (
                            editor.tabs.currentIndex() == 0
                            and editor.table.rowCount() == 7
                            and editor.table.viewport().height() > 160
                        )
                        checks["compact_running_workspace"] = (
                            editor.width() <= 1120
                            and editor.tabs.height() > editor.height() * 0.40
                        )
                        checks["layer_trees_no_inner_scrollbar"] = all(
                            t.verticalScrollBarPolicy()
                            == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
                            for t in (window.tree, window.rail_catalog_widget.tree)
                        )
                        checks["builtin_g1_no_loader"] = not hasattr(
                            editor, "g1_button"
                        )
                        checks["compact_tool_area_two_rows"] = (
                            editor.tabs.height() > editor.height() * 0.65
                        )
                        screenshot = Path(args.screenshot)
                        grab_workspace().save(
                            str(screenshot.with_name(screenshot.stem + "-g1-table.png"))
                        )
                        metro_clock = window.operations.clock
                        window.open_metro_operations()
                        checks["metro_and_rail_independent"] = (
                            window.operations.isVisible()
                            and not editor.isVisible()
                            and window.operations.plan.system == "metro"
                            and editor.plan.system == "rail"
                        )
                        window.open_rail_operations()
                        checks["switching_retains_g1_timetable"] = (
                            editor.table.rowCount() == 7
                            and window.operations.clock == metro_clock
                            and not window.operations.playing
                        )
                        checks["g1_running_diagram"] = (
                            editor.table.rowCount() == 7 and bool(editor.scene.items())
                        )
                        checks["g1_does_not_autoplay"] = (
                            not editor.enabled and not editor.playing
                        )
                        checks["rail_catalog_topology_mode"] = (
                            window.rail_catalog_widget.mode.currentText()
                            == "全国铁路业务分类 → 整条线路"
                        )
                        window.open_sidebar(2)
                        app.processEvents()
                        checks["corridor_navigation_replaces_location"] = (
                            window.side_pages.currentIndex() == 1
                            and window.run_mode.currentIndex() == 1
                            and window.corridor_panel.isVisible()
                        )
                        root = window.corridor_panel.tree.topLevelItem(0)
                        root.setExpanded(True)
                        window.corridor_panel.choose(root, 0)
                        window.corridor_panel.tree.fit_content()
                        app.processEvents()
                        root = window.corridor_panel.tree.topLevelItem(0)
                        checks["corridor_tree_last_row_not_clipped"] = (
                            window.corridor_panel.tree.visualItemRect(
                                root.child(0)
                            ).bottom()
                            < window.corridor_panel.tree.viewport().height()
                        )
                        checks["shared_corridor_catalog"] = (
                            len(editor.document()["routes"]) == 1
                            and root.child(0).text(0) == "G1"
                        )
                        table_document = editor.corridors_document(table=True)
                        checks["corridor_endpoint_line_notation"] = (
                            table_document["schema"] == "railscope.rail-corridors.v2"
                            and len(table_document["corridors"][0]["sequence"]) == 3
                            and "path" not in table_document["corridors"][0]
                        )

                        def inspect_corridor_table():
                            dialog = app.activeModalWidget()
                            table = dialog.findChild(QTableWidget) if dialog else None
                            checks["corridor_endpoint_table_editable"] = bool(
                                table
                                and table.columnCount() == 3
                                and table.rowCount() == 1
                                and table.cellWidget(0, 0).currentData() == 9560692742
                                and table.cellWidget(0, 2).currentData() == 3687619616
                            )
                            if dialog:
                                dialog.grab().save(
                                    str(
                                        screenshot.with_name(
                                            screenshot.stem + "-table.png"
                                        )
                                    )
                                )
                                dialog.reject()

                        editor.line_library(interactive=True)
                        QTimer.singleShot(800, inspect_corridor_table)
                        window.corridor_panel.edit_table(
                            table_document["corridors"][0]["id"]
                        )
                        grab_workspace().save(
                            str(
                                screenshot.with_name(screenshot.stem + "-corridors.png")
                            )
                        )
                        window.open_rail_operations()
                        editor.play()
                        checks["g1_vehicle_on_real_geometry"] = bool(
                            editor.current_vehicle_features
                        )
                        editor.pause()
                        editor.tabs.setCurrentIndex(1)
                        editor.expand_requested.emit()
                        # A national metro source can still be decoding in the
                        # renderer after the earlier interaction checks.  Map
                        # commands are bounded and ordered; allow that queue to
                        # reach the final rail state before inspecting it.
                        QTimer.singleShot(5000, finish)
                        return
                    if args.verify_hierarchy and "hierarchy_dialog_saved" not in checks:
                        exercise_hierarchy()
                        return
                    if args.verify_rail and "g1_entire_route_in_view" not in checks:

                        def inspect_rail_map(current):
                            nonlocal state
                            state = current or state
                            checks["g1_entire_route_in_view"] = bool(
                                current and current.get("railPlanInView")
                            )
                            checks["national_legend_and_vehicle_source"] = bool(
                                current
                                and current.get("legendSystem") == "国铁列车"
                                and current.get("railVehiclesCount") == 1
                                and not current.get("visibility", {}).get("vehicles")
                            )
                            finish()

                        window.map.page().runJavaScript(
                            "JSON.stringify(window.railscope.testState())",
                            lambda value: inspect_rail_map(
                                json.loads(value) if value else None
                            ),
                        )
                        return
                    grab_workspace().save(args.screenshot)
                    if args.verify_rail:
                        checks["g1_diagram_visible_in_workspace"] = (
                            window.rail_operations.isVisible()
                            and window.rail_operations.height() > 200
                        )
                        diagram = window.rail_operations.diagram
                        checks["g1_diagram_fills_editor"] = (
                            diagram.transform().m11() == 1
                            and window.rail_operations.scene.sceneRect().height()
                            >= diagram.viewport().height() - 5
                        )
                    if args.verify_rail:
                        screenshot = Path(args.screenshot)
                        window._capture_path = str(
                            screenshot.with_name(screenshot.stem + "-g1.map.png")
                        )
                        window.map.call("captureMap")
                    if args.smoke_report:
                        report = {
                            "checks": checks,
                            "map": state,
                            "javascript_errors": window.map.page().errors(),
                            "screenshot": args.screenshot,
                        }
                        Path(args.smoke_report).write_text(
                            json.dumps(report, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                    failed = (
                        not state
                        or not state.get("metroLoaded")
                        or state.get("errors")
                        or any(value is False for value in checks.values())
                        or window.map.page().errors()
                    )
                    QTimer.singleShot(500, lambda: app.exit(1 if failed else 0))

                def exercise_hierarchy():
                    original = window.hierarchy
                    snapshot = set(window.visible_lines)
                    kunming_ids = {7957933, 11645555}
                    checks["kunming_4_correctly_classified"] = all(
                        original.parent(original.lookup[rid])[:2] == ("云南省", "昆明")
                        for rid in kunming_ids
                    )
                    test_model = original.clone()
                    test_model.path = ROOT / "data/logs/hierarchy-smoke.json"
                    dialog = HierarchyDialog(test_model, window, kunming_ids)
                    dialog.show()

                    def edit_preview():
                        dialog.province.setCurrentText("云南省")
                        dialog.city.setCurrentText("昆明")
                        dialog.label.setText("4号线 · 自定义目录")
                        dialog.apply_button.click()
                        path = Path(args.screenshot)
                        dialog.grab().save(
                            str(path.with_name(path.stem + "-hierarchy.png"))
                        )
                        dialog.save_and_accept()
                        test_model.load()
                        checks["hierarchy_dialog_saved"] = all(
                            test_model.parent(test_model.lookup[rid])
                            == ("云南省", "昆明", "4号线 · 自定义目录")
                            for rid in kunming_ids
                        )
                        window.hierarchy = test_model
                        window.refresh_hierarchy(kunming_ids)
                        leaf = next(
                            i
                            for i in window.tree_items
                            if not i.childCount() and kunming_ids <= window.item_ids(i)
                        )
                        checks["hierarchy_main_tree_updates"] = (
                            leaf.text(0) == "4号线 · 自定义目录"
                            and leaf.parent().text(0).startswith("昆明")
                            and leaf.parent().parent().text(0) == "云南省"
                        )
                        checks["hierarchy_keeps_visibility"] = (
                            window.visible_lines == snapshot
                            and window.tree_switches[id(leaf)].isChecked()
                            == bool(snapshot & kunming_ids)
                        )
                        checks["hierarchy_keeps_osm_tags"] = (
                            original.lookup[7957933]["network"] == "昆明地铁"
                            and original.lookup[7957933]["operator"]
                            == "云南京建轨道交通投资建设有限公司"
                        )
                        window.hierarchy = original
                        window.refresh_hierarchy(kunming_ids)
                        dialog.deleteLater()
                        QTimer.singleShot(250, finish)

                    QTimer.singleShot(300, edit_preview)

                def exercise_bases():
                    kinds = iter(("satellite", "admin", "standard"))

                    def step():
                        kind = next(kinds, None)
                        if kind is None:
                            QTimer.singleShot(200, finish)
                            return
                        window.base_combo.setCurrentIndex(
                            ("standard", "satellite", "admin").index(kind)
                        )
                        attempts = [0]

                        def inspect_base(payload):
                            latest = json.loads(payload)
                            attempts[0] += 1
                            loaded = (
                                latest["currentBase"] == kind
                                and latest["metroLoaded"]
                                and latest["baseLoaded"]
                            )
                            if not loaded and attempts[0] < 12:
                                QTimer.singleShot(1000, query)
                                return
                            checks[kind + "_base_and_overlays_load"] = loaded
                            checks[kind + "_retains_line_visibility"] = latest[
                                "visibleLines"
                            ] == len(window.catalog)
                            state.update(latest)
                            path = Path(args.screenshot)
                            grab_workspace().save(
                                str(path.with_name(path.stem + "-" + kind + ".png"))
                            )
                            step()

                        def query():
                            window.map.page().runJavaScript(
                                "JSON.stringify(window.railscope.testState())",
                                inspect_base,
                            )

                        QTimer.singleShot(1000, query)

                    step()

                if (
                    not args.verify_interactions
                    or not state
                    or not state.get("metroLoaded")
                ):
                    QTimer.singleShot(200, finish)
                    return
                initial_distance = state["travelled"]
                window.set_all_lines(True)
                window.set_flag("metro", True)
                window.pause_demo()
                parent = next(
                    item for item in window.tree_items if item.text(0) == "上海市"
                )
                parent_ids = window.item_ids(parent)
                window.tree_switches[id(parent)].click()
                parent_visibility_ok = sum(
                    value > 0 for value in window.visible_lines
                ) == len(
                    {
                        route["osm_relation_id"]
                        for route in window.catalog
                    }
                    - {value for value in parent_ids if value > 0}
                )
                window.switches["metro"].click()
                window.base_switches["roads"].click()
                window.overlay_actions["title"].setChecked(True)
                for key in ("legend", "tools", "status", "scale"):
                    window.overlay_actions[key].setChecked(False)
                window.operations.marker_size.setValue(28)
                window.operations.marker_style.setCurrentIndex(2)

                def inspect_controls(payload):
                    controls = json.loads(payload)
                    checks["metro_switch_controls_map"] = (
                        controls["metroVisibility"] == "none"
                    )
                    checks["pause_controls_animation"] = not controls["running"]
                    checks["view_menu_controls_floaters"] = (
                        controls["overlays"]
                        == {
                            "title": True,
                            "legend": False,
                            "tools": False,
                            "status": False,
                            "scale": False,
                        }
                        and not controls["titleHidden"]
                    )
                    checks["adjustable_vehicle_size_and_style"] = (
                        controls["vehicleAppearance"] == {"size": 28, "style": "train"}
                        and controls["markerRadius"] == 14
                        and controls["trainIconVisibility"]
                        == ("visible" if controls["vehicleVisible"] else "none")
                    )
                    window.overlay_actions["title"].setChecked(False)
                    for key in ("legend", "tools", "status", "scale"):
                        window.overlay_actions[key].setChecked(True)
                    checks["province_controls_children"] = parent_visibility_ok
                    checks["line_switch_hides_stations_and_polygons"] = (
                        not controls["line1StationsAllowed"]
                        and not controls["line1AreasAllowed"]
                    )
                    checks["project_directory_controls_construction"] = controls[
                        "visibleConstruction"
                    ] == len(
                        {
                            -route["osm_relation_id"]
                            for route in window.construction_catalog
                        }
                        - {-value for value in parent_ids if value < 0}
                    )
                    checks["parent_thumb_synchronized"] = (
                        window.tree_switches[id(parent)].get_position() == 0
                    )
                    checks["base_road_switch"] = (
                        controls["roadBaseVisibility"] == "none"
                        if controls["vectorAvailable"]
                        else None
                    )
                    window.set_all_lines(True)
                    window.set_flag("metro", True)
                    if not window.base_switches["roads"].isChecked():
                        window.base_switches["roads"].click()
                    window.play_demo()

                    def inspect_movement(payload):
                        latest = json.loads(payload)
                        checks["vehicle_moves_after_resume"] = (
                            latest["travelled"] > initial_distance
                        )
                        state.update(latest)
                        grab_workspace().save(args.screenshot)
                        window.open_sidebar(1)
                        window.operations.locate_current_line()

                        def capture_run():
                            editor = window.operations
                            editor.pause()
                            train = editor.plan.train("DEMO-1-01")
                            original = train["stops"][0]["departure_s"]
                            editor.table.item(0, 5).setText(
                                __import__("operating").format_time(original + 15)
                            )
                            checks["table_edit_changes_plan"] = (
                                editor.plan.train("DEMO-1-01")["stops"][0][
                                    "departure_s"
                                ]
                                == original + 15
                            )
                            editor.undo()
                            checks["edit_undo"] = (
                                editor.plan.train("DEMO-1-01")["stops"][0][
                                    "departure_s"
                                ]
                                == original
                            )
                            editor.redo()
                            checks["edit_redo"] = (
                                editor.plan.train("DEMO-1-01")["stops"][0][
                                    "departure_s"
                                ]
                                == original + 15
                            )
                            editor.clock = 25250
                            editor.push_positions()
                            changed = editor.current_vehicle_features[0]["geometry"][
                                "coordinates"
                            ]
                            editor.undo()
                            editor.push_positions()
                            checks["edited_times_drive_vehicle_positions"] = (
                                changed
                                != editor.current_vehicle_features[0]["geometry"][
                                    "coordinates"
                                ]
                            )
                            checks["shanghai_all_imported_profiles_available"] = len(
                                editor.base_lines
                            ) == 21 and all(
                                line["path"] and len(line["stations"]) >= 2
                                for line in editor.base_lines
                            )
                            path = Path(args.screenshot)
                            grab_workspace().save(
                                str(path.with_name(path.stem + "-run.png"))
                            )
                            editor.tabs.setCurrentIndex(1)
                            editor.diagram.horizontalScrollBar().setValue(0)
                            editor.diagram.verticalScrollBar().setValue(0)
                            QTimer.singleShot(300, capture_diagram)

                        def capture_diagram():
                            window.operations.expand_requested.emit()
                            QTimer.singleShot(200, capture_expanded_diagram)

                        def capture_expanded_diagram():
                            path = Path(args.screenshot)
                            grab_workspace().save(
                                str(path.with_name(path.stem + "-diagram.png"))
                            )
                            window.operations.tabs.setCurrentIndex(0)
                            window.resize(1050, 720)
                            QTimer.singleShot(200, capture_small)

                        def capture_small():
                            path = Path(args.screenshot)
                            grab_workspace().save(
                                str(path.with_name(path.stem + "-compact.png"))
                            )
                            window.resize(1600, 980)
                            window.open_sidebar(0)
                            if not window.right.isVisible():
                                window.toggle_right()
                            window.operations.clock = 25200
                            window.operations.set_enabled(False)

                            def inspect_disabled(payload):
                                latest = json.loads(payload)
                                checks["disable_removes_vehicles"] = (
                                    not latest["running"]
                                    and latest["activeVehicles"] == 0
                                )
                                state.update(latest)
                                (exercise_bases if args.verify_bases else finish)()

                            QTimer.singleShot(
                                200,
                                lambda: window.map.page().runJavaScript(
                                    "JSON.stringify(window.railscope.testState())",
                                    inspect_disabled,
                                ),
                            )

                        QTimer.singleShot(200, capture_run)

                    QTimer.singleShot(
                        1500,
                        lambda: window.map.page().runJavaScript(
                            "JSON.stringify(window.railscope.testState())",
                            inspect_movement,
                        ),
                    )

                QTimer.singleShot(
                    500,
                    lambda: window.map.page().runJavaScript(
                        "JSON.stringify(window.railscope.testState())", inspect_controls
                    ),
                )

            window.map.page().runJavaScript(
                'window.railscope ? JSON.stringify(window.railscope.testState()) : ""',
                complete,
            )

        QTimer.singleShot(24000, capture)
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
