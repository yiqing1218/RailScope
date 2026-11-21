import { forwardRef,useEffect,useImperativeHandle,useRef,useState } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { ArrowRight, Crosshair, Download, GitBranch, Pause, Play, Plus, RotateCcw, TrainFront, Upload } from 'lucide-react'
import type { Geometry } from 'geojson'
import type { Entity } from './api'
import type { MapTrain } from './map'
import {
  chinaDate,datedRunLabel,describeError,formatTime,opsApi,parseTime,sourceLabel,stateLabel,trustLabel,
  type Clock,type Corridor,type Level,type OperationList,type Provenance,type Run,type RunDetail,type Scenario,
  type ScheduleFormat,type Service,type Simulation,type Stop,
} from './operations-api'

export type OperationsHandle={ selectRun:(id:string)=>void }
export type OperationsMap={
  setTrains:(trains:MapTrain[],selected?:string)=>void
  showPath:(shape:Geometry|null)=>void
  focusTrain:(point:[number,number],follow:boolean)=>void
}
type Props={snapshot?:string; visible:boolean; selection?:Entity; map:OperationsMap}
const blank:OperationList={corridors:[],services:[],runs:[],scenarios:[],assignments:[],trainsets:[],stock_types:[]}
const userSource:Provenance={source_kind:'user_defined',source_url_or_reference:'工作区人工录入'}
const initialScenario:Scenario={id:'',name:'假设性场景',headway_s:120,turnaround_s:900,delays:{}}
const storedLevel=():Level=>{
  const value=localStorage.getItem('railscope.ops.level')
  return value==='l1'||value==='l3'?value:'l2'
}

