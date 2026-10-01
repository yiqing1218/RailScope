"""One shared overview for every facility, line and operating object."""
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QTextDocument, QTextOption, QAbstractTextDocumentLayout
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView, QStyledItemDelegate, QStyleOptionViewItem, QStyle


class _WrappedValue(QStyledItemDelegate):
    def document(self, option, text):
        document = QTextDocument()
        document.setDefaultFont(option.font)
        wrapping = QTextOption()
        wrapping.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        document.setDefaultTextOption(wrapping)
        document.setPlainText(text)
        document.setTextWidth(max(40, self.parent().viewport().width()-20))
        return document

    def sizeHint(self, option, index):
        self.initStyleOption(option, index)
        document = self.document(option, index.data() or '')
        return QSize(int(document.size().width())+16, int(document.size().height())+10)

    def paint(self, painter, option, index):
        option = QStyleOptionViewItem(option)
        self.initStyleOption(option, index)
        text = option.text
        option.text = ''
        option.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, option, painter, option.widget)
        document = self.document(option, text)
        context = QAbstractTextDocumentLayout.PaintContext()
        context.palette = option.palette
        painter.save()
        painter.setClipRect(option.rect)
        painter.translate(option.rect.left()+6, option.rect.top()+4)
        document.documentLayout().draw(painter, context)
        painter.restore()


class PropertyOverview(QTableWidget):
    """Label above value; wrap complete IDs and resize with the dock width."""
    def __init__(self, parent=None):
        super().__init__(0, 1, parent)
        self.horizontalHeader().hide()
        self.verticalHeader().hide()
        self.setShowGrid(False)
        self.setWordWrap(True)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._fields = {}
        self.setItemDelegate(_WrappedValue(self))

    def set_rows(self, rows):
        self.clearContents()
        self.setRowCount(2 * len(rows))
        self._fields.clear()
        for i, (key, value) in enumerate(rows):
            label = QTableWidgetItem(str(key))
            label.setForeground(QColor('#536875'))
            label.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.setItem(2*i, 0, label)
            item = QTableWidgetItem(str(value))
            item.setToolTip(str(value))
            item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            self.setItem(2*i+1, 0, item)
            self._fields[str(key)] = 2*i+1
        self.resizeRowsToContents()

    def update_value(self, field, value):
        row = self._fields.get(field)
        if row is not None and row < self.rowCount():
            self.item(row, 0).setText(str(value))
            self.item(row, 0).setToolTip(str(value))
            self.resizeRowToContents(row)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resizeRowsToContents()
