"""Dedicated station-diagram export controls with a real rendered preview."""
from dataclasses import asdict, replace
from collections import Counter
import json
import sqlite3
from pathlib import Path

from PySide6.QtCore import QByteArray, QTimer, Qt, Signal, QPointF
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtGui import QImage, QPainter, QPen, QColor, QPolygonF
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
    track_clicked = Signal(object, object)

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
            if getattr(self,'selected_parts',None):
                size = self.renderer().defaultSize()
                painter.setPen(QPen(QColor('#ed9a22'), 4))
                for part in self.selected_parts:
                    painter.drawPolyline(QPolygonF([QPointF(x+p[0]*target.width()/size.width(),
                                                             y+p[1]*target.height()/size.height()) for p in part]))
        painter.end()

    def mousePressEvent(self, event):
        if not hasattr(self,'page') or event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        target = self.page.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        x,y = (self.width()-target.width())/2,(self.height()-target.height())/2
        size = self.renderer().defaultSize()
        point = ((event.position().x()-x)*size.width()/target.width(),
                 (event.position().y()-y)*size.height()/target.height())
        self.track_clicked.emit(point, event.modifiers())


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
                if values.get('layout_algorithm') not in ('yard_relative_linear_outlets_v3','yard_centered_parallel_outlets_v4','straight_platform_smooth_throats_v5'):
                    values['layout_mode']='yard_relative'
                    values['remove_common_bend']=True
                    if values.get('station_compression') in (1,4):values['station_compression']=2.5
                if values.get('layout_algorithm') not in ('source_shape_shared_transform_v2','yard_relative_linear_outlets_v3','yard_centered_parallel_outlets_v4','straight_platform_smooth_throats_v5'):
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
        self.selected_yards = tuple(defaults.selected_yards)
        main = QVBoxLayout(self)
        hint = QLabel('先分别展开各场的水平正线和咽喉，再绘制场间联络与站外去向。可选全站、单场或市域相关股道组合；台体与乘降面分开标注，股道只使用来源编号或手工图中编号。')
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
        combo(form,'layout_mode','布局方式',[('站台直线 / 分场规则咽喉（推荐）','yard_relative'),('源形状比例（兼容）','source_shape')])
        self.yard_selector = QComboBox()
        self.yard_selector.addItem('全部分场', '[]')
        self.yard_selector.currentIndexChanged.connect(self.choose_yard)
        form.addRow('导出分场', self.yard_selector)
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
        form.addRow(QLabel('站台线始终画直；各分场消除共同弯曲，咽喉用平滑模板连接真实节点。兼容模式保持原有几何比例。'))

        form = page('内容')
        for name, title in [('show_main', '正线'), ('show_station', '站线 / 到发线 / 辅助线'),
                            ('show_connectors', '联络线'), ('show_outer_main', '外围关联主线'),
                            ('show_outer_connectors', '外围联络线'), ('include_construction', '包含在建铁路（虚线）'),
                            ('show_platforms', '真实来源站台符号'), ('show_legend', '图例'),
                            ('show_track_labels','来源股道编号 / 手工图中编号'),('show_platform_labels','实体站台及乘降站台面编号'),
                            ('show_title', '站名标题'), ('show_endpoints', '正线端口名称与通达城市')]:
            check(form, name, title)
        number(form, 'topology_depth', '向外追踪连接层数', 0, 64, 1, True)
        form.addRow(QLabel('外围跟随真实连接的主线和联络线，不按附近几何凑线路。更高层数可扩大数据范围；超出画布的线路只绘制到边界。'))
        form.addRow(QLabel('收束处标线路名，边缘端口标去向；连接点保留真实拓扑，交叉线连续绘制。'))

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
        self.track_table.itemSelectionChanged.connect(self.highlight_tracks)
        self.preview.track_clicked.connect(self.select_preview_track)
        assignment = QFormLayout()
        self.yard_track_numbers = QLineEdit()
        self.yard_track_numbers.setPlaceholderText('例如 1-4、7；也可填写已有图中编号')
        self.yard_assignment_name = QLineEdit()
        self.yard_assignment_name.setPlaceholderText('已有或新分场名称；空白恢复原归属')
        assignment.addRow('股道号范围', self.yard_track_numbers)
        assignment.addRow('指定图示分场', self.yard_assignment_name)
        self.track_table.search.setPlaceholderText('搜索道号、股道名称或 RailScope ID')
        self.track_table.parentWidget().layout().insertLayout(1, assignment)
        numbered_batch = QPushButton('按道号批量指定分场并预览')
        self.track_table.parentWidget().layout().insertWidget(2, numbered_batch)
        numbered_batch.clicked.connect(self.apply_yard_assignment)
        self.selected_track_count = QLabel('也可点击预览中的站台线，按 Ctrl 多选；橙色表示已选股道。')
        self.selected_track_count.setWordWrap(True)
        self.track_table.parentWidget().layout().insertWidget(3, self.selected_track_count)
        selection_batch = QPushButton('将已选股道指定为上方分场并预览')
        selection_batch.clicked.connect(lambda: self.set_yard_rows(
            sorted({i.row() for i in self.track_table.selectedIndexes()}),
            self.yard_assignment_name.text()))
        self.track_table.parentWidget().layout().insertWidget(4, selection_batch)
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
                              platform_overrides=self.platform_rules,selected_yards=self.selected_yards)

    def choose_yard(self):
        self.selected_yards = tuple(json.loads(self.yard_selector.currentData() or '[]'))
        self.timer.start()

    def assign_selected_yard(self):
        rows=sorted({i.row() for i in self.track_table.selectedIndexes()})
        if not rows:
            self.status.setText('先在“分场与股道”中选中需要调整的股道，可按 Ctrl / Shift 多选。')
            return
        value,accepted=QInputDialog.getText(self,'图示分场','分场名称（空白恢复原归属）')
        if accepted:
            self.set_yard_rows(rows, value)

    def set_yard_rows(self, rows, value):
        if not rows:
            self.status.setText('先点击预览站台线或在下表多选股道。')
            return
        self.timer.stop()
        self.track_table.blockSignals(True)
        for row in rows:
            self.track_table.item(row,1).setText(value.strip())
        self.track_table.blockSignals(False)
        self.refresh_preview()

    def apply_yard_assignment(self):
        try:
            try:
                from .station_diagram.manual_yards import numbered_tracks
            except ImportError:
                from station_diagram.manual_yards import numbered_tracks
            self.read_editors()
            ids = numbered_tracks(self.repo, self.track_rules, self.yard_track_numbers.text())
            rows = [row for row in range(self.track_table.rowCount())
                    if self.track_table.item(row,0).data(Qt.ItemDataRole.UserRole) in ids]
            self.set_yard_rows(rows, self.yard_assignment_name.text())
        except ValueError as error:
            self.status.setText(str(error))

    def highlight_tracks(self):
        if not hasattr(self,'current_layout'):
            return
        ids = {self.track_table.item(i.row(),0).data(Qt.ItemDataRole.UserRole)
               for i in self.track_table.selectedIndexes()}
        keys = {ref.edge_id for ident in ids for ref in self.repo.station_tracks[ident].edge_refs}
        self.preview.selected_parts = [part for k in keys & self.current_layout.edges.keys()
                                       for part in self.current_layout.edges[k].parts]
        self.selected_track_count.setText(f'已选 {len(ids)} 个来源股道对象；橙色表示选择，Ctrl 点击可增减。')
        self.preview.update()

    def select_preview_track(self, point, modifiers):
        if not hasattr(self,'current_layout'):
            return
        from shapely.geometry import LineString, Point, box
        try:
            from .station_diagram.topology import build_graph, chains
        except ImportError:
            from station_diagram.topology import build_graph, chains
        layout = self.current_layout
        axis = 1 if self.options().orientation == 'portrait' else 0
        if not layout.core_bounds[axis] <= point[axis] <= layout.core_bounds[axis+2]:
            return
        hit = Point(point)
        band = box(*layout.core_bounds)
        distances = [(LineString(part).intersection(band).distance(hit), key)
                     for key in layout.platform_rail_ids for part in layout.edges[key].parts
                     if not LineString(part).intersection(band).is_empty]
        if not distances or min(distances)[0] > 12:
            return
        key = min(distances)[1]
        graph = build_graph(self.repo,set(layout.edges))
        keys = next(({k for k,_ in legs} for legs in chains(graph,set(layout.platform_rail_ids))
                     if any(k==key for k,_ in legs)),{key})
        ids = {t.id for t in self.repo.station_tracks.values() if any(r.edge_id in keys for r in t.edge_refs)}
        rows = [row for row in range(self.track_table.rowCount())
                if self.track_table.item(row,0).data(Qt.ItemDataRole.UserRole) in ids]
        toggle = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        selected = {i.row() for i in self.track_table.selectedIndexes()} if toggle else set()
        selected = selected - set(rows) if toggle and set(rows) <= selected else selected | set(rows)
        self.track_table.blockSignals(True)
        self.track_table.clearSelection()
        from PySide6.QtCore import QItemSelectionModel
        for row in selected:
            self.track_table.selectionModel().select(self.track_table.model().index(row,0),
                QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        self.track_table.blockSignals(False)
        if rows:
            self.track_table.scrollToItem(self.track_table.item(rows[0],0))
        self.highlight_tracks()

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
            (self.track_table,[(t.id,('道号 '+t.track_number+' · ' if t.track_number else '道号待核对 · ')+t.name)
                               for t in self.repo.station_tracks.values()],self.track_rules,('group_id','label')),
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
            full = build_layout(self.repo,self.context,replace(options,selected_yards=()))
            if any(key not in full.groups for key in self.selected_yards):
                self.selected_yards = ()
                options = replace(options, selected_yards=())
            self.yard_selector.blockSignals(True)
            self.yard_selector.clear()
            self.yard_selector.addItem('全部分场', '[]')
            for key, group in sorted(full.groups.items(), key=lambda p: -p[1]['center']):
                self.yard_selector.addItem(group['name'], json.dumps([key],ensure_ascii=False))
            suburban = tuple(k for k,g in full.groups.items()
                              if any(v in g['name'] for v in ('嘉闵','示范区','机场联络')))
            if suburban:
                self.yard_selector.addItem('市域铁路相关股道（来源分场待核对）', json.dumps(suburban,ensure_ascii=False))
            self.yard_selector.setCurrentIndex(max(0,self.yard_selector.findData(json.dumps(self.selected_yards,ensure_ascii=False))))
            self.yard_selector.blockSignals(False)
            layout = build_layout(self.repo,self.context,options) if self.selected_yards else full
            svg = station_svg(self.repo, self.context, station_info=self.info, options=options, layout=layout)
            self.preview.load(QByteArray(svg.encode('utf-8')))
            self.current_layout = layout
            try:
                from .station_diagram.renderer import destination_warnings
            except ImportError:
                from station_diagram.renderer import destination_warnings
            layout.warnings = list(dict.fromkeys([*layout.warnings, *destination_warnings(self.repo,layout,self.info,options)]))
            self.sync_editors(full)
            self.highlight_tracks()
            self.warning_details.setPlainText('\n'.join(layout.warnings) or '已检查真实轨道连接与明确归属。')
            width, height = options.canvas_size
            self.status.setText(self.settings_warning + f'预览：{width} × {height}，{options.dpi} DPI；{len(layout.edges)} 条真实轨道段，{len(layout.lanes)} 个股道来源对象，{len(layout.warnings)} 项核对提示。')
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
            temp.write_text(json.dumps({**asdict(options), 'layout_algorithm':'straight_platform_smooth_throats_v5'}, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(self.settings_path)