export const OperationsWorkbench=forwardRef<OperationsHandle,Props>(function OperationsWorkbench(
  {snapshot,visible,selection,map},ref
){
  const [tab,setTab]=useState<'corridor'|'trains'|'data'|'scenario'>('corridor')
  const [data,setData]=useState<OperationList>(blank)
  const [selected,setSelected]=useState('')
  const [editor,setEditor]=useState<Run>()
  const [detail,setDetail]=useState<RunDetail>()
  const [clock,setClock]=useState<Clock>({time_s:28800,rate:1,playing:false})
  const [level,setLevel]=useState<Level>(storedLevel)
  const [scenario,setScenario]=useState(initialScenario)
  const [simulation,setSimulation]=useState<Simulation>()
  const [analysis,setAnalysis]=useState<Simulation>()
  const [search,setSearch]=useState('')
  const [date,setDate]=useState(chinaDate)
  const [follow,setFollow]=useState(false)
  const [busy,setBusy]=useState(false)
  const [notice,setNotice]=useState('')
  const [error,setError]=useState('')
  const [corridor,setCorridor]=useState<Corridor>()
  const list=useRef<HTMLDivElement>(null)
  const propsMap=useRef(map)
  propsMap.current=map
  const requestEpoch=useRef(0)
  const snapshotRef=useRef(snapshot)
  snapshotRef.current=snapshot

  useImperativeHandle(ref,()=>({selectRun:id=>{setSelected(id);setTab('trains')}}),[])
  const selectionDate=data.runs.find(run=>run.id===selected)?.service_date
  const filterDate=selectionDate ?? date
  const filtered=data.runs.filter(run=>run.service_date===filterDate && run.train_number.toLowerCase().includes(search.trim().toLowerCase()))
  const available=data.runs.filter(run=>run.service_date===filterDate)
  const runIds=available.map(run=>run.id)
  const batchKey=runIds.join(',')
  const virtual=useVirtualizer({count:filtered.length,getScrollElement:()=>list.current,estimateSize:()=>36,overscan:6})
  const selectedState=simulation?.states.find(state=>state.run_id===selected)

  async function reload() {
    if(!snapshot) return
    const next=await opsApi.list(snapshot)
    if (snapshotRef.current===snapshot) setData(next)
    return next
  }

  async function execute(task:()=>Promise<unknown>,message?:string,refresh=true) {
    if(!snapshot) return
    setBusy(true);setError('');setNotice('')
    try {
      await task()
      if(refresh) await reload()
      if(message) setNotice(message)
    } catch(e){setError(describeError(e))}
    finally {setBusy(false)}
  }

  useEffect(()=>{
    requestEpoch.current++
    setSelected('');setEditor(undefined);setDetail(undefined);setCorridor(undefined);setSimulation(undefined)
    setData(blank)
    propsMap.current.setTrains([]);propsMap.current.showPath(null)
    void reload().catch(e=>setError(describeError(e)))
  },[snapshot])
  useEffect(()=>{
    if(!snapshot || !selected){setDetail(undefined);propsMap.current.showPath(null);return}
    const epoch=++requestEpoch.current
    setDetail(undefined)
    void opsApi.getRun(snapshot,selected).then(result=>{
      if(epoch!==requestEpoch.current)return
      setDetail(result)
      propsMap.current.showPath(result.geometry)
    }).catch(e=>setError(describeError(e)))
    return ()=>{requestEpoch.current++}
  },[snapshot,selected,data])
  useEffect(()=>{localStorage.setItem('railscope.ops.level',level)},[level])

  async function control(action:string,value?:number){
    try {setClock(await opsApi.clock(action,value))}
    catch(e){setError(describeError(e))}
  }

  useEffect(()=>{
    if(!snapshot || !batchKey){propsMap.current.setTrains([]);return}
    let closed=false
    let timer:number|null=null
    const step=async()=>{
      try {
        const time=await opsApi.clock('get')
        const result=await opsApi.simulate(snapshot,batchKey.split(',').slice(0,1000),time.time_s,level,scenario)
        if(closed)return
        setClock(time);setSimulation(result)
        const trains:MapTrain[]=result.states.flatMap(state=>{
          if(state.lon==null||state.lat==null||!['moving','stopped'].includes(state.status))return []
          const run=data.runs.find(run=>run.id===state.run_id)
          const mode=data.services.find(service=>service.id===run?.service_id)?.mode??'rail'
          return [{id:state.run_id,label:run?.train_number??state.run_id,point:[state.lon,state.lat],bearing:state.heading_deg??undefined,kind:mode}]
        })
        propsMap.current.setTrains(trains,selected)
        if(follow){
          const train=trains.find(train=>train.id===selected)
          if(train) propsMap.current.focusTrain(train.point,true)
        }
        if(time.playing) timer=window.setTimeout(step,230)
      } catch(e) {if(!closed)setError(describeError(e))}
    }
    void step()
    return ()=>{closed=true;if(timer!=null)window.clearTimeout(timer)}
  },[snapshot,batchKey,selected,level,scenario,data,follow,clock.playing,clock.playing?null:clock.time_s,clock.rate])

  const changeRun=(id:string)=>{setSelected(id);setEditor(undefined);setTab('trains')}
  const selectClockStart=()=>void control('seek',detail?.chart[0]?.departure_time_s??detail?.chart[0]?.arrival_time_s??28800)
  const corridorList=data.corridors.filter(item=>item.snapshot_id===snapshot)
  return <div className="ops-workbench" hidden={!visible}>
    <div className="panel-title"><TrainFront size={17}/><h2>运行工作台</h2></div>
    <div className="ops-tabs" role="tablist" aria-label="运行工作台">
      {([['corridor','通道'],['trains','车次'],['data','导入与车辆'],['scenario','资源分析']] as const).map(([key,label])=>
        <button key={key} type="button" role="tab" aria-selected={tab===key} onClick={()=>setTab(key)}>{label}</button>)}
    </div>
    {error&&<p role="alert" className="error-text ops-message">{error}</p>}
    {notice&&<p role="status" className="success ops-message">{notice}</p>}
    {!snapshot?<p className="ops-message">请先加载交通数据包。</p>:<>
      {tab==='corridor'&& <CorridorEditor snapshot={snapshot} corridors={corridorList} selection={selection}
        current={corridor} setCurrent={setCorridor} execute={execute} busy={busy}
        demoReady={runs=>{if(runs[0]){setSelected(runs[0].id);setDate(runs[0].service_date);setTab('trains');void control('seek',28800)}}}/>}
      {tab==='trains'&&<div className="ops-pane">
        <div className="ops-fields two">
          <label>运营日期<input type="date" aria-label="运营日期" value={filterDate}
            onChange={e=>{setSelected('');setEditor(undefined);setDate(e.target.value)}}/></label>
          <label>车次查询<input value={search} placeholder="车次号" onChange={e=>setSearch(e.target.value)}/></label>
        </div>
        <div className="ops-list" ref={list} aria-label="车次列表">
          {filtered.length===0&&<p className="muted">当日暂无匹配车次。</p>}
          <div className="ops-list-inner" style={{height:virtual.getTotalSize()}}>
            {virtual.getVirtualItems().map(row=>{
              const run=filtered[row.index]
              return <button key={run.id} type="button" className={run.id===selected?'active':''}
                style={{transform:`translateY(${row.start}px)`}} onClick={()=>changeRun(run.id)}>
                <TrainFront size={15}/><span>{datedRunLabel(run)}</span><small>{sourceLabel[run.provenance.source_kind]}</small>
              </button>
            })}
          </div>
        </div>
        <div className="ops-actions">
          <button className="secondary" disabled={!data.services.length} onClick={()=>setEditor(newRun(data.services[0],date))}><Plus size={14}/>运行实例</button>
          {detail&&<button className="secondary" onClick={()=>setEditor(detail.run)}>编辑时刻</button>}
        </div>
        {editor&&<RunEditor run={editor} services={data.services} corridors={corridorList} selection={selection}
          setRun={setEditor} busy={busy} save={run=>void execute(async()=>{
            const saved=await opsApi.saveRun(snapshot,run);setSelected(saved.id);setEditor(undefined)
          },'已保存计划版本')} close={()=>setEditor(undefined)}/>}
        {detail&&!editor&&<>
          <h3>{detail.run.train_number}</h3>
          <p className="muted">{detail.run.service_date} · {detail.run.time_zone} · 计划版本 {detail.run.plan_version}</p>
          <p>{detail.corridor?trustLabel[detail.corridor.verification_status]:'未绑定走行通道'} · {sourceLabel[detail.run.provenance.source_kind]}</p>
          <p className="muted">{detail.run.provenance.source_url_or_reference}</p>
          <p>车组 {detail.trainset?.identifier || detail.trainset?.name || '未知'} · 车型 {detail.stock_type?.name || '未知'}</p>
          {detail.diagnostics?.map((d,i)=><p key={i} className="muted">{d.message}</p>)}
          <div className="ops-actions">
            <button className="secondary" onClick={selectClockStart}><RotateCcw size={14}/>跳到发车</button>
            <button className="secondary" disabled={!detail.geometry} onClick={()=>propsMap.current.showPath(detail.geometry)}><GitBranch size={14}/>高亮通道</button>
            <button className="secondary" disabled={selectedState?.lon==null||selectedState.lat==null}
              onClick={()=>selectedState?.lon!=null&&selectedState.lat!=null&&propsMap.current.focusTrain([selectedState.lon,selectedState.lat],false)}>
              <Crosshair size={14}/>定位列车
            </button>
          </div>
          <table className="ops-table"><thead><tr><th>车站</th><th>到达</th><th>出发</th></tr></thead><tbody>
            {detail.chart.map((stop,i)=><tr key={i}><td title={stop.station_id}>{stop.station_name ?? stop.station_id}</td>
              <td>{formatTime(stop.arrival_time_s)}</td><td>{formatTime(stop.departure_time_s)}</td></tr>)}
          </tbody></table>
        </>}
      </div>}
      {tab==='data'&&<OperationsData snapshot={snapshot} data={data} selected={selected} date={filterDate} execute={execute} busy={busy}/>}
      {tab==='scenario'&&<div className="ops-pane">
        <p className="muted">假设性场景分析。运行参数和追踪间隔由工作区设置。</p>
        <label>场景<select value={scenario.id} onChange={e=>setScenario(data.scenarios.find(s=>s.id===e.target.value)??initialScenario)}>
          <option value="">新建场景</option>{data.scenarios.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}
        </select></label>
        <label>场景名称<input value={scenario.name} onChange={e=>setScenario({...scenario,name:e.target.value})}/></label>
        <div className="ops-fields two">
          <label>追踪间隔（秒）<input type="number" min={0} max={3600} value={scenario.headway_s}
            onChange={e=>setScenario({...scenario,headway_s:e.target.valueAsNumber||0})}/></label>
          <label>车组周转（秒）<input type="number" min={0} max={14400} value={scenario.turnaround_s}
            onChange={e=>setScenario({...scenario,turnaround_s:e.target.valueAsNumber||0})}/></label>
        </div>
        <label>所选列车首发延误（秒）<input disabled={!selected} type="number" min={0} max={86400}
          value={scenario.delays[selected]??0} onChange={e=>setScenario({...scenario,delays:{...scenario.delays,[selected]:e.target.valueAsNumber||0}})}/></label>
        <div className="ops-actions">
          <button className="secondary" disabled={busy} onClick={()=>void execute(async()=>setScenario(await opsApi.saveScenario(snapshot,scenario)),'场景已保存')}>保存场景</button>
          <button className="primary" disabled={busy||!runIds.length} onClick={()=>void execute(async()=>{
            setLevel('l3');setAnalysis(await opsApi.simulate(snapshot,runIds.slice(0,1000),clock.time_s,'l3',scenario,true))
          },'资源分析已计算',false)}>计算占用和冲突</button>
        </div>
        {analysis&&<><p>{analysis.occupancies.length} 项占用 · {analysis.conflicts.length} 项冲突</p>
          {analysis.diagnostics.map((d,i)=><p className="muted" key={i}>{d.message}</p>)}
          <ul className="ops-conflicts">{analysis.conflicts.slice(0,60).map((c,i)=><li key={i}>{resourceLabel[c.conflict_type]??c.conflict_type}
            {' · '}{formatTime(c.start_time_s)} 至 {formatTime(c.end_time_s)}
            <small>{data.runs.find(r=>r.id===c.train_run_a)?.train_number} / {data.runs.find(r=>r.id===c.train_run_b)?.train_number}</small></li>)}</ul>
        </>}
      </div>}
      <div className="ops-time-panel">
        <div className="ops-time-top">
          <strong aria-label="运行时钟">{formatTime(clock.time_s)}</strong>
          <button className="icon framed" aria-label={clock.playing?'暂停模拟':'播放模拟'} disabled={!runIds.length}
            onClick={()=>void control(clock.playing?'pause':'play')}>{clock.playing?<Pause size={16}/>:<Play size={16}/>}</button>
          <select aria-label="播放倍率" value={clock.rate} onChange={e=>void control('rate',Number(e.target.value))}>
            {[.25,.5,1,2,5,10,30,60].map(r=><option key={r} value={r}>{r}×</option>)}
          </select>
          <select aria-label="仿真层级" value={level} onChange={e=>setLevel(e.target.value as Level)}>
            <option value="l1">L1 计划</option><option value="l2">L2 径路</option><option value="l3">L3 场景</option>
          </select>
        </div>
        <div className="ops-time-top">
          <label className="ops-inline"><input type="checkbox" checked={follow} disabled={!selected} onChange={e=>setFollow(e.target.checked)}/>跟随选中车次</label>
          {selectedState&&<span>{stateLabel[selectedState.status]} {selectedState.speed_mps!=null?`${(selectedState.speed_mps*3.6).toFixed(1)} km/h`:''}</span>}
        </div>
        <form className="ops-time-seek" onSubmit={e=>{
          e.preventDefault()
          try {const time=parseTime(String(new FormData(e.currentTarget).get('seek')));if(time!=null)void control('seek',time)}
          catch(error){setError(describeError(error))}
        }}>
          <input name="seek" aria-label="跳转至时刻" placeholder="HH:MM:SS，可跨日" />
          <button className="secondary">跳转</button>
        </form>
        <RunGraph detail={detail} time={clock.time_s} distance={selectedState?.distance_m??null} seek={time=>void control('seek',time)}/>
      </div>
    </>}
  </div>
})

