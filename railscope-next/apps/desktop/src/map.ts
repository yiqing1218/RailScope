import maplibre, { type ExpressionSpecification, type GeoJSONSource, type MapGeoJSONFeature } from 'maplibre-gl'
import type { Feature, Geometry, Point } from 'geojson'
import { type Bounds, type Kind, type Pack, type Summary, type Visual, tileTemplate } from './api'

export type LayerGroup = 'rail' | 'metro' | 'road' | 'station' | 'facility'
export type MapTheme = 'light' | 'color' | 'dark' | 'transport'
export type MapTrain = { id:string; label:string; point:[number,number]; bearing?:number; kind:'rail'|'metro' }
const colors = { rail: '#dc623d', metro: '#008d8a', road: '#9f771f', station: '#234c57', facility: '#627880', poi: '#9f771f' }
const vectorSources = {
  rail: 'rail', metro: 'metro', road: 'road', station: 'points', poi: 'points', facility: 'facilities',
}
const selection = '#176ce0'
type SelectableKind = Exclude<Kind, 'line'>

export const layerGroups: Array<{ id: LayerGroup; name: string; color: string }> = [
  { id: 'rail', name: '国铁', color: colors.rail },
  { id: 'metro', name: '地铁', color: colors.metro },
  { id: 'road', name: '高速公路', color: colors.road },
  { id: 'station', name: '车站与交通设施', color: colors.station },
  { id: 'facility', name: '铁路设施', color: colors.facility },
]

const layerIds: Record<LayerGroup, string[]> = {
  rail: ['rail-plan', 'rail'], metro: ['metro-plan', 'metro', 'metro-shared'], road: ['road-plan', 'road'],
  station: ['poi', 'poi-area', 'station-area', 'station', 'station-labels'], facility: ['area', 'facility-lines', 'facility-points'],
}
const editId = 'workspace-points'
const interactiveLayers = [editId, ...Object.values(layerIds).flat().reverse()]
const empty = { type: 'FeatureCollection', features: [] } as const
const baseLayers=['land','base-green','base-water','base-water-line','base-buildings','base-admin','base-road','place-labels']
const palettes = {
  light: {water:'#c8dcde',land:'#eef0e7',green:'#d7e6d0',built:'#dcd9d0',road:'#fffaf0',label:'#40565c',admin:'#bfaeaa'},
  color: {water:'#b0d6e1',land:'#f5f0df',green:'#c3e1ba',built:'#e1ccbb',road:'#fff9e3',label:'#3c5559',admin:'#a79cb0'},
  dark: {water:'#172b38',land:'#25343b',green:'#344d45',built:'#475059',road:'#677279',label:'#d3e2e5',admin:'#737383'},
  transport: {water:'#f2f3ef',land:'#f2f3ef',green:'#f2f3ef',built:'#f2f3ef',road:'#f2f3ef',label:'#334e56',admin:'#f2f3ef'},
} as const

function color(defaultColor: string | ExpressionSpecification): ExpressionSpecification {
  return ['case', ['boolean', ['feature-state', 'selected'], false], selection,
    ['to-color', ['coalesce', ['feature-state', 'color'], defaultColor]]]
}

export class MapController {
  readonly map: maplibre.Map
  private ready: Promise<void>
  private generation = 0
  private visibility = new Map<LayerGroup, boolean>()
  private visualState: Visual[] = []
  private selected: { source: string; sourceLayer?: string; id: number } | undefined
  private theme: MapTheme = 'light'
  private packId?: string

