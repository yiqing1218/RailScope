"""RailScope native GIS workbench with independent map layers."""
from __future__ import annotations
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT=Path(__file__).resolve().parent.parent; sys.path.insert(0,str(ROOT/'backend'))
from PySide6.QtCore import QTimer,Qt,QUrl
from PySide6.QtGui import QAction,QColor
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication,QCheckBox,QComboBox,QFileDialog,QHBoxLayout,QLabel,QListWidget,QListWidgetItem,QMainWindow,QMessageBox,QPushButton,QScrollArea,QSplitter,QStackedWidget,QTabWidget,QTreeWidget,QTreeWidgetItem,QVBoxLayout,QWidget
from railscope.demo import load_demo
from railscope.domain import DispatchEvent
from railscope.services.dispatch import add_event,reset
from railscope.services.importers import fetch_highspeed_railway,save_raw_geojson
from railscope.services.timetable import effective_run

SCENARIO='base-2026-09-15'; EMPTY={'type':'FeatureCollection','features':[]}
COLORS={'panel':'#0d2136','line':'#284662','text':'#e9f1f8','muted':'#97b0c8','warn':'#f2b632','conflict':'#ff6578'}
MAP_HTML="""<!doctype html><html><head><meta charset='utf-8'><link href='maplibre-gl.css' rel='stylesheet'><script src='maplibre-gl.js'></script><style>html,body,#map{margin:0;width:100%;height:100%;background:#071525}.maplibregl-ctrl-attrib{font:11px sans-serif}</style></head><body><div id='map'></div><script>
const map=new maplibregl.Map({container:'map',center:[104.2,35.9],zoom:4.25,style:{version:8,glyphs:'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',sources:{osm:{type:'raster',tiles:['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],tileSize:256,attribution:'© OpenStreetMap contributors'},sat:{type:'raster',tiles:['https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_ShadedRelief_Bathymetry/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg'],tileSize:256,attribution:'NASA GIBS Blue Marble'},admin:{type:'raster',tiles:['https://a.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png'],tileSize:256,attribution:'© OpenStreetMap contributors, OSM France'}},layers:[{id:'base-standard',type:'raster',source:'osm'},{id:'base-satellite',type:'raster',source:'sat',layout:{visibility:'none'}},{id:'base-admin',type:'raster',source:'admin',layout:{visibility:'none'}}]}});let pending;
function source(id,data){if(map.getSource(id))map.getSource(id).setData(data);else map.addSource(id,{type:'geojson',data});}
function apply(d){for(const k of ['networkRoad','networkRail','route','blocks','stations','imported','osmMetro','metroStations','metroStationAreas','constructionMetro','vehicles'])source(k,d[k]);if(!map.getLayer('road')){map.addLayer({id:'road',type:'line',source:'networkRoad',paint:{'line-color':'#68798d','line-width':2}});map.addLayer({id:'rail',type:'line',source:'networkRail',filter:['==',['get','mode'],'rail'],paint:{'line-color':'#177fb6','line-width':4}});map.addLayer({id:'metro-reference',type:'line',source:'networkRail',filter:['==',['get','mode'],'metro'],paint:{'line-color':'#d86eb2','line-width':3}});map.addLayer({id:'route',type:'line',source:'route',paint:{'line-color':'#20c997','line-width':7}});map.addLayer({id:'blocks',type:'line',source:'blocks',paint:{'line-color':['case',['get','conflict'],'#ff526d','#f2b632'],'line-width':8,'line-dasharray':[1.2,1.5]}});map.addLayer({id:'imported',type:'line',source:'imported',filter:['!=',['geometry-type'],'Point'],paint:{'line-color':'#7c3aed','line-width':4}});map.addLayer({id:'imported-points',type:'circle',source:'imported',filter:['==',['geometry-type'],'Point'],paint:{'circle-radius':6,'circle-color':'#7c3aed','circle-stroke-color':'#fff','circle-stroke-width':2}});map.addLayer({id:'metro-lines',type:'line',source:'osmMetro',paint:{'line-color':['coalesce',['get','display_color'],'#64748b'],'line-width':['interpolate',['linear'],['zoom'],4,1.5,8,3.5,12,6],'line-opacity':.92}});map.addLayer({id:'construction',type:'line',source:'constructionMetro',paint:{'line-color':'#475569','line-width':['interpolate',['linear'],['zoom'],4,2,8,4,12,7],'line-dasharray':[1.6,1.25],'line-opacity':.98}});map.addLayer({id:'metro-station-area',type:'circle',source:'metroStationAreas',minzoom:7,paint:{'circle-radius':['interpolate',['linear'],['zoom'],7,7,12,13],'circle-color':'#38bdf8','circle-opacity':.12,'circle-stroke-color':'#0ea5e9','circle-stroke-width':1.5}});map.addLayer({id:'metro-stations',type:'circle',source:'metroStations',minzoom:7,paint:{'circle-radius':4,'circle-color':'#fff','circle-stroke-color':'#183b5a','circle-stroke-width':1.5}});map.addLayer({id:'metro-labels',type:'symbol',source:'metroStations',minzoom:10,layout:{'text-field':['get','name'],'text-font':['Open Sans Regular'],'text-size':11,'text-offset':[0,1],'text-anchor':'top'},paint:{'text-color':'#10283c','text-halo-color':'#fff','text-halo-width':1.5}});map.addLayer({id:'vehicles',type:'circle',source:'vehicles',paint:{'circle-radius':7,'circle-color':'#22d3ee','circle-stroke-color':'#fff','circle-stroke-width':2}})}if(d.fit)map.fitBounds(d.bounds,{padding:80,maxZoom:12})}window.updateRailScope=d=>{pending=d;if(map.isStyleLoaded())apply(d)};map.on('load',()=>{if(pending)apply(pending)});
</script></body></html>"""
def clock(s):return '—' if s is None else f'{s//3600:02d}:{s%3600//60:02d}'

