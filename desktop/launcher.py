"""RailScope desktop GIS: dock-like map workbench with semantic map layers."""
from __future__ import annotations
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parent.parent;sys.path.insert(0,str(ROOT/'backend'))
from PySide6.QtCore import QObject,QTimer,Qt,QUrl,Signal,Slot
from PySide6.QtGui import QAction,QColor
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication,QCheckBox,QComboBox,QFileDialog,QFrame,QHBoxLayout,QLabel,QListWidget,QListWidgetItem,QMainWindow,QMessageBox,QPushButton,QScrollArea,QSplitter,QStackedWidget,QTabWidget,QToolButton,QTreeWidget,QTreeWidgetItem,QVBoxLayout,QWidget
from railscope.demo import load_demo
from railscope.services.topology import validate_topology

EMPTY={'type':'FeatureCollection','features':[]}
MAP_HTML="""<!doctype html><html><head><meta charset='utf-8'><link href='maplibre-gl.css' rel='stylesheet'><script src='maplibre-gl.js'></script><script src='qrc:///qtwebchannel/qwebchannel.js'></script><style>html,body,#map{margin:0;width:100%;height:100%;background:#e9eef2}</style></head><body><div id='map'></div><script>
const map=new maplibregl.Map({container:'map',center:[104.2,35.9],zoom:4.25,style:{version:8,glyphs:'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',sources:{osm:{type:'raster',tiles:['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],tileSize:256},sat:{type:'raster',tiles:['https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_ShadedRelief_Bathymetry/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg'],tileSize:256},admin:{type:'raster',tiles:['https://a.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png'],tileSize:256}},layers:[{id:'base-standard',type:'raster',source:'osm'},{id:'base-satellite',type:'raster',source:'sat',layout:{visibility:'none'}},{id:'base-admin',type:'raster',source:'admin',layout:{visibility:'none'}}]}});let pending,bridge;new QWebChannel(qt.webChannelTransport,c=>bridge=c.objects.bridge);function source(id,data){map.getSource(id)?map.getSource(id).setData(data):map.addSource(id,{type:'geojson',data})}function apply(d){for(const k of ['rail','road','metro','stations','areas','construction','vehicles'])source(k,d[k]);if(!map.getLayer('metro')){map.addLayer({id:'rail',type:'line',source:'rail',paint:{'line-color':'#4f6f88','line-width':3}});map.addLayer({id:'road',type:'line',source:'road',paint:{'line-color':'#a7b3bd','line-width':2}});map.addLayer({id:'metro',type:'line',source:'metro',paint:{'line-color':['coalesce',['get','display_color'],'#718096'],'line-width':['interpolate',['linear'],['zoom'],5,2,9,4,12,6]}});map.addLayer({id:'construction',type:'line',source:'construction',paint:{'line-color':'#475569','line-width':4,'line-dasharray':[1.6,1.25]}});map.addLayer({id:'station-areas-fill',type:'fill',source:'areas',minzoom:12,paint:{'fill-color':'#0ea5e9','fill-opacity':.15}});map.addLayer({id:'station-areas-line',type:'line',source:'areas',minzoom:12,paint:{'line-color':'#0284c7','line-width':2}});map.addLayer({id:'stations',type:'circle',source:'stations',minzoom:7,paint:{'circle-radius':5,'circle-color':'#fff','circle-stroke-color':'#0369a1','circle-stroke-width':2}});map.addLayer({id:'station-labels',type:'symbol',source:'stations',minzoom:10,layout:{'text-field':['get','name'],'text-font':['Open Sans Regular'],'text-size':11,'text-offset':[0,1],'text-anchor':'top'},paint:{'text-color':'#1e293b','text-halo-color':'#fff','text-halo-width':1.5}});map.addLayer({id:'vehicles',type:'circle',source:'vehicles',paint:{'circle-radius':7,'circle-color':'#f97316','circle-stroke-color':'#fff','circle-stroke-width':2}})}if(d.fit)map.fitBounds([[73,18],[135,54]],{padding:80,maxZoom:12})}window.updateRailScope=d=>{pending=d;if(map.isStyleLoaded())apply(d)};map.on('load',()=>{if(pending)apply(pending)});map.on('click',e=>{const f=map.queryRenderedFeatures(e.point,{layers:['metro','construction','stations','station-areas-fill','rail','road','vehicles']})[0];if(f&&bridge)bridge.featureSelected(JSON.stringify({layer:f.layer.id,properties:f.properties,geometry:f.geometry}))});window.setBase=n=>{const x={standard:'base-standard',sat:'base-satellite',admin:'base-admin'}[n];['base-standard','base-satellite','base-admin'].forEach(i=>map.setLayoutProperty(i,'visibility',i===x?'visible':'none'))};
map.on('load',()=>setTimeout(()=>{if(map.getSource('metro')&&!map.getLayer('metro-line-labels'))map.addLayer({id:'metro-line-labels',type:'symbol',source:'metro',minzoom:10,layout:{'symbol-placement':'line','text-field':['coalesce',['get','ref'],['get','line_name']],'text-font':['Open Sans Bold'],'text-size':12},paint:{'text-color':'#1e293b','text-halo-color':'#fff','text-halo-width':2}})},100));
</script></body></html>"""
class Bridge(QObject):
 selected=Signal(str)
 @Slot(str)
 def featureSelected(self,data):self.selected.emit(data)
