const test=require('node:test'),assert=require('node:assert/strict');
require('../assets/entity-presentation.js');require('../assets/infrastructure-history.js');
const {History,state}=globalThis.RailScopeHistory;
const record={id:'INF',mode:'rail',source_aliases:['IL'],construction_started:'2008-01-01',opened:'2015-01-01',closed:null};
test('history boundaries, future ray and immutable geometry',()=>{
 const h=new History(),geometry={type:'LineString',coordinates:[[120,30],[121,30]]},data={features:[{properties:{line_id:'IL',construction_status:'construction'},geometry}]};
 h.configure('2007-01-01',[record]);assert.equal(h.apply(data,'rail').features.length,0);
 h.configure('2010-01-01',[record]);assert.equal(h.apply(data,'rail').features[0].properties.history_state,'construction');
 h.configure('2026-01-01',[record]);const operating=h.apply(data,'rail').features[0];assert.equal(operating.properties.construction,false);assert.equal(operating.geometry,geometry);assert.equal(data.features[0].properties.construction_status,'construction');
 assert.equal(state(record,'2099-01-01'),'operating');assert.equal(state({...record,closed:'2026-01-01'},'2026-01-01'),'disused');
});
test('station inherits unique line and mode aliases do not collide',()=>{
 const h=new History();h.configure('2010-01-01',[record]);
 const station={features:[{properties:{station_id:'ST',operating_line_ids:['IL']},geometry:{type:'Point',coordinates:[120,30]}}]};
 assert.equal(h.apply(station,'rail').features[0].properties.history_state,'construction');assert.equal(h.apply(station,'road').features[0].properties.history_state,undefined);
 h.configure('2010-01-01',[record,{...record,id:'ST',source_aliases:['object:station_id:ST'],opened:'2009-01-01'}]);assert.equal(h.apply(station,'rail').features[0].properties.history_state,'operating');
});

test('metro relation and road route inherit without crossing transport modes',()=>{
 const h=new History();h.configure('2010-01-01',[
  {...record,mode:'metro',source_aliases:['metro:relation/10']},
  {...record,mode:'road',source_aliases:['road:route/R1']}
 ]);
 assert.equal(h.apply({features:[{properties:{route_relation_id:10}}]},'metro').features[0].properties.history_state,'construction');
 assert.equal(h.apply({features:[{properties:{route_keys:['R1']}}]},'road').features[0].properties.history_state,'construction');
 assert.equal(h.apply({features:[{properties:{route_relation_id:10}}]},'rail').features[0].properties.history_state,undefined);
});