class MapPanel(QWebEngineView):
 def __init__(self,repo,selected):
  super().__init__();self.repo=repo;self.selected=selected;self.imported=self.read(ROOT/'data/raw/osm/beijing_highspeed.geojson');national=ROOT/'data/processed/osm/china_metro_routes.geojson';preview=ROOT/'data/processed/osm/shanghai_metro_routes.geojson';metro=national if national.exists() else preview;self.metro_scope='全国' if national.exists() else ('上海验证集' if preview.exists() else '未导入');self.osm_metro=self.read(metro);self.metro_stations=self.read(ROOT/'data/processed/osm/china_metro_stations.geojson');self.construction_metro=self.read(ROOT/'data/processed/osm/china_metro_construction.geojson');self.vehicles=dict(EMPTY);self.visible_lines={x['properties']['route_relation_id'] for x in self.osm_metro['features']};self.flags={'rail':True,'road':True,'metro_lines':True,'construction':True,'metro_stations':True,'station_areas':True,'vehicles':False};self.first=True
  settings=self.settings();settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,True);settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls,True);self.setHtml(MAP_HTML,QUrl.fromLocalFile(str(ROOT/'frontend/node_modules/maplibre-gl/dist/index.html')));self.loadFinished.connect(lambda _:QTimer.singleShot(800,self.redraw))
 @staticmethod
 def read(path):return json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(EMPTY)
 def set_base(self,name):
  selected={'标准地图':'base-standard','卫星影像':'base-satellite','行政图':'base-admin'}[name];self.page().runJavaScript("['base-standard','base-satellite','base-admin'].forEach(x=>map.setLayoutProperty(x,'visibility',x==='"+selected+"'?'visible':'none'))")
 def set_vehicle_positions(self,feature_collection):self.vehicles=feature_collection;self.redraw()
 def redraw(self):
  net=[];route=[];blocks=[];run=self.repo.train_runs[self.selected];byblock={x.block_id:x.edge_id for x in self.repo.block_edges};conflicts={x.resource_id for x in self.repo.conflicts}
  for e in self.repo.edges.values():net.append({'type':'Feature','properties':{'id':e.id,'mode':e.mode},'geometry':{'type':'LineString','coordinates':e.coordinates}})
  for ref in self.repo.routes[run.route_path_id].edge_refs:route.append({'type':'Feature','properties':{},'geometry':{'type':'LineString','coordinates':self.repo.edges[ref.edge_id].coordinates}})
  for bid,eid in byblock.items():blocks.append({'type':'Feature','properties':{'conflict':bid in conflicts},'geometry':{'type':'LineString','coordinates':self.repo.edges[eid].coordinates}})
  metro=[x for x in self.osm_metro['features'] if x['properties']['route_relation_id'] in self.visible_lines];station=[x for x in self.metro_stations['features'] if any(i in self.visible_lines for i in x['properties'].get('route_relation_ids',[]))];points=[p for e in self.repo.edges.values() for p in e.coordinates];fallback=[[min(x[0] for x in points),min(x[1] for x in points)],[max(x[0] for x in points),max(x[1] for x in points)]]
  roads=[x for x in net if x['properties']['mode']=='road'];rails=[x for x in net if x['properties']['mode']!='road'];d={'networkRoad':{'type':'FeatureCollection','features':roads} if self.flags['road'] else EMPTY,'networkRail':{'type':'FeatureCollection','features':rails} if self.flags['rail'] else EMPTY,'route':{'type':'FeatureCollection','features':route if self.flags['rail'] else []},'blocks':{'type':'FeatureCollection','features':blocks if self.flags['rail'] else []},'stations':EMPTY,'imported':self.imported,'osmMetro':{'type':'FeatureCollection','features':metro} if self.flags['metro_lines'] else EMPTY,'metroStations':{'type':'FeatureCollection','features':station} if self.flags['metro_stations'] else EMPTY,'metroStationAreas':{'type':'FeatureCollection','features':station} if self.flags['station_areas'] else EMPTY,'constructionMetro':self.construction_metro if self.flags['construction'] else EMPTY,'vehicles':self.vehicles if self.flags['vehicles'] else EMPTY,'bounds':[[73,18],[135,54]] if metro or self.construction_metro['features'] else fallback,'fit':self.first};self.first=False;self.page().runJavaScript('window.updateRailScope('+json.dumps(d,ensure_ascii=False)+')')

