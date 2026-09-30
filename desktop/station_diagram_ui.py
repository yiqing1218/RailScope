"""Dedicated station-diagram export controls with a real rendered preview."""
from dataclasses import asdict
from collections import Counter
import json
import sqlite3
from pathlib import Path

from PySide6.QtCore import QByteArray, QTimer, Qt
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                              QFormLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSpinBox,
                              QTabWidget, QTextEdit, QVBoxLayout, QWidget, QTableWidget, QTableWidgetItem, QLineEdit)

try:
    from .station_diagram_layout import DiagramOptions, edge_role, build_layout, system_name
    from .station_schematic import station_svg, ensure_export_font, port_destination, station_projection
except ImportError:
    from station_diagram_layout import DiagramOptions, edge_role, build_layout, system_name
    from station_schematic import station_svg, ensure_export_font, port_destination, station_projection


class DiagramPreview(QSvgWidget):
    """Use the export renderer in a raster preview, preserving page proportions."""
    def load(self, data):
        super().load(data)
        size = self.renderer().defaultSize().scaled(2400,2400,Qt.AspectRatioMode.KeepAspectRatio)
        self.page = QImage(size, QImage.Format.Format_ARGB32)
        self.page.fill('white')
        painter = QPainter(self.page)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.renderer().render(painter)
        painter.end()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.white)
        if hasattr(self,'page'):
            target = self.page.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            x,y = (self.width()-target.width())//2,(self.height()-target.height())//2
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(x,y,self.page.scaled(target,Qt.AspectRatioMode.KeepAspectRatio,
                                                  Qt.TransformationMode.SmoothTransformation))
        painter.end()


