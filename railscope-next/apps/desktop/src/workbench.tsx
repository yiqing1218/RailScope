import { Activity, ArrowUpRight, Check, ChevronRight, Compass, Crosshair, Database, FileText, Map as MapIcon,
  MapPin, Redo2, Route as RouteIcon, Save, TrainFront, Undo2, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api, type Conflict, type Inspection, type Override, type Pack, type Route, type Stats, type Summary, type Visual, type Workspace } from './api'
import { Catalog, PackMenu, className, kindNames, statusName } from './catalog'
import { layerGroups, MapController, type LayerGroup } from './map'
import { metrics, recordQuery, recordSelection, startMetrics } from './metrics'

const visibilityKey = 'railscope-next.layers'
const defaults = { rail: true, metro: true, road: true, station: true, facility: true }
function loadVisibility() {
  try {
    const saved = JSON.parse(localStorage.getItem(visibilityKey) ?? '{}')
    return Object.fromEntries(layerGroups.map(({ id }) => [id, typeof saved[id] === 'boolean' ? saved[id] : defaults[id]])) as typeof defaults
  } catch {
    return defaults
  }
}

export function App() {
  const [packs, setPacks] = useState<Pack[]>([])
  const [active, setActive] = useState<Pack | null>(null)
  const [changing, setChanging] = useState(true)
  const [globalError, setGlobalError] = useState('')
  const [visibility, setVisibility] = useState(loadVisibility)
  const [details, setDetails] = useState<Inspection>()
  const [group, setGroup] = useState<Summary>()
  const [mapError, setMapError] = useState('')
  const [conflicts, setConflicts] = useState<Conflict[]>([])
  const [workspaceChange, setWorkspaceChange] = useState(0)
  const [detailError, setDetailError] = useState('')
  const [busy, setBusy] = useState(false)
  const [view, setView] = useState<ReturnType<MapController['view']>>()
  const [mode, setMode] = useState<'detail' | 'route' | 'metrics'>('detail')
  const canvas = useRef<HTMLDivElement>(null)
  const controller = useRef<MapController | undefined>(undefined)
  const current = useRef<Pack | null>(null)
  const selection = useRef(0)
  const visualRequest = useRef(0)

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
      }, () => setView(controller.current?.view()), setMapError)
      controller.current = map
      setView(map.view())
      return () => { controller.current = undefined; map.destroy() }
    } catch (error) {
      setMapError(`地图初始化失败 · ${String(error)}`)
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
      void controller.current?.setPack(active).then(() => refreshVisuals(active)).catch(error => setMapError(String(error)))
      void api.conflicts(active.snapshot).then(items => {
        if (current.current?.snapshot === active.snapshot) setConflicts(items)
      }).catch(error => setGlobalError(String(error)))
    }
  }, [active])
  useEffect(() => {
    for (const { id } of layerGroups) controller.current?.setLayer(id, visibility[id])
    localStorage.setItem(visibilityKey, JSON.stringify(visibility))
  }, [visibility])
  useEffect(() => {
    const dismiss = (event: KeyboardEvent) => { if (event.key === 'Escape') clear() }
    window.addEventListener('keydown', dismiss)
    return () => window.removeEventListener('keydown', dismiss)
  }, [])

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
      const result = await api.entity(pack.snapshot, id)
      if (generation !== selection.current) return
      recordQuery(performance.now() - start)
      setDetails(result)
    } catch (error) {
      if (generation === selection.current) setDetailError(String(error))
    } finally {
      if (generation === selection.current) setBusy(false)
    }
  }

  async function change(pack: Pack) {
    if (pack.snapshot === active?.snapshot) return
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
      const geometry = await api.geometry(active.snapshot, details.entity.id)
      if (generation === selection.current) {
        controller.current?.setExact(geometry)
        controller.current?.fit(details.entity.bounds)
      }
    } catch (error) {
      if (generation === selection.current) setDetailError(String(error))
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

  return <div className="app">
    <header className="top">
      <div className="brand"><div className="brand-icon"><TrainFront size={23} strokeWidth={1.7} /></div><strong>RailScope <span>Next</span></strong></div>
      <nav aria-label="工作台">
        <button className={mode === 'detail' ? 'selected' : ''} onClick={() => setMode('detail')}><MapIcon size={17} />地图浏览</button>
        <button className={mode === 'route' ? 'selected' : ''} onClick={() => setMode('route')}><RouteIcon size={17} />路径分析</button>
        <button className={mode === 'metrics' ? 'selected' : ''} onClick={() => setMode('metrics')}><Activity size={17} />诊断</button>
      </nav>
      <PackMenu packs={packs} active={active} changing={changing} change={change} conflicts={conflicts} />
    </header>
    <main className={`workspace ${mode === 'detail' && !details && !busy ? 'inspector-idle' : ''}`}>
      <Catalog pack={active} selected={details?.entity.id} onSelect={item => { void select(item.id, item) }}
        group={group} setGroup={setGroup} view={view?.bounds} visibility={visibility}
        workspaceChange={workspaceChange}
        onLayer={(id: LayerGroup, value: boolean) => setVisibility(old => ({ ...old, [id]: value }))} />
      <section className="map-panel" aria-label="离线交通地图">
        <div className="map-canvas" ref={canvas} />
        <div className="map-tools">
          {active && <><div className="map-title"><Compass size={17} /><span>{active.region} · 交通地图</span></div>
            <button className="map-home" onClick={() => controller.current?.fit(active.bounds, 11)} title="返回数据包范围" aria-label="返回数据包范围"><Crosshair size={17} /></button></>}
        </div>
        {!active && !changing && !globalError && <div className="map-empty">
          <Database size={24} /><h2>尚未加载离线地图</h2><p>将数据包放入 packs 目录后重新启动。</p>
        </div>}
        {(globalError || mapError) && <div className="map-error" role="alert">
          <span>{globalError || mapError}</span><button onClick={() => { setGlobalError(''); setMapError('') }} aria-label="关闭提示"><X size={15} /></button>
        </div>}
        {!globalError && changing && <div className="map-loading">正在校验本地数据包…</div>}
      </section>
      <aside className="inspector" aria-label="工作面板">
        {mode === 'detail' && <>
          <div className="panel-title"><FileText size={16} /><h2>对象属性</h2>{details && <button className="icon" aria-label="清除选中" onClick={clear}><X size={17} /></button>}</div>
          {detailError && <p className="error-text" role="alert">{detailError}</p>}
          {busy ? <div className="blank">正在读取对象…</div> : details && active
            ? <Properties key={details.entity.id} inspection={details} pack={active} exact={exact}
                showMembers={setGroup} update={(state, target) => applyWorkspace(state, target, active)}
                zoomOverride={point => controller.current?.fit([point[0], point[1], point[0], point[1]])} />
            : <div className="blank"><MapPin size={24} /><strong>选择地图对象</strong><p>点击地图或打开搜索结果即可查看资料。</p></div>}
        </>}
        {mode === 'route' && <PathPanel key={active?.snapshot} pack={active} selection={details?.entity}
          show={geometry => controller.current?.setExact(geometry)} />}
        {mode === 'metrics' && <MetricsPanel pack={active} />}
      </aside>
    </main>
    <footer className="statusbar">
      <span><span className={active ? 'dot online' : 'dot'} />{active ? '离线 · 本地快照' : '无活动快照'}</span>
      <span className="snapshot">{active?.snapshot}</span>
      <span className="coordinates">{view && `${view.center.lng.toFixed(4)}° E   ${view.center.lat.toFixed(4)}° N`}</span>
      <span className="zoom">{view ? `Z ${view.zoom.toFixed(1)}` : ''}</span>
    </footer>
  </div>
}