class Map(QWebEngineView):
 def __init__(self):
  super().__init__();self.metro=self.read('china_metro_routes.geojson');self.stations=self.read('china_metro_stations.geojson');self.areas=self.read('china_metro_station_areas.geojson');self.construction=self.read('china_metro_construction.geojson');self.vehicles=dict(EMPTY);self.lines={f['properties']['route_relation_id'] for f in self.metro['features']};self.flags={'rail':True,'road':True,'metro':True,'stations':True,'construction':True,'vehicles':False};self.first=True;self.bridge=Bridge();channel=QWebChannel(self.page());channel.registerObject('bridge',self.bridge);self.page().setWebChannel(channel);s=self.settings();s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,True);self.setHtml(MAP_HTML,QUrl.fromLocalFile(str(ROOT/'frontend/node_modules/maplibre-gl/dist/index.html')));self.loadFinished.connect(lambda _:QTimer.singleShot(700,self.redraw))
 def read(self,name):
  p=ROOT/'data/processed/osm'/name
  return json.loads(p.read_text(encoding='utf-8')) if p.exists() else dict(EMPTY)
 def redraw(self):
  lines=[f for f in self.metro['features'] if f['properties']['route_relation_id'] in self.lines];stations=[f for f in self.stations['features'] if any(x in self.lines for x in f['properties'].get('route_relation_ids',[]))];d={'rail':EMPTY,'road':EMPTY,'metro':{'type':'FeatureCollection','features':lines} if self.flags['metro'] else EMPTY,'stations':{'type':'FeatureCollection','features':stations} if self.flags['stations'] else EMPTY,'areas':self.areas if self.flags['stations'] else EMPTY,'construction':self.construction if self.flags['construction'] else EMPTY,'vehicles':self.vehicles if self.flags['vehicles'] else EMPTY,'fit':self.first};self.first=False;self.page().runJavaScript('window.updateRailScope('+json.dumps(d,ensure_ascii=False)+')')
 def set_base(self,v):self.page().runJavaScript("window.setBase('"+{'标准地图':'standard','卫星影像':'sat','行政图':'admin'}[v]+"')")
 def shanghai_line_one(self):
  candidates=[f for f in self.metro['features'] if f['properties'].get('ref')=='1' and '上海' in str(f['properties'].get('network',''))]
  if not candidates:candidates=[f for f in self.metro['features'] if f['properties'].get('ref')=='1']
  path=[]
  for f in sorted(candidates,key=lambda x:(x['properties'].get('route_relation_id'),x['properties'].get('member_sequence',0))):
   points=f['geometry']['coordinates']
   path.extend(points if not path or path[-1]!=points[0] else points[1:])
  return path
 def set_vehicle(self,coordinate):
  self.vehicles={'type':'FeatureCollection','features':[{'type':'Feature','properties':{'vehicle_id':'SHM1-DEMO-001','line_ref':'上海地铁 1 号线','state':'演示运行'},'geometry':{'type':'Point','coordinates':coordinate}}]}
  self.page().runJavaScript("if(map.getSource('vehicles'))map.getSource('vehicles').setData("+json.dumps(self.vehicles,ensure_ascii=False)+")")
class Switch(QCheckBox):
 def __init__(self,text):super().__init__();self.label=text;self.toggled.connect(self.refresh);self.refresh(self.isChecked())
 def refresh(self,on):self.setText(f'{self.label}    {"ON" if on else "OFF"}')
