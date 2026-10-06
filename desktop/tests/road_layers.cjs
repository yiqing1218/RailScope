const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const layers=new Map(),sources=new Map(),requests=[];
let zoom=5;
const node={style:{},dataset:{},hidden:false};
const sandbox={console,URLSearchParams,AbortController,structuredClone,
  window:{maplibregl:{}},document:{hidden:false,getElementById:()=>node,querySelectorAll:()=>[]},
  qt:{webChannelTransport:{}},QWebChannel:class{},setTimeout:()=>0,clearTimeout:()=>{},
  fetch:async(endpoint,options)=>{requests.push(JSON.parse(options.body));return {ok:true,json:async()=>({type:'FeatureCollection',features:[{id:requests.length}]})};}};
vm.createContext(sandbox);
for(const file of ['entity-presentation.js','infrastructure-history.js','map.js'])
  vm.runInContext(fs.readFileSync('desktop/assets/'+file,'utf8'),sandbox,{filename:file});
assert.equal(vm.runInContext(`normalizedFeature({properties:{line_ids:'["RL-one"]',name:'[普通名称]'}}).properties.line_ids[0]`,sandbox),'RL-one');
assert.equal(vm.runInContext(`normalizedFeature({properties:{name:'[普通名称]'}}).properties.name`,sandbox),'[普通名称]');
// Sprite-free pedestrian polygons must have an explicit light fallback.
sandbox.baseStyle={version:8,sprite:'https://example.test/sprite',layers:[
  {id:'road_area_pattern',type:'fill','source-layer':'transportation',paint:{'fill-pattern':'pedestrian_polygon'}},
  {id:'wetland',type:'fill','source-layer':'landcover',paint:{'fill-pattern':'wetland','fill-opacity':.8}},
  {id:'other_pattern',type:'fill','source-layer':'other',paint:{'fill-pattern':'unknown'}},
  {id:'water',type:'fill','source-layer':'water',paint:{'fill-color':'blue'}}]};
