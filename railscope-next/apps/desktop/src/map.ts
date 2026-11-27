import { t } from './i18n'
import maplibre, { type ExpressionSpecification, type GeoJSONSource, type MapGeoJSONFeature } from 'maplibre-gl'
import type { Feature, Geometry, Point } from 'geojson'
import { type Bounds, type Kind, type Pack, type Summary, type Visual, tileTemplate } from './api'
import { defaultTrackViews, facilityStyles, lifecycle, lifecycles, metroColor, metroStationSymbols, nationalStationSymbols, nonRailStationNames, railPaint, siteIcon, sites, siteSymbol, stationColors, svgSymbol, trackProperty, type TrackViews } from './cartography'
import { defaultLayers, type Visibility, type LayerGroup } from './layers'

export type MapTheme = 'light' | 'color' | 'dark' | 'transport'
export type MapTrain = { id:string; label:string; point:[number,number]; bearing?:number; kind:'rail'|'metro' }
const colors = { rail: '#DF4244', metro: metroColor, road: '#9f771f', station: '#314C65', facility: '#627880', poi: '#9f771f' }
const vectorSources = {
  rail: 'rail', metro: 'metro', road: 'road', station: 'points', poi: 'points', facility: 'facilities',
}
const selection = '#3178C6'
type SelectableKind = Exclude<Kind, 'line'>

const trackLayers = (kind: 'rail' | 'metro') => Object.keys(lifecycles).map(life=>`${kind}-${life}`)
const constructionNames = ['rail-construction-names','metro-construction-names']
const lineLabels = ['rail-line-names','metro-line-names',...constructionNames]
const facilityLayers = Object.keys(facilityStyles).flatMap(key=>[`area-${key}`,`line-${key}`])
const stripes = ['metro-service-1','metro-service-2','metro-service-3']
const editId = 'workspace-points'
const stationSource = 'station-marks'
const stationModes = ['rail','metro'] as const
const stationLabels = stationModes.flatMap(mode=>[`${mode}-station-names`,`${mode}-edited-station-names`])
const contextGeography=['base-green','base-water','base-water-line','base-road']
const contextCities=['city-core','place-labels']
const contextIds=[...contextGeography,'base-admin',...contextCities].map(id=>`context-${id}`)
const baseLayers=['land','base-green','base-water','base-water-line','base-buildings','base-admin','base-road','city-core','place-labels','minor-places','local-road-names',...contextIds]
const labelLayers = [...lineLabels,'road-names',...stationLabels,'facility-labels','poi-labels','business-badges','technical-badges','city-core','place-labels','minor-places','local-road-names']
const trainKinds=['rail','metro'] as const
const trainMarkers=trainKinds.map(mode=>`${mode}-train-marker`)
const trainLabels=trainKinds.map(mode=>`${mode}-train-label`)
const overlays=[...trainMarkers,...trainLabels,'train-route','corridor-route','exact-point','exact-line','exact-area',editId]
const layerIds: Record<LayerGroup, string[]> = {
  geography:['land','base-green','base-water','base-water-line','base-buildings','base-road',...contextGeography.map(id=>`context-${id}`)],
  boundaries:['base-admin','context-base-admin'],
  cities:[...contextCities,...contextCities.map(id=>`context-${id}`)], villages:['minor-places'],localNames:['local-road-names'],localPoi:['poi-labels'],
  rail:trackLayers('rail').filter(id=>id!=='rail-construction'),railNames:['rail-line-names'],station:['rail-stations','rail-edited-stations'],railStationNames:['rail-station-names','rail-edited-station-names'],
  facility:[...facilityLayers,'facility-points','national-site-fill','national-site-outline','operating-site-fill','operating-site-outline'],
  technical:['facility-symbols','facility-labels','operating-site-symbols','operating-site-labels','technical-badges','business-badges'],signals:['signal-symbols'],relics:[],
  metro:[...trackLayers('metro').filter(id=>id!=='metro-construction'),...stripes],metroNames:['metro-line-names'],metroStations:['metro-stations','metro-edited-stations'],metroStationNames:['metro-station-names','metro-edited-station-names'],
  railConstruction:['rail-construction',constructionNames[0]],metroConstruction:['metro-construction',constructionNames[1]],
  interchanges:[],metroFacilities:['metro-entrances','metro-site-fill','metro-site-outline'],
  road:['road-plan','road'],roadNames:['road-names'],roadSites:['poi','poi-area'],
  route:['train-route'],corridor:['corridor-route'],railTrains:[trainMarkers[0],trainLabels[0]],metroTrains:[trainMarkers[1],trainLabels[1]],edit:['exact-point','exact-line','exact-area',editId],
}
const interactiveLayers = [editId,...Object.values(layerIds).flat().filter(id=>!baseLayers.includes(id)&&!overlays.includes(id)&&id!=='poi-labels').reverse()]
const siteSymbols=[...stationModes.flatMap(mode=>[`${mode}-stations`,`${mode}-edited-stations`]),...stationLabels,'operating-site-symbols','operating-site-labels']
const empty = { type: 'FeatureCollection', features: [] } as const
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

