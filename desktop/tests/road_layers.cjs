const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const layers=new Map(),sources=new Map(),requests=[];
let zoom=5;
const node={style:{},dataset:{},hidden:false};
const sandbox={console,URLSearchParams,AbortController,structuredClone,
  window:{maplibregl:{}},document:{hidden:false,getElementById:()=>node,querySelectorAll:()=>[]},
  qt:{webChannelTransport:{}},QWebChannel:class{},setTimeout:()=>0,clearTimeout:()=>{},
  fetch:async(endpoint,options)=>{requests.push(JSON.parse(options.body));return {ok:true,json:async()=>({type:'FeatureCollection',features:[{id:requests.length}]})};}};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('desktop/assets/map.js','utf8'),sandbox);
sandbox.testMap={getLayer:id=>layers.get(id),addLayer:l=>layers.set(l.id,l),
  getSource:id=>sources.get(id),addSource:(id,options)=>sources.set(id,{...options,setData(data){this.data=data;}}),
  setFilter:(id,value)=>{layers.get(id).filter=value;},
  setPaintProperty:(id,key,value)=>{layers.get(id).paint??={};layers.get(id).paint[key]=value;},
  setLayoutProperty:(id,key,value)=>{layers.get(id).layout??={};layers.get(id).layout[key]=value;},
  setLayerZoomRange:(id,min,max)=>Object.assign(layers.get(id),{minzoom:min,maxzoom:max}),
  getStyle:()=>({layers:[...layers.values()]}),hasImage:()=>true,
  getZoom:()=>zoom,getBounds:()=>({getWest:()=>120,getSouth:()=>30,getEast:()=>122,getNorth:()=>32})};
vm.runInContext(`map=testMap;config={roadViewport:true,sources:Object.fromEntries(['metro','stations','areas','construction','rail','railPoints','railPlatforms','railStationAreas','railSignalBoxes','railVehicles','road','imported'].map(k=>[k,empty]))}; installLayers();`,sandbox);
const specPath='../../frontend/node_modules/@maplibre/maplibre-gl-style-spec';
try{
  const {validateStyleMin}=require(specPath);
  const style={version:8,glyphs:'https://example.test/{fontstack}/{range}.pbf',sources:Object.fromEntries([...sources.keys()].map(k=>[k,{type:'geojson',data:{type:'FeatureCollection',features:[]}}])),layers:[...layers.values()]};
  // Null is the documented setPaintProperty reset value; it is absent in a serialized style.
  for(const layer of style.layers)for(const [key,value] of Object.entries(layer.paint||{}))if(value===null)delete layer.paint[key];
  assert.deepEqual(validateStyleMin(style).map(e=>e.message),[]);
}catch(error){if(error.code!=='MODULE_NOT_FOUND')throw error;}
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
