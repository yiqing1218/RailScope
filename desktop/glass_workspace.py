"""Transparent native controls over browser-composited map glass surfaces."""
from PySide6.QtCore import QEvent, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QDialog, QFrame, QVBoxLayout, QWidget

from appearance import DEFAULT, web_theme


class GlassPanel(QFrame):
    def __init__(self, name="panel", parent=None):
        super().__init__(parent)
        self.setObjectName(name)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.theme = web_theme(DEFAULT)
        self.web_backdrop = False

    def apply_theme(self, value):
        self.theme = web_theme(value)
        self.update()

    def paintEvent(self, event):
        if self.web_backdrop:
            # The browser paints and blurs the live map beneath these controls.
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 14, 14)
        painter.setClipPath(path)
        tint = QColor(self.theme["surface"])
        tint.setAlphaF(self.theme["opacity"] / 100 if self.theme["glass"] else 1)
        painter.fillPath(path, tint)
        painter.setClipping(False)
        painter.setPen(QPen(QColor(self.theme["edge"]), 1))
        painter.drawPath(path)


class MapWorkspace(QWidget):
    insetsChanged = Signal(dict)

    def __init__(self, map_widget, left, right, rail, parent=None):
        super().__init__(parent)
        self.map_widget, self.left, self.right, self.rail = map_widget, left, right, rail
        self.setMinimumSize(750, 240)
        self._insets = {}
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.timeout.connect(self.arrange)
        for widget in (map_widget, left, right, rail):
            widget.setParent(self)
        for widget in (left, right, rail):
            widget.installEventFilter(self)
            if isinstance(widget, GlassPanel):
                widget.web_backdrop = True

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Show, QEvent.Type.Hide):
            self._layout_timer.start(0)
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.arrange()

    def showEvent(self, event):
        super().showEvent(event)
        self.arrange()

    def arrange(self):
        self.map_widget.setGeometry(self.rect())
        height = max(0, self.height()-24)
        self.rail.setGeometry(12, 12, 54, height)
        left_width = min(340, max(285, int((self.width()-100)*.3)))
        right_width = min(320, max(275, int((self.width()-100)*.28)))
        self.left.setGeometry(78, 12, left_width, height)
        self.right.setGeometry(self.width()-right_width-12, 12, right_width, height)
        for widget in (self.rail, self.left, self.right):
            widget.raise_()
        insets = {"left": 78 + left_width + 12 if not self.left.isHidden() else 78,
                  "right": right_width + 24 if not self.right.isHidden() else 12,
                  "panels": {key: {"x": panel.x(), "y": panel.y(), "width": panel.width(),
                                   "height": panel.height(), "visible": not panel.isHidden()}
                             for key, panel in (("left", self.left), ("right", self.right), ("rail", self.rail))}}
        if insets != self._insets:
            self._insets = insets
            self.insetsChanged.emit(insets)

    def insets(self):
        return self._insets.copy()

    def apply_theme(self, value):
        for panel in (self.left, self.right, self.rail):
            if isinstance(panel, GlassPanel):
                panel.apply_theme(value)


class WorkbenchWindow(QDialog):
    """Non-modal, resizable native window; closing preserves the editor and plan."""
    def __init__(self, editor, workspace, parent):
        super().__init__(parent, Qt.WindowType.Window)
        self.editor, self.workspace = editor, workspace
        self.setWindowTitle(editor.workspace_title.text())
        self.resize(1280, 780)
        self.setMinimumSize(920, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.panel = GlassPanel("workbenchPanel", self)
        content = QVBoxLayout(self.panel)
        content.setContentsMargins(4, 4, 4, 4)
        content.addWidget(editor)
        layout.addWidget(self.panel)
        editor.closed.connect(self.hide)

    def show_workbench(self, expanded=False):
        self.editor.show()
        if expanded:
            self.showMaximized()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def moveEvent(self, event):
        super().moveEvent(event)
        self.panel.update()

    def showEvent(self, event):
        super().showEvent(event)
        self.editor.show()
        self.panel.update()

    def closeEvent(self, event):
        event.ignore()
        self.hide()