const resourceLabel:Record<string,string>={vehicle_turnaround:'车组周转',opposing_occupation:'对向占用',track_occupation:'轨道占用',headway:'追踪间隔'}

type Execute=(task:()=>Promise<unknown>,message?:string,refresh?:boolean)=>Promise<void>
function CorridorEditor({snapshot,corridors,selection,current,setCurrent,execute,busy,demoReady}:{
  snapshot:string;corridors:Corridor[];selection?:Entity;current?:Corridor;setCurrent:(c?:Corridor)=>void
  execute:Execute;busy:boolean;demoReady:(runs:Run[])=>void
}){
  const [start,setStart]=useState('')
  const [end,setEnd]=useState('')
  const [name,setName]=useState('')
  const [strict,setStrict]=useState(false)
  const [waypoints,setWaypoints]=useState('')
  const [lines,setLines]=useState('')
  const [directionRows,setDirectionRows]=useState('')
  const [origin,setOrigin]=useState('')
  const [destination,setDestination]=useState('')
  const [date,setDate]=useState(chinaDate)
  const pickEdge=!!selection?.has_edge && (selection.kind==='rail'||selection.kind==='metro')
  const list=(text:string)=>text.split(/[\s,，]+/).filter(Boolean)
  useEffect(()=>{
    setDirectionRows(current?.edge_refs.map(e=>`${e.forward?'+':'-'} ${e.edge_id}`).join('\n')??'')
    if(current)setName(current.name)
  },[current])
  return <div className="ops-pane">
    <label>已有通道<select value={current?.id??''} onChange={e=>setCurrent(corridors.find(c=>c.id===e.target.value))}>
      <option value="">构建通道</option>{corridors.map(c=><option value={c.id} key={c.id}>{c.name} · v{c.version}</option>)}
    </select></label>
    <label>通道名称<input value={name} onChange={e=>setName(e.target.value)}/></label>
    {[['起点边',start,setStart],['终点边',end,setEnd]].map(([label,value,set])=>
      <label key={String(label)}>{String(label)}<span className="ops-inline">
        <input value={String(value)} placeholder="物理边 UUID" onChange={e=>(set as (value:string)=>void)(e.target.value)}/>
        <button className="secondary compact" disabled={!pickEdge} onClick={()=>(set as (value:string)=>void)(selection!.id)}>从地图选取</button>
      </span></label>)}
    <label>途经边 UUID<input value={waypoints} onChange={e=>setWaypoints(e.target.value)} placeholder="按走行顺序，可选"/></label>
    <label>限定线路 UUID<input value={lines} onChange={e=>setLines(e.target.value)} placeholder="由业务线路属性复制"/>
      {selection?.kind==='line'&&<button className="secondary compact" onClick={()=>setLines(value=>[value,selection.id].filter(Boolean).join(','))}>加入选中线路</button>}
    </label>
    <label className="ops-inline"><input type="checkbox" checked={strict} onChange={e=>setStrict(e.target.checked)}/>仅在路径唯一时接受</label>
    <button className="primary" disabled={!start||!end||!name||busy} onClick={()=>void execute(async()=>{
      const resolved=await opsApi.resolve(snapshot,{start_id:start,end_id:end,strict,name,waypoints:list(waypoints),line_ids:list(lines)})
      setCurrent(current?.id?{...resolved,id:current.id,version:current.version}:resolved)
    },'候选已计算，请核对走行方向',false)}><ArrowRight size={14}/>解析走行通道</button>
    {current&&<>
      <p>{current.edge_refs.length} 个物理区段 · {trustLabel[current.verification_status]}</p>
      {current.warnings?.map(w=><p key={w} className="muted">{w}</p>)}
      {current.candidates?.map((candidate,i)=><details key={i}><summary>候选路径 {i+1} · {(candidate.meters/1000).toFixed(2)} km</summary>
        <p className="ops-identifiers">{candidate.edge_ids.join(' → ')}</p></details>)}
      <label>物理区段与方向<textarea value={directionRows} onChange={e=>setDirectionRows(e.target.value)} rows={5}
        placeholder="+ UUID 表示正向，- UUID 表示反向"/></label>
      <button className="secondary" disabled={busy||!name} onClick={()=>void execute(async()=>{
        const refs=directionRows.trim().split(/\n/).filter(r=>r.trim()).map((row,i)=>{
          const match=/^([+-])\s+([^\s]+)$/.exec(row.trim())
          if(!match)throw new Error(`第 ${i+1} 行需要 + 或 - 与物理边 UUID`)
          return {edge_id:match[2],sequence:i,forward:match[1]==='+',start_distance_m:0,end_distance_m:0}
        })
        const changed=JSON.stringify(refs.map(e=>[e.edge_id,e.forward]))!==JSON.stringify(current.edge_refs.map(e=>[e.edge_id,e.forward]))
        setCurrent(await opsApi.saveCorridor(snapshot,{...current,name,edge_refs:refs,
          ...(changed?{verification_status:'automatic_reference' as const,resolution_mode:'manual',provenance:userSource}:{})}))
      },'通道版本已保存')}>保存通道版本</button>
      {current.id&&<details>
        <summary>创建仿真示例</summary>
        <p className="muted">生成两趟沿已保存通道的演示计划。请指定示例起终站。</p>
        <label>示例日期<input type="date" value={date} onChange={e=>setDate(e.target.value)}/></label>
        <label>示例起站<input value={origin} onChange={e=>setOrigin(e.target.value)}/>
          <button className="secondary compact" disabled={selection?.kind!=='station'} onClick={()=>setOrigin(selection!.id)}>填入选中车站</button></label>
        <label>示例终站<input value={destination} onChange={e=>setDestination(e.target.value)}/>
          <button className="secondary compact" disabled={selection?.kind!=='station'} onClick={()=>setDestination(selection!.id)}>填入选中车站</button></label>
        <button className="secondary" disabled={!origin||!destination||busy} onClick={()=>void execute(async()=>{
          const result=await opsApi.demo(snapshot,current,origin,destination,date);demoReady(result.runs)
        },'已生成明确标注的仿真示例')}>生成两趟示例</button>
      </details>}
    </>}
  </div>
}

