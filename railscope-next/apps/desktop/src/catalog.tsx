import { t } from './i18n'
import { useVirtualizer } from '@tanstack/react-virtual'
import { ArrowDown, ArrowLeft, ArrowUpRight, Building2, ChevronDown, ChevronRight, CircleHelp, Database, Folder,
  GitBranch, Layers3, LocateFixed, MapPin, Route as RouteIcon, Search, TrainFront, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api, type Bounds, type Conflict, type Filter, type Group, type Kind, type Pack, type Page, type Summary } from './api'
import { LayerTree, type LayerGroup, type Visibility } from './layers'

export const kindNames: Record<Kind, string> = {
  rail: '国铁轨道', metro: '城市轨道', road: '高速公路', station: '车站',
  poi: '交通设施', facility: '铁路设施', line: '业务线路',
}
const classes: Record<string, string> = {
  high_speed: '高速铁路', conventional: '普速铁路', freight: '货运', yard: '站内轨道', siding: '侧线',
  spur: '专用线', crossover: '渡线', subway: '地铁', light_rail: '轻轨', tram: '有轨电车',
  motorway: '高速主线', motorway_link: '高速匝道', rail_station: '铁路车站', metro_station: '地铁车站',
  motorway_junction: '高速出入口', rest_area: '停车区', services: '服务区', toll_booth: '收费站',
  aerodrome: '机场', switch: '道岔', platform: '站台', station_area: '站区',
  station_building:'站房', monorail:'单轨',
  railway_crossing: '铁路交叉点', level_crossing: '平交道口', railway: '铁路线路',
  train: '列车线路关系', road: '道路线路', unknown: '类别待核验',
}
export const className = (value: string) => t(classes[value] ?? value)
export const statusName = (value: string) => ({
  operating: t("运营"), construction: t("在建"), planned: t("规划"), disused: t("停用"), unknown: t("待核验"),
})[value] ?? value

const types: { id: Kind; icon: typeof TrainFront }[] = [
  { id: 'line', icon: GitBranch }, { id: 'station', icon: MapPin }, { id: 'rail', icon: TrainFront },
  { id: 'metro', icon: TrainFront }, { id: 'road', icon: RouteIcon },
  { id: 'facility', icon: Building2 }, { id: 'poi', icon: LocateFixed },
]

export function PackMenu({ packs, active, changing, change, conflicts, onManage }: {
  packs: Pack[]; active: Pack | null; changing: boolean; change: (pack: Pack) => void; conflicts: Conflict[]
  onManage:()=>void
}) {
  const [open, setOpen] = useState(false)
  return <div className="pack-menu">
    <button className="pack-trigger" onClick={() => setOpen(!open)} aria-expanded={open}>
      <Database size={17} /><span>{t(active?.region ?? "数据包")}</span><ChevronDown size={14} />
    </button>
    {open && <div className="pack-popover">
      <div className="popup-title">{t("本地数据包")}<button className="icon" aria-label={t("关闭数据包")} onClick={() => setOpen(false)}><X size={16} /></button></div>
      {packs.length === 0 ? <p>{t("将已生成的数据包放入应用的 packs 目录后重新启动。")}</p>
        : packs.map(pack => <button key={pack.snapshot} disabled={changing} className="pack-choice"
          onClick={() => { change(pack); setOpen(false) }}>
          <span>{t(pack.region)}<small>{pack.snapshot}</small><small>{pack.source_schema>=3?t("原始对象已索引"):t("预建 Z{0}–{1}", pack.min_zoom, pack.max_zoom)}</small></span>
          {active?.snapshot === pack.snapshot ? <b>{t("使用中")}</b> : <ArrowUpRight size={16} />}
        </button>)}
      {active && <p>{active.source} · {active.license}{active.basemap ? ' · Natural Earth' : ''}</p>}
      <button className="secondary compact pack-manage" onClick={()=>{setOpen(false);onManage()}}>{t("管理离线数据")}</button>
      {conflicts.length > 0 && <details open><summary>{t("工作区迁移冲突")}</summary>
        <ul className="conflicts">{conflicts.map(conflict => <li key={`${conflict.source_snapshot}:${conflict.entity}`}>
          {t(conflict.reason)}<small>{conflict.entity} · {conflict.source_snapshot}</small>
        </li>)}</ul></details>}
    </div>}
  </div>
}

