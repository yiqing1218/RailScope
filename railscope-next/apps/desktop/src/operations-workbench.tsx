import { forwardRef,useEffect,useImperativeHandle,useMemo,useRef,useState } from 'react'
import { createPortal } from 'react-dom'
import { useVirtualizer } from '@tanstack/react-virtual'
import { ArrowRight, Crosshair, Download, GitBranch, Pause, Play, Plus, RotateCcw, TrainFront, Upload } from 'lucide-react'
import type { Geometry } from 'geojson'
import type { Entity, Summary } from './api'
import type { MapTrain } from './map'
import { ControlCorridorEditor } from './corridor-workbench'
import {
  chinaDate,datedRunLabel,describeError,formatTime,opsApi,parseTime,sourceLabel,stateLabel,trustLabel,
  type Clock,type Corridor,type Cycle,type Level,type OperationList,type Provenance,type Run,type RunDetail,type Scenario,
  type ScheduleFormat,type Service,type Simulation,type Stop,type TrainState,
} from './operations-api'

export type OperationsHandle={selectRun:(id:string)=>void;startCorridor:(start?:Summary,end?:Summary)=>void;openScenario:()=>void;pause:()=>void;refresh:()=>void}
export type OperationsMap={
  setTrains:(trains:MapTrain[],selected?:string)=>void
  showPath:(shape:Geometry|null)=>void
  focusTrain:(point:[number,number],follow:boolean)=>void
}
type Props={snapshot?:string; visible:boolean; selection?:Entity; map:OperationsMap; rightDock:HTMLElement|null;bottomDock:HTMLElement|null;
  openBottom:()=>void;inspectStop:(id:string)=>void;onPlaying:(playing:boolean)=>void;onDirty:(dirty:boolean)=>void}
const blank:OperationList={corridors:[],services:[],runs:[],scenarios:[],assignments:[],trainsets:[],stock_types:[],cycles:[]}
const userSource:Provenance={source_kind:'user_defined',source_url_or_reference:'工作区人工录入'}
const initialScenario:Scenario={id:'',name:'假设性场景',headway_s:120,turnaround_s:900,delays:{}}
const storedLevel=():Level=>{
  const value=localStorage.getItem('railscope.ops.level')
  return value==='l1'||value==='l3'?value:'l2'
}

