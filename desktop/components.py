"""Reusable native controls for the RailScope desktop workbench."""

from pathlib import Path

from PySide6.QtCore import (
    Property,
    QPropertyAnimation,
    QRectF,
    Qt,
    QEasingCurve,
    QTimer,
    QEvent,
    QSize,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPalette
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QTreeWidget,
    QTabWidget,
)

THEME = """
QMainWindow, #workspace { background: #edf2f5; }
QWidget { color: #243b48; font-family: 'Microsoft YaHei UI', 'Aptos'; font-size: 13px; }
QMenuBar { background: #ffffff; border-bottom: 1px solid #d5e1e7; padding: 3px 12px; }
QMenuBar::item { padding: 6px 12px; border-radius: 5px; }
QMenuBar::item:pressed { background: #dcefeb; color: #075f59; }
QMenuBar::item:selected, QMenu::item:selected { background: #e2f2f2; color: #086b68; }
QMenu { background: #ffffff; border: 1px solid #d3dfe6; border-radius: 8px; padding: 8px; }
QMenu::item { padding: 8px 30px 8px 14px; border-radius: 4px; }
#header { background: #ffffff; border-bottom: 2px solid #147d78; }
#brand { font-size: 24px; font-weight: 700; color: #172b37; }
#subheading { color: #526673; font-size: 12px; }
#badge { background: #e1f3ed; color: #176c50; padding: 5px 10px; border-radius: 10px; font-size: 11px; }
#panel { background: #ffffff; border: 1px solid #cfdce4; border-radius: 8px; }
#rail { background: #e4edef; border: 1px solid #cbdadd; border-radius: 8px; }
#panelTitle { font-size: 16px; font-weight: 700; }
#dialogTitle { font-size: 24px; font-weight: 700; color: #163f4a; }
#muted { color: #536875; font-size: 12px; }
#sectionLabel { color: #526775; font-size: 11px; font-weight: 600; }
#card { background: #f4f8fa; border: 1px solid #dce7ed; border-radius: 10px; }
#demoCard { background: #eaf6f4; border: 1px solid #c4e4de; border-radius: 11px; }
#routeBadge { background: #c82732; color: #ffffff; border-radius: 8px; font-size: 20px; font-weight: 700; padding: 8px; }
#selectedTitle { font-size: 19px; font-weight: 700; }
#metricValue { font-size: 25px; font-weight: 700; color: #193d46; }
#fold { background: #ffffff; border: 1px solid #e0e8ed; border-radius: 8px; }
QPushButton, QToolButton { background: #ffffff; border: 1px solid #bdcdd6; border-radius: 6px; padding: 7px 12px; }
QPushButton:hover, QToolButton:hover { background: #e8f3f3; border-color: #9ac4c3; }
QPushButton:pressed, QToolButton:pressed { background: #dcebea; }
QPushButton:focus, QToolButton:focus, QLineEdit:focus { border: 1px solid #117d77; }
QPushButton:checked, QToolButton:checked { background: #e0f2ef; color: #096c65; border-color: #afd4ce; }
QPushButton:disabled, QToolButton:disabled { color: #8797a0; background: #eef2f4; border-color: #e2e8eb; }
QPushButton#primary { background: #0c776f; color: #ffffff; border-color: #0c776f; font-weight: 600; }
QPushButton#primary:hover { background: #09645e; }
QToolButton#railButton { border: none; background: transparent; font-size: 13px; padding: 8px 3px; }
QToolButton#railButton:checked { background: #dff0ed; color: #0c736a; }
QToolButton#foldHeader { background: transparent; border: none; padding: 10px; font-weight: 700; text-align: left; }
QLineEdit, QComboBox, QSpinBox { background: #ffffff; border: 1px solid #adbfca; border-radius: 6px; padding: 8px 10px; selection-background-color: #cce9e5; }
QLineEdit:read-only { background: #edf2f5; color: #526775; }
QComboBox QLineEdit { border: none; padding: 0; background: transparent; }
QComboBox:focus, QSpinBox:focus { border: 1px solid #117d77; }
QComboBox QAbstractItemView { background: #ffffff; color: #20313d; border: 1px solid #cddce3; selection-background-color: #dcefeb; selection-color: #164840; outline: 0; }
QLineEdit { selection-background-color: #cce9e5; }
QComboBox::drop-down { border: none; width: 23px; }
QTreeWidget { background: transparent; border: none; outline: 0; }
QTreeWidget::item { padding: 6px 0; border-radius: 4px; }
QTreeWidget::item:hover { background: #f0f6f8; }
QTreeWidget::item:selected { background: #dcefea; color: #104f49; }
QTreeWidget:focus { border: 1px solid #9cc9c3; border-radius: 7px; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { width: 7px; background: transparent; margin: 2px; }
QScrollBar::handle:vertical { background: #c7d3db; border-radius: 3px; min-height: 25px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { height: 7px; background: transparent; margin: 2px; }
QScrollBar::handle:horizontal { background: #c7d3db; border-radius: 3px; min-width: 25px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: transparent; border: none; border-bottom: 2px solid transparent; padding: 9px 15px; color: #526673; }
QTabBar::tab:selected { background: #f3faf8; color: #0b7268; border-bottom: 2px solid #0c776f; }
QTableWidget { background: #ffffff; alternate-background-color: #f3f7f9; border: 1px solid #d5e1e7; border-radius: 6px; gridline-color: #e3eaef; }
QTableWidget::item { padding: 7px; }
QTableWidget::item:selected { background: #dff0eb; color: #153f38; }
QHeaderView::section { background: #f3f7f9; color: #526673; border: none; border-bottom: 1px solid #dce5eb; padding: 9px 7px; font-weight: 600; }
QToolTip { background: #ffffff; color: #20313d; border: 1px solid #cadbdc; padding: 7px; }
QPlainTextEdit { background: #f5f8fa; border: 1px solid #dce5eb; border-radius: 8px; padding: 7px; font-family: 'Cascadia Code', 'Microsoft YaHei UI'; font-size: 11px; }
QProgressBar { background: #dce9e7; border: none; border-radius: 4px; height: 7px; }
QProgressBar::chunk { background: #11968a; border-radius: 4px; }
QSlider::groove:horizontal { height: 4px; background: #d5e2e6; border-radius: 2px; }
QSlider::handle:horizontal { width: 14px; margin: -5px 0; border-radius: 7px; background: #0e857b; }
QStatusBar { background: #f9fbfc; color: #536875; border-top: 1px solid #dce4ea; font-size: 11px; }
QSplitter::handle { background: transparent; width: 8px; }
QDialog { background: #f7fafb; }
QDialogButtonBox { background: transparent; }
QGroupBox { border: 1px solid #d9e5e9; border-radius: 9px; margin-top: 12px; padding: 12px 8px 8px; background: #ffffff; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; color: #285560; }
"""


