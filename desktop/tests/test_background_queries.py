from threading import Event, get_ident
from types import SimpleNamespace

from PySide6.QtCore import QTimer, QObject
from PySide6.QtWidgets import QWidget

from desktop.background_queries import QueryQueue
from desktop.corridor_ui import SearchChoice
from desktop.rail_connection_ui import StationConnectionSelector


def test_queue_keeps_only_latest_pending_result_and_gui_remains_live(qtbot):
    queue, owner = QueryQueue(workers=1), QWidget()
    qtbot.addWidget(owner)
    gate, started = Event(), Event()
    results, worker_threads, ticks = [], [], []
    gui_thread = get_ident()
    timer = QTimer(owner)
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start()
    def slow():
        worker_threads.append(get_ident())
        started.set()
        assert gate.wait(3)
        return 'stale'
    queue.submit(owner, 'search', slow, results.append, lambda e: results.append(e))
    qtbot.waitUntil(started.is_set)
    for i in range(100):
        queue.submit(owner, 'search', lambda i=i: i, results.append, results.append)
    assert len(queue.pending) == 1 and len(queue.active) == 1
    qtbot.waitUntil(lambda: len(ticks) >= 4)
    gate.set()
    qtbot.waitUntil(lambda: results == [99])
    assert worker_threads != [gui_thread]
    assert not queue.pending and not queue.active


def test_closed_owner_cannot_receive_result_and_failure_delivers_on_gui(qtbot):
    queue, owner = QueryQueue(workers=1), QObject()
    gate, started = Event(), Event()
    results = []
    def slow():
        started.set()
        gate.wait(3)
    queue.submit(owner, 'read', slow, results.append, results.append)
    qtbot.waitUntil(started.is_set)
    owner.deleteLater()
    qtbot.wait(10)
    gate.set()
    qtbot.waitUntil(lambda: not queue.active)
    assert results == []
    another = QWidget()
    qtbot.addWidget(another)
    def fail():
        raise ValueError('index unavailable')
    queue.submit(another, 'read', fail, results.append,
                 lambda e: results.append((str(e), get_ident())))
    qtbot.waitUntil(lambda: bool(results))
    assert results == [('index unavailable', get_ident())]


def test_station_fields_can_save_before_links_finish_without_erasing_anchors(qtbot):
    gate, started = Event(), Event()
    def connections(endpoint):
        started.set()
        assert gate.wait(3)
        return [{'id': 'RL-1', 'name': '甲线'}]
    library = SimpleNamespace(connected_lines=connections, search_lines=lambda *a, **k: [])
    widget = StationConnectionSelector(library, 'station:node/1', async_load=True)
    qtbot.addWidget(widget)
    qtbot.waitUntil(started.is_set)
    assert widget.connections() is None and not widget.search.isEnabled()
    gate.set()
    qtbot.waitUntil(widget.search.isEnabled)
    assert widget.line_ids() == ['RL-1'] and widget.connections() is None


def test_async_picker_preserves_selection_and_discards_old_text(qtbot):
    gate, started = Event(), Event()
    def search(query):
        if query == 'old':
            started.set()
            assert gate.wait(3)
        return [(query or 'saved', query or '保留原选择')]
    widget = SearchChoice(search, '搜索', 'saved', '保留原选择', async_query=True)
    qtbot.addWidget(widget)
    widget._choices_dirty = True
    widget.load_choices()
    qtbot.waitUntil(lambda: not widget._choices_dirty)
    assert widget.currentData() == 'saved'
    widget.setEditText('old')
    widget.find_results()
    qtbot.waitUntil(started.is_set)
    widget.text_edited('new')
    widget.find_results()
    gate.set()
    qtbot.waitUntil(lambda: widget.count() == 1 and widget.itemData(0) == 'new')
    widget._search_timer.stop()
    assert widget.currentData() is None and widget.currentText() == 'new'


def test_hidden_picker_stops_debounce_and_restarts_latest_text_on_reopen(qtbot):
    calls = []
    widget = SearchChoice(lambda q: calls.append(q) or [(q, q)], '搜索', async_query=True)
    qtbot.addWidget(widget)
    widget.show()
    widget.text_edited('latest')
    widget.hide()
    assert not widget._search_timer.isActive()
    qtbot.wait(250)
    assert calls == []
    widget.show()
    qtbot.waitUntil(lambda: calls == ['latest'])
    qtbot.waitUntil(lambda: widget.itemData(0) == 'latest')