  constructor(element: HTMLElement, onPick: (id: number | null) => void, onMove: () => void, onError: (error: string) => void,
    onTrainPick?: (id: string) => void) {
    this.map = new maplibre.Map({
      container: element, center: [106, 33], zoom: 3.7, maxZoom: 22,
      dragRotate: false, pitchWithRotate: false, renderWorldCopies: false,
      maxTileCacheSize: 144, maxTileCacheZoomLevels: 4,
      localIdeographFontFamily: 'Microsoft YaHei UI',
      style: {
        version: 8,
        sources: {
          exact: { type: 'geojson', data: { ...empty, features: [] } },
          'train-path': { type: 'geojson', data: { ...empty, features: [] } },
          trains: { type: 'geojson', data: { ...empty, features: [] } },
          [editId]: { type: 'geojson', promoteId: 'rid', data: { ...empty, features: [] } },
        },
        layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#cadde0' } }],
      },
      attributionControl: false,
    })
    this.ready = new Promise(resolve => this.map.once('load', () => resolve()))
    this.map.addControl(new maplibre.AttributionControl({ compact: true }), 'bottom-right')
    this.map.addControl(new maplibre.NavigationControl({ showCompass: false }), 'top-right')
    this.map.addControl(new maplibre.ScaleControl({ unit: 'metric' }), 'bottom-left')
    this.map.on('click', event => {
      const { x, y } = event.point
      if (this.map.getLayer('train-marker')) {
        const train = this.map.queryRenderedFeatures([[x-6,y-6],[x+6,y+6]], {layers:['train-marker']})[0]
        if (typeof train?.properties.run === 'string') {onTrainPick?.(train.properties.run); return}
      }
      const hits = this.map.queryRenderedFeatures([[x - 5, y - 5], [x + 5, y + 5]], {
        layers: interactiveLayers.filter(id => this.map.getLayer(id)),
      })
      const picked = hits.find(hit => typeof hit.id === 'number' && !hit.state?.relocated)
      this.selectFeature(picked)
      onPick(typeof picked?.id === 'number' ? picked.id : null)
    })
    this.map.on('moveend', onMove)
    this.map.on('error', event => onError(event.error.message))
  }

