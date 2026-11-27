import type { ExpressionSpecification } from 'maplibre-gl'

export const tracks = {
  rail_highspeed: ['高速铁路', '#DF4244', 4, 3],
  rail_main: ['铁路干线', '#D58A28', 3, 5],
  rail_branch: ['铁路支线', '#8E794D', 2.5, 7],
  rail_unspecified: ['其他／待核验国铁', '#7D8993', 2.5, 8],
  track_siding: ['站线／到发线', '#7D8993', 2, 13],
  track_yard: ['调车线／车场线', '#9DA5AC', 1.8, 14],
  track_spur: ['专用线', '#9B7D65', 2, 11],
  track_crossover: ['渡线', '#4B8097', 2, 14],
  metro_track: ['地铁物理轨道', '#169CA0', 3, 7],
  light_rail_track: ['轻轨', '#7665B3', 2.5, 9],
  tram_track: ['有轨电车', '#5C9A70', 2, 10],
  monorail_track: ['单轨／磁浮', '#A979B5', 2.5, 9],
  unknown_track: ['未归类轨道', '#89939C', 1.8, 11],
} as const

export const lifecycles = {
  active: ['运营', 1, 3, null],
  construction: ['在建', .85, 3, [4,3]],
  proposed: ['规划', .65, 9, [1,3]],
  disused: ['停用', .50, 11, [4,4]],
  abandoned: ['废弃', .32, 12, [1,4]],
  razed: ['拆除', .20, 14, [1,5]],
  unknown: ['待核验', .70, 3, null],
} as const

export const stationColors = {rail:'#314C65',metro:tracks.metro_track[1]} as const
const stationCircle = (mode:keyof typeof stationColors, radius=4) =>
  `<circle cx="12" cy="12" r="${radius}" fill="#fff" stroke="${stationColors[mode]}" stroke-width="1.8"/>`

export const sites = {
  rail_station_passenger: ['铁路客运站', 9, stationCircle('rail')],
  rail_station_unknown: ['铁路车站（未分类）', 9, stationCircle('rail')],
  metro_station: ['地铁站', 9, stationCircle('metro')],
  metro_interchange: ['地铁换乘站', 7, stationCircle('metro',6)],
  rail_halt: ['乘降所', 11, stationCircle('rail')],
  tram_stop: ['有轨电车站', 11, stationCircle('metro')],
  rail_yard_site: ['车场／货场', 11, `<rect x="1" y="4" width="22" height="16" rx="2" fill="none" stroke="#8B775C" stroke-width="1.7"/><path d="M5 9 H19 M5 12 H19 M5 15 H19" fill="none" stroke="#8B775C" stroke-width="1.2"/>`],
  rail_depot_site: ['车辆段／基地', 11, `<path d="M3 20V9l9-6 9 6v11Z" fill="#f9fcf9" stroke="#2c7666" stroke-width="2"/><path d="M8 20v-8h8v8" fill="none" stroke="#2c7666" stroke-width="1.8"/>`],
  rail_marshalling_site: ['编组站', 11, `<circle cx="12" cy="12" r="9" fill="#fffaf0" stroke="#9a6035" stroke-width="2"/><path d="M6 16h12M9 16v-4l5-5M13 16v-4l4-4" fill="none" stroke="#9a6035" stroke-width="1.6"/>`],
  rail_junction_site: ['分歧线路所', 11, `<path d="M12 4 L20 12 L12 20 L4 12 Z" fill="#A37437"/>`],
  rail_crossover_site: ['渡线所', 13, `<path d="M5 5 L19 19 M19 5 L5 19" fill="none" stroke="#806C58" stroke-width="2.5" stroke-linecap="round"/>`],
  rail_spur_junction_site: ['专用线接入所', 13, `<path d="M12 4 L21 20 L3 20 Z" fill="none" stroke="#806C58" stroke-width="2" stroke-linejoin="round"/>`],
  rail_site_unknown: ['未分类运营地点', 13, `<rect x="6.5" y="6.5" width="11" height="11" rx="1" fill="none" stroke="#8D959C" stroke-width="1.5"/>`],
  switch_detail: ['道岔', 17, `<circle cx="12" cy="12" r="4.1" fill="#596B78" stroke="#F8FAF7" stroke-width="1.5"/>`],
  level_crossing_detail: ['平交道口', 16, `<rect x="4" y="7" width="16" height="10" rx="3" fill="#FBF4E4" stroke="#80502B" stroke-width="1.3"/><path d="M10 9.5v5m4-5v5" stroke="#80502B" stroke-width="1.65" stroke-linecap="round"/>`],
  railway_crossing_detail: ['轨道菱形交叉', 16, `<path d="M5 12 H19 M12 5 V19" fill="none" stroke="#56616D" stroke-width="2" stroke-linecap="round"/>`],
  signal_detail: ['信号机', 17, `<path d="M12 17V9" stroke="#596579" stroke-width="2"/><circle cx="12" cy="7" r="3" fill="#596579"/>`],
  subway_entrance: ['地铁出入口', 16, `<path d="M7 17V7H17V17M10 11L12 9L14 11M12 9V17" fill="none" stroke="#169CA0" stroke-width="1.6"/>`],
} as const

