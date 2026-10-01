"""One clock for map, both players, history and station views."""

from datetime import date
from PySide6.QtCore import QObject, Signal, QTimer


class SessionTime(QObject):
    changed = Signal()
    calendar_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.seconds = 25200.0
        self.day = date.today().isoformat()
        self.current_date = True
        self.driver = None
        self._timer = QTimer(self)
        self._timer.setInterval(60000)
        self._timer.timeout.connect(self.check_today)
        self._timer.start()

    def set_seconds(self, value):
        value = max(0, min(172799, float(value)))
        if value != self.seconds:
            self.seconds = value
            self.changed.emit()

    def set_day(self, day=None):
        value = day or date.today().isoformat()
        date.fromisoformat(value)
        mode_changed = self.current_date != (day is None)
        self.current_date = day is None
        if value != self.day or mode_changed:
            self.day = value
            self.calendar_changed.emit()

    def check_today(self):
        if self.current_date:
            self.set_day()
