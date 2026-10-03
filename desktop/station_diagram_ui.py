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
                              QTabWidget, QTextEdit, QVBoxLayout, QWidget, QTableWidget, QTableWidgetItem, QLineEdit,
                              QAbstractItemView, QInputDialog)

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
                if values.get('layout_algorithm') != 'yard_relative_linear_outlets_v3':
                    values['layout_mode']='yard_relative'
                    values['remove_common_bend']=True
                    if values.get('station_compression') in (1,4):values['station_compression']=2.5
                if values.get('layout_algorithm') not in ('source_shape_shared_transform_v2','yard_relative_linear_outlets_v3'):
                    if values.get('platform_width') == 1.4:
                        values['platform_width'] = 1
                # Discard retired controls from the interrupted first design.
                values = {key:value for key,value in values.items() if key in asdict(defaults)}
                if values.get('outside_compression',8) < 1:
                    values['outside_compression'] = 8
                if values.get('color_scheme') not in ('systems','mono'):
                    values['color_scheme'] = 'systems'
                values['station_width'] = values['connector_width'] = values.get('main_width',3)
                defaults = DiagramOptions(**values)
            except (OSError, ValueError, TypeError):
                self.settings_warning = '上次设置无效，已恢复默认设置。'
        self.line_rules = dict(defaults.line_overrides)
        self.port_rules = dict(defaults.port_overrides)
        self.yard_rules = dict(defaults.yard_overrides)
        self.track_rules = dict(defaults.track_overrides)
        self.platform_rules = dict(defaults.platform_overrides)
        main = QVBoxLayout(self)
        hint = QLabel('分场各自消除共同弯曲、保留相对形状；站台方向水平，站内压短并展开间距。最后接轨点之外的正线拟合为直线走势并分开引出。分场、股道、台体、乘降面和延长线均可编辑。P / T 为图示序号，不替代官方编号；缺失面号不会按形状猜测。')
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
        self.warning_details = QTextEdit()
        self.warning_details.setReadOnly(True)
        self.warning_details.setMaximumHeight(135)
        preview_column.addWidget(self.warning_details)
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
        combo(form,'layout_mode','布局方式',[('分场相对弯曲（推荐）','yard_relative'),('源形状比例（兼容）','source_shape')])
        check(form,'auto_rotate','按站台方向自动旋正')
        check(form,'remove_common_bend','按分场消除共同弯曲')
        check(form,'align_main_outlets','正线示意延长至图边缘')
        combo(form, 'orientation', '构图方向', [('横向', 'landscape'), ('纵向', 'portrait')])
        check(form, 'show_north', '真实北向指北针')
        number(form, 'station_compression', '站台方向压缩倍数', 1, 12, .25)
        number(form, 'outside_compression', '站外纵向压缩倍数', 1, 30, .5)
        number(form, 'direction_radius_m', '站外方向参考半径 / m', 500, 10000, 250)
        number(form, 'platform_width', '站台符号宽度倍数', .5, 4, .1)
        number(form, 'margin', '全图留白（图面单位）', 10, 240, 5)
        form.addRow(QLabel('相对弯曲按各分场处理，站内宽高统一规范化；咽喉仍保留真实连接。兼容模式保持原有几何比例。'))

        form = page('内容')
        for name, title in [('show_main', '正线'), ('show_station', '站线 / 到发线 / 辅助线'),
                            ('show_connectors', '联络线'), ('show_outer_main', '外围关联主线'),
                            ('show_outer_connectors', '外围联络线'), ('include_construction', '包含在建铁路（虚线）'),
                            ('show_platforms', '真实来源站台符号'), ('show_legend', '图例'),
                            ('show_track_labels','全部股道编号 / 图示序号'),('show_platform_labels','实体站台及乘降站台面编号'),
                            ('show_title', '站名标题'), ('show_endpoints', '正线端口名称与通达城市')]:
            check(form, name, title)
        number(form, 'topology_depth', '向外追踪连接层数', 0, 64, 1, True)
        form.addRow(QLabel('外围跟随真实连接的主线和联络线，不按附近几何凑线路。更高层数可扩大数据范围；超出画布的线路只绘制到边界。'))
        form.addRow(QLabel('收束处标线路名，边缘端口标去向；道岔符号只绘制在真实共用节点上。'))

        def editor_page(title, headers):
            widget = QWidget()
            column = QVBoxLayout(widget)
            notes={'逐线编辑':'空白值沿用自动判断。仅修改图面；线路按稳定 RailScope ID 保存。',
                   '边缘端口':'文字留空沿用线路去向；“不标”隐藏文字。“延长”只增加示意线，不代表真实铁路延伸。',
                   '分场样式':'每个分场统一颜色。名称和颜色只用于图面，不改写基础设施归属。',
                   '分场与股道':'按稳定股道 ID 调整图示分场和编号。空白沿用原归属；新分场可直接填写名称。图示编号不作为官方运营编号。',
                   '站台编号':'实体台体与乘降站台面分开。空白使用来源编号；缺失编号明确提示。此页填写的编号只用于本张图，不写入运营数据。'}
            note = QLabel(notes[title])
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
        self.port_table, port_reset = editor_page('边缘端口', ['线路端口', '标注', '自定义文字（用 \\n 换行）', '引出规则', '水平偏移', '垂直偏移'])
        self.yard_table,yard_reset=editor_page('分场样式',['分场 / ID','图中名称','统一颜色 #RRGGBB'])
        self.track_table,track_reset=editor_page('分场与股道',['股道 / ID','分场（空白为原归属）','图中编号 / 名称'])
        self.platform_table,platform_reset=editor_page('站台编号',['来源站台 / ID','实体台体图中编号','乘降面图中编号（如 1;2）'])
        platform_reset.clicked.connect(lambda:self.reset_editor('platform'))
        self.track_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.track_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        batch=QPushButton('将选中股道设置为同一图示分场…')
        self.track_table.parentWidget().layout().addWidget(batch)
        batch.clicked.connect(self.assign_selected_yard)
        yard_reset.clicked.connect(lambda:self.reset_editor('yard'))
        track_reset.clicked.connect(lambda:self.reset_editor('track'))
        line_reset.clicked.connect(lambda: self.reset_editor('line'))
        port_reset.clicked.connect(lambda: self.reset_editor('port'))

        form = page('样式')
        for name, title in [('main_width', '统一铁路默认线宽')]:
            number(form, name, title, .5, 15, .5)
        number(form, 'title_size', '标题字号', 20, 90, 2)
        number(form, 'label_size', '标注字号', 12, 48, 1)
        combo(form, 'platform_fill', '站台填充', [('浅灰', 'gray'), ('淡绿', 'tint'), ('白底轮廓', 'outline')])
        combo(form, 'color_scheme', '颜色编码', [('明确线路与分场归属', 'systems'), ('单色打印', 'mono')])
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
        values['station_width'] = values['connector_width'] = values['main_width']
        return DiagramOptions(**values, color_overrides=colors,
                              line_overrides=self.line_rules, port_overrides=self.port_rules,
                              yard_overrides=self.yard_rules,track_overrides=self.track_rules,
                              platform_overrides=self.platform_rules)

    def assign_selected_yard(self):
        rows=sorted({i.row() for i in self.track_table.selectedIndexes()})
        if not rows:
            self.status.setText('先在“分场与股道”中选中需要调整的股道，可按 Ctrl / Shift 多选。')
            return
        value,accepted=QInputDialog.getText(self,'图示分场','分场名称（空白恢复原归属）')
        if accepted:
            self.track_table.blockSignals(True)
            for row in rows:self.track_table.item(row,1).setText(value.strip())
            self.track_table.blockSignals(False)
            self.refresh_preview()

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
            for col, name in ((1,'visible'),):
                value = self.port_table.cellWidget(row,col).currentData()
                if value is not None:
                    rule[name] = value
            text = self.port_table.item(row,2).text()
            if text:
                rule['text'] = text.replace('\\n','\n')
            extend=self.port_table.cellWidget(row,3).currentData()
            if extend is not None:rule['extend']=extend
            for col, name in ((4,'dx'),(5,'dy')):
                text = self.port_table.item(row,col).text().strip()
                if text:
                    rule[name] = float(text)
            if rule:
                self.port_rules[key] = rule
            else:
                self.port_rules.pop(key,None)
        for row in range(self.yard_table.rowCount()):
            key=self.yard_table.item(row,0).data(Qt.ItemDataRole.UserRole)
            rule={name:self.yard_table.item(row,col).text().strip() for col,name in ((1,'name'),(2,'color'))
                  if self.yard_table.item(row,col).text().strip()}
            if rule:self.yard_rules[key]=rule
            else:self.yard_rules.pop(key,None)
        for row in range(self.track_table.rowCount()):
            key=self.track_table.item(row,0).data(Qt.ItemDataRole.UserRole)
            group=self.track_table.item(row,1).text().strip()
            rule={}
            if group:
                ident=next((k for k,y in self.repo.yards.items() if y.name==group),None)
                rule['group_id']=ident or 'diagram-yard:'+group
            label=self.track_table.item(row,2).text().strip()
            if label:rule['label']=label
            if rule:self.track_rules[key]=rule
            else:self.track_rules.pop(key,None)
        for row in range(self.platform_table.rowCount()):
            key=self.platform_table.item(row,0).data(Qt.ItemDataRole.UserRole)
            rule={name:self.platform_table.item(row,col).text().strip() for col,name in ((1,'physical_number'),(2,'faces'))
                  if self.platform_table.item(row,col).text().strip()}
            if rule:self.platform_rules[key]=rule
            else:self.platform_rules.pop(key,None)

    def sync_editors(self, layout):
        physical={a['id']:a['text'] for a in layout.annotations if a['kind']=='physical-platform'}
        faces={p.id:[a['text'] for a in layout.annotations if a['kind']=='platform-face' and a['id'].startswith(p.id+':')]
               for p in layout.platforms}
        for table,records,rules,fields in (
            (self.yard_table,[(k,g['name']) for k,g in layout.groups.items()],self.yard_rules,('name','color')),
            (self.track_table,[(t.id,t.name) for t in self.repo.station_tracks.values()],self.track_rules,('group_id','label')),
            (self.platform_table,[(p.id,physical.get(p.id,p.id)+' · '+' / '.join(faces[p.id])) for p in layout.platforms],self.platform_rules,('physical_number','faces'))):
            table.blockSignals(True)
            present={table.item(row,0).data(Qt.ItemDataRole.UserRole) for row in range(table.rowCount())}
            for key,name in records:
                if key in present:continue
                row=table.rowCount();table.insertRow(row)
                table.setItem(row,0,self.readonly_item(name if table is self.platform_table else name+'\n'+key,key))
                table.setRowHeight(row,50 if table is not self.platform_table else 32)
                for col,field in enumerate(fields,1):
                    value=rules.get(key,{}).get(field,'')
                    if field=='group_id':value=self.repo.yards[value].name if value in self.repo.yards else value.removeprefix('diagram-yard:')
                    table.setItem(row,col,QTableWidgetItem(value))
            table.blockSignals(False);table.resizeColumnsToContents()
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
            side = {'left':'左','right':'右','top':'上','bottom':'下'}[port['side']]
            self.port_table.setItem(row,0,self.readonly_item(f'{system_name(port["line"])} · {side}',key))
            for col,name,titles in ((1,'visible',('标注','不标')),):
                self.port_table.setCellWidget(row,col,self.editor_combo([('自动',None),(titles[0],True),(titles[1],False)],rule.get(name)))
            self.port_table.setCellWidget(row,3,self.editor_combo([('自动',None),('延长',True),('不延长',False)],rule.get('extend')))
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
        table,rules={'line':(self.line_table,self.line_rules),'port':(self.port_table,self.port_rules),
                     'yard':(self.yard_table,self.yard_rules),'track':(self.track_table,self.track_rules),
                     'platform':(self.platform_table,self.platform_rules)}[kind]
        rules.clear()
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
            layout = build_layout(self.repo,self.context,options)
            try:
                from .station_diagram.renderer import destination_warnings
            except ImportError:
                from station_diagram.renderer import destination_warnings
            layout.warnings.extend(destination_warnings(self.repo,layout,self.info,options))
            self.sync_editors(layout)
            self.warning_details.setPlainText('\n'.join(layout.warnings) or '已检查真实轨道连接与明确归属。')
            width, height = options.canvas_size
            self.status.setText(self.settings_warning + f'预览：{width} × {height}，{options.dpi} DPI；{len(layout.edges)} 条真实轨道，{len(layout.lanes)} 根核心股道，{len(layout.warnings)} 项核对提示。')
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
            temp.write_text(json.dumps({**asdict(options), 'layout_algorithm':'yard_relative_linear_outlets_v3'}, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(self.settings_path)
