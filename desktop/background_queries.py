"""Bounded latest-request-wins queries; workers never read or update widgets.

Callers capture query parameters on the GUI thread. Only the latest pending
query per consumer/channel is kept; obsolete results are discarded, including
after the dialog has closed.
"""

from collections import OrderedDict
from dataclasses import dataclass
import weakref
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot, Qt
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid


@dataclass
class Query:
    key: tuple
    sequence: int
    owner: object
    action: object
    success: object
    failure: object


class _Worker(QRunnable):
    def __init__(self, queue, query):
        super().__init__()
        self.queue, self.query = queue, query

    def run(self):
        result, error = None, None
        try:
            result = self.query.action()
        except Exception as exc:
            error = exc
        self.queue.finished.emit(self.query, result, error)


class QueryQueue(QObject):
    finished = Signal(object, object, object)

    def __init__(self, parent=None, workers=2):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(workers)
        self.workers = workers
        self.pending = OrderedDict()
        self.active = set()
        self.latest = {}
        self.sequence = 0
        self._owners = weakref.WeakSet()
        self.finished.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    def submit(self, owner, channel, action, success, failure):
        key = id(owner), channel
        self.sequence += 1
        query = Query(key, self.sequence, weakref.ref(owner), action, success, failure)
        self.latest[key] = query.sequence
        self.pending[key] = query
        if owner not in self._owners:
            self._owners.add(owner)
            ident = id(owner)
            owner.destroyed.connect(lambda: self.cancel(ident))
        self._start()

    def cancel(self, ident, channel=None):
        for key in list(self.latest):
            if key[0] == ident and (channel is None or key[1] == channel):
                self.latest.pop(key, None)
                self.pending.pop(key, None)

    def _start(self):
        for key in list(self.pending):
            if len(self.active) >= self.workers:
                break
            if key in self.active:
                continue
            query = self.pending.pop(key)
            owner = query.owner()
            if owner is None or not isValid(owner):
                self.latest.pop(key, None)
                continue
            self.active.add(key)
            self.pool.start(_Worker(self, query))

    @Slot(object, object, object)
    def _deliver(self, query, result, error):
        self.active.discard(query.key)
        owner = query.owner()
        try:
            if self.latest.get(query.key) == query.sequence:
                self.latest.pop(query.key, None)
                if owner is not None and isValid(owner):
                    if error is None:
                        query.success(result)
                    else:
                        query.failure(error)
        finally:
            self._start()


def query_queue():
    app = QApplication.instance()
    if app is None:
        raise RuntimeError('Background queries require QApplication')
    if not hasattr(app, '_rail_query_queue'):
        app._rail_query_queue = QueryQueue(app)
    return app._rail_query_queue
