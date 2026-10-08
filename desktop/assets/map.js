'use strict';
const maplibregl = window.maplibregl;
let bridge, map, config, standardStyle, currentBase = 'standard', pendingBase=null, selectedFeature = null, selectedLayer = null;
let selectedFeatures=[],selectionMode='click',boxSelecting=false,suppressMapClick=false;
let running = false, travelled = 0;
const visibility = {metro:false, stations:false, construction:false, rail:false, railConstruction:false,railStationTracks:false,railStations:false,railControlPoints:false,railVehicles:false,railPlan:false,road:false,roadConstruction:false,roadServices:false,university:false, imported:false, vehicles:false};
let railHiddenLineIds=[];
const baseDetails = {roads:true, admin:true, labels:true, buildings:true};
let visibleIds = [], visibleStationIds=[], vectorAvailable = false, sourceReadySent = false, mapErrors = [], mapWarnings = [];
let animationStarted = false, constructionIds=[], operatingMode=false, operatingVehicles=emptyPlaceholder(), operatingClock=25200;
let followTrain=false;
let railVehicleOverlay,metroVehicleOverlay;
let trainFollowMoving=false,trainFollowTimer=null,lastRailVehicleSignature='',lastCameraStatus='';
let focusedBounds=null;
const overlays={title:false,tools:true,legend:true,status:false,scale:true};
let uiInsets={left:78,right:12};
function viewportPadding(extra=32){return {left:uiInsets.left+extra,right:uiInsets.right+extra,top:extra,bottom:extra};}
function setUiInsets(value){
  uiInsets={left:Math.max(0,Math.min(500,Number(value.left)||0)),right:Math.max(0,Math.min(500,Number(value.right)||0))};
  for(const [key,amount] of Object.entries(uiInsets))document.documentElement.style.setProperty(`--${key}-inset`,amount+'px');
  fitFocusedBounds();
}
function setAppearance(theme){
  if(!theme)return;
  for(const key of ['text','muted','accent','surface','edge','hover','selected'])if(/^#[0-9a-f]{6}$/i.test(theme[key]||''))
    document.documentElement.style.setProperty('--'+key,theme[key]);
  const color=theme.surface||'#f2faf8',rgb=[1,3,5].map(start=>parseInt(color.slice(start,start+2),16));
  document.documentElement.style.setProperty('--glass',`rgba(${rgb.join(',')},${theme.glass===false?1:(theme.opacity||86)/100})`);
  document.documentElement.style.setProperty('--ui-density',theme.density==='compact'?'12px':'13px');
}
let chineseMapFamily='Microsoft YaHei UI';
function applyMapFontSizes(){
  const size=config?.fonts?.map?.size;
  if(!size||!map?.getStyle)return;
  for(const layer of map.getStyle().layers||[])if(layer.type==='symbol'&&layer.source!=='openmaptiles')
    map.setLayoutProperty(layer.id,'text-size',size);
}
function setFonts(fonts,reload=false){
  config.fonts=fonts;
  const navigation=fonts.navigation;
  if(navigation){document.documentElement.style.setProperty('--nav-font-family',navigation.family);document.documentElement.style.setProperty('--nav-font-size',navigation.size+'px');}
  applyMapFontSizes();
  const family=fonts.map?.family;
  if(reload&&family&&family!==chineseMapFamily){
    chineseMapFamily=family;
    map.setStyle(map.getStyle(),{diff:false,localIdeographFontFamily:family});
  }
}
let vehicleAppearance={size:14,style:'glow'};
function setOverlay(key,on){
  if(!(key in overlays))return;
  overlays[key]=!!on;
  const element=document.getElementById({'title':'map-title','tools':'map-tools','legend':'map-legend','status':'map-status'}[key]);
  if(element)element.hidden=!on;
  if(key==='scale')for(const node of document.querySelectorAll('.maplibregl-ctrl-scale'))node.style.display=on?'':'none';
  if(key==='tools'&&!on)document.getElementById('location-panel').hidden=true;
}
function applyVehicleAppearance(){
  if(!map.getLayer('vehicles'))return;
  const {size,style}=vehicleAppearance;
  map.setPaintProperty('vehicles','circle-radius',size/2);
  map.setPaintProperty('vehicles','circle-opacity',style==='ring'?0:1);
  map.setPaintProperty('vehicles','circle-stroke-color',style==='ring'?'#0c9184':'#ffffff');
  map.setPaintProperty('vehicles','circle-stroke-width',style==='ring'?2.5:2);
  map.setPaintProperty('vehicles-halo','circle-radius',size*.95);
  map.setPaintProperty('vehicles-halo','circle-opacity',style==='glow'?.16:0);
  map.setLayoutProperty('vehicles','visibility',visibility.vehicles&&style!=='train'?'visible':'none');
  map.setLayoutProperty('vehicles-symbol','visibility',visibility.vehicles&&style==='train'?'visible':'none');
  map.setLayoutProperty('vehicles-symbol','icon-size',size/24);
}
function emptyPlaceholder(){return {type:'FeatureCollection',features:[]};}
const empty = {type:'FeatureCollection',features:[]};
const infrastructureHistory=new RailScopeHistory.History();
const historyResident=new Map();
const historyLayerParts=new Map();
const historicalModes={metro:'metro',stations:'metro',areas:'metro',construction:'metro',rail:'rail',railPoints:'rail',railPlatforms:'rail',railStationAreas:'rail',railSignalBoxes:'rail',road:'road',roadServices:'road',imported:'other'};
function hasRailHistory(){return [...infrastructureHistory.aliases.values()].some(record=>record.mode==='rail'&&(record.opened||record.closed||record.construction_started));}
function applyInfrastructureHistory(day,records){
  const old=new Map([...infrastructureHistory.aliases.values()].map(record=>[record.id,record]));
  const changed=new Set();
  if(day!==infrastructureHistory.day)for(const mode of Object.values(historicalModes))changed.add(mode);
  const next=new Map(records.map(record=>[record.id,record]));
  for(const id of new Set([...old.keys(),...next.keys()])){
    const a=old.get(id),b=next.get(id);
    if(JSON.stringify(a)!==JSON.stringify(b)){if(a)changed.add(a.mode);if(b)changed.add(b.mode);}
  }
  infrastructureHistory.configure(day,records);
  for(const [id,data] of [...historyResident])if(changed.has(historicalModes[id]))map.getSource(id)?.setData(data);
}
function historyData(id,data){
  if(!data||!Array.isArray(data.features)||!historicalModes[id])return data;
  historyResident.set(id,data);
  const projected=infrastructureHistory.apply(data,historicalModes[id]);
  if(['rail','metro','construction','road'].includes(id)){
    const split=projected.features.filter(f=>f.properties.history_state&&f.properties.history_state!=='unknown');
    historyResident.set(id+':projected',split);
    updateHistoryLines();
    return {...projected,features:projected.features.filter(f=>!split.includes(f))};
  }
  return projected;
}
function historyStateFilter(mode){
  const inactive=['in',['get','history_state'],['literal',['construction','planned','disused']]];
  const operating=mode==='rail'?(visibility.rail||visibility.railStationTracks):visibility[mode];
  const building=mode==='metro'?visibility.construction:visibility[mode+'Construction'];
  const zoom=mode==='metro'?(minZooms.metroLines??0):mode==='rail'?(minZooms.railLines??0):(minZooms.roads??0);
  const constructionZoom=mode==='metro'?(minZooms.metroConstruction??0):zoom;
  return ['any',['all',['!',inactive],['literal',!!operating],['>=',['zoom'],zoom]],['all',inactive,['literal',!!building],['>=',['zoom'],constructionZoom]]];
}
function roadRouteFilter(){
  const keys=roadRouteSelection?[roadRouteSelection]:roadVisibleRoutes;
  return keys===null?['literal',true]:keys.length?['any',...keys.map(key=>['in',key,['coalesce',['get','route_keys'],['literal',[]]]])]:['literal',false];
}
function applyRoadRouteFilter(){
  const selected=roadRouteFilter();
  for(const [id,building] of [['road',false],['road-labels',false],['road-construction',true],['road-construction-labels',true]])
    if(map?.getLayer(id))map.setFilter(id,['all',[building?'==':'!=',['get','construction'],true],selected]);
  for(const id of ['history-road','history-road-labels'])if(map?.getLayer(id))map.setFilter(id,['all',historyStateFilter('road'),selected]);
}
function updateHistoryLines(){
  for(const [mode,sources,on] of [['rail',['rail'],visibility.rail||visibility.railConstruction||visibility.railStationTracks],['metro',['metro','construction'],visibility.metro||visibility.construction],['road',['road'],visibility.road||visibility.roadConstruction]]){
    const parts=sources.map(id=>historyResident.get(id+':projected')||empty.features),old=historyLayerParts.get(mode);
    const source=map?.getSource('history-'+mode);
    if(source&&(!old||parts.some((part,index)=>part!==old[index]))){source.setData({type:'FeatureCollection',features:parts.flat()});historyLayerParts.set(mode,parts);}
    if(mode==='road')applyRoadRouteFilter();
    if(mode==='metro'){
      const selected=['any',['in',['get','route_relation_id'],['literal',visibleIds]],
        ['in',['get','osm_way_id'],['literal',constructionIds]]];
      for(const id of ['history-metro','history-metro-labels'])if(map?.getLayer(id))map.setFilter(id,['all',historyStateFilter(mode),selected]);
    }
    for(const id of ['history-'+mode,'history-'+mode+'-labels'])if(map?.getLayer(id))map.setLayoutProperty(id,'visibility',on&&(!id.endsWith('-labels')||config?.railPointStyles?.labels?.show_line_names!==false)?'visible':'none');
  }
  if(map?.getLayer('history-rail-stripes'))map.setLayoutProperty('history-rail-stripes','visibility',visibility.rail||visibility.railConstruction||visibility.railStationTracks?'visible':'none');
}
function updateHistoryPaints(){
  for(const [mode,base] of [['rail','rail'],['metro','metro'],['road','road']]){
    if(!map?.getLayer('history-'+mode)||!map.getLayer(base))continue;
    map.setPaintProperty('history-'+mode,'line-color',['case',['==',['get','history_state'],'disused'],'#8b969c',map.getPaintProperty(base,'line-color')]);
    map.setPaintProperty('history-'+mode,'line-width',map.getPaintProperty(base,'line-width'));
    const raw=map.getPaintProperty(base,'line-dasharray');
    const dash=raw?(Array.isArray(raw)&&typeof raw[0]!=='string'?['literal',raw]:raw):['literal',[1,0]];
    map.setPaintProperty('history-'+mode,'line-dasharray',['case',['in',['get','history_state'],['literal',['construction','disused']]],['literal',[2,1.5]],dash]);
  }
  if(map?.getLayer('history-rail-stripes'))map.setPaintProperty('history-rail-stripes','line-opacity',map.getPaintProperty('rail-stripes','line-opacity')??1);
  for(const layer of map?.getStyle()?.layers||[]){
    if(!historicalModes[layer.source]||layer.type==='line')continue;
    const properties={circle:['circle-color','circle-stroke-color'],fill:['fill-color','fill-outline-color'],symbol:['text-color']}[layer.type]||[];
    for(const property of properties){
      const value=map.getPaintProperty(layer.id,property);
      if(value===undefined||value===null)continue;
      if(Array.isArray(value)&&value[0]==='case'&&value[1]?.[1]?.[1]==='history_state')continue;
      map.setPaintProperty(layer.id,property,['case',['==',['get','history_state'],'disused'],'#8b969c',value]);
    }
  }
  if(map?.getLayer('history-rail-labels'))map.setLayoutProperty('history-rail-labels','text-size',Number(config?.railPointStyles?.labels?.line_font_size)||11);
}
function installHistoryLayers(){
  historyLayerParts.clear();
  for(const mode of ['rail','metro','road']){
    if(!map.getSource('history-'+mode))map.addSource('history-'+mode,{type:'geojson',data:empty});
    const base=mode==='rail'?'rail':mode==='metro'?'metro':'road';
    addLayer({id:'history-'+mode,type:'line',source:'history-'+mode,paint:{
      'line-color':['case',['==',['get','history_state'],'disused'],'#8b969c',map.getPaintProperty(base,'line-color')||'#466979'],
      'line-width':map.getPaintProperty(base,'line-width')||2,
      'line-dasharray':['case',['in',['get','history_state'],['literal',['construction','disused']] ],['literal',[2,1.5]],['literal',[1,0]]]}});
    addLayer({id:'history-'+mode+'-labels',type:'symbol',source:'history-'+mode,minzoom:7,layout:{'symbol-placement':'line','text-field':['coalesce',['get','line_display_name'],['get','display_name'],['get','line_name'],['get','name'],''],'text-font':vectorAvailable?['Noto Sans Regular']:['Open Sans Regular'],'text-size':12},paint:{'text-color':'#425d6b','text-halo-color':'#fff','text-halo-width':2}});
  }
  for(const [id,data] of [...historyResident])if(historicalModes[id])map.getSource(id)?.setData(data);
  addLayer({id:'history-rail-stripes',type:'line',source:'history-rail',minzoom:13,filter:['==',['get','history_state'],'operating'],paint:{'line-color':'#fff','line-width':2,'line-dasharray':[2,2]}});
  updateHistoryPaints();applyRailWays();applyMinZooms();
  updateHistoryLines();
}
const detailGroup = layer => {
  const id = layer.id.toLowerCase(), sl = layer['source-layer'] || '';
  if (/boundary/.test(id)) return 'admin';
  if (/^label_(state|country|city|town|village|other)/.test(id)) return 'admin';
  if (sl === 'transportation' || sl === 'transportation_name' || /^road_/.test(id)) return 'roads';
  if (sl === 'building') return 'buildings';
  if (layer.type === 'symbol') return 'labels';
  return '';
};
function report(method, ...args) { if (bridge && typeof bridge[method] === 'function') bridge[method](...args); }
async function queryViewport(endpoint,params,signal){
  const response=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(params)),signal});
  if(!response.ok)throw new Error(`${endpoint} 视窗查询失败 (${response.status})`);
  return response.json();
}
function styleWorkbench(style) {
  // Text-only base map avoids a remote sprite blocking the local GIS layers.
  delete style.sprite;
  for (const layer of style.layers) {
    const sl=layer['source-layer'];
    if(layer.layout) delete layer.layout['icon-image'];
    const paint=layer.paint || (layer.paint={});
    delete paint['fill-pattern'];
    if(layer.type==='background')paint['background-color']='#f4f7f8';
    if(layer.type==='fill') {
      if(sl==='water')paint['fill-color']='#c8dee8';
      else if(sl==='building')paint['fill-color']='#e3e9ed';
      else if(sl==='landuse')paint['fill-color']='#edf0ed';
      else if(sl==='landcover'||sl==='park')paint['fill-color']='#e3ede6';
      else if(sl==='transportation')paint['fill-color']='#eef2f3';
      // Pattern-only fills otherwise fall back to MapLibre's opaque black
      // after the sprite is removed (pedestrian plazas, courtyards, etc.).
      else if(paint['fill-color']===undefined)paint['fill-color']='#e3e9ed';
    }
    if(layer.type==='line'&&sl==='transportation')paint['line-color']=layer.id.includes('casing')?'#ccd6dd':'#fafcfd';
    if(layer.type==='line'&&sl==='waterway')paint['line-color']='#b9d4e0';
    if(layer.type==='symbol') {
      // Owned infrastructure labels come only from the selectable overlays.
      const restrictions=[];
      if(sl==='transportation_name')restrictions.push(['!', ['in',['coalesce',['get','class'],''],['literal',['motorway','motorway_link']]]]);
      if(sl==='poi'){
        restrictions.push(['!', ['in',['coalesce',['get','class'],''],['literal',['rail','college','university']]]]);
        restrictions.push(['!', ['in',['coalesce',['get','subclass'],''],['literal',['university','college','railway_station','halt','subway','tram_stop','services','rest_area']]]]);
      }
      if(restrictions.length)layer.filter=['all',layer.filter||['literal',true],...restrictions];

      paint['text-color']='#536774';paint['text-halo-color']='#ffffff';
      if(layer.layout?.['text-field'])layer.layout['text-field']=['coalesce',['get','name:zh'],['get','name'],['get','name:latin']];
    }
  }
  return style;
}
function fallbackStandardStyle(){
  // A local style over the existing vector provider keeps every label controllable.
  // Provider/schema: https://openfreemap.org/quick_start/
  return {version:8,glyphs:'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf',
    sources:{openmaptiles:{type:'vector',url:'https://tiles.openfreemap.org/planet',attribution:'OpenFreeMap © OpenMapTiles · © OpenStreetMap contributors'}},layers:[
      {id:'base-background',type:'background',paint:{'background-color':'#f4f7f8'}},
      {id:'landcover',type:'fill',source:'openmaptiles','source-layer':'landcover',paint:{'fill-color':'#e3ede6','fill-opacity':.6}},
      {id:'water',type:'fill',source:'openmaptiles','source-layer':'water',paint:{'fill-color':'#c8dee8'}},
      {id:'waterway',type:'line',source:'openmaptiles','source-layer':'waterway',paint:{'line-color':'#b9d4e0','line-width':1}},
      {id:'road_reference',type:'line',source:'openmaptiles','source-layer':'transportation',filter:['!=',['get','class'],'rail'],paint:{'line-color':'#d6dfe4','line-width':1}},
      {id:'building',type:'fill',source:'openmaptiles','source-layer':'building',minzoom:14,paint:{'fill-color':'#e3e9ed'}},
      {id:'boundary',type:'line',source:'openmaptiles','source-layer':'boundary',paint:{'line-color':'#b8c9cc','line-width':1,'line-dasharray':[3,2]}},
      {id:'label_city',type:'symbol',source:'openmaptiles','source-layer':'place',layout:{'text-field':['coalesce',['get','name:zh'],['get','name'],''],'text-font':['Noto Sans Regular'],'text-size':12},paint:{'text-color':'#536774','text-halo-color':'#ffffff','text-halo-width':1.5}}
    ]};
}
function rasterStyle(type) {
  if(type!=='satellite')return fallbackStandardStyle();
  const tile='https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2024_3857/default/g/{z}/{y}/{x}.jpg';
  const attribution='<a href="https://cloudless.eox.at/documentation/license">EOxCloudless © EOX</a> (Contains modified Copernicus Sentinel data 2024) · CC BY-NC-SA 4.0';
  return {version:8,glyphs:'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',sources:{base:{type:'raster',tiles:[tile],maxzoom:14,tileSize:256,attribution}},layers:[{id:'base-background',type:'background',paint:{'background-color':'#e8eef1'}},{id:'base-raster',type:'raster',source:'base',paint:{'raster-fade-duration':150}}]};
}
function addSource(id, data) {
  if(map.getSource(id))return;
  const options={type:'geojson',data,generateId:true};
  if(id==='road'&&config?.roadViewport)options.attribution='© OpenStreetMap contributors';
  map.addSource(id,options);
  if(historicalModes[id]){
    const source=map.getSource(id),set=source.setData.bind(source);
    source.setData=data=>set(historyData(id,data));
  }
}
const adminLevels={'admin-province':4,'admin-city':5,'admin-county':6};
let adminController=null,adminTimer=null,adminRequest=0,adminSourceKey=null,adminFeatureCount=0;
function administrativeStyle(type){
  const color={'admin-province':'#53608b','admin-city':'#137b78','admin-county':'#b36b29'}[type];
  return {version:8,glyphs:'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
    sources:{administrative:{type:'geojson',data:empty,attribution:'© OpenStreetMap contributors · Natural Earth 1:10m 陆地轮廓 · 行政边界参考数据'}},
    layers:[{id:'admin-background',type:'background',paint:{'background-color':'#d8e7ed'}},
      {id:'admin-fill',type:'fill',source:'administrative',paint:{'fill-color':['match',['%', ['get','osm_relation_id'],5],0,'#d8e4e8',1,'#e8dec9',2,'#d9e6d2',3,'#e6d8dd','#e1dfec'],'fill-opacity':.8}},
      {id:'admin-boundary-halo',type:'line',source:'administrative',paint:{'line-color':'#fffdf8','line-width':5}},
      {id:'admin-boundary',type:'line',source:'administrative',paint:{'line-color':color,'line-width':2.2}},
      {id:'admin-names',type:'symbol',source:'administrative',layout:{'text-field':['coalesce',['get','display_name'],['get','name']],'text-font':['Open Sans Regular'],'text-size':14,'text-padding':12},paint:{'text-color':color,'text-halo-color':'#fffdf8','text-halo-width':2}}]};
}
function scheduleAdminViewport(){
  clearTimeout(adminTimer);adminController?.abort();++adminRequest;
  if(!adminLevels[currentBase]||graphicsPaused||document.hidden)return;
  adminTimer=setTimeout(updateAdminViewport,180);
}
async function updateAdminViewport(){
  if(!adminLevels[currentBase]||!map.getSource('administrative')||graphicsPaused||document.hidden)return;
  const b=map.getBounds(),zoom=map.getZoom(),level=adminLevels[currentBase];
  const bbox=[Math.max(-180,b.getWest()),Math.max(-85,b.getSouth()),Math.min(180,b.getEast()),Math.min(85,b.getNorth())].map(n=>Number(n.toFixed(4))).join(',');
  const key=JSON.stringify([bbox,level,zoom<7?0:zoom<10?1:2]);
  if(adminSourceKey===key)return;
  adminController?.abort();const controller=new AbortController();adminController=controller;const request=++adminRequest;
  try{
    const response=await fetch('/api/admin?'+new URLSearchParams({bbox,level,zoom}),{signal:controller.signal});
    if(!response.ok)throw new Error('行政边界视窗查询失败');
    const data=await response.json();
    if(request!==adminRequest||level!==adminLevels[currentBase])return;
    map.getSource('administrative')?.setData(data);adminSourceKey=key;adminFeatureCount=data.features.length;
    if(data.missing)report('notice','行政边界尚未就绪；首次选择后在后台建立本地索引。');
    else if(data.truncated)report('notice','行政边界细节已达视窗上限，请放大查看。');
  }catch(error){if(error.name!=='AbortError')report('mapError',String(error));}
}
let railRequest=0;
let railController=null,railTimer=null;
const railSourceKeys=new Map();
const railBudgetStats=new Map();
let railBudgetMessage='';
function updateRailBudgetStatus(){
  const names={rail:'线路',railPoints:'车站与控制点',railPlatforms:'站台',railStationAreas:'站区'};
  const reasons={features:'元素数',bytes:'数据量',vertices:'顶点数',feature_bytes:'单个元素大小',scan:'安全扫描量'};
  const details=[],limited=[];
  for(const [kind,data] of railBudgetStats){
    if(!railSourceVisible(kind))continue;
    const budget=data.budget,usage=budget?.usage;
    const cause=(budget?.reasons||[]).map(key=>reasons[key]||key).join('、');
    details.push(names[kind]+(usage?`：${usage.features}/${budget.limits.features} 个要素，${(usage.bytes/1048576).toFixed(2)} MiB，${usage.vertices} 个顶点`:'')+
      (data.truncated?`；达到${cause||'加载'}上限`:''));
    if(data.truncated)limited.push(names[kind]+(cause?`（${cause}）`:''));
  }
  document.getElementById('map-status').title=details.length?`国铁按数据图层分别限额（含边缘预加载）\n${details.join('\n')}`:'国铁按当前地图范围加载';
  const camera=document.getElementById('camera-status');
  const message=limited.length?`国铁${limited.join('、')}达到上限，部分要素未加载；可放大地图或调整加载上限`:'';
  if(message){camera.textContent=message;report('notice',message);}
  else if(railBudgetMessage){
    if(camera.textContent===railBudgetMessage)camera.textContent=lastCameraStatus||'国铁视窗加载完成';
    report('notice','国铁视窗加载完成');
  }
  railBudgetMessage=message;
}
const entityPresentation=new RailScopeEntityPresentation.EntityPresentation(
  async features=>{
    const values=[];
    // A long visible line can own thousands of features. Bound each read-only
    // request; the presentation controller paints once after the full reply.
    for(let start=0;start<features.length;start+=200){
      const response=await fetch('/api/entity-presentation',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(features.slice(start,start+200))});
      if(!response.ok)throw new Error('对象地图更新失败：'+response.status);
      values.push(...await response.json());
    }
    return values;
  },
  (source,data)=>{map.getSource(source)?.setData(data);refreshSelection();},
  error=>report('mapError',String(error)));
