const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {createExpression}=require('../../frontend/node_modules/@maplibre/maplibre-gl-style-spec');
function setup(){
 const layers=new Map(),sources=new Map(),node={style:{},dataset:{}};
 const context=vm.createContext({window:{maplibregl:{}},document:{hidden:false,getElementById:()=>node,querySelectorAll:()=>[]},
  qt:{webChannelTransport:{}},QWebChannel:class{},structuredClone,URLSearchParams,AbortController,setTimeout:()=>0,clearTimeout(){}});
 const map={getLayer:id=>layers.get(id),addLayer:l=>layers.set(l.id,l),getSource:id=>sources.get(id),
  addSource:(id,data)=>sources.set(id,{...data,setData(value){this.data=value;}}),getStyle:()=>({layers:[...layers.values()]}),
  setFilter:(id,value)=>layers.get(id).filter=value,setLayoutProperty:(id,key,value)=>(layers.get(id).layout??={})[key]=value,
  setPaintProperty:(id,key,value)=>(layers.get(id).paint??={})[key]=value,getPaintProperty:(id,key)=>layers.get(id)?.paint?.[key],
  setLayerZoomRange:(id,min,max)=>Object.assign(layers.get(id),{minzoom:min,maxzoom:max}),getZoom:()=>15,hasImage:()=>true,
  getBounds:()=>({toArray:()=>[[120,30],[122,32]]})};
 context.mockMap=map;
 for(const file of ['entity-presentation.js','infrastructure-history.js','map.js'])vm.runInContext(fs.readFileSync('desktop/assets/'+file,'utf8'),context);
 vm.runInContext("map=mockMap;config={sources:{},railViewport:false,roadViewport:false,railPointStyles:{labels:{show_line_names:true}}};installLayers();installHistoryLayers();",context);
 return {context,map,layers};
}
function allows(filter,properties,zoom=15){
 if(!filter)return true;
 const compiled=createExpression(JSON.parse(JSON.stringify(filter)),'filter');
 assert.equal(compiled.result,'success',JSON.stringify(compiled.value));
 return compiled.value.evaluate({zoom},{type:2,properties});
}
test('base labels cannot bypass motorway, station, or university layer selection',()=>{
 const {context}=setup();
 context.base={layers:[{id:'highway-shield',source:'openmaptiles','source-layer':'transportation_name',type:'symbol',layout:{'text-field':['get','name']}},
  {id:'poi',source:'openmaptiles','source-layer':'poi',type:'symbol',layout:{'text-field':['get','name']}}]};
 vm.runInContext('styleWorkbench(base)',context);
 assert.equal(allows(context.base.layers[0].filter,{class:'motorway'}),false);
 assert.equal(allows(context.base.layers[0].filter,{class:'primary'}),true);
 for(const props of [{class:'rail'},{class:'college'},{subclass:'university'},{subclass:'services'}])assert.equal(allows(context.base.layers[1].filter,props),false);
 assert.equal(allows(context.base.layers[1].filter,{class:'hospital'}),true);
});
test('turning off base labels also hides road and administrative text',()=>{
 const {context,layers}=setup();
 for(const [id,sl] of [['street','transportation_name'],['label_city','place']])layers.set(id,{id,type:'symbol',source:'openmaptiles','source-layer':sl});
 vm.runInContext('baseDetails.labels=false;applyBaseDetails();',context);
 for(const id of ['street','label_city'])assert.equal(layers.get(id).layout.visibility,'none');
});
test('road label zoom thresholds follow the road geometry',()=>{
 const {context,layers}=setup();
 vm.runInContext('minZooms={roads:14};applyMinZooms();',context);
 for(const id of ['road','road-construction','road-labels','road-construction-labels','history-road','history-road-labels'])assert.equal(layers.get(id).minzoom,14,id);
});
test('station and control names obey their own switches and zoom thresholds',()=>{
 const {context,layers}=setup();
 vm.runInContext('visibility.railStations=true;visibility.railControlPoints=false;minZooms={railStations:10,railSwitches:15};sharedRailStationFilter();',context);
 const control={kind:'signal_box',osm_node_id:1,display_name:'线路所'};
 assert.equal(allows(layers.get('rail-station-labels').filter,control),false);
 vm.runInContext('visibility.railControlPoints=true;visibility.railStations=false;sharedRailStationFilter();',context);
 assert.equal(allows(layers.get('rail-station-labels').filter,control,14),false);
 assert.equal(allows(layers.get('rail-station-labels').filter,control,16),true);
 assert.equal(allows(layers.get('rail-station-labels').filter,{kind:'station',osm_node_id:2}),false);
});
test('unchecking a motorway immediately hides its resident geometry and name together',()=>{
 const {context,layers}=setup();
 vm.runInContext("roadVisibleRoutes=['G/G2'];applyRoadRouteFilter();",context);
 for(const id of ['road','road-labels']){
  assert.equal(allows(layers.get(id).filter,{route_keys:['G/G2'],construction:false}),true);
  assert.equal(allows(layers.get(id).filter,{route_keys:['G/G1'],construction:false}),false);
 }
});
test('historical lines and names obey construction switches and directory selection',()=>{
 const {context,layers}=setup();
 vm.runInContext("visibility.road=true;visibility.roadConstruction=false;roadVisibleRoutes=['G/G2'];visibility.rail=true;visibility.railConstruction=false;railGroups=['RL-1'];applyVisibility();",context);
 for(const id of ['history-road','history-road-labels']){
  assert.equal(allows(layers.get(id).filter,{route_keys:['G/G2'],history_state:'construction'}),false);
  assert.equal(allows(layers.get(id).filter,{route_keys:['G/G1'],history_state:'operating'}),false);
 }
 for(const id of ['history-rail','history-rail-labels'])assert.equal(allows(layers.get(id).filter,{catalog_group_id:'RL-1',history_state:'construction'}),false);
});


test('fallback base stays vector and never has baked infrastructure labels',()=>{
 const {context}=setup();
 const style=vm.runInContext("rasterStyle('standard')",context);
 const {validateStyleMin}=require('../../frontend/node_modules/@maplibre/maplibre-gl-style-spec');
 assert.deepEqual(validateStyleMin(JSON.parse(JSON.stringify(style))).map(e=>e.message),[]);
 assert.equal(style.sources.openmaptiles.type,'vector');
 assert.equal(style.layers.some(l=>l.type==='raster'),false);
 assert.equal(style.layers.filter(l=>l.type==='symbol').every(l=>l['source-layer']==='place'),true);
});

test('control symbols and their names remain visible with stations switched off',()=>{
 const {context,layers}=setup();
 vm.runInContext('visibility.railStations=false;visibility.railControlPoints=true;applyVisibility();',context);
 assert.equal(layers.get('rail-points').layout.visibility,'none');
 assert.equal(layers.get('rail-detail-points').layout.visibility,'visible');
 assert.equal(layers.get('rail-station-labels').layout.visibility,'visible');
});

test('historical metro construction names respect their own zoom threshold',()=>{
 const {context,layers}=setup();
 vm.runInContext("visibility.metro=true;visibility.construction=true;visibleIds=[10];constructionIds=[20];minZooms={metroLines:8,metroConstruction:14};applyMinZooms();",context);
 for(const id of ['history-metro','history-metro-labels']){
  assert.equal(layers.get(id).minzoom,8);
  assert.equal(allows(layers.get(id).filter,{osm_way_id:20,history_state:'construction'},13),false);
  assert.equal(allows(layers.get(id).filter,{osm_way_id:20,history_state:'construction'},14),true);
 }
});