const businessLabel: ExpressionSpecification = ['match', ['get','label'],
  ['线路','轨道','道路','铁路线路','未命名线路'], '', ['coalesce',['get','label'],'']]

export class MapController {
  readonly map: maplibre.Map
  private ready: Promise<void>
  private generation = 0
  private visibility:Visibility = defaultLayers
  private visualState: Visual[] = []
  private selected: { source: string; sourceLayer?: string; id: number } | undefined
  private theme: MapTheme = 'light'
  private focusLine?:number
  private trackViews: TrackViews = defaultTrackViews
  private filterZoom = -1
  private packId?: string
  private following = false

  constructor(element: HTMLElement, onPick: (id: number | null) => void, onMove: () => void, onError: (error: string) => void,
    onTrainPick?: (id: string) => void, onManualMove?:()=>void) {
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
          'corridor-path': { type: 'geojson', data: { ...empty, features: [] } },
          trains: { type: 'geojson', data: { ...empty, features: [] } },
          [editId]: { type: 'geojson', promoteId: 'rid', data: { ...empty, features: [] } },
        },
        layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#cadde0' } }],
      },
      attributionControl: false,
    })
    this.ready = new Promise<void>(resolve => this.map.once('load', () => resolve())).then(async () => {
      await Promise.all(Object.entries(sites).map(([id, [,, graphic]]) => new Promise<void>((resolve, reject) => {
        const img = new Image()
        img.onload = () => { if (!this.map.hasImage(id)) this.map.addImage(id, img, {pixelRatio:2}); resolve() }
        img.onerror = () => reject(new Error(t("无法载入地图符号 · {0}", id)))
        img.src = svgSymbol(graphic)
      })))
    })
    this.map.on('zoom', () => this.setZoomFilters())
    this.map.addControl(new maplibre.AttributionControl({ compact: true }), 'bottom-right')
    this.map.addControl(new maplibre.NavigationControl({ showCompass: false }), 'top-right')
    this.map.addControl(new maplibre.ScaleControl({ unit: 'metric' }), 'bottom-left')
    this.map.on('click', event => {
      const { x, y } = event.point
      const trainLayers=trainMarkers.filter(id=>this.map.getLayer(id))
      if (trainLayers.length) {
        const train = this.map.queryRenderedFeatures([[x-6,y-6],[x+6,y+6]], {layers:trainLayers})[0]
        if (typeof train?.properties.run === 'string') {onTrainPick?.(train.properties.run); return}
      }
      const hits = this.map.queryRenderedFeatures([[x - 5, y - 5], [x + 5, y + 5]], {
        layers: interactiveLayers.filter(id => this.map.getLayer(id)),
      })
      const eligible=hits.filter(hit=>typeof hit.id==='number'&&!hit.state?.relocated)
      const picked = eligible.find(hit=>hit.layer.id===editId||siteSymbols.includes(hit.layer.id))
        ??eligible.find(hit=>hit.layer.type!=='fill')??eligible[0]
      this.selectFeature(picked)
      onPick(typeof picked?.id === 'number' ? picked.id : null)
    })
    this.map.on('moveend', onMove)
    this.map.on('movestart',event=>{
      if(!event.originalEvent)return
      this.following=false
      onManualMove?.()
    })
    this.map.on('dragstart',()=>{
      this.following=false
      onManualMove?.()
    })
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
    for (const id of new Set(Object.values(layerIds).flat())) {
      if (this.map.getLayer(id)) this.map.removeLayer(id)
    }
    for (const id of ['transport', 'base', 'context', stationSource]) {
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
    this.map.addSource(stationSource, {
      type:'vector', tiles:[`${tileTemplate(pack.snapshot)}/stations`], minzoom:9, maxzoom:22, promoteId:'rid',
    })
    if (pack.source_schema >= 3) {
      this.map.addSource('context', {
        type:'vector', tiles:[`${tileTemplate(pack.snapshot)}/context`], minzoom:3, maxzoom:8,
        attribution:'© OpenStreetMap contributors · ODbL 1.0',
      })
      this.addBaseLayers('context',3,overview?22:9)
    }
    this.addBaseLayers('transport',pack.source_schema>=3?9:0)
    const planned = ['in', ['get', 'status'], ['literal', ['construction', 'planned', 'disused']]]
    for (const group of ['road'] as const) {
      const base = { type: 'line', source: 'transport', 'source-layer': group, layout: { 'line-cap': 'round', 'line-join': 'round' } } as const
      this.map.addLayer({
        ...base, id: `${group}-plan`, filter: planned as ExpressionSpecification,
        paint: { 'line-color': color(colors[group]), 'line-width': ['coalesce',['feature-state','width'],1.35], 'line-dasharray': [2, 2],
          'line-opacity':['coalesce',['feature-state','opacity'],0.65] },
      })
      this.map.addLayer({
        ...base, id: group, filter: ['!', planned as ExpressionSpecification],
        paint: {
          'line-color': color(colors[group]),
          'line-width': ['interpolate', ['linear'], ['zoom'],
            5,['coalesce',['feature-state','width'],0.65],10,['coalesce',['feature-state','width'],1.35],
            14,['coalesce',['feature-state','width'],2.35],18,['coalesce',['feature-state','width'],4]],
          'line-opacity': ['coalesce',['feature-state','opacity'],0.63],
        },
      })
    }
    this.addRailLayers()
    this.addSiteLayers()
    this.addBusinessLabels()
    this.map.moveLayer('city-core')
    if(this.map.getLayer('context-city-core')) this.map.moveLayer('context-city-core')
    this.addStationLayers()
    this.setFocusLine(this.focusLine)
    this.map.addLayer({ id: editId, source: editId, type: 'circle', filter:['!', ['has','mode']],
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
    this.map.addLayer({id:'corridor-route',source:'corridor-path',type:'line',
      layout:{'line-cap':'round','line-join':'round'},
      paint:{'line-color':'#26798c','line-width':4.25,'line-opacity':.83}})
    for(const [i,mode] of trainKinds.entries()){
      const filter:ExpressionSpecification=['==',['get','kind'],mode]
      this.map.addLayer({id:trainMarkers[i],source:'trains',type:'circle',filter,
        layout:{'circle-sort-key':['case',['boolean',['get','selected'],false],1,0]},paint:{
          'circle-radius':['case',['boolean',['get','selected'],false],7,4.5],
          'circle-color':mode==='metro'?'#008573':'#7553cc','circle-stroke-color':'#fff','circle-stroke-width':1.5}})
      this.map.addLayer({id:trainLabels[i],source:'trains',type:'symbol',minzoom:8,filter,layout:{
        'text-field':['get','label'],'text-font':['Microsoft YaHei UI'],'text-size':12,'text-offset':[0,1.15],
        'symbol-sort-key':['case',['boolean',['get','selected'],false],0,1]},
        paint:{'text-color':mode==='metro'?'#174740':'#322359','text-halo-color':'#fff','text-halo-width':1.1}})
    }
    this.setTheme(this.theme)
    this.setTrackViews(this.trackViews)
    this.refreshVisibility()
    if(changingPack) this.fit(pack.bounds, pack.region === '全国' ? 6 : 11)
    else if(picked && this.map.getSource(picked.source)) this.map.setFeatureState(picked,{selected:true})
  }

  private addRailLayers() {
    const width = ['coalesce',['feature-state','width'],trackProperty(2)] as ExpressionSpecification
    for (const kind of ['rail','metro'] as const) {
      for (const [life,[,alpha,minzoom,dash]] of Object.entries(lifecycles)) {
        this.map.addLayer({id:`${kind}-${life}`,type:'line',source:'transport','source-layer':kind,minzoom,
          layout:{'line-cap':'round','line-join':'round'},paint:{
            'line-color':color(kind==='rail'?railPaint(this.trackViews.rail):trackProperty(1)),
            'line-width':['interpolate',['linear'],['zoom'],6,['*',.55,width],10,['*',.75,width],14,width,17,['*',1.15,width],20,['*',1.25,width]],
            'line-opacity':['coalesce',['feature-state','opacity'],alpha],
            ...(dash ? {'line-dasharray':[...dash]} : {}),
          }})
      }
    }
    stripes.forEach((id,index)=>this.map.addLayer({id,type:'line',source:'transport','source-layer':'metro',minzoom:9,
      filter:['all',['==',lifecycle,'active'],['has',`service${index+1}`]],layout:{'line-cap':'round','line-join':'round'},
      paint:{'line-color':color(this.metroColor(`service${index+1}`,['metro_color','metro_secondary','metro_tertiary'][index])),
        'line-offset':['case',['>=',['to-number',['get','service_count']],3],[-3.5,0,3.5][index],
          ['==',['to-number',['get','service_count']],2],[-1.75,1.75,0][index],0],
        'line-width':['case',['>', ['to-number',['get','service_count']],1],2.6,3],
      }}))
  }

  private addSiteLayers() {
    const polygon = ['==',['geometry-type'],'Polygon'] as ExpressionSpecification
    const point = ['==',['geometry-type'],'Point'] as ExpressionSpecification
    const isStation = ['in',siteSymbol,['literal',[...nationalStationSymbols,...metroStationSymbols]]] as ExpressionSpecification
    const isNonRailStation = ['in',siteSymbol,['literal',Object.keys(nonRailStationNames)]] as ExpressionSpecification
    const isOtherPlace:ExpressionSpecification = ['all',['!',isStation],['!=',['get','station_duplicate'],1]]
    const operatingSite = ['in',['get','site_role'],['literal',['junction','marshalling','yard','depot']]] as ExpressionSpecification
    const relocated = ['case',['boolean',['feature-state','relocated'],false],0,1] as ExpressionSpecification
    for (const [key, style] of Object.entries(facilityStyles)) {
      const filter:ExpressionSpecification=['all',['==',siteSymbol,key],['!',operatingSite]]
      this.map.addLayer({id:`area-${key}`,type:'fill',source:'transport','source-layer':'facilities',minzoom:style.zoom,
        filter:['all',filter,polygon],paint:{'fill-color':color(style.fill),'fill-opacity':style.opacity}})
      this.map.addLayer({id:`line-${key}`,type:'line',source:'transport','source-layer':'facilities',minzoom:style.zoom,
        filter:['all',filter,key==='station_area'?polygon:['in',['geometry-type'],['literal',['LineString','Polygon']]]],
        paint:{'line-color':color(style.outline),'line-width':key==='platform'?['case',polygon,1,4]:1,
          ...(key==='station_area'?{'line-dasharray':[4,3]}:{})}})
    }
    const siteAreas:[string,'points'|'facilities',ExpressionSpecification][]=[
      ['national-site','points',['all',polygon,['has','site_role'],['!=',['get','site_role'],'metro']]],
      ['metro-site','points',['all',polygon,['==',['get','site_role'],'metro']]],
      ['operating-site','facilities',['all',polygon,operatingSite]],
    ]
    for (const [id,layer,filter] of siteAreas) {
      const base={source:'transport','source-layer':layer,minzoom:id==='operating-site'?12:13,filter}
      const tint=id==='metro-site'?'#238c81':id==='operating-site'?'#af763d':'#3d75a7'
      this.map.addLayer({...base,id:`${id}-fill`,type:'fill',paint:{'fill-color':color(tint),'fill-opacity':.18}})
      this.map.addLayer({...base,id:`${id}-outline`,type:'line',paint:{'line-color':color(tint),'line-width':1.3}})
    }
    this.map.addLayer({id:'facility-points',type:'circle',source:'transport','source-layer':'facilities',minzoom:15,
      filter:['all',point,['==',siteSymbol,'platform']],paint:{'circle-color':color(facilityStyles.platform.outline),'circle-radius':3,'circle-opacity':relocated}})
    this.map.addLayer({id:'poi-area',type:'fill',source:'transport','source-layer':'points',minzoom:11,
      filter:['all',polygon,isOtherPlace,['!',isNonRailStation]],paint:{'fill-color':color(colors.poi),'fill-opacity':.26}})
    this.map.addLayer({id:'poi',type:'circle',source:'transport','source-layer':'points',minzoom:12,
      filter:['all',point,isOtherPlace,['!',isNonRailStation]],paint:{'circle-color':color(colors.poi),'circle-radius':3.5,
        'circle-stroke-color':'#fff','circle-stroke-width':1,'circle-opacity':relocated,'circle-stroke-opacity':relocated}})
    for (const id of ['facility-symbols','operating-site-symbols','metro-entrances','signal-symbols']) {
      const scale = ['/', ['coalesce',['get','size_pct'],100], 100] as ExpressionSpecification
      this.map.addLayer({id,type:'symbol',source:'transport','source-layer':'facilities',minzoom:id==='signal-symbols'?17:id==='operating-site-symbols'?11:6,
        ...(id==='operating-site-symbols'?{filter:operatingSite}:{}),
        layout:{'icon-image':siteIcon,'icon-size':['interpolate',['linear'],['zoom'],6,['*',.55,scale],14,scale,18,['*',1.05,scale]],
          'icon-allow-overlap':false,'icon-padding':1},
        paint:{'icon-opacity':relocated}})
    }
    for (const [id,layer,field,minzoom, filter] of [
      ['poi-labels','points','label',13,isOtherPlace],
      ['facility-labels','facilities','label',11,['in',siteSymbol,['literal',['switch_detail','railway_crossing_detail']]]],
      ['operating-site-labels','facilities','label',11,operatingSite],
    ] as const) {
      this.map.addLayer({id,type:'symbol',source:'transport','source-layer':layer,minzoom,
        filter:['all',filter as ExpressionSpecification,['has',field]],layout:{'text-field':['get',field],'text-font':['Microsoft YaHei UI'],
          'text-size':field==='label'?12:10,
          ...(field==='label'?{'text-variable-anchor':['top','bottom','right','left'],'text-radial-offset':.85,'text-padding':4}:{'text-anchor':'left','text-offset':[1,0]}),
          'symbol-sort-key':['coalesce',['get','rank'],4]},
        paint:{'text-color':'#193941','text-halo-color':'#eef0e7','text-halo-width':1.2,'text-opacity':relocated}})
    }
  }

  private addStationLayers() {
    for (const mode of stationModes) for (const moved of [false,true]) {
      const prefix = moved ? `${mode}-edited` : mode
      const source = moved ? {source:editId} : {source:stationSource,'source-layer':'stations'}
      const matchMode:ExpressionSpecification = ['==',['get','mode'],mode]
      const tint = stationColors[mode]
      const relocated:number|ExpressionSpecification = moved ? 1 :
        ['case',['boolean',['feature-state','relocated'],false],0,1]
      this.map.addLayer({id:`${prefix}-stations`,...source,type:'circle',minzoom:9,filter:matchMode,
        paint:{'circle-radius':4,'circle-color':'#fff','circle-stroke-width':1.8,
          'circle-stroke-color':moved?color(['coalesce',['get','color'],tint]):color(tint),
          'circle-opacity':relocated,'circle-stroke-opacity':relocated}})
      this.map.addLayer({id:`${prefix}-station-names`,...source,type:'symbol',minzoom:9,filter:matchMode,
        layout:{'text-field':['get','label'],'text-font':['Microsoft YaHei UI'],'text-size':12,
          'text-variable-anchor':['top','bottom','right','left'],'text-radial-offset':1,'text-padding':4,
          'symbol-sort-key':['coalesce',['get','rank'],4]},
        paint:{'text-color':'#193a51','text-halo-color':'#f9fcfc','text-halo-width':1.25,'text-opacity':relocated}})
    }
    for (const [id,field,minzoom] of [['business-badges','business',15],['technical-badges','technical',13]] as const) {
      this.map.addLayer({id,type:'symbol',source:stationSource,'source-layer':'stations',minzoom,
        filter:['has',field],layout:{'text-field':['get',field],'text-font':['Microsoft YaHei UI'],
          'text-size':10,'text-anchor':'left','text-offset':[1,0]},
        paint:{'text-color':'#193a51','text-halo-color':'#f9fcfc','text-halo-width':1.2,
          'text-opacity':['case',['boolean',['feature-state','relocated'],false],0,1]}})
    }
  }

  private setZoomFilters(force = false) {
    const zoom = Math.floor(this.map.getZoom())
    if ((!force && zoom === this.filterZoom) || !this.map.getLayer('rail-active')) return
    this.filterZoom = zoom
    for (const kind of ['rail','metro'] as const) for (const life of Object.keys(lifecycles)) {
      const filters: ExpressionSpecification = ['all',['==',lifecycle,life],['<=',trackProperty(3),zoom]]
      if (kind==='metro' && life==='active' && this.trackViews.metro==='lines' && zoom>=9) filters.push(['!', ['has','service1']])
      this.map.setFilter(`${kind}-${life}`,filters)
    }
    const keys = Object.entries(sites).filter(([,site])=>site[1]<=zoom).map(([key])=>key)
    const railKeys=nationalStationSymbols
    const metroKeys=metroStationSymbols
    const moved=this.visualState.flatMap(item=>item.station&&item.value.point?[item.station.render_id]:[])
    for (const mode of stationModes) {
      const filter:ExpressionSpecification = ['all',['==',['get','mode'],mode],['<=',['get','min_z'],zoom]]
      if(moved.length)filter.push(['!',['in',['get','rid'],['literal',moved]]])
      this.map.setFilter(`${mode}-stations`,filter)
      const labels:ExpressionSpecification = ['all',filter]
      if (mode==='metro' && this.visibility.railStationNames) {
        const adjusted=this.visualState.flatMap(item=>item.station&&(item.value.name||item.value.point)?[item.station.render_id]:[])
        const preferred:ExpressionSpecification = ['any',['!',['has','rail_peer']],['>',['get','rail_peer_z'],zoom]]
        if(adjusted.length)preferred.push(['in',['get','rid'],['literal',adjusted]],['in',['get','rail_peer'],['literal',adjusted]])
        labels.push(preferred)
      }
      this.map.setFilter(`${mode}-station-names`,labels)
    }
    const detailKeys=keys.filter(key=>!['signal_detail','subway_entrance',...railKeys,...metroKeys].includes(key))
    this.map.setFilter('facility-symbols',['all',['==',['geometry-type'],'Point'],['!', ['has','site_role']],['in',siteSymbol,['literal',detailKeys]]])
    this.map.setFilter('facility-labels',['all',['!', ['has','site_role']],['in',siteSymbol,['literal',detailKeys]],['has','label']])
    this.map.setFilter('metro-entrances',['all',['==',['geometry-type'],'Point'],['==',siteSymbol,'subway_entrance']])
    this.map.setFilter('signal-symbols',['all',['==',siteSymbol,'signal_detail'],['==',['geometry-type'],'Point']])
  }

  private addBusinessLabels(){
    for(const [id,layer,minzoom] of [['rail-line-names','rail_names',8],['metro-line-names','metro_names',10],
      [constructionNames[0],'rail_names',8],[constructionNames[1],'metro_names',10],['road-names','road_names',10]] as const){
      const construction = constructionNames.includes(id)
      this.map.addLayer({id,type:'symbol',source:'transport','source-layer':layer,minzoom,
        ...(layer!=='road_names'?{filter:['==',['coalesce',['get','construction'],0],Number(construction)] as ExpressionSpecification}:{}),
        layout:{'symbol-placement':'line-center','text-field':businessLabel,'text-font':['Microsoft YaHei UI'],
          'text-size':id==='rail-line-names'?12:11,'text-max-angle':35,'text-padding':11,'text-offset':[0,-.4],
          'text-keep-upright':true,'symbol-sort-key':['coalesce',['get','rank'],3]},
        paint:{'text-color':construction?'#805425':'#263e53','text-halo-color':'#f9fcfc','text-halo-width':1.75}})
    }
  }

  setFocusLine(renderId?:number){
    this.focusLine=renderId
    for(const id of [...lineLabels,'road-names','rail-station-names','metro-station-names']){
      if(!this.map.getLayer(id))continue
      const associated:ExpressionSpecification=id.includes('station')
        ?['>=',['index-of',`,${renderId},`,['coalesce',['get','line_rids'],'']],0]
        :['==',['get','rid'],renderId??0]
      const base:ExpressionSpecification=['coalesce',['get','rank'],4]
      this.map.setLayoutProperty(id,'symbol-sort-key',renderId?['case',associated,-1,base]:base)
    }
  }

  setTrackViews(views: TrackViews) {
    this.trackViews = views
    for (const id of trackLayers('rail')) {
      if (this.map.getLayer(id)) this.map.setPaintProperty(id,'line-color',color(railPaint(views.rail)))
    }
    this.setZoomFilters(true)
    this.refreshVisibility()
  }

  private addBaseLayers(source:'transport'|'context',min:number,maxzoom=22) {
    const context=source==='context'
    const layerId=(id:string)=>context?`context-${id}`:id
    const polygon=['==',['geometry-type'],'Polygon'] as ExpressionSpecification
    const linear=['==',['geometry-type'],'LineString'] as ExpressionSpecification
    for (const [id,layer] of [['base-green','base_green'],['base-water','base_water'],['base-buildings','base_building']] as const) {
      if(context&&id==='base-buildings')continue
      this.map.addLayer({id:layerId(id),type:'fill',source,'source-layer':layer,minzoom:min,maxzoom,filter:polygon,paint:{'fill-color':'#d5ded6'}})
    }
    for (const [id,layer] of [['base-water-line','base_water'],['base-road','base_road'],['base-admin','base_admin']] as const)
      this.map.addLayer({id:layerId(id),type:'line',source,'source-layer':layer,minzoom:min,maxzoom,...(id==='base-admin'?{}:{filter:linear}),
        paint:{'line-color':'#d5ded6','line-width':['interpolate',['linear'],['zoom'],5,0.55,9,0.9,18,2.1]}})
    const major=['in',['get','class'],['literal',['city','county','province','state','borough','district']]] as ExpressionSpecification
    const name=['all',['has','label'],['!=',['get','label'],'']] as ExpressionSpecification
    for(const [id,minzoom,test] of [['place-labels',context?3:5,['all',major,['!=',['get','class'],'city']]],['minor-places',11,['!',major]]] as const){
      if(context&&id==='minor-places')continue
      this.map.addLayer({id:layerId(id),type:'symbol',source,'source-layer':'base_place',minzoom:Math.max(min,minzoom),maxzoom,
        filter:['all',name,test as ExpressionSpecification],layout:{
          'text-field':['get','label'],'text-font':['Microsoft YaHei UI'],
          'symbol-sort-key':['match',['get','class'],'city',0,'province',1,'county',2,4],
          'text-size':id==='place-labels'?['match',['get','class'],'city',16,'province',14,12]:10},
        paint:{'text-color':'#586d7e','text-halo-width':1.15}})
    }
    this.map.addLayer({id:layerId('city-core'),type:'symbol',source,'source-layer':'base_place',minzoom:Math.max(min,context?3:5),maxzoom,
      filter:['all',name,['==',['get','class'],'city']],layout:{
        'text-field':['get','label'],'text-font':['Microsoft YaHei UI'],'text-size':16,
        'text-variable-anchor':['top','bottom','right','left'],'text-radial-offset':.6,'text-padding':4},
      paint:{'text-color':'#586d7e','text-halo-width':1.25}})
    if(!context) this.map.addLayer({id:'local-road-names',type:'symbol',source,'source-layer':'base_road',minzoom:12,filter:name,
      layout:{'symbol-placement':'line','symbol-spacing':315,'text-field':['get','label'],'text-font':['Microsoft YaHei UI'],'text-size':10},
      paint:{'text-color':'#657482','text-halo-width':1}})
  }

  setTheme(theme: MapTheme) {
    this.theme=theme
    const palette=palettes[theme]
    for(const [id,key,value] of [
      ['background','background-color',palette.water],['land','fill-color',palette.land],
      ['land','fill-outline-color',palette.land],['base-green','fill-color',palette.green],
      ['base-water','fill-color',palette.water],['base-water-line','line-color',palette.water],
      ['base-buildings','fill-color',palette.built],['base-admin','line-color',palette.admin],
      ['base-road','line-color',palette.road],
      ...labelLayers.flatMap(id=>[
        [id,'text-color',theme==='dark'?palette.label:id.includes('line-names')?'#2c4559':id.endsWith('station-names')?'#193a51':palette.label],
        [id,'text-halo-color',theme==='dark'?palette.land:'#fbfdfd'],
      ]),
    ]) for(const target of [id,`context-${id}`]) {
      if(this.map.getLayer(target)) this.map.setPaintProperty(target,key,value)
    }
    this.refreshVisibility()
  }

  private metroColor(service: string, osmColor: string): ExpressionSpecification {
    const overrides=this.visualState.filter(item=>item.kind==='line' && item.value.color)
    const osm=['coalesce',['get',osmColor],trackProperty(1)] as ExpressionSpecification
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
    this.following=follow
    if(follow&&this.map.project(point).dist(this.map.project(this.map.getCenter()))<0.5)return
    this.map.easeTo({center:point,zoom:follow?this.map.getZoom():Math.max(this.map.getZoom(),11),
      duration:follow?210:500,...(follow?{easeId:'train-follow'}:{})})
  }

  stopFollowing(){
    if(this.following)this.map.stop()
    this.following=false
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

  setLayers(visibility: Visibility) {
    this.visibility=visibility
    this.setZoomFilters(true)
    this.refreshVisibility()
    this.updateEdits()
  }

  private refreshVisibility() {
    if (this.map.getLayer('metro-stations')) {
      this.map.setPaintProperty('metro-stations','circle-radius',this.visibility.interchanges
        ? ['case',['==',['get','interchange'],1],6,4] : 4)
    }
    for (const [group, ids] of Object.entries(layerIds) as [LayerGroup,string[]][]) for (const id of ids) {
      if (!this.map.getLayer(id)) continue
      let visible = this.visibility[group]
      if(baseLayers.includes(id)&&this.theme==='transport')visible=false
      if (/-abandoned$|-razed$/.test(id)) visible &&= this.visibility.relics
      if (stripes.includes(id)) visible &&= this.trackViews.metro==='lines'
      if (id.endsWith('-badges')) visible &&= this.trackViews.rail==='technical'
      const value=visible?'visible':'none'
      if ((this.map.getLayoutProperty(id,'visibility')??'visible')!==value) this.map.setLayoutProperty(id,'visibility',value)
    }
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
      const visual = this.visualState.find(value=>value.render_id===item.render_id)
      const id = visual?.station?.render_id ?? item.render_id
      this.selected = visual?.value.point
        ? { source: editId, id }
        : item.kind==='station'
          ? { source:stationSource,sourceLayer:'stations',id }
          : { source:'transport',sourceLayer:vectorSources[item.kind],id }
      this.map.setFeatureState(this.selected, { selected: true })
    }
  }

  setVisuals(visuals: Visual[]) {
    if (!this.map.getSource('transport')) return
    const target = (visual: Visual) => ({ source: 'transport', sourceLayer: vectorSources[visual.kind as SelectableKind], id: visual.render_id })
    const marker = (id:number) => ({source:stationSource,sourceLayer:'stations',id})
    for (const old of this.visualState.filter(visual=>visual.kind!=='line')) {
      this.map.removeFeatureState(target(old), 'color')
      this.map.removeFeatureState(target(old), 'relocated')
      this.map.removeFeatureState(target(old), 'width')
      this.map.removeFeatureState(target(old), 'opacity')
      if(old.station) {
        this.map.removeFeatureState(marker(old.station.render_id),'color')
        this.map.removeFeatureState(marker(old.station.render_id),'relocated')
      }
    }
    const stationVisuals = new Map<number,Visual>()
    for(const visual of visuals) {
      const id=visual.station?.render_id
      if(id!=null&&(!stationVisuals.has(id)||id===visual.render_id))stationVisuals.set(id,visual)
    }
    this.visualState = [...visuals.filter(visual=>!visual.station),...stationVisuals.values()]
    for (const visual of this.visualState.filter(visual=>visual.kind!=='line')) {
      const { color, point, width, opacity } = visual.value
      this.map.setFeatureState(target(visual), { ...(color && { color }), ...(point && { relocated: true }),
        ...(width!=null&&{width}),...(opacity!=null&&{opacity}) })
      if(visual.station)this.map.setFeatureState(marker(visual.station.render_id), {color:color||null,relocated:!!point})
    }
    stripes.forEach((id,index)=>{
      if(this.map.getLayer(id)) this.map.setPaintProperty(id,'line-color',color(this.metroColor(`service${index+1}`,['metro_color','metro_secondary','metro_tertiary'][index])))
    })
    const names=this.visualState.filter(item=>item.value.name)
    for(const id of [...lineLabels,'road-names','rail-station-names','metro-station-names','facility-labels','operating-site-labels','poi-labels']){
      if(!this.map.getLayer(id))continue
      const fallback:ExpressionSpecification=lineLabels.includes(id)||id==='road-names'?businessLabel:['get','label']
      const stationLabel=id==='rail-station-names'||id==='metro-station-names'
      const applicable=names.filter(item=>!!item.station===stationLabel)
      this.map.setLayoutProperty(id,'text-field',applicable.length?['match',['get','rid'],...applicable.flatMap(item=>[item.station?.render_id??item.render_id,item.value.name]),fallback]:fallback)
    }
    this.setZoomFilters(true)
    this.updateEdits()
  }

  private updateEdits() {
    const source = this.map.getSource(editId) as GeoJSONSource | undefined
    if (!source) return
    const features = this.visualState.flatMap(({ render_id, kind, value, station }): Feature<Point>[] => {
      if (!value.point || kind === 'line') return []
      const layer = kind === 'poi' ? 'roadSites' : kind
      if (!station && !this.visibility[layer]) return []
      return [{ type: 'Feature', geometry: { type: 'Point', coordinates: value.point },
        properties: station
          ? {rid:station.render_id, mode:station.mode,label:value.name||station.name,...(value.color&&{color:value.color})}
          : {rid:render_id,color:value.color||colors[kind]} }]
    })
    source.setData({ type: 'FeatureCollection', features })
  }

  clearSelection() {
    if (this.selected && this.map.getSource(this.selected.source)) this.map.removeFeatureState(this.selected, 'selected')
    this.selected = undefined
    this.setFocusLine()
    this.setExact(null)
  }

  setExact(geometry: Geometry | null) {
    const source = this.map.getSource('exact') as GeoJSONSource | undefined
    source?.setData({ type: 'FeatureCollection', features: geometry ? [{ type: 'Feature', properties: {}, geometry }] : [] })
  }

  setCorridorPath(geometry:Geometry|null){
    const source=this.map.getSource('corridor-path') as GeoJSONSource|undefined
    source?.setData({type:'FeatureCollection',features:geometry?[{type:'Feature',properties:{},geometry}]:[]})
    if(geometry?.type!=='GeometryCollection')return
    const bounds:Bounds=[180,90,-180,-90]
    for(const segment of geometry.geometries){
      if(segment.type!=='LineString')continue
      for(const [x,y] of segment.coordinates){
        bounds[0]=Math.min(bounds[0],x);bounds[1]=Math.min(bounds[1],y)
        bounds[2]=Math.max(bounds[2],x);bounds[3]=Math.max(bounds[3],y)
      }
    }
    if(bounds[0]<=bounds[2])this.fit(bounds,15)
  }

  destroy() {
    this.generation++
    this.map.remove()
  }
}
