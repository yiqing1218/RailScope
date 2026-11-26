import { t } from './i18n'
import { invoke } from '@tauri-apps/api/core'
import type { Geometry, LineString } from 'geojson'

export type Provenance = {
  source_kind: 'demo' | 'user_defined' | 'publisher' | 'gtfs' | 'osm'
  source_url_or_reference: string
  source_timestamp?: string
  confidence?: number
  verified_by?: string
}

export type EdgeRef = {
  edge_id: string
  sequence: number
  forward: boolean
  start_distance_m: number
  end_distance_m: number
}

export type Corridor = {
  id: string
  name: string
  version: number
  snapshot_id: string
  origin_node_id: string
  destination_node_id: string
  edge_refs: EdgeRef[]
  verification_status: 'automatic_reference' | 'verified' | 'schematic'
  resolution_mode: string
  source_id: string
  provenance: Provenance
  geometry?: LineString | null
  warnings?: string[]
  candidates?: { edge_ids: string[]; meters: number }[]
  intent?: ControlStep[]
  mode?: 'rail' | 'metro'
  lines?: { id:string; name:string }[]
  current_version?: boolean
}
export const corridorKey=(corridor:{id:string;version:number})=>`${corridor.id}:${corridor.version}`
export const runCorridorKey=(run:Run)=>run.corridor_id&&run.corridor_version!=null?`${run.corridor_id}:${run.corridor_version}`:''
export type ControlStep = { node_id:string; line_id:string|null; via_edge?:string|null }
export type ControlPoint = { id:string; name:string; point:[number,number]; meters?:number; evidence?:string }
export type CorridorPreview = { corridor:Corridor; geometry:Geometry; mode:'rail'|'metro' }

export type Service = {
  id: string
  train_code: string
  operator?: string | null
  mode: 'rail' | 'metro'
  origin_station_id?: string
  destination_station_id?: string
  provenance: Provenance
}

export type Stop = {
  station_id: string
  sequence: number
  arrival_time_s?: number | null
  departure_time_s?: number | null
  pass_through?: boolean
  platform_id?: string | null
  station_route_id?: string | null
  stop_edge_id?: string | null
  stop_edge_sequence?: number | null
  stop_offset_m?: number | null
  timing_evidence?: string
  anchor_source?: string | null
}

export type Run = {
  id: string
  service_id: string
  train_number: string
  service_date: string
  time_zone: string
  plan_version: number
  status: string
  origin_station_id?: string
  destination_station_id?: string
  corridor_id?: string | null
  corridor_version?: number | null
  stops: Stop[]
  provenance: Provenance
}

export type Scenario = {
  id: string
  name: string
  headway_s: number
  turnaround_s: number
  delays: Record<string, number>
}

export type Cycle = {
  id:string; name:string; outbound_id:string; outbound_version:number; return_id:string; return_version:number;
  service_date:string; first_departure_s:number; end_s:number; turnaround_s:number; launch_gap_s:number; vehicles:number; trainsets:string[];
}

export type StockType = { id: string; name: string; series?: string; max_speed_kmh?: number; length_m?: number; provenance:Provenance }
export type Trainset = { id: string; name: string; identifier?: string; stock_type_id?: string; provenance:Provenance }
export type Assignment = { trainset_id: string; run_id: string }
export type OperationList = {
  cycles: Cycle[]
  corridors: Corridor[]
  services: Service[]
  runs: Run[]
  dates: string[]
  dates_by_mode?: Partial<Record<Service['mode'],string[]>>
  stock_types: StockType[]
  trainsets: Trainset[]
  assignments: Assignment[]
  scenarios: Scenario[]
}

