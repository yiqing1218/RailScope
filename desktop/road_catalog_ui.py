"""National and province expressway directory backed by a local SQLite index."""

from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QTreeView, QVBoxLayout, QWidget

try:
    from .components import text_label
    from .lazy_directory import SqliteDirectoryModel
    from .road_store import route, routes, sync_directory
except ImportError:
    from components import text_label
    from lazy_directory import SqliteDirectoryModel
    from road_store import route, routes, sync_directory


class RoadCatalog(QWidget):
    build_requested = Signal()
    route_selected = Signal(str)

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
        layout.addWidget(self.tree)
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
        records = routes(self.database, self.search.text().strip())
        if self.model is None:
            self.model = SqliteDirectoryModel(self.database)
            self.model.toggled.connect(self.toggle_node)
            self.tree.setModel(self.model)
        self.model.set_search(self.search.text())
        self.model.fetchMore()
        self.model.set_visible(self.visible_routes)
        self.note.setText(f"当前快照：{len(records):,} 条高速目录项。G 为国家高速，S 为省级高速；编号缺失的路段单独待核对。数据：© OpenStreetMap contributors。")

    def filter_rows(self, text):
        if self.model is not None:
            self.model.set_search(text)
            self.model.fetchMore()

    def toggle_node(self, key, on):
        keys = self.model.ids_below(key)
        self.visible_routes.update(keys) if on else self.visible_routes.difference_update(keys)
        self.model.set_visible(self.visible_routes)
        self.map.call("setRoadRouteSelection", None)
        self.map.call("setRoadRoutes", sorted(self.visible_routes))
        if on and keys:
            self.route_selected.emit(next(iter(keys)))

    def set_all(self, on):
        if self.model is None:
            return
        self.visible_routes = {record["key"] for record in routes(self.database)} if on else set()
        self.model.set_visible(self.visible_routes)
        self.map.call("setRoadRouteSelection", None)
        self.map.call("setRoadRoutes", sorted(self.visible_routes))

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