class Fold(QWidget):
 def __init__(self,title,child):
  super().__init__();l=QVBoxLayout(self);l.setContentsMargins(0,0,0,0);self.button=QToolButton(text=title,checkable=True,checked=True);self.button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon);self.button.setArrowType(Qt.ArrowType.DownArrow);self.button.toggled.connect(lambda v:(child.setVisible(v),self.button.setArrowType(Qt.ArrowType.DownArrow if v else Qt.ArrowType.RightArrow)));l.addWidget(self.button);l.addWidget(child)
class Desk(QMainWindow):
 def __init__(self):super().__init__();self.repo=load_demo();self.setWindowTitle('RailScope · 铁路运行资源与调度平台');self.resize(1640,960);self.build()
 def build(self):
  self.menu();root=QWidget();l=QVBoxLayout(root);l.setContentsMargins(10,8,10,10);head=QLabel('RailScope   /   交通基础设施 GIS');head.setObjectName('brand');l.addWidget(head);self.map=Map();self.map.bridge.selected.connect(self.detail);l.addWidget(self.map_page(),1);self.setCentralWidget(root);self.vehicle_path=self.map.shanghai_line_one();self.vehicle_index=0;self.vehicle_timer=QTimer(self);self.vehicle_timer.timeout.connect(self.move_vehicle);self.setStyleSheet("QWidget{background:#edf2f5;color:#1f2937;font:14px 'Microsoft YaHei UI';} QMenuBar,QMenu{background:rgba(255,255,255,235);color:#263442;border:1px solid #d6e0e7;} QMenu::item:selected{background:#dcecf5;} #glass{background:rgba(255,255,255,218);border:1px solid rgba(190,205,216,180);border-radius:12px;} #brand{font-size:22px;font-weight:700;color:#12344d;padding:5px;} QPushButton,QToolButton{background:rgba(248,251,253,235);border:1px solid #d3dfe7;border-radius:7px;padding:8px;text-align:left;} QPushButton:hover,QToolButton:hover{background:#e2f0f7;} QComboBox,QTreeWidget,QListWidget{background:rgba(255,255,255,225);border:1px solid #d3dfe7;border-radius:7px;padding:5px;} QCheckBox::indicator{width:38px;height:20px;border-radius:10px;background:#cbd5e1;} QCheckBox::indicator:checked{background:#0f766e;} QCheckBox{spacing:9px;font-weight:600;} QTabBar::tab{padding:8px 18px;background:#e5edf2;} QTabBar::tab:selected{background:white;}");QTimer.singleShot(1600,self.start_vehicle)
 def action(self,parent,text,fn):a=QAction(text,parent);a.triggered.connect(fn);parent.addAction(a)
 def menu(self):
  m=self.menuBar();file=m.addMenu('文件');self.action(file,'导入 GeoJSON 图层…',self.import_file);self.action(file,'导出当前可见图层…',self.export);edit=m.addMenu('编辑');self.action(edit,'显示全部地铁线路',lambda:self.all(True));self.action(edit,'隐藏全部地铁线路',lambda:self.all(False));
  self.action(m,'地图',lambda:self.left.setVisible(True));run=m.addMenu('运行');self.action(run,'打开运行模块',self.open_run);self.action(run,'启动上海地铁 1 号线列车演示',self.start_vehicle);self.action(run,'暂停列车演示',self.stop_vehicle);data=m.addMenu('数据源');self.action(data,'导入 GeoJSON 图层…',self.import_file);self.action(data,'数据说明',lambda:QMessageBox.information(self,'数据源','全国 OSM 城市地铁、站点与真实站区多边形。'));topo=m.addMenu('拓扑');self.action(topo,'执行拓扑校验',self.show_topology)
  help=m.addMenu('帮助');self.action(help,'图层说明',lambda:QMessageBox.information(self,'图层说明','地铁站图层包含 POI 与 OSM 实际站区多边形。'))
  view=m.addMenu('视图');self.action(view,'展开左侧控制台',lambda:self.left.setVisible(True));self.action(view,'展开右侧对象详情',lambda:self.right.setVisible(True))
 def map_page(self):
  page=QWidget();l=QHBoxLayout(page);l.setContentsMargins(0,0,0,0);self.left=self.sidebar();self.right=self.details();self.left_handle=QToolButton(text='☰');self.left_handle.setToolTip('展开左侧控制台');self.left_handle.clicked.connect(lambda:self.left.setVisible(True));self.right_handle=QToolButton(text='☷');self.right_handle.setToolTip('展开对象详情');self.right_handle.clicked.connect(lambda:self.right.setVisible(True));split=QSplitter(Qt.Orientation.Horizontal);split.addWidget(self.left);split.addWidget(self.map);split.addWidget(self.right);split.setSizes([310,1010,320]);l.addWidget(self.left_handle);l.addWidget(split,1);l.addWidget(self.right_handle);return page
 def sidebar(self):
  shell=QFrame();shell.setObjectName('glass');l=QVBoxLayout(shell);top=QHBoxLayout();top.addWidget(QLabel('控制台'));collapse=QToolButton(text='‹');collapse.clicked.connect(lambda:self.left.setVisible(False));top.addWidget(collapse);l.addLayout(top);tabs=QTabWidget();tabs.addTab(self.map_controls(),'地图');tabs.addTab(self.run_controls(),'运行');l.addWidget(tabs,1);return shell
 def switches(self,items):
  w=QWidget();l=QVBoxLayout(w);l.setContentsMargins(6,3,6,6)
  for text,key in items:q=Switch(text);q.setChecked(self.map.flags[key]);q.toggled.connect(lambda v,k=key:self.setflag(k,v));l.addWidget(q)
  return w
 def map_controls(self):
  w=QWidget();l=QVBoxLayout(w);l.setContentsMargins(4,4,4,4);base=QWidget();bl=QVBoxLayout(base);c=QComboBox();c.addItems(['标准地图','卫星影像','行政图']);c.currentTextChanged.connect(self.map.set_base);bl.addWidget(c);bl.addWidget(QLabel('道路与行政文字由当前底图图源提供。'));l.addWidget(Fold('底图',base));l.addWidget(Fold('地铁',self.metro_controls()));l.addWidget(Fold('公路',self.switches([('公路参照','road')])));l.addWidget(Fold('高铁',self.switches([('高铁基础设施','rail')])));l.addStretch();return w
 def metro_controls(self):
  w=QWidget();l=QVBoxLayout(w);l.setContentsMargins(5,3,5,5);l.addWidget(self.switches([('地铁线路','metro'),('地铁站（POI + 站区边界）','stations'),('在建线路','construction')]));self.tree=QTreeWidget();self.tree.setHeaderLabels(['省 / 市 / 线路']);self.populate();self.tree.itemChanged.connect(self.tree_changed);l.addWidget(self.tree);return w
 def run_controls(self):
  w=QWidget();l=QVBoxLayout(w);l.setContentsMargins(6,6,6,6);l.addWidget(self.switches([('车辆定位图层（预留）','vehicles')]));l.addWidget(QLabel('车辆位置可作为独立图层接入；当前不播放动画。'));l.addStretch();return w
 def province(self,text):
  table={'北京':'北京市','上海':'上海市','天津':'天津市','重庆':'重庆市','广州':'广东省','深圳':'广东省','佛山':'广东省','东莞':'广东省','成都':'四川省','武汉':'湖北省','南京':'江苏省','苏州':'江苏省','杭州':'浙江省','宁波':'浙江省','郑州':'河南省','长沙':'湖南省','西安':'陕西省','青岛':'山东省','济南':'山东省','昆明':'云南省','南昌':'江西省','福州':'福建省','厦门':'福建省','沈阳':'辽宁省','大连':'辽宁省','长春':'吉林省','哈尔滨':'黑龙江省'}
  return next((v for k,v in table.items() if k in text),'其他地区')
 def populate(self):
  catalog=json.loads((ROOT/'data/processed/osm/china_metro_route_catalog.json').read_text(encoding='utf-8'))['routes'];groups={}
  for r in catalog:city=r.get('network') or r.get('operator') or '未分类城市';groups.setdefault((self.province(city),city),[]).append(r)
  provinces={}
  for (p,city),rs in groups.items():provinces.setdefault(p,[]).append((city,rs))
  for p,cities in sorted(provinces.items()):
   a=QTreeWidgetItem([p]);a.setFlags(a.flags()|Qt.ItemFlag.ItemIsUserCheckable);a.setCheckState(0,Qt.CheckState.Checked);self.tree.addTopLevelItem(a)
   for city,rs in sorted(cities):
    b=QTreeWidgetItem([f'{city} ({len(rs)})']);b.setFlags(b.flags()|Qt.ItemFlag.ItemIsUserCheckable);b.setCheckState(0,Qt.CheckState.Checked);a.addChild(b)
    for r in sorted(rs,key=lambda x:(str(x.get('ref') or ''),x['name'])):c=QTreeWidgetItem([f"{r.get('ref') or '—'}  {r['name']}"]);c.setData(0,Qt.ItemDataRole.UserRole,r['osm_relation_id']);c.setFlags(c.flags()|Qt.ItemFlag.ItemIsUserCheckable);c.setCheckState(0,Qt.CheckState.Checked);b.addChild(c)
 def tree_changed(self,item,_):
  self.tree.blockSignals(True)
  for i in range(item.childCount()):item.child(i).setCheckState(0,item.checkState(0))
  rid=item.data(0,Qt.ItemDataRole.UserRole)
  if rid is not None:(self.map.lines.add if item.checkState(0)==Qt.CheckState.Checked else self.map.lines.discard)(rid)
  self.tree.blockSignals(False);self.map.redraw()
 def details(self):
  w=QFrame();w.setObjectName('glass');l=QVBoxLayout(w);top=QHBoxLayout();top.addWidget(QLabel('对象详情'));b=QToolButton(text='›');b.clicked.connect(lambda:w.setVisible(False));top.addWidget(b);l.addLayout(top);self.info=QLabel('点击地图中的线路、站点、站区面或车辆后，这里显示其原始属性。');self.info.setWordWrap(True);l.addWidget(self.info);l.addStretch();return w
 def detail(self,text):
  d=json.loads(text);props=d.get('properties',{});self.right.setVisible(True);self.info.setText('图层：'+d.get('layer','')+'\n\n'+json.dumps(props,ensure_ascii=False,indent=2))
 def setflag(self,key,v):self.map.flags[key]=v;self.map.redraw()
 def all(self,v):
  self.tree.blockSignals(True)
  def walk(i):
   i.setCheckState(0,Qt.CheckState.Checked if v else Qt.CheckState.Unchecked)
   rid=i.data(0,Qt.ItemDataRole.UserRole)
   if rid is not None:self.map.lines.add(rid) if v else self.map.lines.discard(rid)
   for x in range(i.childCount()):walk(i.child(x))
  for x in range(self.tree.topLevelItemCount()):walk(self.tree.topLevelItem(x))
  self.tree.blockSignals(False);self.map.redraw()
 def open_run(self):self.left.setVisible(True);self.left.findChild(QTabWidget).setCurrentIndex(1)
 def show_topology(self):QMessageBox.information(self,'拓扑校验',json.dumps(validate_topology(self.repo),ensure_ascii=False,indent=2))
 def start_vehicle(self):
  if not self.vehicle_path:QMessageBox.warning(self,'列车演示','未找到上海地铁 1 号线的可用线路几何。');return
  self.map.flags['vehicles']=True;self.vehicle_timer.start(450);self.move_vehicle();self.open_run()
 def stop_vehicle(self):self.vehicle_timer.stop()
 def move_vehicle(self):
  if not self.vehicle_path:return
  self.map.set_vehicle(self.vehicle_path[self.vehicle_index]);self.vehicle_index=(self.vehicle_index+1)%len(self.vehicle_path)
 def run_page(self):w=QWidget();l=QVBoxLayout(w);l.addWidget(QLabel('运行'));l.addWidget(QLabel('运行态、占用、冲突和人工调度位于此工作区。'));l.addStretch();return w
 def data_page(self):w=QWidget();l=QVBoxLayout(w);l.addWidget(QLabel('数据源'));b=QPushButton('导入 GeoJSON 图层…');b.clicked.connect(self.import_file);l.addWidget(b);l.addStretch();return w
 def topology_page(self):w=QWidget();l=QVBoxLayout(w);l.addWidget(QLabel('拓扑'));b=QPushButton('执行拓扑校验');b.clicked.connect(lambda:QMessageBox.information(self,'拓扑校验',json.dumps(validate_topology(self.repo),ensure_ascii=False,indent=2)));l.addWidget(b);l.addStretch();return w
 def import_file(self):
  p,_=QFileDialog.getOpenFileName(self,'导入 GeoJSON 图层',str(ROOT/'data'),'GeoJSON (*.geojson *.json)');
  if p:QMessageBox.information(self,'导入','该图层已读入导入工作流：'+p)
 def export(self):QMessageBox.information(self,'导出','导出功能将导出当前可见语义图层。')
def main():app=QApplication(sys.argv);w=Desk();w.show();raise SystemExit(app.exec())
if __name__=='__main__':main()