  async setPack(pack: Pack, overview = false) {
    const generation = ++this.generation
    await this.ready
    if (generation !== this.generation) return
    const changingPack = this.packId !== pack.snapshot
    const picked = changingPack ? undefined : this.selected
    if (changingPack) this.clearSelection()
    this.packId = pack.snapshot
    this.visualState = []
    this.updateEdits()
    for (const id of ['train-marker','train-label','train-route','exact-point', 'exact-line', 'exact-area', ...interactiveLayers, ...baseLayers]) {
      if (this.map.getLayer(id)) this.map.removeLayer(id)
    }
    for (const id of ['transport', 'base']) {
      if (this.map.getSource(id)) this.map.removeSource(id)
    }
    const tiles = [tileTemplate(pack.snapshot, overview)]
    if (pack.basemap) {
      this.map.addSource('base', {
        type: 'vector', tiles: [tileTemplate(pack.snapshot, true)], minzoom: 0, maxzoom: 7,
        attribution: 'Natural Earth',
      })
      this.map.addLayer({ id: 'land', type: 'fill', source: 'base', 'source-layer': 'land',
        paint: { 'fill-color': '#eef0e7', 'fill-outline-color': '#eef0e7' } })
    }
    this.map.addSource('transport', {
      type: 'vector', tiles, minzoom: pack.min_zoom, maxzoom: overview ? pack.max_zoom : 22, promoteId: 'rid',
      attribution: '© OpenStreetMap contributors · ODbL 1.0',
    })
    this.addBaseLayers()
    const planned = ['in', ['get', 'status'], ['literal', ['construction', 'planned', 'disused']]]
    for (const group of ['road', 'rail', 'metro'] as const) {
      const base = { type: 'line', source: 'transport', 'source-layer': group, layout: { 'line-cap': 'round', 'line-join': 'round' } } as const
      this.map.addLayer({
        ...base, id: `${group}-plan`, filter: planned as ExpressionSpecification,
        paint: { 'line-color': color(colors[group]), 'line-width': ['coalesce',['feature-state','width'],1.35], 'line-dasharray': [2, 2],
          'line-opacity':['coalesce',['feature-state','opacity'],0.65] },
      })
      this.map.addLayer({
        ...base, id: group, filter: ['!', planned as ExpressionSpecification],
        paint: {
          'line-color': group === 'metro' ? color(this.metroColor('service1','metro_color')) : color(colors[group]),
          'line-width': ['interpolate', ['linear'], ['zoom'],
            5,['coalesce',['feature-state','width'],0.65],10,['coalesce',['feature-state','width'],1.35],
            14,['coalesce',['feature-state','width'],2.35],18,['coalesce',['feature-state','width'],4]],
          'line-opacity': ['coalesce',['feature-state','opacity'],group === 'road' ? 0.63 : 0.88],
        },
      })
    }
    this.map.addLayer({id:'metro-shared',type:'line',source:'transport','source-layer':'metro',minzoom:10,
      filter:['all',['has','metro_secondary'],['!',planned as ExpressionSpecification]],
      paint:{'line-color': color(this.metroColor('service2','metro_secondary')),
        'line-offset':['interpolate',['linear'],['zoom'],10,1.5,18,3],
        'line-width':['interpolate',['linear'],['zoom'],10,0.9,18,2.2]}})
    this.map.addLayer({
      id: 'area', type: 'fill', source: 'transport', 'source-layer': 'facilities',
      filter: ['==', ['geometry-type'], 'Polygon'],
      paint: { 'fill-color': color(colors.facility), 'fill-opacity': 0.35 },
    })
    for (const [id,kind,paint] of [['poi-area','services',colors.poi],['station-area','rail_station',colors.station]] as const) {
      const classes=kind==='services'?['services','rest_area','aerodrome']:['rail_station','metro_station']
      this.map.addLayer({id, type:'fill',source:'transport','source-layer':'points',
        filter:['all',['==',['geometry-type'],'Polygon'],['in',['get','class'],['literal',classes]]],
        paint:{'fill-color':color(paint),'fill-opacity':0.26}})
    }
    this.map.addLayer({
      id: 'facility-lines', type: 'line', source: 'transport', 'source-layer': 'facilities',
      filter: ['==', ['geometry-type'], 'LineString'],
      paint: { 'line-color': color(colors.facility), 'line-width': 2.2 },
    })
    for (const [layer, id, baseColor, predicate] of [
      ['points', 'poi', colors.poi, ['!', ['in', ['get', 'class'], ['literal', ['rail_station', 'metro_station']]]]],
      ['points', 'station', colors.station, ['in', ['get', 'class'], ['literal', ['rail_station', 'metro_station']]]],
      ['facilities', 'facility-points', colors.facility, ['==', ['geometry-type'], 'Point']],
    ] as const) {
      this.map.addLayer({
        id, type: 'circle', source: 'transport', 'source-layer': layer,
        filter: predicate as unknown as ExpressionSpecification,
        paint: {
          'circle-color': color(baseColor), 'circle-radius': ['interpolate', ['linear'], ['zoom'], 9, 2, 15, 4.5],
          'circle-stroke-color': '#f9fbf5', 'circle-stroke-width': 1,
          'circle-opacity': ['case', ['boolean', ['feature-state', 'relocated'], false], 0, 1],
          'circle-stroke-opacity': ['case', ['boolean', ['feature-state', 'relocated'], false], 0, 1],
        },
      })
    }
    this.map.addLayer({
      id: 'station-labels', type: 'symbol', source: 'transport', 'source-layer': 'points', minzoom: 10,
      filter: ['has', 'label'],
      layout: { 'text-field': ['get', 'label'], 'text-font': ['Microsoft YaHei UI'],
        'text-size': ['interpolate', ['linear'], ['zoom'], 10, 10, 16, 13],
        'text-anchor': 'top', 'text-offset': [0, 0.75], 'text-max-width': 12 },
      paint: { 'text-color': '#193941', 'text-halo-color': '#eef0e7', 'text-halo-width': 1.3,
        'text-opacity': ['case', ['boolean', ['feature-state', 'relocated'], false], 0, 1] },
    })
    this.map.addLayer({ id: editId, source: editId, type: 'circle',
      paint: {
        'circle-radius': 5,
        'circle-color': ['case', ['boolean', ['feature-state', 'selected'], false], selection, ['to-color', ['get', 'color']]],
        'circle-stroke-width': 1.4, 'circle-stroke-color': '#fafbf6',
      } })
    this.map.addLayer({ id: 'exact-area', source: 'exact', type: 'fill', filter: ['==', ['geometry-type'], 'Polygon'],
      paint: { 'fill-color': selection, 'fill-opacity': 0.23 } })
    this.map.addLayer({ id: 'exact-line', source: 'exact', type: 'line',
      paint: { 'line-color': selection, 'line-width': 3 } })
    this.map.addLayer({ id: 'exact-point', source: 'exact', type: 'circle', filter: ['==', ['geometry-type'], 'Point'],
      paint: { 'circle-radius': 7, 'circle-color': selection, 'circle-stroke-width': 2, 'circle-stroke-color': '#fff' } })
    this.map.addLayer({id:'train-route',source:'train-path',type:'line',
      paint:{'line-color':'#775bd7','line-width':3.1,'line-opacity':0.75}})
    this.map.addLayer({id:'train-marker',source:'trains',type:'circle',paint:{
      'circle-radius':['case',['boolean',['get','selected'],false],7,4.5],
      'circle-color':['case',['==',['get','kind'],'metro'],'#00ad8e','#7553cc'],
      'circle-stroke-color':'#fff','circle-stroke-width':1.5}})
    this.map.addLayer({id:'train-label',source:'trains',type:'symbol',minzoom:8,layout:{
      'text-field':['get','label'],'text-font':['Microsoft YaHei UI'],'text-size':12,'text-offset':[0,1.15]},
      paint:{'text-color':'#322359','text-halo-color':'#fff','text-halo-width':1.1}})
    this.setTheme(this.theme)
    for (const [group, visible] of this.visibility) this.setLayer(group, visible)
    if(changingPack) this.fit(pack.bounds, pack.region === '全国' ? 6 : 11)
    else if(picked && this.map.getSource(picked.source)) this.map.setFeatureState(picked,{selected:true})
  }

