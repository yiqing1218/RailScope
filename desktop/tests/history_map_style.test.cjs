/* Validate the production history layers with the installed MapLibre spec. */
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {validateStyleMin}=require('../../frontend/node_modules/@maplibre/maplibre-gl-style-spec');
test('history layers retain valid data-driven colors, widths and dash arrays',()=>{
 const empty={type:'FeatureCollection',features:[]};
 const style={version:8,sources:{rail:{type:'geojson',data:empty},metro:{type:'geojson',data:empty},road:{type:'geojson',data:empty},railPoints:{type:'geojson',data:empty}},layers:[
  {id:'rail',source:'rail',type:'line',paint:{'line-color':'#194786','line-width':2,'line-dasharray':[1,0]}},
  {id:'rail-stripes',source:'rail',type:'line',paint:{'line-color':'#fff','line-width':2,'line-opacity':0}},
  {id:'metro',source:'metro',type:'line',paint:{'line-color':'#a52432','line-width':3}},
  {id:'road',source:'road',type:'line',paint:{'line-color':'#227b86','line-width':2}},
  {id:'rail-points',source:'railPoints',type:'circle',paint:{'circle-color':'#fff','circle-stroke-color':'#194786'}}],glyphs:'https://example.com/{fontstack}/{range}.pbf'};
 const sources=new Map(Object.keys(style.sources).map(id=>[id,{setData(data){this.data=data;return this;}}]));
 const map={getStyle:()=>style,getLayer:id=>style.layers.find(l=>l.id===id),getSource:id=>sources.get(id),
  addSource(id,value){style.sources[id]=value;sources.set(id,{setData(data){this.data=data;return this;}});},
  addLayer(layer){style.layers.push(layer);},setFilter(id,filter){this.getLayer(id).filter=filter;},
  getPaintProperty(id,key){return this.getLayer(id)?.paint[key];},setPaintProperty(id,key,value){this.getLayer(id).paint[key]=value;},
  setLayoutProperty(id,key,value){const layer=this.getLayer(id);(layer.layout??={})[key]=value;}};
 const context=vm.createContext({window:{maplibregl:{}},mockMap:map,mockConfig:{sources:{},railStyles:{},railViewport:false},
  qt:{webChannelTransport:{}},QWebChannel:class{},structuredClone,setTimeout,clearTimeout,console});
 for(const file of ['entity-presentation.js','infrastructure-history.js','map.js'])vm.runInContext(fs.readFileSync('desktop/assets/'+file,'utf8'),context,{filename:file});
 vm.runInContext('map=mockMap;config=mockConfig;installHistoryLayers();',context);
 const errors=validateStyleMin(JSON.parse(JSON.stringify(style)));
 assert.deepEqual(errors.map(e=>e.message),[]);
 const colors=JSON.stringify(map.getLayer('rail-points').paint);
 vm.runInContext('updateHistoryPaints();updateHistoryPaints();',context);
 assert.equal(JSON.stringify(map.getLayer('rail-points').paint),colors);
 vm.runInContext("infrastructureHistory.configure('2010-01-01',[{id:'INF',mode:'rail',source_aliases:['IL'],construction_started:'2008-01-01',opened:'2015-01-01'}]);addSource('test-not-history',empty);map.getSource('rail').setData(historyData('rail',{type:'FeatureCollection',features:[{type:'Feature',properties:{line_id:'IL'},geometry:{type:'LineString',coordinates:[[120,30],[121,30]]}}]}));",context);
 assert.equal(sources.get('history-rail').data.features[0].properties.history_state,'construction');
 assert.equal(sources.get('rail').data.features.length,0);
 const writes=[];
 for(const [id,source] of sources){const set=source.setData.bind(source);source.setData=data=>{writes.push(id);return set(data);};}
 vm.runInContext("for(const id of ['rail','road']){const source=map.getSource(id),set=source.setData.bind(source);source.setData=data=>set(historyData(id,data));}map.getSource('road').setData(empty);",context);
 writes.length=0;
 vm.runInContext("applyInfrastructureHistory('2010-01-01',[{id:'INF',mode:'rail',source_aliases:['IL'],construction_started:'2008-01-01',opened:'2011-01-01'}]);",context);
 assert.ok(writes.includes('rail'));
 assert.ok(!writes.some(id=>id.includes('road')||id.includes('metro')));
});
