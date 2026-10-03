"""One editor for persisted StationTrack names and numbers."""

from dataclasses import replace
from uuid import uuid5, NAMESPACE_URL
from railscope.domain import Yard
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QMessageBox,
    QComboBox,
    QTabWidget, QWidget,
)

try:
    from .station_tracks import automatic_numbering
    from .station_track_semantics import TRACK_ROLE_LABELS
except ImportError:
    from station_tracks import automatic_numbering
    from station_track_semantics import TRACK_ROLE_LABELS


class StationTrackDialog(QDialog):
    def __init__(self, repo, parent=None, reference=None, context=()):
        super().__init__(parent)
        self.repo = repo
        self.context = context

        def order(key):
            number = repo.station_tracks[key].track_number or ""
            return (0, int(number), key) if number.isdigit() else (1, number, key)

        self.keys = sorted(repo.station_tracks, key=order)
        self.setWindowTitle(
            next(iter(repo.stations.values())).name + " · 股道名称与编号"
        )
        self.resize(1250, 680)
        layout = QVBoxLayout(self)
        note = QLabel(
            "每行引用真实连续物理区间。分场名称、铁路体系和业务线路仅填写明确归属，保存到工作区。未填写的归属在站场导出中提示待核对。正式股道号仅填写有来源的编号。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索股道名称、编号或稳定 ID")
        layout.addWidget(self.search)
        self.table = QTableWidget(len(self.keys), 10)
        self.table.setHorizontalHeaderLabels(
            [
                "名称",
                "正式股道号",
                "用途（来源分类）",
                "长度 / m",
                "稳定股道 ID",
                "分场名称",
                "铁路体系",
                "业务线路归属",
                "分场类型",
                "站台面编号（参考）",
            ]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.ResizeToContents
        )
        tabs = QTabWidget()
        layout.addWidget(tabs)
        actual = QWidget()
        QVBoxLayout(actual).addWidget(self.table)
        tabs.addTab(actual, "实际股道与分场")
        if reference:
            tabs.addTab(self.reference_panel(reference), "网站分场对应")
        self.fill()
        self.search.textChanged.connect(self.filter)
        bar = QHBoxLayout()
        auto = QPushButton("整理显示序号")
        auto.clicked.connect(self.number)
        bar.addWidget(auto)
        bar.addStretch()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        bar.addWidget(buttons)
        layout.addLayout(bar)

    def reference_panel(self, profile):
        try:
            from .reference_integration import yard_bindings, apply_yard_binding
        except ImportError:
            from reference_integration import yard_bindings, apply_yard_binding
        panel = QWidget()
        layout = QVBoxLayout(panel)
        note = QLabel('来源：' + profile['source_url'] + '\n网站“站台”指站台面。下面按已有正式股道号提出同号候选，需确认对应关系；未编号股道可手工选择。保存后引用实际稳定股道 ID。')
        note.setWordWrap(True)
        layout.addWidget(note)
        bindings = yard_bindings(self.repo, profile, self.context)
        table = QTableWidget(len(bindings), 6)
        table.setHorizontalHeaderLabels(['确认', '分场 / 范围', '来源编号', '适用线路', '实际股道', '对应状态'])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        statuses = {'platform_face_track_reference':'同号且与真实站台相邻（参考）',
                    'reference_number_match':'同号候选，待确认', 'ambiguous_track_number':'正式股道号重复',
                    'ambiguous_source_scope':'多个来源范围冲突', 'missing_track_number':'缺少同号股道',
                    'conflicting_railway_class':'来源分场与实际铁路体系冲突',
                    'missing_number_range':'来源未标注范围'}
        for row, binding in enumerate(bindings):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check.setCheckState(Qt.CheckState.Unchecked)
            table.setItem(row,0,check)
            yard = binding['yard']
            for column, value in [(1,yard['name']), (2,('站台面 ' if yard.get('number_kind')=='platform_face' else '股道 ')+str(binding['number'] or '未知')),
                                  (3,yard.get('lines','')), (5,statuses[binding['status']])]:
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row,column,item)
            tracks = QComboBox()
            tracks.addItem('选择实际股道…', None)
            for key in self.keys:
                t = self.repo.station_tracks[key]
                tracks.addItem((t.track_number+'道 · ' if t.track_number else '')+t.name+' · '+key[-8:], key)
            tracks.setCurrentIndex(max(0,tracks.findData(binding['track_id'])))
            table.setCellWidget(row,4,tracks)
        layout.addWidget(table)
        select = QPushButton('勾选无冲突的同号候选（仍需确认）')
        select.clicked.connect(lambda: [table.item(row,0).setCheckState(Qt.CheckState.Checked)
            for row,binding in enumerate(bindings) if binding['status']=='reference_number_match'
            and not self.repo.station_tracks[binding['track_id']].yard_id])
        layout.addWidget(select)
        apply = QPushButton('确认勾选的对应关系并填入分场')
        layout.addWidget(apply)

        def commit():
            selected = []
            for row, binding in enumerate(bindings):
                if table.item(row,0).checkState()==Qt.CheckState.Checked:
                    key = table.cellWidget(row,4).currentData()
                    if not key:
                        QMessageBox.warning(self,'对应未应用','请为勾选的来源编号选择实际股道')
                        return
                    selected.append((binding,key))
            if len({key for _,key in selected})!=len(selected):
                QMessageBox.warning(self,'对应未应用','同一实际股道不能同时对应多条来源记录')
                return
            try:
                self.collect()
                for binding,key in selected:
                    apply_yard_binding(self.repo,profile,binding,key,confirmed=True)
                self.fill()
                self.filter(self.search.text())
            except ValueError as error:
                QMessageBox.warning(self,'对应未应用',str(error))
        apply.clicked.connect(commit)
        self.reference_table = table
        return panel

    def fill(self):
        for row, key in enumerate(self.keys):
            t = self.repo.station_tracks[key]
            for column, value in enumerate(
                (
                    t.name,
                    t.track_number or "",
                    TRACK_ROLE_LABELS.get(t.track_role, "用途待核实"),
                    f"{t.length_m:.1f}",
                    key,
                )
            ):
                item = QTableWidgetItem(value)
                alias = t.provenance.get("display_alias", {}).get("value")
                if alias:
                    item.setToolTip(alias + "（仅供显示，非正式股道号）")
                if column > 1:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, item)
            yard = self.repo.yards.get(t.yard_id)
            self.table.setItem(
                row,
                5,
                QTableWidgetItem(
                    yard.name
                    if yard
                    else t.provenance.get("yard", {}).get("name") or t.yard_id or ""
                ),
            )
            classes = QComboBox()
            for value, label in [
                ("unknown", "待核对"),
                ("high_speed", "高速"),
                ("conventional", "普速"),
                ("freight", "货运"),
                ("other", "其他"),
                ("industrial", "工业"),
            ]:
                classes.addItem(label, value)
            classes.setCurrentIndex(max(0, classes.findData(t.railway_class)))
            self.table.setCellWidget(row, 6, classes)
            lines = QComboBox()
            lines.addItem("归属待核对", None)
            for line in sorted(self.repo.lines.values(), key=lambda l: (l.name, l.id)):
                lines.addItem(line.name, line.id)
            if (
                t.infrastructure_line_id
                and lines.findData(t.infrastructure_line_id) < 0
            ):
                lines.addItem(
                    "原归属需核对：" + t.infrastructure_line_id,
                    t.infrastructure_line_id,
                )
            lines.setCurrentIndex(max(0, lines.findData(t.infrastructure_line_id)))
            self.table.setCellWidget(row, 7, lines)
            types = QComboBox()
            for value, label in [
                ("unknown", "待核对"),
                ("high_speed", "高速场"),
                ("conventional", "普速场"),
                ("intercity", "城际场"),
                ("freight", "货运场"),
                ("mixed", "综合场"),
                ("other", "其他"),
            ]:
                types.addItem(label, value)
            types.setCurrentIndex(
                max(0, types.findData(yard.yard_type if yard else "unknown"))
            )
            self.table.setCellWidget(row, 8, types)
            face = QTableWidgetItem(t.platform_number or '')
            face.setFlags(face.flags() & ~Qt.ItemFlag.ItemIsEditable)
            face.setToolTip('站台面编号；参考对应不等于实体站台序号。来源与核验状态保存在股道记录中。')
            self.table.setItem(row,9,face)

    def collect(self):
        updates = {}
        yards = {}
        for row, key in enumerate(self.keys):
            name = self.table.item(row, 0).text().strip()
            number = self.table.item(row, 1).text().strip()
            if not name:
                raise ValueError("股道名称不能为空")
            old = self.repo.station_tracks[key]
            yard_name = self.table.item(row, 5).text().strip()
            cls = self.table.cellWidget(row, 6).currentData()
            line = self.table.cellWidget(row, 7).currentData()
            yard_type = self.table.cellWidget(row, 8).currentData()
            old_yard = self.repo.yards.get(old.yard_id)
            previous_name = (
                old_yard.name
                if old_yard
                else old.provenance.get("yard", {}).get("name") or old.yard_id or ""
            )
            previous_type = old_yard.yard_type if old_yard else "unknown"
            if not yard_name and yard_type != "unknown":
                raise ValueError("填写分场类型前请先填写分场名称")
            if (name, number, yard_name, cls, line, yard_type) != (
                old.name,
                old.track_number or "",
                previous_name,
                old.railway_class,
                old.infrastructure_line_id,
                previous_type,
            ):
                provenance = {
                    **old.provenance,
                    "name": {
                        "source": "workspace_override",
                        "verification_status": "user_named",
                    },
                }
                if number != (old.track_number or ""):
                    provenance["track_number"] = {
                        "source": "workspace_override",
                        "evidence": number or None,
                        "verification_status": "user_verified",
                    }
                yard_id = (
                    old.yard_id
                    if yard_name == previous_name
                    else (
                        "Y-"
                        + uuid5(
                            NAMESPACE_URL, old.station_id + "/yard/" + yard_name
                        ).hex
                        if yard_name
                        else None
                    )
                )
                if yard_name != previous_name or cls != old.railway_class:
                    provenance["yard"] = {
                        "name": yard_name,
                        "railway_class": cls,
                        "source": "workspace_override",
                        "verification_status": "user_edited",
                    }
                if line != old.infrastructure_line_id:
                    provenance["line_membership"] = {
                        "line_id": line,
                        "source": "workspace_override",
                        "verification_status": "user_edited",
                    }
                updates[key] = replace(
                    old,
                    name=name,
                    track_number=number or None,
                    yard_id=yard_id,
                    railway_class=cls,
                    infrastructure_line_id=line,
                    verification_status="user_named",
                    provenance=provenance,
                )
                if yard_id:
                    value = Yard(
                        yard_id,
                        old.station_id,
                        yard_name,
                        yard_type,
                        source_id="workspace_override",
                        snapshot_id=old.snapshot_id,
                        verification_status="user_edited",
                        provenance={
                            "source": "workspace_override",
                            "railway_class": cls,
                        },
                    )
                    if yard_id in yards and yards[yard_id].yard_type != yard_type:
                        raise ValueError("同一分场的类型不一致，请统一填写")
                    yards[yard_id] = value
        self.repo.station_tracks.update(updates)
        self.repo.yards.update(yards)

    def number(self):
        try:
            self.collect()
            automatic_numbering(self.repo)
            self.fill()
            self.filter(self.search.text())
        except ValueError as error:
            QMessageBox.warning(self, "编号未完成", str(error))

    def filter(self, text):
        for row in range(len(self.keys)):
            self.table.setRowHidden(
                row,
                text.casefold()
                not in " ".join(
                    self.table.item(row, c).text() for c in range(6)
                ).casefold(),
            )

    def save(self):
        try:
            self.collect()
            self.accept()
        except ValueError as error:
            QMessageBox.warning(self, "股道未保存", str(error))
