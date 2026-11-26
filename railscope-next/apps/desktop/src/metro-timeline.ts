import type { ChartStop, Run } from './operations-api'

export type TimeRange = readonly [start:number,end:number]
export type EvidenceKind = 'boundary'|'map'|'field'
export const midnight = 24 * 3600
export const metroPresets = {
  first: [5 * 3600, 7.5 * 3600],
  morning: [7 * 3600, 10 * 3600],
  midday: [10 * 3600, 16.5 * 3600],
  evening: [16 * 3600, 20 * 3600],
  last: [21.5 * 3600, Infinity],
} as const satisfies Record<string,TimeRange>

export function evidenceKind(text?:string):EvidenceKind|undefined {
  if(!text)return
  if(/现场|站台屏/.test(text))return 'field'
  if(/公开首末班|公示/.test(text))return 'boundary'
  if(/截图|地图预计|地图预测/.test(text))return 'map'
}

export function metroStations(chart:ChartStop[],line7:boolean) {
  const ids=new Set<string>()
  const unique=chart.filter(stop=>{
    if(ids.has(stop.station_id))return false
    ids.add(stop.station_id)
    return true
  })
  return line7&&unique[0]?.station_name==='花木路'&&unique.at(-1)?.station_name==='美兰湖'?unique.reverse():unique
}

export function metroRuns(runs:Run[],rows:ChartStop[]) {
  const indices=new Map(rows.map((stop,index)=>[stop.station_id,index]))
  return runs.flatMap(run=>{
    if(run.status==='cancelled')return []
    const points=run.stops.flatMap(stop=>{
      const index=indices.get(stop.station_id)
      const arrival=stop.arrival_time_s??stop.departure_time_s
      if(index==null||arrival==null)return []
      return [{index,arrival,departure:stop.departure_time_s??arrival,evidence:stop.timing_evidence,kind:evidenceKind(stop.timing_evidence)}]
    })
    const first=points[0]
    const last=points.at(-1)
    if(!first||!last||points.length<2||points.length!==run.stops.length)return []
    const reverse=first.index>last.index
    if(points.some((point,i)=>i>0&&(reverse?point.index>=points[i-1].index:point.index<=points[i-1].index)))return []
    const low=Math.min(first.index,last.index)
    const high=Math.max(first.index,last.index)
    const short=low!==0||high!==rows.length-1
    return [{run,points,reverse,short,route:`${rows[low].station_id}/${rows[high].station_id}`,low,high,first,last}]
  })
}

export type MetroTrace=ReturnType<typeof metroRuns>[number]

export function operatingRange(traces:MetroTrace[]):TimeRange {
  if(!traces.length)return [0,midnight]
  const first=Math.min(...traces.map(trace=>trace.first.arrival))
  const last=Math.max(...traces.map(trace=>trace.last.departure))
  const start=Math.max(0,Math.floor(first/3600)*3600)
  return [start,Math.max(start+1800,Math.ceil(last/1800)*1800)]
}

export function fitTimeRange(start:number,duration:number,limits:TimeRange):TimeRange {
  const span=Math.min(limits[1]-limits[0],Math.max(1200,duration))
  const left=Math.min(limits[1]-span,Math.max(limits[0],start))
  return [left,left+span]
}

export function timeTicks(range:TimeRange) {
  const wanted=(range[1]-range[0])/13
  const step=[60,120,300,600,900,1800,3600,7200,10800,21600,43200].find(value=>value>=wanted)??86400
  const first=Math.ceil(range[0]/step)*step
  const ticks=[]
  for(let seconds=first;seconds<=range[1];seconds+=step)ticks.push(seconds)
  if(range[0]<midnight&&range[1]>midnight&&!ticks.includes(midnight))ticks.push(midnight)
  return {step,ticks:ticks.sort((a,b)=>a-b)}
}