function unboundStop(sequence:number):Stop{
  return {station_id:'',sequence,arrival_time_s:null,departure_time_s:null}
}
function newRun(service:Service,date:string):Run{
  return {id:'',service_id:service.id,train_number:service.train_code,service_date:date,time_zone:'Asia/Shanghai',
    plan_version:0,status:'scheduled',stops:[unboundStop(0),unboundStop(1)],provenance:userSource}
}
function RunEditor({run,services,corridors,selection,setRun,save,close,busy}:{
  run:Run;services:Service[];corridors:Corridor[];selection?:Entity;setRun:(r:Run)=>void;save:(r:Run)=>void;close:()=>void;busy:boolean
}){
  const corridor=corridors.find(c=>c.id===run.corridor_id && c.version===run.corridor_version)
  const [parseError,setParseError]=useState('')
  const set=(i:number,value:Partial<Stop>)=>setRun({...run,stops:run.stops.map((stop,n)=>n===i?{...stop,...value}:stop)})
  return <form className="ops-editor" onSubmit={e=>{
    e.preventDefault()
    const form=new FormData(e.currentTarget)
    try{
      const stops=run.stops.map((stop,i)=>({...stop,
        arrival_time_s:parseTime(String(form.get(`arrive-${i}`)))??null,
        departure_time_s:parseTime(String(form.get(`depart-${i}`)))??null}))
      setParseError('');save({...run,stops})
    }catch(error){setParseError(describeError(error))}
  }}>
    <label>车次服务<select value={run.service_id} onChange={e=>setRun({...run,service_id:e.target.value})}>
      {services.map(service=><option key={service.id} value={service.id}>{service.train_code} · {service.mode==='rail'?'国铁':'上海地铁'}</option>)}
    </select></label>
    <label>运营日期<input required type="date" value={run.service_date} onChange={e=>setRun({...run,service_date:e.target.value})}/></label>
    <label>计划状态<select value={run.status} onChange={e=>setRun({...run,status:e.target.value})}>
      <option value="scheduled">计划开行</option><option value="cancelled">取消</option>
    </select></label>
    <label>固定通道版本<select value={corridor?.id??''} onChange={e=>{
      const c=corridors.find(c=>c.id===e.target.value)
      setRun({...run,corridor_id:c?.id,corridor_version:c?.version,
        stops:run.stops.map(stop=>({...stop,stop_edge_id:null,stop_edge_sequence:null,stop_offset_m:null}))})
    }}><option value="">时刻表 · 暂无通道</option>
      {corridors.map(c=><option key={c.id} value={c.id}>{c.name} · v{c.version}</option>)}
    </select></label>
    {run.stops.map((stop,i)=><fieldset key={i}>
      <legend>停站 {i+1}</legend>
      <label>车站 UUID<input required value={stop.station_id} onChange={e=>set(i,{station_id:e.target.value})}/></label>
      <button className="secondary compact" type="button" disabled={selection?.kind!=='station'} onClick={()=>set(i,{station_id:selection!.id})}>填入选中车站</button>
      <div className="ops-fields two">
        <label>到达<input name={`arrive-${i}`} key={`${run.id}-arrive-${i}`}
          defaultValue={stop.arrival_time_s==null?'':formatTime(stop.arrival_time_s)} placeholder="可超过 24:00"/></label>
        <label>出发<input name={`depart-${i}`} key={`${run.id}-depart-${i}`}
          defaultValue={stop.departure_time_s==null?'':formatTime(stop.departure_time_s)} placeholder="可超过 24:00"/></label>
      </div>
      <label className="ops-inline"><input type="checkbox" checked={!!stop.pass_through} onChange={e=>set(i,{pass_through:e.target.checked})}/>通过站</label>
      {corridor&&<div className="ops-fields two">
        <label>通道内区段序号<input value={stop.stop_edge_sequence==null?'':stop.stop_edge_sequence+1} type="number" min={1}
          max={corridor.edge_refs.length} onChange={e=>{
            const ref=corridor.edge_refs[e.target.valueAsNumber-1]
            set(i,{stop_edge_id:ref?.edge_id??null,stop_edge_sequence:ref?.sequence??null,stop_offset_m:ref?(stop.stop_offset_m??0):null})
          }}/></label>
        <label>沿走行方向偏移（米）<input type="number" step=".01" min={0} value={stop.stop_offset_m??''}
          onChange={e=>set(i,{stop_offset_m:e.target.value===''?null:e.target.valueAsNumber})}/></label>
      </div>}
    </fieldset>)}
    <div className="ops-actions">
      <button className="secondary compact" type="button" onClick={()=>setRun({...run,stops:[...run.stops,unboundStop(run.stops.length)]})}>增加停站</button>
      <button className="secondary compact" type="button" disabled={run.stops.length<=2}
        onClick={()=>setRun({...run,stops:run.stops.slice(0,-1)})}>删除末站</button>
      <button className="primary" disabled={busy}>保存时刻</button><button className="secondary" onClick={close} type="button">取消</button>
    </div>
    {parseError&&<p className="error-text">{parseError}</p>}
  </form>
}

