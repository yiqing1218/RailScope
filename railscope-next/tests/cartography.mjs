import { strict as assert } from 'node:assert'
import { test } from 'node:test'
import { facilityStyles, lifecycles, missingColor, railPaint, sites, tracks } from '../apps/desktop/src/cartography.ts'

test('default railway colors, Z14 widths and lifecycle patterns', () => {
  assert.deepEqual(tracks.rail_main.slice(1), ['#D58A28', 3, 5])
  assert.deepEqual(tracks.rail_highspeed.slice(1), ['#DF4244', 4, 3])
  assert.deepEqual(tracks.track_siding.slice(1), ['#7D8993', 2, 13])
  assert.deepEqual(tracks.track_crossover.slice(1), ['#4B8097', 2, 14])
  assert.deepEqual(lifecycles.active.slice(1), [1, 3, null])
  assert.deepEqual(lifecycles.construction.slice(1), [.85, 3, [4, 3]])
  assert.deepEqual(lifecycles.abandoned.slice(1), [.32, 12, [1, 4]])
  assert.equal(railPaint('speed').at(-1), missingColor)
})

test('station symbols and source-geometry layers have independent display rules', () => {
  assert.match(sites.rail_station_unknown[2], /width="14" height="14".*fill="none" stroke="#314C65"/)
  assert.match(sites.rail_station_passenger[2], /fill="#314C65"/)
  assert.match(sites.rail_halt[2], /r="3.5" fill="#62758B"/)
  assert.match(sites.metro_interchange[2], /r="9.5".*r="3.5"/)
  assert.match(sites.rail_yard_site[2], /width="22" height="16".*M5 9 H19 M5 12 H19 M5 15 H19/)
  assert.match(sites.rail_crossover_site[2], /M5 5 L19 19 M19 5 L5 19.*#806C58/)
  assert.deepEqual(facilityStyles.platform, {name:'站台',fill:'#E6BF72',outline:'#BC853E',opacity:.55,zoom:14})
  assert.equal(facilityStyles.station_area.zoom, 13)
  assert.equal(facilityStyles.station_building.zoom, 14)
  assert.notEqual(facilityStyles.railway_land.fill, facilityStyles.station_area.fill)
})