export function PackInstall({onInstalled}:{onInstalled:(pack:Pack)=>void}) {
  const [path,setPath]=useState('')
  const [job,setJob]=useState('')
  const [progress,setProgress]=useState<[number,number]>()
  const [status,setStatus]=useState('')
  const [failure,setFailure]=useState('')
  useEffect(()=>{
    if(!job)return
    const timer=window.setInterval(()=>void api.packJob(job).then(value=>{if(value)setProgress(value)}).catch(()=>{}),350)
    return ()=>window.clearInterval(timer)
  },[job])
  async function install(){
    const id=crypto.randomUUID()
    setJob(id);setStatus('');setFailure('');setProgress(undefined)
    try{
      const manifest=await api.install(path.trim(),id)
      onInstalled(manifest)
      setStatus("校验通过，数据包已安装")
      setPath('')
    } catch(e){setFailure(String(e))}
    finally{setJob('')}
  }
  return <div className="local-pack-install"><h3>{t("安装本地快照")}</h3>
        <form className="pack-install" onSubmit={e=>{e.preventDefault();void install()}}>
          <input value={path} onChange={e=>setPath(e.target.value)} placeholder={t("数据包文件夹的绝对路径")} aria-label={t("数据包文件夹")}/>
          <div className="ops-actions"><button className="secondary compact" disabled={!path.trim()||!!job}>{t("校验并安装")}</button>
            {!!job&&<button type="button" className="secondary compact" onClick={()=>void api.packJob(job,true)}>{t("取消")}</button>}
          </div>
          {job&&<><progress value={progress?.[0]??0} max={progress?.[1]||1}/>
            <small>{progress?.[0]===progress?.[1]?t("校验快照…"):t("复制数据…")}</small></>}
          {status&&<small role="status">{t(status)}</small>}{failure&&<small role="alert" className="error-text">{t(failure)}</small>}
        </form>
  </div>
}