class CurrentPageTabs(QTabWidget):
    """Size a sidebar tab strip from its current page, not the tallest hidden one."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.currentChanged.connect(lambda _index: self.updateGeometry())

    def tabInserted(self, index):
        super().tabInserted(index)
        self.widget(index).installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.currentWidget() and event.type() == QEvent.Type.LayoutRequest:
            self.updateGeometry()
        return super().eventFilter(watched, event)

    def sizeHint(self):
        page = self.currentWidget()
        height = page.sizeHint().height() if page is not None else 0
        return QSize(super().sizeHint().width(), max(0,height) + self.tabBar().sizeHint().height() + 4)

    def minimumSizeHint(self):
        return QSize(0, self.sizeHint().height())


class GrowingTree(QTreeWidget):
    """Small trees fit their rows; larger trees scroll inside a bounded viewport."""

    MAX_VIEW_HEIGHT = 560

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._height_pending = False
        self._filter_height_floor = 0
        self._height_timer = QTimer(self)
        self._height_timer.setSingleShot(True)
        self._height_timer.timeout.connect(self.fit_content)
        self.expanded.connect(self.schedule_height)
        self.collapsed.connect(self.schedule_height)
        self.model().rowsInserted.connect(self.schedule_height)
        self.model().rowsRemoved.connect(self.schedule_height)
        self.model().modelReset.connect(self.schedule_height)
        self.schedule_height()

    def schedule_height(self, *args):
        if not self._height_pending:
            self._height_pending = True
            self._height_timer.start(0)

    def set_filter_active(self, active):
        """Keep a useful directory viewport while rows are temporarily filtered."""
        if active:
            self._filter_height_floor = min(320, self.MAX_VIEW_HEIGHT)
        else:
            self._filter_height_floor = 0
        self.schedule_height()

    def fit_content(self):
        self._height_pending = False
        total = 4 + (0 if self.isHeaderHidden() else self.header().height())

        def rows(item):
            if item.isHidden():
                return
            yield item
            if item.isExpanded():
                for index in range(item.childCount()):
                    yield from rows(item.child(index))

        for index in range(self.topLevelItemCount()):
            for item in rows(self.topLevelItem(index)):
                total += max(32, self.sizeHintForIndex(self.indexFromItem(item)).height())
                if total >= self.MAX_VIEW_HEIGHT:
                    break
            if total >= self.MAX_VIEW_HEIGHT:
                break
        self.setFixedHeight(max(32, self._filter_height_floor, min(total, self.MAX_VIEW_HEIGHT)))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (
            QEvent.Type.StyleChange,
            QEvent.Type.FontChange,
        ) and hasattr(self, "_height_timer"):
            self.schedule_height()

    def showEvent(self, event):
        super().showEvent(event)
        self.schedule_height()


class Switch(QAbstractButton):
    """An actual animated OFF/ON switch with a painted sliding thumb."""

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(56, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._position = 1.0 if checked else 0.0
        self._mixed = False
        self.animation = QPropertyAnimation(self, b"position", self)
        self.animation.setDuration(160)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def setChecked(self, checked):
        super().setChecked(checked)
        # Parent/child synchronization blocks signals, not the visual state.
        if hasattr(self, "animation") and self.signalsBlocked():
            self.animation.stop()
            self.set_position(1.0 if checked else 0.0)

    def _animate(self, checked):
        self._mixed = False
        self.animation.stop()
        self.animation.setStartValue(self._position)
        self.animation.setEndValue(1.0 if checked else 0.0)
        self.animation.start()

    def get_position(self):
        return self._position

    def set_position(self, value):
        self._position = value
        self.update()

    position = Property(float, get_position, set_position)

    def setMixed(self, mixed):
        self._mixed = mixed
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.4)
        track = QColor("#137c73" if self.isChecked() else "#647985")
        if self._mixed:
            track = QColor("#647985")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(1, 2, 54, 24), 12, 12)
        painter.setFont(QFont("Aptos", 7, QFont.Weight.Bold))
        painter.setPen(QColor("#ffffff"))
        text = "ON" if self.isChecked() else "OFF"
        text_rect = QRectF(4, 2, 28, 24) if self.isChecked() else QRectF(25, 2, 28, 24)
        painter.drawText(
            text_rect, Qt.AlignmentFlag.AlignCenter, "−" if self._mixed else text
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QRectF(4 + 28 * self._position, 5, 18, 18))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#0b645f"), 1.5))
            painter.drawRoundedRect(QRectF(0.8, 0.8, 54.4, 26.4), 13, 13)


class SquareSwitch(QAbstractButton):
    """White visibility checkbox used by map-layer master controls."""

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(22, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mixed = False
        self.toggled.connect(lambda _: self.setMixed(False))

    def setMixed(self, value):
        self._mixed = bool(value)
        self.update()

    def setChecked(self, checked):
        self._mixed = False
        super().setChecked(checked)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.45)
        accent = self.window().property("appearanceAccent") or "#0c776f"
        painter.setBrush(self.palette().color(QPalette.ColorRole.Base))
        painter.setPen(QPen(QColor(accent if self.isChecked() or self._mixed else "#8297a2"), 1.5))
        painter.drawRoundedRect(QRectF(2.5, 2.5, 17, 17), 2, 2)
        painter.setPen(QPen(QColor(accent), 2.3, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        if self._mixed:
            painter.drawLine(7, 11, 15, 11)
        elif self.isChecked():
            painter.drawLine(6, 11, 10, 15)
            painter.drawLine(10, 15, 16, 7)


def directory_checkbox_style():
    """Render one white square in the left-hand check column of each directory."""
    icons = Path(__file__).resolve().parent / "assets"
    states = {"unchecked": "visibility_off.svg", "checked": "visibility_on.svg",
              "indeterminate": "visibility_mixed.svg"}
    return "\n".join(
        f'{view}::indicator:{state} {{ width: 18px; height: 18px; image: url("{(icons / file).as_posix()}"); }}'
        for view in ("QTreeView", "QTreeWidget")
        for state, file in states.items()
    )


def text_label(text, name="muted", wrap=False):
    label = QLabel(text)
    label.setObjectName(name)
    label.setWordWrap(wrap)
    if wrap:
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return label


def switch_row(title, switch, subtitle=""):
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 4, 0, 4)
    captions = QVBoxLayout()
    captions.setSpacing(2)
    label = text_label(title, name="", wrap=True)
    captions.addWidget(label)
    if subtitle:
        captions.addWidget(text_label(subtitle, wrap=True))
    layout.addLayout(captions, 1)
    switch.setAccessibleName(title)
    layout.addWidget(switch)
    layout.setSpacing(12)
    return row


def visibility_row(title, control, subtitle=""):
    """Keep the square on the left, aligned with directory row indicators."""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 4, 0, 4)
    layout.setSpacing(10)
    control.setAccessibleName(title)
    layout.addWidget(control, alignment=Qt.AlignmentFlag.AlignTop)
    captions = QVBoxLayout()
    captions.setSpacing(2)
    captions.addWidget(text_label(title, name="", wrap=True))
    if subtitle:
        captions.addWidget(text_label(subtitle, wrap=True))
    layout.addLayout(captions, 1)
    return row


class Fold(QFrame):
    def __init__(self, title, child, expanded=True, count=""):
        super().__init__()
        self.setObjectName("fold")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 6)
        layout.setSpacing(0)
        self.button = QToolButton()
        self.button.setObjectName("foldHeader")
        self.button.setText(f"{title}    {count}" if count else title)
        self.button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.button.setCheckable(True)
        self.button.setChecked(expanded)
        self.button.setSizePolicy(
            self.button.sizePolicy().horizontalPolicy(),
            self.button.sizePolicy().verticalPolicy(),
        )
        self.button.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.button.toggled.connect(lambda on: self._toggle(child, on))
        layout.addWidget(self.button)
        layout.addWidget(child)
        child.setVisible(expanded)

    def _toggle(self, child, on):
        child.setVisible(on)
        self.button.setArrowType(
            Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow
        )