export type ChartStop = Stop & { station_name?: string | null; distance_m?: number | null }
export type RunChart = { run_id: string; corridor_id?: string | null; corridor_version?: number | null; stops: ChartStop[] }
export type Diagnostic = { code: string; message: string; run_id?: string | null }
export type RunDetail = {
  run: Run
  service: Service | null
  corridor: Corridor | null
  geometry: LineString | null
  chart: ChartStop[]
  trainset: Trainset | null
  stock_type: StockType | null
  diagnostics?: Diagnostic[]
}

export type Level = 'l1' | 'l2' | 'l3'
export type Clock = {time_s:number; rate:number; playing:boolean}
export type TrainState = {
  run_id: string
  time_s: number
  edge_id?: string | null
  edge_sequence?: number | null
  distance_along_edge_m?: number | null
  distance_m?: number | null
  lon?: number | null
  lat?: number | null
  heading_deg?: number | null
  speed_mps?: number | null
  status: 'scheduled' | 'moving' | 'stopped' | 'finished' | 'cancelled' | 'unresolved'
  position_kind: 'planned' | 'simulated'
  verification_status?: string | null
}
export type Occupancy = {
  train_run_id: string
  resource_type: string
  resource_id: string
  start_time_s: number
  end_time_s: number
  direction?: string | null
}
export type ResourceConflict = {
  train_run_a: string
  train_run_b: string
  resource_type: string
  resource_id: string
  start_time_s: number
  end_time_s: number
  conflict_type: string
  severity: string
}
export type Simulation = {
  states: TrainState[]
  occupancies: Occupancy[]
  conflicts: ResourceConflict[]
  diagnostics: Diagnostic[]
  time_s?: number
}

export type ScheduleFormat = 'json' | 'csv' | 'gtfs'
export type LegacyIssue = {id:string;schema:string;reason:string}

function operations<T>(snapshot: string, action: string, args: Record<string, unknown> = {}) {
  return invoke<T>('operations', { snapshot, request: { action, ...args } })
}

export const opsApi = {
  list: (snapshot: string, service_date?:string) => operations<OperationList>(snapshot, 'list', {service_date}),
  resolve: (snapshot: string, args: {
    start_id: string; end_id: string; strict: boolean; name: string; waypoints?: string[]; line_ids?: string[]
  }) => operations<Corridor>(snapshot, 'resolve_corridor', args),
  saveCorridor: (snapshot: string, corridor: Corridor) => operations<Corridor>(snapshot, 'save_corridor', { corridor }),
  archiveCorridor:(snapshot:string,id:string)=>operations<void>(snapshot,'archive_corridor',{id}),
  cancelRun:(snapshot:string,run:Run)=>operations<Run>(snapshot,'cancel_run',{id:run.id,version:run.plan_version}),
  generateCycle:(snapshot:string,cycle:Cycle)=>operations<{template:Cycle;runs:number}>(snapshot,'generate_cycle',{cycle}),
  legacyIssues:(snapshot:string)=>operations<LegacyIssue[]>(snapshot,'legacy_issues'),
  importLegacy:(snapshot:string,content:string,service_date:string,aliases:Record<string,string>)=>
    operations<{corridors:number;runs:number;pending:boolean;issue?:string}>(snapshot,'import_legacy',{content,service_date,aliases}),
  corridorQuery:<T>(snapshot:string,action:string,args:Record<string,unknown>,job=crypto.randomUUID())=>
    invoke<T>('corridor_query',{snapshot,request:{action,...args},job}),
  saveService: (snapshot: string, service: Service) => operations<Service>(snapshot, 'save_service', { service }),
  saveRun: (snapshot: string, run: Run) => operations<Run>(snapshot, 'save_run', { run }),
  getRun: (snapshot: string, id: string) => operations<RunDetail>(snapshot, 'get_run', { id }),
  previewRun: (snapshot: string, run: Run, time_s: number) =>
    operations<TrainState>(snapshot, 'preview_run', { run, time_s }),
  suggestAnchors: (snapshot:string,run:Run) =>
    operations<{run:Run;matches:{station:string;distance_m:number}[]}>(snapshot,'suggest_anchors',{run}),
  saveScenario: (snapshot: string, scenario: Scenario) => operations<Scenario>(snapshot, 'save_scenario', { scenario }),
  simulate: (snapshot: string, run_ids: string[], time_s: number, level: Level, scenario?: Scenario, resources=false) =>
    operations<Simulation>(snapshot, 'simulate', { run_ids, time_s, level, scenario, resources }),
  clock:(action:string,value?:number)=>invoke<Clock>('operations_clock',{action,value}),
  import: (snapshot: string, format: ScheduleFormat, content: string, service_date?: string,stop_map?:Record<string,string>) =>
    operations<{services:number;runs:number;corridors:number;first_date:string;duplicate?:boolean}>(snapshot, 'import_timetable', { format, content, service_date, stop_map }),
  importExternal:(snapshot:string,format:ScheduleFormat,content:string,preview:boolean,service_date?:string,stop_map?:Record<string,string>)=>
    operations<{services:number;runs:number;corridors:number;first_date:string;duplicate?:boolean;
      corridor_ids?:Record<string,{id:string;version:number}>}>(
        snapshot,'import_external_plan',{ format,content,service_date,stop_map,preview }),
  export: (snapshot: string, format: ScheduleFormat, run_ids?: string[]) =>
    operations<{ content: string; format: ScheduleFormat }>(snapshot, 'export_timetable', { format, run_ids }),
  demo: (snapshot:string,corridor:Corridor,origin_station_id:string,destination_station_id:string,service_date:string)=>
    operations<{runs:Run[]}>(snapshot,'seed_demo',{corridor_id:corridor.id,version:corridor.version,origin_station_id,destination_station_id,service_date}),
  saveStock: (snapshot:string,stock:StockType)=>operations<StockType>(snapshot,'save_stock',{stock}),
  saveTrainset: (snapshot:string,trainset:Trainset)=>operations<Trainset>(snapshot,'save_trainset',{trainset}),
  assign: (snapshot:string,run_id:string,trainset_id:string)=>operations<Assignment>(snapshot,'assign',{assignment:{run_id,trainset_id}}),
}