  private addBaseLayers() {
    const source='transport'
    const polygon=['==',['geometry-type'],'Polygon'] as ExpressionSpecification
    const linear=['==',['geometry-type'],'LineString'] as ExpressionSpecification
    for (const [id,layer] of [['base-green','base_green'],['base-water','base_water'],['base-buildings','base_building']] as const)
      this.map.addLayer({id,type:'fill',source,'source-layer':layer,filter:polygon,paint:{'fill-color':'#d5ded6'}})
    for (const [id,layer] of [['base-water-line','base_water'],['base-road','base_road'],['base-admin','base_admin']] as const)
      this.map.addLayer({id,type:'line',source,'source-layer':layer,...(id==='base-admin'?{}:{filter:linear}),
        paint:{'line-color':'#d5ded6','line-width':['interpolate',['linear'],['zoom'],9,0.55,18,2.1]}})
    this.map.addLayer({id:'place-labels',type:'symbol',source,'source-layer':'base_place',
      filter:['all',['has','label'],['!=',['get','label'],'']],layout:{
        'text-field':['get','label'],'text-font':['Microsoft YaHei UI'],'text-size':['interpolate',['linear'],['zoom'],9,10,16,14]},
      paint:{'text-color':'#40565c','text-halo-width':1}})
  }

  setTheme(theme: MapTheme) {
    this.theme=theme
    const palette=palettes[theme]
    for(const [id,key,value] of [
      ['background','background-color',palette.water],['land','fill-color',palette.land],
      ['land','fill-outline-color',palette.land],['base-green','fill-color',palette.green],
      ['base-water','fill-color',palette.water],['base-water-line','line-color',palette.water],
      ['base-buildings','fill-color',palette.built],['base-admin','line-color',palette.admin],
      ['base-road','line-color',palette.road],['place-labels','text-color',palette.label],
      ['place-labels','text-halo-color',palette.land],['station-labels','text-color',palette.label],
      ['station-labels','text-halo-color',palette.land],
    ]) if(this.map.getLayer(id)) this.map.setPaintProperty(id,key,value)
    for(const id of baseLayers) if(this.map.getLayer(id)) this.map.setLayoutProperty(id,'visibility',theme==='transport'?'none':'visible')
  }

  private metroColor(service: string, osmColor: string): ExpressionSpecification {
    const overrides=this.visualState.filter(item=>item.kind==='line' && item.value.color)
    const osm=['coalesce',['get',osmColor],colors.metro] as ExpressionSpecification
    const [first, ...rest] = overrides
    return first ? ['match',['get',service],first.id,first.value.color,...rest.flatMap(item=>[item.id,item.value.color]),osm] as ExpressionSpecification : osm
  }

  setTrains(trains: MapTrain[], selectedId?:string) {
    const source=this.map.getSource('trains') as GeoJSONSource | undefined
    source?.setData({type:'FeatureCollection',features:trains.map(train=>({
      type:'Feature',id:train.id,geometry:{type:'Point',coordinates:train.point},
      properties:{run:train.id,label:train.label,selected:train.id===selectedId,kind:train.kind,bearing:train.bearing??0},
    }))})
  }