class Desk(QMainWindow):
 def __init__(self):
  super().__init__();self.repo=load_demo();self.selected='run-101';self.setWindowTitle('RailScope · 铁路运行资源与调度平台');self.resize(1600,960);self.build();self.refresh()
 def add(self,menu,text,fn):a=QAction(text,menu);a.triggered.connect(fn);menu.addAction(a)
 def build(self):
  bar=self.menuBar();file=bar.addMenu('文件');self.add(file,'导入 GeoJSON 图层…',self.import_file);self.add(file,'导出当前可见图层…',self.export_visible);file.addSeparator();self.add(file,'退出',self.close);edit=bar.addMenu('编辑');self.add(edit,'清除手动导入图层',self.clear_import);self.add(edit,'显示全部地铁线路',lambda:self.all_lines(True));self.add(edit,'隐藏全部地铁线路',lambda:self.all_lines(False));view=bar.addMenu('视图');self.add(view,'缩放至全国',self.zoom_china);self.add(view,'恢复图层默认状态',self.reset_layers);tools=bar.addMenu('工具');self.add(tools,'从 OSM 导入北京周边高铁',self.import_osm);self.add(tools,'执行拓扑校验',self.show_topology);help=bar.addMenu('帮助');self.add(help,'数据署名与图层说明',self.about)
  root=QWidget();outer=QVBoxLayout(root);outer.setContentsMargins(0,0,0,0);head=QWidget();hl=QHBoxLayout(head);hl.setContentsMargins(22,0,22,0);brand=QLabel('RailScope');brand.setObjectName('brand');hl.addWidget(brand);hl.addWidget(QLabel('交通基础设施 GIS · 铁路运行资源与调度'),1);badge=QLabel('V0 · V1 · V5');badge.setObjectName('badge');hl.addWidget(badge);outer.addWidget(head);nav=QWidget();nl=QHBoxLayout(nav);nl.setContentsMargins(16,6,16,6);nl.addWidget(QLabel('工作区'));self.pages=QStackedWidget();self.map=MapPanel(self.repo,self.selected);self.pages.addWidget(self.map_page());self.pages.addWidget(self.operations());self.pages.addWidget(self.data());self.pages.addWidget(self.topology())
  for i,n in enumerate(('地图','运行','数据源','拓扑')):b=QPushButton(n);b.setCheckable(True);b.setChecked(i==0);b.clicked.connect(lambda _,x=i:self.open(x));nl.addWidget(b);setattr(self,f'nav{i}',b)
  nl.addStretch();outer.addWidget(nav);outer.addWidget(self.pages,1);self.setCentralWidget(root);self.statusBar().showMessage('就绪：四个主工作区各出现一次。');self.setStyleSheet("QWidget{background:#0d2136;color:#e9f1f8;font:14px 'Microsoft YaHei UI';} QMenuBar,QMenu{background:#0b1c2d;color:#e9f1f8;} QMenu::item:selected{background:#1b5279;} QListWidget,QTreeWidget,QComboBox{background:#102b45;border:1px solid #284662;border-radius:6px;padding:5px;} QListWidget::item{padding:8px;border-bottom:1px solid #23415d;} QListWidget::item:selected,QTreeWidget::item:selected{background:#1b5279;} QPushButton{background:#164b72;border:1px solid #34749b;border-radius:5px;padding:9px;text-align:left;} QPushButton:hover,QPushButton:checked{background:#21648d;} QCheckBox{padding:5px 0;} #brand{font-size:22px;font-weight:700;} #badge{background:#143c58;border-radius:12px;padding:5px 11px;color:#bce3f7;} #section{font-weight:700;color:#97b0c8;padding-top:10px;} #detail{color:#97b0c8;line-height:1.55;}")
 def pane(self,title):w=QWidget();l=QVBoxLayout(w);l.setContentsMargins(16,14,16,14);h=QLabel(title);h.setStyleSheet('font-size:19px;font-weight:700;');l.addWidget(h);return w,l
 def section(self,text):q=QLabel(text);q.setObjectName('section');return q
 def check(self,l,text,key):q=QCheckBox(text);q.setChecked(self.map.flags[key]);q.toggled.connect(lambda v,k=key:self.toggle(k,v));l.addWidget(q);return q
 def map_page(self):
  page=QWidget();l=QHBoxLayout(page);l.setContentsMargins(0,0,0,0);split=QSplitter(Qt.Orientation.Horizontal);split.addWidget(self.layers());split.addWidget(self.map);split.addWidget(self.details());split.setSizes([310,980,320]);l.addWidget(split);return page
 def layers(self):
  w,l=self.pane('地图图层');scroll=QScrollArea();scroll.setWidgetResizable(True);body=QWidget();c=QVBoxLayout(body);c.addWidget(self.section('底图'));base=QComboBox();base.addItems(['标准地图','卫星影像','行政图']);base.currentTextChanged.connect(self.map.set_base);c.addWidget(base);n=QLabel('标准地图由 OSM 图源提供道路、地名与行政文字；可独立切换卫星与行政图。');n.setObjectName('detail');n.setWordWrap(True);c.addWidget(n);c.addWidget(self.section('铁路'));self.check(c,'铁路基础设施','rail');c.addWidget(self.section('公路'));self.check(c,'道路参照','road');c.addWidget(self.section('地铁'));self.check(c,'已运营地铁/轻轨线路','metro_lines');self.check(c,'在建地铁/轻轨（深灰色虚线）','construction');self.check(c,'地铁站 POI','metro_stations');self.check(c,'地铁站 POI 区域边界','station_areas');c.addWidget(self.section('城市 / 线路'));self.tree=QTreeWidget();self.tree.setHeaderLabels(['城市 / 网络 / 线路']);self.tree.setMinimumHeight(320);self.populate_tree();self.tree.itemChanged.connect(self.tree_changed);c.addWidget(self.tree,1);c.addWidget(self.section('运行状态'));v=self.check(c,'车辆定位（预留图层）','vehicles');v.setToolTip('已预留车辆位置图层；当前版本不实现沿线动画。');c.addWidget(self.section('数据导入'));b=QPushButton('导入 GeoJSON 图层…');b.clicked.connect(self.import_file);c.addWidget(b);c.addStretch();scroll.setWidget(body);l.addWidget(scroll,1);return w
 def populate_tree(self):
  p=ROOT/'data/processed/osm/china_metro_route_catalog.json';catalog=json.loads(p.read_text(encoding='utf-8')) if p.exists() else {'routes':[]};groups={}
  for r in catalog['routes']:groups.setdefault(r.get('network') or r.get('operator') or '未分类城市网络',[]).append(r)
  for city,routes in sorted(groups.items()):
   parent=QTreeWidgetItem([f'{city}  ({len(routes)})']);parent.setFlags(parent.flags()|Qt.ItemFlag.ItemIsUserCheckable);parent.setCheckState(0,Qt.CheckState.Checked);self.tree.addTopLevelItem(parent)
   for r in sorted(routes,key=lambda x:(str(x.get('ref') or ''),x['name'])):child=QTreeWidgetItem([f"{r.get('ref') or '—'}  {r['name']}"]);child.setData(0,Qt.ItemDataRole.UserRole,r['osm_relation_id']);child.setFlags(child.flags()|Qt.ItemFlag.ItemIsUserCheckable);child.setCheckState(0,Qt.CheckState.Checked);parent.addChild(child)
 def tree_changed(self,item,_):
  self.tree.blockSignals(True)
  if item.childCount():
   for i in range(item.childCount()):item.child(i).setCheckState(0,item.checkState(0))
  rid=item.data(0,Qt.ItemDataRole.UserRole)
  if rid is not None:(self.map.visible_lines.add if item.checkState(0)==Qt.CheckState.Checked else self.map.visible_lines.discard)(rid)
  self.tree.blockSignals(False);self.map.redraw()
 def details(self):w,l=self.pane('对象详情');l.addWidget(QLabel('在地图中选择基础设施、线路、站点或资源后，此处显示属性。'));q=QLabel('• 全国 OSM 城市地铁/轻轨\n• 在建地铁/轻轨：深灰色虚线\n• 地铁站 POI 与可单独开关的范围环\n• 车辆位置图层已预留');q.setObjectName('detail');q.setWordWrap(True);l.addWidget(q);l.addStretch();return w
 def operations(self):w,l=self.pane('运行与调度');split=QSplitter(Qt.Orientation.Horizontal);split.addWidget(self.right());self.tabs=QTabWidget();self.conflicts=QListWidget();self.occupancies=QListWidget();self.stops=QListWidget();self.tabs.addTab(self.conflicts,'冲突');self.tabs.addTab(self.occupancies,'资源占用');self.tabs.addTab(self.stops,'有效时刻表');split.addWidget(self.tabs);split.setSizes([380,1000]);l.addWidget(split,1);return w
 def data(self):
  w,l=self.pane('数据源与导入');m=json.loads((ROOT/'data/processed/osm/china_metro_import_manifest.json').read_text(encoding='utf-8'));cp=ROOT/'data/processed/osm/china_metro_construction_manifest.json';c=json.loads(cp.read_text(encoding='utf-8')) if cp.exists() else {};l.addWidget(QLabel(f"全国 OSM 城市地铁/轻轨\n线路关系：{m['route_relations_accepted']}\n轨道几何段：{m['line_member_features_written']}\n地铁站 POI：{m.get('metro_station_features_written',0)}\n\n在建地铁/轻轨\n施工轨道段：{c.get('construction_way_features_written',0)}\n样式：深灰色虚线"));b=QPushButton('导入 GeoJSON 图层…');b.clicked.connect(self.import_file);l.addWidget(b);l.addWidget(QLabel('来源：© OpenStreetMap contributors · ODbL 1.0'));l.addStretch();return w
 def topology(self):w,l=self.pane('铁路拓扑');self.topology_text=QLabel('尚未校验。');self.topology_text.setWordWrap(True);l.addWidget(self.topology_text);b=QPushButton('执行拓扑校验');b.clicked.connect(self.show_topology);l.addWidget(b);l.addWidget(QLabel('拓扑与车辆、时刻表、调度事件保持分离。'));l.addStretch();return w
 def right(self):
  w,l=self.pane('运行控制');l.addWidget(self.section('列车运行任务'));self.trains=QListWidget();self.trains.setMaximumHeight(145);self.trains.itemClicked.connect(self.choose);l.addWidget(self.trains);l.addWidget(self.section('选中车次'));self.detail=QLabel();self.detail.setObjectName('detail');self.detail.setWordWrap(True);l.addWidget(self.detail);l.addWidget(self.section('人工调度'))
  for text,fn in [('延误 +300 秒',self.delay),('始发站扣车 +300 秒',self.hold),('取消本次运行',self.cancel),('恢复原始计划',self.reset)]:b=QPushButton(text);b.clicked.connect(fn);l.addWidget(b)
  self.status=QLabel();self.status.setObjectName('detail');self.status.setWordWrap(True);l.addWidget(self.status);l.addStretch();return w
 def open(self,i):self.pages.setCurrentIndex(i);[getattr(self,f'nav{x}').setChecked(x==i) for x in range(4)]
 def toggle(self,key,value):self.map.flags[key]=value;self.map.redraw()
 def all_lines(self,value):
  self.tree.blockSignals(True)
  for i in range(self.tree.topLevelItemCount()):
   p=self.tree.topLevelItem(i);p.setCheckState(0,Qt.CheckState.Checked if value else Qt.CheckState.Unchecked)
   for j in range(p.childCount()):
    c=p.child(j);c.setCheckState(0,Qt.CheckState.Checked if value else Qt.CheckState.Unchecked);rid=c.data(0,Qt.ItemDataRole.UserRole);self.map.visible_lines.add(rid) if value else self.map.visible_lines.discard(rid)
  self.tree.blockSignals(False);self.map.redraw()
 def reset_layers(self):
  for key in self.map.flags:self.map.flags[key]=key!='vehicles'
  self.all_lines(True);self.map.first=True;self.map.redraw()
 def zoom_china(self):self.map.page().runJavaScript('map.fitBounds([[73,18],[135,54]],{padding:80,maxZoom:12})')
 def import_file(self):
  path,_=QFileDialog.getOpenFileName(self,'导入 GeoJSON 图层',str(ROOT/'data'),'GeoJSON (*.geojson *.json)')
  if not path:return
  try:self.map.imported=json.loads(Path(path).read_text(encoding='utf-8'));self.map.redraw();self.status.setText(f"已叠加导入 {len(self.map.imported.get('features',[]))} 个 GeoJSON 要素。")
  except Exception as e:QMessageBox.critical(self,'导入失败',str(e))
 def export_visible(self):
  path,_=QFileDialog.getSaveFileName(self,'导出当前可见图层',str(ROOT/'data/export.geojson'),'GeoJSON (*.geojson)')
  if not path:return
  features=[*self.map.imported['features']];features += [x for x in self.map.osm_metro['features'] if self.map.flags['metro_lines'] and x['properties']['route_relation_id'] in self.map.visible_lines];features += [x for x in self.map.metro_stations['features'] if self.map.flags['metro_stations'] and any(i in self.map.visible_lines for i in x['properties'].get('route_relation_ids',[]))];features += self.map.construction_metro['features'] if self.map.flags['construction'] else [];Path(path).write_text(json.dumps({'type':'FeatureCollection','features':features},ensure_ascii=False,indent=2),encoding='utf-8');self.statusBar().showMessage(f'已导出 {len(features)} 个要素：{path}',6000)
 def clear_import(self):self.map.imported=dict(EMPTY);self.map.redraw()
 def import_osm(self):
  try:r=fetch_highspeed_railway(116.1,39.6,116.8,40.1,15);save_raw_geojson(r,ROOT/'data/raw/osm/beijing_highspeed.geojson');self.map.imported={'type':'FeatureCollection','features':[ *self.map.imported['features'],*r.geojson['features']]};self.map.redraw()
  except Exception as e:QMessageBox.critical(self,'OSM 导入失败',str(e))
 def show_topology(self):
  from railscope.services.topology import validate_topology
  self.topology_text.setText(json.dumps(validate_topology(self.repo),ensure_ascii=False,indent=2));self.open(3)
 def about(self):QMessageBox.information(self,'数据署名与图层说明','地图与地铁数据：© OpenStreetMap contributors，ODbL 1.0。\n在建线：深灰色虚线，只来自明确施工标记。\n站点区域边界是 POI 可视范围环，不是法定用地边界。')
 def choose(self,item):self.selected=str(item.data(Qt.ItemDataRole.UserRole));self.refresh()
 def dispatch(self,k,v):add_event(self.repo,DispatchEvent(f'event-{uuid4()}',SCENARIO,self.selected,k,new_value=v));self.refresh()
 def delay(self):self.dispatch('delay_train',300)
 def hold(self):self.dispatch('hold_train',(self.repo.train_runs[self.selected].origin_station_id,300))
 def cancel(self):self.dispatch('cancel_train',True)
 def reset(self):reset(self.repo,SCENARIO);self.refresh()
 def refresh(self):
  self.trains.blockSignals(True);self.trains.clear()
  for r in self.repo.train_runs.values():i=QListWidgetItem(f'G{r.train_number}     {r.service_date}');i.setData(Qt.ItemDataRole.UserRole,r.id);self.trains.addItem(i);self.trains.setCurrentItem(i) if r.id==self.selected else None
  self.trains.blockSignals(False);r=self.repo.train_runs[self.selected];eff=effective_run(self.repo,SCENARIO,self.selected);self.detail.setText(f"G{r.train_number}  ·  {r.service_date}\n{self.repo.stations[r.origin_station_id].name} → {self.repo.stations[r.destination_station_id].name}\n状态：{'已取消' if eff.cancelled else '有效运行'}\n径路：{len(self.repo.routes[r.route_path_id].edge_refs)} 个拓扑边");self.conflicts.clear();self.occupancies.clear();self.stops.clear()
  for c in self.repo.conflicts:i=QListWidgetItem(f'{c.conflict_type} | G{c.train_run_a[-3:]}/G{c.train_run_b[-3:]} | {clock(c.start_time_s)}–{clock(c.end_time_s)}');i.setForeground(QColor(COLORS['warn'] if c.severity=='medium' else COLORS['conflict']));self.conflicts.addItem(i)
  for o in self.repo.occupancies:self.occupancies.addItem(f'G{o.train_run_id[-3:]} · {o.resource_id} · {clock(o.start_time_s)}–{clock(o.end_time_s)} · {o.direction}')
  for s in eff.stops:self.stops.addItem(f'{s.sequence}. {self.repo.stations[s.station_id].name} 到 {clock(s.arrival_time_s)} 发 {clock(s.departure_time_s)}')
  self.status.setText(f'已计算 {len(self.repo.occupancies)} 条占用记录 / {len(self.repo.conflicts)} 个冲突\n调度事件不会修改原始时刻表。');self.map.selected=self.selected;self.map.redraw()
def main():app=QApplication(sys.argv);w=Desk();w.show();raise SystemExit(app.exec())
if __name__=='__main__':main()
