import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { ChevronDown, ChevronRight, GitBranch, Route, TrainFront, TramFront } from 'lucide-react'
import { t } from './i18n'
import { corridorKey, formatTime, runCorridorKey, type Corridor, type Run, type Service } from './operations-api'

export type NetworkMode = Service['mode']
export type LineGroup = {id:string; mode:NetworkMode; name:string; corridors:Corridor[]}
export const networkName = {rail:'国铁',metro:'城市轨道'} as const

export function operationLines(corridors:Corridor[]):LineGroup[] {
  const groups=new Map<string,LineGroup>()
  for(const corridor of corridors){
    const mode=corridor.mode??'rail'
    const reference=corridor.source_id.startsWith('reference:shanghai-metro:')?corridor.source_id:undefined
    const lines=[...new Map(corridor.lines?.map(line=>[line.id,line])).values()]
    const name=reference?corridor.name.match(/^上海地铁.+?号线/)?.[0]:undefined
    const key=reference??lines.map(line=>line.id).sort().join('/')
    const id=`${mode}:${key}`
    const group=groups.get(id)??{id,mode,name:name??(lines.length?lines.map(line=>line.name).join(' → '):t('线路待归属')),corridors:[]}
    group.corridors.push(corridor)
    groups.set(id,group)
  }
  return [...groups.values()].sort((a,b)=>Number(a.id.endsWith(':'))-Number(b.id.endsWith(':'))||a.name.localeCompare(b.name,'zh-CN'))
}

export type DirectorySelection={line?:string; corridor?:string}
type Props={
  groups:LineGroup[]; runs:Run[]; services:Map<string,Service>; date:string; dates:string[];
  datesByMode?: Partial<Record<NetworkMode,string[]>>;
  selected:string; scope:DirectorySelection; onDate:(date:string)=>void; onScope:(scope:DirectorySelection)=>void; onRun:(run:Run)=>void;
}

function Branch({open,toggle,icon,name,count,current=false,runList=false,children}:{open:boolean;toggle:()=>void;icon:ReactNode;
  name:string;count:number;current?:boolean;runList?:boolean;children:ReactNode}){
  const id=useId()
  return <li>
    <button type="button" className="ops-tree-row" aria-label={name} aria-expanded={open} aria-controls={id} aria-current={current?'true':undefined}
      title={name} onClick={toggle}>
      {open?<ChevronDown size={13}/>:<ChevronRight size={13}/>} {icon}<span>{name}</span><small>{count}</small>
    </button>
    <ul id={id} hidden={!open} className={runList?'ops-tree-run-list':undefined}>{open&&children}</ul>
  </li>
}