let roadRequest=0,roadController=null,roadTimer=null,roadRouteSelection=null,roadVisibleRoutes=null;
let roadSourceKey=null;
let roadServiceKey=null,roadVisibleServices=[];
let metroRequest=0,metroController=null,metroTimer=null;
let minZooms={};
let universityController=null,universityTimer=null,universityRequest=0,universityKey=null,universitySelection=[];
let universityData=empty;
function scheduleUniversityViewport(){
  clearTimeout(universityTimer);universityController?.abort();++universityRequest;
  const zoom=map.getZoom(),minimum=Math.min(minZooms.universityPois??9,minZooms.universityOutlines??12);
  if(!visibility.university||graphicsPaused||document.hidden||zoom<minimum||!universitySelection.length){
    map.getSource('universities')?.setData(empty);universityData=empty;universityKey=null;refreshSelection();return;
  }
  universityTimer=setTimeout(updateUniversityViewport,180);
}
async function updateUniversityViewport(){
  if(!map.getSource('universities')||!visibility.university)return;
  const controller=new AbortController();universityController=controller;
  const request=++universityRequest,b=map.getBounds(),zoom=map.getZoom();
  const kind=zoom<(minZooms.universityOutlines??12)?'poi':zoom<(minZooms.universityPois??9)?'outline':'all';
  const params=new URLSearchParams({bbox:[b.getWest(),b.getSouth(),b.getEast(),b.getNorth()].join(','),selected:JSON.stringify(universitySelection),kind,zoom:String(zoom)});
  const key=params.toString();if(key===universityKey)return;
  try{
    const data=await queryViewport('/api/universities',params,controller.signal);
    if(request!==universityRequest||!visibility.university)return;
    universityData=data;map.getSource('universities').setData(data);universityKey=key;refreshSelection();
    if(data.truncated)report('notice','大学图层达到视窗加载上限，放大地图可查看更多校园。');
  }catch(error){if(error.name!=='AbortError')report('mapError',String(error));}
}
const metroSourceKeys=new Map();
let graphicsPaused=false;
const populatedRailSources=new Set();
const staticSourceState=new Map();
function updateStaticSources(){
  if(!config||graphicsPaused)return;
  const zoom=map.getZoom(),bounds=map.getBounds();
  for(const id of ['metro','stations','areas','construction','road','imported']){
    if(config.metroViewport&&['metro','stations','areas','construction'].includes(id))continue;
    if(id==='road'&&config.roadViewport)continue;
    const group=id==='areas'?'stations':id;
    const on=!!(visibility[group]||(id==='road'&&visibility.roadConstruction))&&!document.hidden&&zoom>=({metro:minZooms.metroLines,stations:minZooms.metroStations,areas:minZooms.metroAreas,construction:minZooms.metroConstruction,road:minZooms.roads}[id]||0);
    const spatial=id==='stations'||id==='areas';
      const key=on?(spatial?JSON.stringify([visibleIds,visibleStationIds,bounds.toArray()]):'on'):'off';
    if(staticSourceState.get(id)===key||!map.getSource(id))continue;
    let data=on?config.sources[id]:empty;
    if(on&&spatial&&data?.features){
      // Bound detailed station geometry to the current camera. A hidden
      // layer otherwise still leaves a complete national worker index alive.
      let vertices=0;
      const features=[];
      function visit(coords,box){
        if(typeof coords[0]==='number'){box[0]=Math.min(box[0],coords[0]);box[1]=Math.min(box[1],coords[1]);box[2]=Math.max(box[2],coords[0]);box[3]=Math.max(box[3],coords[1]);return 1;}
        return coords.reduce((count,part)=>count+visit(part,box),0);
      }
      for(const feature of data.features){
        const stationIds=id==='areas'?(feature.properties.station_ids||[]):[feature.properties.infrastructure_id||feature.properties.station_id];
        const relations=feature.properties.route_relation_ids||[];
        if(!stationIds.some(value=>visibleStationIds.includes(value)||visibleStationIds.some(item=>String(item).startsWith(String(value)+'@'))||relations.some(relation=>visibleStationIds.includes(String(value)+'@line-'+relation))))continue;
        const box=[Infinity,Infinity,-Infinity,-Infinity],count=visit(feature.geometry.coordinates,box);
        if(box[2]<bounds.getWest()||box[0]>bounds.getEast()||box[3]<bounds.getSouth()||box[1]>bounds.getNorth())continue;
        if(vertices+count>100000||features.length>=6000){document.getElementById('camera-status').textContent='已限制地图细节量，请放大查看';continue;}
        vertices+=count;features.push(feature);
      }
      data={type:'FeatureCollection',features};
    }
    map.getSource(id).setData(data||empty);staticSourceState.set(id,key);
  }
}
function metroSourceVisible(kind){
  if(graphicsPaused||document.hidden)return false;
  const zoom=map.getZoom();
  return !!visibility[kind==='areas'?'stations':kind]&&zoom>=({metro:minZooms.metroLines,stations:minZooms.metroStations,areas:minZooms.metroAreas,construction:minZooms.metroConstruction}[kind]||0);
}
function scheduleMetroViewport(){
  if(!config?.metroViewport)return;
  clearTimeout(metroTimer);metroController?.abort();++metroRequest;
  for(const kind of ['metro','stations','areas','construction']){
    if(metroSourceVisible(kind))continue;
    if(metroSourceKeys.has(kind))map.getSource(kind)?.setData(empty);
    metroSourceKeys.delete(kind);
  }
  metroTimer=setTimeout(updateMetroViewport,180);
}
async function updateMetroViewport(){
  if(!config.metroViewport||graphicsPaused||document.hidden)return;
  metroController?.abort();const controller=new AbortController();metroController=controller;
  const request=++metroRequest,b=map.getBounds(),span=Math.max(.02,(b.getEast()-b.getWest())/8);
  const southSpan=Math.max(.02,(b.getNorth()-b.getSouth())/8);
  const bbox=[Math.max(-180,Math.floor((b.getWest()-span)/span)*span),Math.max(-85,Math.floor((b.getSouth()-southSpan)/southSpan)*southSpan),Math.min(180,Math.ceil((b.getEast()+span)/span)*span),Math.min(85,Math.ceil((b.getNorth()+southSpan)/southSpan)*southSpan)].map(value=>Number(value.toFixed(5))).join(',');
  for(const kind of ['metro','stations','areas','construction']){
    if(!metroSourceVisible(kind))continue;
    const selected=kind==='metro'?visibleIds:kind==='construction'?constructionIds:visibleStationIds;
    const key=JSON.stringify([bbox,selected]);
    if(metroSourceKeys.get(kind)===key)continue;
    if(!selected.length){map.getSource(kind)?.setData(empty);metroSourceKeys.set(kind,key);continue;}
    const params=new URLSearchParams({kind,bbox,selected:JSON.stringify(selected)});
    try{
      const data=await queryViewport('/api/metro',params,controller.signal);
      if(request!==metroRequest||!metroSourceVisible(kind))return;
      map.getSource(kind)?.setData(data);metroSourceKeys.set(kind,key);
      if(data.truncated)document.getElementById('camera-status').textContent='已限制地图细节量，请放大查看';
    }catch(error){if(error.name!=='AbortError')report('mapError',String(error));return;}
  }
}
let railWays=null,railSections=null,railGroups=null,railExclude=false,railPointExclusions=null,railPointIncludes=null,railControlPointIncludes=null,railLineIds=null;
let railFacilityMode='all',railFacilityGroups=[];
let railIncludedEdges=[],railExcludedEdges=[];
let railAssetSelection={platforms:[],hiddenPlatforms:[],switches:[],hiddenSwitches:[]};
function railSourceVisible(kind){
  const zoom=map.getZoom();
  if(graphicsPaused||document.hidden)return false;
  if(kind==='rail')return (visibility.rail||visibility.railConstruction||visibility.railStationTracks)&&zoom>=(minZooms.railLines||0);
  if(kind==='railPoints')return (visibility.railStations&&zoom>=(minZooms.railStations??10))||(visibility.railControlPoints&&zoom>=(minZooms.railSwitches??15));
  if(kind==='railPlatforms')return visibility.railStations&&zoom>=(minZooms.railPlatforms??12);
  if(kind==='railStationAreas')return visibility.railStations&&zoom>=(minZooms.railAreas??11);
  return !!visibility[kind];
}
function scheduleRailViewport(){
  clearTimeout(railTimer);
  railController?.abort();
  ++railRequest;
  for(const kind of populatedRailSources){
    if(!railSourceVisible(kind)){
      entityPresentation.register(kind,empty);
      map.getSource(kind)?.setData(empty);
      populatedRailSources.delete(kind);
      railSourceKeys.delete(kind);
      railBudgetStats.delete(kind);
    }
  }
  updateRailBudgetStatus();
  railTimer=setTimeout(updateRailViewport,180);
}
function scheduleRoadViewport(){
  if(!config?.roadViewport)return;
  clearTimeout(roadTimer);roadController?.abort();++roadRequest;
  const paused=document.hidden||graphicsPaused;
  if((!visibility.road&&!visibility.roadConstruction)||paused||map.getZoom()<(minZooms.roads||0)){map.getSource('road')?.setData(empty);roadSourceKey=null;}
  if(!visibility.roadServices||paused){map.getSource('roadServices')?.setData(empty);roadServiceKey=null;}
  if(paused)return;
  roadTimer=setTimeout(updateRoadViewport,180);
}
async function updateRoadViewport(){
  if(!config.roadViewport||!map.getSource('road')||document.hidden||graphicsPaused)return;
  roadController?.abort();const controller=new AbortController();roadController=controller;
  const request=++roadRequest,b=map.getBounds(),dx=Math.max(.02,(b.getEast()-b.getWest())/8),dy=Math.max(.02,(b.getNorth()-b.getSouth())/8);
  const bbox=[Math.max(-180,Math.floor((b.getWest()-dx)/dx)*dx),Math.max(-85,Math.floor((b.getSouth()-dy)/dy)*dy),Math.min(180,Math.ceil((b.getEast()+dx)/dx)*dx),Math.min(85,Math.ceil((b.getNorth()+dy)/dy)*dy)].map(value=>Number(value.toFixed(5))).join(',');
  const zoom=Math.floor(map.getZoom());
  const key=JSON.stringify([bbox,zoom,roadRouteSelection,roadVisibleRoutes]);
  if((visibility.road||visibility.roadConstruction)&&zoom>=(minZooms.roads||0)&&key!==roadSourceKey){
  const params=new URLSearchParams({bbox,zoom:String(zoom)});
  if(roadRouteSelection)params.set('route',roadRouteSelection);
  else if(roadVisibleRoutes!==null)params.set('routes',JSON.stringify(roadVisibleRoutes));
  try{
    const data=await queryViewport('/api/roads',params,controller.signal);
    if(request!==roadRequest||(!visibility.road&&!visibility.roadConstruction))return;
    map.getSource('road')?.setData(data);roadSourceKey=key;
    if(data.truncated)document.getElementById('map-status').title='高速公路视窗数据已达上限，请放大查看';
  }catch(error){if(error.name!=='AbortError')report('mapError',String(error));}
  }
  const serviceKey=JSON.stringify([bbox,zoom>=11,roadVisibleServices]);
  if(visibility.roadServices&&serviceKey!==roadServiceKey){
    try{
      const data=await queryViewport('/api/roads',new URLSearchParams({kind:'services',bbox,zoom:String(zoom),selected:JSON.stringify(roadVisibleServices)}),controller.signal);
      if(request!==roadRequest||!visibility.roadServices)return;
      config.sources.roadServices=data;map.getSource('roadServices')?.setData(data);roadServiceKey=serviceKey;refreshSelection();
      if(data.upgrade_required)report('notice','请更新全国公路目录以加入服务区。');
    }catch(error){if(error.name!=='AbortError')report('mapError',String(error));}
  }
}
function applyRailWays(){
  let selected=null;
  if(railSections!==null||railWays!==null||railGroups!==null){
    const filters=[];
    if((railSections||[]).length)filters.push(['in',['get','section_id'],['literal',railSections]]);
    if((railWays||[]).length)filters.push(['in',['get','osm_way_id'],['literal',railWays]]);
    if((railGroups||[]).length)filters.push(['in',['get','catalog_group_id'],['literal',railGroups]]);
    selected=filters.length===0?['==',['literal',1],0]:filters.length===1?filters[0]:['any',...filters];
    if(railExclude)selected=['!',selected];
  }
  if(railIncludedEdges.length){
    const direct=['in',['get','network_edge_id'],['literal',railIncludedEdges]];
    if(selected)selected=['any',selected,direct];
  }
  if(railExcludedEdges.length){
    const allowed=['!', ['in',['get','network_edge_id'],['literal',railExcludedEdges]]];
    selected=selected?['all',selected,allowed]:allowed;
  }
  const inactive=['in',['coalesce',['get','construction_status'],['case',['==',['get','construction'],true],'construction','operating']],['literal',['construction','planned','disused']]];
  for(const id of ['rail','rail-stripes','rail-construction'])if(map.getLayer(id)){
    const construction=id==='rail-construction'?inactive:['!',inactive];
    map.setFilter(id,selected?['all',construction,selected]:construction);
  }
  for(const id of ['history-rail','history-rail-labels'])if(map.getLayer(id))map.setFilter(id,['all',historyStateFilter('rail'),selected||['literal',true]]);
  if(map.getLayer('history-rail-stripes'))map.setFilter('history-rail-stripes',['all',historyStateFilter('rail'),['==',['get','history_state'],'operating'],selected||['literal',true]]);
  if(map.getLayer('rail-line-labels')){
    const state=['any',['all',['!',inactive],['literal',!!(visibility.rail||visibility.railStationTracks)]],['all',inactive,['literal',!!visibility.railConstruction]]];
    map.setFilter('rail-line-labels',selected?['all',state,selected]:state);
  }
}
async function updateRailViewport(){
  if(!config.railViewport||!map.getSource('rail')||graphicsPaused||document.hidden)return;
  railController?.abort();
  const controller=new AbortController();railController=controller;
  const request=++railRequest,b=map.getBounds(),dx=Math.max(.02,(b.getEast()-b.getWest())/8),dy=Math.max(.02,(b.getNorth()-b.getSouth())/8);
  const bbox=[Math.max(-180,Math.floor((b.getWest()-dx)/dx)*dx),Math.max(-85,Math.floor((b.getSouth()-dy)/dy)*dy),Math.min(180,Math.ceil((b.getEast()+dx)/dx)*dx),Math.min(85,Math.ceil((b.getNorth()+dy)/dy)*dy)].map(value=>Number(value.toFixed(5))).join(',');
  try{
    for(const kind of ['rail','railPoints','railPlatforms','railStationAreas']){
      if(!railSourceVisible(kind))continue;
      const params=new URLSearchParams({kind,bbox,zoom:String(map.getZoom())});
      if(kind==='railPoints'){
        params.set('point_stations',String(!!visibility.railStations));
        params.set('point_controls',String(!!visibility.railControlPoints));
      }
      if(kind==='rail'){
        params.set('exclude',String(railExclude));
        params.set('facility',railFacilityMode);
        if(railFacilityGroups.length)params.set('facility_groups',JSON.stringify(railFacilityGroups));
        params.set('states',JSON.stringify(hasRailHistory()?['operating','unknown','construction','planned','disused']:[...(visibility.rail||visibility.railStationTracks?['operating','unknown']:[]),...(visibility.railConstruction?['construction','planned','disused']:[])]));
        if((railSections||[]).length)params.set('sections',JSON.stringify(railSections));
        if((railWays||[]).length)params.set('ways',JSON.stringify(railWays));
        if((railGroups||[]).length)params.set('groups',JSON.stringify(railGroups));
        if(railIncludedEdges.length)params.set('included_edges',JSON.stringify(railIncludedEdges));
        if(railExcludedEdges.length)params.set('excluded_edges',JSON.stringify(railExcludedEdges));
        params.set('only_explicit',String(railSections!==null||railWays!==null||railGroups!==null));
      }
      const key=params.toString();
      if(railSourceKeys.get(kind)===key)continue;
      const data=await queryViewport('/api/rail',params,controller.signal);
      if(request!==railRequest||!railSourceVisible(kind))return;
      if(data.busy){railTimer=setTimeout(updateRailViewport,350);return;}
      entityPresentation.register(kind,data);
      map.getSource(kind)?.setData(data);populatedRailSources.add(kind);railSourceKeys.set(kind,key);
      railBudgetStats.set(kind,{budget:data.budget,truncated:data.truncated});updateRailBudgetStatus();
    }
  }catch(error){if(error.name!=='AbortError')report('mapError',String(error));}
}
function addLayer(layer) { if (!map.getLayer(layer.id)) map.addLayer(layer); }
function applyRailStyles(){
  const styles=config.railStyles||{};
  const entries=Object.entries(styles).filter(([type,style])=>!type.startsWith('_')&&style&&typeof style==='object'&&!Array.isArray(style));
  const fallback=styles._default||{color:'#667887',width:2,pattern:'alternating'};
  const curve=Array.isArray(styles._zoom_width_curve)&&styles._zoom_width_curve.length>=2
    ?styles._zoom_width_curve
    :[{zoom:3,scale:.8},{zoom:5,scale:.9},{zoom:8,scale:1.05},{zoom:12,scale:1.25},{zoom:16,scale:1.5},{zoom:19,scale:1.8}];
  const role=['coalesce',['get','rail_style_key'],'_default'];
  const colors=['match',role],widths=['match',role];
  for(const [type,style] of entries){
    colors.push(type,style.color);widths.push(type,Number(style.width));
  }
  colors.push(fallback.color);widths.push(Number(fallback.width));
  const baseWidth=['coalesce',['get','rail_display_width'],entries.length?widths:2];
  const widthCurve=['interpolate',['linear'],['zoom']];
  for(const point of curve)widthCurve.push(Number(point.zoom),['*',baseWidth,Number(point.scale)]);
  for(const id of ['rail','rail-stripes','rail-construction'])if(map.getLayer(id)){
    map.setPaintProperty(id,'line-color',id==='rail-stripes'?'#ffffff':['coalesce',['get','rail_display_color'],entries.length?colors:'#667887']);
    map.setPaintProperty(id,'line-width',widthCurve);
  }
  const patterns={solid:[1,0],alternating:[1,0],dashed:[3,2],long_dash:[8,3],dotted:[.3,2],dash_dot:[6,2,.3,2]};
  if(map.getLayer('rail')){
    const dash=['match',role];
    for(const [type,style] of entries)dash.push(type,['literal',patterns[style.pattern]||patterns.solid]);
    dash.push(['literal',patterns[fallback.pattern]||patterns.solid]);
    map.setPaintProperty('rail','line-dasharray',entries.length?dash:[1,0]);
  }
  if(map.getLayer('rail-construction'))map.setPaintProperty('rail-construction','line-opacity',.65);
  if(map.getLayer('rail-stripes')){
    const solid=entries.filter(([,style])=>style.pattern!=='alternating').map(([type])=>type);
    const known=entries.map(([type])=>type);
    map.setPaintProperty('rail-stripes','line-opacity',['case',['in',role,['literal',solid]],0,
      ['in',role,['literal',known]],1,fallback.pattern==='alternating'?1:0]);
  }
}
function applyMetroStyles(){
  const styles=config.metroStyles||{};
  const curve=Array.isArray(styles.zoom_curve)&&styles.zoom_curve.length>=2
    ?styles.zoom_curve
    :[{zoom:3,scale:.3},{zoom:8,scale:.6},{zoom:12,scale:1},{zoom:16,scale:1.25},{zoom:19,scale:1.45}];
  const widthCurve=base=>{const value=['interpolate',['linear'],['zoom']];for(const point of curve)value.push(Number(point.zoom),base*Number(point.scale));return value;};
  if(map.getLayer('metro'))map.setPaintProperty('metro','line-width',widthCurve(Number(styles.operating_width)||5));
  if(map.getLayer('construction'))map.setPaintProperty('construction','line-width',widthCurve(Number(styles.construction_width)||5));
}
function applyRoadStyles(){
  if(!map.getLayer('road'))return;
  const s=config.roadStyles||{},width=Number(s.width)||2.5;
  map.setPaintProperty('road','line-color',['match',['get','road_class'],'national',s.national_color||'#176c9a','provincial',s.provincial_color||'#bb7538',s.other_color||'#8898a4']);
  map.setPaintProperty('road','line-width',['interpolate',['linear'],['zoom'],4,width*.48,9,width,14,width*1.6]);
  map.setPaintProperty('road','line-dasharray',s.pattern==='dashed'?[Number(s.dash_length)||3,Number(s.dash_gap)||2]:null);
  map.setLayoutProperty('road','line-cap',s.pattern==='dashed'?'butt':'round');
  map.setLayoutProperty('road','line-join','round');
}
function applyRailPointStyles(){
  const styles=config.railPointStyles||{station:{size:4,shape:'ring'},control:{size:3,shape:'solid'},labels:{show_station_names:true,show_line_names:true,station_font_size:12,line_font_size:11}};
  for(const [id,kind,color] of [['rail-points','station','#466979'],['rail-detail-points','control','#a36d3c']]){
    if(!map.getLayer(id))continue;
    map.setLayoutProperty(id,'visibility',visibility[kind==='station'?'railStations':'railControlPoints']?'visible':'none');
    const style=styles[kind]||{};
    map.setPaintProperty(id,'circle-radius',Number(style.size)||3);
    map.setPaintProperty(id,'circle-color',style.shape==='solid'?color:'#ffffff');
    map.setPaintProperty(id,'circle-stroke-color',color);
    map.setPaintProperty(id,'circle-stroke-width',style.shape==='solid'?1:2);
  }
  const labels=styles.labels||{};
  if(map.getLayer('rail-station-labels')){
    map.setLayoutProperty('rail-station-labels','visibility',labels.show_station_names===false?'none':((visibility.railStations||visibility.railControlPoints)?'visible':'none'));
    map.setLayoutProperty('rail-station-labels','text-size',config.fonts?.map?.size||Number(labels.station_font_size)||12);
  }
  if(map.getLayer('rail-line-labels')){
    map.setLayoutProperty('rail-line-labels','visibility',labels.show_line_names===false?'none':((visibility.rail||visibility.railConstruction||visibility.railStationTracks)?'visible':'none'));
    map.setLayoutProperty('rail-line-labels','text-size',config.fonts?.map?.size||Number(labels.line_font_size)||11);
  }
}
function applyLineLabelVisibility(){
  const enabled=config.railPointStyles?.labels?.show_line_names!==false;
  for(const [id,on] of [['rail-line-labels',visibility.rail||visibility.railConstruction||visibility.railStationTracks],['line-labels',visibility.metro],['road-labels',visibility.road],['road-construction-labels',visibility.roadConstruction]])
    if(map.getLayer(id))map.setLayoutProperty(id,'visibility',enabled&&on?'visible':'none');
}
function applyMinZooms(){
  const ranges={
    metroLines:['metro','line-labels','history-metro','history-metro-labels'],metroStations:['stations','station-labels'],
    metroAreas:['areas-fill','areas-outline'],metroConstruction:['construction'],
    railLines:['rail','rail-stripes','rail-construction','rail-line-labels','history-rail','history-rail-labels'],
    railStations:['rail-points','rail-signal-box-symbol'],
    railSwitches:['rail-detail-points'],
    railPlatforms:['rail-platform-fill','rail-platform-outline'],
    railAreas:['rail-station-fill','rail-station-outline','rail-signal-box-fill','rail-signal-box-outline'],
    roads:['road','road-construction','road-labels','road-construction-labels','history-road','history-road-labels'],
    universityPois:['university-pois','university-labels'],universityOutlines:['university-fill','university-outline']
  };
  for(const [kind,layers] of Object.entries(ranges)){
    const zoom=minZooms[kind];
    if(!Number.isInteger(zoom))continue;
    for(const id of layers)if(map.getLayer(id))map.setLayerZoomRange(id,zoom,24);
  }
  for(const id of ['history-metro','history-metro-labels'])if(map.getLayer(id))map.setLayerZoomRange(id,Math.min(minZooms.metroLines??0,minZooms.metroConstruction??0),24);
  updateHistoryLines();
  if(map.getLayer('rail-station-labels'))map.setLayerZoomRange('rail-station-labels',Math.min(minZooms.railStations??10,minZooms.railSwitches??15),24);
  applyRailWays();sharedRailStationFilter();
}
function installLayers() {
  const sources = {...config.sources, universities:empty, roadServices:empty, vehicles:empty, selection:empty};
  universityKey=null;
  roadServiceKey=null;
  staticSourceState.clear();populatedRailSources.clear();railSourceKeys.clear();roadSourceKey=null;metroSourceKeys.clear();
  for (const [id, data] of Object.entries(sources)) addSource(id, ['metro','stations','areas','construction','road','imported'].includes(id)?empty:data);
  addLayer({id:'rail',type:'line',source:'rail',layout:{'line-cap':'round','line-join':'round'},paint:{'line-color':'#667887','line-width':['interpolate',['linear'],['zoom'],4,.4,8,.9,12,2,16,3],'line-opacity':.8}});
  map.setFilter('rail',['!=',['get','construction'],true]);
  addLayer({id:'rail-stripes',type:'line',source:'rail',minzoom:13,layout:{'line-cap':'round','line-join':'round'},filter:['!=',['get','construction'],true],paint:{'line-color':'#ffffff','line-width':2,'line-dasharray':[2,2]}});
  addLayer({id:'rail-construction',type:'line',source:'rail',layout:{'line-cap':'round','line-join':'round'},filter:['==',['get','construction'],true],paint:{'line-color':'#475569','line-width':['interpolate',['linear'],['zoom'],4,.5,8,1,12,2.5,16,3.5],'line-dasharray':[2,1.5]}});
  addLayer({id:'rail-points',type:'circle',source:'railPoints',minzoom:10,paint:{'circle-radius':['case',['==',['get','kind'],'switch'],2,4],'circle-color':'#ffffff','circle-stroke-color':'#466979','circle-stroke-width':1.5}});
  addLayer({id:'rail-detail-points',type:'circle',source:'railPoints',minzoom:15,filter:['!', ['in',['get','kind'],['literal',['station','halt','yard','depot','workshop','works','engine_shed']]]],paint:{'circle-radius':2,'circle-color':'#ffffff','circle-stroke-color':'#466979','circle-stroke-width':1}});
  addLayer({id:'rail-station-labels',type:'symbol',source:'railPoints',minzoom:7,filter:['any',['in',['get','kind'],['literal',['station','halt','yard','depot','workshop','works','engine_shed','signal_box','junction','crossing']]],['has','display_name']],layout:{'text-field':['coalesce',['get','display_name'],['get','name']],'text-font':vectorAvailable?['Noto Sans Regular']:['Open Sans Regular'],'text-size':12,'text-offset':[0,1.2],'text-anchor':'top','text-optional':true},paint:{'text-color':'#263f4d','text-halo-color':'#ffffff','text-halo-width':1.8}});
  addLayer({id:'rail-line-labels',type:'symbol',source:'rail',minzoom:5,layout:{'symbol-placement':'line','symbol-spacing':320,'text-field':['coalesce',['get','line_display_name'],['get','line_name'],['get','name']],'text-font':vectorAvailable?['Noto Sans Bold']:['Open Sans Bold'],'text-size':11,'text-optional':true},paint:{'text-color':'#425d6b','text-halo-color':'#ffffff','text-halo-width':2}});
  addLayer({id:'rail-platform-fill',type:'fill',source:'railPlatforms',filter:['==',['geometry-type'],'Polygon'],minzoom:12,paint:{'fill-color':'#466979','fill-opacity':.22}});
  addLayer({id:'rail-platform-outline',type:'line',source:'railPlatforms',minzoom:12,paint:{'line-color':'#466979','line-width':1.5}});
  addLayer({id:'rail-station-fill',type:'fill',source:'railStationAreas',minzoom:11,paint:{'fill-color':'#688191','fill-opacity':.2}});
  addLayer({id:'rail-station-outline',type:'line',source:'railStationAreas',minzoom:11,paint:{'line-color':'#486979','line-width':1.5}});
  addLayer({id:'rail-signal-box-fill',type:'fill',source:'railSignalBoxes',filter:['==',['geometry-type'],'Polygon'],paint:{'fill-color':'#b97435','fill-opacity':.16}});
  addLayer({id:'rail-signal-box-outline',type:'line',source:'railSignalBoxes',filter:['==',['geometry-type'],'Polygon'],paint:{'line-color':'#a45f27','line-width':2}});
  addLayer({id:'rail-signal-box-symbol',type:'symbol',source:'railSignalBoxes',filter:['==',['geometry-type'],'Point'],layout:{'text-field':['concat','▭ ',['coalesce',['get','display_name'],['get','name']]],'text-font':vectorAvailable?['Noto Sans Bold']:['Open Sans Bold'],'text-size':12,'text-allow-overlap':true},paint:{'text-color':'#8b4b1f','text-halo-color':'#ffffff','text-halo-width':2}});
  addLayer({id:'rail-vehicles',type:'circle',source:'railVehicles',paint:{'circle-radius':['/', ['coalesce',['get','display_size'],14],2],'circle-color':['coalesce',['get','display_color'],'#486e9a'],'circle-opacity':['case',['in',['get','display_style'],['literal',['ring','train']]],0,1],'circle-stroke-color':['case',['==',['get','display_style'],'ring'],['coalesce',['get','display_color'],'#486e9a'],'#ffffff'],'circle-stroke-width':2}});
  addLayer({id:'rail-vehicle-symbols',type:'symbol',source:'railVehicles',filter:['==',['get','display_style'],'train'],layout:{'text-field':'车','text-font':vectorAvailable?['Noto Sans Regular']:['Open Sans Regular'],'text-size':['coalesce',['get','display_size'],14],'text-allow-overlap':true},paint:{'text-color':['coalesce',['get','display_color'],'#486e9a'],'text-halo-color':'#ffffff','text-halo-width':2}});
  addLayer({id:'rail-vehicle-labels',type:'symbol',source:'railVehicles',layout:{'text-field':['get','trip_id'],'text-font':vectorAvailable?['Noto Sans Regular']:['Open Sans Regular'],'text-size':12,'text-offset':[0,-1.4],'text-allow-overlap':true},paint:{'text-color':'#20313d','text-halo-color':'#ffffff','text-halo-width':2}});
  if(!map.getSource('railPlan'))map.addSource('railPlan',{type:'geojson',data:config.sources.railPlan||empty});
  addLayer({id:'rail-plan-path',type:'line',source:'railPlan',filter:['==',['geometry-type'],'LineString'],paint:{'line-color':['coalesce',['get','color'],'#466979'],'line-width':3}});
  addLayer({id:'rail-plan-stations',type:'circle',source:'railPlan',filter:['==',['geometry-type'],'Point'],paint:{'circle-radius':4,'circle-color':'#ffffff','circle-stroke-color':'#466979','circle-stroke-width':2}});
  addLayer({id:'rail-plan-labels',type:'symbol',source:'railPlan',filter:['==',['geometry-type'],'Point'],layout:{'text-field':['coalesce',['get','display_name'],['get','name']],'text-font':vectorAvailable?['Noto Sans Regular']:['Open Sans Regular'],'text-size':12,'text-offset':[0,1.2]},paint:{'text-color':'#20313d','text-halo-color':'#ffffff','text-halo-width':2}});
  addLayer({id:'road',type:'line',source:'road',paint:{'line-color':['match',['get','road_class'],'national','#176c9a','provincial','#bb7538','#8898a4'],'line-width':['interpolate',['linear'],['zoom'],4,1.2,9,2.5,14,4],'line-opacity':.9}});
  map.setFilter('road',['!=',['get','construction'],true]);
  addLayer({id:'road-construction',type:'line',source:'road',filter:['==',['get','construction'],true],layout:{'line-cap':'round','line-join':'round'},paint:{'line-color':'#bd7338','line-width':['interpolate',['linear'],['zoom'],4,1.2,9,2.5,14,4],'line-dasharray':[3,2]}});
  for(const [id,isConstruction] of [['road-labels',false],['road-construction-labels',true]])addLayer({id,type:'symbol',source:'road',minzoom:6,filter:[isConstruction?'==':'!=',['get','construction'],true],layout:{'symbol-placement':'line','symbol-spacing':350,'text-field':['concat',['coalesce',['get','ref'],''],' ',['coalesce',['get','display_name'],['get','name'],''],isConstruction?'（在建）':''],'text-font':vectorAvailable?['Noto Sans Regular']:['Open Sans Regular'],'text-size':11,'text-padding':8},paint:{'text-color':isConstruction?'#995526':'#23556c','text-halo-color':'#ffffff','text-halo-width':2}});
  addLayer({id:'road-service-outline-fill',type:'fill',source:'roadServices',filter:['==',['get','asset_kind'],'outline'],minzoom:11,paint:{'fill-color':'#4b987d','fill-opacity':.18}});
  addLayer({id:'road-service-outline',type:'line',source:'roadServices',filter:['==',['get','asset_kind'],'outline'],minzoom:11,paint:{'line-color':'#3b8069','line-width':1.5}});
  addLayer({id:'road-service-buildings',type:'fill',source:'roadServices',filter:['==',['get','asset_kind'],'building'],minzoom:11,paint:{'fill-color':'#608e7a','fill-outline-color':'#386b54','fill-opacity':.6}});
  addLayer({id:'road-service-poi',type:'circle',source:'roadServices',filter:['==',['get','asset_kind'],'poi'],paint:{'circle-radius':4,'circle-color':'#ffffff','circle-stroke-color':'#3b8069','circle-stroke-width':2}});
  addLayer({id:'road-service-labels',type:'symbol',source:'roadServices',filter:['==',['get','asset_kind'],'poi'],minzoom:8,layout:{'text-field':['coalesce',['get','display_name'],['get','name']],'text-font':vectorAvailable?['Noto Sans Regular']:['Open Sans Regular'],'text-size':11,'text-offset':[0,1.1],'text-anchor':'top'},paint:{'text-color':'#286149','text-halo-color':'#ffffff','text-halo-width':2}});
  addLayer({id:'metro',type:'line',source:'metro',paint:{'line-color':['coalesce',['get','display_color'],'#718096'],'line-width':['interpolate',['linear'],['zoom'],4,1.5,8,3,12,5],'line-opacity':.95}});
  addLayer({id:'construction',type:'line',source:'construction',paint:{'line-color':'#475569','line-width':['interpolate',['linear'],['zoom'],4,1.5,10,3.5,14,5],'line-dasharray':[1.6,1.2]}});
  addLayer({id:'areas-fill',type:'fill',source:'areas',minzoom:12,paint:{'fill-color':'#078c92','fill-opacity':.16}});
  addLayer({id:'areas-outline',type:'line',source:'areas',minzoom:12,paint:{'line-color':'#087f85','line-width':1.5}});
  addLayer({id:'stations',type:'circle',source:'stations',minzoom:9,paint:{'circle-radius':['interpolate',['linear'],['zoom'],9,2.8,13,4.5],'circle-color':'#ffffff','circle-stroke-color':'#256c77','circle-stroke-width':1.5}});
  addLayer({id:'station-labels',type:'symbol',source:'stations',minzoom:12,layout:{'text-field':['coalesce',['get','display_name'],['get','name']],'text-font':vectorAvailable?['Noto Sans Regular']:['Open Sans Regular'],'text-size':11,'text-offset':[0,1.1],'text-anchor':'top'},paint:{'text-color':'#20313d','text-halo-color':'#ffffff','text-halo-width':1.5}});
  addLayer({id:'line-labels',type:'symbol',source:'metro',minzoom:10,layout:{'symbol-placement':'line','symbol-spacing':260,'text-field':['coalesce',['get','line_display_name'],['get','display_name'],['get','ref'],['get','line_name']],'text-font':vectorAvailable?['Noto Sans Bold']:['Open Sans Bold'],'text-size':11},paint:{'text-color':'#273d49','text-halo-color':'#ffffff','text-halo-width':2}});
  addLayer({id:'imported-fill',type:'fill',source:'imported',filter:['==',['geometry-type'],'Polygon'],paint:{'fill-color':'#697dcc','fill-opacity':.15}});
  addLayer({id:'university-fill',type:'fill',source:'universities',minzoom:12,filter:['==',['geometry-type'],'Polygon'],paint:{'fill-color':'#3d8b7c','fill-opacity':.14}});
  addLayer({id:'university-outline',type:'line',source:'universities',minzoom:12,filter:['==',['geometry-type'],'Polygon'],paint:{'line-color':'#347b71','line-width':1.5,'line-opacity':.8}});
  addLayer({id:'university-pois',type:'circle',source:'universities',minzoom:9,filter:['==',['geometry-type'],'Point'],paint:{'circle-radius':['interpolate',['linear'],['zoom'],9,3,13,4.5,17,6],'circle-color':'#f2faf8','circle-stroke-color':'#347b71','circle-stroke-width':1.8}});
  addLayer({id:'university-labels',type:'symbol',source:'universities',minzoom:9,filter:['==',['geometry-type'],'Point'],layout:{'text-field':['get','name'],'text-font':vectorAvailable?['Noto Sans Regular']:['Open Sans Regular'],'text-size':12,'text-offset':[0,1.2],'text-anchor':'top'},paint:{'text-color':'#245d55','text-halo-color':'#ffffff','text-halo-width':1.8}});
  addLayer({id:'imported-line',type:'line',source:'imported',filter:['!=',['geometry-type'],'Point'],paint:{'line-color':'#697dcc','line-width':3}});
  addLayer({id:'imported-point',type:'circle',source:'imported',filter:['==',['geometry-type'],'Point'],paint:{'circle-color':'#697dcc','circle-radius':5,'circle-stroke-color':'white','circle-stroke-width':2}});
  addLayer({id:'selection-line',type:'line',source:'selection',filter:['!=',['geometry-type'],'Point'],paint:{'line-color':'#0c9c90','line-width':9,'line-opacity':.35}});
  addLayer({id:'selection-point',type:'circle',source:'selection',filter:['==',['geometry-type'],'Point'],paint:{'circle-radius':12,'circle-color':'#0c9c90','circle-opacity':.2,'circle-stroke-color':'#0c9c90','circle-stroke-width':2}});
  addLayer({id:'vehicles-halo',type:'circle',source:'vehicles',paint:{'circle-radius':17,'circle-color':'#059b8d','circle-opacity':.18}});
  addLayer({id:'vehicles',type:'circle',source:'vehicles',paint:{'circle-radius':7,'circle-color':'#0c9184','circle-stroke-color':'#ffffff','circle-stroke-width':3}});
  if(!map.hasImage('train-icon')){
    const canvas=document.createElement('canvas');canvas.width=48;canvas.height=48;
    const ctx=canvas.getContext('2d');
    ctx.fillStyle='#ffffff';ctx.beginPath();ctx.roundRect(6,2,36,44,10);ctx.fill();
    ctx.fillStyle='#087e76';ctx.beginPath();ctx.roundRect(10,5,28,38,7);ctx.fill();
    ctx.fillStyle='#e9ffff';ctx.fillRect(15,12,18,12);ctx.fillRect(16,29,4,4);ctx.fillRect(28,29,4,4);
    map.addImage('train-icon',ctx.getImageData(0,0,48,48),{pixelRatio:2});
  }
  addLayer({id:'vehicles-symbol',type:'symbol',source:'vehicles',layout:{'icon-image':'train-icon','icon-size':14/24,'icon-allow-overlap':true,'icon-ignore-placement':true}});
  addLayer({id:'vehicle-label',type:'symbol',source:'vehicles',minzoom:11,layout:{'text-field':['get','vehicle_id'],'text-font':vectorAvailable?['Noto Sans Bold']:['Open Sans Bold'],'text-size':10,'text-offset':[0,-2]},paint:{'text-color':'#0b6058','text-halo-color':'#ffffff','text-halo-width':2}});
  applyVisibility(); applyLineFilter(); applyConstructionFilter();applyBaseDetails();
  map.getSource('vehicles').setData(visibility.vehicles?operatingVehicles:empty);
  for(const [key,on] of Object.entries(overlays))setOverlay(key,on);
  refreshSelection();
  applyRailWays();
  applyRailStyles();
  applyMetroStyles();
  applyRoadStyles();
  applyRailPointStyles();
  applyMinZooms();
  applyMapFontSizes();
  scheduleUniversityViewport();
  scheduleRailViewport();
  scheduleRoadViewport();
  scheduleMetroViewport();
  adminSourceKey=null;adminFeatureCount=0;scheduleAdminViewport();
  updateStaticSources();
}
const groups = {metro:['metro','line-labels'],stations:['stations','station-labels','areas-fill','areas-outline'],construction:['construction'],rail:['rail','rail-stripes','rail-line-labels'],railConstruction:['rail-construction'],railStations:['rail-points','rail-detail-points','rail-station-labels','rail-platform-fill','rail-platform-outline','rail-station-fill','rail-station-outline','rail-signal-box-fill','rail-signal-box-outline','rail-signal-box-symbol'],railControlPoints:[],railVehicles:['rail-vehicles','rail-vehicle-symbols','rail-vehicle-labels'],railPlan:['rail-plan-path','rail-plan-stations','rail-plan-labels'],road:['road','road-labels'],roadConstruction:['road-construction','road-construction-labels'],roadServices:['road-service-outline-fill','road-service-outline','road-service-buildings','road-service-poi','road-service-labels'],imported:['imported-fill','imported-line','imported-point'],vehicles:['vehicles-halo','vehicles','vehicles-symbol','vehicle-label']};
groups.university=['university-fill','university-outline','university-pois','university-labels'];
function railOwnersVisibleFilter(){
  const present=field=>{
    const owners=['coalesce',['get',field],['literal',[]]];
    if(railLineIds!==null)return railLineIds.length?['any',...railLineIds.map(id=>['in',id,owners])]:['literal',false];
    const hidden=railHiddenLineIds.map(id=>['case',['in',id,owners],1,0]);
    return ['>', ['length',owners],hidden.length?['+',0,...hidden]:0];
  };
  return ['any',['==',['length',['coalesce',['get','line_ids'],['literal',[]]]],0],
    ['all',['literal',!!visibility.rail],present('operating_line_ids')],
    ['all',['literal',!!visibility.railConstruction],present('construction_line_ids')]];
}
function sharedRailStationFilter(){
  if(!map.getLayer('rail-points'))return;
  const nodes=visibility.railPlan?(config.sources.railPlan?.features||[]).filter(f=>f.geometry.type==='Point').map(f=>f.properties.osm_node_id):[];
  const allowed=['!', ['in',['coalesce',['get','osm_node_id'],['get','station_source_id']],['literal',railPointExclusions||[]]]];
  const ownerAllowed=railOwnersVisibleFilter();
  const directlySelected=railPointIncludes===null?['==',['literal',1],1]:railPointIncludes.length?['in',['coalesce',['get','osm_node_id'],['get','station_source_id']],['literal',railPointIncludes]]:['==',['literal',1],0];
  const directlySelectedArea=railPointIncludes===null?['==',['literal',1],1]:railPointIncludes.length?['any',...railPointIncludes.map(id=>['in',id,['coalesce',['get','associated_station_ids'],['literal',[]]]])]:['==',['literal',1],0];
  const controlIds=[...new Set([...(railControlPointIncludes||[]),...railAssetSelection.switches])];
  const directlySelectedControl=['all',
    ['!', ['in',['get','osm_node_id'],['literal',railAssetSelection.hiddenSwitches]]],
    railControlPointIncludes===null?['==',['literal',1],1]:['in',['get','osm_node_id'],['literal',controlIds]]];
  const stationAllowed=['all',directlySelected,railPointIncludes===null?ownerAllowed:['literal',true]];
  const controlAllowed=['all',directlySelectedControl,railControlPointIncludes===null?ownerAllowed:['literal',true]];
  map.setFilter('rail-points',['all',['in',['get','kind'],['literal',['station','halt','yard','depot','workshop','works','engine_shed']]],['!', ['in',['get','osm_node_id'],['literal',nodes]]],allowed,stationAllowed]);
  if(map.getLayer('rail-detail-points'))map.setFilter('rail-detail-points',['all',['!', ['in',['get','kind'],['literal',['station','halt','yard','depot','workshop','works','engine_shed']]]],allowed,controlAllowed]);
  if(map.getLayer('rail-station-labels'))map.setFilter('rail-station-labels',['all',['any',['in',['get','kind'],['literal',['station','halt','yard','depot','workshop','works','engine_shed','signal_box','junction','crossing']]],['has','display_name']],allowed,['case',['in',['get','kind'],['literal',['station','halt','yard','depot','workshop','works','engine_shed']]],['all',['literal',!!visibility.railStations],['>=',['zoom'],minZooms.railStations??10],['!', ['in',['get','osm_node_id'],['literal',nodes]]],stationAllowed],['all',['literal',!!visibility.railControlPoints],['>=',['zoom'],minZooms.railSwitches??15],controlAllowed]]]);
  const areasAllowed=(railPointExclusions||[]).length?['!', ['any',...(railPointExclusions||[]).map(id=>['in',id,['coalesce',['get','associated_station_ids'],['literal',[]]]])]]:['==',['literal',1],1];
  for(const id of ['rail-platform-fill','rail-platform-outline','rail-station-fill','rail-station-outline'])if(map.getLayer(id)){
    const geometry=id.endsWith('-fill')?['==',['geometry-type'],'Polygon']:['==',['literal',1],1];
    const platform=id.startsWith('rail-platform');
    const assetAllowed=platform?['all',
      ['!', ['in',['get','osm_way_id'],['literal',railAssetSelection.hiddenPlatforms]]],
      ['any',directlySelectedArea,['in',['get','osm_way_id'],['literal',railAssetSelection.platforms]]]]:directlySelectedArea;
    map.setFilter(id,['all',geometry,areasAllowed,assetAllowed,railPointIncludes===null?ownerAllowed:['literal',true]]);
  }
}
function refreshSelection(){
  if(!map.getSource('selection'))return;
  const vehicleLayers=[...groups.vehicles,...groups.railVehicles];
  for(const [overlay,layers] of [[metroVehicleOverlay,groups.vehicles],[railVehicleOverlay,groups.railVehicles]]){
    overlay?.setSelected(selectedFeatures.filter(f=>layers.includes(f.__layer)).map(f=>f.properties.vehicle_id||f.properties.trip_id));
  }
  const entityVisible=feature=>{
    const p=feature.properties||{}, layer=feature.__layer||feature.layer||selectedLayer;
    if(p.history_state&&p.history_state!=='unknown'&&['rail','metro','road'].includes(layer))return !!(visibility[layer]||visibility[layer==='metro'?'construction':layer+'Construction']||(layer==='rail'&&visibility.railStationTracks));
    if(['road','road-construction','road-labels','road-construction-labels'].includes(layer)){
      const keys=roadRouteSelection?[roadRouteSelection]:roadVisibleRoutes;
      if(keys!==null&&!keys.some(key=>(p.route_keys||[]).includes(key)))return false;
    }
    if(['rail-points','rail-detail-points','rail-station-labels'].includes(layer)){
      const station=['station','halt','yard','depot','workshop','works','engine_shed'].includes(p.kind);
      if(!visibility[station?'railStations':'railControlPoints']||map.getZoom()<(station?(minZooms.railStations??10):(minZooms.railSwitches??15)))return false;
    }
    const group=Object.entries(groups).find(([,layers])=>layers.includes(layer))?.[0];
    if(group&&!(['rail-detail-points','rail-station-labels'].includes(layer)&&visibility.railControlPoints)&&!visibility[group]&&!(group==='rail'&&visibility.railStationTracks)&&!(layer==='rail-line-labels'&&(visibility.railConstruction||visibility.railStationTracks)))return false;
    if(p.service_id)return roadVisibleServices.includes(p.service_id);
    if(p.campus_id)return visibility.university&&universitySelection.includes(p.campus_id);
    if(group==='railStations'&&Array.isArray(p.line_ids)&&p.line_ids.length){
      const explicit=(railPointIncludes||[]).includes(p.osm_node_id??p.station_source_id)||(railControlPointIncludes||[]).includes(p.osm_node_id);
      const owners=[...(visibility.rail?(p.operating_line_ids||[]):[]),...(visibility.railConstruction?(p.construction_line_ids||[]):[])];
      if(!explicit&&!owners.some(id=>railLineIds===null?!railHiddenLineIds.includes(id):railLineIds.includes(id)))return false;
    }
    if(groups.rail.includes(layer)||layer==='rail-construction'){
      const inactive=['construction','planned','disused'].includes(p.construction_status||(p.construction?'construction':'operating'));
      if(!visibility[inactive?'railConstruction':'rail']&&!(visibility.railStationTracks&&!inactive))return false;
      if(railSections!==null||railWays!==null||railGroups!==null){
        const selected=(railSections||[]).includes(p.section_id)||(railWays||[]).includes(p.osm_way_id)||(railGroups||[]).includes(p.catalog_group_id);
        if((railExclude?selected:!selected)&&!railIncludedEdges.includes(p.network_edge_id))return false;
      }
      if(railExcludedEdges.includes(p.network_edge_id))return false;
    }
    return true;
  };
  const visibleSelection=selectedFeatures.filter(feature=>{
    if(vehicleLayers.includes(feature.__layer))return false;
    return entityVisible(feature);
  }).flatMap(({__layer,...feature})=>feature.properties.service_id?
    (config.sources.roadServices?.features||[]).filter(value=>value.properties.service_id===feature.properties.service_id):[feature]);
  map.getSource('selection').setData(visibleSelection.length?{type:'FeatureCollection',features:visibleSelection}:(selectedFeatures.length===0&&selectedFeature&&entityVisible(selectedFeature)&&!vehicleLayers.includes(selectedLayer)?{type:'FeatureCollection',features:[selectedFeature]}:empty));
}
function applyVisibility() {
  updateHistoryLines();
  for (const [key, ids] of Object.entries(groups)) for (const id of ids) if (map.getLayer(id)) map.setLayoutProperty(id,'visibility',visibility[key]?'visible':'none');
  if(visibility.railStationTracks)for(const id of groups.rail)if(map.getLayer(id))map.setLayoutProperty(id,'visibility','visible');
  applyRoadRouteFilter();applyRailWays();sharedRailStationFilter();applyVehicleAppearance();applyRailPointStyles();applyLineLabelVisibility();refreshSelection();
  // DOM markers own moving vehicles. Never re-upload infrastructure or ask
  // MapLibre's symbol collision pass to replace labels on every simulation tick.
  for(const id of [...groups.vehicles,...groups.railVehicles])if(map.getLayer(id))map.setLayoutProperty(id,'visibility','none');
  metroVehicleOverlay?.setVisible(visibility.vehicles);railVehicleOverlay?.setVisible(visibility.railVehicles);
}
function applyLineFilter() {
  updateHistoryLines();
  const filter = ['in',['get','route_relation_id'],['literal',visibleIds]];
  for (const id of ['metro','line-labels']) if (map.getLayer(id)) map.setFilter(id,filter);
  // Station sources are already bounded to the selected stable station IDs.
  const stationFilter = ['==',['literal',1],1];
  for (const id of ['stations','station-labels','areas-fill','areas-outline']) if (map.getLayer(id)) map.setFilter(id,stationFilter);
  // Operating visibility is controlled by line/train groups, independently of physical layers.
  refreshSelection();
}
function applyConstructionFilter(){updateHistoryLines();if(map.getLayer('construction'))map.setFilter('construction',['in',['get','osm_way_id'],['literal',constructionIds]]);refreshSelection();}
function applyBaseDetails() {
  if (!map.getStyle()) return;
  for (const layer of map.getStyle().layers) {
    if (layer.source !== 'openmaptiles') continue;
    const group = detailGroup(layer);
    let on = (!group || baseDetails[group]) && (layer.type!=='symbol'||baseDetails.labels);
    map.setLayoutProperty(layer.id,'visibility',on?'visible':'none');
  }
}
function fitFocusedBounds(duration=0) {
  if(!focusedBounds)return;
  const canvas=map.getCanvas();
  const padding=Math.max(12,Math.min(48,canvas.clientHeight*.12,canvas.clientWidth*.08));
  map.fitBounds(focusedBounds,{padding:viewportPadding(padding),duration,maxZoom:14,pitch:0,bearing:0});
}
function focusDemo() {
  focusedBounds=null;
  if(!config.demo.coordinates.length)return;
  const bounds=config.demo.coordinates.reduce((b,p)=>b.extend(p),new maplibregl.LngLatBounds());
  map.fitBounds(bounds,{padding:{top:130,left:80,right:80,bottom:100},duration:700,maxZoom:12.5});
  document.getElementById('scene-title').textContent='上海 · 轨道交通';
}
async function setBase(type) {
  if (!map.getLayer('metro')) {pendingBase=type;return;}
  currentBase=type;
  sourceReadySent=false;
  document.getElementById('loading').style.display='flex';
  // Diff-style swaps do not emit style.load. Explicitly reload so GIS layers
  // are reinstalled deterministically instead of disappearing after a swap.
  if(type.startsWith('satellite')){vectorAvailable=false;map.setStyle(rasterStyle(type),{diff:false});}
  else if(adminLevels[type]){vectorAvailable=false;map.setStyle(administrativeStyle(type),{diff:false});}
  else if(standardStyle){vectorAvailable=true;map.setStyle(structuredClone(standardStyle),{diff:false});}
  else {standardStyle=fallbackStandardStyle();vectorAvailable=true;map.setStyle(structuredClone(standardStyle),{diff:false});}
  document.getElementById('base-status').textContent={standard:'标准地图',satellite:'卫星影像 · 10 m','admin-province':'省级行政区','admin-city':'市级行政区','admin-county':'县级行政区'}[type];
  report('baseChanged',type,vectorAvailable);
}
function selectionKey(feature){
  const p=feature.properties||{};
  if(p.campus_id)return "campus:"+p.campus_id;
  if(p.service_id)return "service:"+p.service_id;
  return [feature.__layer||feature.layer?.id,p.infrastructure_id,p.catalog_group_id,p.network_edge_id,p.osm_node_id,p.osm_way_id,p.route_relation_id,p.station_id,p.corridor_id,p.trip_id,p.service_id,p.source_ref].join('|');
}
function normalizedFeature(feature){
  const properties={...feature.properties};
  for(const [key,value] of Object.entries(properties)){
    if(typeof value==='string'&&/^[\s]*[\[{]/.test(value)){
      try{properties[key]=JSON.parse(value);}catch{/* Ordinary text stays text. */}
    }
  }
  const layer=feature.layer?.id||feature.__layer;
  return {type:'Feature',properties,geometry:feature.geometry,__layer:layer?.startsWith('history-')?layer.slice(8).replace(/-labels$/,''):layer};
}
function publishSelection(){
  const payload=selectedFeatures.map(({__layer,...feature})=>({layer:__layer,properties:feature.properties,geometry:feature.geometry,geometry_type:feature.geometry?.type}));
  report('featuresSelected',JSON.stringify(payload));
  const primary=payload.at(-1);
  if(primary)report('featureSelected',JSON.stringify(primary));
}
function selectFeature(feature,additive=false) {
  const normalized=normalizedFeature(feature),key=selectionKey(normalized);
  if(additive){
    const index=selectedFeatures.findIndex(value=>selectionKey(value)===key);
    if(index>=0)selectedFeatures.splice(index,1);else selectedFeatures.push(normalized);
  }else selectedFeatures=[normalized];
  const primary=selectedFeatures.at(-1)||null;
  selectedFeature=primary?{type:'Feature',properties:primary.properties,geometry:primary.geometry}:null;
  selectedLayer=primary?.__layer||null;
  refreshSelection();publishSelection();
}
async function init() {
  config=await (await fetch('/config.json')).json();visibleIds=config.visibleIds;constructionIds=config.constructionIds;minZooms=config.minZooms||{};
  if(config.appearance)setAppearance(config.appearance);
  chineseMapFamily=config.fonts?.map?.family||'Microsoft YaHei UI';
  try {
    const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),8000);
    const response=await fetch('https://tiles.openfreemap.org/styles/liberty',{signal:controller.signal});clearTimeout(timer);
    if(!response.ok)throw new Error('Vector style request failed');
    standardStyle=styleWorkbench(await response.json());vectorAvailable=true;
  } catch(error) { standardStyle=fallbackStandardStyle();vectorAvailable=true; }
  maplibregl.setWorkerCount(2);
  map=new maplibregl.Map({container:'map',center:[105,35],zoom:4,style:standardStyle||rasterStyle('standard'),attributionControl:true,renderWorldCopies:false,
    localIdeographFontFamily:chineseMapFamily,
    pixelRatio:1,maxCanvasSize:[2560,1440],maxTileCacheSize:96,antialias:false,fadeDuration:0,crossSourceCollisions:false});
  railVehicleOverlay=new window.RailScopeMotion.VehicleOverlay(map,maplibregl.Marker,selectFeature,'rail-vehicles');
  metroVehicleOverlay=new window.RailScopeMotion.VehicleOverlay(map,maplibregl.Marker,selectFeature,'vehicles');
  map.on('webglcontextlost',()=>{
    universityController?.abort();clearTimeout(universityTimer);++universityRequest;
    graphicsPaused=true;railVehicleOverlay.setPaused(true);metroVehicleOverlay.setPaused(true);railController?.abort();clearTimeout(railTimer);++railRequest;roadController?.abort();clearTimeout(roadTimer);++roadRequest;
    document.getElementById('loading').style.display='none';
    document.getElementById('camera-status').textContent='图形资源中断，地图已暂停；可保存计划后重启';
    report('mapError','图形上下文丢失，已暂停地图数据更新，运行计划仍可保存。');
  });
  map.on('webglcontextrestored',()=>{graphicsPaused=false;railVehicleOverlay.setPaused(false);metroVehicleOverlay.setPaused(false);staticSourceState.clear();metroSourceKeys.clear();railSourceKeys.clear();roadSourceKey=null;updateStaticSources();scheduleRailViewport();scheduleRoadViewport();scheduleMetroViewport();});
  document.addEventListener('visibilitychange',()=>{scheduleRailViewport();scheduleRoadViewport();scheduleMetroViewport();scheduleAdminViewport();updateStaticSources();});
  map.on('moveend',updateStaticSources);
  map.on('moveend',scheduleUniversityViewport);
  document.addEventListener('visibilitychange',scheduleUniversityViewport);
  // Keep a selected corridor fully visible when the editor changes map size.
  // User navigation releases this framing instead of snapping back later.
  map.on('resize',()=>fitFocusedBounds());
  map.on('movestart',event=>{if(event.originalEvent)focusedBounds=null;});
  for(const id of ['map-tools','location-panel']){
    const element=document.getElementById(id);
    element.addEventListener('click',()=>{focusedBounds=null;},true);
    element.addEventListener('change',()=>{focusedBounds=null;},true);
  }
  report('baseChanged',currentBase,vectorAvailable);
  map.addControl(new maplibregl.ScaleControl({maxWidth:90,unit:'metric'}),'bottom-left');
  map.on('style.load',()=>{
    installLayers();
    installHistoryLayers();
    if(config.fonts)setFonts(config.fonts);
    if(pendingBase){const type=pendingBase;pendingBase=null;if(type!==currentBase){setBase(type);return;}}
    // A hidden, empty metro source may never emit isSourceLoaded. The map
    // shell is usable after the first frame; viewport data arrives separately.
    map.once('render',()=>{if(!sourceReadySent){sourceReadySent=true;document.getElementById('loading').style.display='none';report('dataReady');}});
    if (!animationStarted) {
      animationStarted=true;running=false;
      map.fitBounds([[73,18],[135,54]],{padding:60,duration:0});
      report('ready');report('demoState',running);
    }
  });
  map.on('error',event=>{
    const message=String(event.error?.message||event.error);
    const target=message==='The source image could not be decoded.'?mapWarnings:mapErrors;
    if(!target.includes(message)){target.push(message);if(target.length>100)target.shift();}
  });
  map.on('sourcedata',event=>{if(event.sourceId==='metro'&&event.isSourceLoaded&&!sourceReadySent){sourceReadySent=true;document.getElementById('loading').style.display='none';report('dataReady');}});
  map.on('moveend',()=>{const p=map.getCenter(),status=`${p.lat.toFixed(3)}° N · ${p.lng.toFixed(3)}° E`;if(status!==lastCameraStatus){lastCameraStatus=status;document.getElementById('camera-status').textContent=status;report('cameraChanged',p.lng,p.lat,map.getZoom());}const nearest=config.cities.reduce((best,city)=>{const d=Math.hypot((city.center[0]-p.lng)*Math.cos(p.lat*Math.PI/180),city.center[1]-p.lat);return d<best.d?{city,d}:best;},{city:null,d:Infinity});document.getElementById('scene-title').textContent=map.getZoom()>=9&&nearest.d<.6?nearest.city.name+' · 轨道交通':'全国 · 轨道交通';});
  const selectableLayers=()=>['rail-vehicle-symbols','rail-vehicles','rail-plan-stations','rail-plan-path','vehicles-symbol','vehicles','university-labels','university-pois','university-outline','university-fill','history-rail','history-metro','history-road','stations','areas-fill','metro','construction','rail-points','rail-detail-points','rail-station-labels','rail-platform-fill','rail-platform-outline','rail-station-fill','rail-station-outline','rail-signal-box-fill','rail-signal-box-outline','rail-signal-box-symbol','rail-construction','rail','road-service-poi','road-service-buildings','road-service-outline-fill','road-construction','road','imported-fill','imported-line','imported-point'].filter(id=>map.getLayer(id));
  map.on('dblclick',event=>{
    const feature=map.queryRenderedFeatures(event.point,{layers:selectableLayers()})[0];
    if(!feature)return;
    event.preventDefault();
    selectFeature(feature);
    const value=normalizedFeature(feature);
    report('featureActivated',JSON.stringify({layer:value.__layer,properties:value.properties,geometry:value.geometry}));
  });
  map.on('click',event=>{
    if(suppressMapClick){suppressMapClick=false;return;}
    const feature=map.queryRenderedFeatures(event.point,{layers:selectableLayers()})[0];
    if(feature)selectFeature(feature,!!(event.originalEvent?.ctrlKey||event.originalEvent?.metaKey));
    else if(!(event.originalEvent?.ctrlKey||event.originalEvent?.metaKey)){
      selectedFeatures=[];selectedFeature=null;selectedLayer=null;refreshSelection();publishSelection();
    }
  });
  map.on('contextmenu',event=>{
    event.preventDefault();
    const feature=map.queryRenderedFeatures(event.point,{layers:selectableLayers()})[0];
    if(feature&&selectedFeatures.length<2&&!selectedFeatures.some(value=>selectionKey(value)===selectionKey(normalizedFeature(feature))))selectFeature(feature);
    report('mapContextRequested');
  });
  map.on('mousedown',event=>{
    if(!selectionMode.startsWith('box')||event.originalEvent?.button!==0)return;
    event.preventDefault();boxSelecting=true;
    const start={x:event.point.x,y:event.point.y},box=document.getElementById('selection-box'),canvas=map.getCanvasContainer();
    box.hidden=false;box.style.left=start.x+'px';box.style.top=start.y+'px';box.style.width='0';box.style.height='0';
    const move=mouse=>{
      const rect=canvas.getBoundingClientRect(),current={x:mouse.clientX-rect.left,y:mouse.clientY-rect.top};
      box.style.left=Math.min(start.x,current.x)+'px';box.style.top=Math.min(start.y,current.y)+'px';
      box.style.width=Math.abs(start.x-current.x)+'px';box.style.height=Math.abs(start.y-current.y)+'px';
    };
    const up=mouse=>{
      document.removeEventListener('mousemove',move);document.removeEventListener('mouseup',up);box.hidden=true;boxSelecting=false;suppressMapClick=true;
      const rect=canvas.getBoundingClientRect(),end={x:mouse.clientX-rect.left,y:mouse.clientY-rect.top};
      const layerGroups={box_switch:['rail-detail-points'],box_line:['rail','rail-stripes','rail-construction','history-rail'],box_station:['rail-points','rail-detail-points','stations']};
      const layers=(layerGroups[selectionMode]||selectableLayers()).filter(id=>map.getLayer(id));
      const features=map.queryRenderedFeatures([[Math.min(start.x,end.x),Math.min(start.y,end.y)],[Math.max(start.x,end.x),Math.max(start.y,end.y)]],{layers});
      const values=[],seen=new Set();
      for(const feature of features){
        if(selectionMode==='box_switch'&&feature.properties?.kind!=='switch')continue;
        if(selectionMode==='box_station'&&feature.layer?.id!=='stations'&&!['station','halt','signal_box','junction','crossing','yard','depot','workshop','works','engine_shed'].includes(feature.properties?.kind))continue;
        const value=normalizedFeature(feature),key=selectionKey(value);
        if(!seen.has(key)){seen.add(key);values.push(value);}
        if(values.length>=5000)break;
      }
      selectedFeatures=(mouse.ctrlKey||mouse.metaKey)?[...selectedFeatures,...values.filter(value=>!selectedFeatures.some(old=>selectionKey(old)===selectionKey(value)))]:values;
      const primary=selectedFeatures.at(-1)||null;selectedFeature=primary?{type:'Feature',properties:primary.properties,geometry:primary.geometry}:null;selectedLayer=primary?.__layer||null;
      refreshSelection();publishSelection();
    };
    document.addEventListener('mousemove',move);document.addEventListener('mouseup',up);
  });
  let hoverPoint=null,hoverTimer=null;
  map.on('mousemove',event=>{
    if(selectionMode.startsWith('box')){map.getCanvas().style.cursor='crosshair';return;}
    hoverPoint=event.point;
    if(hoverTimer)return;
    hoverTimer=setTimeout(()=>{
      hoverTimer=null;
      if(selectionMode.startsWith('box')||!hoverPoint)return;
      const ids=selectableLayers();
      map.getCanvas().style.cursor=map.queryRenderedFeatures(hoverPoint,{layers:ids}).length?'pointer':'grab';
    },50);
  });
  const locationPanel=document.getElementById('location-panel');
  const citySelect=document.getElementById('city-view'), lineSelect=document.getElementById('line-view');
  for(const [index,city] of config.cities.entries()){const option=new Option(city.name,String(index));citySelect.add(option);}
  function populateLocationLines(city=''){lineSelect.replaceChildren(new Option(city?city+' · 选择线路':'选择城市与线路',''));for(const [index,line] of config.lineViews.entries())if(!city||line.city===city)lineSelect.add(new Option(line.name,String(index)));}
  populateLocationLines();
  citySelect.onchange=()=>{if(citySelect.value===''){populateLocationLines();return;}const city=config.cities[Number(citySelect.value)];populateLocationLines(city.name);map.flyTo({center:city.center,zoom:11});document.getElementById('scene-title').textContent=city.name+' · 轨道交通';};
  lineSelect.onchange=()=>{if(lineSelect.value==='')return;const line=config.lineViews[Number(lineSelect.value)], b=line.bounds;map.fitBounds([[b[0],b[1]],[b[2],b[3]]],{padding:viewportPadding(),maxZoom:13});document.getElementById('scene-title').textContent=line.name;locationPanel.hidden=true;};
  document.getElementById('coordinate-go').onclick=()=>{const values=document.getElementById('coordinate-view').value.split(/[,，\s]+/).filter(Boolean).map(Number);if(values.length!==2||!values.every(Number.isFinite)||Math.abs(values[0])>180||Math.abs(values[1])>85){document.getElementById('location-message').textContent='请输入合法经纬度，如 121.47, 31.23';return;}map.flyTo({center:values,zoom:14});locationPanel.hidden=true;};
  document.getElementById('follow-train').onclick=()=>{followTrain=!followTrain;document.getElementById('follow-train').textContent=followTrain?'停止跟随':'跟随首列车';};
  map.on('dragstart',()=>{followTrain=false;document.getElementById('follow-train').textContent='跟随首列车';});
  map.on('moveend',()=>{if(!trainFollowMoving)scheduleRailViewport();scheduleRoadViewport();scheduleMetroViewport();scheduleAdminViewport();});
  function showLocationPanel(on){locationPanel.hidden=typeof on==='boolean'?!on:!locationPanel.hidden;if(!locationPanel.hidden)document.getElementById('coordinate-view').focus();}
  function fitFeatures(features){
    const bounds=RailScopeNavigation.geometryBounds(features);
    if(!bounds){report('notice','没有可定位的要素，请先勾选图层或选中地图对象。');return;}
    focusedBounds=null;map.fitBounds(bounds,{padding:viewportPadding(),maxZoom:16,duration:500});
  }
  new RailScopeNavigation.Navigation(map,document,{
    shouldRecord:()=>!trainFollowMoving,
    home:()=>{focusedBounds=null;map.fitBounds([[73,18],[135,54]],{padding:viewportPadding(),duration:700,bearing:0,pitch:0});},
    location:showLocationPanel,selected:()=>fitFeatures(selectedFeatures.length?selectedFeatures:[selectedFeature].filter(Boolean)),
    visible:()=>{
      const features=[];
      if(visibility.university)features.push(...universityData.features);
      for(const [kind,data] of entityPresentation.sources)if(railSourceVisible(kind))features.push(...data.features);
      for(const id of visibleIds){const b=config.routeBounds[id];if(b)features.push({geometry:{coordinates:[[b[0],b[1]],[b[2],b[3]]]}});}
      for(const key of ['road','imported'])if(visibility[key]&&Array.isArray(config.sources[key]?.features))features.push(...config.sources[key].features);
      fitFeatures(features);
    },
  });
  window.railscope={
    setAppearance, setUiInsets, setFonts,
    setUniversitySelection(ids){universitySelection=ids||[];scheduleUniversityViewport();refreshSelection();},
    reloadUniversityViewport(){universityKey=null;scheduleUniversityViewport();},
    setInfrastructureHistory(day,records){
      const previouslyConfigured=hasRailHistory();
      applyInfrastructureHistory(day,records);
      updateHistoryLines();refreshSelection();
      if(previouslyConfigured!==hasRailHistory()){railSourceKeys.clear();scheduleRailViewport();}
    },
    showLocationPanel,
    setRunSystem(system){
      const rail=system==='rail';
      document.getElementById('legend-line-label').textContent=rail?'单向运行通道':'运营地铁线路';
      document.getElementById('legend-line-swatch').style.backgroundColor=rail?'#466979':'#c82732';
      document.getElementById('legend-station-label').textContent=rail?'经停控制点':'地铁站';
      document.getElementById('legend-vehicle-label').textContent=rail?'国铁列车':'地铁列车';
    },
    setRailWays(ids){railWays=ids;railSections=null;railGroups=null;railExclude=false;applyRailWays();refreshSelection();scheduleRailViewport();},
    reloadRailViewport(){railSourceKeys.clear();scheduleRailViewport();},
    patchRailEntities(changes){entityPresentation.patch(changes);},
    reloadMetroViewport(){metroSourceKeys.clear();scheduleMetroViewport();},
    reloadRoadViewport(){roadSourceKey=null;roadServiceKey=null;scheduleRoadViewport();},
    reloadAdminViewport(){adminSourceKey=null;scheduleAdminViewport();},
    enableRoadViewport(){config.roadViewport=true;config.sources.road=empty;staticSourceState.delete('road');roadSourceKey=null;roadServiceKey=null;map.getSource('road')?.setData(empty);scheduleRoadViewport();},
    setRoadRouteSelection(key){roadRouteSelection=key||null;applyRoadRouteFilter();refreshSelection();scheduleRoadViewport();},
    setRoadServices(keys){roadVisibleServices=Array.isArray(keys)?keys:[];for(const id of groups.roadServices)if(map.getLayer(id))map.setFilter(id,['all',['in',['get','service_id'],['literal',roadVisibleServices]],['==',['get','asset_kind'],id.includes('buildings')?'building':id.includes('outline')?'outline':'poi']]);refreshSelection();scheduleRoadViewport();},
    setRoadRoutes(keys){roadVisibleRoutes=Array.isArray(keys)?keys:null;applyRoadRouteFilter();refreshSelection();scheduleRoadViewport();},
    setRailSelection(sectionIds,wayIds,groupIds=null){railSections=sectionIds;railWays=wayIds;railGroups=groupIds;railExclude=false;applyRailWays();refreshSelection();scheduleRailViewport();},
    setRailExclusions(sectionIds,wayIds,groupIds=null){railSections=sectionIds;railWays=wayIds;railGroups=groupIds;railExclude=true;applyRailWays();refreshSelection();scheduleRailViewport();},
    setRailEdgeSelection(included,excluded){railIncludedEdges=Array.isArray(included)?included:[];railExcludedEdges=Array.isArray(excluded)?excluded:[];applyRailWays();refreshSelection();scheduleRailViewport();},
    setRailFacilityMode(mode,groups=[]){railFacilityMode=['all','lines','facilities'].includes(mode)?mode:'all';railFacilityGroups=Array.isArray(groups)?groups:[];scheduleRailViewport();},
    setRailStyles(value){config.railStyles=value;applyRailStyles();updateHistoryPaints();},
    setRoadStyles(value){config.roadStyles=value;applyRoadStyles();updateHistoryPaints();},
    setRailPointStyles(value){config.railPointStyles=value;applyRailPointStyles();applyLineLabelVisibility();updateHistoryPaints();updateHistoryLines();},
    setRailSignalBoxes(value){config.sources.railSignalBoxes=value||empty;map.getSource('railSignalBoxes')?.setData(value||empty);},
    setMetroStyles(value){config.metroStyles=value;applyMetroStyles();updateHistoryPaints();},
    setMinZooms(value){minZooms=value||{};config.minZooms=minZooms;applyMinZooms();staticSourceState.clear();metroSourceKeys.clear();railSourceKeys.clear();roadSourceKey=null;updateStaticSources();scheduleMetroViewport();scheduleRailViewport();scheduleRoadViewport();scheduleUniversityViewport();},
    setSelectionMode(value){selectionMode=['box','box_switch','box_line','box_station'].includes(value)?value:'click';if(selectionMode.startsWith('box'))map.dragPan.disable();else map.dragPan.enable();map.getCanvas().style.cursor=selectionMode.startsWith('box')?'crosshair':'grab';document.getElementById('selection-box').dataset.mode=selectionMode;},
    clearSelection(){selectedFeatures=[];selectedFeature=null;selectedLayer=null;refreshSelection();publishSelection();},
    setRailPlan(data){config.sources.railPlan=data;map.getSource('railPlan')?.setData(data);visibility.railPlan=true;applyVisibility();},
    setRailOperatingVehicles(data,clock,playing){config.sources.railVehicles=data;railVehicleOverlay.update(data,playing);},
    setRailVehicleAppearance(value){if(map.getLayer('rail-vehicles')){const size=Math.max(8,Math.min(40,Number(value.size)||14));map.setPaintProperty('rail-vehicles','circle-radius',['/', ['coalesce',['get','display_size'],size],2]);}},
    async exportHighQuality(width){
      try{report('imageCaptured',await window.exportRailScopeMap(map,width,[railVehicleOverlay,metroVehicleOverlay]));}
      catch(error){report('imageCaptured','error: '+error.message);}
    },
    captureMap(waitForIdle=false){
      const event=waitForIdle&&!map.loaded()?'idle':'render';
      let done=false;
      const finish=data=>{if(done)return;done=true;clearTimeout(timeout);map.off(event,capture);report('imageCaptured',data);};
      const capture=()=>{
        try{
          const source=map.getCanvas(),canvas=document.createElement('canvas');canvas.width=source.width;canvas.height=source.height;
          const ctx=canvas.getContext('2d');ctx.drawImage(source,0,0);
          railVehicleOverlay.draw(ctx,source.width/map.getContainer().clientWidth);metroVehicleOverlay.draw(ctx,source.width/map.getContainer().clientWidth);
          const credit=document.querySelector('.maplibregl-ctrl-attrib-inner')?.textContent||'© OpenStreetMap contributors';
          const scale=window.devicePixelRatio||1;ctx.font=`${12*scale}px sans-serif`;ctx.fillStyle='rgba(255,255,255,.9)';ctx.fillRect(0,canvas.height-24*scale,canvas.width,24*scale);ctx.fillStyle='#263b47';ctx.fillText(credit,8*scale,canvas.height-8*scale);
          finish(canvas.toDataURL('image/png'));
        }catch(error){finish('error: '+error.message);}
      };
      const timeout=setTimeout(()=>finish('error: 地图渲染等待超时，请检查底图或缩小范围后重试'),15000);
      map.once(event,capture);map.triggerRepaint();
    },
    setVisibility(key,on){if(visibility[key]===!!on)return;visibility[key]=!!on;applyVisibility();updateStaticSources();if(['rail','railConstruction','railStationTracks','railStations','railControlPoints'].includes(key))scheduleRailViewport();if(['road','roadConstruction','roadServices'].includes(key))scheduleRoadViewport();if(['metro','stations','construction'].includes(key))scheduleMetroViewport();if(key==='university')scheduleUniversityViewport();},
    setOverlay,
    setVehicleAppearance(value){const size=Number(value.size);if(!Number.isFinite(size)||!['glow','ring','train'].includes(value.style))return;vehicleAppearance={size:Math.max(8,Math.min(40,size)),style:value.style};applyVisibility();},
    setLines(ids){visibleIds=ids;applyLineFilter();updateStaticSources();scheduleMetroViewport();},
    setMetroStations(ids){visibleStationIds=ids;applyLineFilter();staticSourceState.delete('stations');staticSourceState.delete('areas');updateStaticSources();scheduleMetroViewport();},
    setRailPointExclusions(ids){railPointExclusions=ids;sharedRailStationFilter();},
    setRailPointSelection(ids){railPointIncludes=ids;sharedRailStationFilter();},setBase,
    setRailAssetSelection(platforms,hiddenPlatforms,switches,hiddenSwitches){railAssetSelection={platforms,hiddenPlatforms,switches,hiddenSwitches};sharedRailStationFilter();},
    setRailControlPointSelection(ids){railControlPointIncludes=ids;sharedRailStationFilter();},
    setRailLineSelection(ids,excluded=[]){const canonical=id=>config.railLinePresentation?.[id]?.line_id||id;railLineIds=ids===null?null:[...new Set(ids.map(canonical))];railHiddenLineIds=[...new Set(excluded.map(canonical))];sharedRailStationFilter();},
    setConstruction(ids){constructionIds=ids;applyConstructionFilter();scheduleMetroViewport();},
    setBaseDetail(key,on){baseDetails[key]=on;applyBaseDetails();},focusDemo,
    focusChina(){document.getElementById('focus-china').click();},
    focus(lon,lat,zoom=13,title=''){focusedBounds=null;map.flyTo({center:[lon,lat],zoom,duration:700});if(title)document.getElementById('scene-title').textContent=title;},
    focusBounds(bounds){focusedBounds=bounds;map.resize();fitFocusedBounds(0);},
    fit(bounds,title=''){focusedBounds=bounds;map.resize();fitFocusedBounds(600);if(title)document.getElementById('scene-title').textContent=title;},
    setOperatingVehicles(data,clock,playing){
      operatingMode=true;operatingVehicles=data;operatingClock=clock;running=playing;
      travelled=data.features[0]?.properties.distance_m||0;
      metroVehicleOverlay.update(data,playing);
      if(['vehicles','vehicles-symbol'].includes(selectedLayer)&&selectedFeature){selectedFeature=data.features.find(f=>f.properties.vehicle_id===selectedFeature.properties.vehicle_id)||null;}
      if(followTrain){const vehicle=data.features.find(f=>visibleIds.includes(f.properties.route_relation_id));if(vehicle){trainFollowMoving=true;clearTimeout(trainFollowTimer);map.easeTo({center:vehicle.geometry.coordinates,zoom:Math.max(map.getZoom(),13),duration:240,easing:t=>t});trainFollowTimer=setTimeout(()=>{trainFollowMoving=false;scheduleRailViewport();},500);}}
    },
    imported(data){config.sources.imported=data;staticSourceState.delete('imported');updateStaticSources();},
    testState(){
      const baseSource=adminLevels[currentBase]?'administrative':vectorAvailable?'openmaptiles':'base';
      const allowsLine1=id=>map.getLayer(id)&&(map.getFilter(id)||[]).some(clause=>Array.isArray(clause)&&clause[0]==='in'&&clause[1]===199200);
      const railCoordinates=(config.sources.railPlan?.features||[]).filter(f=>f.geometry.type==='LineString').flatMap(f=>f.geometry.coordinates);
      return {railPlanInView:railCoordinates.length>0&&railCoordinates.every(p=>map.getBounds().contains(p)),railVehiclesCount:config.sources.railVehicles?.features.length||0,legendSystem:document.getElementById('legend-vehicle-label').textContent,ready:sourceReadySent,sources:Object.keys(map.getStyle().sources),
        metroLoaded:!!map.getSource('metro')&&map.isSourceLoaded('metro'),
        baseLoaded:!!map.getSource(baseSource)&&map.isSourceLoaded(baseSource),
        currentBase,adminFeatureCount,visibleLines:visibleIds.length,visibleConstruction:constructionIds.length,selectionMode,selectedCount:selectedFeatures.length,
        universityFeatures:universityData.features.length,fontSettings:config.fonts,uiInsets,
        universityPoiScreen:(()=>{const feature=universityData.features.find(f=>f.geometry.type==='Point');if(!feature)return null;const p=map.project(feature.geometry.coordinates);return [p.x,p.y];})(),
        center:map.getCenter(),zoom:map.getZoom(),
        loadingVisible:document.getElementById('loading').style.display!=='none',
        rendered:Object.fromEntries(['metro','stations','rail','rail-points','road','admin-fill','university-fill','university-pois'].filter(id=>map.getLayer(id)).map(id=>[id,map.queryRenderedFeatures({layers:[id]}).length])),
        line1StationsAllowed:allowsLine1('stations'),line1AreasAllowed:allowsLine1('areas-fill'),
        running,travelled,operatingMode,operatingClock,activeVehicles:operatingVehicles.features.length,
        visibility:{...visibility},vehicleVisible:visibility.vehicles,vectorAvailable,errors:mapErrors,warnings:mapWarnings,overlays,vehicleAppearance,
        markerRadius:map.getPaintProperty('vehicles','circle-radius'),trainIconVisibility:map.getLayoutProperty('vehicles-symbol','visibility'),
        titleHidden:document.getElementById('map-title').hidden,hasLine1Button:!!document.getElementById('focus-demo'),
        vehicle:operatingVehicles.features[0]?.geometry.coordinates||null,
        metroVisibility:map.getLayer('metro')?map.getLayoutProperty('metro','visibility'):null,
        roadBaseVisibility:map.getLayer('road_motorway')?map.getLayoutProperty('road_motorway','visibility'):null};
    }
  };
}
new QWebChannel(qt.webChannelTransport,channel=>{bridge=channel.objects.bridge;init().catch(error=>report('mapError',String(error)));});
