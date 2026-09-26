"""Editor for user-verified station and line-post connections."""

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
)

try:
    from .corridor_ui import SearchChoice
    from .components import text_label
except ImportError:
    from corridor_ui import SearchChoice
    from components import text_label


class StationConnectionSelector(QWidget):
    """Searchable multi-selection of business lines for one endpoint."""

    def __init__(self, library, endpoint, parent=None):
        super().__init__(parent)
        self.library = library
        self.endpoint = endpoint
        self._names = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(
            text_label(
                "手工指定接轨线路；通道按连通关系匹配端点。需要固定股道时，请在通道编排中选择物理区间。",
                wrap=True,
            )
        )
        search_row = QHBoxLayout()
        self.search = SearchChoice(
            self._search_lines, "搜索线路名称或 RL 稳定编号"
        )
        search_row.addWidget(self.search, 1)
        add = QPushButton("添加")
        add.clicked.connect(self.add_selected_line)
        search_row.addWidget(add)
        layout.addLayout(search_row)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["接轨线路", "稳定编号"])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setMinimumHeight(130)
        layout.addWidget(self.table)
        remove = QPushButton("移除所选线路")
        remove.clicked.connect(self.remove_selected_lines)
        layout.addWidget(remove)
        for line in library.connected_lines(endpoint):
            self._append(line["id"], line["name"])

    def _search_lines(self, query):
        return [
            (line["id"], f"{line['name']} · {line['id']}")
            for line in self.library.search_lines(query, limit=100)
        ]

    def _append(self, line_id, name):
        if line_id in self._names:
            return
        self._names[line_id] = name
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(name))
        self.table.setItem(row, 1, QTableWidgetItem(line_id))
        self.table.setRowHeight(row, 34)

    def add_selected_line(self):
        line_id = self.search.currentData()
        if line_id is None:
            return
        label = self.search.currentText().rsplit(" · ", 1)[0]
        self._append(str(line_id), label)

    def remove_selected_lines(self):
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            item = self.table.item(row, 1)
            if item:
                self._names.pop(item.text(), None)
            self.table.removeRow(row)

    def line_ids(self):
        return [
            self.table.item(row, 1).text()
            for row in range(self.table.rowCount())
        ]

    def connections(self):
        return self.library.station_connection_override(
            self.endpoint, self.line_ids()
        )
