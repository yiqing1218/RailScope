'use strict';
let bridge, map, config, standardStyle, currentBase = 'standard', selectedFeature = null, selectedLayer = null;
let running = false, speed = 30, travelled = 0, direction = 1, lastTime = 0, lastReport = 0, vehicleVisible = true;
const visibility = {metro:true, stations:true, construction:true, rail:true, road:true, imported:true, vehicles:true};
const baseDetails = {roads:true, admin:true, labels:true, buildings:true};
let visibleIds = [], vectorAvailable = false, sourceReadySent = false, mapErrors = [];
let animationStarted = false, constructionIds=[], operatingMode=false, operatingVehicles=emptyPlaceholder(), operatingClock=25200;
let followTrain=false;
function emptyPlaceholder(){return {type:'FeatureCollection',features:[]};}
const empty = {type:'FeatureCollection',features:[]};
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
    }
    if(layer.type==='line'&&sl==='transportation')paint['line-color']=layer.id.includes('casing')?'#ccd6dd':'#fafcfd';
    if(layer.type==='line'&&sl==='waterway')paint['line-color']='#b9d4e0';
    if(layer.type==='symbol') {
      paint['text-color']='#536774';paint['text-halo-color']='#ffffff';
      if(layer.layout?.['text-field'])layer.layout['text-field']=['coalesce',['get','name:zh'],['get','name'],['get','name:latin']];
    }
  }
  return style;
}
function rasterStyle(type) {
  const satellite=type.startsWith('satellite'), lite=type==='satellite-lite';
  const tile=lite?'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_ShadedRelief_Bathymetry/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg':satellite?'https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2024_3857/default/g/{z}/{y}/{x}.jpg':'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
  const attribution=lite?'NASA GIBS Blue Marble':satellite?'<a href="https://cloudless.eox.at/documentation/license">EOxCloudless © EOX</a> (Contains modified Copernicus Sentinel data 2024) · CC BY-NC-SA 4.0':'© OpenStreetMap contributors';
  return {version:8,glyphs:'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',sources:{base:{type:'raster',tiles:[tile],maxzoom:lite?8:satellite?14:19,tileSize:256,attribution}},layers:[{id:'base-background',type:'background',paint:{'background-color':'#e8eef1'}},{id:'base-raster',type:'raster',source:'base',paint:{'raster-fade-duration':150}}]};
}
function addSource(id, data) { if (!map.getSource(id)) map.addSource(id,{type:'geojson',data,generateId:true}); }
function addLayer(layer) { if (!map.getLayer(layer.id)) map.addLayer(layer); }
function installLayers() {
  const sources = {...config.sources, vehicles:empty, selection:empty};
  for (const [id, data] of Object.entries(sources)) addSource(id, data);
  addLayer({id:'rail',type:'line',source:'rail',paint:{'line-color':'#667887','line-width':3}});
  addLayer({id:'road',type:'line',source:'road',paint:{'line-color':'#8898a4','line-width':2}});
  addLayer({id:'metro',type:'line',source:'metro',paint:{'line-color':['coalesce',['get','display_color'],'#718096'],'line-width':['interpolate',['linear'],['zoom'],4,1.5,8,3,12,5],'line-opacity':.95}});
  addLayer({id:'construction',type:'line',source:'construction',paint:{'line-color':'#475569','line-width':['interpolate',['linear'],['zoom'],4,1.5,10,3.5,14,5],'line-dasharray':[1.6,1.2]}});
  addLayer({id:'areas-fill',type:'fill',source:'areas',minzoom:12,paint:{'fill-color':'#078c92','fill-opacity':.16}});
  addLayer({id:'areas-outline',type:'line',source:'areas',minzoom:12,paint:{'line-color':'#087f85','line-width':1.5}});
  addLayer({id:'stations',type:'circle',source:'stations',minzoom:9,paint:{'circle-radius':['interpolate',['linear'],['zoom'],9,2.8,13,4.5],'circle-color':'#ffffff','circle-stroke-color':'#256c77','circle-stroke-width':1.5}});
  addLayer({id:'station-labels',type:'symbol',source:'stations',minzoom:12,layout:{'text-field':['get','name'],'text-font':vectorAvailable?['Noto Sans Regular']:['Open Sans Regular'],'text-size':11,'text-offset':[0,1.1],'text-anchor':'top'},paint:{'text-color':'#20313d','text-halo-color':'#ffffff','text-halo-width':1.5}});
  addLayer({id:'line-labels',type:'symbol',source:'metro',minzoom:10,layout:{'symbol-placement':'line','symbol-spacing':260,'text-field':['coalesce',['get','ref'],['get','line_name']],'text-font':vectorAvailable?['Noto Sans Bold']:['Open Sans Bold'],'text-size':11},paint:{'text-color':'#273d49','text-halo-color':'#ffffff','text-halo-width':2}});
  addLayer({id:'imported-fill',type:'fill',source:'imported',filter:['==',['geometry-type'],'Polygon'],paint:{'fill-color':'#697dcc','fill-opacity':.15}});
  addLayer({id:'imported-line',type:'line',source:'imported',filter:['!=',['geometry-type'],'Point'],paint:{'line-color':'#697dcc','line-width':3}});
  addLayer({id:'imported-point',type:'circle',source:'imported',filter:['==',['geometry-type'],'Point'],paint:{'circle-color':'#697dcc','circle-radius':5,'circle-stroke-color':'white','circle-stroke-width':2}});
  addLayer({id:'selection-line',type:'line',source:'selection',filter:['!=',['geometry-type'],'Point'],paint:{'line-color':'#0c9c90','line-width':9,'line-opacity':.35}});
  addLayer({id:'selection-point',type:'circle',source:'selection',filter:['==',['geometry-type'],'Point'],paint:{'circle-radius':12,'circle-color':'#0c9c90','circle-opacity':.2,'circle-stroke-color':'#0c9c90','circle-stroke-width':2}});
  addLayer({id:'vehicles-halo',type:'circle',source:'vehicles',paint:{'circle-radius':17,'circle-color':'#059b8d','circle-opacity':.18}});
  addLayer({id:'vehicles',type:'circle',source:'vehicles',paint:{'circle-radius':7,'circle-color':'#0c9184','circle-stroke-color':'#ffffff','circle-stroke-width':3}});
  addLayer({id:'vehicle-label',type:'symbol',source:'vehicles',minzoom:11,layout:{'text-field':['get','vehicle_id'],'text-font':vectorAvailable?['Noto Sans Bold']:['Open Sans Bold'],'text-size':10,'text-offset':[0,-2]},paint:{'text-color':'#0b6058','text-halo-color':'#ffffff','text-halo-width':2}});
  applyVisibility(); applyLineFilter(); applyConstructionFilter();applyBaseDetails();
  if(operatingMode)map.getSource('vehicles').setData(visibility.vehicles?operatingVehicles:empty);else updateVehicle();
  refreshSelection();
}
const groups = {metro:['metro','line-labels'],stations:['stations','station-labels','areas-fill','areas-outline'],construction:['construction'],rail:['rail'],road:['road'],imported:['imported-fill','imported-line','imported-point'],vehicles:['vehicles-halo','vehicles','vehicle-label']};
function refreshSelection(){
  if(!map.getSource('selection'))return;
  const props=selectedFeature?.properties||{};
  let ids=props.route_relation_ids||[];
  if(typeof ids==='string'){try{ids=JSON.parse(ids);}catch{ids=[];}}
  const group=Object.entries(groups).find(([,layers])=>layers.includes(selectedLayer))?.[0];
  const allowed=selectedFeature&&(!group||visibility[group])&&
    (!props.route_relation_id||visibleIds.includes(props.route_relation_id))&&
    (!['stations','areas-fill','areas-outline'].includes(selectedLayer)||ids.some(id=>visibleIds.includes(id)))&&
    (selectedLayer!=='construction'||constructionIds.includes(props.osm_way_id));
  map.getSource('selection').setData(allowed?{type:'FeatureCollection',features:[selectedFeature]}:empty);
}
function applyVisibility() { for (const [key, ids] of Object.entries(groups)) for (const id of ids) if (map.getLayer(id)) map.setLayoutProperty(id,'visibility',visibility[key]?'visible':'none');refreshSelection(); }
function applyLineFilter() {
  const filter = ['in',['get','route_relation_id'],['literal',visibleIds]];
  for (const id of ['metro','line-labels']) if (map.getLayer(id)) map.setFilter(id,filter);
  const stationFilter = visibleIds.length ? ['any',...visibleIds.map(id=>['in',id,['coalesce',['get','route_relation_ids'],['literal',[]]]])] : ['==',1,0];
  for (const id of ['stations','station-labels','areas-fill','areas-outline']) if (map.getLayer(id)) map.setFilter(id,stationFilter);
  for (const id of ['vehicles','vehicles-halo','vehicle-label']) if(map.getLayer(id))map.setFilter(id,filter);
  refreshSelection();
}
function applyConstructionFilter(){if(map.getLayer('construction'))map.setFilter('construction',['in',['get','osm_way_id'],['literal',constructionIds]]);refreshSelection();}
function applyBaseDetails() {
  if (!map.getStyle()) return;
  for (const layer of map.getStyle().layers) {
    if (layer.source !== 'openmaptiles') continue;
    const group = detailGroup(layer);
    let on = !group || baseDetails[group];
    if (currentBase === 'admin' && ['roads','buildings'].includes(group)) on = false;
    if (currentBase === 'admin' && (layer['source-layer']==='poi' || layer['source-layer']==='aeroway')) on = false;
    map.setLayoutProperty(layer.id,'visibility',on?'visible':'none');
  }
}
function demoPosition() {
  const data=config.demo, total=data.length_m;
  if(data.coordinates.length<2)return null;
  travelled=Math.max(0,Math.min(total,travelled));
  let low=0, high=data.cumulative.length-1;
  while(low+1<high){const mid=(low+high)>>1;if(data.cumulative[mid]<=travelled)low=mid;else high=mid;}
  const a=data.coordinates[low], b=data.coordinates[Math.min(low+1,data.coordinates.length-1)];
  const span=data.cumulative[Math.min(low+1,data.cumulative.length-1)]-data.cumulative[low];
  const t=span?(travelled-data.cumulative[low])/span:0;
  return [a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t];
}
function updateVehicle() {
  if (!config.demo.coordinates.length || !map.getSource('vehicles')) return;
  const feature={type:'Feature',properties:{vehicle_id:'SHM1-DEMO-001',name:'上海 1 号线 · SH-01',line_ref:'1',state:running?'演示运行':'已暂停',source:'线路几何演示',distance_km:(travelled/1000).toFixed(2),speed_multiplier:speed},geometry:{type:'Point',coordinates:demoPosition()}};
  map.getSource('vehicles').setData(visibility.vehicles?{type:'FeatureCollection',features:[feature]}:empty);
}
function frame(time) {
  const dt=lastTime?Math.min(.1,(time-lastTime)/1000):0;lastTime=time;
  if(running&&!operatingMode){travelled+=direction*18*speed*dt;if(travelled>=config.demo.length_m){travelled=config.demo.length_m;direction=-1;}if(travelled<=0){travelled=0;direction=1;}updateVehicle();}
  if(time-lastReport>500&&!operatingMode){report('demoProgress',travelled,config.demo.length_m,running);lastReport=time;}
  requestAnimationFrame(frame);
}
function focusDemo() {
  if(!config.demo.coordinates.length)return;
  const bounds=config.demo.coordinates.reduce((b,p)=>b.extend(p),new maplibregl.LngLatBounds());
  map.fitBounds(bounds,{padding:{top:130,left:80,right:80,bottom:100},duration:700,maxZoom:12.5});
  document.getElementById('scene-title').textContent='上海 · 轨道交通';
}
async function setBase(type) {
  if (!map.getLayer('metro')) return;
  currentBase=type;
  sourceReadySent=false;
  document.getElementById('loading').style.display='flex';
  // Diff-style swaps do not emit style.load. Explicitly reload so GIS layers
  // are reinstalled deterministically instead of disappearing after a swap.
  if(type.startsWith('satellite')){vectorAvailable=false;map.setStyle(rasterStyle(type),{diff:false});}
  else if(standardStyle){vectorAvailable=true;map.setStyle(structuredClone(standardStyle),{diff:false});}
  else {vectorAvailable=false;map.setStyle(rasterStyle(type),{diff:false});}
  document.getElementById('base-status').textContent={standard:'标准地图',satellite:'卫星影像 · 10 m',admin:'行政区划','satellite-lite':'卫星影像 · NASA'}[type];
  report('baseChanged',type,vectorAvailable);
}
function selectFeature(feature) {
  selectedFeature={type:'Feature',properties:feature.properties,geometry:feature.geometry};
  selectedLayer=feature.layer.id;refreshSelection();
  report('featureSelected',JSON.stringify({layer:feature.layer.id,properties:feature.properties,geometry_type:feature.geometry.type}));
}
async function init() {
  config=await (await fetch('/config.json')).json();visibleIds=config.visibleIds;constructionIds=config.constructionIds;
  try {
    const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),8000);
    const response=await fetch('https://tiles.openfreemap.org/styles/liberty',{signal:controller.signal});clearTimeout(timer);
    if(!response.ok)throw new Error('Vector style request failed');
    standardStyle=styleWorkbench(await response.json());vectorAvailable=true;
  } catch(error) { vectorAvailable=false; }
  map=new maplibregl.Map({container:'map',center:[121.44,31.22],zoom:11,style:standardStyle||rasterStyle('standard'),attributionControl:true,renderWorldCopies:false});
  report('baseChanged',currentBase,vectorAvailable);
  map.addControl(new maplibregl.ScaleControl({maxWidth:90,unit:'metric'}),'bottom-left');
  map.on('style.load',()=>{
    installLayers();
    if (!animationStarted) {
      animationStarted=true;running=false;
      if(config.demo.coordinates.length>1)focusDemo();
      else {
        map.fitBounds([[73,18],[135,54]],{padding:80});
        document.getElementById('scene-title').textContent='中国 · 城市轨道交通';
        document.getElementById('scene-subtitle').textContent='未加载上海 1 号线演示几何';
      }
      requestAnimationFrame(frame);report('ready');report('demoState',running);
    }
  });
  map.on('error',event=>{const message=String(event.error?.message||event.error);if(!mapErrors.includes(message))mapErrors.push(message);});
  map.on('sourcedata',event=>{if(event.sourceId==='metro'&&event.isSourceLoaded&&!sourceReadySent){sourceReadySent=true;document.getElementById('loading').style.display='none';report('dataReady');}});
  map.on('moveend',()=>{const p=map.getCenter();document.getElementById('camera-status').textContent=`${p.lat.toFixed(3)}° N · ${p.lng.toFixed(3)}° E`;report('cameraChanged',p.lng,p.lat,map.getZoom());});
  map.on('click',event=>{const ids=['vehicles','stations','areas-fill','metro','construction','rail','road','imported-fill','imported-line','imported-point'].filter(id=>map.getLayer(id));const f=map.queryRenderedFeatures(event.point,{layers:ids})[0];if(f)selectFeature(f);});
  map.on('mousemove',event=>{const ids=['vehicles','stations','areas-fill','metro','construction'].filter(id=>map.getLayer(id));map.getCanvas().style.cursor=map.queryRenderedFeatures(event.point,{layers:ids}).length?'pointer':'';});
  document.getElementById('focus-demo').onclick=focusDemo;
  const locationButton=document.getElementById('location-menu'), locationPanel=document.getElementById('location-panel');
  locationButton.onclick=()=>{locationPanel.hidden=!locationPanel.hidden;locationButton.setAttribute('aria-expanded',String(!locationPanel.hidden));};
  const citySelect=document.getElementById('city-view'), lineSelect=document.getElementById('line-view');
  for(const [index,city] of config.cities.entries()){const option=new Option(city.name,String(index));citySelect.add(option);}
  for(const [index,line] of config.lineViews.entries()){const option=new Option(line.name,String(index));lineSelect.add(option);}
  citySelect.onchange=()=>{if(citySelect.value==='')return;const city=config.cities[Number(citySelect.value)];map.flyTo({center:city.center,zoom:11});document.getElementById('scene-title').textContent=city.name+' · 轨道交通';locationPanel.hidden=true;};
  lineSelect.onchange=()=>{if(lineSelect.value==='')return;const line=config.lineViews[Number(lineSelect.value)], bounds=line.coordinates.reduce((b,p)=>b.extend(p),new maplibregl.LngLatBounds());map.fitBounds(bounds,{padding:80,maxZoom:13});document.getElementById('scene-title').textContent=line.name;locationPanel.hidden=true;};
  document.getElementById('coordinate-go').onclick=()=>{const values=document.getElementById('coordinate-view').value.split(/[,，\s]+/).filter(Boolean).map(Number);if(values.length!==2||!values.every(Number.isFinite)||Math.abs(values[0])>180||Math.abs(values[1])>85){document.getElementById('location-message').textContent='请输入合法经纬度，如 121.47, 31.23';return;}map.flyTo({center:values,zoom:14});locationPanel.hidden=true;};
  document.getElementById('fit-visible').onclick=()=>{const bounds=new maplibregl.LngLatBounds();for(const id of visibleIds){const b=config.routeBounds[id];if(b){bounds.extend([b[0],b[1]]);bounds.extend([b[2],b[3]]);}}if(!bounds.isEmpty())map.fitBounds(bounds,{padding:80,maxZoom:13});};
  document.getElementById('follow-train').onclick=()=>{followTrain=!followTrain;document.getElementById('follow-train').textContent=followTrain?'停止跟随':'跟随首列车';};
  map.on('dragstart',()=>{followTrain=false;document.getElementById('follow-train').textContent='跟随首列车';});
  document.getElementById('tilt').onclick=()=>map.easeTo({pitch:map.getPitch()>20?0:45});
  document.getElementById('focus-china').onclick=()=>{map.fitBounds([[73,18],[135,54]],{padding:80,duration:700});document.getElementById('scene-title').textContent='中国 · 城市轨道交通';};
  document.getElementById('zoom-in').onclick=()=>map.zoomIn();document.getElementById('zoom-out').onclick=()=>map.zoomOut();document.getElementById('north').onclick=()=>map.easeTo({bearing:0,pitch:0});
  window.railscope={
    setVisibility(key,on){visibility[key]=on;applyVisibility();if(key==='vehicles'){if(operatingMode)map.getSource('vehicles')?.setData(on?operatingVehicles:empty);else updateVehicle();}},
    setLines(ids){visibleIds=ids;applyLineFilter();},setBase,
    setConstruction(ids){constructionIds=ids;applyConstructionFilter();},
    setBaseDetail(key,on){baseDetails[key]=on;applyBaseDetails();},focusDemo,
    focusChina(){document.getElementById('focus-china').click();},
    focus(lon,lat,zoom=13,title=''){map.flyTo({center:[lon,lat],zoom,duration:700});if(title)document.getElementById('scene-title').textContent=title;},
    fit(bounds,title=''){map.fitBounds(bounds,{padding:80,duration:600,maxZoom:14});if(title)document.getElementById('scene-title').textContent=title;},
    setOperatingVehicles(data,clock,playing){
      operatingMode=true;operatingVehicles=data;operatingClock=clock;running=playing;
      travelled=data.features[0]?.properties.distance_m||0;
      map.getSource('vehicles')?.setData(visibility.vehicles?data:empty);
      if(selectedLayer==='vehicles'&&selectedFeature){selectedFeature=data.features.find(f=>f.properties.vehicle_id===selectedFeature.properties.vehicle_id)||null;refreshSelection();}
      if(followTrain){const vehicle=data.features.find(f=>visibleIds.includes(f.properties.route_relation_id));if(vehicle)map.jumpTo({center:vehicle.geometry.coordinates,zoom:Math.max(map.getZoom(),13)});}
    },
    play(){running=true;visibility.vehicles=true;applyVisibility();updateVehicle();report('demoState',true);},
    pause(){running=false;updateVehicle();report('demoState',false);},
    reset(){travelled=0;direction=1;updateVehicle();},setSpeed(value){speed=value;},
    imported(data){config.sources.imported=data;map.getSource('imported').setData(data);},
    testState(){
      const baseSource=vectorAvailable?'openmaptiles':'base';
      const allowsLine1=id=>map.getLayer(id)&&(map.getFilter(id)||[]).some(clause=>Array.isArray(clause)&&clause[0]==='in'&&clause[1]===199200);
      return {ready:sourceReadySent,sources:Object.keys(map.getStyle().sources),
        metroLoaded:!!map.getSource('metro')&&map.isSourceLoaded('metro'),
        baseLoaded:!!map.getSource(baseSource)&&map.isSourceLoaded(baseSource),
        currentBase,visibleLines:visibleIds.length,visibleConstruction:constructionIds.length,
        line1StationsAllowed:allowsLine1('stations'),line1AreasAllowed:allowsLine1('areas-fill'),
        running,travelled,operatingMode,operatingClock,activeVehicles:operatingVehicles.features.length,
        vehicleVisible:visibility.vehicles,vectorAvailable,errors:mapErrors,
        vehicle:operatingMode?operatingVehicles.features[0]?.geometry.coordinates:demoPosition(),
        metroVisibility:map.getLayer('metro')?map.getLayoutProperty('metro','visibility'):null,
        roadBaseVisibility:map.getLayer('road_motorway')?map.getLayoutProperty('road_motorway','visibility'):null};
    }
  };
}
new QWebChannel(qt.webChannelTransport,channel=>{bridge=channel.objects.bridge;init().catch(error=>report('mapError',String(error)));});
