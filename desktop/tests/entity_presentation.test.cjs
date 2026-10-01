const test=require('node:test'),assert=require('node:assert/strict');
require('../assets/entity-presentation.js');
const {EntityPresentation,ownerKeys}=globalThis.RailScopeEntityPresentation;
const feature=id=>({geometry:{type:'Point',coordinates:[120,30]},properties:{station_source_id:id,name:id}});
test('rename only paints the affected resident source and keeps coordinates',async()=>{
  const requests=[],painted=[];
  const p=new EntityPresentation(async f=>{requests.push(f);return f.map(v=>({...v.properties,display_name:'新名'}));},
    (source,data)=>painted.push([source,data]),error=>{throw error;});
  const a=feature('way/1'),b=feature('way/2');
  p.register('railPoints',{features:[a,b]});p.register('rail',{features:[]});
  await p.patch({'station:way/1':{display_name:'新名'}});
  assert.equal(requests.length,1);assert.equal(requests[0].length,1);
  assert.deepEqual(painted.map(v=>v[0]),['railPoints']);
  assert.equal(a.properties.display_name,'新名');assert.equal(b.properties.display_name,undefined);
  assert.deepEqual(a.geometry.coordinates,[120,30]);
});
test('queued edits during a response are applied in order',async()=>{
  let release;const paints=[];let calls=0;
  const p=new EntityPresentation(async f=>{calls++;if(calls===1)await new Promise(r=>release=r);return f.map(v=>({...v.properties,revision:calls}));},
    (_,data)=>paints.push(data.features[0].properties.revision),error=>{throw error;});
  p.register('railPoints',{features:[feature('way/1')]});
  const done=p.patch({'station:way/1':{display_name:'甲'}});
  p.patch({'station:way/1':{display_name:'乙'}});release();await done;
  assert.deepEqual(paints,[1,2]);
});
test('replaced viewport cannot be overwritten by an old response',async()=>{
  let release;const paints=[];let calls=0;
  const p=new EntityPresentation(async f=>{calls++;if(calls===1)await new Promise(r=>release=r);return f.map(v=>({...v.properties,revision:calls}));},
    (_,data)=>paints.push(data.features[0].properties.revision),error=>{throw error;});
  p.register('railPoints',{features:[feature('way/1')]});
  const done=p.patch({'station:way/1':null});
  p.register('railPoints',{features:[feature('way/1')]});release();await done;
  assert.deepEqual(paints,[2]);
});
test('all linked station and yard representations have owner keys',()=>{
  assert(ownerKeys({infrastructure_id:'way/99',associated_station_ids:'["way/1"]'}).includes('station:way/1'));
  assert(ownerKeys({catalog_group_id:'ST-1',section_id:'IS-1',network_edge_id:'NE-1'}).includes('object:section_id:IS-1'));
});

test('logical shared edits repaint resident members without expanding source IDs',async()=>{
  const requests=[];
  const presentation=new EntityPresentation(async features=>{requests.push(features);return features.map(f=>({...f.properties,rail_display_color:'#123456'}));},()=>{},error=>{throw error;});
  const a={geometry:{type:'LineString',coordinates:[[120,30],[121,31]]},properties:{catalog_group_id:'RL-a',assembly_id:'RLU-shared'}};
  const b={geometry:{type:'LineString',coordinates:[[121,31],[122,32]]},properties:{catalog_group_id:'RL-b',assembly_id:'RLU-shared'}};
  presentation.register('rail',{features:[a,b]});
  await presentation.patch({'line-assembly:RLU-shared':{attributes:{color:'#123456'}}});
  assert.equal(requests[0].length,2);
  assert.equal(a.properties.rail_display_color,b.properties.rail_display_color);
  assert.deepEqual(b.geometry.coordinates,[[121,31],[122,32]]);
});

test('a transient bridge failure recovers without a manual refresh',async()=>{
  let calls=0;const paints=[],errors=[];
  const p=new EntityPresentation(async f=>{if(++calls===1)throw new Error('temporary');return f.map(v=>({...v.properties,display_name:'已恢复'}));},
    (_,data)=>paints.push(data.features[0].properties.display_name),error=>errors.push(error));
  p.register('railPoints',{features:[feature('way/1')]});
  await p.patch({'station:way/1':{display_name:'已恢复'}});
  assert.equal(calls,2);assert.deepEqual(paints,['已恢复']);assert.deepEqual(errors,[]);
});