export const thematic = {
  speed: { name:'最高速度', entries:[[1,'0–80 km/h','#4C99D8'],[2,'81–160 km/h','#4BA96A'],[3,'161–250 km/h','#D9A23A'],[4,'251–350 km/h','#D94D45'],[5,'>350 km/h','#8855B8']] },
  gauge: { name:'轨距', entries:[[0,'<1435 mm','#B98A45'],[1,'1435 mm','#3F83C3'],[2,'>1435 mm','#865CB5']] },
  power: { name:'供电方式', entries:[['overhead','接触网','#336EBD'],['third_rail','第三轨','#9155B8'],['none','无电气化','#878C92']] },
  lifecycle: { name:'生命周期', entries:[] },
  technical: { name:'车站技术作业类型', entries:[] },
} as const

export type RailView = 'infrastructure' | keyof typeof thematic
export type MetroView = 'lines' | 'infrastructure'
export type TrackViews = { rail: RailView; metro: MetroView }
export const defaultTrackViews: TrackViews = { rail:'infrastructure', metro:'lines' }
export const nationalStationSymbols=['rail_station_passenger','rail_station_unknown','rail_halt']
export const metroStationSymbols=['metro_station','metro_interchange','tram_stop']
export const nonRailStationNames = { bus_station:'公交场站', unclassified_transport_station:'未分类公共交通站' }
export const siteRoleNames = {station:'国铁车站',metro:'城市轨道车站',junction:'线路所',marshalling:'编组站',yard:'车场／货场',depot:'车辆段／运用所',area:'铁路场站范围'}
export const siteAreaNames = {boundary:'站区边界',railway_land:'铁路用地',building:'站房轮廓'}

export function readTrackViews(storage: Pick<Storage,'getItem'>): TrackViews {
  const rail=storage.getItem('railscope-next.rail-style')
  const metro=storage.getItem('railscope-next.metro-style')
  return {
    rail:rail&&Object.hasOwn(thematic,rail)?rail as keyof typeof thematic:defaultTrackViews.rail,
    metro:metro==='infrastructure'?'infrastructure':defaultTrackViews.metro,
  }
}
export const missingColor = '#ADB4BD'
export const metroColor = tracks.metro_track[1]

export const facilityStyles = {
  platform: { name: '站台', fill: '#E6BF72', outline: '#BC853E', opacity: .55, zoom: 14 },
  station_area: { name: '站区边界', fill: '#70A6D8', outline: '#70A6D8', opacity: .09, zoom: 13 },
  station_building: { name: '站房', fill: '#8BA8B8', outline: '#8BA8B8', opacity: .30, zoom: 14 },
  railway_land: { name: '铁路用地', fill: '#ADB4BD', outline: '#8D959C', opacity: .18, zoom: 14 },
} as const

export function symbolName(symbol: string) {
  return tracks[symbol as keyof typeof tracks]?.[0] ?? sites[symbol as keyof typeof sites]?.[0]
    ?? facilityStyles[symbol as keyof typeof facilityStyles]?.name ?? nonRailStationNames[symbol as keyof typeof nonRailStationNames]
}

// Existing PMTiles store only the earlier class field. Exact tiles carry the audit-derived symbol.
export const trackSymbol: ExpressionSpecification = ['coalesce', ['get','symbol'], ['match', ['get','class'],
  'high_speed','rail_highspeed','conventional','rail_unspecified','freight','rail_unspecified',
  'siding','track_siding','yard','track_yard','spur','track_spur','crossover','track_crossover',
  'subway','metro_track','light_rail','light_rail_track','tram','tram_track','monorail','monorail_track','rail_unspecified']]

export const siteSymbol: ExpressionSpecification = ['case',['==',['get','class'],'level_crossing'],'level_crossing_detail',
  ['coalesce',['get','symbol'],['match',['get','class'],
  'rail_station','rail_station_unknown','metro_station','metro_station','switch','switch_detail',
  'railway_crossing','railway_crossing_detail','platform','platform','station_area','station_area',
  'station_building','station_building','rail_site_unknown']]]

export const siteIcon: ExpressionSpecification = ['match',['get','site_role'],
  'junction','rail_junction_site','marshalling','rail_marshalling_site','yard','rail_yard_site','depot','rail_depot_site',siteSymbol]

export const lifecycle: ExpressionSpecification = ['coalesce',['get','life'], ['match',['get','status'],
  'operating','active','planned','proposed','construction','construction','disused','disused','unknown']]

export function trackProperty(index: 1 | 2 | 3): ExpressionSpecification {
  return ['match',trackSymbol,...Object.entries(tracks).flatMap(([key,value])=>[key,value[index]]),tracks.unknown_track[index]] as ExpressionSpecification
}

export function railPaint(view: RailView): ExpressionSpecification {
  if (view === 'infrastructure' || view === 'lifecycle' || view === 'technical') return trackProperty(1)
  const gauge: ExpressionSpecification = ['case',['!', ['has','gauge']],-1, ['<',['to-number',['get','gauge']],1435],0,
    ['==',['get','gauge'],1435],1,2]
  const key = view === 'gauge' ? gauge : ['get', view]
  return ['match',key,...thematic[view].entries.flatMap(([value,,color])=>[value,color]),missingColor] as ExpressionSpecification
}

export const svgSymbol = (content: string) => `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24">${content}</svg>`)}`
