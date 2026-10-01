// Standalone: node desktop/tests/vehicle_motion_test.cjs
const assert = require('node:assert/strict');
const {coordinateAt, VehicleOverlay} = require('../assets/vehicle-motion.js');
const frames = [[0, 0, 0], [100, 1, 0], [200, 1, 1], [400, 1, 1]];
assert.deepEqual(coordinateAt(frames, 50), [.5, 0]);
assert.deepEqual(coordinateAt(frames, 150), [1, .5]);
assert.deepEqual(coordinateAt(frames, 350), [1, 1]);
assert.deepEqual(coordinateAt(frames, 999), [1, 1]);
let callback, created = 0, removed = 0;
global.requestAnimationFrame = cb => { callback = cb; return 1; };
global.document = {hidden: false, createElement: () => ({style: {setProperty() {}}, dataset: {},
  append() {}, setAttribute() {}, addEventListener(type, cb) { this[type] = cb; }})};
class Marker {
  constructor() { created++; }
  setLngLat(value) { this.value = value; return this; }
  getLngLat() { return this.value; }
  addTo() { return this; }
  remove() { removed++; }
}
const map = {getSource() { throw Error('Vehicle updates must not touch GeoJSON sources'); }};
const selected = [];
const overlay = new VehicleOverlay(map, Marker, f => selected.push(f), 'rail-vehicles');
const feature = {properties: {trip_id: 'G1', motion_frames: frames, display_style: 'ring'},
                 geometry: {type: 'Point', coordinates: [0, 0]}};
overlay.setVisible(true);
overlay.update({features: [feature]}, true);
const item = overlay.items.get('G1');
callback(item.started + 150);
assert.deepEqual(item.marker.value, [1, .5]);
overlay.update({features: [feature]}, false);
callback(item.started + 150);
assert.deepEqual(item.marker.value, [0, 0]);
assert.equal(created, 1);
overlay.setSelected(['G1']);
assert.equal(item.element.dataset.selected, 'true');
item.element.click({stopPropagation() {}, ctrlKey: false});
assert.equal(selected[0].layer.id, 'rail-vehicles');
overlay.setVisible(false);
assert.equal(item.element.style.display, 'none');
overlay.update({features: []}, false);
assert.equal(removed, 1);
console.log('Vehicle interpolation, dwell, pause, identity reuse and source isolation: passed');
