const test=require('node:test'),assert=require('node:assert/strict');
require('../assets/map-navigation.js');

test('camera history restores complete views and replaces the forward branch after new navigation',()=>{
  const listeners={},buttons=new Map();
  const document={getElementById:id=>{if(!buttons.has(id))buttons.set(id,{style:{},setAttribute(){},addEventListener(){}});return buttons.get(id);},addEventListener(){}};
  let state={center:[120,30],zoom:8,bearing:10,pitch:45};
  const map={getCenter:()=>({lng:state.center[0],lat:state.center[1]}),getZoom:()=>state.zoom,getBearing:()=>state.bearing,getPitch:()=>state.pitch,
    on:(key,fn)=>(listeners[key]=fn),easeTo:value=>{state=value;listeners.moveend();},zoomIn(){},zoomOut(){}};
  let following=false;
  const navigation=new RailScopeNavigation.Navigation(map,document,{shouldRecord:()=>!following});
  state={center:[121,31],zoom:14,bearing:0,pitch:0};listeners.moveend();
  navigation.back();assert.deepEqual(state.center,[120,30]);assert.equal(state.pitch,45);
  assert.equal(buttons.get('view-forward').disabled,false);
  state={center:[122,32],zoom:10,bearing:0,pitch:0};listeners.moveend();
  assert.equal(buttons.get('view-forward').disabled,true);assert.equal(navigation.history.length,2);
  following=true;state={center:[123,33],zoom:13,bearing:0,pitch:0};listeners.moveend();
  assert.equal(navigation.history.length,2);
});
test('fit geometry handles polygon rings and multiple campus outlines without mutating features',()=>{
  const features=[{geometry:{type:'MultiPolygon',coordinates:[[[[120,30],[122,32],[120,30]]]]}},
    {geometry:{type:'Point',coordinates:[119,29]}}];
  const original=JSON.stringify(features);
  assert.deepEqual(RailScopeNavigation.geometryBounds(features),[[119,29],[122,32]]);
  assert.equal(JSON.stringify(features),original);
  assert.equal(RailScopeNavigation.geometryBounds([]),null);
});
test('campus outline and POI are one selection, and distinct campuses remain distinct',()=>{
  const fs=require('node:fs'),vm=require('node:vm');
  const source=fs.readFileSync(require('node:path').join(__dirname,'../assets/map.js'),'utf8');
  const context=vm.createContext({});
  vm.runInContext(source.slice(source.indexOf('function selectionKey('),source.indexOf('function normalizedFeature(')),context);
  const key=(id,layer)=>context.selectionKey({properties:{campus_id:id},__layer:layer});
  assert.equal(key('UNI-1','university-fill'),key('UNI-1','university-pois'));
  assert.notEqual(key('UNI-1','university-fill'),key('UNI-2','university-fill'));
});
