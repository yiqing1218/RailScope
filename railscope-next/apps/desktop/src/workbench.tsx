import { t, useLanguage } from './i18n'
import { Activity, ArrowUpRight, Check, ChevronRight, CircleHelp, Compass, Crosshair, Database, FileText, Map as MapIcon,
  MapPin, Maximize2, PanelBottom, PanelLeft, PanelRight, Redo2, Route as RouteIcon, Save, Settings as SettingsIcon, TrainFront, Undo2, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { createPortal } from 'react-dom'
import { api, type Bounds, type BusinessLineList, type Conflict, type Inspection, type Override, type Pack, type RawSource, type Route, type Stats, type Summary, type Visual, type Workspace } from './api'
import { Catalog, PackInstall, PackMenu, className, kindNames, statusName } from './catalog'
import { MapController, type MapTheme } from './map'
import { LayerTree, defaultLayers as defaults, type LayerGroup, type Visibility } from './layers'
import { metrics, recordQuery, recordSelection, startMetrics } from './metrics'
import { OperationsWorkbench, type OperationsHandle } from './operations-workbench'
import { chinaDate, opsApi, type ControlPoint, type LegacyIssue, type Level } from './operations-api'
import { facilityStyles, lifecycles, missingColor, nonRailStationNames, readTrackViews, siteAreaNames, siteRoleNames, sites, svgSymbol, symbolName, thematic, tracks, type TrackViews } from './cartography'
import { Settings, themes, type SettingsSection } from './settings'
import { LanguageSelect, Welcome } from './welcome'

const visibilityKey = 'railscope-next.layers'
type Mode = 'detail' | 'operations' | 'analysis' | 'data'
const modes: {id:Mode; title:string; description:string; icon:typeof MapIcon}[] = [
  {id:'detail',title:'地图',description:'浏览目录、地图和对象属性。',icon:MapIcon},
  {id:'operations',title:'运行',description:'编排通道、时刻与列车仿真。',icon:TrainFront},
  {id:'analysis',title:'分析与诊断',description:'查询轨道连通性和数据诊断。',icon:Activity},
  {id:'data',title:'数据',description:'管理离线快照、来源与迁移。',icon:Database},
]
const layoutDefaults = {left:310,right:343,bottom:320,hideLeft:false,hideRight:false,hideBottom:true,focusBottom:false}
type Layout = typeof layoutDefaults
const defaultLayout = (mode:Mode):Layout => mode==='operations'?{...layoutDefaults,left:248,right:350}:layoutDefaults
function layoutPreferences(): Record<Mode,Layout> {
  let saved:Record<string,Partial<Layout>> = {}
  try {
    saved = JSON.parse(localStorage.getItem('railscope-next.layout-v4')??'{}') ?? {}
    if (!Object.keys(saved).length) saved.detail=JSON.parse(localStorage.getItem('railscope-next.layout-v3')??'{}')
  } catch { /* Use defaults for invalid preferences. */ }
  return Object.fromEntries(modes.map(({id})=>{
    const previous=saved[id]
    const layout={...defaultLayout(id),...previous,focusBottom:false}
    if(id==='operations'&&previous?.left===layoutDefaults.left)layout.left=defaultLayout(id).left
    return [id,layout]
  })) as Record<Mode,Layout>
}
function loadVisibility() {
  try {
    const saved = JSON.parse(localStorage.getItem(visibilityKey) ?? '{}')
    return Object.fromEntries(Object.entries(defaults).map(([id, value])=>[id,typeof saved[id]==='boolean'?saved[id]:value])) as Visibility
  } catch {
    return defaults
  }
}

export function App() {
  const language=useLanguage()
  const [welcomeOpen,setWelcomeOpen]=useState(()=>localStorage.getItem('railscope-next.quick-start')!=='seen')
  function closeWelcome(){
    localStorage.setItem('railscope-next.quick-start','seen')
    setWelcomeOpen(false)
  }
  const [packs, setPacks] = useState<Pack[]>([])
  const [active, setActive] = useState<Pack | null>(null)
  const [changing, setChanging] = useState(true)
  const [globalError, setGlobalError] = useState('')
  const [visibility, setVisibility] = useState(loadVisibility)
  const onPreset=(value:Partial<Visibility>)=>setVisibility(old=>({...old,...value}))
  const onLayer=(key:LayerGroup,value:boolean)=>onPreset({[key]:value})
  const [details, setDetails] = useState<Inspection>()
  const [backStation,setBackStation]=useState<Summary>()
  const [group, setGroup] = useState<Summary>()
  const [mapError, setMapError] = useState('')
  const [conflicts, setConflicts] = useState<Conflict[]>([])
  const [workspaceChange, setWorkspaceChange] = useState(0)
  const [detailError, setDetailError] = useState('')
  const [busy, setBusy] = useState(false)
  const [view, setView] = useState<ReturnType<MapController['view']>>()
  const [mode, setMode] = useState<Mode>('detail')
  const [analysisTab,setAnalysisTab] = useState<'route'|'topology'|'conflicts'|'quality'|'metrics'>('route')
  const [dataTab,setDataTab] = useState<'packs'|'source'|'review'|'import'>('packs')
  const [theme,setTheme]=useState<MapTheme>(()=>{
    const value=localStorage.getItem('railscope-next.theme') as MapTheme|null
    return value&&Object.hasOwn(themes,value)?value:'light'
  })
  const [overview,setOverview]=useState(()=>localStorage.getItem('railscope-next.overview')==='true')
  const [layouts,setLayouts]=useState(layoutPreferences)
  const layout=layouts[mode]
  const setLayout=(update:(old:Layout)=>Layout)=>setLayouts(old=>({...old,[mode]:update(old[mode])}))
  const [rightDock,setRightDock]=useState<HTMLDivElement|null>(null)
  const [bottomDock,setBottomDock]=useState<HTMLDivElement|null>(null)
  const [analysisDock,setAnalysisDock]=useState<HTMLDivElement|null>(null)
  const [simulationPlaying,setSimulationPlaying]=useState(false)
  const [operationsDirty,setOperationsDirty]=useState(false)
  const [trackViews,setTrackViews]=useState(()=>readTrackViews(localStorage))
  const [settingsOpen,setSettingsOpen]=useState(false)
  const [settingsSection,setSettingsSection]=useState<SettingsSection>('map')
  const [level,setLevel]=useState<Level>(()=>{
    const value=localStorage.getItem('railscope.ops.level')
    return value==='l1'||value==='l3'?value:'l2'
  })
  const operations=useRef<OperationsHandle>(null)
  const canvas = useRef<HTMLDivElement>(null)
  const mapPanel = useRef<HTMLElement>(null)
  const controller = useRef<MapController | undefined>(undefined)
  const current = useRef<Pack | null>(null)
  const selection = useRef(0)
  const visualRequest = useRef(0)
  const operationsMap=useMemo(()=>({
    setTrains: (...args:Parameters<MapController['setTrains']>)=>controller.current?.setTrains(...args),
    showPath: (...args:Parameters<MapController['setTrainPath']>)=>controller.current?.setTrainPath(...args),
    showCorridor: (...args:Parameters<MapController['setCorridorPath']>)=>controller.current?.setCorridorPath(...args),
    focusTrain: (...args:Parameters<MapController['focusTrain']>)=>controller.current?.focusTrain(...args),
    revealLayer: (layer:'trains'|'route')=>setVisibility(old=>old[layer]?old:{...old,[layer]:true}),
  }),[])

  useEffect(() => {
    try {
      const map = new MapController(canvas.current!, id => {
        if (id === null) {
          clear()
          return
        }
        const started = performance.now()
        requestAnimationFrame(() => recordSelection(performance.now() - started))
        void select(String(id))
      }, () => setView(controller.current?.view()), setMapError, id=>{
        setMode('operations')
        if(id!=='draft-preview')operations.current?.selectRun(id)
      })
      controller.current = map
      const observer=new ResizeObserver(()=>map.map.resize())
      if(mapPanel.current)observer.observe(mapPanel.current)
      setView(map.view())
      return () => { observer.disconnect(); controller.current = undefined; map.destroy() }
    } catch (error) {
      setMapError(t("地图初始化失败 · {0}", String(error)))
    }
  }, [])
  useEffect(() => startMetrics(), [])
  useEffect(() => {
    api.packs().then(result => { setPacks(result.packs); setActive(result.active) })
      .catch(error => setGlobalError(String(error))).finally(() => setChanging(false))
  }, [])
  useEffect(() => {
    current.current = active
    clear()
    setGroup(undefined)
    setConflicts([])
    if (active) {
      void api.conflicts(active.snapshot).then(items => {
        if (current.current?.snapshot === active.snapshot) setConflicts(items)
      }).catch(error => setGlobalError(String(error)))
    }
  }, [active])
  useEffect(()=>{
    localStorage.setItem('railscope-next.overview',String(overview))
    if(active)void controller.current?.setPack(active,overview).then(()=>refreshVisuals(active)).catch(error=>setMapError(String(error)))
  },[active,overview])
  useEffect(()=>{
    controller.current?.setTheme(theme)
    localStorage.setItem('railscope-next.theme',theme)
  },[theme])
  useEffect(()=>{
    localStorage.setItem('railscope-next.layout-v4',JSON.stringify(layouts))
  },[layouts])
  useEffect(()=>{
    controller.current?.setTrackViews(trackViews)
    localStorage.setItem('railscope-next.rail-style',trackViews.rail)
    localStorage.setItem('railscope-next.metro-style',trackViews.metro)
  },[trackViews])
  useEffect(()=>{localStorage.setItem('railscope.ops.level',level)},[level])
  useEffect(() => {
    controller.current?.setLayers(visibility)
    localStorage.setItem(visibilityKey, JSON.stringify(visibility))
  }, [visibility])
  useEffect(() => {
    const dismiss = (event: KeyboardEvent) => {
      if(event.key==='Escape') document.querySelectorAll<HTMLDetailsElement>('.map-tools details[open]').forEach(item=>{item.open=false})
      if(event.ctrlKey&&event.key===','){event.preventDefault();setSettingsOpen(true)}
    }
    window.addEventListener('keydown', dismiss)
    return () => window.removeEventListener('keydown', dismiss)
  }, [])
  useEffect(()=>{
    if(!operationsDirty)return
    const leave=(event:BeforeUnloadEvent)=>{event.preventDefault();event.returnValue=''}
    window.addEventListener('beforeunload',leave)
    return ()=>window.removeEventListener('beforeunload',leave)
  },[operationsDirty])

  function clear() {
    selection.current++
    setDetails(undefined)
    setDetailError('')
    setBusy(false)
    controller.current?.clearSelection()
  }

  async function select(id: string, summary?: Summary) {
    const pack = current.current
    if (!pack) return
    const generation = ++selection.current
    setDetails(undefined)
    setDetailError('')
    setBusy(true)
    if (summary) {
      controller.current?.selectSummary(summary)
      controller.current?.fit(summary.bounds)
    }
    const start = performance.now()
    try {
      let result = await api.entity(pack.snapshot, id)
      if(generation!==selection.current)return
      const owner=result.entity.site?.owner
      if(owner){
        result=await api.entity(pack.snapshot,owner.id)
        if(generation!==selection.current)return
        controller.current?.selectSummary(owner)
      }
      if (generation !== selection.current) return
      recordQuery(performance.now() - start)
      setDetails(result)
      if(result.entity.has_geometry&&(result.entity.kind==='line'||result.entity.site)) {
        await displayGeometry(pack,result.entity,generation,false)
      }
      if(generation===selection.current)controller.current?.setFocusLine(result.entity.kind==='line'?result.entity.render_id:undefined)
    } catch (error) {
      if (generation === selection.current) setDetailError(String(error))
    } finally {
      if (generation === selection.current) setBusy(false)
    }
  }

  async function change(pack: Pack) {
    if (pack.snapshot === active?.snapshot) return
    if (operationsDirty && !window.confirm(t("当前有未保存的运行草稿。切换数据包并放弃草稿？"))) return
    setGlobalError('')
    setMapError('')
    setChanging(true)
    try {
      setActive(await api.activate(pack.snapshot))
    } catch (error) {
      setGlobalError(String(error))
    } finally {
      setChanging(false)
    }
  }

  async function exact() {
    if (!active || !details) return
    const generation = selection.current
    try {
      await displayGeometry(active,details.entity,generation,true)
    } catch (error) {
      if (generation === selection.current) setDetailError(String(error))
    }
  }

  async function displayGeometry(pack:Pack,entity:Inspection['entity'],generation:number,fit:boolean){
    const areas=entity.site?.areas.map(area=>area.object).filter(area=>area.id!==entity.id).slice(0,8)??[]
    const objects=[entity,...areas]
    const geometries=(await Promise.all(objects.map(object=>api.geometry(pack.snapshot,object.id))))
      .filter(geometry=>geometry!==null)
    if(generation!==selection.current)return
    controller.current?.setExact(geometries.length>1?{type:'GeometryCollection',geometries}:geometries[0]??null)
    if(fit){
      const bounds=objects.reduce<Bounds>((box,{bounds})=>[
        Math.min(box[0],bounds[0]),Math.min(box[1],bounds[1]),Math.max(box[2],bounds[2]),Math.max(box[3],bounds[3]),
      ],entity.bounds)
      controller.current?.fit(bounds)
    }
  }

  async function refreshVisuals(pack: Pack) {
    const request = ++visualRequest.current
    const visuals: Visual[] = []
    let items = await api.visuals(pack.snapshot)
    while (request === visualRequest.current && current.current?.snapshot === pack.snapshot) {
      visuals.push(...items)
      if (items.length < 160) break
      items = await api.visuals(pack.snapshot, items.at(-1)!.id)
    }
    if (request === visualRequest.current && current.current?.snapshot === pack.snapshot) controller.current?.setVisuals(visuals)
  }

  function applyWorkspace(update: Workspace, expected: Inspection, pack: Pack) {
    if (current.current?.snapshot !== pack.snapshot) return
    void refreshVisuals(pack).catch(error => setMapError(String(error)))
    setWorkspaceChange(value => value + 1)
    setDetails(value => value?.entity.id === expected.entity.id && value.workspace.revision <= update.revision
      ? { ...value, workspace: update } : value)
  }

  function resize(event:ReactPointerEvent<HTMLDivElement>,side:'left'|'right'|'bottom'){
    event.preventDefault()
    const start=side==='bottom'?event.clientY:event.clientX
    const initial=layout[side]
    const node=event.currentTarget
    node.setPointerCapture(event.pointerId)
    const move=(e:PointerEvent)=>setLayout(old=>({...old,[side]:Math.min(side==='left'?420:side==='right'?480:Math.floor(window.innerHeight*.7),
      Math.max(side==='left'?190:side==='right'?260:160,initial+((side==='bottom'?e.clientY:e.clientX)-start)*(side==='left'?1:-1)))}))
    node.addEventListener('pointermove',move)
    node.addEventListener('lostpointercapture',()=>node.removeEventListener('pointermove',move),{once:true})
  }

  function showOperations(start?:Summary,end?:Summary){
    setMode('operations')
    setLayouts(value=>({...value,operations:{...value.operations,hideLeft:false}}))
    operations.current?.startCorridor(start,end)
  }

  function openBottom(focus=false){
    setLayouts(old=>({...old,operations:{...old.operations,hideBottom:false,focusBottom:focus||old.operations.focusBottom}}))
  }
  function openData(){
    setMode('data');setDataTab('packs')
    setLayouts(old=>({...old,data:{...old.data,hideLeft:false}}))
  }

  const zoom=view?Math.floor(view.zoom):0
  const precise=!overview && active && (zoom>=15||zoom>active.max_zoom||(active.source_schema>=3&&zoom>=9))
  return <div className="app" data-theme={theme} data-language={language}>
    <header className="top">
      <div className="brand"><div className="brand-icon"><TrainFront size={23} strokeWidth={1.7} /></div><strong>RailScope <span>Next</span></strong></div>
      <nav aria-label={t("工作台")}>
        {modes.map(({id,title,description,icon:Icon})=><button key={id} className={mode===id?'selected':''} aria-current={mode===id?'page':undefined}
          aria-label={t(title)} title={t(description)} onClick={()=>setMode(id)}><Icon size={17}/><span>{t(title)}</span></button>)}
        <button aria-haspopup="dialog" title={t("设置 (Ctrl+,)")} onClick={()=>setSettingsOpen(true)}><SettingsIcon size={17}/>{t("设置")}</button>
      </nav>
      <div className="workspace-tools">
        <button className="icon" title={t("显隐任务面板")} aria-label={t("任务面板")} aria-pressed={!layout.hideLeft} onClick={()=>setLayout(old=>({...old,hideLeft:!old.hideLeft}))}><PanelLeft size={17}/></button>
        <button className="icon" title={t("显隐对象面板")} aria-label={t("对象面板")} aria-pressed={!layout.hideRight} onClick={()=>setLayout(old=>({...old,hideRight:!old.hideRight}))}><PanelRight size={17}/></button>
        <button className="icon" title={t("显隐底部工作区")} aria-label={t("底部工作区")} aria-pressed={!layout.hideBottom} onClick={()=>setLayout(old=>({...old,hideBottom:!old.hideBottom,focusBottom:false}))}><PanelBottom size={17}/></button>
        <PackMenu packs={packs} active={active} changing={changing} change={change} conflicts={conflicts} onManage={openData} />
        <button className="icon quick-start-trigger" title={t("使用指南")} aria-label={t("使用指南")} aria-haspopup="dialog" onClick={()=>setWelcomeOpen(true)}>
          <CircleHelp size={18}/>
        </button>
        <LanguageSelect />
      </div>
    </header>
    <Settings open={settingsOpen} section={settingsSection} onSection={setSettingsSection} onClose={()=>setSettingsOpen(false)}
      theme={theme} setTheme={setTheme} overview={overview} setOverview={setOverview} trackViews={trackViews} setTrackViews={setTrackViews}
      visibility={visibility} onLayer={onLayer} onPreset={onPreset} mode={mode} layouts={layouts}
      onPanel={(target,panel,visible)=>setLayouts(old=>({...old,[target]:{...old[target],[panel]:!visible,
        focusBottom:panel==='hideBottom'&&!visible?false:old[target].focusBottom}}))}
      resetLayout={target=>setLayouts(old=>({...old,[target]:defaultLayout(target)}))}
      level={level} setLevel={setLevel} pack={active} onData={()=>{setSettingsOpen(false);openData()}}/>
    <Welcome open={welcomeOpen&&!changing} pack={active} onClose={closeWelcome}
      onMap={()=>{closeWelcome();setMode('detail')}}
      onOperations={()=>{closeWelcome();setMode('operations')}}
      onData={()=>{closeWelcome();openData()}}/>
    <main className="workspace" data-mode={mode} data-left={!layout.hideLeft} data-right={!layout.hideRight}
      style={{gridTemplateColumns:`${layout.hideLeft?'0px':`min(${layout.left}px, 32vw)`} minmax(0, 1fr) ${layout.hideRight?'0px':`min(${layout.right}px, 33vw)`}`,
        gridTemplateRows:`minmax(${layout.focusBottom&&!layout.hideBottom?'115px':'0'},1fr) ${layout.hideBottom?'0px':layout.focusBottom?'min(67vh, 760px)':`min(${layout.bottom}px, 55vh)`}`}}>
      <aside className="task-dock" aria-label={t("任务面板")} hidden={layout.hideLeft}>
      <div className="catalog-dock" hidden={mode!=='detail'}><Catalog pack={active} selected={details?.entity.id} onSelect={item => { void select(item.id, item) }}
        group={group} setGroup={setGroup} view={view?.bounds} visibility={visibility}
        workspaceChange={workspaceChange}
        onLayer={onLayer} onPreset={onPreset} /></div>
      <OperationsWorkbench ref={operations} key={active?.snapshot} snapshot={active?.snapshot} visible={mode==='operations'}
        selection={details?.entity} map={operationsMap} rightDock={rightDock} bottomDock={bottomDock} openBottom={openBottom}
        inspectStop={id=>void api.entity(active!.snapshot,id).then(result=>select(id,result.entity)).catch(error=>setDetailError(String(error)))}
        onPlaying={setSimulationPlaying} onDirty={setOperationsDirty} level={level} setLevel={setLevel}/>
      <div className="task-pane" hidden={mode!=='analysis'}>
        <div className="panel-title"><Activity size={17}/><h2>{t("分析与诊断")}</h2></div>
        <div className="task-menu">{([['route',t("路径分析")],['topology',t("拓扑与通道")],['conflicts',t("时刻与车辆冲突")],['quality',t("数据质量")],['metrics',t("系统性能")]] as const).map(([id,label])=>
          <button key={id} className={analysisTab===id?'active':''} aria-pressed={analysisTab===id} onClick={()=>setAnalysisTab(id)}>{label}</button>)}</div>
        <div hidden={analysisTab!=='route'}><PathPanel key={active?.snapshot} pack={active} selection={details?.entity} transfer={showOperations}
          show={geometry => controller.current?.setExact(geometry)} dock={analysisDock} visible={mode==='analysis'&&analysisTab==='route'}
          reveal={() => setLayouts(old=>({...old,analysis:{...old.analysis,hideBottom:false}}))} /></div>
        {analysisTab==='topology'&&<div className="property-content task-summary"><h3>{t("物理连接")}</h3><p>{t("路径分析按显式拓扑与运行方向查询。选择物理轨道可检查来源或打开通道工作区。")}</p>
          <button className="secondary" disabled={!details?.entity.has_edge} onClick={()=>showOperations(details?.entity)}>{t("在运行中检查轨道")}</button></div>}
        {analysisTab==='conflicts'&&<div className="property-content task-summary"><p>{t("资源分析使用已保存时刻、经过校验的通道和车组分配。")}</p>
          <button className="secondary" onClick={()=>{setMode('operations');operations.current?.openScenario()}}>{t("打开运行资源分析")}</button></div>}
        {analysisTab==='quality'&&<div className="property-content task-summary"><p>{conflicts.length} {t("条快照迁移冲突")}</p>
          <button className="secondary" onClick={()=>{setDataTab('review');setMode('data')}}>{t("查看来源与冲突")}</button></div>}
        {analysisTab==='metrics'&&<MetricsPanel pack={active}/>}
      </div>
      <div className="task-pane" hidden={mode!=='data'}>
        <div className="panel-title"><Database size={17}/><h2>{t("数据")}</h2></div>
        <div className="task-menu">{([['packs',t("数据包")],['import',t("导入旧版计划")],['source',t("版本与来源")],['review',t("审校冲突")]] as const).map(([id,label])=>
          <button key={id} className={dataTab===id?'active':''} aria-pressed={dataTab===id} onClick={()=>setDataTab(id)}>{label}</button>)}</div>
        {dataTab==='packs'&&<div className="property-content task-summary">
          {packs.map(pack=><button key={pack.snapshot} className="pack-choice" disabled={changing||pack.snapshot===active?.snapshot}
            onClick={()=>void change(pack)}><span>{t(pack.region)}<small>{pack.snapshot}</small></span><b>{pack.snapshot===active?.snapshot?t("当前"):t("切换")}</b></button>)}
          <PackInstall onInstalled={pack=>{setPacks(items=>[pack,...items.filter(item=>item.snapshot!==pack.snapshot)]);if(!active)void change(pack)}}/>
        </div>}
        {dataTab==='source'&&<div className="property-content task-summary"><p>{active?.source ?? t("未加载快照")}</p><p>{active?.source_file}</p><p>{active?.license}</p>
          {active&&<dl>{Object.entries(active.source_objects??{}).map(([key,count])=><div className="source-count" key={key}><dt>{key}</dt><dd>{count.toLocaleString()}</dd></div>)}</dl>}</div>}
        {dataTab==='review'&&<div className="property-content task-summary">{conflicts.length===0?<p>{t("当前快照没有已记录的迁移冲突。")}</p>:
          <ul className="conflicts">{conflicts.map(item=><li key={`${item.entity}-${item.source_snapshot}`}>
            <span>{item.reason}</span><small>{item.source_snapshot} · {item.entity}</small></li>)}</ul>}</div>}
        {active&&<LegacyData key={active.snapshot} snapshot={active.snapshot} tab={dataTab} onImported={()=>operations.current?.refresh()}/>}
      </div>
      </aside>
      <section className="map-panel" ref={mapPanel} aria-label={t("离线交通地图")}>
        <div className="map-canvas" ref={canvas} />
        {!layout.hideLeft&&<div className="dock-resize left" aria-hidden="true" onPointerDown={e=>resize(e,'left')}/>}
        {!layout.hideRight&&<div className="dock-resize right" aria-hidden="true" onPointerDown={e=>resize(e,'right')}/>}
        <div className="map-tools">
          {active && <><div className="map-title"><Compass size={17} /><span>{t(active.region)} {t("· 交通地图")}</span></div>
            <button className="map-home" onClick={() => controller.current?.fit(active.bounds, 11)} title={t("返回数据包范围")} aria-label={t("返回数据包范围")}><Crosshair size={17} /></button></>}
          <details className="view-settings">
            <summary>{t("视图")}</summary>
            <div>
              <form className="coordinate-jump" onSubmit={event=>{
                event.preventDefault()
                const form=new FormData(event.currentTarget)
                const [lng,lat,zoom]=['lng','lat','zoom'].map(key=>Number(form.get(key)))
                if(Number.isFinite(lng)&&Math.abs(lng)<=180&&Number.isFinite(lat)&&Math.abs(lat)<=85&&zoom>=0&&zoom<=22)
                  controller.current?.map.jumpTo({center:[lng,lat],zoom})
              }}>
                <input name="lng" required type="number" step="any" aria-label={t("跳转经度")} placeholder={t("经度")}/>
                <input name="lat" required type="number" step="any" aria-label={t("跳转纬度")} placeholder={t("纬度")}/>
                <input name="zoom" required type="number" step="any" aria-label={t("跳转缩放")} placeholder="Z"/>
                <button className="secondary compact">{t("定位")}</button>
              </form>
              <button className="secondary compact" onClick={()=>{setSettingsSection('map');setSettingsOpen(true)}}>{t("地图与底图设置")}</button>
            </div>
          </details>
          <details className="view-settings"><summary>{t("图层")}</summary><div>
            <button className="secondary compact" onClick={()=>{setSettingsSection('tracks');setSettingsOpen(true)}}>{t("轨道配色设置")}</button>
            <LayerTree visibility={visibility} onLayer={onLayer} onPreset={onPreset}/>
            <details className="rail-legend"><summary>{t("图例")}</summary><MapLegend views={trackViews}/></details>
          </div></details>
        </div>
        {!active && !changing && !globalError && <div className="map-empty">
          <Database size={24} /><h2>{t("尚未加载离线地图")}</h2>
          <button className="primary compact" onClick={()=>setWelcomeOpen(true)}>{t("使用指南")}</button>
        </div>}
        {(globalError || mapError) && <div className="map-error" role="alert">
          <span>{globalError || mapError}</span><button onClick={() => { setGlobalError(''); setMapError('') }} aria-label={t("关闭提示")}><X size={15} /></button>
        </div>}
        {!globalError && changing && <div className="map-loading">{t("正在校验本地数据包…")}</div>}
      </section>
      <aside className="inspector" aria-label={t("工作面板")} hidden={layout.hideRight}>
        <div hidden={mode==='data'||mode==='operations'}>
          <div className="panel-title"><FileText size={16} /><h2>{t("对象属性")}</h2>{details && <button className="icon" aria-label={t("清除选中")} onClick={clear}><X size={17} /></button>}</div>
          {backStation&&details?.entity.kind==='line'&&<button className="secondary compact" onClick={()=>{void select(backStation.id,backStation);setBackStation(undefined)}}>{t("返回")} {backStation.name}</button>}
          {detailError && <p className="error-text" role="alert">{detailError}</p>}
          {busy ? <div className="blank">{t("正在读取对象…")}</div> : details && active
            ? <Properties key={details.entity.id} inspection={details} pack={active} exact={exact}
                showMembers={line=>{setGroup(line);setMode('detail')}} update={(state, target) => applyWorkspace(state, target, active)}
                openLine={item=>{if(details.entity.kind==='station')setBackStation(details.entity);else if(item.kind==='station')setBackStation(undefined);void select(item.id,item)}}
                useForCorridor={()=>showOperations(details.entity)} useForAnalysis={()=>{setAnalysisTab('route');setMode('analysis')}}
                zoomOverride={point => controller.current?.fit([point[0], point[1], point[0], point[1]])} />
            : <div className="blank"><MapPin size={24} /><strong>{t("选择地图对象")}</strong><p>{t("点击地图或打开搜索结果即可查看资料。")}</p></div>}
        </div>
        {mode==='detail'&&active&&details?.entity.kind==='line'&&<BusinessLines key={active.snapshot} snapshot={active.snapshot} selection={details?.entity}
          openMember={item => { void select(item.id, item) }} />}
        {mode==='data'&&<div className="property-content task-summary"><h3>{active?.snapshot??t("快照详情")}</h3>
          {active&&<><p>{active.source}</p><code>{active.source_sha256}</code><p>{active.bounds.join(' · ')}</p></>}</div>}
        <div ref={setRightDock} hidden={mode!=='operations'}/>
      </aside>
      <section className="bottom-dock" aria-label={t("底部工作区")} hidden={layout.hideBottom}>
        <div className="dock-resize bottom" aria-hidden="true" onPointerDown={e=>resize(e,'bottom')}/>
        <div className="panel-title"><PanelBottom size={15}/><h2>{mode==='operations'?t("时刻与运行图"):mode==='analysis'?t("分析结果"):t("底部工作区")}</h2>
          <button className="icon" aria-label={t("聚焦底部工作区")} aria-pressed={layout.focusBottom} onClick={()=>setLayout(old=>({...old,focusBottom:!old.focusBottom}))}><Maximize2 size={15}/></button>
          <button className="icon" aria-label={t("收起底部工作区")} onClick={()=>setLayout(old=>({...old,hideBottom:true,focusBottom:false}))}><X size={16}/></button>
        </div>
        <div ref={setBottomDock} className="bottom-content" hidden={mode!=='operations'}/>
        <div ref={setAnalysisDock} className="bottom-content" hidden={mode!=='analysis'}/>
      </section>
    </main>
    <footer className="statusbar">
      <span><span className={active ? 'dot online' : 'dot'} />{active ? t("离线 · 本地快照") : t("无活动快照")}</span>
      <span className="snapshot">{active?.snapshot}</span>
      {simulationPlaying&&<button className="status-action" onClick={()=>{setMode('operations');operations.current?.pause()}}>{t("计划仿真中 · 暂停")}</button>}
      {operationsDirty&&<span>{t("运行草稿未保存")}</span>}
      {active&&<span>{precise?t("源几何 · 按视野生成"):t("概览瓦片")}{active.source_schema>=3?t(" · OSM 原始对象"):''}</span>}
      <span className="coordinates">{view && `${view.center.lng.toFixed(4)}° E   ${view.center.lat.toFixed(4)}° N`}</span>
      <span className="zoom">{view ? `Z ${view.zoom.toFixed(1)}` : ''}</span>
    </footer>
  </div>
}

function LegacyData({snapshot,tab,onImported}:{snapshot:string;tab:string;onImported:()=>void}){
  const [date,setDate]=useState(chinaDate)
  const [aliases,setAliases]=useState('')
  const [file,setFile]=useState<File>()
  const [status,setStatus]=useState('')
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  const [issues,setIssues]=useState<LegacyIssue[]>([])
  useEffect(()=>{
    if(tab!=='review')return
    let mounted=true
    void opsApi.legacyIssues(snapshot).then(items=>{if(mounted)setIssues(items)}).catch(cause=>{if(mounted)setError(String(cause))})
    return ()=>{mounted=false}
  },[snapshot,tab])
  async function load(){
    if(!file)return
    setError('');setStatus('');setBusy(true)
    try {
      if(file.size>16*1024*1024)throw new Error(t("旧版文件超过 16 MiB。"))
      const mapping=aliases.trim()?JSON.parse(aliases):{}
      const result=await opsApi.importLegacy(snapshot,(await file.text()).replace(/^\uFEFF/,''),date,mapping)
      if(result.pending)setError(result.issue??t("存在待修复引用，请在审校中查看。"))
      else {setStatus(t("已导入 {0} 条通道、{1} 趟车次。", result.corridors, result.runs));onImported()}
    }catch(cause){setError(String(cause))}finally{setBusy(false)}
  }
  return <>
    <form hidden={tab!=='import'} className="property-content task-summary data-import" onSubmit={e=>{e.preventDefault();void load()}}>
      <label>{t("旧版 JSON")}<input type="file" accept=".json" onChange={e=>setFile(e.target.files?.[0])}/></label>
      <label>{t("文件未指定运营日时使用")}<input type="date" value={date} onChange={e=>setDate(e.target.value)} required/></label>
      <label>{t("显式 ID 对照（JSON，可选）")}<textarea value={aliases} onChange={e=>setAliases(e.target.value)} rows={5}
        placeholder={t("{\"line:旧线路编号\":\"现有 RailScope UUID\"}")}/></label>
      <button className="primary" disabled={busy||!file}>{t("校验并导入")}</button>
      {status&&<p role="status">{status}</p>}{error&&<p className="error-text" role="alert">{error}</p>}
    </form>
    {tab==='review'&&<div className="property-content task-summary"><h3>{t("旧计划待修复")}</h3>
      {issues.length===0?<p>{t("暂无待修复旧计划。")}</p>:issues.map(item=><details key={item.id}><summary>{item.schema}</summary><p>{item.reason}</p></details>)}
    </div>}
  </>
}

function MapLegend({views}:{views:TrackViews}) {
  const themed = views.rail!=='infrastructure' ? thematic[views.rail] : undefined
  const items: [string,string][] = themed?.entries.length ? [...themed.entries.map(([,name,color]):[string,string]=>[name,color]),[t("未知／缺失"),missingColor]]
    : Object.entries(tracks).filter(([key])=>key.startsWith('rail_')||key.startsWith('track_')).map(([, [name,color]])=>[name,color])
  return <ul className="map-legend-list">
    {items.map(([name,color])=><li key={name}><i style={{backgroundColor:color}}/>{t(name)}</li>)}
    {views.rail==='lifecycle'&&Object.entries(lifecycles).map(([key,[name,opacity,,dash]])=><li key={key}>
      <i style={{borderTop:`2px ${dash?'dashed':'solid'} currentColor`,opacity}}/>{t(name)}</li>)}
    {views.metro==='lines'&&<li>{t("城市轨道 · 按运营线路色显示")}</li>}
    {Object.entries(tracks).filter(([key])=>['metro_track','light_rail_track','tram_track','monorail_track'].includes(key))
      .map(([, [name,color]])=><li key={name}><i style={{backgroundColor:color}}/>{t(name)}</li>)}
    {Object.values(sites).map(([name,,graphic])=><li key={name}><img alt="" src={svgSymbol(graphic)}/>{t(name)}</li>)}
    {Object.values(facilityStyles).map(item=><li key={item.name}><i style={{backgroundColor:item.fill,border:`1px solid ${item.outline}`}}/>{t(item.name)}</li>)}
  </ul>
}

function Properties({ inspection, pack, exact, showMembers, openLine, update, zoomOverride, useForCorridor, useForAnalysis }: {
  inspection: Inspection; pack: Pack; exact: () => void; showMembers: (line: Summary) => void
  openLine:(line:Summary)=>void
  update: (state: Workspace, target: Inspection) => void
  zoomOverride: (point: [number, number]) => void
  useForCorridor: () => void
  useForAnalysis: () => void
}) {
  const { entity, workspace } = inspection
  const meaning=entity.semantics
  const [tab, setTab] = useState<'overview' | 'lines' | 'attributes' | 'workspace'>('overview')
  const [draft, setDraft] = useState<Override>(workspace.value)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [confirmation, setConfirmation] = useState('')
  const [coordinates, setCoordinates] = useState<[string, string]>(['', ''])
  const [groups,setGroups]=useState<string[]>([])
  useEffect(()=>{
    if(tab!=='workspace')return
    let active=true
    void api.groups(pack.snapshot).then(items=>{if(active)setGroups(items.map(item=>item.name))}).catch(()=>{})
    return ()=>{active=false}
  },[tab,pack.snapshot])
  useEffect(() => {
    setDraft(workspace.value)
    setCoordinates(workspace.value.point ? [String(workspace.value.point[0]), String(workspace.value.point[1])] : ['', ''])
  }, [workspace])
  const pointEntity = entity.has_geometry && !entity.has_edge
    && entity.bounds[0] === entity.bounds[2] && entity.bounds[1] === entity.bounds[3]
  const station = entity.kind === 'station'
  const line = entity.kind === 'line'
  const missing = t("待补充")
  const edited = draft.designation !== workspace.value.designation || draft.operator !== workspace.value.operator
    || draft.category !== workspace.value.category
    || JSON.stringify(draft.associated_lines ?? []) !== JSON.stringify(workspace.value.associated_lines ?? [])
    || draft.name !== workspace.value.name || draft.note !== workspace.value.note || draft.color !== workspace.value.color
    || draft.group !== workspace.value.group
    || draft.width!==workspace.value.width || draft.opacity!==workspace.value.opacity
    || coordinates.some((coordinate, i) => coordinate !== String(workspace.value.point?.[i] ?? ''))

  async function change(action: 'apply' | 'undo' | 'redo') {
    setError('')
    setConfirmation('')
    setSaving(true)
    try {
      const [x, y] = coordinates.map(coordinate => coordinate.trim())
      if (action === 'apply' && (x || y) && (!x || !y || !Number.isFinite(Number(x)) || !Number.isFinite(Number(y)))) {
        setError(t("请填写有效的经纬度，或同时留空以恢复原始位置。"))
        return
      }
      const result = action === 'apply'
        ? await api.update(pack.snapshot, entity.id, workspace.revision, {
            ...draft, group: draft.group.trim(), point: x && y ? [Number(x), Number(y)] : null,
          })
        : await api.replay(pack.snapshot, entity.id, workspace.revision, action === 'redo')
      update(result, inspection)
      if (result.value.point) zoomOverride(result.value.point)
      setConfirmation({ apply: t("已保存到工作区"), undo: t("已撤销"), redo: t("已重做") }[action])
    } catch (e) {
      setError(String(e))
      try {
        const refreshed = await api.entity(pack.snapshot, entity.id)
        update(refreshed.workspace, inspection)
        setDraft(draft)
      } catch { /* the active data pack changed */ }
    } finally {
      setSaving(false)
    }
  }

  return <div className="properties">
    <div className="object-head">
      <span className="object-type">{t(entity.site?siteRoleNames[entity.site.role]:kindNames[entity.kind])}</span>
      <h3>{workspace.value.name || entity.name}</h3>
      <span>{!entity.site&&<>{t(symbolName(meaning.render_class)??className(entity.class))} · </>}{t(lifecycles[meaning.lifecycle as keyof typeof lifecycles]?.[0]??statusName(entity.status))}</span>
    </div>
    <div className="tabs property-tabs" role="group" aria-label={t("对象选项卡")}>
      {([
        ['overview', "概览", "查看常用属性和地图位置。"],
        ['lines', "关联", "查看所属线路、线路成员或车站关联。"],
        ['attributes', "来源", "核对快照、原始 OSM 对象和核验状态。"],
        ['workspace', "编辑", "在工作区保存名称、分类、属性和显示设置。"],
      ] as const).map(([id,label,description])=><button key={id} className={tab===id?'active':''}
        aria-pressed={tab===id} title={t(description)} onClick={()=>setTab(id)}>{t(label)}</button>)}
    </div>
    <p className="property-tab-help">{t({
      overview:"查看常用属性和地图位置。",lines:"查看所属线路、线路成员或车站关联。",
      attributes:"核对快照、原始 OSM 对象和核验状态。",workspace:"在工作区保存名称、分类、属性和显示设置。",
    }[tab])}</p>
    {tab==='overview' ? <div className="property-content site-overview">
      <dl>
        <dt>{t("对象类别")}</dt><dd>{t(entity.site?siteRoleNames[entity.site.role]:className(entity.class))}</dd>
        {(station || line)&&<>
          <dt>{t("编号")}</dt><dd>{workspace.value.designation || entity.tags.ref || missing}</dd>
          <dt>{t("运营管理")}</dt><dd>{workspace.value.operator || entity.tags.operator || missing}</dd>
          <dt>{t("目录分类")}</dt><dd>{workspace.value.category || missing}</dd>
          <dt>{t("工作区分组")}</dt><dd>{workspace.value.group || missing}</dd>
        </>}
        {station&&<><dt>{t("运输业务")}</dt><dd>{{passenger:t("客运"),freight:t("货运"),mixed:t("客货运")}[meaning.business??'']??missing}</dd>
          <dt>{t("技术类型")}</dt><dd>{{marshalling:t("编组站"),district:t("区段站"),intermediate:t("中间站")}[meaning.technical??'']??missing}</dd></>}
        {(line || entity.has_edge&&entity.kind!=='road')&&<>
          <dt>{t("速度／轨距")}</dt><dd>{meaning.speed_kmh==null?missing:`${meaning.speed_kmh.toFixed(1)} km/h`} · {meaning.gauge_mm==null?missing:`${meaning.gauge_mm} mm`}</dd>
          <dt>{t("供电／股道号")}</dt><dd>{{overhead:t("接触网"),third_rail:t("第三轨"),none:t("无电气化")}[meaning.electrification??'']??missing} · {entity.tags['railway:track_ref']??missing}</dd>
        </>}
        {workspace.value.note&&<><dt>{t("备注")}</dt><dd>{workspace.value.note}</dd></>}
      </dl>
      {entity.site&&<div className="site-coverage">
        <h4>{t("地图范围")}</h4>
        {entity.site.areas.length?<ul>{entity.site.areas.map(area=><li key={area.object.id}><strong>{t(siteAreaNames[area.kind])}</strong>
          <span>{area.object.name} · {t(area.evidence)}</span></li>)}</ul>:<p className="muted">{t("当前快照未关联面状轮廓。")}</p>}
      </div>}
      <div className="ops-actions">
        {entity.has_geometry&&<button className="secondary compact" onClick={exact}><Crosshair size={15}/>{t("查看精确几何")}</button>}
        {workspace.value.point&&<button className="secondary compact" onClick={()=>zoomOverride(workspace.value.point!)}>{t("定位工作区坐标")}</button>}
        {(station&&!Object.hasOwn(nonRailStationNames,meaning.render_class)||entity.has_edge&&entity.kind!=='road')&&
          <button className="secondary compact" onClick={useForCorridor}>{t("作为通道起点")}</button>}
        {entity.has_edge&&<button className="secondary compact" onClick={useForAnalysis}>{t("用于路径分析")}</button>}
      </div>
    </div> : tab==='lines' ? <div className="property-content">
      {station?<><StationLinks entity={entity} pack={pack} openLine={openLine}/>
        <StationLineEditor snapshot={pack.snapshot} value={draft.associated_lines??[]} openLine={openLine}
          change={associated_lines=>setDraft({...draft,associated_lines})}/>
        <button className="primary compact" disabled={saving||!edited} onClick={()=>void change('apply')}>{t("保存工作区关联")}</button>
        {error&&<p className="error-text" role="alert">{error}</p>}
        {confirmation&&<p className="success" role="status">{confirmation}</p>}
      </>:<div className="members">
        {line&&<button onClick={()=>showMembers(entity)}>{t("浏览线路成员")}<ChevronRight size={15}/></button>}
        {line&&!!inspection.workspace_stations?.length&&<><h4>{t("工作区关联车站")}</h4>
          {inspection.workspace_stations.map(item=><button key={item.id} onClick={()=>openLine(item)}>
            {item.name}<ArrowUpRight size={14}/></button>)}</>}
        {!!entity.memberships.length&&<><h4>{t("所属线路")}</h4>{entity.memberships.map(member=>
          <button key={member.id} onClick={()=>openLine(member)}>{member.name || t("未命名线路")}<ArrowUpRight size={14}/></button>)}</>}
        {!line&&!entity.memberships.length&&<p className="muted">{t("当前快照没有线路关联。")}</p>}
      </div>}
    </div> : tab === 'attributes' ? <div className="property-content">
      <dl>
        {workspace.value.name && <><dt>{t("原始名称")}</dt><dd>{entity.name}</dd></>}
        <dt>{t("快照")}</dt><dd>{pack.snapshot}</dd>
        <dt>{t("来源")}</dt><dd>{entity.source_ref} · v{entity.source_version}</dd>
        <dt>{t("核验")}</dt><dd>{entity.verification === 'verified' ? t("已核验") : t("自动参考")} · {entity.confidence === 'low' ? t("低") : t("中")}{t("置信度")}</dd>
        <dt>{t("实体 ID")}</dt><dd className="monospace">{entity.id}</dd>
        <dt>{t("分类依据")}</dt><dd>{t(meaning.reason)}</dd>
      </dl>
      <SourceBrowser snapshot={pack.snapshot} initial={entity.source_ref}/>
    </div> : <form className="property-content edit-form" onSubmit={e => { e.preventDefault(); void change('apply') }}>
      <label>{t("工作区名称")}<input value={draft.name} placeholder={entity.name} maxLength={120} onChange={e => setDraft({ ...draft, name: e.target.value })} /></label>
      <label>{t("工作区分组")}<input value={draft.group} list="workspace-group-names" placeholder={t("留空则移出分组")} maxLength={80}
        onChange={e => setDraft({ ...draft, group: e.target.value })} /></label>
      <datalist id="workspace-group-names">{groups.map(name=><option key={name} value={name}/>)}</datalist>
      {line&&<p className="muted">{t("让多条线路使用同一个分组名，即可在目录中一起浏览。")}</p>}
      {(station||line)&&<>
        <label>{t("目录分类")}<input value={draft.category??''} placeholder={t("例如：高速铁路、城市轨道、客运站")} maxLength={120}
          onChange={e=>setDraft({...draft,category:e.target.value})}/></label>
        <label>{t("编号")}<input value={draft.designation??''} placeholder={entity.tags.ref??missing} maxLength={120}
          onChange={e=>setDraft({...draft,designation:e.target.value})}/></label>
        <label>{t("运营管理")}<input value={draft.operator??''} placeholder={entity.tags.operator??missing} maxLength={120}
          onChange={e=>setDraft({...draft,operator:e.target.value})}/></label>
      </>}
      <label>{t("备注")}<textarea value={draft.note} rows={5} maxLength={4000} onChange={e => setDraft({ ...draft, note: e.target.value })} /></label>
      {(entity.kind !== 'line'||['subway','light_rail','tram','monorail'].includes(entity.class)) && <label>{t("地图颜色")}<span className="color-row">
        <input type="color" value={draft.color || '#176ce0'} onChange={e => setDraft({ ...draft, color: e.target.value })} />
        <button type="button" className="secondary compact" disabled={!draft.color} onClick={() => setDraft({ ...draft, color: '' })}>{t("恢复默认")}</button>
      </span></label>}
      {entity.has_edge&&<div className="point-fields">
        <label>{t("线宽（像素）")}<input type="number" step=".25" min={.25} max={24}
          value={draft.width??''} placeholder={t("按缩放分级")} onChange={e=>setDraft({...draft,width:e.target.value===''?null:e.target.valueAsNumber})}/></label>
        <label>{t("不透明度（0–1）")}<input type="number" step=".05" min={0} max={1}
          value={draft.opacity??''} placeholder={t("默认")} onChange={e=>setDraft({...draft,opacity:e.target.value===''?null:e.target.valueAsNumber})}/></label>
      </div>}
      {pointEntity && <div className="point-fields">
        <label>{t("工作区经度")}<input inputMode="decimal" value={coordinates[0]} placeholder={String(entity.bounds[0])}
          onChange={e => setCoordinates([e.target.value, coordinates[1]])} /></label>
        <label>{t("工作区纬度")}<input inputMode="decimal" value={coordinates[1]} placeholder={String(entity.bounds[1])}
          onChange={e => setCoordinates([coordinates[0], e.target.value])} /></label>
      </div>}
      <div className="edit-actions">
        <button className="primary" type="submit" disabled={!edited || saving}><Save size={15} />{t("保存")}</button>
        <button className="icon framed" type="button" aria-label={t("撤销")} disabled={!workspace.can_undo || saving} onClick={() => void change('undo')}><Undo2 size={17} /></button>
        <button className="icon framed" type="button" aria-label={t("重做")} disabled={!workspace.can_redo || saving} onClick={() => void change('redo')}><Redo2 size={17} /></button>
      </div>
      {error && <p className="error-text" role="alert">{error}</p>}
      {confirmation && <p className="success" role="status"><Check size={15} />{confirmation}</p>}
    </form>}
  </div>
}

function SourceBrowser({snapshot,initial}:{snapshot:string;initial:string}){
  const [history,setHistory]=useState([initial])
  const [source,setSource]=useState<RawSource>()
  const [offset,setOffset]=useState(0)
  const [open,setOpen]=useState(false)
  const [error,setError]=useState('')
  const reference=history.at(-1)!
  useEffect(()=>{
    if(!open)return
    let cancelled=false
    setError('');setSource(undefined)
    void api.rawSource(snapshot,reference,offset)
      .then(data=>{if(!cancelled)setSource(data)})
      .catch(error=>{if(!cancelled)setError(String(error))})
    return ()=>{cancelled=true}
  },[snapshot,reference,offset,open])
  return <details onToggle={e=>setOpen(e.currentTarget.open)}>
    <summary>{t("OSM 源对象与原始成员")}</summary>
    {error&&<p className="error-text">{error}</p>}
    {open&&!source&&!error&&<p className="muted">{t("读取来源…")}</p>}
    {source&&<>
      <div className="ops-actions"><strong>{source.ref} · v{source.version??'—'}</strong>
        {history.length>1&&<button className="secondary compact" onClick={()=>{setHistory(history.slice(0,-1));setOffset(0)}}>{t("返回")}</button>}
      </div>
      {!source.complete&&<p className="muted">{t("此版本的快照仅保留部分原始元数据。")}</p>}
      {source.point&&<p className="muted">{source.point.join(', ')}</p>}
      {source.tags&&<dl className="raw-source-tags">{Object.entries(source.tags).map(([key,value])=><div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl>}
      {!!source.rows.length&&<ol className="raw-source-members" start={offset+1}>{source.rows.map(row=>
        <li key={row.ordinal}><button className="secondary compact" onClick={()=>{setHistory([...history,row.ref]);setOffset(0)}}>{row.ref}</button>
          {row.role&&<small> {row.role}</small>}</li>)}</ol>}
      <div className="ops-actions">
        {offset>0&&<button className="secondary compact" onClick={()=>setOffset(Math.max(0,offset-100))}>{t("上一页")}</button>}
        {source.next!=null&&<button className="secondary compact" onClick={()=>setOffset(source.next!)}>{t("下一页")}</button>}
      </div>
    </>}
  </details>
}

function StationLineEditor({snapshot,value,change,openLine}:{snapshot:string;value:string[];change:(ids:string[])=>void;openLine:(line:Summary)=>void}){
  const [query,setQuery]=useState('')
  const [matches,setMatches]=useState<Summary[]>([])
  const [chosen,setChosen]=useState<Summary[]>([])
  const [error,setError]=useState('')
  const key=value.join(',')
  useEffect(()=>{
    let active=true
    const ids=key?key.split(','):[]
    void Promise.all(ids.map(id=>api.entity(snapshot,id).then(result=>result.entity))).then(results=>{
      if(active)setChosen(results)
    }).catch(cause=>{if(active)setError(String(cause))})
    return ()=>{active=false}
  },[snapshot,key])
  useEffect(()=>{
    let active=true
    setMatches([]);setError('')
    if(!query.trim())return
    const timer=window.setTimeout(()=>void api.page(snapshot,{kind:'line',query:query.trim()}).then(page=>{
      if(active)setMatches(page.items.slice(0,12))
    }).catch(cause=>{if(active)setError(String(cause))}),200)
    return ()=>{active=false;window.clearTimeout(timer)}
  },[snapshot,query])
  return <section className="manual-links">
    <h4>{t("手工关联线路")}</h4>
    <p className="muted">{t("记录工作区关联。自动关系、接轨证据和物理拓扑独立保留。")}</p>
    {value.map(id=><div className="manual-link" key={id}>
      <button type="button" className="station-link" disabled={!chosen.some(line=>line.id===id)}
        onClick={()=>{const line=chosen.find(item=>item.id===id);if(line)openLine(line)}}>{chosen.find(line=>line.id===id)?.name || id}</button>
      <button type="button" className="secondary compact" onClick={()=>change(value.filter(item=>item!==id))}>{t("移除")}</button>
    </div>)}
    <label>{t("查找线路")}<input value={query} maxLength={128} onChange={e=>setQuery(e.target.value)}/></label>
    {matches.filter(line=>!value.includes(line.id)).map(line=><button key={line.id} type="button" className="secondary compact"
      disabled={value.length>=32} onClick={()=>{change([...value,line.id]);setQuery('')}}>{t("关联 {0}",line.name || t("未命名线路"))}</button>)}
    {error&&<p className="error-text" role="alert">{error}</p>}
  </section>
}

function StationLinks({entity,pack,openLine}:{entity:Inspection['entity'];pack:Pack;openLine:(line:Summary)=>void}){
  const [tracks,setTracks]=useState<Summary[]>()
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  async function load(){
    setBusy(true);setError('')
    try{
      const controls=await opsApi.corridorQuery<ControlPoint[]>(pack.snapshot,'controls',{id:entity.id})
      const mode=entity.class==='metro_station'?'metro':'rail'
      const replies=await Promise.all(controls.slice(0,8).map(point=>opsApi.corridorQuery<{id:string;name:string}[]>(
        pack.snapshot,'lines',{node:point.id,mode})))
      const ids=[...new Set(replies.flat().map(item=>item.id))].slice(0,30)
      setTracks(await Promise.all(ids.map(id=>api.entity(pack.snapshot,id).then(item=>item.entity))))
    }catch(error){setError(String(error))}finally{setBusy(false)}
  }
  return <div className="members">
    <h4>{t("相关线路")}</h4>
    {entity.station_links.length===0&&<p className="muted">{t("线路待关联")}</p>}
    {(['infrastructure','service','area_track','route_reference'] as const).map(relation=>{
      const links=entity.station_links.filter(link=>link.relation===relation)
      return links.length>0&&<div className="station-evidence" key={relation}>
        <strong>{{service:t("运营线路"),infrastructure:t("铁路业务线路"),area_track:t("穿越场站范围 · 几何参考"),route_reference:t("列车关系参考")}[relation]}</strong>
        {links.map(link=><button key={link.line.id} onClick={()=>openLine(link.line)}>
          <span>{link.line.name || t("未命名线路")}<small>{t(link.evidence)} · {link.source_ref}</small></span><ArrowUpRight size={14}/>
        </button>)}
      </div>
    })}
    <details className="station-evidence"><summary>{t("物理接轨与待关联")}</summary>
      {tracks==null&&!error&&<button className="secondary compact" disabled={busy} onClick={()=>void load()}>{busy?t("正在查询轨道锚点…"):t("查询物理接轨")}</button>}
      {tracks?.map(line=><button key={line.id} onClick={()=>openLine(line)}>{line.name || t("未命名线路")}<ArrowUpRight size={14}/></button>)}
      {tracks?.length===0&&<small>{t("当前来源没有可确认的物理接轨。")}</small>}
      {error&&<p className="error-text">{error}</p>}
    </details>
  </div>
}

function PathPanel({ pack, selection, show, transfer, dock, visible, reveal }: {
  pack: Pack | null; selection?: Summary & { has_edge: boolean }; show: (geometry: Route['geometry']) => void
  transfer: (start?:Summary,end?:Summary)=>void
  dock: HTMLDivElement|null; visible: boolean; reveal: () => void
}) {
  const [points, setPoints] = useState<{ start?: Summary; end?: Summary }>({})
  const [result, setResult] = useState<Route>()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const job = useRef<string | null>(null)
  useEffect(() => () => {
    const id = job.current
    job.current = null
    if (id) void api.cancel(id)
  }, [])
  async function analyze() {
    if (!pack || !points.start || !points.end) return
    const id = crypto.randomUUID()
    job.current = id
    setBusy(true)
    setResult(undefined)
    setError('')
    try {
      const route = await api.route(pack.snapshot, points.start.id, points.end.id, id)
      if (job.current !== id) return
      setResult(route)
      show(route.geometry)
      reveal()
    } catch (e) {
      if (job.current === id) setError(String(e))
    } finally {
      if (job.current === id) {
        setBusy(false)
        job.current = null
      }
    }
  }
  return <>
    <div className="panel-title"><RouteIcon size={16} /><h2>{t("路径分析")}</h2></div>
    <div className="property-content path">
      <p>{t("从地图或目录选中运营轨道或道路，再设为起点或终点。")}</p>
      {(['start', 'end'] as const).map(which => <div className="path-point" key={which}>
        <small>{which === 'start' ? t("起点边") : t("终点边")}</small>
        <strong>{points[which]?.name ?? t("尚未选择")}</strong>
        <button className="secondary compact" disabled={!selection?.has_edge || busy} onClick={() => {
          setPoints(old => ({ ...old, [which]: selection })); setResult(undefined); setError('')
        }}>{t("使用选中对象")}</button>
      </div>)}
      <button className="primary" disabled={!points.start || !points.end || busy} onClick={() => void analyze()}><RouteIcon size={16} />{t("计算连接路径")}</button>
      {busy && <button className="secondary" onClick={() => { if (job.current) void api.cancel(job.current) }}>{t("取消")}</button>}
      {error && <p className="error-text" role="alert">{error}</p>}
      {result && <button className="secondary" onClick={reveal}>{t("查看本次结果")}</button>}
    </div>
    {result && dock && createPortal(<div className="property-content" hidden={!visible}>
      <div className="result-path"><strong>{(result.meters / 1000).toFixed(2)} km</strong>
        <span>{points.start?.name} → {points.end?.name}</span><span>{result.edges.length} {t("个物理区段 · 自动参考")}</span>
        {points.start?.kind!=='road'&&<button className="secondary compact" onClick={()=>transfer(points.start,points.end)}>{t("转到运行 · 新建通道")}</button>}
        {result.geometry && <button className="secondary compact" onClick={() => show(result.geometry)}>{t("高亮路径")}</button>}
      </div>
    </div>, dock)}
  </>
}

function BusinessLines({ snapshot, selection, openMember }: {
  snapshot: string; selection?: Summary; openMember: (item: Summary) => void
}) {
  const [history, setHistory] = useState<BusinessLineList>()
  const [sources, setSources] = useState<Summary[]>([])
  const [name, setName] = useState('')
  const [split, setSplit] = useState('')
  const [rename,setRename]=useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [page, setPage] = useState<{id: string; offset: number; members: Summary[]; next: number | null}>()

  async function refresh() {
    setHistory(await api.lineEdit<BusinessLineList>(snapshot, {action: 'list'}))
  }
  async function perform(work: () => Promise<void>) {
    setBusy(true)
    setError('')
    try { await work() }
    catch (cause) { setError(String(cause)) }
    finally { setBusy(false) }
  }
  async function open(id: string, offset: number) {
    await perform(async () => {
      const result = await api.lineEdit<{members: Summary[]; next: number | null}>(snapshot, {action: 'members', id, offset})
      setPage({id, offset, ...result})
    })
  }
  async function replay(action: 'undo' | 'redo') {
    await perform(async () => {
      await api.lineEdit(snapshot, {action})
      setPage(undefined)
      await refresh()
    })
  }
  return <details className="property-content" onToggle={event => {
    if (event.currentTarget.open && !history && !busy) void perform(refresh)
  }}>
    <summary>{t("工作区线路拆分与合并")}</summary>
    <p>{t("从目录选取源线路。拆分点按源成员顺序填写，拆分或合并结果保存在当前快照的工作区。")}</p>
    <div className="ops-actions">
      <button className="secondary compact" disabled={busy || selection?.kind !== 'line' || sources.some(line => line.id === selection.id)}
        onClick={() => { if (selection) setSources([...sources, selection]) }}>{t("加入选中线路")}</button>
      <button className="secondary compact" disabled={busy || !history?.undo} onClick={() => void replay('undo')}>{t("撤销线路编辑")}</button>
      <button className="secondary compact" disabled={busy || !history?.redo} onClick={() => void replay('redo')}>{t("重做线路编辑")}</button>
    </div>
    {!!sources.length && <ol>{sources.map(line => <li key={line.id}>{line.name} <button className="secondary compact"
      onClick={() => setSources(sources.filter(item => item.id !== line.id))}>{t("移除")}</button></li>)}</ol>}
    <form onSubmit={event => {
      event.preventDefault()
      void perform(async () => {
        await api.lineEdit(snapshot, {action: 'create', name, sources: sources.map(line => line.id), split_at: split ? Number(split) : null})
        setName(''); setSplit(''); setSources([]); await refresh()
      })
    }}>
      <label>{t("新线路名称")} <input required maxLength={100} value={name} onChange={event => setName(event.target.value)} /></label>
      {sources.length === 1 && <label>{t("拆分点（第几个成员之后）")} <input required type="number" min={1} step={1}
        value={split} onChange={event => setSplit(event.target.value)} /></label>}
      <button className="primary compact" disabled={busy || !sources.length || !name.trim()}>{sources.length === 1 ? t("拆分线路") : t("合并线路")}</button>
    </form>
    {error && <p className="error-text" role="alert">{error}</p>}
    {history?.lines.map(line => <div key={line.id} className="ops-row">
      <button className="secondary compact" disabled={busy} onClick={() => {setRename(line.name);void open(line.id, 0)}}>{line.name} · {line.member_count} {t("个成员")}</button>
      {page?.id === line.id && <>
        <form onSubmit={event=>{
          event.preventDefault()
          void perform(async()=>{
            await api.lineEdit(snapshot,{action:'rename',id:line.id,name:rename})
            await refresh()
          })
        }}>
          <label>{t("工作区名称")}<input required value={rename} maxLength={100} onChange={event=>setRename(event.target.value)}/></label>
          <button className="secondary compact" disabled={busy||!rename.trim()||rename.trim()===line.name}>{t("保存名称")}</button>
        </form>
        <ol className="raw-source-members" start={page.offset + 1}>{page.members.map((item, index) => <li key={`${item.id}-${index}`}>
          <button className="secondary compact" onClick={() => openMember(item)}>{item.name}</button></li>)}</ol>
        <div className="ops-actions">
          {page.offset > 0 && <button className="secondary compact" disabled={busy} onClick={() => void open(line.id, page.offset - 64)}>{t("上一页")}</button>}
          {page.next != null && <button className="secondary compact" disabled={busy} onClick={() => void open(line.id, page.next!)}>{t("下一页")}</button>}
        </div>
      </>}
    </div>)}
  </details>
}

function MetricsPanel({ pack }: { pack: Pack | null }) {
  const [frame, setFrame] = useState(metrics)
  const [stats, setStats] = useState<Stats | null>(null)
  useEffect(() => {
    let closed = false
    const refresh = () => {
      setFrame(metrics())
      void api.diagnostics().then(value => { if (!closed) setStats(value) }).catch(() => {})
    }
    refresh()
    const timer = window.setInterval(refresh, 1200)
    return () => { closed = true; window.clearInterval(timer) }
  }, [pack?.snapshot])
  const counts = stats ? stats.hits + stats.misses : 0
  return <>
    <div className="panel-title"><Activity size={16} /><h2>{t("渲染与数据诊断")}</h2></div>
    <div className="property-content">
      <p className="muted">{t("WebView 动画帧采样 ·")} {frame.samples} {t("帧")}</p>
      <div className="metrics">
        <div><strong>{frame.fps}</strong><small>{t("平均 FPS")}</small></div>
        <div><strong>{frame.p95.toFixed(1)}</strong><small>{t("P95 帧时间 · ms")}</small></div>
      </div>
      <dl>
        <dt>{t("P50 / P99 帧时间")}</dt><dd>{frame.p50.toFixed(1)} / {frame.p99.toFixed(1)} ms</dd>
        <dt>{t("点击至下一帧 P95")}</dt><dd>{frame.selection.toFixed(1)} ms</dd>
        <dt>{t("属性查询 P95")}</dt><dd>{frame.query.toFixed(1)} ms</dd>
        <dt>{t("精细瓦片生成")}</dt><dd>{stats?.precise_tiles??0}</dd>
        <dt>{t("精细瓦片平均生成")}</dt><dd>{stats?.precise_tiles? (stats.precise_micros/1000/stats.precise_tiles).toFixed(2):'—'} ms</dd>
        <dt>{t("精细瓦片磁盘命中")}</dt><dd>{stats?.disk_hits??0}</dd>
        <dt>{t("瓦片缓存命中")}</dt><dd>{counts ? Math.round(100 * stats!.hits / counts) : 0}%</dd>
        <dt>{t("缓存占用")}</dt><dd>{((stats?.bytes ?? 0) / (1024 * 1024)).toFixed(2)} MiB</dd>
        <dt>{t("读取 / 淘汰")}</dt><dd>{stats?.misses ?? 0} / {stats?.evictions ?? 0}</dd>
      </dl>
    </div>
  </>
}