export const OperationsWorkbench=forwardRef<OperationsHandle,Props>(function OperationsWorkbench(
  {snapshot,visible,selection,map,rightDock,bottomDock,openBottom,inspectStop,onPlaying,onDirty},ref
){
  const [tab,setTab]=useState<'corridor'|'trains'|'data'|'scenario'>(()=>{
    const value=localStorage.getItem('railscope.ops.tab')
    return value==='trains'||value==='data'||value==='scenario'?value:'corridor'
  })
  const [data,setData]=useState<OperationList>(blank)
  const [selected,setSelected]=useState('')
  const [editor,setEditor]=useState<Run>()
  const [detail,setDetail]=useState<RunDetail>()
  const [clock,setClock]=useState<Clock>({time_s:28800,rate:1,playing:false})
  const [level,setLevel]=useState<Level>(storedLevel)
  const [scenario,setScenario]=useState(initialScenario)
  const [simulation,setSimulation]=useState<Simulation>()
  const [draftPosition,setDraftPosition]=useState<TrainState>()
  const [draftFeedback,setDraftFeedback]=useState('')
  const [analysis,setAnalysis]=useState<Simulation>()
  const [search,setSearch]=useState('')
  const [date,setDate]=useState(chinaDate)
  const [follow,setFollow]=useState(false)
  const [busy,setBusy]=useState(false)
  const [notice,setNotice]=useState('')
  const [error,setError]=useState('')
  const [corridor,setCorridor]=useState<Corridor>()
  const [prefill,setPrefill]=useState<{start?:Summary;end?:Summary}>()
  const [corridorDirty,setCorridorDirty]=useState(false)
  const [corridorFilter,setCorridorFilter]=useState('')
  const [onlySelected,setOnlySelected]=useState(false)
  const [trainMode,setTrainMode]=useState<'all'|'rail'|'metro'>('all')
  const [bottomTab,setBottomTab]=useState<'table'|'chart'>('chart')
  const [past,setPast]=useState<Run[]>([])
  const [future,setFuture]=useState<Run[]>([])
  const list=useRef<HTMLDivElement>(null)
  const propsMap=useRef(map)
  propsMap.current=map
  const requestEpoch=useRef(0)
  const snapshotRef=useRef(snapshot)
  snapshotRef.current=snapshot

  useImperativeHandle(ref,()=>({
    selectRun:id=>{setSelected(id);setTab('trains');openBottom()},
    startCorridor:(start,end)=>{setTab('corridor');if(!corridorDirty||window.confirm('以所选对象替换通道草稿？')){
      setCorridor(undefined);setPrefill({start,end})
    }},
    openScenario:()=>{setTab('scenario');openBottom()},
    pause:()=>void control('pause'),
    refresh:()=>void reload().catch(error=>setError(describeError(error))),
  }))
  const byRun=useMemo(()=>new Map(data.runs.map(run=>[run.id,run])),[data.runs])
  const byService=useMemo(()=>new Map(data.services.map(service=>[service.id,service])),[data.services])
  const selectionDate=data.runs.find(run=>run.id===selected)?.service_date
  const filterDate=selectionDate ?? date
  const filtered=data.runs.filter(run=>run.service_date===filterDate && run.train_number.toLowerCase().includes(search.trim().toLowerCase())&&
    (!corridorFilter||run.corridor_id===corridorFilter)&&(trainMode==='all'||byService.get(run.service_id)?.mode===trainMode))
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
  useEffect(()=>{localStorage.setItem('railscope.ops.tab',tab)},[tab])
  useEffect(()=>{onPlaying(clock.playing)},[clock.playing,onPlaying])
  useEffect(()=>{onDirty(corridorDirty||!!editor)},[corridorDirty,editor,onDirty])

  async function control(action:string,value?:number){
    try {setClock(await opsApi.clock(action,value))}
    catch(e){setError(describeError(e))}
  }

  useEffect(()=>{
    if(!snapshot || (!batchKey&&!editor)){propsMap.current.setTrains([]);return}
    let closed=false
    let timer:number|null=null
    const step=async()=>{
      try {
        const time=await opsApi.clock('get')
        const result=await opsApi.simulate(snapshot,batchKey?batchKey.split(',').slice(0,1000):[],time.time_s,level,scenario)
        let draft:TrainState|undefined
        let feedback=''
        if(editor?.corridor_id){
          try {draft=await opsApi.previewRun(snapshot,editor,time.time_s)}
          catch(error){feedback=describeError(error)}
        }
        if(closed)return
        setClock(time);setSimulation(result)
        setDraftPosition(draft);setDraftFeedback(feedback)
        const trains:MapTrain[]=result.states.flatMap(state=>{
          if(draft&&state.run_id===editor?.id)return []
          if(state.lon==null||state.lat==null||!['moving','stopped'].includes(state.status))return []
          const run=byRun.get(state.run_id)
          const mode=run ? byService.get(run.service_id)?.mode??'rail' : 'rail'
          if (onlySelected && state.run_id!==selected) return []
          return [{id:state.run_id,label:run?.train_number??state.run_id,point:[state.lon,state.lat],bearing:state.heading_deg??undefined,kind:mode}]
        })
        if(draft?.lon!=null&&draft.lat!=null){
          const mode=editor?byService.get(editor.service_id)?.mode??'rail':'rail'
          trains.push({id:'draft-preview',label:`${editor?.train_number??''} · 草稿`,point:[draft.lon,draft.lat],
            bearing:draft.heading_deg??undefined,kind:mode})
        }
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
  },[snapshot,batchKey,selected,level,scenario,byRun,byService,follow,onlySelected,editor,clock.playing,clock.playing?null:clock.time_s,clock.rate])

  const changeRun=(id:string)=>{setSelected(id);setTab('trains');openBottom()}
  const beginEdit=(run:Run)=>{if(!editor||window.confirm('以所选车次替换时刻草稿？')){
    setEditor(run);setPast([]);setFuture([]);setBottomTab('table');openBottom()
  }}
  const updateDraft=(run:Run)=>{if(editor){setPast(old=>[...old.slice(-59),editor]);setFuture([])}setEditor(run)}
  const replayDraft=(redo:boolean)=>{
    if(!editor)return
    const source=redo?future:past;const target=source.at(-1)
    if(!target)return
    if(redo){setFuture(source.slice(0,-1));setPast([...past,editor])}
    else {setPast(source.slice(0,-1));setFuture([...future,editor])}
    setEditor(target)
  }
  const selectClockStart=()=>void control('seek',detail?.chart[0]?.departure_time_s??detail?.chart[0]?.arrival_time_s??28800)
  const corridorList=data.corridors.filter(item=>item.snapshot_id===snapshot)
  return <div className="ops-workbench" hidden={!visible}>
    <div className="panel-title"><TrainFront size={17}/><h2>运行工作台</h2></div>
    <div className="ops-tabs" role="tablist" aria-label="运行工作台">
      {([['corridor','共享通道'],['trains','车次与时刻'],['data','车辆与交路'],['scenario','模拟控制']] as const).map(([key,label])=>
        <button key={key} type="button" role="tab" aria-selected={tab===key} onClick={()=>{setTab(key);if(key==='scenario')openBottom()}}>{label}</button>)}
    </div>
    {error&&<p role="alert" className="error-text ops-message">{error}</p>}
    {notice&&<p role="status" className="success ops-message">{notice}</p>}
    {!snapshot?<p className="ops-message">请先加载交通数据包。</p>:<>
      <div hidden={tab!=='corridor'}>
        <ControlCorridorEditor snapshot={snapshot} corridors={corridorList} selection={selection} prefill={prefill} current={corridor}
          setCurrent={setCorridor} execute={execute} busy={busy} dirty={setCorridorDirty} show={propsMap.current.showPath}
          locate={point=>propsMap.current.focusTrain(point,false)}/>
        <details className="advanced-corridor"><summary>高级物理边与示例</summary><CorridorEditor snapshot={snapshot} corridors={corridorList} selection={selection}
          current={corridor} setCurrent={setCorridor} execute={execute} busy={busy}
          demoReady={runs=>{if(runs[0]){setSelected(runs[0].id);setDate(runs[0].service_date);setTab('trains');openBottom();void control('seek',28800)}}}/></details>
      </div>
      <div className="ops-pane" hidden={tab!=='trains'}>
        <div className="ops-fields two">
          <label>运营日期<input type="date" aria-label="运营日期" value={filterDate}
            onChange={e=>{setSelected('');setDate(e.target.value)}}/></label>
          <label>车次查询<input value={search} placeholder="车次号" onChange={e=>setSearch(e.target.value)}/></label>
        </div>
        <label>制式<select value={trainMode} onChange={e=>setTrainMode(e.target.value as typeof trainMode)}>
          <option value="all">全部</option><option value="rail">国铁</option><option value="metro">上海地铁</option>
        </select></label>
        {corridorFilter&&<button className="secondary compact" onClick={()=>setCorridorFilter('')}>取消通道筛选</button>}
        <div className="ops-list" ref={list} aria-label="车次列表">
          {filtered.length===0&&<p className="muted">当日暂无匹配车次。</p>}
          <div className="ops-list-inner" style={{height:virtual.getTotalSize()}}>
            {virtual.getVirtualItems().map(row=>{
              const run=filtered[row.index]
              return <button key={run.id} type="button" className={run.id===selected?'active':''}
                style={{transform:`translateY(${row.start}px)`}} onClick={()=>changeRun(run.id)}>
                <TrainFront size={15}/><span>{datedRunLabel(run)}</span><small>{formatTime(run.stops[0]?.departure_time_s??run.stops[0]?.arrival_time_s)}</small>
              </button>
            })}
          </div>
        </div>
        <div className="ops-actions">
          <button className="secondary" disabled={!data.services.length} onClick={()=>beginEdit(newRun(data.services[0],date))}><Plus size={14}/>运行实例</button>
          {detail&&<button className="secondary" onClick={()=>beginEdit(detail.run)}>编辑时刻</button>}
          {detail&&<button className="secondary" onClick={()=>beginEdit({...detail.run,id:'',plan_version:0})}>复制车次</button>}
        </div>
      </div>
      {rightDock&&createPortal(<div hidden={!visible} className="ops-pane ops-inspection">
        {tab==='corridor'&&corridor&&<>
          <h3>{corridor.name}</h3><p>{trustLabel[corridor.verification_status]} · {corridor.edge_refs.length} 段 · {((corridor.edge_refs.at(-1)?.end_distance_m??0)/1000).toFixed(2)} km</p>
          <dl><dt>起点</dt><dd>{corridor.origin_node_id}</dd><dt>终点</dt><dd>{corridor.destination_node_id}</dd>
          <dt>来源</dt><dd>{corridor.provenance.source_url_or_reference}</dd><dt>版本</dt><dd>{corridor.version||'尚未保存'}</dd></dl>
          {corridor.id&&<div className="ops-actions">
            <button className="secondary" onClick={()=>{setCorridorFilter(corridor.id);setTab('trains')}}>引用车次（{data.runs.filter(run=>run.corridor_id===corridor.id).length}）</button>
            <button className="secondary" disabled={!data.services.length} onClick={()=>{
              setTab('trains');beginEdit({...newRun(data.services[0],filterDate),corridor_id:corridor.id,corridor_version:corridor.version})
            }}>在此通道新建车次</button></div>}
        </>}
        {tab==='trains'&&detail&&<>
          <h3>{detail.run.train_number}</h3>
          <p className="muted">{detail.run.service_date} · {detail.run.time_zone} · 计划版本 {detail.run.plan_version}</p>
          <p>{detail.corridor?trustLabel[detail.corridor.verification_status]:'未绑定走行通道'} · {sourceLabel[detail.run.provenance.source_kind]}</p>
          <p className="muted">{detail.run.provenance.source_url_or_reference}</p>
          <p>车组 {detail.trainset?.identifier || detail.trainset?.name || '未指定车组'} · 车型 {detail.stock_type?.name || '未知'}</p>
          {detail.diagnostics?.map((d,i)=><p key={i} className="muted">{d.message}</p>)}
          <div className="ops-actions">
            <button className="secondary" onClick={selectClockStart}><RotateCcw size={14}/>跳到发车</button>
            <button className="secondary" disabled={!detail.geometry} onClick={()=>propsMap.current.showPath(detail.geometry)}><GitBranch size={14}/>高亮通道</button>
            <button className="secondary" disabled={selectedState?.lon==null||selectedState.lat==null}
              onClick={()=>selectedState?.lon!=null&&selectedState.lat!=null&&propsMap.current.focusTrain([selectedState.lon,selectedState.lat],false)}>
              <Crosshair size={14}/>定位列车
            </button>
            <button className="secondary" onClick={()=>setTab('data')}>分配车组</button>
            <button className="secondary" disabled={detail.run.status==='cancelled'||busy} onClick={()=>{
              if(window.confirm('保存为停运版本？原有时刻会保留在历史版本中。'))void execute(()=>opsApi.cancelRun(snapshot,detail.run),'已保存停运版本')
            }}>停运</button>
          </div>
        </>}
      </div>,rightDock)}
      <div hidden={tab!=='data'}><OperationsData snapshot={snapshot} data={data} selected={selected} date={filterDate} execute={execute} busy={busy}/></div>
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
            <div className="ops-actions">{[c.train_run_a,c.train_run_b].map(id=><button key={id} className="secondary compact"
              onClick={()=>{changeRun(id);void control('seek',c.start_time_s)}}>{byRun.get(id)?.train_number??id.slice(0,8)}</button>)}
              {c.resource_type==='track'&&<button className="secondary compact" onClick={()=>inspectStop(c.resource_id)}>定位轨道</button>}
            </div></li>)}</ul>
        </>}
      </div>}
      {bottomDock&&createPortal(<div hidden={!visible} className="ops-bottom">
      <div className="ops-time-panel">
        <div className="ops-time-top">
          <strong aria-label="运行时钟">{formatTime(clock.time_s)}</strong>
          <button className="icon framed" aria-label={clock.playing?'暂停模拟':'播放模拟'} disabled={!runIds.length&&!editor?.corridor_id}
            onClick={()=>void control(clock.playing?'pause':'play')}>{clock.playing?<Pause size={16}/>:<Play size={16}/>}</button>
          <select aria-label="播放倍率" value={clock.rate} onChange={e=>void control('rate',Number(e.target.value))}>
            {[.25,.5,1,2,5,10,30,60].map(r=><option key={r} value={r}>{r}×</option>)}
          </select>
          <select aria-label="仿真层级" value={level} onChange={e=>setLevel(e.target.value as Level)}>
            <option value="l1">L1 计划</option><option value="l2">L2 径路</option><option value="l3">L3 场景</option>
          </select>
          <button className="secondary compact" onClick={()=>void control('reset')}>重置</button>
        </div>
        <div className="ops-time-top">
          <label className="ops-inline"><input type="checkbox" checked={follow} disabled={!selected} onChange={e=>setFollow(e.target.checked)}/>跟随选中车次</label>
          <label className="ops-inline"><input type="checkbox" checked={onlySelected} onChange={e=>setOnlySelected(e.target.checked)}/>仅显示所选车次</label>
          <span>{editor?'草稿 · 计划位置':'计划位置'}</span>
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
      </div>
      {editor&&draftFeedback&&<p className="muted" role="status">草稿位置暂不可预览 · {draftFeedback}</p>}
      <div className="bottom-tabs"><button className="secondary compact" aria-pressed={bottomTab==='table'} onClick={()=>setBottomTab('table')}>时刻表</button>
        <button className="secondary compact" aria-pressed={bottomTab==='chart'} onClick={()=>setBottomTab('chart')}>运行图</button>
        {editor&&<><button className="secondary compact" disabled={!past.length} onClick={()=>replayDraft(false)}>撤销时刻修改</button>
          <button className="secondary compact" disabled={!future.length} onClick={()=>replayDraft(true)}>重做时刻修改</button></>}</div>
      <div hidden={bottomTab!=='table'}>
        {editor?<RunEditor run={editor} services={data.services} corridors={[...corridorList,...(detail?.corridor&&!corridorList.some(c=>c.id===detail.corridor?.id&&c.version===detail.corridor?.version)?[detail.corridor]:[])]}
          selection={selection} locate={inspectStop} setRun={updateDraft} busy={busy} save={run=>void execute(async()=>{
            const saved=await opsApi.saveRun(snapshot,run);setSelected(saved.id);setEditor(undefined);setPast([]);setFuture([])
          },'已保存计划版本')} close={()=>setEditor(undefined)}/>:detail&&
          <table className="ops-table bottom-timetable"><thead><tr><th>站序</th><th>车站</th><th>到达</th><th>出发</th><th>站台</th><th>性质</th></tr></thead><tbody>
            {detail.chart.map((stop,i)=><tr key={i}><td>{i+1}</td><td><button className="station-link" onClick={()=>inspectStop(stop.platform_id??stop.station_id)}>{stop.station_name??stop.station_id}</button></td>
              <td>{formatTime(stop.arrival_time_s)}</td><td>{formatTime(stop.departure_time_s)}</td><td>{stop.platform_id??'—'}</td><td>{stop.pass_through?'通过':'停站'}</td></tr>)}
          </tbody></table>}
      </div>
      <div hidden={bottomTab!=='chart'}><RunGraph detail={detail} editor={editor} corridors={corridorList} time={clock.time_s}
        distance={(editor?draftPosition:selectedState)?.distance_m??null} seek={time=>void control('seek',time)} edit={updateDraft}/></div>
      </div>,bottomDock)}
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
function RunEditor({run,services,corridors,selection,setRun,save,close,busy,locate}:{
  run:Run;services:Service[];corridors:Corridor[];selection?:Entity;setRun:(r:Run)=>void;save:(r:Run)=>void;close:()=>void;busy:boolean;locate:(id:string)=>void
}){
  const corridor=corridors.find(c=>c.id===run.corridor_id && c.version===run.corridor_version)
  const [parseError,setParseError]=useState('')
  const [minutes,setMinutes]=useState(0)
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
    <label>固定通道版本<select value={corridor?`${corridor.id}:${corridor.version}`:''} onChange={e=>{
      const c=corridors.find(c=>`${c.id}:${c.version}`===e.target.value)
      setRun({...run,corridor_id:c?.id,corridor_version:c?.version,
        stops:run.stops.map(stop=>({...stop,stop_edge_id:null,stop_edge_sequence:null,stop_offset_m:null}))})
    }}><option value="">时刻表 · 暂无通道</option>
      {corridors.map(c=><option key={`${c.id}:${c.version}`} value={`${c.id}:${c.version}`}>{c.name} · v{c.version}</option>)}
    </select></label>
    <div className="ops-inline"><label>整车平移（分钟）<input type="number" value={minutes} onChange={e=>setMinutes(e.target.valueAsNumber||0)}/></label>
      <button type="button" className="secondary compact" disabled={!minutes} onClick={()=>{
        const shift=(value:number|null|undefined)=>value==null?null:value+Math.round(minutes*60)
        const stops=run.stops.map(stop=>({...stop,arrival_time_s:shift(stop.arrival_time_s),departure_time_s:shift(stop.departure_time_s)}))
        if(stops.some(stop=>[stop.arrival_time_s,stop.departure_time_s].some(t=>t!=null&&(t<0||t>604800)))){
          setParseError('平移后时刻超出运营日范围。'); return
        }
        setParseError('');setRun({...run,stops})
      }}>平移时刻</button></div>
    {run.stops.map((stop,i)=><fieldset key={i}>
      <legend>停站 {i+1}</legend>
      <label>车站 UUID<input required value={stop.station_id} onChange={e=>set(i,{station_id:e.target.value})}/></label>
      <button className="secondary compact" type="button" disabled={selection?.kind!=='station'} onClick={()=>set(i,{station_id:selection!.id})}>填入选中车站</button>
      <button className="secondary compact" type="button" disabled={!stop.station_id} onClick={()=>locate(stop.platform_id??stop.station_id)}>定位站点</button>
      <div className="ops-fields two">
        <label>到达<TimeInput name={`arrive-${i}`} value={stop.arrival_time_s} change={arrival_time_s=>set(i,{arrival_time_s})}/></label>
        <label>出发<TimeInput name={`depart-${i}`} value={stop.departure_time_s} change={departure_time_s=>set(i,{departure_time_s})}/></label>
      </div>
      <label className="ops-inline"><input type="checkbox" checked={!!stop.pass_through} onChange={e=>set(i,{pass_through:e.target.checked})}/>通过站</label>
      <label>已确认站台<input value={stop.platform_id??''} onChange={e=>set(i,{platform_id:e.target.value||null})} placeholder="稳定站台 ID，可留空"/></label>
      <button className="secondary compact" type="button" disabled={selection?.class!=='platform'} onClick={()=>set(i,{platform_id:selection!.id})}>填入选中站台</button>
      {corridor&&<div className="ops-fields two">
        <label>通道内区段序号<input value={stop.stop_edge_sequence==null?'':stop.stop_edge_sequence+1} type="number" min={1}
          max={corridor.edge_refs.length} onChange={e=>{
            const ref=corridor.edge_refs[e.target.valueAsNumber-1]
            set(i,{stop_edge_id:ref?.edge_id??null,stop_edge_sequence:ref?.sequence??null,stop_offset_m:ref?(stop.stop_offset_m??0):null})
          }}/></label>
        <label>沿走行方向偏移（米）<input type="number" step="any" min={0} value={stop.stop_offset_m??''}
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

function TimeInput({name,value,change}:{name:string;value:number|null|undefined;change:(value:number|null)=>void}){
  const formatted=value==null?'':formatTime(value)
  const [text,setText]=useState(formatted)
  const [invalid,setInvalid]=useState(false)
  useEffect(()=>{setText(formatted);setInvalid(false)},[formatted])
  return <input name={name} value={text} aria-invalid={invalid} placeholder="可超过 24:00" onChange={e=>setText(e.target.value)} onBlur={()=>{
    try {const next=parseTime(text)??null;setInvalid(false);if(next!==(value??null))change(next)}
    catch {setInvalid(true)}
  }}/>
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
    <CycleForm snapshot={snapshot} data={data} date={date} execute={execute} busy={busy}/>
  </div>
}

function CycleForm({snapshot,data,date,execute,busy}:{snapshot:string;data:OperationList;date:string;execute:Execute;busy:boolean}){
  const [out,setOut]=useState('')
  const [back,setBack]=useState('')
  const [name,setName]=useState('')
  const [message,setMessage]=useState('')
  const [values,setValues]=useState({first:'06:00:00',end:'08:00:00',turnaround:300,gap:300,vehicles:1,vehicle:''})
  const knownMetro=new Set(data.services.filter(item=>item.mode==='metro').map(item=>item.id))
  const options=data.runs.filter(run=>knownMetro.has(run.service_id)&&run.corridor_id&&run.status==='scheduled')
  const outRun=options.find(run=>run.id===out)
  const returnOptions=options.filter(run=>run.id!==out&&run.origin_station_id===outRun?.destination_station_id&&run.destination_station_id===outRun?.origin_station_id)
  function useTemplate(template?:Cycle){
    if(!template)return
    setOut(template.outbound_id);setBack(template.return_id);setName(template.name)
    setValues({first:formatTime(template.first_departure_s),end:formatTime(template.end_s),turnaround:template.turnaround_s,
      gap:template.launch_gap_s,vehicles:template.vehicles,vehicle:template.trainsets[0]??''})
  }
  const backRun=returnOptions.find(run=>run.id===back)
  return <details className="cycle-panel"><summary>上海地铁往返循环</summary>
    <div className="task-summary"><p>使用已有的有锚点单程车次，按两端折返与投放间隔生成新计划。</p>
      {!!data.cycles.length&&<label>已保存配置<select value="" onChange={e=>useTemplate(data.cycles.find(item=>item.id===e.target.value))}>
        <option value="">选择后载入参数</option>{data.cycles.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}
      <label>循环名称<input value={name} onChange={e=>setName(e.target.value)}/></label>
      <label>出程模板<select value={out} onChange={e=>{setOut(e.target.value);setBack('')}}><option value="">选择有径路的城轨车次</option>
        {options.map(run=><option value={run.id} key={run.id}>{datedRunLabel(run)} · v{run.plan_version}</option>)}</select></label>
      <label>返程模板<select value={back} onChange={e=>setBack(e.target.value)}><option value="">选择反向接续车次</option>
        {returnOptions.map(run=><option value={run.id} key={run.id}>{datedRunLabel(run)} · v{run.plan_version}</option>)}</select></label>
      <div className="ops-fields two"><label>投放开始<input value={values.first} onChange={e=>setValues({...values,first:e.target.value})}/></label>
        <label>投放截止<input value={values.end} onChange={e=>setValues({...values,end:e.target.value})}/></label></div>
      <div className="ops-fields two"><label>折返（秒）<input type="number" value={values.turnaround} onChange={e=>setValues({...values,turnaround:e.target.valueAsNumber||0})}/></label>
        <label>投入间隔（秒）<input type="number" value={values.gap} onChange={e=>setValues({...values,gap:e.target.valueAsNumber||0})}/></label></div>
      <label>投放车辆数<input type="number" min={1} max={100} value={values.vehicles} onChange={e=>setValues({...values,vehicles:e.target.valueAsNumber||1})}/></label>
      <label>首组已核实车组<select value={values.vehicle} onChange={e=>setValues({...values,vehicle:e.target.value})}>
        <option value="">未指定车组</option>{data.trainsets.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <button className="primary" disabled={busy||!name.trim()||!outRun||!backRun} onClick={()=>void execute(async()=>{
        if(!outRun||!backRun)return
        const first=parseTime(values.first);const end=parseTime(values.end)
        if(first==null||end==null)throw new Error('请输入完整运营时段')
        const result=await opsApi.generateCycle(snapshot,{id:'',name,outbound_id:outRun.id,outbound_version:outRun.plan_version,
          return_id:backRun.id,return_version:backRun.plan_version,service_date:date,first_departure_s:first,end_s:end,
          turnaround_s:values.turnaround,launch_gap_s:values.gap,vehicles:values.vehicles,trainsets:values.vehicle?[values.vehicle]:[]})
        setMessage(`已为 ${date} 生成 ${result.runs} 趟可单独编辑的计划车次`)
      })}>生成并保存计划</button>
      {message&&<p role="status">{message}</p>}
    </div>
  </details>
}

function RunGraph({detail,editor,corridors,time,distance,seek,edit}:{detail?:RunDetail;editor?:Run;corridors:Corridor[];
  time:number;distance:number|null;seek:(time:number)=>void;edit:(run:Run)=>void}){
  const drag=useRef<{index:number;field:'arrival_time_s'|'departure_time_s';value:number;initial:number}|undefined>(undefined)
  const [dragTime,setDragTime]=useState<number>()
  const corridor=editor&&(corridors.find(c=>c.id===editor.corridor_id&&c.version===editor.corridor_version)??
    (detail?.corridor?.id===editor.corridor_id&&detail?.corridor?.version===editor.corridor_version?detail?.corridor:undefined))
  const chart=editor?editor.stops.map(stop=>{
    const edge=corridor?.edge_refs[stop.stop_edge_sequence??-1]
    return {...stop,distance_m:edge?.edge_id===stop.stop_edge_id&&edge&&stop.stop_offset_m!=null?edge.start_distance_m+stop.stop_offset_m:null}
  }):detail?.chart??[]
  const points=chart.flatMap((stop,index)=>(['arrival_time_s','departure_time_s'] as const).flatMap(field=>{
    const time=stop[field]
    return time!=null&&stop.distance_m!=null?[{time,distance:stop.distance_m,index,field}]:[]
  }))
  if(points.length<2)return <p className="muted">运行图需要已锚定的时刻与通道。</p>
  const begin=Math.min(...points.map(item=>item.time))
  const end=Math.max(begin+1,...points.map(item=>item.time))
  const total=Math.max(1,...points.map(item=>item.distance))
  const x=(time:number)=>55+625*(time-begin)/(end-begin)
  const y=(meters:number)=>175-151*meters/total
  const pointerValue=(svg:SVGSVGElement,px:number)=>{
    const box=svg.getBoundingClientRect()
    return Math.max(0,Math.min(604800,Math.round(begin+((px-box.left)/box.width*720-55)/625*(end-begin))))
  }
  return <figure className="run-graph">
    <figcaption>时间—距离运行图</figcaption>
    <svg viewBox="0 0 720 205" role="img" aria-label="所选列车计划线和当前时刻" preserveAspectRatio="none"
      onClick={event=>{if(!editor)seek(pointerValue(event.currentTarget,event.clientX))}}
      onPointerMove={event=>{if(drag.current){drag.current.value=pointerValue(event.currentTarget,event.clientX);setDragTime(drag.current.value)}}}
      onPointerUp={()=>{
        const final=drag.current;drag.current=undefined;setDragTime(undefined)
        if(editor&&final&&final.value!==final.initial)edit({...editor,stops:editor.stops.map((s,i)=>i===final.index?{...s,[final.field]:final.value}:s)})
      }} onPointerCancel={()=>{drag.current=undefined;setDragTime(undefined)}}>
      <path d="M55 20 V175 H680" fill="none" stroke="currentColor" opacity=".35"/>
      {[0,.25,.5,.75,1].map(p=><line key={p} x1={x(begin+p*(end-begin))} x2={x(begin+p*(end-begin))} y1="20" y2="175" opacity=".18" stroke="currentColor"/>)}
      <text x="2" y="23">{(total/1000).toFixed(1)} km</text>
      <text x="44" y="198">{formatTime(begin)}</text><text x="632" y="198">{formatTime(end)}</text>
      <polyline points={points.map(item=>`${x(dragTime!=null&&drag.current?.index===item.index&&drag.current.field===item.field?dragTime:item.time)},${y(item.distance)}`).join(' ')} fill="none" stroke="#785acb" strokeWidth="2"/>
      {editor&&points.map(item=><circle key={`${item.index}-${item.field}`} cx={x(dragTime!=null&&drag.current?.index===item.index&&drag.current.field===item.field?dragTime:item.time)}
        cy={y(item.distance)} r="5" fill="#785acb" className="time-handle" onPointerDown={event=>{
          event.preventDefault();const svg=event.currentTarget.ownerSVGElement;if(!svg)return
          svg.setPointerCapture(event.pointerId);drag.current={index:item.index,field:item.field,value:item.time,initial:item.time}
        }}><title>{`第 ${item.index+1} 站 ${item.field==='arrival_time_s'?'到达':'出发'} ${formatTime(item.time)}`}</title></circle>)}
      {time>=begin&&time<=end&&<line x1={x(time)} x2={x(time)} y1="20" y2="175" stroke="#158962" strokeDasharray="3 2"/>}
      {time>=begin&&time<=end&&distance!=null&&<circle cx={x(time)} cy={y(distance)} r="4" fill="#158962"/>}
    </svg>
  </figure>
}