function Properties({ inspection, pack, exact, showMembers, update, zoomOverride }: {
  inspection: Inspection; pack: Pack; exact: () => void; showMembers: (line: Summary) => void
  update: (state: Workspace, target: Inspection) => void
  zoomOverride: (point: [number, number]) => void
}) {
  const { entity, workspace } = inspection
  const [tab, setTab] = useState<'attributes' | 'workspace'>('attributes')
  const [draft, setDraft] = useState<Override>(workspace.value)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [confirmation, setConfirmation] = useState('')
  const [coordinates, setCoordinates] = useState<[string, string]>(['', ''])
  useEffect(() => {
    setDraft(workspace.value)
    setCoordinates(workspace.value.point ? [String(workspace.value.point[0]), String(workspace.value.point[1])] : ['', ''])
  }, [workspace])
  const pointEntity = entity.has_geometry && !entity.has_edge
    && entity.bounds[0] === entity.bounds[2] && entity.bounds[1] === entity.bounds[3]
  const edited = draft.name !== workspace.value.name || draft.note !== workspace.value.note || draft.color !== workspace.value.color
    || draft.group !== workspace.value.group
    || coordinates.some((coordinate, i) => coordinate !== String(workspace.value.point?.[i] ?? ''))

  async function change(action: 'apply' | 'undo' | 'redo') {
    setError('')
    setConfirmation('')
    setSaving(true)
    try {
      const [x, y] = coordinates.map(coordinate => coordinate.trim())
      if (action === 'apply' && (x || y) && (!x || !y || !Number.isFinite(Number(x)) || !Number.isFinite(Number(y)))) {
        setError('请填写有效的经纬度，或同时留空以恢复原始位置。')
        return
      }
      const result = action === 'apply'
        ? await api.update(pack.snapshot, entity.id, workspace.revision, {
            ...draft, group: draft.group.trim(), point: x && y ? [Number(x), Number(y)] : null,
          })
        : await api.replay(pack.snapshot, entity.id, workspace.revision, action === 'redo')
      update(result, inspection)
      if (result.value.point) zoomOverride(result.value.point)
      setConfirmation({ apply: '已保存到工作区', undo: '已撤销', redo: '已重做' }[action])
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
      <span className="object-type">{kindNames[entity.kind]}</span>
      <h3>{workspace.value.name || entity.name}</h3>
      <span>{className(entity.class)} · {statusName(entity.status)}</span>
    </div>
    <div className="tabs property-tabs">
      <button className={tab === 'attributes' ? 'active' : ''} onClick={() => setTab('attributes')}>来源与关联</button>
      <button className={tab === 'workspace' ? 'active' : ''} onClick={() => setTab('workspace')}>工作区</button>
    </div>
    {tab === 'attributes' ? <div className="property-content">
      <dl>
        {workspace.value.name && <><dt>原始名称</dt><dd>{entity.name}</dd></>}
        <dt>快照</dt><dd>{pack.snapshot}</dd>
        <dt>来源</dt><dd>{entity.source_ref} · v{entity.source_version}</dd>
        <dt>核验</dt><dd>{entity.verification === 'verified' ? '已核验' : '自动参考'} · {entity.confidence === 'low' ? '低' : '中'}置信度</dd>
        <dt>实体 ID</dt><dd className="monospace">{entity.id}</dd>
      </dl>
      {entity.has_geometry && <button className="secondary" onClick={exact}><Crosshair size={15} />查看精确几何</button>}
      {workspace.value.point && <button className="secondary" onClick={() => zoomOverride(workspace.value.point!)}><MapPin size={15} />定位工作区坐标</button>}
      {entity.kind === 'line' && <button className="secondary" onClick={() => showMembers(entity)}><ChevronRight size={15} />浏览线路成员</button>}
      {entity.memberships.length > 0 && <div className="members"><h4>所属线路</h4>{entity.memberships.map(line =>
        <button key={line.id} onClick={() => showMembers(line)}>{line.name}<ArrowUpRight size={14} /></button>)}</div>}
      <details><summary>原始 OSM 标签 · {Object.keys(entity.tags).length}</summary>
        <dl className="tags">{Object.entries(entity.tags).flatMap(([k, v]) => [<dt key={`k-${k}`}>{k}</dt>, <dd key={`v-${k}`}>{v}</dd>])}</dl>
      </details>
    </div> : <form className="property-content edit-form" onSubmit={e => { e.preventDefault(); void change('apply') }}>
      <label>工作区名称<input value={draft.name} placeholder={entity.name} maxLength={120} onChange={e => setDraft({ ...draft, name: e.target.value })} /></label>
      <label>工作区分组<input value={draft.group} placeholder="留空则移出分组" maxLength={80}
        onChange={e => setDraft({ ...draft, group: e.target.value })} /></label>
      <label>备注<textarea value={draft.note} rows={5} maxLength={4000} onChange={e => setDraft({ ...draft, note: e.target.value })} /></label>
      {entity.kind !== 'line' && <label>地图颜色<span className="color-row">
        <input type="color" value={draft.color || '#176ce0'} onChange={e => setDraft({ ...draft, color: e.target.value })} />
        <button type="button" className="secondary compact" disabled={!draft.color} onClick={() => setDraft({ ...draft, color: '' })}>恢复默认</button>
      </span></label>}
      {pointEntity && <div className="point-fields">
        <label>工作区经度<input inputMode="decimal" value={coordinates[0]} placeholder={String(entity.bounds[0])}
          onChange={e => setCoordinates([e.target.value, coordinates[1]])} /></label>
        <label>工作区纬度<input inputMode="decimal" value={coordinates[1]} placeholder={String(entity.bounds[1])}
          onChange={e => setCoordinates([coordinates[0], e.target.value])} /></label>
      </div>}
      <div className="edit-actions">
        <button className="primary" type="submit" disabled={!edited || saving}><Save size={15} />保存</button>
        <button className="icon framed" type="button" aria-label="撤销" disabled={!workspace.can_undo || saving} onClick={() => void change('undo')}><Undo2 size={17} /></button>
        <button className="icon framed" type="button" aria-label="重做" disabled={!workspace.can_redo || saving} onClick={() => void change('redo')}><Redo2 size={17} /></button>
      </div>
      {error && <p className="error-text" role="alert">{error}</p>}
      {confirmation && <p className="success" role="status"><Check size={15} />{confirmation}</p>}
    </form>}
  </div>
}

function PathPanel({ pack, selection, show }: {
  pack: Pack | null; selection?: Summary & { has_edge: boolean }; show: (geometry: Route['geometry']) => void
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
    <div className="panel-title"><RouteIcon size={16} /><h2>路径分析</h2></div>
    <div className="property-content path">
      <p>从地图或目录选中运营轨道或道路，再设为起点或终点。</p>
      {(['start', 'end'] as const).map(which => <div className="path-point" key={which}>
        <small>{which === 'start' ? '起点边' : '终点边'}</small>
        <strong>{points[which]?.name ?? '尚未选择'}</strong>
        <button className="secondary compact" disabled={!selection?.has_edge || busy} onClick={() => {
          setPoints(old => ({ ...old, [which]: selection })); setResult(undefined); setError('')
        }}>使用选中对象</button>
      </div>)}
      <button className="primary" disabled={!points.start || !points.end || busy} onClick={() => void analyze()}><RouteIcon size={16} />计算连接路径</button>
      {busy && <button className="secondary" onClick={() => { if (job.current) void api.cancel(job.current) }}>取消</button>}
      {error && <p className="error-text" role="alert">{error}</p>}
      {result && <div className="result-path"><strong>{(result.meters / 1000).toFixed(2)} km</strong><span>{result.edges.length} 个物理区段 · 自动参考</span>
        {result.geometry && <button className="secondary compact" onClick={() => show(result.geometry)}>高亮路径</button>}</div>}
    </div>
  </>
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
    <div className="panel-title"><Activity size={16} /><h2>渲染与数据诊断</h2></div>
    <div className="property-content">
      <p className="muted">WebView 动画帧采样 · {frame.samples} 帧</p>
      <div className="metrics">
        <div><strong>{frame.fps}</strong><small>平均 FPS</small></div>
        <div><strong>{frame.p95.toFixed(1)}</strong><small>P95 帧时间 · ms</small></div>
      </div>
      <dl>
        <dt>P50 / P99 帧时间</dt><dd>{frame.p50.toFixed(1)} / {frame.p99.toFixed(1)} ms</dd>
        <dt>点击至下一帧 P95</dt><dd>{frame.selection.toFixed(1)} ms</dd>
        <dt>属性查询 P95</dt><dd>{frame.query.toFixed(1)} ms</dd>
        <dt>瓦片缓存命中</dt><dd>{counts ? Math.round(100 * stats!.hits / counts) : 0}%</dd>
        <dt>缓存占用</dt><dd>{((stats?.bytes ?? 0) / (1024 * 1024)).toFixed(2)} MiB</dd>
        <dt>读取 / 淘汰</dt><dd>{stats?.misses ?? 0} / {stats?.evictions ?? 0}</dd>
      </dl>
    </div>
  </>
}
