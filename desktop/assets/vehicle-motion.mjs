// Interpolate along shared route geometry, never along a chord between updates.
export function alongPath(path, distance) {
  const points = path?.coordinates, cumulative = path?.cumulative;
  if (!points?.length || cumulative?.length !== points.length) return null;
  if (distance <= cumulative[0]) return points[0];
  if (distance >= cumulative.at(-1)) return points.at(-1);
  let low = 0, high = cumulative.length - 1;
  while (high - low > 1) {
    const mid = (low + high) >> 1;
    if (cumulative[mid] <= distance) low = mid; else high = mid;
  }
  const span = cumulative[high] - cumulative[low];
  const t = span ? (distance - cumulative[low]) / span : 0;
  return points[low].map((v, i) => v + (points[high][i] - v) * t);
}

export class VehicleMotion {
  constructor() { this.paths = {}; this.rows = []; this.received = null; this.clock = null; this.playing = false; }
  sample(now) {
    return {type: 'FeatureCollection', features: this.rows.map(row => {
      const t = this.duration ? Math.max(0, Math.min(1, (now - this.received) / this.duration)) : 1;
      const distance = row.from + (row.to - row.from) * t;
      const coordinate = alongPath(this.paths[row.feature.properties.motion_path], distance);
      return {...row.feature, properties: {...row.feature.properties, distance_m: distance},
        geometry: coordinate ? {type: 'Point', coordinates: coordinate} : row.feature.geometry};
    })};
  }
  accept(data, clock, playing, options = {}, now = performance.now()) {
    if (options.paths) this.paths = options.paths;
    const elapsed = this.received == null ? 0 : now - this.received;
    const expected = elapsed / 1000 * (options.speed || 1);
    const jump = this.clock == null || clock < this.clock || Math.abs(clock - this.clock - expected) > Math.max(2, expected * 2);
    const smooth = playing && this.playing && !jump && elapsed > 0 && elapsed < 1500;
    const previous = new Map(this.sample(now).features.map(f => [f.properties.vehicle_id, f]));
    this.duration = smooth ? Math.min(500, Math.max(80, elapsed)) : 0;
    this.rows = (data.features || []).map(feature => {
      const old = previous.get(feature.properties.vehicle_id), to = feature.properties.distance_m;
      const compatible = smooth && old?.properties.motion_path === feature.properties.motion_path;
      return {feature, from: compatible ? old.properties.distance_m : to, to};
    });
    this.received = now; this.clock = clock; this.playing = playing;
    return this.sample(now);
  }
  active(now) { return this.playing && this.duration > 0 && now < this.received + this.duration; }
}
