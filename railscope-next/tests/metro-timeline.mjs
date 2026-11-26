import { strict as assert } from 'node:assert'
import { test } from 'node:test'
import { evidenceKind, fitTimeRange, metroRuns, metroStations, operatingRange, timeTicks } from '../apps/desktop/src/metro-timeline.ts'

test('both directions share the source station order and preserve a cross-midnight run', () => {
  const stops = [
    { station_id:'c', station_name:'花木路', distance_m:0 },
    { station_id:'b', station_name:'祁华路', distance_m:1000 },
    { station_id:'a', station_name:'美兰湖', distance_m:2000 },
  ]
  const rows = metroStations(stops,true)
  assert.deepEqual(rows.map(stop=>stop.station_id),['a','b','c'])
  assert.equal(stops[0].station_id,'c')

  const leg = (id,first,last,start,end) => ({id,status:'scheduled',stops:[
    {station_id:first,departure_time_s:start,timing_evidence:'公开首末班边界'},
    {station_id:last,arrival_time_s:end,timing_evidence:'现场站台屏预计到站'},
  ]})
  const traces = metroRuns([leg('out','b','c',22000,23000),leg('back','c','b',85900,87000)],rows)
  assert.deepEqual(traces.map(trace=>[trace.route,trace.reverse,trace.short]),[['b/c',false,true],['b/c',true,true]])
  assert.equal(traces[1].last.departure,87000)
  assert.deepEqual(operatingRange(traces),[21600,88200])
  assert.equal(traces[0].first.kind,'boundary')
  assert.equal(traces[0].last.kind,'field')
  assert.equal(evidenceKind('周三地图预计到站（分钟）'),'map')
  assert.equal(evidenceKind('区间与停站推演'),undefined)
})

test('manual pan and zoom remain inside the operating day and retain a midnight tick', () => {
  assert.deepEqual(fitTimeRange(-2000,60,[18000,90000]),[18000,19200])
  assert.deepEqual(fitTimeRange(92200,3600,[18000,90000]),[86400,90000])
  const {ticks}=timeTicks([20700,90000])
  assert.ok(ticks.includes(86400))
  assert.ok(ticks.every(time=>time>=20700&&time<=90000))
})