function OperationsData({snapshot,data,selected,date,execute,busy}:{
  snapshot:string;data:OperationList;selected:string;date:string;execute:Execute;busy:boolean
}){
  const [code,setCode]=useState('')
  const [mode,setMode]=useState<'rail'|'metro'>('rail')
  const [source,setSource]=useState('')
  const [stock,setStock]=useState('')
  const [vehicle,setVehicle]=useState('')
  const [stockId,setStockId]=useState('')
  const [vehicleId,setVehicleId]=useState('')
  const [format,setFormat]=useState<ScheduleFormat>('json')
  const [stopMap,setStopMap]=useState('')
  const file=useRef<HTMLInputElement>(null)

  async function readFile(f:File){
    if(f.size>16*1024*1024)throw new Error('文件大于 16 MiB，请按日期或线路拆分。')
    if(format!=='gtfs')return f.text()
    const bytes=new Uint8Array(await f.arrayBuffer())
    let binary=''
    for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192))
    return btoa(binary)
  }

  async function download(){
    const result=await opsApi.export(snapshot,format,selected?[selected]:undefined)
    const text=result.format==='gtfs'?Uint8Array.from(atob(result.content),c=>c.charCodeAt(0)):result.content
    const url=URL.createObjectURL(new Blob([text],{type:result.format==='gtfs'?'application/zip':'text/plain;charset=utf-8'}))
    const a=document.createElement('a');a.href=url;a.download='railscope-timetable.'+(result.format==='gtfs'?'zip':result.format)
    a.click();window.setTimeout(()=>URL.revokeObjectURL(url),30000)
  }

  return <div className="ops-pane">
    <h3>新建车次服务</h3>
    <div className="ops-fields two"><label>车次号<input value={code} onChange={e=>setCode(e.target.value)}/></label>
      <label>制式<select value={mode} onChange={e=>setMode(e.target.value as 'rail'|'metro')}>
        <option value="rail">国铁</option><option value="metro">上海地铁</option>
      </select></label></div>
    <label>来源说明<input value={source} placeholder="例如工作区自定义计划" onChange={e=>setSource(e.target.value)}/></label>
    <button className="secondary" disabled={!code||busy} onClick={()=>void execute(async()=>{
      await opsApi.saveService(snapshot,{id:'',train_code:code,mode,provenance:{...userSource,source_url_or_reference:source||userSource.source_url_or_reference}})
      setCode('')
    },'车次服务已创建')}>保存服务</button>
    <h3>时刻表导入与导出</h3>
    <label>文件格式<select value={format} onChange={e=>{setFormat(e.target.value as ScheduleFormat);if(file.current)file.current.value=''}}>
      <option value="json">RailScope JSON</option><option value="csv">CSV</option><option value="gtfs">GTFS ZIP</option>
    </select></label>
    {format==='gtfs'&&<label>GTFS 站点映射<textarea value={stopMap} onChange={e=>setStopMap(e.target.value)} rows={3} placeholder='{"feed-stop":"RailScope 车站 UUID"}'/></label>}
    <input aria-label="选择时刻表文件" type="file" ref={file} accept={format==='gtfs'?'.zip':'.'+format}/>
    <div className="ops-actions">
      <button className="secondary" disabled={busy} onClick={()=>void execute(async()=>{
        const input=file.current?.files?.[0]
        if(!input)throw new Error('请选择时刻表文件。')
        const imported=await opsApi.import(snapshot,format,await readFile(input),date,stopMap?JSON.parse(stopMap):undefined)
        if(!imported.runs)throw new Error('没有匹配的运行实例。')
      },'时刻表已导入')}><Upload size={15}/>导入</button>
      <button className="secondary" disabled={busy||!data.runs.length} onClick={()=>void execute(download,'导出文件已生成',false)}><Download size={15}/>导出{selected?'所选车次':'时刻表'}</button>
    </div>
    <h3>车型、车组和担当</h3>
    <label>车型名称<input value={stock} onChange={e=>setStock(e.target.value)}/></label>
    <button className="secondary" disabled={busy||!stock} onClick={()=>void execute(async()=>{
      const result=await opsApi.saveStock(snapshot,{id:'',name:stock,provenance:userSource});setStockId(result.id);setStock('')
    },'车型已保存')}>新增车型</button>
    <label>车组名称<input value={vehicle} onChange={e=>setVehicle(e.target.value)}/></label>
    <label>车型<select value={stockId} onChange={e=>setStockId(e.target.value)}>
      <option value="">未知</option>{data.stock_types.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
    <button className="secondary" disabled={busy||!vehicle} onClick={()=>void execute(async()=>{
      const result=await opsApi.saveTrainset(snapshot,{id:'',name:vehicle,stock_type_id:stockId||undefined,provenance:userSource})
      setVehicleId(result.id);setVehicle('')
    },'车组已保存')}>新增车组</button>
    <label>担当车组<select value={vehicleId} onChange={e=>setVehicleId(e.target.value)}>
      <option value="">请选择</option>{data.trainsets.map(v=><option key={v.id} value={v.id}>{v.name}</option>)}</select></label>
    <button className="secondary" disabled={busy||!vehicleId||!selected}
      onClick={()=>void execute(()=>opsApi.assign(snapshot,selected,vehicleId),'所选运行实例的担当已保存')}>分配给所选车次</button>
  </div>
}