export function Catalog({ pack, selected, onSelect, group, setGroup, view, visibility, onLayer, onPreset, workspaceChange }: {
  pack: Pack | null; selected?: string; onSelect: (summary: Summary) => void
  group: Summary | undefined; setGroup: (summary?: Summary) => void; view?: Bounds
  visibility: Visibility; onLayer: (group: LayerGroup, value: boolean) => void; onPreset:(value:Partial<Visibility>)=>void
  workspaceChange: number
}) {
  const [tab, setTab] = useState<'catalog' | 'layers'>('catalog')
  const [query, setQuery] = useState('')
  const [kind, setKind] = useState<Kind>()
  const [groups, setGroups] = useState<Group[]>([])
  const [workGroup, setWorkGroup] = useState<string>()
  const [nearby, setNearby] = useState(false)
  const [result, setResult] = useState<Page>({ items: [], next: null })
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const request = useRef(0)
  const list = useRef<HTMLDivElement>(null)
  const snapshot = pack?.snapshot
  const bbox = nearby ? view : undefined
  const browse = Boolean(query.trim() || kind || group || nearby || workGroup)
  const virtualizer = useVirtualizer({
    count: result.items.length, getScrollElement: () => list.current, estimateSize: () => 57, overscan: 6,
  })

  function filter(cursor?: string): Filter {
    return { query: query.trim(), kind: group ? undefined : kind, parent: group?.id, bbox, cursor }
  }

  useEffect(() => {
    let closed = false
    if (snapshot) void api.groups(snapshot).then(items => { if (!closed) setGroups(items) })
      .catch(error => { if (!closed) setError(String(error)) })
    else setGroups([])
    return () => { closed = true }
  }, [snapshot, workspaceChange])
  useEffect(() => setWorkGroup(undefined), [snapshot, group?.id])

  function fetchPage(snapshot: string, cursor?: string) {
    return workGroup ? api.groupMembers(snapshot, workGroup, cursor) : api.page(snapshot, filter(cursor))
  }

  useEffect(() => {
    const version = ++request.current
    setError('')
    setResult({ items: [], next: null })
    list.current?.scrollTo(0, 0)
    setPending(Boolean(snapshot && browse))
    if (!snapshot || !browse) return
    const timer = window.setTimeout(() => fetchPage(snapshot)
      .then(page => { if (request.current === version) setResult(page) })
      .catch(e => { if (request.current === version) setError(String(e)) })
      .finally(() => { if (request.current === version) setPending(false) }), query.trim() ? 180 : 0)
    return () => { window.clearTimeout(timer); request.current++ }
  }, [snapshot, query, kind, group?.id, bbox, browse, workGroup, workspaceChange])

  async function more() {
    if (!result.next || pending || !snapshot) return
    const version = request.current
    setPending(true)
    try {
      const page = await fetchPage(snapshot, result.next)
      if (version === request.current) setResult({ items: [...result.items, ...page.items], next: page.next })
    } catch (e) {
      if (version === request.current) setError(String(e))
    } finally {
      if (version === request.current) setPending(false)
    }
  }

  function overview() {
    setKind(undefined)
    setGroup(undefined)
    setWorkGroup(undefined)
    setQuery('')
    setNearby(false)
  }

  return <aside className="sidebar" aria-label={t("数据浏览")}>
    <div className="tabs">
      <button className={tab === 'catalog' ? 'active' : ''} onClick={() => setTab('catalog')}><Database size={15} />{t("目录")}</button>
      <button className={tab === 'layers' ? 'active' : ''} onClick={() => setTab('layers')}><Layers3 size={15} />{t("图层")}</button>
    </div>
    {tab === 'catalog' ? <>
      <div className="sidebar-heading">
        <span className="small-label">{t("本地交通地理数据")}</span>
        <h1>{pack ? t("{0}交通网", t(pack.region)) : t("交通网络")}</h1>
      </div>
      <label className="search"><Search size={17} />
        <input aria-label={t("搜索交通实体")} placeholder={t("搜索车站、线路或拼音")} value={query}
          onChange={e => { setWorkGroup(undefined); setQuery(e.target.value) }} maxLength={128} />
        {query && <button className="icon" aria-label={t("清空搜索")} onClick={() => setQuery('')}><X size={15} /></button>}
      </label>
      <div className="section-label">
        {browse ? <button className="back" onClick={overview}><ArrowLeft size={14} />{workGroup ?? group?.name ?? t(kind ? kindNames[kind] : "浏览目录")}</button>
          : <span>{t("按对象浏览")}</span>}
        <button className={nearby ? 'nearby on' : 'nearby'} aria-pressed={nearby} disabled={!pack || !!workGroup} title={t("限制在当前视野")}
          onClick={() => setNearby(!nearby)}><LocateFixed size={15} />{t("当前视野")}</button>
      </div>
      {!browse ? <div className="categories">
        {types.map(({ id, icon: Icon }) => <button key={id} disabled={!pack} onClick={() => setKind(id)}>
          <Icon size={18} /><span>{t(kindNames[id])}</span><small>{(pack?.counts[id] ?? 0).toLocaleString(document.documentElement.lang)}</small>
          <ChevronRight size={15} />
        </button>)}
        {groups.length > 0 && <div className="group-label">{t("工作区分组")}</div>}
        {groups.map(item => <button key={item.name} onClick={() => setWorkGroup(item.name)}>
          <Folder size={18} /><span className="group-name">{item.name}</span><small>{item.count}</small><ChevronRight size={15} />
        </button>)}
      </div> : <>
        <div className="results" ref={list}>
          {error && <p className="sidebar-message" role="alert">{error}</p>}
          {!error && !result.items.length && <p className="sidebar-message">{pending ? t("正在查询…") : t("没有符合条件的对象")}</p>}
          <div style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
            {virtualizer.getVirtualItems().map(virtualRow => {
              const item = result.items[virtualRow.index]
              const Icon = types.find(type => type.id === item.kind)?.icon ?? CircleHelp
              return <button key={item.id} className={selected === item.id ? 'result-row current' : 'result-row'}
                style={{ height: virtualRow.size, transform: `translateY(${virtualRow.start}px)` }}
                onClick={() => onSelect(item)}>
                <Icon size={17} /><span><strong>{item.name}</strong><small>{className(item.class)} · {statusName(item.status)}</small></span>
                <ChevronRight size={15} />
              </button>
            })}
          </div>
          {result.next && <button className="more" disabled={pending} onClick={more}><ArrowDown size={14} />{pending ? t("载入中…") : t("加载更多")}</button>}
        </div>
      </>}
      <div className="sidebar-base"><Database size={14} /> {pack ? t("{0} 个对象", Object.values(pack.counts).reduce((a, b) => a + b, 0).toLocaleString('zh-CN')) : t("等待安装离线数据")}</div>
    </> : <>
      <div className="sidebar-heading"><span className="small-label">{t("地图显示")}</span><h1>{t("交通图层")}</h1></div>
      <LayerTree visibility={visibility} onLayer={onLayer} onPreset={onPreset}/>
    </>}
  </aside>
}