export function describeError(error: unknown) {
  if (typeof error === 'object' && error !== null && 'message' in error) {
    const code = 'code' in error && typeof error.code === 'string' ? ` · ${error.code}` : ''
    return `${String(error.message)}${code}`
  }
  return String(error)
}

export function formatTime(seconds: number | null | undefined) {
  if (seconds == null || !Number.isFinite(seconds)) return '—'
  const total = Math.abs(Math.floor(seconds))
  const fields = [Math.floor(total / 3600), Math.floor(total % 3600 / 60), total % 60]
  return (seconds < 0 ? '−' : '') + fields.map(field => String(field).padStart(2, '0')).join(':')
}

export function parseTime(input: string): number | undefined {
  const value = input.trim()
  if (!value) return undefined
  const match = /^(\d{1,3}):([0-5]\d)(?::([0-5]\d))?$/.exec(value)
  if (!match) throw new Error(t("时刻“{0}”格式有误。请输入 HH:MM 或 HH:MM:SS。", input))
  return Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3] ?? 0)
}

export const trustLabel: Record<Corridor['verification_status'], string> = {
  automatic_reference: '自动参考',
  verified: '来源已核验',
  schematic: '示意径路',
}

export const sourceLabel: Record<Provenance['source_kind'], string> = {
  demo: '示例', user_defined: '用户录入', publisher: '发布方资料', gtfs: 'GTFS', osm: 'OpenStreetMap',
}

export const stateLabel: Record<TrainState['status'], string> = {
  scheduled: '待发', moving: '运行中', stopped: '停站', finished: '已结束', cancelled: '取消', unresolved: '径路未解析',
}

export function datedRunLabel(run: Run) {
  return `${run.train_number} · ${run.service_date}`
}

export function chinaDate() {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date())
}
