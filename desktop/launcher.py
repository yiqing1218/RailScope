"""RailScope single-window desktop GIS workbench."""

from __future__ import annotations

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from PySide6.QtCore import QObject, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QColor, QIcon, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
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
    QMainWindow,
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
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from components import Fold, Switch, THEME, switch_row, text_label
from geometry import build_demo_path
from hierarchy import Hierarchy, label_order
from hierarchy_ui import HierarchyDialog
from metro_data import associate_station_areas, build_shanghai_lines
from operating import Plan
from operating_ui import OperationsEditor
from railscope.demo import load_demo
from railscope.services.topology import validate_topology

EMPTY = {"type": "FeatureCollection", "features": []}
DATA = ROOT / "data" / "processed" / "osm"
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

    def permitted(self):
        path = unquote(urlsplit(self.path).path)
        allowed = (
            (
                path.startswith("/desktop/assets/")
                and Path(path).suffix in {".html", ".css", ".js"}
            )
            or path.startswith("/frontend/node_modules/maplibre-gl/dist/")
            or (path.startswith("/data/processed/osm/") and path.endswith(".geojson"))
            or path == "/data/raw/osm/beijing_highspeed.geojson"
        )
        return allowed and ".." not in path and "\\" not in path

    def do_HEAD(self):
        if not self.permitted():
            self.send_error(404)
            return
        super().do_HEAD()

    def do_GET(self):
        path = urlsplit(self.path).path
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
    initialized = Signal()
    dataLoaded = Signal()
    progress = Signal(float, float, bool)
    state = Signal(bool)
    camera = Signal(float, float, float)
    base = Signal(str, bool)
    error = Signal(str)

    @Slot(str)
    def featureSelected(self, data):
        self.selected.emit(data)

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

    def javaScriptConsoleMessage(self, level, message, line, source):
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            self.console_errors.append(message)


