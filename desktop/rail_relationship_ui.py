"""Name-first pickers backed by stable station and line IDs."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QListWidget, QListWidgetItem

try:
    from .corridor_ui import SearchChoice
except ImportError:
    from corridor_ui import SearchChoice


class RelationshipSelector(QWidget):
    def __init__(self, search, items, placeholder, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        row = QHBoxLayout()
        self.search = SearchChoice(search, placeholder)
        row.addWidget(self.search,1)
        add = QPushButton('添加')
        add.clicked.connect(self.add_selected)
        row.addWidget(add)
        layout.addLayout(row)
        self.items = QListWidget()
        self.items.setMinimumHeight(150)
        self.items.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        layout.addWidget(self.items)
        remove = QPushButton('移除所选')
        remove.clicked.connect(self.remove_selected)
        layout.addWidget(remove)
        for key, name in items:
            self.append(key, name)

    def append(self, key, name):
        if key in self.values():
            return
        item = QListWidgetItem(name)
        item.setData(Qt.ItemDataRole.UserRole, key)
        item.setToolTip(f'{name}\n{key}')
        self.items.addItem(item)

    def add_selected(self):
        if self.search.currentData():
            self.append(str(self.search.currentData()), self.search.currentText().split(' · ')[0])

    def remove_selected(self):
        for item in self.items.selectedItems():
            self.items.takeItem(self.items.row(item))

    def values(self):
        return [self.items.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.items.count())]