  setTrainPath(geometry: Geometry | null) {
    const source=this.map.getSource('train-path') as GeoJSONSource | undefined
    source?.setData({type:'FeatureCollection',features:geometry?[{type:'Feature',geometry,properties:{}}]:[]})
  }

  focusTrain(point:[number,number], follow:boolean) {
    this.map.easeTo({center:point,zoom:follow?this.map.getZoom():Math.max(this.map.getZoom(),11),duration:follow?230:500})
  }

  fit(bounds: Bounds, maxZoom = 15) {
    const [west, south, east, north] = bounds
    this.map.fitBounds([[west, south], [east, north]], { padding: 65, maxZoom, duration: 440 })
  }

  view() {
    const bounds = this.map.getBounds()
    return {
      center: this.map.getCenter(),
      zoom: this.map.getZoom(),
      bounds: [Math.max(-180, bounds.getWest()), Math.max(-90, bounds.getSouth()),
        Math.min(180, bounds.getEast()), Math.min(90, bounds.getNorth())] as Bounds,
    }
  }

  setLayer(group: LayerGroup, visible: boolean) {
    this.visibility.set(group, visible)
    for (const id of layerIds[group]) {
      if (this.map.getLayer(id)) this.map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none')
    }
    this.updateEdits()
  }

  private selectFeature(feature?: MapGeoJSONFeature) {
    this.clearSelection()
    if (typeof feature?.id !== 'number') return
    this.selected = { source: feature.source, sourceLayer: feature.sourceLayer ?? undefined, id: feature.id }
    this.map.setFeatureState(this.selected, { selected: true })
  }

  selectSummary(item: Summary) {
    this.clearSelection()
    if (item.kind !== 'line' && this.map.getSource('transport')) {
      this.selected = this.visualState.some(visual => visual.render_id === item.render_id && visual.value.point)
        ? { source: editId, sourceLayer: undefined, id: item.render_id }
        : { source: 'transport', sourceLayer: vectorSources[item.kind], id: item.render_id }
      this.map.setFeatureState(this.selected, { selected: true })
    }
  }

  setVisuals(visuals: Visual[]) {
    if (!this.map.getSource('transport')) return
    const target = (visual: Visual) => ({ source: 'transport', sourceLayer: vectorSources[visual.kind as SelectableKind], id: visual.render_id })
    for (const old of this.visualState.filter(visual=>visual.kind!=='line')) {
      this.map.removeFeatureState(target(old), 'color')
      this.map.removeFeatureState(target(old), 'relocated')
      this.map.removeFeatureState(target(old), 'width')
      this.map.removeFeatureState(target(old), 'opacity')
    }
    this.visualState = visuals
    for (const visual of this.visualState.filter(visual=>visual.kind!=='line')) {
      const { color, point, width, opacity } = visual.value
      this.map.setFeatureState(target(visual), { ...(color && { color }), ...(point && { relocated: true }),
        ...(width!=null&&{width}),...(opacity!=null&&{opacity}) })
    }
    if(this.map.getLayer('metro')) this.map.setPaintProperty('metro','line-color',color(this.metroColor('service1','metro_color')))
    if(this.map.getLayer('metro-shared')) this.map.setPaintProperty('metro-shared','line-color',color(this.metroColor('service2','metro_secondary')))
    this.updateEdits()
  }

  private updateEdits() {
    const source = this.map.getSource(editId) as GeoJSONSource | undefined
    if (!source) return
    const features = this.visualState.flatMap(({ render_id, kind, value }): Feature<Point>[] => {
      if (!value.point || kind === 'line') return []
      const layer = kind === 'poi' ? 'station' : kind
      if (this.visibility.get(layer) === false) return []
      return [{ type: 'Feature', geometry: { type: 'Point', coordinates: value.point },
        properties: { rid: render_id, color: value.color || colors[kind] } }]
    })
    source.setData({ type: 'FeatureCollection', features })
  }

  clearSelection() {
    if (this.selected && this.map.getSource(this.selected.source)) this.map.removeFeatureState(this.selected, 'selected')
    this.selected = undefined
    this.setExact(null)
  }

  setExact(geometry: Geometry | null) {
    const source = this.map.getSource('exact') as GeoJSONSource | undefined
    source?.setData({ type: 'FeatureCollection', features: geometry ? [{ type: 'Feature', properties: {}, geometry }] : [] })
  }

  destroy() {
    this.generation++
    this.map.remove()
  }
}
