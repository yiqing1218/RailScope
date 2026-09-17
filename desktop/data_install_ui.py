"""Non-blocking download tool attached to the single desktop workspace."""

from pathlib import Path
from threading import Event
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QProgressBar,
    QComboBox,
)

try:
    from .components import Switch, switch_row, text_label
    from .data_install import install, Cancelled
except ImportError:
    from components import Switch, switch_row, text_label
    from data_install import install, Cancelled


class InstallerWorker(QThread):
    progress = Signal(dict)
    failed = Signal(str)
    completed = Signal()

    def __init__(self, root, force, parent=None, kind="metro"):
        super().__init__(parent)
        self.root, self.force = root, force
        self.kind = kind
        self.cancel = Event()

    def run(self):
        try:
            install(
                self.root, self.progress.emit, self.cancel, self.force, kind=self.kind
            )
        except Cancelled as error:
            self.failed.emit(str(error))
        except Exception as error:
            self.failed.emit(
                "未完成："
                + str(error)
                + "\n原有地铁数据未替换；可查看 data/logs 中的导入日志。"
            )
        else:
            self.completed.emit()


class DataDownloadDialog(QDialog):
    reload_requested = Signal()

    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.root = Path(root)
        self.worker = None
        self.setWindowTitle("全国轨道数据 · 自动下载与导入")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        layout.addWidget(text_label("获取全国轨道路网", "selectedTitle"))
        self.kind = QComboBox()
        self.kind.addItem("地铁 · 线路 / 站点 / 在建 / 真实边界", "metro")
        self.kind.addItem("国铁 · 轨道 / 站台 / 线路所 / 道岔", "rail")
        layout.addWidget(self.kind)
        layout.addWidget(
            text_label(
                "下载 → 文件校验 → 线路与站点 → 在建工程 → 真实站区 → 安全应用",
                "muted",
                True,
            )
        )
        info = QLabel(
            "数据源：Geofabrik / OpenStreetMap\n全国 PBF 约 1.5 GB，请预留至少 10 GB 磁盘空间。首次下载和导入可能需要较长时间。\n保留 OSM 原始属性与颜色；源数据没有绘制的站区不会推测补造。"
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        self.force = Switch(False)
        layout.addWidget(switch_row("重新下载最新快照（默认复用已有 PBF）", self.force))
        self.state = text_label(
            "尚未开始。已有完整 PBF 会直接复用；中断下载可以续传。", wrap=True
        )
        layout.addWidget(self.state)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        layout.addWidget(self.bar)
        self.detail = text_label("", "muted", True)
        layout.addWidget(self.detail)
        row = QHBoxLayout()
        self.start_button = QPushButton("开始 / 继续下载并导入")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start)
        row.addWidget(self.start_button)
        self.pause_button = QPushButton("暂停下载")
        self.pause_button.setEnabled(False)
        self.pause_button.clicked.connect(self.pause)
        row.addWidget(self.pause_button)
        layout.addLayout(row)
        self.apply_button = QPushButton("载入已完成的数据")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.reload_requested.emit)
        layout.addWidget(self.apply_button)
        close = QPushButton("收起（任务继续运行）")
        close.clicked.connect(self.hide)
        layout.addWidget(close)
        layout.addWidget(
            text_label(
                "© OpenStreetMap contributors · ODbL 1.0\n导入使用独立目录，失败不会替换当前路网；用户运行计划与目录设置不修改。",
                "muted",
                True,
            )
        )

    def start(self):
        if self.worker and self.worker.isRunning():
            return
        self.start_button.setEnabled(False)
        self.force.setEnabled(False)
        self.apply_button.setEnabled(False)
        self.pause_button.setEnabled(True)
        self.state.setText("正在连接数据源…")
        self.bar.setRange(0, 0)
        self.kind.setEnabled(False)
        self.worker = InstallerWorker(
            self.root, self.force.isChecked(), self, self.kind.currentData()
        )
        self.worker.progress.connect(self.update_progress)
        self.worker.failed.connect(self.state.setText)
        self.worker.completed.connect(lambda: self.apply_button.setEnabled(True))
        self.worker.finished.connect(self.finished_task)
        self.worker.start()

    def pause(self):
        if self.worker:
            self.worker.cancel.set()
        self.pause_button.setEnabled(False)
        self.state.setText("正在暂停当前下载请求，已下载部分会保留…")

    def update_progress(self, value):
        phase = value["phase"]
        self.state.setText(value["message"])
        self.pause_button.setEnabled(phase in ("download", "verify"))
        if phase == "download":
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(value["received"] / value["total"] * 1000))
            self.detail.setText(
                f"{value['received'] / 1024**2:,.1f} / {value['total'] / 1024**2:,.1f} MB"
            )
        elif phase == "import":
            self.bar.setRange(0, 0)
            self.detail.setText(
                f"导入阶段 {value['step']} / {value['steps']} · 可以收起此工具继续浏览地图"
            )
        elif phase == "done":
            self.bar.setRange(0, 1000)
            self.bar.setValue(1000)
            self.detail.setText("数据已完整校验并激活。请先保存运行计划，再点击载入。")

    def finished_task(self):
        self.start_button.setEnabled(True)
        self.force.setEnabled(True)
        self.kind.setEnabled(True)
        self.pause_button.setEnabled(False)
        if not self.apply_button.isEnabled():
            self.bar.setRange(0, 1000)
            self.bar.setValue(0)

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.hide()
            event.ignore()
        else:
            super().closeEvent(event)
