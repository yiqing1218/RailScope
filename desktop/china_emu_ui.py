"""Searchable source profiles, including line scopes, station yards and models."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QVBoxLayout, QLineEdit, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox, QWidget, QPushButton,
)

try:
    from .china_emu import load_store, VEHICLE_LABELS
    from .line_metadata import FIELD_LABELS
    from .catalog_metadata import STATION_OVERVIEW_FIELDS
except ImportError:
    from china_emu import load_store, VEHICLE_LABELS
    from line_metadata import FIELD_LABELS
    from catalog_metadata import STATION_OVERVIEW_FIELDS


class ReferencePanel(QWidget):
    def __init__(self, parent=None, store=None, vehicle_only=False, create_vehicle=None):
        super().__init__(parent)
        self.setWindowTitle("中国动车组 · 线路、站场与车型参考资料")
        self.resize(980, 720)
        self.store = store
        self.profiles = (store or load_store()).profiles
        layout = QVBoxLayout(self)
        label = QLabel('来源：<a href="https://china-emu.cn/">中国动车组</a> · '
                       '未核验参考 · <a href="https://china-emu.cn/About/Agreement/">使用协议（禁止商业使用）</a>')
        label.setOpenExternalLinks(True)
        layout.addWidget(label)
        self.kind = QComboBox()
        for title, value in [("车型", "vehicle"), ("线路 / 区段", "line"), ("车站 / 站场", "station")]:
            self.kind.addItem(title, value)
        layout.addWidget(self.kind)
        self.kind.setVisible(not vehicle_only)
        self.search = QLineEdit()
        self.search.setPlaceholderText("输入车型、线路或车站名称")
        layout.addWidget(self.search)
        self.objects = QTableWidget(0, 2)
        self.objects.setHorizontalHeaderLabels(["名称", "来源地址"])
        self.objects.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.objects.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.objects.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.objects, 1)
        self.details = QTableWidget(0, 2)
        self.details.setHorizontalHeaderLabels(["属性 / 适用范围", "网站参考值"])
        self.details.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.details.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.details, 2)
        if create_vehicle:
            create = QPushButton("用选中车型新建车辆…")
            create.clicked.connect(lambda: create_vehicle(self.selected_profile()))
            layout.addWidget(create)
        self.kind.currentIndexChanged.connect(self.populate)
        self.search.textChanged.connect(self.populate)
        self.objects.itemSelectionChanged.connect(self.show_profile)
        self.populate()

    def selected_profile(self):
        item = self.objects.item(self.objects.currentRow(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def refresh_profiles(self):
        selected = self.selected_profile()
        self.profiles = (self.store or load_store()).profiles
        self.populate()
        if selected:
            for row in range(self.objects.rowCount()):
                if self.objects.item(row,0).data(Qt.ItemDataRole.UserRole)['id']==selected['id']:
                    self.objects.selectRow(row)
                    break

    def populate(self):
        self.objects.setRowCount(0)
        self.details.setRowCount(0)
        query = self.search.text().strip().casefold()
        for profile in self.profiles:
            if profile["kind"] != self.kind.currentData() or query not in profile["name"].casefold():
                continue
            row = self.objects.rowCount()
            self.objects.insertRow(row)
            item = QTableWidgetItem(profile["name"])
            item.setData(Qt.ItemDataRole.UserRole, profile)
            self.objects.setItem(row, 0, item)
            self.objects.setItem(row, 1, QTableWidgetItem(profile["source_url"]))
        if self.objects.rowCount():
            self.objects.selectRow(0)

    def show_profile(self):
        item = self.objects.item(self.objects.currentRow(), 0)
        if item is None:
            return
        profile = item.data(Qt.ItemDataRole.UserRole)
        labels = (VEHICLE_LABELS if profile["kind"] == "vehicle" else
                  dict(STATION_OVERVIEW_FIELDS) if profile["kind"] == "station" else FIELD_LABELS)
        values = [("资料获取时间", profile["retrieved_at"]), ("来源版本", profile["snapshot_id"])]
        values.extend((labels.get(k, k), str(v)) for k, v in profile["attributes"].items())
        for scope in profile.get("scopes", []):
            span = scope["name"] + (" · " + scope["span"] if scope.get("span") else "")
            if scope.get("source_notes"):
                values.append((span + " · 来源条件说明", scope["source_notes"]))
            if scope.get("lines"):
                values.append((span + " · 适用线路", "、".join(scope["lines"])))
            values.extend((span + " · " + labels.get(k, k), str(v)) for k, v in scope["attributes"].items())
            values.extend((span + " · 站场", y["name"] + " · " + y.get("lines", "")) for y in scope.get("yards", []))
            if scope.get("platform_numbers"):
                values.append((span + " · 来源标注站台编号", "、".join(scope["platform_numbers"])))
        self.details.setRowCount(len(values))
        for row, (key, value) in enumerate(values):
            self.details.setItem(row, 0, QTableWidgetItem(key))
            self.details.setItem(row, 1, QTableWidgetItem(value))
        self.details.resizeRowsToContents()


class ReferenceDialog(QDialog):
    def __init__(self, parent=None, store=None):
        super().__init__(parent)
        self.setWindowTitle("中国动车组 · 线路、站场与车型参考资料")
        self.resize(980, 720)
        layout = QVBoxLayout(self)
        self.panel = ReferencePanel(self, store)
        layout.addWidget(self.panel)
        for name in ('profiles', 'kind', 'search', 'objects', 'details', 'populate', 'show_profile'):
            setattr(self, name, getattr(self.panel, name))
