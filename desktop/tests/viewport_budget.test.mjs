import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source=readFileSync(new URL('../assets/map.js',import.meta.url),'utf8').replace(/^import .*;\r?\n/gm,'');
function setup(){
  const elements={'camera-status':{textContent:'当前位置'},'map-status':{title:''}};
  const context=vm.createContext({window:{maplibregl:{}},document:{hidden:false,getElementById:id=>elements[id]},
    qt:{webChannelTransport:{}},QWebChannel:function(){},AbortController,URLSearchParams,
    setTimeout:()=>1,clearTimeout:()=>{},console});
  for(const file of ['entity-presentation.js','infrastructure-history.js'])
    vm.runInContext(readFileSync(new URL('../assets/'+file,import.meta.url),'utf8'),context);
  vm.runInContext(source,context);
  context.writes=[];context.requests=[];
  context.query=async (url,params)=>{
    context.requests.push(Object.fromEntries(params));
    return {type:'FeatureCollection',features:[],truncated:false,
      budget:{usage:{features:0,bytes:0,vertices:0},limits:{features:2},reasons:[]}};
  };
  vm.runInContext(`config={railViewport:true};minZooms={};visibility.railStations=true;
    entityPresentation.register=()=>{};
    map={getZoom:()=>16,getBounds:()=>({getWest:()=>120,getSouth:()=>30,getEast:()=>122,getNorth:()=>32}),
      getSource:id=>({setData:data=>writes.push({id,data})})};queryViewport=query;`,context);
  return {context,elements};
}

test('point visibility enters the request key so turning controls on reloads the shared source',async()=>{
  const {context}=setup();
  await vm.runInContext('updateRailViewport()',context);
  const first=context.requests.find(p=>p.kind==='railPoints');
  assert.equal(first.point_stations,'true');assert.equal(first.point_controls,'false');
  vm.runInContext('visibility.railControlPoints=true',context);
  await vm.runInContext('updateRailViewport()',context);
  assert.equal(context.requests.filter(p=>p.kind==='railPoints').length,2);
  assert.equal(context.requests.at(-1).point_controls,'true');
});

test('budget status reports all sources and clears the warning after a complete reload',async()=>{
  const {context,elements}=setup();
  context.query=async (url,params)=>({type:'FeatureCollection',features:[],truncated:params.get('kind')==='railPlatforms',
    budget:{usage:{features:2,bytes:1024,vertices:12},limits:{features:2},reasons:params.get('kind')==='railPlatforms'?['bytes']:[]}});
  vm.runInContext('queryViewport=query',context);
  await vm.runInContext('updateRailViewport()',context);
  assert.match(elements['map-status'].title,/站台/);
  assert.match(elements['map-status'].title,/数据量/);
  assert.match(elements['camera-status'].textContent,/站台/);
  context.query=async()=>({type:'FeatureCollection',features:[],truncated:false,
    budget:{usage:{features:1,bytes:100,vertices:1},limits:{features:2},reasons:[]}});
  vm.runInContext('queryViewport=query;railSourceKeys.clear()',context);
  await vm.runInContext('updateRailViewport()',context);
  assert.doesNotMatch(elements['camera-status'].textContent,/上限/);
  assert.doesNotMatch(elements['map-status'].title,/达到/);
});
