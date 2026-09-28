"""One editor for persisted StationTrack names and numbers."""
from dataclasses import replace
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QMessageBox)
try:
    from .station_tracks import automatic_numbering
    from .station_track_semantics import TRACK_ROLE_LABELS
except ImportError:
    from station_tracks import automatic_numbering
    from station_track_semantics import TRACK_ROLE_LABELS


class StationTrackDialog(QDialog):
    def __init__(self, repo, parent=None):
        super().__init__(parent)
        self.repo=repo
        def order(key):
            number=repo.station_tracks[key].track_number or ''
            return (0,int(number),key) if number.isdigit() else (1,number,key)
        self.keys=sorted(repo.station_tracks, key=order)
        self.setWindowTitle(next(iter(repo.stations.values())).name + ' · 股道名称与编号')
        self.resize(960,620)
        layout=QVBoxLayout(self)
        note=QLabel('每行股道可引用多个连续物理区间。正式股道号仅填写有来源的编号；自动整理只生成显示序号。')
        note.setWordWrap(True);layout.addWidget(note)
        self.search=QLineEdit();self.search.setPlaceholderText('搜索股道名称、编号或稳定 ID')
        layout.addWidget(self.search)
        self.table=QTableWidget(len(self.keys),5)
        self.table.setHorizontalHeaderLabels(['名称','正式股道号','用途（来源分类）','长度 / m','稳定股道 ID'])
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4,QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)
        self.fill()
        self.search.textChanged.connect(self.filter)
        bar=QHBoxLayout();auto=QPushButton('整理显示序号');auto.clicked.connect(self.number);bar.addWidget(auto);bar.addStretch()
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save);buttons.rejected.connect(self.reject);bar.addWidget(buttons);layout.addLayout(bar)

    def fill(self):
        for row,key in enumerate(self.keys):
            t=self.repo.station_tracks[key]
            for column,value in enumerate((t.name,t.track_number or '',TRACK_ROLE_LABELS.get(t.track_role, '用途待核实'),f'{t.length_m:.1f}',key)):
                item=QTableWidgetItem(value)
                alias = t.provenance.get('display_alias', {}).get('value')
                if alias:
                    item.setToolTip(alias + '（仅供显示，非正式股道号）')
                if column>1:item.setFlags(item.flags()&~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row,column,item)

    def collect(self):
        for row,key in enumerate(self.keys):
            name=self.table.item(row,0).text().strip();number=self.table.item(row,1).text().strip()
            if not name:raise ValueError('股道名称不能为空')
            old=self.repo.station_tracks[key]
            if (name,number)!=(old.name,old.track_number or ''):
                provenance = {**old.provenance, 'name': {'source': 'workspace_override', 'verification_status': 'user_named'}}
                if number != (old.track_number or ''):
                    provenance['track_number'] = {'source': 'workspace_override', 'evidence': number or None,
                                                  'verification_status': 'user_verified'}
                self.repo.station_tracks[key]=replace(old,name=name,track_number=number or None,
                    verification_status='user_named', provenance=provenance)

    def number(self):
        try:
            self.collect();automatic_numbering(self.repo);self.fill();self.filter(self.search.text())
        except ValueError as error:QMessageBox.warning(self,'编号未完成',str(error))

    def filter(self,text):
        for row in range(len(self.keys)):
            self.table.setRowHidden(row,text.casefold() not in ' '.join(self.table.item(row,c).text() for c in range(5)).casefold())

    def save(self):
        try:self.collect();self.accept()
        except ValueError as error:QMessageBox.warning(self,'股道未保存',str(error))