export function OperationsDirectory({groups,runs,services,date,dates,datesByMode,selected,scope,onDate,onScope,onRun}:Props){
  const [expanded,setExpanded]=useState<Record<string,boolean>>({})
  const [search,setSearch]=useState('')
  const query=search.trim().toLocaleLowerCase()
  const dateList=useId()
  const matches=(name:string)=>name.toLocaleLowerCase().includes(query)
  const open=(id:string,fallback:boolean)=>query!==''||(expanded[id]??fallback)
  const toggle=(id:string,isOpen:boolean)=>setExpanded(old=>({...old,[id]:!isOpen}))
  const activeRun=runs.find(run=>run.id===selected)
  const activeKey=activeRun?runCorridorKey(activeRun):''
  const activeMode=activeRun?services.get(activeRun.service_id)?.mode:undefined
  const activeGroup=groups.find(group=>group.corridors.some(c=>corridorKey(c)===activeKey))?.id
  const scopedMode=groups.find(group=>group.id===scope.line)?.mode
  const tree=useRef<HTMLElement>(null)
  useEffect(()=>{
    if(!scopedMode||!scope.line||!scope.corridor)return
    setExpanded(old=>({...old,[scopedMode]:true,[scope.line!]:true,[scope.corridor!]:true}))
  },[scopedMode,scope.line,scope.corridor])
  useEffect(()=>{
    if(!activeMode||!selected)return
    const inner=activeKey||`unbound-${activeMode}`
    setExpanded(old=>({...old,[activeMode]:true,[inner]:true,...(activeGroup&&{[activeGroup]:true})}))
    const frame=requestAnimationFrame(()=>tree.current?.querySelector('.ops-tree-run[aria-current]')?.scrollIntoView({block:'nearest'}))
    return ()=>cancelAnimationFrame(frame)
  },[activeMode,activeGroup,activeKey,selected])
  const assigned=new Map<string,Run[]>()
  for(const run of runs){
    const key=runCorridorKey(run)||`unbound-${services.get(run.service_id)?.mode??'rail'}`
    const children=assigned.get(key)??[]
    children.push(run)
    assigned.set(key,children)
  }
  const rows=(items:Run[])=>items.map(run=><li key={run.id}><button type="button" className="ops-tree-row ops-tree-run"
    aria-current={selected===run.id?'true':undefined} onClick={()=>onRun(run)}>
    <TrainFront size={13}/><span>{run.train_number}</span><small>{formatTime(run.stops[0]?.departure_time_s??run.stops[0]?.arrival_time_s)}</small>
  </button></li>)
  return <div className="ops-directory">
    <div className="ops-directory-filters">
      <label>{t('运营日期')}<input type="date" aria-label={t('运营日期')} list={dateList} value={date} onChange={e=>onDate(e.target.value)}/></label>
      <datalist id={dateList}>{dates.map(day=><option key={day} value={day}/>)}</datalist>
      <input aria-label={t('搜索运行目录')} value={search} placeholder={t('搜索线路、通道或车次')} onChange={e=>setSearch(e.target.value)}/>
    </div>
    <nav ref={tree} className="ops-tree" aria-label={t('运行目录')}>
      <ul>{(['rail','metro'] as const).map(mode=>{
        const branches=groups.filter(group=>group.mode===mode)
        const modeRuns=runs.filter(run=>services.get(run.service_id)?.mode===mode)
        const unbound=assigned.get(`unbound-${mode}`)??[]
        const recent=datesByMode?.[mode]?.[0]
        const networkOpen=open(mode,true)
        const Icon=mode==='metro'?TramFront:TrainFront
        const branchesShown=branches.flatMap(group=>{
          const children=group.corridors.flatMap(corridor=>{
            const key=corridorKey(corridor)
            const scheduled=assigned.get(key)??[]
            const all=matches(group.name)||matches(corridor.name)
            const found=all?scheduled:scheduled.filter(run=>matches(run.train_number))
            if(!all&&!found.length)return []
            const corridorOpen=open(key,activeKey===key)
            const label=corridor.source_id.startsWith('reference:shanghai-metro:')?corridor.name.replace(/^上海地铁.+?号线\s*/, ''):corridor.name
            return [<Branch key={key} name={label} icon={<GitBranch size={14}/>} count={scheduled.length} open={corridorOpen} runList
              current={scope.corridor===key||activeKey===key} toggle={()=>{
                toggle(key,corridorOpen&&scope.corridor===key&&!activeRun);onScope({line:group.id,corridor:key})
              }}>
              {found.length?rows(found):<li className="ops-tree-hint">{t('当日无车次')}</li>}
            </Branch>]
          })
          if(!children.length)return []
          const count=group.corridors.reduce((sum,corridor)=>sum+(assigned.get(corridorKey(corridor))?.length??0),0)
          const lineOpen=open(group.id,scope.line===group.id||mode==='metro'||group.corridors.some(c=>corridorKey(c)===activeKey))
          return [<Branch key={group.id} name={group.name} icon={<Route size={14}/>} count={count} open={lineOpen}
            current={scope.line===group.id&&!scope.corridor&&!activeRun} toggle={()=>{
              toggle(group.id,lineOpen&&scope.line===group.id&&!scope.corridor&&!activeRun);onScope({line:group.id})
            }}>{children}</Branch>]
        })
        const foundUnbound=unbound.filter(run=>matches(run.train_number))
        const unboundOpen=open(`unbound-${mode}`,!!activeRun&&unbound.includes(activeRun))
        if(query&&!branchesShown.length&&!foundUnbound.length)return null
        return <Branch key={mode} name={t(networkName[mode])} icon={<Icon size={15}/>} count={modeRuns.length} open={networkOpen} toggle={()=>toggle(mode,networkOpen)}>
          {!modeRuns.length&&recent&&recent!==date&&<li><button className="ops-tree-date" onClick={()=>{
            if(scope.line&&!groups.some(group=>group.id===scope.line&&group.mode===mode))onScope({})
            onDate(recent)
          }}>{t('查看最近计划 · {0}',recent)}</button></li>}
          {branchesShown}
          {foundUnbound.length>0&&<Branch name={t('未绑定走行通道')} icon={<GitBranch size={14}/>} count={unbound.length} open={unboundOpen} runList
            toggle={()=>toggle(`unbound-${mode}`,unboundOpen)}>{rows(foundUnbound)}</Branch>}
          {!branches.length&&!unbound.length&&<li className="ops-tree-hint">{t('暂无通道或车次')}</li>}
        </Branch>
      })}</ul>
      {query&&!groups.some(group=>matches(group.name)||group.corridors.some(c=>matches(c.name)))&&!runs.some(run=>matches(run.train_number))&&
        <p className="ops-tree-hint">{t('无匹配线路、通道或车次')}</p>}
    </nav>
  </div>
}
