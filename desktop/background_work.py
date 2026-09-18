"""Run a finite preparation job with responsive progress and cooperative cancellation."""

from PySide6.QtCore import QThread, Signal, Qt, QEventLoop
from PySide6.QtWidgets import QProgressDialog


class WorkCancelled(ValueError):
    pass


class _Worker(QThread):
    progress = Signal(str)

    def __init__(self, action):
        super().__init__()
        self.action, self.result, self.error = action, None, None

    def report(self, text):
        if self.isInterruptionRequested():
            raise WorkCancelled("操作已取消；已有数据保留")
        self.progress.emit(text)

    def run(self):
        try:
            self.result = self.action(self.report)
        except BaseException as error:
            self.error = error


def prepare_with_progress(parent, title, action):
    worker = _Worker(action)
    dialog = QProgressDialog("正在准备…", "取消", 0, 0, parent)
    dialog.setWindowTitle(title)
    dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
    dialog.setMinimumWidth(440)
    dialog.setMinimumDuration(0)
    dialog.setAutoClose(False)
    worker.progress.connect(dialog.setLabelText)
    dialog.canceled.connect(worker.requestInterruption)
    worker.finished.connect(dialog.accept)
    loop = QEventLoop()
    worker.finished.connect(loop.quit)
    # Keep the thread owned while a canceled task observes its next checkpoint.
    dialog.canceled.connect(lambda: dialog.setLabelText("正在取消，保留原数据…"))
    dialog.show()
    worker.start()
    loop.exec()
    worker.wait()
    if worker.error:
        raise worker.error
    if worker.isInterruptionRequested():
        raise WorkCancelled("操作已取消；已有数据保留")
    return worker.result