function RunGraph({detail,time,distance,seek}:{detail?:RunDetail;time:number;distance:number|null;seek:(time:number)=>void}){
  const points=(detail?.chart??[]).flatMap(stop=>[stop.arrival_time_s,stop.departure_time_s].flatMap(t=>
    t!=null&&stop.distance_m!=null?[[t,stop.distance_m]]:[]))
  if(points.length<2)return <p className="muted">运行图需要已锚定的时刻与通道。</p>
  const begin=points[0][0]
  const end=Math.max(begin+1,points.at(-1)![0])
  const total=Math.max(1,points.at(-1)![1])
  const x=(time:number)=>35+285*(time-begin)/(end-begin)
  const y=(meters:number)=>108-91*meters/total
  return <figure className="run-graph">
    <figcaption>时间—距离运行图</figcaption>
    <svg viewBox="0 0 342 142" role="img" aria-label="所选列车计划线和当前时刻" onClick={event=>{
      const box=event.currentTarget.getBoundingClientRect()
      const px=(event.clientX-box.left)/box.width*342
      seek(Math.max(begin,Math.min(end,Math.round(begin+(px-35)/285*(end-begin)))))
    }}>
      <path d="M35 15 V108 H320" fill="none" stroke="currentColor" opacity=".35"/>
      {[0,.25,.5,.75,1].map(p=><line key={p} x1={x(begin+p*(end-begin))} x2={x(begin+p*(end-begin))} y1="15" y2="108" opacity=".18" stroke="currentColor"/>)}
      <text x="2" y="23">{(total/1000).toFixed(1)} km</text>
      <text x="26" y="126">{formatTime(begin)}</text><text x="278" y="126">{formatTime(end)}</text>
      <polyline points={points.map(([t,m])=>`${x(t)},${y(m)}`).join(' ')} fill="none" stroke="#785acb" strokeWidth="2"/>
      {time>=begin&&time<=end&&<line x1={x(time)} x2={x(time)} y1="15" y2="108" stroke="#158962" strokeDasharray="3 2"/>}
      {time>=begin&&time<=end&&distance!=null&&<circle cx={x(time)} cy={y(distance)} r="4" fill="#158962"/>}
    </svg>
  </figure>
}
