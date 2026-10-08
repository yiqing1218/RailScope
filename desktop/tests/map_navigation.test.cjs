const test=require('node:test'),assert=require('node:assert/strict');
require('../assets/map-navigation.js');

test('native sidebar glass follows panel geometry and disappears with its owner',()=>{
  const fs=require('node:fs'),vm=require('node:vm');
  const source=fs.readFileSync(require('node:path').join(__dirname,'../assets/map.js'),'utf8');
  const panels=Object.fromEntries(['left','right','rail'].map(key=>['workspace-glass-'+key,{style:{},hidden:true}]));
  const context=vm.createContext({uiInsets:{},fitFocusedBounds(){},document:{documentElement:{style:{setProperty(){}}},getElementById:id=>panels[id]}});
  vm.runInContext(source.slice(source.indexOf('function setUiInsets('),source.indexOf('function setFonts(')),context);
  context.setUiInsets({left:420,right:12,panels:{left:{x:78,y:12,width:330,height:800,visible:true}}});
  assert.equal(panels['workspace-glass-left'].hidden,false);
  assert.equal(panels['workspace-glass-left'].style.width,'330px');
  assert.equal(panels['workspace-glass-left'].style.left,'78px');
  assert.equal(panels['workspace-glass-right'].hidden,true);
  context.setUiInsets({left:78,right:12,panels:{left:{visible:false}}});
  assert.equal(panels['workspace-glass-left'].hidden,true);
});

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

test('airport outline and POI share selection identity without colliding with campuses',()=>{
  const fs=require('node:fs'),vm=require('node:vm');
  const source=fs.readFileSync(require('node:path').join(__dirname,'../assets/map.js'),'utf8');
  const context=vm.createContext({});
  vm.runInContext(source.slice(source.indexOf('function selectionKey('),source.indexOf('function normalizedFeature(')),context);
  const key=(id,layer)=>context.selectionKey({properties:{airport_id:id},__layer:layer});
  assert.equal(key('APT-1','airport-fill'),key('APT-1','airport-pois'));
  assert.notEqual(key('APT-1','airport-fill'),key('APT-2','airport-pois'));
  assert.notEqual(key('APT-1','airport-fill'),context.selectionKey({properties:{campus_id:'APT-1'}}));
});
