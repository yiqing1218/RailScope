"""National and province expressway directory backed by a local SQLite index."""

from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

try:
    from .components import text_label
    from .road_store import routes
except ImportError:
    from components import text_label
    from road_store import routes


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
        self.search.returnPressed.connect(self.refresh)
        layout.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setMinimumHeight(260)
        self.tree.setMaximumHeight(440)
        self.tree.itemClicked.connect(self.select_item)
        self.tree.itemDoubleClicked.connect(self.focus_item)
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
        self.tree.clear()
        if not self.database.is_file():
            self.note.setText("尚未建立全国高速目录。可使用本机已下载的全国 OSM 快照提取。")
            return
        records = routes(self.database, self.search.text().strip())
        roots = {
            "national": QTreeWidgetItem(self.tree, ["国家高速"]),
            "provincial": QTreeWidgetItem(self.tree, ["省级高速"]),
            "unresolved": QTreeWidgetItem(self.tree, ["待核对高速"]),
        }
        provinces = {}
        for record in records:
            kind = record["kind"]
            parent = roots[kind]
            if kind != "national":
                province = record["province"]
                group_key = kind, province
                if group_key not in provinces:
                    provinces[group_key] = QTreeWidgetItem(parent, [province])
                parent = provinces[group_key]
            label = record["ref"] or record["name"] or "未命名高速"
            item = QTreeWidgetItem(parent, [f"{label} · {record['segment_count']:,} 段"])
            item.setData(0, Qt.ItemDataRole.UserRole, record)
            sample_name = f"；路段名称示例：{record['name']}" if record["ref"] and record["name"] else ""
            item.setToolTip(0, f"OSM highway=motorway；编号取自 ref{sample_name}；双击定位。")
        for root in roots.values():
            root.setExpanded(True)
        self.note.setText(f"当前快照：{len(records):,} 条高速目录项。G 为国家高速，S 为省级高速；编号缺失的路段单独待核对。数据：© OpenStreetMap contributors。")

    def select_item(self, item, _column):
        record = item.data(0, Qt.ItemDataRole.UserRole)
        self.map.call("setRoadRouteSelection", record["key"] if record else None)
        if record:
            self.route_selected.emit(record["key"])

    def focus_item(self, item, _column):
        record = item.data(0, Qt.ItemDataRole.UserRole)
        if record:
            self.map.call(
                "fit",
                [[record["minx"], record["miny"]], [record["maxx"], record["maxy"]]],
                record["ref"] or record["name"],
            )
            self.map.call("setRoadRouteSelection", record["key"])
            self.route_selected.emit(record["key"])
