import test from 'node:test';
import assert from 'node:assert/strict';
import {VehicleMotion, alongPath} from '../assets/vehicle-motion.mjs';
import {namedFeature} from '../assets/display-names.mjs';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const path = {coordinates: [[0,0],[1,0],[1,1]], cumulative: [0,100,200]};
const packet = d => ({type:'FeatureCollection',features:[{type:'Feature',properties:{vehicle_id:'G1',motion_path:'COR-1',distance_m:d},geometry:{type:'Point',coordinates:alongPath(path,d)}}]});

test('vehicle follows the bend, shares its path, and snaps on pause or time jump', () => {
  const motion = new VehicleMotion();
  motion.accept(packet(50), 0, true, {paths:{'COR-1':path},speed:1}, 0);
  motion.accept(packet(150), .3, true, {speed:1}, 300);
  assert.deepEqual(motion.sample(450).features[0].geometry.coordinates, [1,0]);
  assert.deepEqual(motion.sample(525).features[0].geometry.coordinates, [1,.25]);
  assert.equal(motion.active(525), true);
  motion.accept(packet(175), .4, false, {}, 550);
  assert.deepEqual(motion.sample(550).features[0].geometry.coordinates, [1,.75]);
  assert.equal(motion.active(550), false);
  motion.accept(packet(50), 100, true, {}, 650);
  assert.deepEqual(motion.sample(650).features[0].geometry.coordinates, [.5,0]);
  motion.accept({type:'FeatureCollection',features:[]}, 101, false, {}, 700);
  assert.equal(motion.sample(700).features.length, 0);
});

test('renamed labels retain IDs, source names, geometry and support undo', () => {
  const source = {type:'Feature',properties:{line_id:'IL-1',catalog_group_id:'RL-1',name:'原名',line_name:'原线名'},geometry:{type:'LineString',coordinates:[[0,0],[1,1]]}};
  const renamed = namedFeature(source, {lines:{'IL-1':'新线名'}});
  assert.equal(renamed.properties.line_display_name, '新线名');
  assert.equal(renamed.properties.line_id, 'IL-1');
  assert.equal(renamed.properties.name, '原名');
  assert.equal(renamed.geometry, source.geometry);
  assert.equal(source.properties.display_name, undefined);
  assert.equal(namedFeature(renamed, {}).properties.line_display_name, undefined);
  const point = {...source,properties:{osm_node_id:1,name:'站'},geometry:{type:'Point',coordinates:[0,0]}};
  assert.equal(namedFeature(point,{stations:{'node/1':'新站'}}).properties.display_name,'新站');
  assert.equal(namedFeature(point,{features:{'node/1':'道岔'}}).properties.display_name,'道岔');
});

test('only assigned station yards receive descriptive names', () => {
  const feature={type:'Feature',properties:{line_name:'未命名轨道·w1',station_name:'南京南',track_type:'高速铁路站场股道',station_assignment_status:'nearby_reference'},geometry:{type:'LineString',coordinates:[[0,0],[1,1]]}};
  const label=namedFeature(feature);
  assert.equal(label.properties.line_display_name,'南京南 · 站场股道');
  assert.equal(label.properties.display_name_verification_status,'nearby_reference');
  assert.equal(namedFeature({...feature,properties:{...feature.properties,station_name:'未关联站场'}}).properties.line_display_name,undefined);
});

test('actual vehicle update sends only vehicle packets to the independent overlay', () => {
  const packets=[];
  const context=vm.createContext({window:{maplibregl:{}},document:{hidden:false},
    qt:{webChannelTransport:{}},QWebChannel:function(){},setTimeout:()=>{throw new Error('Static map refresh during vehicle update');},clearTimeout:()=>{},console});
  for(const file of ['entity-presentation.js','infrastructure-history.js','map.js'])
    vm.runInContext(readFileSync(new URL('../assets/'+file,import.meta.url),'utf8'),context);
  context.overlay={update:(data,playing)=>packets.push({data,playing})};
  const source=readFileSync(new URL('../assets/map.js',import.meta.url),'utf8');
  const body=source.match(/setRailOperatingVehicles\(data,clock,playing\)\{(.*?)\},/s)[1];
  vm.runInContext(`config={sources:{}};railVehicleOverlay=overlay;vehicleUpdate=function(data,clock,playing){${body}}`,context);
  context.data=packet(50);
  vm.runInContext('vehicleUpdate(data,0,true)',context);
  context.data=packet(150);
  vm.runInContext('vehicleUpdate(data,.3,false)',context);
  assert.equal(packets.length,2);
  assert.equal(packets[1].playing,false);
  assert.equal(packets[1].data.features[0].properties.distance_m,150);
});
