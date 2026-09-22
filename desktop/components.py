"""Reusable native controls for the RailScope desktop workbench."""

from PySide6.QtCore import (
    Property,
    QPropertyAnimation,
    QRectF,
    Qt,
    QEasingCurve,
    QTimer,
    QEvent,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPen
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
)

THEME = """
QMainWindow, #workspace { background: #edf1f4; }
QWidget { color: #202f3b; font-family: 'Aptos', 'Microsoft YaHei UI'; font-size: 13px; }
QMenuBar { background: #fbfcfd; border-bottom: 1px solid #dce4ea; padding: 5px 14px; }
QMenuBar::item { padding: 6px 12px; border-radius: 5px; }
QMenuBar::item:selected, QMenu::item:selected { background: #e2f2f2; color: #086b68; }
QMenu { background: #ffffff; border: 1px solid #d3dfe6; border-radius: 8px; padding: 8px; }
QMenu::item { padding: 8px 30px 8px 14px; border-radius: 4px; }
#header { background: #fbfcfd; border-bottom: 1px solid #dce4ea; }
#brand { font-size: 24px; font-weight: 700; color: #172b37; }
#subheading { color: #526673; font-size: 12px; }
#badge { background: #e1f3ed; color: #176c50; padding: 5px 10px; border-radius: 10px; font-size: 11px; }
#panel { background: rgba(255,255,255,242); border: 1px solid #dbe4eb; border-radius: 12px; }
#rail { background: #f9fbfc; border: 1px solid #dbe4eb; border-radius: 10px; }
#panelTitle { font-size: 16px; font-weight: 700; }
#muted { color: #536875; font-size: 12px; }
#sectionLabel { color: #526775; font-size: 11px; font-weight: 600; }
#card { background: #f4f8fa; border: 1px solid #dce7ed; border-radius: 10px; }
#demoCard { background: #eaf6f4; border: 1px solid #c4e4de; border-radius: 11px; }
#routeBadge { background: #c82732; color: #ffffff; border-radius: 8px; font-size: 20px; font-weight: 700; padding: 8px; }
#selectedTitle { font-size: 19px; font-weight: 700; }
#metricValue { font-size: 25px; font-weight: 700; color: #193d46; }
#fold { background: #ffffff; border: 1px solid #e0e8ed; border-radius: 8px; }
QPushButton, QToolButton { background: #f7fafc; border: 1px solid #d6e1e8; border-radius: 7px; padding: 7px 11px; }
QPushButton:hover, QToolButton:hover { background: #e8f3f3; border-color: #9ac4c3; }
QPushButton:pressed, QToolButton:pressed { background: #dcebea; }
QPushButton:focus, QToolButton:focus, QLineEdit:focus { border: 1px solid #117d77; }
QPushButton:checked, QToolButton:checked { background: #e0f2ef; color: #096c65; border-color: #afd4ce; }
QPushButton#primary { background: #0c776f; color: #ffffff; border-color: #0c776f; font-weight: 600; }
QPushButton#primary:hover { background: #09645e; }
QToolButton#railButton { border: none; background: transparent; font-size: 13px; padding: 8px 3px; }
QToolButton#railButton:checked { background: #dff0ed; color: #0c736a; }
QToolButton#foldHeader { background: transparent; border: none; padding: 10px; font-weight: 700; text-align: left; }
QLineEdit, QComboBox { background: #ffffff; border: 1px solid #d4dfe7; border-radius: 7px; padding: 8px 10px; }
QLineEdit { selection-background-color: #cce9e5; }
QComboBox::drop-down { border: none; width: 23px; }
QTreeWidget { background: transparent; border: none; outline: 0; }
QTreeWidget::item { padding: 5px 0; border-radius: 5px; }
QTreeWidget::item:hover { background: #f0f6f8; }
QTreeWidget::item:selected { background: #e6f2f0; color: #164840; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { width: 7px; background: transparent; margin: 2px; }
QScrollBar::handle:vertical { background: #c7d3db; border-radius: 3px; min-height: 25px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: transparent; border: none; border-bottom: 2px solid transparent; padding: 9px 15px; color: #526673; }
QTabBar::tab:selected { background: #f3faf8; color: #0b7268; border-bottom: 2px solid #0c776f; }
QTableWidget { background: transparent; border: none; gridline-color: #e3eaef; }
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
"""


class GrowingTree(QTreeWidget):
    """Content-height tree: scrolling belongs exclusively to the surrounding panel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
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
            self._filter_height_floor = max(self._filter_height_floor, min(900, max(320, self.height())))
        else:
            self._filter_height_floor = 0
        self.schedule_height()

    def fit_content(self):
        self._height_pending = False
        self.doItemsLayout()

        def height(item):
            if item.isHidden():
                return 0
            value = max(
                32,
                self.visualItemRect(item).height(),
                self.sizeHintForIndex(self.indexFromItem(item)).height(),
            )
            if item.isExpanded():
                value += sum(height(item.child(i)) for i in range(item.childCount()))
            return value

        total = sum(
            height(self.topLevelItem(i)) for i in range(self.topLevelItemCount())
        )
        self.setFixedHeight(
            max(
                32,
                self._filter_height_floor,
                total + 4 + (0 if self.isHeaderHidden() else self.header().height()),
            )
        )

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