class MapView(QWebEngineView):
    def __init__(self, server):
        super().__init__()
        self.setPage(MapPage(self))
        self.bridge = Bridge(self)
        self.channel = QWebChannel(self.page())
        self.channel.registerObject("bridge", self.bridge)
        self.page().setWebChannel(self.channel)
        self.commands = []
        self.is_ready = False
        self.bridge.initialized.connect(self._ready)
        self.load(
            QUrl(f"http://127.0.0.1:{server.server_port}/desktop/assets/map.html")
        )

    def call(self, method, *args):
        code = (
            f"window.railscope.{method}("
            + ",".join(json.dumps(arg, ensure_ascii=False) for arg in args)
            + ")"
        )
        if self.is_ready:
            self.page().runJavaScript(code)
        else:
            self.commands.append(code)

    def _ready(self):
        self.is_ready = True
        for code in self.commands:
            self.page().runJavaScript(code)
        self.commands.clear()


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
        features = read_json(DATA / "china_metro_routes.geojson", EMPTY)["features"]
        stations = read_json(DATA / "china_metro_stations.geojson", EMPTY)["features"]
        self.station_areas = {
            "type": "FeatureCollection",
            "features": associate_station_areas(
                read_json(DATA / "china_metro_station_areas.geojson", EMPTY)[
                    "features"
                ],
                stations,
            ),
        }
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
        self.route_centers = {}
        self.route_bounds = {}
        for feature in features:
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
        self.visible_lines = {
            route["osm_relation_id"]
            for route in [*self.catalog, *self.construction_catalog]
        }
        self.flags = {
            key: True
            for key in (
                "metro",
                "stations",
                "construction",
                "rail",
                "road",
                "imported",
                "vehicles",
            )
        }
        self.switches = {}
        self.tree_switches = {}
        self.imported = dict(EMPTY)
        self.selected_data = {}
        self.running = bool(self.demo["coordinates"])
        self.distance = 0.0
        self._stations_cache = stations
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
        self.config = self.make_config()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), LocalHandler)
        self.server.config = self.config
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.map = MapView(self.server)
        self.operations = OperationsEditor(
            self.plan, self.map, self.shanghai_lines, self.plan_path
        )
        self.build()
        self.switches["vehicles"] = self.operations.vehicle_switch
        self.operations.vehicle_switch.toggled.connect(
            lambda on: self.set_flag("vehicles", on)
        )
        self.operations.updated.connect(self.refresh_operating_selection)
        self.connect_map()
        self._show_demo_details()
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

    def make_config(self):
        sources = {
            key: f"/data/processed/osm/{name}" if (DATA / name).exists() else EMPTY
            for key, name in {
                "metro": "china_metro_routes.geojson",
                "stations": "china_metro_stations.geojson",
                "areas": "china_metro_station_areas.geojson",
                "construction": "china_metro_construction.geojson",
            }.items()
        }
        rail = ROOT / "data" / "raw" / "osm" / "beijing_highspeed.geojson"
        sources["areas"] = self.station_areas
        sources["construction"] = self.construction
        sources["rail"] = (
            "/data/raw/osm/beijing_highspeed.geojson" if rail.exists() else EMPTY
        )
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
            "visibleIds": sorted(r for r in self.visible_lines if r > 0),
            "constructionIds": sorted(-r for r in self.visible_lines if r < 0),
            "demo": self.demo,
            "cities": [
                {"name": city, "center": [lon, lat]} for city, _, lon, lat in REGIONS
            ],
            "routeBounds": self.route_bounds,
            "lineViews": [
                {
                    "name": "上海 · " + line["name"],
                    "coordinates": line["path"]["coordinates"],
                }
                for line in self.shanghai_lines
                if line["path"]
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
        self.map_stack.addWidget(self.operations)
        self.map_stack.setSizes([350, 500])
        self.operations.expand_requested.connect(
            lambda: self.map_stack.setSizes(
                [200, max(340, self.map_stack.height() - 200)]
            )
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

    def build_menus(self):
        bar = self.menuBar()
        file = bar.addMenu("文件")
        self.add_action(file, "导入 GeoJSON 图层…", self.import_file, "Ctrl+I")
        self.add_action(file, "导出可见图层…", self.export_visible, "Ctrl+E")
        file.addSeparator()
        self.add_action(file, "退出", self.close, "Alt+F4")
        edit = bar.addMenu("编辑")
        self.add_action(edit, "目录层级设置…", self.edit_hierarchy)
        edit.addSeparator()
        self.add_action(edit, "显示全部地铁线路", lambda: self.set_all_lines(True))
        self.add_action(edit, "隐藏全部地铁线路", lambda: self.set_all_lines(False))
        self.add_action(edit, "清除导入图层", self.clear_imported)
        map_menu = bar.addMenu("地图")
        self.add_action(map_menu, "地图图层控制", lambda: self.open_sidebar(0))
        self.add_action(map_menu, "查看全国路网", lambda: self.map.call("focusChina"))
        self.add_action(map_menu, "定位上海 1 号线", lambda: self.map.call("focusDemo"))
        run = bar.addMenu("运行")
        self.add_action(run, "运行控制", lambda: self.open_sidebar(1))
        self.add_action(run, "开始计划仿真", self.play_demo)
        self.add_action(run, "暂停列车演示", self.pause_demo)
        self.add_action(run, "重新从起点运行", self.reset_demo)
        self.add_action(
            run,
            "打开可编辑运行表 / 运行图",
            lambda: (self.open_sidebar(1), self.operations.show()),
        )
        self.add_action(run, "保存运行计划", self.operations.save, "Ctrl+S")
        self.add_action(run, "导入运行计划…", self.operations.import_plan)
        self.add_action(run, "导出运行计划…", self.operations.export_plan)
        data = bar.addMenu("数据源")
        self.add_action(data, "查看数据概览", self.show_data_summary)
        self.add_action(data, "导入 GeoJSON 图层…", self.import_file)
        topology = bar.addMenu("拓扑")
        self.add_action(topology, "校验基础设施连通性", self.show_topology)
        view = bar.addMenu("视图")
        self.add_action(view, "展开 / 收起左侧栏", self.toggle_left)
        self.add_action(view, "展开 / 收起对象详情", self.toggle_right)
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
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索城市、线路或地铁站   Ctrl+F")
        self.search.setMinimumWidth(260)
        self.search.setMaximumWidth(345)
        self.search.returnPressed.connect(self.search_map)
        completer = QCompleter(
            [city for city, *_ in REGIONS] + ["上海 1 号线"], self.search
        )
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.search.setCompleter(completer)
        layout.addWidget(self.search)
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
        layout.addWidget(
            self.rail_button(
                "定位", "定位上海 1 号线", lambda: self.map.call("focusDemo"), False
            )
        )
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
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 5, 0)
        layout.setSpacing(10)
        layout.addWidget(Fold("底图", self.base_controls()))
        layout.addWidget(
            Fold("地铁", self.metro_controls(), count=f"{len(self.catalog)} 个关系")
        )
        layout.addWidget(Fold("公路", self.reference_controls("road"), expanded=False))
        layout.addWidget(Fold("高铁", self.reference_controls("rail"), expanded=False))
        layout.addStretch()
        scroll.setWidget(body)
        return scroll

    def base_controls(self):
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 0, 8, 6)
        layout.setSpacing(5)
        self.base_combo = QComboBox()
        self.base_combo.addItems(
            ["标准地图", "卫星影像 · 10 m", "行政区划", "卫星影像 · 轻量 NASA"]
        )
        self.base_combo.currentIndexChanged.connect(
            lambda index: self.map.call(
                "setBase", ("standard", "satellite", "admin", "satellite-lite")[index]
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
        layout.addWidget(text_label("线路目录", "sectionLabel"))
        self.line_search = QLineEdit()
        self.line_search.setPlaceholderText("筛选城市 / 线路")
        self.line_search.textChanged.connect(self.filter_tree)
        layout.addWidget(self.line_search)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(14)
        self.tree.setMinimumHeight(285)
        self.tree.setMaximumHeight(410)
        self.tree.setUniformRowHeights(True)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(1, 62)
        self.populate_tree()
        self.tree.itemDoubleClicked.connect(self.focus_tree_item)
        layout.addWidget(self.tree)
        self.line_count = text_label("")
        layout.addWidget(self.line_count)
        self.update_count()
        return body

    def route_city(self, route):
        return self.hierarchy.parent(route)[:2]

    def edit_hierarchy(self):
        selected = self.tree.currentItem()
        ids = self.item_ids(selected) if selected else set()
        dialog = HierarchyDialog(self.hierarchy, self, ids)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.hierarchy.overrides = dialog.model.overrides
        self.refresh_hierarchy(ids)
        self.load_status.setText("  目录层级已保存 · 原始 OSM 数据与显示开关保持不变")

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
        self.update_count()

    def populate_tree(self):
        groups = self.hierarchy.grouped()
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
                if city == "上海":
                    city_item.setExpanded(True)
                    province_item.setExpanded(True)
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
        control = Switch(True)
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

    def update_count(self):
        self.line_count.setText(
            f"{self.leaf_count} 条线路 / 工程 · {sum(r > 0 for r in self.visible_lines)} 个线路关系可见"
        )

    def send_directory_filter(self):
        self.map.call("setLines", sorted(r for r in self.visible_lines if r > 0))
        self.map.call(
            "setConstruction", sorted(-r for r in self.visible_lines if r < 0)
        )

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
        return self.operations.sidebar()

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
        self.camera_status = text_label("上海 · WGS84")
        status.addPermanentWidget(self.camera_status)
        status.addPermanentWidget(
            text_label(
                f"  {len(self.catalog)} 个线路关系  ·  {self.manifest.get('metro_station_features_written', 0):,} 个站点  "
            )
        )

    def connect_map(self):
        bridge = self.map.bridge
        bridge.selected.connect(self.display_feature)
        bridge.dataLoaded.connect(
            lambda: self.load_status.setText("  本地路网已载入 · 地图就绪")
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

    def base_changed(self, kind, vector):
        index = ("standard", "satellite", "admin", "satellite-lite").index(kind)
        self.base_combo.blockSignals(True)
        self.base_combo.setCurrentIndex(index)
        self.base_combo.blockSignals(False)
        enabled = vector and not kind.startswith("satellite")
        for control in self.base_switches.values():
            control.setEnabled(enabled)
        if kind == "satellite-lite":
            self.base_hint.setText("NASA 全球卫星影像 · 高层级使用原始影像放大")
        elif kind == "satellite":
            self.base_hint.setText(
                "EOX Sentinel-2 · 10 米像素 · 按需加载 · 免费限非商业用途，保留署名；不是亚米级影像"
            )
        elif vector:
            self.base_hint.setText("矢量底图 · 道路、行政、文字和建筑可分别开关")
        else:
            self.base_hint.setText("OSM 栅格底图 · 矢量图源暂不可用")

    def open_sidebar(self, index):
        self.left.show()
        self.side_pages.setCurrentIndex(index)
        self.map_rail.setChecked(index == 0)
        self.run_rail.setChecked(index == 1)
        self.side_title.setText("运行控制" if index else "图层控制")
        self.side_subtitle.setText(
            "列车展示与车辆图层" if index else "按要素与线路组织地图"
        )
        self.operations.setVisible(index == 1)

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

    def set_all_lines(self, on):
        self.visible_lines = (
            {r["osm_relation_id"] for r in [*self.catalog, *self.construction_catalog]}
            if on
            else set()
        )
        self.sync_tree_switches()
        self.send_directory_filter()
        self.update_count()

    def display_feature(self, data):
        feature = json.loads(data) if isinstance(data, str) else data
        props = dict(feature.get("properties", {}))
        relation = props.get("route_relation_id")
        if relation in self.route_lookup:
            route = self.route_lookup[relation]
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
        for key, value in props.items():
            if isinstance(value, str) and value[:1] in ("{", "["):
                try:
                    props[key] = json.loads(value)
                except ValueError:
                    pass
        feature["properties"] = props
        self.selected_data = feature
        title = next(
            (
                str(props[key])
                for key in ("name", "line_name", "vehicle_id", "ref", "id")
                if props.get(key)
            ),
            "地图对象",
        )
        layers = {
            "metro": "地铁线路",
            "stations": "地铁站 POI",
            "areas-fill": "地铁站区多边形",
            "construction": "在建线路",
            "vehicles": "演示列车",
            "rail": "高铁轨道",
            "road": "道路参考",
        }
        self.selected_title.setText(title)
        self.selected_type.setText(
            layers.get(feature.get("layer"), feature.get("layer", "地图对象"))
        )
        rows = []
        translated = {
            "ref": "线路编号",
            "network": "所属网络",
            "operator": "运营方",
            "source": "数据来源",
            "osm_way_id": "OSM Way",
            "osm_node_id": "OSM Node",
            "route_relation_id": "OSM Relation",
            "state": "运行状态",
            "vehicle_id": "车辆编号",
            "distance_km": "已行驶 km",
            "speed_multiplier": "演示速度",
            "color_raw": "原始颜色",
            "color_source": "颜色来源",
        }
        for key in translated:
            if key in props and props[key] is not None:
                rows.append((translated[key], str(props[key])))
        tags = {**props.get("way_tags", {}), **props.get("relation_tags", {})}
        for key, title in [
            ("from", "起点"),
            ("to", "终点"),
            ("distance", "标注距离"),
            ("gauge", "轨距 mm"),
            ("voltage", "电压 V"),
            ("frequency", "频率 Hz"),
            ("electrified", "电气化"),
            ("maxspeed", "最高速度"),
            ("opening_date", "开通日期"),
            ("start_date", "启用日期"),
            ("tunnel", "隧道"),
            ("bridge", "桥梁"),
            ("railway:cbtc", "信号系统"),
            ("website", "官网"),
            ("wikipedia", "维基百科"),
            ("wikidata", "Wikidata"),
        ]:
            if tags.get(key):
                rows.append((title, str(tags[key])))
        if props.get("relation_members"):
            rows.append(("OSM 关系成员", str(len(props["relation_members"]))))
        if not rows:
            rows = [
                (key, str(value))
                for key, value in props.items()
                if not isinstance(value, (dict, list))
            ]
        self.set_property_rows(rows)
        self.raw.setPlainText(json.dumps(feature, ensure_ascii=False, indent=2))
        self.right.show()
        self.detail_rail.setChecked(True)

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
        if self.selected_data.get("layer") != "vehicles" or not self.right.isVisible():
            return
        vehicle_id = self.selected_data["properties"].get("vehicle_id")
        feature = next(
            (
                f
                for f in self.operations.current_vehicle_features
                if f["properties"]["vehicle_id"] == vehicle_id
            ),
            None,
        )
        if feature:
            properties = self.selected_data["properties"]
            properties.update(feature["properties"])
            for row in range(self.properties.rowCount()):
                title = self.properties.item(row, 0).text()
                if title == "已行驶 km":
                    self.properties.item(row, 1).setText(str(properties["distance_km"]))
                if title == "运行状态":
                    self.properties.item(row, 1).setText(properties["state"])
            self.raw.setPlainText(
                json.dumps(self.selected_data, ensure_ascii=False, indent=2)
            )

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
                    f"{self.manifest.get('metro_station_area_features_written', 0):,}",
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
            return
        if "上海" in query and "1" in query:
            self.map.call("focusDemo")
            self._show_demo_details()
            return
        for city, province, lon, lat in REGIONS:
            if query == city or query == city + "市":
                self.map.call("focus", lon, lat, 11, city + " · 轨道交通")
                self.line_search.setText(city)
                self.open_sidebar(0)
                return
        if self._stations_cache is None:
            self._stations_cache = read_json(
                DATA / "china_metro_stations.geojson", EMPTY
            )["features"]
        result = next(
            (
                station
                for station in self._stations_cache
                if query in station["properties"].get("name", "")
            ),
            None,
        )
        if result:
            self.map.call("focus", *result["geometry"]["coordinates"], 15)
            self.display_feature(
                {"layer": "stations", "properties": result["properties"]}
            )
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
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    window = Desk()
    window.show()
    if args.screenshot:

        def capture():
            def complete(payload):
                state = json.loads(payload) if payload else None
                # Keep the complete workspace visible in the verification image.
                checks = {
                    "single_workspace": not hasattr(window, "pages"),
                    "sidebar_reopen": False,
                    "switch_has_thumb": isinstance(window.switches["metro"], Switch),
                    "continuous_demo_km": round(window.demo["length_m"] / 1000, 2),
                }
                window.toggle_left()
                window.open_sidebar(0)
                window.toggle_right()
                window.toggle_right()
                checks["sidebar_reopen"] = (
                    window.left.isVisible() and window.right.isVisible()
                )

                def finish():
                    if args.verify_hierarchy and "hierarchy_dialog_saved" not in checks:
                        exercise_hierarchy()
                        return
                    window.grab().save(args.screenshot)
                    if args.smoke_report:
                        report = {
                            "checks": checks,
                            "map": state,
                            "javascript_errors": window.map.page().console_errors,
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
                        or window.map.page().console_errors
                    )
                    app.exit(1 if failed else 0)

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
                            window.grab().save(
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
                window.pause_demo()
                window.switches["metro"].click()
                parent = next(
                    item for item in window.tree_items if item.text(0) == "上海市"
                )
                parent_ids = window.item_ids(parent)
                window.tree_switches[id(parent)].click()
                window.base_switches["roads"].click()

                def inspect_controls(payload):
                    controls = json.loads(payload)
                    checks["metro_switch_controls_map"] = (
                        controls["metroVisibility"] == "none"
                    )
                    checks["pause_controls_animation"] = not controls["running"]
                    checks["province_controls_children"] = controls[
                        "visibleLines"
                    ] == len(window.catalog) - sum(r > 0 for r in parent_ids)
                    checks["line_switch_hides_stations_and_polygons"] = (
                        not controls["line1StationsAllowed"]
                        and not controls["line1AreasAllowed"]
                    )
                    checks["project_directory_controls_construction"] = controls[
                        "visibleConstruction"
                    ] == len(window.construction_catalog) - sum(
                        r < 0 for r in parent_ids
                    )
                    checks["parent_thumb_synchronized"] = (
                        window.tree_switches[id(parent)].get_position() == 0
                    )
                    checks["base_road_switch"] = (
                        controls["roadBaseVisibility"] == "none"
                        if controls["vectorAvailable"]
                        else None
                    )
                    window.switches["metro"].click()
                    window.tree_switches[id(parent)].click()
                    if not window.base_switches["roads"].isChecked():
                        window.base_switches["roads"].click()
                    window.play_demo()

                    def inspect_movement(payload):
                        latest = json.loads(payload)
                        checks["vehicle_moves_after_resume"] = (
                            latest["travelled"] > initial_distance
                        )
                        state.update(latest)
                        window.grab().save(args.screenshot)
                        window.open_sidebar(1)

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
                            window.grab().save(
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
                            window.grab().save(
                                str(path.with_name(path.stem + "-diagram.png"))
                            )
                            window.operations.tabs.setCurrentIndex(0)
                            window.resize(1050, 720)
                            QTimer.singleShot(200, capture_small)

                        def capture_small():
                            path = Path(args.screenshot)
                            window.grab().save(
                                str(path.with_name(path.stem + "-compact.png"))
                            )
                            window.resize(1600, 980)
                            window.open_sidebar(0)
                            if not window.right.isVisible():
                                window.toggle_right()
                            window.operations.clock = 25200
                            window.operations.play()
                            QTimer.singleShot(
                                200, exercise_bases if args.verify_bases else finish
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