vm.runInContext('styleWorkbench(baseStyle)',sandbox);
assert.equal(sandbox.baseStyle.sprite,undefined);
for(const layer of sandbox.baseStyle.layers){
  assert.equal(layer.paint['fill-pattern'],undefined);
  assert.match(layer.paint['fill-color'],/^#[a-f0-9]{6}$/);
  assert.notEqual(layer.paint['fill-color'],'#000000');
}
assert.equal(sandbox.baseStyle.layers[1].paint['fill-opacity'],.8);
sandbox.testMap={getLayer:id=>layers.get(id),addLayer:l=>layers.set(l.id,l),
  getSource:id=>sources.get(id),addSource:(id,options)=>sources.set(id,{...options,setData(data){this.data=data;}}),
  setFilter:(id,value)=>{layers.get(id).filter=value;},
  setPaintProperty:(id,key,value)=>{layers.get(id).paint??={};layers.get(id).paint[key]=value;},
  setLayoutProperty:(id,key,value)=>{layers.get(id).layout??={};layers.get(id).layout[key]=value;},
  setLayerZoomRange:(id,min,max)=>Object.assign(layers.get(id),{minzoom:min,maxzoom:max}),
  getStyle:()=>({layers:[...layers.values()]}),hasImage:()=>true,
  getZoom:()=>zoom,getBounds:()=>({getWest:()=>120,getSouth:()=>30,getEast:()=>122,getNorth:()=>32})};
vm.runInContext(`map=testMap;config={roadViewport:true,sources:Object.fromEntries(['metro','stations','areas','construction','rail','railPoints','railPlatforms','railStationAreas','railSignalBoxes','railVehicles','road','imported'].map(k=>[k,empty]))}; installLayers();`,sandbox);
vm.runInContext(`config.railStyles={'class.conventional':{color:'#283541',width:2.5,pattern:'solid'}};applyRailStyles();`,sandbox);
assert.match(JSON.stringify(layers.get('rail').paint['line-color']), /rail_style_key/);
assert.match(JSON.stringify(layers.get('rail-stripes').paint['line-opacity']), /rail_style_key/);
const specPath='../../frontend/node_modules/@maplibre/maplibre-gl-style-spec';
try{
  const {validateStyleMin}=require(specPath);
  const style={version:8,glyphs:'https://example.test/{fontstack}/{range}.pbf',sources:Object.fromEntries([...sources.keys()].map(k=>[k,{type:'geojson',data:{type:'FeatureCollection',features:[]}}])),layers:[...layers.values()]};
  // Null is the documented setPaintProperty reset value; it is absent in a serialized style.
  for(const layer of style.layers)for(const [key,value] of Object.entries(layer.paint||{}))if(value===null)delete layer.paint[key];
  assert.deepEqual(validateStyleMin(style).map(e=>e.message),[]);
}catch(error){if(error.code!=='MODULE_NOT_FOUND')throw error;}
// Evaluate the actual filters, including the shared-station case.
{
  const {createExpression}=require(specPath);
  const evaluate=(value,props)=>{
    const compiled=createExpression(JSON.parse(JSON.stringify(value)),'filter');
    assert.equal(compiled.result,'success',JSON.stringify(compiled.value));
    return compiled.value.evaluate({zoom:15},{properties:props});
  };
  vm.runInContext("visibility.rail=true;visibility.railConstruction=true;railLineIds=null;railHiddenLineIds=['RL-build'];railPointIncludes=null;railControlPointIncludes=null;sharedRailStationFilter();",sandbox);
  const exclusive={kind:'station',osm_node_id:1,line_ids:['RL-build'],operating_line_ids:[],construction_line_ids:['RL-build']};
  const shared={...exclusive,line_ids:['RL-build','RL-open'],operating_line_ids:['RL-open']};
  assert.equal(evaluate(layers.get('rail-points').filter,exclusive),false);
  assert.equal(evaluate(layers.get('rail-points').filter,shared),true);
  vm.runInContext("railHiddenLineIds=[];visibility.railConstruction=false;sharedRailStationFilter();",sandbox);
  assert.equal(evaluate(layers.get('rail-station-labels').filter,exclusive),false);
  assert.equal(evaluate(layers.get('rail-station-labels').filter,shared),true);
  vm.runInContext("visibility.railConstruction=true;sharedRailStationFilter();railGroups=['RL-build'];railSections=[];railWays=[];railExclude=true;applyRailWays();",sandbox);
  assert.equal(evaluate(layers.get('rail-line-labels').filter,{catalog_group_id:'RL-build',construction:true}),false);
  assert.equal(evaluate(layers.get('rail-line-labels').filter,{catalog_group_id:'RL-open',construction:false}),true);
  vm.runInContext("config.railPointStyles={labels:{show_line_names:false}};applyLineLabelVisibility();",sandbox);
  assert.equal(layers.get('rail-line-labels').layout.visibility,'none');
  assert.equal(vm.runInContext("selectionKey({__layer:'road-service-poi',properties:{service_id:'RSA-one',source_ref:'node/1'}})===selectionKey({__layer:'road-service-outline',properties:{service_id:'RSA-one',source_ref:'way/2'}})",sandbox),true);
  vm.runInContext("config.railPointStyles={labels:{show_line_names:true}};visibility.rail=false;visibility.railConstruction=false;railGroups=null;railSections=null;railWays=null;railExclude=false;",sandbox);
}
(async()=>{
  vm.runInContext("visibility.roadConstruction=true;roadVisibleRoutes=['G/G2/construction'];applyVisibility();",sandbox);
  await vm.runInContext('updateRoadViewport()',sandbox);
  assert.equal(requests.length,1);
  assert.equal(layers.get('road').layout.visibility,'none');
  assert.equal(layers.get('road-construction').layout.visibility,'visible');
  assert.equal(layers.get('road-construction-labels').layout.visibility,'visible');
  assert.equal(layers.get('road-labels').layout.visibility,'none');
  vm.runInContext("visibility.roadConstruction=false;visibility.roadServices=true;roadVisibleServices=['RSA-test'];scheduleRoadViewport();applyVisibility();",sandbox);
  await vm.runInContext('updateRoadViewport()',sandbox);
  assert.equal(requests.at(-1).kind,'services');
  assert.equal(sources.get('road').data.features.length,0);
  for(const id of ['road-service-poi','road-service-buildings','road-service-outline'])assert.equal(layers.get(id).layout.visibility,'visible');
  const count=requests.length;
  zoom=13;
  await vm.runInContext('updateRoadViewport()',sandbox);
  assert.equal(requests.length,count+1); // Zooming in fetches actual outlines and buildings.
  vm.runInContext('visibility.roadServices=false;scheduleRoadViewport();applyVisibility();',sandbox);
  assert.equal(sources.get('roadServices').data.features.length,0);
  console.log('Road layers, independent switches, service detail loading and style validation passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
