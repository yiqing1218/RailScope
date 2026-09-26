"""National and province expressway directory backed by a local SQLite index."""

from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QTreeView, QVBoxLayout, QWidget, QTabWidget

try:
    from .components import text_label
    from .lazy_directory import SqliteDirectoryModel
    from .road_store import route, routes, sync_directory
    from .road_services import services, sync_directory as sync_services
except ImportError:
    from components import text_label
    from lazy_directory import SqliteDirectoryModel
    from road_store import route, routes, sync_directory
    from road_services import services, sync_directory as sync_services


class RoadCatalog(QWidget):
    build_requested = Signal()
    route_selected = Signal(str)
    service_selected = Signal(str)

    def __init__(self, database, map_view, parent=None):
        super().__init__(parent)
        self.database = Path(database)
        self.map = map_view
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 0, 6, 4)
        layout.setSpacing(7)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索 G / S 编号、高速名称或省份")
        self.search.textChanged.connect(self.filter_rows)
        layout.addWidget(self.search)
        self.tree = QTreeView()
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setMinimumHeight(260)
        self.tree.setMaximumHeight(440)
        self.model = None
        self.visible_routes = set()
        self.tree.doubleClicked.connect(self.focus_item)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.tree, '高速线路')
        self.service_tree = QTreeView()
        self.service_tree.setHeaderHidden(True)
        self.service_tree.setUniformRowHeights(True)
        self.service_tree.setMinimumHeight(260)
        self.service_tree.setMaximumHeight(440)
        self.service_model = None
        self.visible_services = set()
        self.service_tree.doubleClicked.connect(self.focus_service)
        self.tabs.addTab(self.service_tree, '服务区 · 行政区域')
        self.tabs.currentChanged.connect(lambda _: self.search.setPlaceholderText('搜索服务区名称或行政区域' if self.tabs.currentIndex() else '搜索 G / S 编号、高速名称或省份'))
        layout.addWidget(self.tabs)
        controls = QHBoxLayout()
        self.build = QPushButton("从全国 OSM 快照建立 / 更新目录")
        self.build.clicked.connect(self.build_requested.emit)
        controls.addWidget(self.build)
        layout.addLayout(controls)
        self.note = text_label("", wrap=True)
        layout.addWidget(self.note)
        self.refresh()

    def refresh(self):
        if not self.database.is_file():
            self.note.setText("尚未建立全国高速目录。可使用本机已下载的全国 OSM 快照提取。")
            return
        sync_directory(self.database)
        sync_services(self.database)
        records = routes(self.database, self.search.text().strip())
        if self.model is None:
            self.model = SqliteDirectoryModel(self.database)
            self.model.toggled.connect(self.toggle_node)
            self.tree.setModel(self.model)
        self.model.set_search(self.search.text())
        self.model.fetchMore()
        self.model.set_visible(self.visible_routes)
        if self.service_model is None:
            self.service_model = SqliteDirectoryModel(self.database, 'service_directory')
            self.service_model.toggled.connect(self.toggle_service)
            self.service_tree.setModel(self.service_model)
        self.service_model.set_search(self.search.text())
        self.service_model.fetchMore()
        self.service_model.set_visible(self.visible_services)
        self.note.setText(f"当前快照：{len(records):,} 条高速目录项，{len(services(self.database)):,} 个服务区。在建线路标注“在建”；服务区按省／市／县分类。数据：© OpenStreetMap contributors。")

    def filter_rows(self, text):
        if self.model is not None:
            self.model.set_search(text)
            self.model.fetchMore()
        if self.service_model is not None:
            self.service_model.set_search(text)
            self.service_model.fetchMore()

    def toggle_node(self, key, on):
        keys = self.model.ids_below(key)
        self.visible_routes.update(keys) if on else self.visible_routes.difference_update(keys)
        self.model.set_visible(self.visible_routes)
        self.map.call("setRoadRouteSelection", None)
        self.map.call("setRoadRoutes", sorted(self.visible_routes))
        if on and keys:
            for construction in (False, True):
                match = next((k for k in keys if k.endswith('/construction') == construction), None)
                if match:
                    self.route_selected.emit(match)

    def set_all(self, on, construction=None):
        if self.model is None:
            return
        keys = {record['key'] for record in routes(self.database)
                if construction is None or record['key'].endswith('/construction') == construction}
        self.visible_routes.update(keys) if on else self.visible_routes.difference_update(keys)
        self.model.set_visible(self.visible_routes)
        self.map.call("setRoadRouteSelection", None)
        self.map.call("setRoadRoutes", sorted(self.visible_routes))

    def toggle_service(self, key, on):
        keys = self.service_model.ids_below(key)
        self.visible_services.update(keys) if on else self.visible_services.difference_update(keys)
        self.send_services()
        if on and keys:
            self.service_selected.emit(next(iter(keys)))

    def send_services(self):
        if self.service_model:
            self.service_model.set_visible(self.visible_services)
        self.map.call('setRoadServices', sorted(self.visible_services))

    def set_services_all(self, on):
        self.visible_services = {record['id'] for record in services(self.database)} if on else set()
        self.send_services()

    def focus_service(self, index):
        key = self.service_model.data(index, Qt.ItemDataRole.UserRole)
        item = next((item for item in services(self.database) if item['id'] == key), None)
        if item:
            self.map.call('fit', [[item['minx']-.002, item['miny']-.002], [item['maxx']+.002, item['maxy']+.002]], item['name'])

    def select_item(self, index):
        # Selecting a row must never narrow the independently checked routes.
        pass

    def focus_item(self, index):
        key = self.model.data(index, Qt.ItemDataRole.UserRole)
        record = route(self.database, key) if key and key.startswith(("G/", "S/", "U/")) else None
        if record:
            self.map.call(
                "fit",
                [[record["minx"], record["miny"]], [record["maxx"], record["maxy"]]],
                record["ref"] or record["name"],
            )
