import maplibre, { type ExpressionSpecification, type GeoJSONSource, type MapGeoJSONFeature } from 'maplibre-gl'
import type { Feature, Geometry, Point } from 'geojson'
import { type Bounds, type Kind, type Pack, type Summary, type Visual, tileTemplate } from './api'

export type LayerGroup = 'rail' | 'metro' | 'road' | 'station' | 'facility'
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
  rail: ['rail-plan', 'rail'], metro: ['metro-plan', 'metro'], road: ['road-plan', 'road'],
  station: ['poi', 'station', 'station-labels'], facility: ['area', 'facility-lines', 'facility-points'],
}
const editId = 'workspace-points'
const interactiveLayers = [editId, ...Object.values(layerIds).flat().reverse()]
const empty = { type: 'FeatureCollection', features: [] } as const

function color(defaultColor: string): ExpressionSpecification {
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

  constructor(element: HTMLElement, onPick: (id: number | null) => void, onMove: () => void, onError: (error: string) => void) {
    this.map = new maplibre.Map({
      container: element, center: [106, 33], zoom: 3.7, maxZoom: 19,
      dragRotate: false, pitchWithRotate: false, renderWorldCopies: false,
      maxTileCacheSize: 144, maxTileCacheZoomLevels: 4,
      localIdeographFontFamily: 'Microsoft YaHei UI',
      style: {
        version: 8,
        sources: {
          exact: { type: 'geojson', data: { ...empty, features: [] } },
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

  async setPack(pack: Pack) {
    const generation = ++this.generation
    await this.ready
    if (generation !== this.generation) return
    this.clearSelection()
    this.visualState = []
    this.updateEdits()
    for (const id of ['exact-point', 'exact-line', 'exact-area', ...interactiveLayers, 'land']) {
      if (this.map.getLayer(id)) this.map.removeLayer(id)
    }
    for (const id of ['transport', 'base']) {
      if (this.map.getSource(id)) this.map.removeSource(id)
    }
    const tiles = [tileTemplate(pack.snapshot)]
    if (pack.basemap) {
      this.map.addSource('base', {
        type: 'vector', tiles, minzoom: 0, maxzoom: 7,
        attribution: 'Natural Earth',
      })
      this.map.addLayer({ id: 'land', type: 'fill', source: 'base', 'source-layer': 'land',
        paint: { 'fill-color': '#eef0e7', 'fill-outline-color': '#eef0e7' } })
    }
    this.map.addSource('transport', {
      type: 'vector', tiles, minzoom: pack.min_zoom, maxzoom: pack.max_zoom, promoteId: 'rid',
      attribution: '© OpenStreetMap contributors · ODbL 1.0',
    })
    const planned = ['in', ['get', 'status'], ['literal', ['construction', 'planned', 'disused']]]
    for (const group of ['road', 'rail', 'metro'] as const) {
      const base = { type: 'line', source: 'transport', 'source-layer': group, layout: { 'line-cap': 'round', 'line-join': 'round' } } as const
      this.map.addLayer({
        ...base, id: `${group}-plan`, filter: planned as ExpressionSpecification,
        paint: { 'line-color': color(colors[group]), 'line-width': 1.35, 'line-dasharray': [2, 2], 'line-opacity': 0.65 },
      })
      this.map.addLayer({
        ...base, id: group, filter: ['!', planned as ExpressionSpecification],
        paint: {
          'line-color': color(colors[group]),
          'line-width': ['interpolate', ['linear'], ['zoom'], 5, 0.65, 10, 1.35, 14, 2.35, 18, 4],
          'line-opacity': group === 'road' ? 0.63 : 0.88,
        },
      })
    }
    this.map.addLayer({
      id: 'area', type: 'fill', source: 'transport', 'source-layer': 'facilities',
      filter: ['==', ['geometry-type'], 'Polygon'],
      paint: { 'fill-color': color(colors.facility), 'fill-opacity': 0.35 },
    })
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
    for (const [group, visible] of this.visibility) this.setLayer(group, visible)
    this.fit(pack.bounds, pack.region === '全国' ? 6 : 11)
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
    for (const old of this.visualState) {
      this.map.removeFeatureState(target(old), 'color')
      this.map.removeFeatureState(target(old), 'relocated')
    }
    this.visualState = visuals.filter(visual => visual.kind !== 'line')
    for (const visual of this.visualState) {
      const { color, point } = visual.value
      this.map.setFeatureState(target(visual), { ...(color && { color }), ...(point && { relocated: true }) })
    }
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
