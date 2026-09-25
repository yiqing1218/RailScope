'use strict';
const maplibregl = window.maplibregl;
let bridge, map, config, standardStyle, currentBase = 'standard', selectedFeature = null, selectedLayer = null;
let selectedFeatures=[],selectionMode='click',boxSelecting=false,suppressMapClick=false;
let running = false, travelled = 0;
const visibility = {metro:false, stations:false, construction:false, rail:false, railConstruction:false,railStations:false,railControlPoints:false,railVehicles:false,railPlan:false,road:false, imported:false, vehicles:false};
const baseDetails = {roads:true, admin:true, labels:true, buildings:true};
let visibleIds = [], visibleStationIds=[], vectorAvailable = false, sourceReadySent = false, mapErrors = [], mapWarnings = [];
let animationStarted = false, constructionIds=[], operatingMode=false, operatingVehicles=emptyPlaceholder(), operatingClock=25200;
let followTrain=false;
let trainFollowMoving=false,trainFollowTimer=null,lastRailVehicleSignature='',lastCameraStatus='';
let focusedBounds=null;
const overlays={title:false,tools:true,legend:true,status:false,scale:true};
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
  const satellite=type==='satellite';
  const tile=satellite?'https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2024_3857/default/g/{z}/{y}/{x}.jpg':'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
  const attribution=satellite?'<a href="https://cloudless.eox.at/documentation/license">EOxCloudless © EOX</a> (Contains modified Copernicus Sentinel data 2024) · CC BY-NC-SA 4.0':'© OpenStreetMap contributors';
  return {version:8,glyphs:'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',sources:{base:{type:'raster',tiles:[tile],maxzoom:satellite?14:19,tileSize:256,attribution}},layers:[{id:'base-background',type:'background',paint:{'background-color':'#e8eef1'}},{id:'base-raster',type:'raster',source:'base',paint:{'raster-fade-duration':150}}]};
}
function addSource(id, data) {
  if(map.getSource(id))return;
  const options={type:'geojson',data,generateId:true};
  if(id==='road'&&config?.roadViewport)options.attribution='© OpenStreetMap contributors';
  map.addSource(id,options);
}
let railRequest=0;
let railController=null,railTimer=null;
let roadRequest=0,roadController=null,roadTimer=null,roadRouteSelection=null;
let graphicsPaused=false;
const populatedRailSources=new Set();
const staticSourceState=new Map();
function updateStaticSources(){
  if(!config||graphicsPaused)return;
  const zoom=map.getZoom(),bounds=map.getBounds();
  for(const id of ['metro','stations','areas','construction','road','imported']){
    if(id==='road'&&config.roadViewport)continue;
    const group=id==='areas'?'stations':id;
    const on=!!visibility[group]&&!document.hidden&&zoom>=({stations:9,areas:12}[id]||0);
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
let railWays=null,railSections=null,railGroups=null,railExclude=false,railPointExclusions=null,railPointIncludes=null,railControlPointIncludes=null,railLineIds=null;
function railSourceVisible(kind){
  const zoom=map.getZoom();
  if(graphicsPaused||document.hidden)return false;
  if(kind==='rail')return visibility.rail||visibility.railConstruction;
  if(kind==='railPoints')return (visibility.railStations||visibility.railControlPoints)&&zoom>=10;
  if(kind==='railPlatforms')return visibility.railStations&&zoom>=12;
  if(kind==='railStationAreas')return visibility.railStations&&zoom>=11;
  return !!visibility[kind];
}
function scheduleRailViewport(){
  clearTimeout(railTimer);
  railController?.abort();
  ++railRequest;
  for(const kind of populatedRailSources){
    if(!railSourceVisible(kind)){
      map.getSource(kind)?.setData(empty);
      populatedRailSources.delete(kind);
    }
  }
  railTimer=setTimeout(updateRailViewport,180);
}
function scheduleRoadViewport(){
  if(!config?.roadViewport)return;
  clearTimeout(roadTimer);roadController?.abort();++roadRequest;
  if(!visibility.road||document.hidden||graphicsPaused){map.getSource('road')?.setData(empty);return;}
  roadTimer=setTimeout(updateRoadViewport,180);
}
async function updateRoadViewport(){
  if(!config.roadViewport||!visibility.road||!map.getSource('road')||document.hidden||graphicsPaused)return;
  roadController?.abort();const controller=new AbortController();roadController=controller;
  const request=++roadRequest,b=map.getBounds();
  const bbox=[Math.max(-180,b.getWest()),Math.max(-85,b.getSouth()),Math.min(180,b.getEast()),Math.min(85,b.getNorth())].join(',');
  const params=new URLSearchParams({bbox});
  if(roadRouteSelection)params.set('route',roadRouteSelection);
  try{
    const data=await fetch(`/api/roads?${params}`,{signal:controller.signal}).then(r=>{if(!r.ok)throw new Error('高速公路视窗查询失败');return r.json();});
    if(request!==roadRequest||!visibility.road)return;
    map.getSource('road')?.setData(data);
    if(data.truncated)document.getElementById('map-status').title='高速公路视窗数据已达上限，请放大查看';
  }catch(error){if(error.name!=='AbortError')report('mapError',String(error));}
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
  const inactive=['in',['coalesce',['get','construction_status'],['case',['==',['get','construction'],true],'construction','operating']],['literal',['construction','planned','disused']]];
  for(const id of ['rail','rail-stripes','rail-construction'])if(map.getLayer(id)){
    const construction=id==='rail-construction'?inactive:['!',inactive];
    map.setFilter(id,selected?['all',construction,selected]:construction);
  }
}
async function updateRailViewport(){
  if(!config.railViewport||!map.getSource('rail')||graphicsPaused||document.hidden)return;
  railController?.abort();
  const controller=new AbortController();railController=controller;
  const request=++railRequest,b=map.getBounds();
  const bbox=[Math.max(-180,b.getWest()),Math.max(-85,b.getSouth()),Math.min(180,b.getEast()),Math.min(85,b.getNorth())].join(',');
  try{
    for(const kind of ['rail','railPoints','railPlatforms','railStationAreas']){
      if(!railSourceVisible(kind))continue;
      const params=new URLSearchParams({kind,bbox,zoom:String(map.getZoom())});
      if(kind==='rail'&&!railExclude){
        if((railSections||[]).length)params.set('sections',JSON.stringify(railSections));
        if((railWays||[]).length)params.set('ways',JSON.stringify(railWays));
        if((railGroups||[]).length)params.set('groups',JSON.stringify(railGroups));
      }
      const data=await fetch(`/api/rail?${params}`,{signal:controller.signal}).then(r=>{if(!r.ok)throw new Error('国铁视窗查询失败');return r.json();});
      if(request!==railRequest||!railSourceVisible(kind))return;
      if(data.busy){railTimer=setTimeout(updateRailViewport,350);return;}
      map.getSource(kind)?.setData(data);populatedRailSources.add(kind);
      if(data.truncated)document.getElementById('camera-status').textContent='已限制地图细节量，请放大查看';
      if(kind==='rail')document.getElementById('map-status').title=data.truncated?'已达到视窗数据预算，放大查看完整股道':'国铁按当前地图范围加载';
    }
  }catch(error){if(error.name!=='AbortError')report('mapError',String(error));}
}
function addLayer(layer) { if (!map.getLayer(layer.id)) map.addLayer(layer); }
function applyRailStyles(){
  const styles=config.railStyles||{};
  const entries=Object.entries(styles).filter(([type,style])=>type!=='_zoom_width_curve'&&style&&typeof style==='object');
  const curve=Array.isArray(styles._zoom_width_curve)&&styles._zoom_width_curve.length>=2
    ?styles._zoom_width_curve
    :[{zoom:3,scale:.8},{zoom:5,scale:.9},{zoom:8,scale:1.05},{zoom:12,scale:1.25},{zoom:16,scale:1.5},{zoom:19,scale:1.8}];
  const colors=['match',['get','track_type']],widths=['match',['get','track_type']];
  for(const [type,style] of entries){
    colors.push(type,style.color);widths.push(type,Number(style.width));
  }
  colors.push('#667887');widths.push(2);
  const baseWidth=entries.length?widths:2;
  const widthCurve=['interpolate',['linear'],['zoom']];
  for(const point of curve)widthCurve.push(Number(point.zoom),['*',baseWidth,Number(point.scale)]);
  for(const id of ['rail','rail-stripes','rail-construction'])if(map.getLayer(id)){
    map.setPaintProperty(id,'line-color',id==='rail-stripes'?'#ffffff':entries.length?colors:'#667887');
    map.setPaintProperty(id,'line-width',widthCurve);
  }
  if(map.getLayer('rail-construction'))map.setPaintProperty('rail-construction','line-opacity',.65);
  if(map.getLayer('rail-stripes')){
    const solid=entries.filter(([,style])=>style.pattern==='solid').map(([type])=>type);
    map.setPaintProperty('rail-stripes','line-opacity',['case',['in',['get','track_type'],['literal',solid]],0,1]);
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
function applyRailPointStyles(){
  const styles=config.railPointStyles||{station:{size:4,shape:'ring'},control:{size:3,shape:'solid'},labels:{show_station_names:true,show_line_names:true,station_font_size:12,line_font_size:11}};
  for(const [id,kind,color] of [['rail-points','station','#466979'],['rail-detail-points','control','#a36d3c']]){
    if(!map.getLayer(id))continue;
    const style=styles[kind]||{};
    map.setPaintProperty(id,'circle-radius',Number(style.size)||3);
    map.setPaintProperty(id,'circle-color',style.shape==='solid'?color:'#ffffff');
    map.setPaintProperty(id,'circle-stroke-color',color);
    map.setPaintProperty(id,'circle-stroke-width',style.shape==='solid'?1:2);
  }
  const labels=styles.labels||{};
  if(map.getLayer('rail-station-labels')){
    map.setLayoutProperty('rail-station-labels','visibility',labels.show_station_names===false?'none':(visibility.railStations?'visible':'none'));
    map.setLayoutProperty('rail-station-labels','text-size',Number(labels.station_font_size)||12);
  }
  if(map.getLayer('rail-line-labels')){
    map.setLayoutProperty('rail-line-labels','visibility',labels.show_line_names===false?'none':(visibility.rail?'visible':'none'));
    map.setLayoutProperty('rail-line-labels','text-size',Number(labels.line_font_size)||11);
  }
}
function installLayers() {
  const sources = {...config.sources, vehicles:empty, selection:empty};
  staticSourceState.clear();populatedRailSources.clear();
  for (const [id, data] of Object.entries(sources)) addSource(id, ['metro','stations','areas','construction','road','imported'].includes(id)?empty:data);
  addLayer({id:'rail',type:'line',source:'rail',layout:{'line-cap':'round','line-join':'round'},paint:{'line-color':'#667887','line-width':['interpolate',['linear'],['zoom'],4,.4,8,.9,12,2,16,3],'line-opacity':.8}});
  map.setFilter('rail',['!=',['get','construction'],true]);
  addLayer({id:'rail-stripes',type:'line',source:'rail',minzoom:13,layout:{'line-cap':'round','line-join':'round'},filter:['!=',['get','construction'],true],paint:{'line-color':'#ffffff','line-width':2,'line-dasharray':[2,2]}});
  addLayer({id:'rail-construction',type:'line',source:'rail',layout:{'line-cap':'round','line-join':'round'},filter:['==',['get','construction'],true],paint:{'line-color':'#475569','line-width':['interpolate',['linear'],['zoom'],4,.5,8,1,12,2.5,16,3.5],'line-dasharray':[2,1.5]}});
  addLayer({id:'rail-points',type:'circle',source:'railPoints',minzoom:10,paint:{'circle-radius':['case',['==',['get','kind'],'switch'],2,4],'circle-color':'#ffffff','circle-stroke-color':'#466979','circle-stroke-width':1.5}});
  addLayer({id:'rail-detail-points',type:'circle',source:'railPoints',minzoom:15,filter:['!', ['in',['get','kind'],['literal',['station','halt']]]],paint:{'circle-radius':2,'circle-color':'#ffffff','circle-stroke-color':'#466979','circle-stroke-width':1}});
  addLayer({id:'rail-station-labels',type:'symbol',source:'railPoints',minzoom:7,filter:['in',['get','kind'],['literal',['station','halt','signal_box','junction','crossing']]],layout:{'text-field':['coalesce',['get','display_name'],['get','name']],'text-font':vectorAvailable?['Noto Sans Regular']:['Open Sans Regular'],'text-size':12,'text-offset':[0,1.2],'text-anchor':'top','text-optional':true},paint:{'text-color':'#263f4d','text-halo-color':'#ffffff','text-halo-width':1.8}});
  addLayer({id:'rail-line-labels',type:'symbol',source:'rail',minzoom:5,layout:{'symbol-placement':'line','symbol-spacing':320,'text-field':['coalesce',['get','line_display_name'],['get','line_name'],['get','name']],'text-font':vectorAvailable?['Noto Sans Bold']:['Open Sans Bold'],'text-size':11,'text-optional':true},paint:{'text-color':'#425d6b','text-halo-color':'#ffffff','text-halo-width':2}});
  addLayer({id:'rail-platform-fill',type:'fill',source:'railPlatforms',filter:['==',['geometry-type'],'Polygon'],minzoom:12,paint:{'fill-color':'#466979','fill-opacity':.22}});
  addLayer({id:'rail-platform-outline',type:'line',source:'railPlatforms',minzoom:12,paint:{'line-color':'#466979','line-width':1.5}});
  addLayer({id:'rail-station-fill',type:'fill',source:'railStationAreas',minzoom:11,paint:{'fill-color':'#688191','fill-opacity':.2}});
  addLayer({id:'rail-station-outline',type:'line',source:'railStationAreas',minzoom:11,paint:{'line-color':'#486979','line-width':1.5}});
  addLayer({id:'rail-signal-box-fill',type:'fill',source:'railSignalBoxes',filter:['==',['geometry-type'],'Polygon'],paint:{'fill-color':'#b97435','fill-opacity':.16}});
  addLayer({id:'rail-signal-box-outline',type:'line',source:'railSignalBoxes',filter:['==',['geometry-type'],'Polygon'],paint:{'line-color':'#a45f27','line-width':2}});
  addLayer({id:'rail-signal-box-symbol',type:'symbol',source:'railSignalBoxes',filter:['==',['geometry-type'],'Point'],layout:{'text-field':['concat','▭ ',['get','name']],'text-font':vectorAvailable?['Noto Sans Bold']:['Open Sans Bold'],'text-size':12,'text-allow-overlap':true},paint:{'text-color':'#8b4b1f','text-halo-color':'#ffffff','text-halo-width':2}});
  addLayer({id:'rail-vehicles',type:'circle',source:'railVehicles',paint:{'circle-radius':['/', ['coalesce',['get','display_size'],14],2],'circle-color':['coalesce',['get','display_color'],'#486e9a'],'circle-opacity':['case',['in',['get','display_style'],['literal',['ring','train']]],0,1],'circle-stroke-color':['case',['==',['get','display_style'],'ring'],['coalesce',['get','display_color'],'#486e9a'],'#ffffff'],'circle-stroke-width':2}});
  addLayer({id:'rail-vehicle-symbols',type:'symbol',source:'railVehicles',filter:['==',['get','display_style'],'train'],layout:{'text-field':'车','text-font':vectorAvailable?['Noto Sans Regular']:['Open Sans Regular'],'text-size':['coalesce',['get','display_size'],14],'text-allow-overlap':true},paint:{'text-color':['coalesce',['get','display_color'],'#486e9a'],'text-halo-color':'#ffffff','text-halo-width':2}});
  addLayer({id:'rail-vehicle-labels',type:'symbol',source:'railVehicles',layout:{'text-field':['get','trip_id'],'text-font':vectorAvailable?['Noto Sans Regular']:['Open Sans Regular'],'text-size':12,'text-offset':[0,-1.4],'text-allow-overlap':true},paint:{'text-color':'#20313d','text-halo-color':'#ffffff','text-halo-width':2}});
  if(!map.getSource('railPlan'))map.addSource('railPlan',{type:'geojson',data:config.sources.railPlan||empty});
  addLayer({id:'rail-plan-path',type:'line',source:'railPlan',filter:['==',['geometry-type'],'LineString'],paint:{'line-color':'#466979','line-width':3}});
  addLayer({id:'rail-plan-stations',type:'circle',source:'railPlan',filter:['==',['geometry-type'],'Point'],paint:{'circle-radius':4,'circle-color':'#ffffff','circle-stroke-color':'#466979','circle-stroke-width':2}});
  addLayer({id:'rail-plan-labels',type:'symbol',source:'railPlan',filter:['==',['geometry-type'],'Point'],layout:{'text-field':['get','name'],'text-font':vectorAvailable?['Noto Sans Regular']:['Open Sans Regular'],'text-size':12,'text-offset':[0,1.2]},paint:{'text-color':'#20313d','text-halo-color':'#ffffff','text-halo-width':2}});
  addLayer({id:'road',type:'line',source:'road',paint:{'line-color':['match',['get','road_class'],'national','#176c9a','provincial','#bb7538','#8898a4'],'line-width':['interpolate',['linear'],['zoom'],4,1.2,9,2.5,14,4],'line-opacity':.9}});
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
  applyRailPointStyles();
  scheduleRailViewport();
  scheduleRoadViewport();
  updateStaticSources();
}
const groups = {metro:['metro','line-labels'],stations:['stations','station-labels','areas-fill','areas-outline'],construction:['construction'],rail:['rail','rail-stripes','rail-line-labels'],railConstruction:['rail-construction'],railStations:['rail-points','rail-detail-points','rail-station-labels','rail-platform-fill','rail-platform-outline','rail-station-fill','rail-station-outline','rail-signal-box-fill','rail-signal-box-outline','rail-signal-box-symbol'],railControlPoints:[],railVehicles:['rail-vehicles','rail-vehicle-symbols','rail-vehicle-labels'],railPlan:['rail-plan-path','rail-plan-stations','rail-plan-labels'],road:['road'],imported:['imported-fill','imported-line','imported-point'],vehicles:['vehicles-halo','vehicles','vehicles-symbol','vehicle-label']};
function sharedRailStationFilter(){
  if(!map.getLayer('rail-points'))return;
  const nodes=visibility.railPlan?(config.sources.railPlan?.features||[]).filter(f=>f.geometry.type==='Point').map(f=>f.properties.osm_node_id):[];
  const allowed=['!', ['in',['get','osm_node_id'],['literal',railPointExclusions||[]]]];
  const onSelectedLine=railLineIds===null?['==',['literal',1],visibility.rail?1:0]:railLineIds.length?['any',...railLineIds.map(id=>['in',id,['coalesce',['get','line_ids'],['literal',[]]]])]:['==',['literal',1],0];
  const directlySelected=railPointIncludes===null?['==',['literal',1],1]:railPointIncludes.length?['in',['get','osm_node_id'],['literal',railPointIncludes]]:['==',['literal',1],0];
  const directlySelectedArea=railPointIncludes===null?['==',['literal',1],1]:railPointIncludes.length?['any',...railPointIncludes.map(id=>['in',id,['coalesce',['get','associated_station_ids'],['literal',[]]]])]:['==',['literal',1],0];
  const directlySelectedControl=railControlPointIncludes===null?['==',['literal',1],1]:railControlPointIncludes.length?['in',['get','osm_node_id'],['literal',railControlPointIncludes]]:['==',['literal',1],0];
  const stationAllowed=['any',onSelectedLine,directlySelected];
  map.setFilter('rail-points',['all',['in',['get','kind'],['literal',['station','halt']]],['!', ['in',['get','osm_node_id'],['literal',nodes]]],allowed,stationAllowed]);
  if(map.getLayer('rail-detail-points'))map.setFilter('rail-detail-points',['all',['!', ['in',['get','kind'],['literal',['station','halt']]]],allowed,directlySelectedControl]);
  if(map.getLayer('rail-station-labels'))map.setFilter('rail-station-labels',['all',['in',['get','kind'],['literal',['station','halt','signal_box','junction','crossing']]],allowed,['any',stationAllowed,directlySelectedControl]]);
  const areasAllowed=(railPointExclusions||[]).length?['!', ['any',...(railPointExclusions||[]).map(id=>['in',id,['coalesce',['get','associated_station_ids'],['literal',[]]]])]]:['==',['literal',1],1];
  for(const id of ['rail-platform-fill','rail-platform-outline','rail-station-fill','rail-station-outline'])if(map.getLayer(id)){
    const geometry=id.endsWith('-fill')?['==',['geometry-type'],'Polygon']:['==',['literal',1],1];
    map.setFilter(id,['all',geometry,areasAllowed,['any',onSelectedLine,directlySelectedArea]]);
  }
}
function refreshSelection(){
  if(!map.getSource('selection'))return;
  const props=selectedFeature?.properties||{};
  let ids=props.route_relation_ids||[];
  if(typeof ids==='string'){try{ids=JSON.parse(ids);}catch{ids=[];}}
  let stationIds=props.station_ids||[props.infrastructure_id||props.station_id];
  if(typeof stationIds==='string'){try{stationIds=JSON.parse(stationIds);}catch{stationIds=[stationIds];}}
  if(!Array.isArray(stationIds))stationIds=[];
  const group=Object.entries(groups).find(([,layers])=>layers.includes(selectedLayer))?.[0];
  const allowed=selectedFeature&&(!group||visibility[group])&&
    (!props.route_relation_id||visibleIds.includes(props.route_relation_id))&&
    (!['stations','areas-fill','areas-outline'].includes(selectedLayer)||stationIds.some(id=>visibleStationIds.includes(id)||visibleStationIds.some(value=>String(value).startsWith(String(id)+'@'))))&&
    (selectedLayer!=='construction'||constructionIds.includes(props.osm_way_id));
  const visibleSelection=selectedFeatures.filter(feature=>{
    const group=Object.entries(groups).find(([,layers])=>layers.includes(feature.__layer))?.[0];
    return !group||visibility[group];
  }).map(({__layer,...feature})=>feature);
  map.getSource('selection').setData(visibleSelection.length?{type:'FeatureCollection',features:visibleSelection}:(allowed?{type:'FeatureCollection',features:[selectedFeature]}:empty));
}
function applyVisibility() { for (const [key, ids] of Object.entries(groups)) for (const id of ids) if (map.getLayer(id)) map.setLayoutProperty(id,'visibility',visibility[key]?'visible':'none');sharedRailStationFilter();applyVehicleAppearance();applyRailPointStyles();refreshSelection(); }
function applyLineFilter() {
  const filter = ['in',['get','route_relation_id'],['literal',visibleIds]];
  for (const id of ['metro','line-labels']) if (map.getLayer(id)) map.setFilter(id,filter);
  // Station sources are already bounded to the selected stable station IDs.
  const stationFilter = ['==',['literal',1],1];
  for (const id of ['stations','station-labels','areas-fill','areas-outline']) if (map.getLayer(id)) map.setFilter(id,stationFilter);
  // Operating visibility is controlled by line/train groups, independently of physical layers.
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
function fitFocusedBounds(duration=0) {
  if(!focusedBounds)return;
  const canvas=map.getCanvas();
  const padding=Math.max(12,Math.min(48,canvas.clientHeight*.12,canvas.clientWidth*.08));
  map.fitBounds(focusedBounds,{padding,duration,maxZoom:14,pitch:0,bearing:0});
}
function focusDemo() {
  focusedBounds=null;
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
  document.getElementById('base-status').textContent={standard:'标准地图',satellite:'卫星影像 · 10 m',admin:'行政区划'}[type];
  report('baseChanged',type,vectorAvailable);
}
function selectionKey(feature){
  const p=feature.properties||{};
  return [feature.__layer||feature.layer?.id,p.infrastructure_id,p.catalog_group_id,p.network_edge_id,p.osm_node_id,p.osm_way_id,p.route_relation_id,p.station_id,p.corridor_id,p.trip_id].join('|');
}
function normalizedFeature(feature){return {type:'Feature',properties:{...feature.properties},geometry:feature.geometry,__layer:feature.layer?.id||feature.__layer};}
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
  config=await (await fetch('/config.json')).json();visibleIds=config.visibleIds;constructionIds=config.constructionIds;
  try {
    const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),8000);
    const response=await fetch('https://tiles.openfreemap.org/styles/liberty',{signal:controller.signal});clearTimeout(timer);
    if(!response.ok)throw new Error('Vector style request failed');
    standardStyle=styleWorkbench(await response.json());vectorAvailable=true;
  } catch(error) { vectorAvailable=false; }
  maplibregl.setWorkerCount(2);
  map=new maplibregl.Map({container:'map',center:[105,35],zoom:4,style:standardStyle||rasterStyle('standard'),attributionControl:true,renderWorldCopies:false,
    pixelRatio:1,maxCanvasSize:[2560,1440],maxTileCacheSize:96,antialias:false,fadeDuration:0});
  map.on('webglcontextlost',()=>{
    graphicsPaused=true;railController?.abort();clearTimeout(railTimer);++railRequest;roadController?.abort();clearTimeout(roadTimer);++roadRequest;
    document.getElementById('loading').style.display='none';
    document.getElementById('camera-status').textContent='图形资源中断，地图已暂停；可保存计划后重启';
    report('mapError','图形上下文丢失，已暂停地图数据更新，运行计划仍可保存。');
  });
  map.on('webglcontextrestored',()=>{graphicsPaused=false;staticSourceState.clear();updateStaticSources();scheduleRailViewport();scheduleRoadViewport();});
  document.addEventListener('visibilitychange',()=>{scheduleRailViewport();scheduleRoadViewport();updateStaticSources();});
  map.on('moveend',updateStaticSources);
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
  const selectableLayers=()=>['rail-vehicle-symbols','rail-vehicles','rail-plan-stations','rail-plan-path','vehicles-symbol','vehicles','stations','areas-fill','metro','construction','rail-points','rail-detail-points','rail-platform-fill','rail-platform-outline','rail-station-fill','rail-station-outline','rail-signal-box-fill','rail-signal-box-outline','rail-signal-box-symbol','rail-construction','rail','road','imported-fill','imported-line','imported-point'].filter(id=>map.getLayer(id));
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
      const layerGroups={box_switch:['rail-detail-points'],box_line:['rail','rail-stripes','rail-construction'],box_station:['rail-points','rail-detail-points','stations']};
      const layers=(layerGroups[selectionMode]||selectableLayers()).filter(id=>map.getLayer(id));
      const features=map.queryRenderedFeatures([[Math.min(start.x,end.x),Math.min(start.y,end.y)],[Math.max(start.x,end.x),Math.max(start.y,end.y)]],{layers});
      const values=[],seen=new Set();
      for(const feature of features){
        if(selectionMode==='box_switch'&&feature.properties?.kind!=='switch')continue;
        if(selectionMode==='box_station'&&feature.layer?.id!=='stations'&&!['station','halt','signal_box','junction','crossing'].includes(feature.properties?.kind))continue;
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
      const ids=['rail-vehicle-symbols','rail-vehicles','vehicles-symbol','vehicles','stations','areas-fill','metro','construction','rail-points','rail-detail-points','rail'].filter(id=>map.getLayer(id));
      map.getCanvas().style.cursor=map.queryRenderedFeatures(hoverPoint,{layers:ids}).length?'pointer':'grab';
    },50);
  });
  const locationPanel=document.getElementById('location-panel');
  const citySelect=document.getElementById('city-view'), lineSelect=document.getElementById('line-view');
  for(const [index,city] of config.cities.entries()){const option=new Option(city.name,String(index));citySelect.add(option);}
  function populateLocationLines(city=''){lineSelect.replaceChildren(new Option(city?city+' · 选择线路':'选择城市与线路',''));for(const [index,line] of config.lineViews.entries())if(!city||line.city===city)lineSelect.add(new Option(line.name,String(index)));}
  populateLocationLines();
  citySelect.onchange=()=>{if(citySelect.value===''){populateLocationLines();return;}const city=config.cities[Number(citySelect.value)];populateLocationLines(city.name);map.flyTo({center:city.center,zoom:11});document.getElementById('scene-title').textContent=city.name+' · 轨道交通';};
  lineSelect.onchange=()=>{if(lineSelect.value==='')return;const line=config.lineViews[Number(lineSelect.value)], b=line.bounds;map.fitBounds([[b[0],b[1]],[b[2],b[3]]],{padding:80,maxZoom:13});document.getElementById('scene-title').textContent=line.name;locationPanel.hidden=true;};
  document.getElementById('coordinate-go').onclick=()=>{const values=document.getElementById('coordinate-view').value.split(/[,，\s]+/).filter(Boolean).map(Number);if(values.length!==2||!values.every(Number.isFinite)||Math.abs(values[0])>180||Math.abs(values[1])>85){document.getElementById('location-message').textContent='请输入合法经纬度，如 121.47, 31.23';return;}map.flyTo({center:values,zoom:14});locationPanel.hidden=true;};
  document.getElementById('fit-visible').onclick=()=>{const bounds=new maplibregl.LngLatBounds();for(const id of visibleIds){const b=config.routeBounds[id];if(b){bounds.extend([b[0],b[1]]);bounds.extend([b[2],b[3]]);}}if(!bounds.isEmpty())map.fitBounds(bounds,{padding:80,maxZoom:13});};
  document.getElementById('follow-train').onclick=()=>{followTrain=!followTrain;document.getElementById('follow-train').textContent=followTrain?'停止跟随':'跟随首列车';};
  map.on('dragstart',()=>{followTrain=false;document.getElementById('follow-train').textContent='跟随首列车';});
  map.on('moveend',()=>{if(!trainFollowMoving)scheduleRailViewport();scheduleRoadViewport();});
  document.getElementById('tilt').onclick=()=>map.easeTo({pitch:map.getPitch()>20?0:45});
  document.getElementById('focus-china').onclick=()=>{map.fitBounds([[73,18],[135,54]],{padding:80,duration:700});document.getElementById('scene-title').textContent='中国 · 城市轨道交通';};
  document.getElementById('zoom-in').onclick=()=>map.zoomIn();document.getElementById('zoom-out').onclick=()=>map.zoomOut();document.getElementById('north').onclick=()=>map.easeTo({bearing:0,pitch:0});
  window.railscope={
    showLocationPanel(){locationPanel.hidden=!locationPanel.hidden;if(!locationPanel.hidden)document.getElementById('coordinate-view').focus();},
    setRunSystem(system){
      const rail=system==='rail';
      document.getElementById('legend-line-label').textContent=rail?'单向运行通道':'运营地铁线路';
      document.getElementById('legend-line-swatch').style.backgroundColor=rail?'#466979':'#c82732';
      document.getElementById('legend-station-label').textContent=rail?'经停控制点':'地铁站';
      document.getElementById('legend-vehicle-label').textContent=rail?'国铁列车':'地铁列车';
    },
    setRailWays(ids){railWays=ids;railSections=null;railGroups=null;railExclude=false;applyRailWays();scheduleRailViewport();},
    reloadRailViewport(){scheduleRailViewport();},
    enableRoadViewport(){config.roadViewport=true;config.sources.road=empty;staticSourceState.delete('road');map.getSource('road')?.setData(empty);scheduleRoadViewport();},
    setRoadRouteSelection(key){roadRouteSelection=key||null;scheduleRoadViewport();},
    setRailSelection(sectionIds,wayIds,groupIds=null){railSections=sectionIds;railWays=wayIds;railGroups=groupIds;railExclude=false;applyRailWays();scheduleRailViewport();},
    setRailExclusions(sectionIds,wayIds,groupIds=null){railSections=sectionIds;railWays=wayIds;railGroups=groupIds;railExclude=true;applyRailWays();scheduleRailViewport();},
    setRailStyles(value){config.railStyles=value;applyRailStyles();},
    setRailPointStyles(value){config.railPointStyles=value;applyRailPointStyles();},
    setRailSignalBoxes(value){config.sources.railSignalBoxes=value||empty;map.getSource('railSignalBoxes')?.setData(value||empty);},
    setMetroStyles(value){config.metroStyles=value;applyMetroStyles();},
    setSelectionMode(value){selectionMode=['box','box_switch','box_line','box_station'].includes(value)?value:'click';if(selectionMode.startsWith('box'))map.dragPan.disable();else map.dragPan.enable();map.getCanvas().style.cursor=selectionMode.startsWith('box')?'crosshair':'grab';document.getElementById('selection-box').dataset.mode=selectionMode;},
    clearSelection(){selectedFeatures=[];selectedFeature=null;selectedLayer=null;refreshSelection();publishSelection();},
    setRailPlan(data){config.sources.railPlan=data;map.getSource('railPlan')?.setData(data);visibility.railPlan=true;applyVisibility();},
    setRailOperatingVehicles(data){const signature=JSON.stringify(data);if(signature===lastRailVehicleSignature)return;lastRailVehicleSignature=signature;config.sources.railVehicles=data;if(!graphicsPaused)map.getSource('railVehicles')?.setData(visibility.railVehicles?data:empty);},
    setRailVehicleAppearance(value){if(map.getLayer('rail-vehicles')){const size=Math.max(8,Math.min(40,Number(value.size)||14));map.setPaintProperty('rail-vehicles','circle-radius',['/', ['coalesce',['get','display_size'],size],2]);}},
    captureMap(){
      map.once('render',()=>{
        try{
          const source=map.getCanvas(),canvas=document.createElement('canvas');canvas.width=source.width;canvas.height=source.height;
          const ctx=canvas.getContext('2d');ctx.drawImage(source,0,0);
          const credit=document.querySelector('.maplibregl-ctrl-attrib-inner')?.textContent||'© OpenStreetMap contributors';
          const scale=window.devicePixelRatio||1;ctx.font=`${12*scale}px sans-serif`;ctx.fillStyle='rgba(255,255,255,.9)';ctx.fillRect(0,canvas.height-24*scale,canvas.width,24*scale);ctx.fillStyle='#263b47';ctx.fillText(credit,8*scale,canvas.height-8*scale);
          report('imageCaptured',canvas.toDataURL('image/png'));
        }catch(error){report('imageCaptured','error');}
      });map.triggerRepaint();
    },
    setVisibility(key,on){visibility[key]=on;applyVisibility();updateStaticSources();if(key==='vehicles')map.getSource('vehicles')?.setData(on?operatingVehicles:empty);if(key==='railVehicles')map.getSource('railVehicles')?.setData(on?config.sources.railVehicles:empty);if(['rail','railConstruction','railStations','railControlPoints'].includes(key))scheduleRailViewport();if(key==='road')scheduleRoadViewport();},
    setOverlay,
    setVehicleAppearance(value){const size=Number(value.size);if(!Number.isFinite(size)||!['glow','ring','train'].includes(value.style))return;vehicleAppearance={size:Math.max(8,Math.min(40,size)),style:value.style};applyVehicleAppearance();},
    setLines(ids){visibleIds=ids;applyLineFilter();updateStaticSources();},
    setMetroStations(ids){visibleStationIds=ids;applyLineFilter();staticSourceState.delete('stations');staticSourceState.delete('areas');updateStaticSources();},
    setRailPointExclusions(ids){railPointExclusions=ids;sharedRailStationFilter();},
    setRailPointSelection(ids){railPointIncludes=ids;sharedRailStationFilter();},setBase,
    setRailControlPointSelection(ids){railControlPointIncludes=ids;sharedRailStationFilter();},
    setRailLineSelection(ids){railLineIds=ids;sharedRailStationFilter();},
    setConstruction(ids){constructionIds=ids;applyConstructionFilter();},
    setBaseDetail(key,on){baseDetails[key]=on;applyBaseDetails();},focusDemo,
    focusChina(){document.getElementById('focus-china').click();},
    focus(lon,lat,zoom=13,title=''){focusedBounds=null;map.flyTo({center:[lon,lat],zoom,duration:700});if(title)document.getElementById('scene-title').textContent=title;},
    fit(bounds,title=''){focusedBounds=bounds;map.resize();fitFocusedBounds(600);if(title)document.getElementById('scene-title').textContent=title;},
    setOperatingVehicles(data,clock,playing){
      operatingMode=true;operatingVehicles=data;operatingClock=clock;running=playing;
      travelled=data.features[0]?.properties.distance_m||0;
      if(!graphicsPaused)map.getSource('vehicles')?.setData(visibility.vehicles?data:empty);
      if(['vehicles','vehicles-symbol'].includes(selectedLayer)&&selectedFeature){selectedFeature=data.features.find(f=>f.properties.vehicle_id===selectedFeature.properties.vehicle_id)||null;refreshSelection();}
      if(followTrain){const vehicle=data.features.find(f=>visibleIds.includes(f.properties.route_relation_id));if(vehicle){trainFollowMoving=true;clearTimeout(trainFollowTimer);map.easeTo({center:vehicle.geometry.coordinates,zoom:Math.max(map.getZoom(),13),duration:240,easing:t=>t});trainFollowTimer=setTimeout(()=>{trainFollowMoving=false;scheduleRailViewport();},500);}}
    },
    imported(data){config.sources.imported=data;staticSourceState.delete('imported');updateStaticSources();},
    testState(){
      const baseSource=vectorAvailable?'openmaptiles':'base';
      const allowsLine1=id=>map.getLayer(id)&&(map.getFilter(id)||[]).some(clause=>Array.isArray(clause)&&clause[0]==='in'&&clause[1]===199200);
      const railCoordinates=(config.sources.railPlan?.features||[]).filter(f=>f.geometry.type==='LineString').flatMap(f=>f.geometry.coordinates);
      return {railPlanInView:railCoordinates.length>0&&railCoordinates.every(p=>map.getBounds().contains(p)),railVehiclesCount:config.sources.railVehicles?.features.length||0,legendSystem:document.getElementById('legend-vehicle-label').textContent,ready:sourceReadySent,sources:Object.keys(map.getStyle().sources),
        metroLoaded:!!map.getSource('metro')&&map.isSourceLoaded('metro'),
        baseLoaded:!!map.getSource(baseSource)&&map.isSourceLoaded(baseSource),
        currentBase,visibleLines:visibleIds.length,visibleConstruction:constructionIds.length,selectionMode,selectedCount:selectedFeatures.length,
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