class StationDiagramDialog(QDialog):
    def __init__(self, repo, context=(), station_info=None, parent=None, settings_path=None, reload_callback=None):
        super().__init__(parent)
        self.repo, self.context, self.info = repo, context, station_info or {}
        self.settings_path = Path(settings_path) if settings_path else None
        self.reload_callback = reload_callback
        self.loaded_depth = None
        self.setWindowTitle('站场示意图导出设置')
        self.resize(1180, 780)
        self.controls = {}
        defaults = DiagramOptions()
        self.settings_warning = ''
        if self.settings_path and self.settings_path.exists():
            try:
                values = json.loads(self.settings_path.read_text(encoding='utf-8'))
                # Discard retired controls from the interrupted first design.
                values = {key:value for key,value in values.items() if key in asdict(defaults)}
                if values.get('outside_compression',8) < 1:
                    values['outside_compression'] = 8
                if 'line_overrides' not in values:
                    values['remove_common_bend'] = False
                if values.get('color_scheme') not in ('systems','mono'):
                    values['color_scheme'] = 'systems'
                defaults = DiagramOptions(**values)
            except (OSError, ValueError, TypeError):
                self.settings_warning = '上次设置无效，已恢复默认设置。'
        self.line_rules = dict(defaults.line_overrides)
        self.port_rules = dict(defaults.port_overrides)
        main = QVBoxLayout(self)
        hint = QLabel('沿站台方向压缩；自动判断仅作图面参考。可在“逐线编辑 / 两端文字”中修正归属、粗细、颜色、标注和延长，不修改原始铁路数据。')
        hint.setWordWrap(True)
        main.addWidget(hint)
        row = QHBoxLayout()
        main.addLayout(row, 1)
        tabs = QTabWidget()
        tabs.setMinimumWidth(400)
        row.addWidget(tabs, 0)
        preview_column = QVBoxLayout()
        row.addLayout(preview_column, 1)
        self.preview = DiagramPreview()
        self.preview.setMinimumSize(420, 300)
        preview_column.addWidget(self.preview, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        preview_column.addWidget(self.status)
        refresh = QPushButton('刷新预览')
        refresh.clicked.connect(self.refresh_preview)
        preview_column.addWidget(refresh)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.refresh_preview)

        def page(name):
            widget = QWidget()
            form = QFormLayout(widget)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(widget)
            tabs.addTab(scroll, name)
            return form

        def check(form, name, title):
            control = QCheckBox()
            control.setChecked(getattr(defaults, name))
            control.toggled.connect(lambda *_: self.timer.start())
            form.addRow(title, control)
            self.controls[name] = control

        def number(form, name, title, low, high, step=.05, integer=False):
            control = QSpinBox() if integer else QDoubleSpinBox()
            control.setRange(low, high)
            control.setSingleStep(step)
            control.setValue(getattr(defaults, name))
            control.valueChanged.connect(lambda *_: self.timer.start())
            form.addRow(title, control)
            self.controls[name] = control

        def combo(form, name, title, choices):
            control = QComboBox()
            for text, value in choices:
                control.addItem(text, value)
            control.setCurrentIndex(control.findData(getattr(defaults, name)))
            control.currentIndexChanged.connect(lambda *_: self.timer.start())
            form.addRow(title, control)
            self.controls[name] = control

        form = page('布局')
        check(form, 'auto_rotate', '自动旋正到站场主轴')
        combo(form, 'orientation', '构图方向', [('横向', 'landscape'), ('纵向', 'portrait')])
        check(form, 'show_north', '真实北向指北针')
        check(form, 'remove_common_bend', '去除共同弯曲（实验，默认关闭）')
        check(form, 'align_main_outlets', '水平端口示意延长到左右统一边界')
        number(form, 'station_compression', '站场区域轴向压缩倍数', 1, 12, .25)
        number(form, 'outside_compression', '站外区域轴向压缩倍数', 1, 30, .5)
        number(form, 'platform_width', '站台符号宽度倍数', .5, 4, .1)
        number(form, 'margin', '全图留白（图面单位）', 10, 240, 5)
        form.addRow(QLabel('压缩倍数越大，轴向长度越短。画布保持固定；站外压缩加大，会显示更长的站外区间。站场、站外共用总图面尺度。'))

        form = page('内容')
        for name, title in [('show_main', '正线'), ('show_station', '站线 / 到发线 / 辅助线'),
                            ('show_connectors', '联络线'), ('show_outer_main', '外围关联主线'),
                            ('show_outer_connectors', '外围联络线'), ('include_construction', '包含在建铁路（虚线）'),
                            ('show_platforms', '真实来源站台符号'), ('show_legend', '图例'),
                            ('show_title', '站名标题'), ('show_endpoints', '正线端口名称与通达城市')]:
            check(form, name, title)
        number(form, 'topology_depth', '向外追踪连接层数', 0, 64, 1, True)
        form.addRow(QLabel('外围跟随真实连接的主线和联络线，不按附近几何凑线路。更高层数可扩大数据范围；超出画布的线路只绘制到边界。'))
        form.addRow(QLabel('只有收束后的正线端口标注名称；不标联络线，不绘制标注引线和道岔说明。'))

        def editor_page(title, headers):
            widget = QWidget()
            column = QVBoxLayout(widget)
            note = QLabel('空白值沿用自动判断。仅修改图面；线路按稳定 RailScope ID 保存。' if title == '逐线编辑' else
                          '仅列出当前图面可用的外端口。文字留空沿用线路名与去向；“不标”隐藏文字。“延长”只增加示意线，不代表真实铁路延伸。')
            note.setWordWrap(True)
            column.addWidget(note)
            search = QLineEdit()
            search.setPlaceholderText('搜索线路名称或 RailScope ID')
            column.addWidget(search)
            table = QTableWidget(0, len(headers))
            table.setHorizontalHeaderLabels(headers)
            table.setMinimumWidth(520)
            table.setAlternatingRowColors(True)
            table.itemChanged.connect(lambda *_: self.timer.start())
            table.search = search
            search.textChanged.connect(lambda text: self.filter_editor(table, text))
            column.addWidget(table)
            reset = QPushButton('本页恢复自动判断')
            column.addWidget(reset)
            tabs.addTab(widget, title)
            return table, reset

        self.line_table, line_reset = editor_page('逐线编辑', ['线路 / ID', '显示', '角色', '颜色归属系统', '颜色 #RRGGBB', '线宽', '端口标注'])
        self.port_table, port_reset = editor_page('两端文字', ['线路端口', '标注', '自定义文字（用 \\n 换行）', '示意延长', '水平偏移', '垂直偏移'])
        line_reset.clicked.connect(lambda: self.reset_editor('line'))
        port_reset.clicked.connect(lambda: self.reset_editor('port'))

        form = page('样式')
        for name, title in [('main_width', '正线线宽'), ('station_width', '站线线宽'), ('connector_width', '联络线线宽')]:
            number(form, name, title, .5, 15, .5)
        number(form, 'title_size', '标题字号', 20, 90, 2)
        number(form, 'label_size', '标注字号', 12, 48, 1)
        combo(form, 'platform_fill', '站台填充', [('浅灰', 'gray'), ('淡绿', 'tint'), ('白底轮廓', 'outline')])
        combo(form, 'color_scheme', '颜色编码', [('线路系统与相连站线', 'systems'), ('单色打印', 'mono')])
        self.colors = QTextEdit()
        self.colors.setMaximumHeight(140)
        self.colors.setPlaceholderText('每行：线路系统名称或 RailScope 线路 ID = #RRGGBB\n例如：京沪高速线 = #2463a3')
        self.colors.setPlainText('\n'.join(f'{k} = {v}' for k,v in defaults.color_overrides.items()))
        self.colors.textChanged.connect(self.timer.start)
        form.addRow('手动颜色映射', self.colors)
        form = page('输出')
        number(form, 'width', '输出长边（像素 / SVG 单位）', 800, 12000, 100, True)
        number(form, 'aspect_ratio', '长边 / 短边比', 1.1, 3, .05)
        number(form, 'dpi', '分辨率（DPI）', 72, 1200, 50, True)
        combo(form, 'output_format', '文件格式', [('SVG 矢量', 'svg'), ('PNG 图片', 'png'), ('PDF 矢量', 'pdf')])
        form.addRow(QLabel('PNG 像素尺寸等于输出尺寸；DPI 决定打印尺寸。SVG / PDF 保留矢量轨道和文字。'))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('导出…')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        main.addWidget(buttons)
        self.refresh_preview()

    def options(self):
        values = {}
        for name, control in self.controls.items():
            values[name] = control.isChecked() if isinstance(control, QCheckBox) else control.currentData() if isinstance(control, QComboBox) else control.value()
        colors = {}
        for line in self.colors.toPlainText().splitlines():
            if line.strip():
                key, sep, value = line.partition('=')
                if not sep or not key.strip():
                    raise ValueError('颜色映射须为：线路名称 = #RRGGBB')
                colors[key.strip()] = value.strip()
        self.read_editors()
        return DiagramOptions(**values, color_overrides=colors,
                              line_overrides=self.line_rules, port_overrides=self.port_rules)

    def editor_combo(self, choices, value):
        widget = QComboBox()
        for title, data in choices:
            widget.addItem(title, data)
        widget.setCurrentIndex(max(0, widget.findData(value)))
        widget.currentIndexChanged.connect(lambda *_: self.timer.start())
        return widget

    def readonly_item(self, text, key):
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        item.setData(Qt.ItemDataRole.UserRole, key)
        item.setToolTip(key)
        return item

    def filter_editor(self, table, text):
        text = text.strip().lower()
        for row in range(table.rowCount()):
            item = table.item(row,0)
            available = item.data(Qt.ItemDataRole.UserRole+1) is not False
            table.setRowHidden(row,not available or text not in (item.text()+' '+item.toolTip()).lower())

    def read_editors(self):
        for row in range(self.line_table.rowCount()):
            key = self.line_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            get = lambda col: self.line_table.item(row, col).text().strip()
            rule = {'visible': self.line_table.cellWidget(row,1).currentData(),
                    'role': self.line_table.cellWidget(row,2).currentData()}
            for col, name in ((3,'system'),(4,'color'),(5,'width')):
                if get(col):
                    rule[name] = float(get(col)) if name == 'width' else get(col)
            label = self.line_table.cellWidget(row,6).currentData()
            if label is not None:
                rule['label'] = label
            if rule == {'visible':True,'role':'auto'}:
                self.line_rules.pop(key, None)
            else:
                self.line_rules[key] = rule
        for row in range(self.port_table.rowCount()):
            key = self.port_table.item(row,0).data(Qt.ItemDataRole.UserRole)
            rule = {}
            for col, name in ((1,'visible'),(3,'extend')):
                value = self.port_table.cellWidget(row,col).currentData()
                if value is not None:
                    rule[name] = value
            text = self.port_table.item(row,2).text()
            if text:
                rule['text'] = text.replace('\\n','\n')
            for col, name in ((4,'dx'),(5,'dy')):
                text = self.port_table.item(row,col).text().strip()
                if text:
                    rule[name] = float(text)
            if rule:
                self.port_rules[key] = rule
            else:
                self.port_rules.pop(key,None)

    def sync_editors(self, layout):
        counts = Counter(e.infrastructure_line_id for e in self.repo.edges.values())
        roles = {'main':'正线','station':'站线','connector':'联络 / 渡线','auxiliary':'辅助线'}
        choices = [('自动', 'auto')] + [(v,k) for k,v in roles.items()]
        lines = {self.line_table.item(row,0).data(Qt.ItemDataRole.UserRole) for row in range(self.line_table.rowCount())}
        self.line_table.blockSignals(True)
        for key in sorted(counts, key=lambda key: (self.repo.lines[key].name.startswith('未命名') if key in self.repo.lines else True,
                                                  self.repo.lines[key].name if key in self.repo.lines else '',str(key))):
            if key in lines or key not in self.repo.lines:
                continue
            line = self.repo.lines[key]
            edge = next(e for e in self.repo.edges.values() if e.infrastructure_line_id == key)
            row = self.line_table.rowCount()
            self.line_table.insertRow(row)
            item = self.readonly_item(f'{line.name} ({counts[key]}段)\n{key}',key)
            item.setToolTip(f'{key}\n自动角色：{roles[edge_role(self.repo,edge)]}；含多个角色时可按实际需要修正')
            self.line_table.setItem(row,0,item)
            rule = self.line_rules.get(key,{})
            for col, values, value in ((1,[('显示',True),('隐藏',False)],rule.get('visible',True)),
                    (2,choices,rule.get('role','auto')),
                    (6,[('自动',None),('强制标',True),('不标',False)],rule.get('label'))):
                self.line_table.setCellWidget(row,col,self.editor_combo(values,value))
            for col,name in ((3,'system'),(4,'color'),(5,'width')):
                self.line_table.setItem(row,col,QTableWidgetItem(str(rule.get(name,''))))
            self.line_table.setRowHeight(row,50)
        self.line_table.blockSignals(False)
        keys = {self.port_table.item(row,0).data(Qt.ItemDataRole.UserRole) for row in range(self.port_table.rowCount())}
        current = {port['key']:port for port in layout.ports}
        self.port_table.blockSignals(True)
        local,*_ = station_projection(self.repo)
        for port in layout.ports:
            key = port['key']
            if key in keys:
                continue
            row = self.port_table.rowCount()
            self.port_table.insertRow(row)
            rule = self.port_rules.get(key,{})
            side = '左' if port['side']=='left' else '右'
            self.port_table.setItem(row,0,self.readonly_item(f'{system_name(port["line"])} · {side}',key))
            for col,name,titles in ((1,'visible',('标注','不标')),(3,'extend',('延长','不延长'))):
                self.port_table.setCellWidget(row,col,self.editor_combo([('自动',None),(titles[0],True),(titles[1],False)],rule.get(name)))
            for col,name in ((2,'text'),(4,'dx'),(5,'dy')):
                self.port_table.setItem(row,col,QTableWidgetItem(str(rule.get(name,'')).replace('\n','\\n')))
            destination = port_destination({**port,'side':'right' if port['vector'][0]>=0 else 'left'},self.info,local)
            self.port_table.item(row,2).setToolTip('自动文字：'+system_name(port['line'])+ (' / 往'+destination if destination else ''))
            keys.add(key)
        for row in range(self.port_table.rowCount()):
            item = self.port_table.item(row,0)
            item.setData(Qt.ItemDataRole.UserRole+1,item.data(Qt.ItemDataRole.UserRole) in current)
        self.port_table.blockSignals(False)
        self.line_table.resizeColumnsToContents()
        self.port_table.resizeColumnsToContents()
        self.port_table.setColumnWidth(2,230)
        self.filter_editor(self.line_table,self.line_table.search.text())
        self.filter_editor(self.port_table,self.port_table.search.text())

    def reset_editor(self, kind):
        table = self.line_table if kind == 'line' else self.port_table
        (self.line_rules if kind == 'line' else self.port_rules).clear()
        table.setRowCount(0)
        self.refresh_preview()

    def refresh_preview(self):
        try:
            options = self.options()
            if self.reload_callback and self.loaded_depth != options.topology_depth:
                self.status.setText('正在读取站外连接…')
                self.repo, self.context, self.info = self.reload_callback(options.topology_depth)
                self.loaded_depth = options.topology_depth
            ensure_export_font()
            svg = station_svg(self.repo, self.context, station_info=self.info, options=options)
            self.preview.load(QByteArray(svg.encode('utf-8')))
            self.sync_editors(build_layout(self.repo,self.context,options))
            width, height = options.canvas_size
            self.status.setText(self.settings_warning + f'预览：{width} × {height}，{options.dpi} DPI。压缩仅沿站台轴；原始拓扑与几何保留。')
            return True
        except (OSError, ValueError, KeyError, sqlite3.Error) as error:
            self.status.setText(str(error))
            return False

    def accept(self):
        if self.refresh_preview():
            super().accept()

    def save_settings(self, options):
        if self.settings_path:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.settings_path.with_suffix('.tmp')
            temp.write_text(json.dumps(asdict(options), ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(self.settings_path)
