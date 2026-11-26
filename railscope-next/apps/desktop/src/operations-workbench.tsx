import { t } from './i18n'
import { forwardRef,useEffect,useId,useImperativeHandle,useMemo,useRef,useState,type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { ArrowRight, Crosshair, Download, GitBranch, Pause, Play, Plus, RotateCcw, TrainFront, Upload, X } from 'lucide-react'
import type { Geometry } from 'geojson'
import { api, type Entity, type Summary } from './api'
import type { MapTrain } from './map'
import { ControlCorridorEditor } from './corridor-workbench'
import { MetroDiagram } from './metro-diagram'
import { OperationsDirectory, networkName, operationLines, type DirectorySelection, type NetworkMode } from './operations-directory'
import { simulationLevels } from './settings'
import {
  chinaDate,corridorKey,datedRunLabel,describeError,formatTime,opsApi,parseTime,runCorridorKey,sourceLabel,stateLabel,trustLabel,
  type Clock,type Corridor,type Cycle,type Level,type OperationList,type Provenance,type Run,type RunDetail,type Scenario,
  type ScheduleFormat,type Service,type Simulation,type Stop,type TrainState,
} from './operations-api'

export type OperationsHandle={selectRun:(id:string)=>void;startCorridor:(start?:Summary,end?:Summary)=>void;openScenario:()=>void;pause:()=>void;releaseCamera:()=>void;refresh:()=>void}
export type OperationsMap={
  setTrains:(trains:MapTrain[],selected?:string)=>void
  showPath:(shape:Geometry|null)=>void
  showCorridor:(shape:Geometry|null)=>void
  focusTrain:(point:[number,number],follow:boolean)=>void
  revealLayer:(layer:'railTrains'|'metroTrains'|'route'|'corridor')=>void
  stopFollowing:()=>void
}
type Props={snapshot?:string; visible:boolean; selection?:Entity; map:OperationsMap; rightDock:HTMLElement|null;bottomDock:HTMLElement|null;
  openBottom:(focus?:boolean)=>void;inspectStop:(id:string)=>void;onPlaying:(playing:boolean)=>void;onDirty:(dirty:boolean)=>void;
  trainLayers:Record<NetworkMode,boolean>;onTrainLayer:(mode:NetworkMode,value:boolean)=>void;
  level:Level;setLevel:(level:Level)=>void}
const blank:OperationList={corridors:[],services:[],runs:[],dates:[],scenarios:[],assignments:[],trainsets:[],stock_types:[],cycles:[]}
const userSource:Provenance={source_kind:'user_defined',source_url_or_reference:'工作区人工录入'}
const initialScenario:Scenario={id:'',name:'假设性场景',headway_s:120,turnaround_s:900,delays:{}}
const departure=(run:Run)=>run.stops[0]?.departure_time_s??run.stops[0]?.arrival_time_s??Infinity

export const OperationsWorkbench=forwardRef<OperationsHandle,Props>(function OperationsWorkbench(
  {snapshot,visible,selection,map,rightDock,bottomDock,openBottom,inspectStop,onPlaying,onDirty,trainLayers,onTrainLayer,level,setLevel},ref
){
  const [scope,setScope]=useState<DirectorySelection>({})
  const [popup,setPopup]=useState<'plans'|'scenario'|'new-run'|'run'|'corridor'|'vehicle'>()
  const [editMode,setEditMode]=useState<NetworkMode>('rail')
  const [data,setData]=useState<OperationList>(blank)
  const [selected,setSelected]=useState('')
  const [editor,setEditor]=useState<Run>()
  const [detail,setDetail]=useState<RunDetail>()
  const [metroAxis,setMetroAxis]=useState<RunDetail>()
  const [clock,setClock]=useState<Clock>({time_s:28800,rate:1,playing:false})
  const [scenario,setScenario]=useState(initialScenario)
  const [simulation,setSimulation]=useState<Simulation>()
  const [draftPosition,setDraftPosition]=useState<TrainState>()
  const [draftFeedback,setDraftFeedback]=useState('')
  const [analysis,setAnalysis]=useState<Simulation>()
  const [date,setDate]=useState(chinaDate)
  const [follow,setFollow]=useState(false)
  const [busy,setBusy]=useState(false)
  const [notice,setNotice]=useState('')
  const [error,setError]=useState('')
  const [corridor,setCorridor]=useState<Corridor>()
  const [prefill,setPrefill]=useState<{start?:Summary;end?:Summary}>()
  const [corridorDirty,setCorridorDirty]=useState(false)
  const [onlySelected,setOnlySelected]=useState(false)
  const [initialDate,setInitialDate]=useState(true)
  const [bottomTab,setBottomTab]=useState<'table'|'chart'|'metro'|'conflicts'>('chart')
  const [past,setPast]=useState<Run[]>([])
  const [future,setFuture]=useState<Run[]>([])
  const propsMap=useRef(map)
  propsMap.current=map
  const requestEpoch=useRef(0)
  const dateRef=useRef(date)
  dateRef.current=date
  const snapshotRef=useRef(snapshot)
  snapshotRef.current=snapshot

  useImperativeHandle(ref,()=>({
    selectRun:id=>selectRun(id),
    startCorridor:(start,end)=>{if(!corridorDirty||window.confirm(t("以所选对象替换通道草稿？"))){
      const target=start?.kind==='metro'||start?.class==='metro_station'?'metro':'rail'
      setEditMode(target);setCorridor(undefined);setPrefill({start,end});setPopup('corridor')
    }},
    openScenario:()=>setPopup('scenario'),
    pause:()=>void control('pause'),
    releaseCamera:()=>setFollow(false),
    refresh:()=>void reload().catch(error=>setError(describeError(error))),
  }))
  const byRun=useMemo(()=>new Map(data.runs.map(run=>[run.id,run])),[data.runs])
  const byService=useMemo(()=>new Map(data.services.map(service=>[service.id,service])),[data.services])
  const available=useMemo(()=>data.runs.filter(run=>run.service_date===date).sort((a,b)=>departure(a)-departure(b)||a.train_number.localeCompare(b.train_number)),[data.runs,date])
  const groups=useMemo(()=>operationLines(data.corridors),[data.corridors])
  const line=groups.find(group=>group.id===scope.line)
  const activeCorridor=line?.corridors.find(value=>corridorKey(value)===scope.corridor)
  const lineRuns=useMemo(()=>{
    if(!line)return available
    const keys=new Set(line.corridors.map(corridorKey))
    return available.filter(run=>keys.has(runCorridorKey(run)))
  },[available,line])
  const scopedRuns=activeCorridor?lineRuns.filter(run=>runCorridorKey(run)===scope.corridor):lineRuns
  const services=data.services.filter(service=>service.mode===editMode)
  const corridorList=data.corridors.filter(item=>item.current_version!==false&&(!item.mode||item.mode===editMode))
  const runIds=useMemo(()=>available.map(run=>run.id),[available])
  const selectedState=simulation?.states.find(state=>state.run_id===selected)

  async function reload() {
    if(!snapshot) return
    const next=await opsApi.list(snapshot,date)
    if (snapshotRef.current===snapshot&&dateRef.current===date) setData(next)
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
    propsMap.current.setTrains([]);propsMap.current.showPath(null);propsMap.current.showCorridor(null)
  },[snapshot])
  useEffect(()=>{
    void reload().catch(e=>setError(describeError(e)))
  },[snapshot,date])
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
  const metroAxisId=line?.mode==='metro'?lineRuns.reduce<Run|undefined>((best,run)=>
    run.status!=='cancelled'&&run.stops.length>(best?.stops.length??1)?run:best,undefined)?.id:undefined
  useEffect(()=>{
    setMetroAxis(undefined)
    if(!snapshot||!metroAxisId)return
    let active=true
    void opsApi.getRun(snapshot,metroAxisId).then(value=>{if(active)setMetroAxis(value)})
      .catch(cause=>{if(active)setError(describeError(cause))})
    return ()=>{active=false}
  },[snapshot,metroAxisId])
  useEffect(()=>{
    if(initialDate&&data.dates.length){
      if(!data.dates.includes(date))setDate(data.dates[0])
      setInitialDate(false)
    }
  },[initialDate,data.dates,date])
  useEffect(()=>{onPlaying(clock.playing)},[clock.playing,onPlaying])
  useEffect(()=>{onDirty(corridorDirty||!!editor)},[corridorDirty,editor,onDirty])

  function revealTrain(mode:NetworkMode){propsMap.current.revealLayer(mode==='metro'?'metroTrains':'railTrains')}
  async function control(action:string,value?:number,mode?:NetworkMode){
    try {
      const next=await opsApi.clock(action,value)
      if(action==='play'){
        if(mode)revealTrain(mode)
        else if(!trainLayers.rail&&!trainLayers.metro){
          const modes=new Set(available.map(run=>byService.get(run.service_id)?.mode))
          if(modes.has('rail'))revealTrain('rail')
          if(modes.has('metro'))revealTrain('metro')
        }
      }
      setClock(next)
    }
    catch(e){setError(describeError(e))}
  }

  useEffect(()=>{
    if(!snapshot || (!runIds.length&&!editor)){setSimulation(undefined);propsMap.current.setTrains([]);return}
    let closed=false
    let timer:number|null=null
    const step=async()=>{
      try {
        const time=await opsApi.clock('get')
        const batches=Array.from({length:Math.ceil(runIds.length/1000)},(_,i)=>runIds.slice(i*1000,(i+1)*1000))
        const results=await Promise.all(batches.map(ids=>opsApi.simulate(snapshot,ids,time.time_s,level,scenario)))
        const result:Simulation={states:results.flatMap(value=>value.states),diagnostics:results.flatMap(value=>value.diagnostics),occupancies:[],conflicts:[]}
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
          if (onlySelected && selected && state.run_id!==selected) return []
          return [{id:state.run_id,label:run?.train_number??state.run_id,point:[state.lon,state.lat],bearing:state.heading_deg??undefined,kind:mode}]
        })
        if(draft?.lon!=null&&draft.lat!=null){
          const mode=editor?byService.get(editor.service_id)?.mode??'rail':'rail'
          trains.push({id:'draft-preview',label:t("{0} · 草稿", editor?.train_number??''),point:[draft.lon,draft.lat],
            bearing:draft.heading_deg??undefined,kind:mode})
        }
        propsMap.current.setTrains(trains,selected)
        if(time.playing) timer=window.setTimeout(step,230)
      } catch(e) {if(!closed)setError(describeError(e))}
    }
    void step()
    return ()=>{closed=true;if(timer!=null)window.clearTimeout(timer)}
  },[snapshot,runIds,selected,level,scenario,byRun,byService,onlySelected,editor,clock.playing,clock.playing?null:clock.time_s,clock.rate])

  useEffect(()=>{
    if(follow&&visible&&selectedState?.run_id===selected&&selectedState.lon!=null&&selectedState.lat!=null)
      propsMap.current.focusTrain([selectedState.lon,selectedState.lat],true)
  },[follow,visible,selected,selectedState])
  useEffect(()=>{
    if(!visible||!follow)propsMap.current.stopFollowing()
  },[visible,follow])

  function chooseScope(value:DirectorySelection){
    setScope(value);setSelected('');setFollow(false)
    const group=groups.find(item=>item.id===value.line)
    setBottomTab(group?.mode==='metro'?'metro':'chart');openBottom()
  }
  async function selectRun(id:string) {
    if(!snapshot)return
    const fromList=byRun.get(id)
    try {
      const result=fromList?undefined:await opsApi.getRun(snapshot,id)
      if(snapshotRef.current!==snapshot)return
      const run=result?.run??fromList
      if(!run)return
      if(run.service_date!==date)chooseDate(run.service_date)
      changeRun(run.id,run,result?.service?.mode)
    }catch(error){setError(describeError(error))}
  }
  const changeRun=(id:string,run=byRun.get(id),knownMode?:NetworkMode)=>{
    const mode=knownMode??(run&&byService.get(run.service_id)?.mode)
    const key=run?runCorridorKey(run):''
    const group=groups.find(item=>item.corridors.some(value=>corridorKey(value)===key))
    setScope(group?{line:group.id,corridor:key}:{});setSelected(id);openBottom()
    if(!editor)setBottomTab(mode==='metro'?'metro':'chart')
  }
  const beginEdit=(run:Run)=>{if(!editor||window.confirm(t("以所选车次替换时刻草稿？"))){
    const mode=byService.get(run.service_id)?.mode
    if(mode)setEditMode(mode)
    setEditor(run);setPast([]);setFuture([]);setPopup('run');setBottomTab('chart');openBottom()
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
  function chooseDate(day:string){
    if(!day||day===date)return
    setInitialDate(false);setSelected('');setFollow(false);setAnalysis(undefined);setDate(day)
    void control('pause')
  }
  async function startPlayback(fromDeparture=false){
    if(!snapshot)return
    const moving=new Set(simulation?.states.filter(state=>state.status==='moving'||state.status==='stopped').map(state=>state.run_id))
    const active=scopedRuns.find(run=>moving.has(run.id))
    const run=(fromDeparture?detail?.run:undefined)??active??scopedRuns.find(run=>run.corridor_id&&run.status==='scheduled')
    if(!run)return
    const time=fromDeparture||!active?departure(run):clock.time_s
    if(!Number.isFinite(time))return
    if(!selected){
      if(line){setSelected(run.id);if(line.mode==='metro')setBottomTab('metro')}
      else changeRun(run.id)
    }
    try{
      const {states}=await opsApi.simulate(snapshot,[run.id],time,level,scenario)
      const first=states[0]
      if(first?.lon!=null&&first.lat!=null)propsMap.current.focusTrain([first.lon,first.lat],false)
    }catch(error){setError(describeError(error))}
    await control('seek',time)
    openBottom()
    await control('play',undefined,byService.get(run.service_id)?.mode)
  }
  const contextMode=detail?.service?.mode??line?.mode??'rail'
  const vehicle=detail?.trainset
  const relatedRuns=vehicle?available.filter(run=>data.assignments.some(item=>item.run_id===run.id&&item.trainset_id===vehicle.id)):[]
  const playable=scopedRuns.some(run=>run.corridor_id&&run.status==='scheduled')
  const showCorridor=async(value:Corridor)=>{
    if(!snapshot)return
    await execute(async()=>{
      const result=await opsApi.corridorQuery<{geometry:Geometry}>(snapshot,'preview',{corridor:value})
      propsMap.current.revealLayer('corridor');propsMap.current.showCorridor(result.geometry)
    },undefined,false)
  }
  function openCorridor(value?:Corridor){
    setPrefill(undefined);setCorridor(value);setEditMode(value?.mode??contextMode);setPopup('corridor')
  }
  function closePopup(){
    if(popup==='corridor'){
      if(corridorDirty&&!window.confirm(t('放弃未保存的通道草稿？')))return
      setCorridorDirty(false);propsMap.current.showCorridor(null)
    }
    setPopup(undefined)
  }
  const modalTitle=popup==='new-run'?t("定义新车次"):popup==='run'?t("编辑时刻"):popup==='corridor'?t("通道编排"):
    popup==='plans'?t("计划与车辆"):popup==='vehicle'?t('担当车组'):t("仿真场景")
  return <div className="ops-workbench" hidden={!visible}>
    <div className="panel-title"><TrainFront size={17}/><h2>{t("运行目录")}</h2></div>
    {!snapshot?<p className="ops-message">{t("请先加载交通数据包。")}</p>:<>
      <OperationsDirectory groups={groups} runs={available} services={byService} date={date} dates={data.dates} datesByMode={data.dates_by_mode}
        selected={selected} scope={scope} onDate={chooseDate} onScope={chooseScope} onRun={run=>changeRun(run.id,run)}/>
      {rightDock&&createPortal(<div hidden={!visible} className="ops-pane ops-inspection">
        {!popup&&error&&<p role="alert" className="error-text ops-message">{error}</p>}
        {!popup&&notice&&<p role="status" className="success ops-message">{notice}</p>}
        <div className="ops-summary">
          <h3>{line?.name??t("运行详情")}</h3>
          <p className="muted">{line?t(networkName[line.mode]):t('国铁与城市轨道')} · {date}</p>
          <p>{t("{0} 趟车次 · {1} 条通道",scopedRuns.length,activeCorridor?1:line?.corridors.length??data.corridors.length)}</p>
          <div className="ops-actions">
            {!detail&&<button className="primary" disabled={!playable} onClick={()=>void startPlayback()}>
              <Play size={14}/>{t(activeCorridor?'从此通道开始播放':line?'从此线路开始播放':'播放当日计划')}</button>}
            {line&&<button className="secondary" onClick={()=>chooseScope({})}>{t('返回全网')}</button>}
          </div>
        </div>
        {!detail&&activeCorridor&&<>
          <h3>{activeCorridor.name}</h3><p>{t(trustLabel[activeCorridor.verification_status])} · {activeCorridor.edge_refs.length} {t("段 ·")} {((activeCorridor.edge_refs.at(-1)?.end_distance_m??0)/1000).toFixed(2)} km</p>
          <p className="muted">{activeCorridor.provenance.source_url_or_reference} · v{activeCorridor.version}</p>
          {!!activeCorridor.lines?.length&&<p>{activeCorridor.lines.map(item=>item.name).join(' → ')}</p>}
          <div className="ops-actions">
            <button className="secondary" onClick={()=>void showCorridor(activeCorridor)}>{t('高亮通道')}</button>
            <button className="secondary" onClick={()=>openCorridor(activeCorridor)}>{t('编辑通道')}</button>
            <button className="secondary" disabled={activeCorridor.current_version===false} onClick={()=>{setEditMode(contextMode);setPopup('new-run')}}>{t("在此通道新建车次")}</button>
          </div>
        </>}
        {detail&&<>
          <h3>{detail.run.train_number}</h3>
          <p className="muted">{detail.run.service_date} · {detail.run.time_zone} {t("· 计划版本")} {detail.run.plan_version}</p>
          <dl className="ops-relationship">
            <dt>{t('线路')}</dt><dd>{line?<button className="station-link" onClick={()=>chooseScope({line:line.id})}>{line.name}</button>:t('线路待归属')}</dd>
            <dt>{t('通道')}</dt><dd>{detail.corridor&&line?<button className="station-link" onClick={()=>chooseScope({line:line.id,corridor:corridorKey(detail.corridor!)})}>
              {detail.corridor.name} · v{detail.corridor.version}</button>:detail.corridor?.name??t('未绑定走行通道')}</dd>
            <dt>{t('车次')}</dt><dd>{detail.run.train_number} · {detail.chart[0]?.station_name??'—'} → {detail.chart.at(-1)?.station_name??'—'}</dd>
            <dt>{t('担当车组')}</dt><dd>{vehicle?<button className="station-link" onClick={()=>setPopup('vehicle')}>{vehicle.identifier||vehicle.name}</button>:t('未指定车组')}
              {detail.stock_type&&<> · {detail.stock_type.name}</>}</dd>
          </dl>
          <p>{detail.corridor?t(trustLabel[detail.corridor.verification_status]):t("未绑定走行通道")} · {t(sourceLabel[detail.run.provenance.source_kind])}</p>
          <p className="muted">{detail.run.provenance.source_url_or_reference}</p>
          {selectedState&&<p className="ops-train-status">{t(stateLabel[selectedState.status])} {selectedState.speed_mps!=null?`${(selectedState.speed_mps*3.6).toFixed(1)} km/h`:''}</p>}
          {detail.diagnostics?.map((d,i)=><p key={i} className="muted">{d.message}</p>)}
          <div className="ops-actions">
            <button className="primary" disabled={!detail.corridor||detail.run.status!=='scheduled'} onClick={()=>void startPlayback(true)}>
              <Play size={14}/>{t("从发车开始模拟")}</button>
            <button className="secondary" onClick={()=>beginEdit(detail.run)}>{t("编辑时刻")}</button>
            <button className="secondary" onClick={selectClockStart}><RotateCcw size={14}/>{t("跳到发车")}</button>
            <button className="secondary" disabled={!detail.geometry} onClick={()=>{
              propsMap.current.revealLayer('route');propsMap.current.showPath(detail.geometry)
            }}><GitBranch size={14}/>{t("高亮通道")}</button>
            <button className="secondary" disabled={selectedState?.lon==null||selectedState.lat==null}
              onClick={()=>{
                if(selectedState?.lon==null||selectedState.lat==null)return
                revealTrain(contextMode)
                propsMap.current.focusTrain([selectedState.lon,selectedState.lat],false)
              }}>
              <Crosshair size={14}/>{t("定位列车")}
            </button>
            <button className="secondary" onClick={()=>setPopup('plans')}>{t("分配车组")}</button>
          </div>
          <details><summary>{t("更多车次操作")}</summary><div className="ops-actions">
            <button className="secondary" onClick={()=>beginEdit({...detail.run,id:'',plan_version:0})}>{t("复制车次")}</button>
            <button className="secondary" disabled={detail.run.status==='cancelled'||busy} onClick={()=>{
              if(window.confirm(t("保存为停运版本？原有时刻会保留在历史版本中。")))void execute(()=>opsApi.cancelRun(snapshot,detail.run),t("已保存停运版本"))
            }}>{t("停运")}</button>
          </div></details>
        </>}
        <div className="ops-side-tools">
          <button className="secondary" onClick={()=>openCorridor()}>{t("通道编排")}</button>
          <button className="secondary" onClick={()=>{setEditMode(contextMode);setPopup('new-run')}}>{t("定义新车次")}</button>
          <button className="secondary" onClick={()=>setPopup('plans')}>{t("计划与车辆")}</button>
          <button className="secondary" onClick={()=>setPopup('scenario')}>{t("仿真场景")}</button>
          {editor&&<button className="secondary" onClick={()=>setPopup('run')}>{t("继续编辑草稿")}</button>}
          {analysis&&<button className="secondary" onClick={()=>{setBottomTab('conflicts');openBottom()}}>{t("查看占用与冲突")}</button>}
        </div>
        <details className="ops-play-options"><summary>{t("播放设置")}</summary>
          <label>{t("仿真层级")}<select aria-label={t("仿真层级")} value={level} onChange={e=>setLevel(e.target.value as Level)}>
            {Object.entries(simulationLevels).map(([id,name])=><option key={id} value={id}>{t(name)}</option>)}
          </select></label>
          <label className="ops-inline"><input type="checkbox" checked={onlySelected} onChange={e=>setOnlySelected(e.target.checked)}/>{t("仅显示所选车次")}</label>
        </details>
      </div>,rightDock)}
      {visible&&popup&&<OperationsDialog title={modalTitle} floating={popup==='corridor'||popup==='run'} close={closePopup}>
        {error&&<p className="ops-message error-text" role="alert">{error}</p>}
        {notice&&<p className="ops-message success" role="status">{notice}</p>}
        {popup==='plans'&&<OperationsData snapshot={snapshot} data={{...data,runs:available}}
          selected={selected} date={date} execute={execute} busy={busy}
          onImported={day=>{chooseDate(day);setPopup(undefined)}}/>}
        {popup==='vehicle'&&vehicle&&<div className="ops-pane">
          <h3>{vehicle.name}</h3>
          <p>{[vehicle.identifier,detail?.stock_type?.name].filter(Boolean).join(' · ')||t('未知车型')}</p>
          <p className="muted">{vehicle.provenance.source_url_or_reference}</p>
          <h3>{t('当日担当车次')}</h3>
          <div className="ops-actions">{relatedRuns.map(run=><button key={run.id} className="secondary" onClick={()=>{
            changeRun(run.id,run);setPopup(undefined)
          }}>{run.train_number} · {formatTime(departure(run))}</button>)}</div>
          <button className="secondary" onClick={()=>setPopup('plans')}>{t('分配车组')}</button>
        </div>}
        {popup==='new-run'&&<form className="ops-pane" onSubmit={event=>{
          event.preventDefault()
          const code=String(new FormData(event.currentTarget).get('trainCode')??'').trim()
          if(!code)return
          void execute(async()=>{
            const service=await opsApi.saveService(snapshot,{id:'',train_code:code,mode:editMode,provenance:userSource})
            const path=activeCorridor?.mode===editMode&&activeCorridor.current_version!==false?activeCorridor:undefined
            beginEdit({...newRun(service,date),corridor_id:path?.id??null,corridor_version:path?.version??null})
          },t("车次服务已创建"))
        }}><NetworkField mode={editMode} onChange={setEditMode}/>
          <label>{t("车次号")}<input required maxLength={30} name="trainCode" placeholder={editMode==='rail'?'G1':'7-M-001'}/></label>
          <div className="ops-actions">
            <button className="primary" disabled={busy}>{t("创建并编排时刻")}</button>
            <button className="secondary" type="button" disabled={!services.length} onClick={()=>beginEdit(newRun(services[0],date))}>{t("从已有车次创建运行实例")}</button>
          </div>
        </form>}
        {popup==='corridor'&&<>
          <div className="ops-pane"><NetworkField mode={editMode} onChange={mode=>{
            if(corridorDirty&&!window.confirm(t('放弃未保存的通道草稿？')))return
            setCorridorDirty(false);setCorridor(undefined);setPrefill(undefined);setEditMode(mode)
            propsMap.current.showCorridor(null)
          }}/></div>
          <ControlCorridorEditor key={editMode} mode={editMode} snapshot={snapshot} corridors={data.corridors.filter(item=>!item.mode||item.mode===editMode)}
            selection={selection} prefill={prefill} current={corridor} setCurrent={setCorridor} execute={execute} busy={busy} dirty={setCorridorDirty}
            show={shape=>{if(shape)propsMap.current.revealLayer('corridor');propsMap.current.showCorridor(shape)}}
            locate={point=>{setFollow(false);propsMap.current.focusTrain(point,false)}} isDirty={corridorDirty}/>
          <details className="advanced-corridor"><summary>{t("高级物理边与示例")}</summary>
            <CorridorEditor snapshot={snapshot} corridors={corridorList} selection={selection}
              current={corridor} setCurrent={setCorridor} execute={execute} busy={busy}
              demoReady={runs=>{if(runs[0]){setDate(runs[0].service_date);changeRun(runs[0].id,runs[0],editMode);setPopup(undefined);void control('seek',28800)}}}/>
          </details>
        </>}
        {popup==='run'&&editor&&<>
          <div className="ops-actions ops-draft-tools">
            <button className="secondary compact" disabled={!past.length} onClick={()=>replayDraft(false)}>{t("撤销时刻修改")}</button>
            <button className="secondary compact" disabled={!future.length} onClick={()=>replayDraft(true)}>{t("重做时刻修改")}</button>
          </div>
          <RunEditor snapshot={snapshot} mode={editMode} run={editor} services={services}
            corridors={data.corridors.filter(item=>corridorKey(item)===runCorridorKey(editor)||item.current_version!==false&&(!item.mode||item.mode===editMode))}
            stationNames={new Map(detail?.chart.map(stop=>[stop.station_id,stop.station_name??stop.station_id]))}
            selection={selection} locate={inspectStop} setRun={updateDraft} busy={busy} save={run=>void execute(async()=>{
              const saved=await opsApi.saveRun(snapshot,run);setDate(saved.service_date);changeRun(saved.id,saved,editMode);setEditor(undefined);setPopup(undefined);setPast([]);setFuture([])
            },t("已保存计划版本"))} close={()=>{setEditor(undefined);setPopup(undefined)}}/>
        </>}
        {popup==='scenario'&&<div className="ops-pane">
        <p className="muted">{t("假设性场景分析。运行参数和追踪间隔由工作区设置。")}</p>
        <label>{t("场景")}<select value={scenario.id} onChange={e=>setScenario(data.scenarios.find(s=>s.id===e.target.value)??initialScenario)}>
          <option value="">{t("新建场景")}</option>{data.scenarios.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}
        </select></label>
        <label>{t("场景名称")}<input value={scenario.name} onChange={e=>setScenario({...scenario,name:e.target.value})}/></label>
        <div className="ops-fields two">
          <label>{t("追踪间隔（秒）")}<input type="number" min={0} max={3600} value={scenario.headway_s}
            onChange={e=>setScenario({...scenario,headway_s:e.target.valueAsNumber||0})}/></label>
          <label>{t("车组周转（秒）")}<input type="number" min={0} max={14400} value={scenario.turnaround_s}
            onChange={e=>setScenario({...scenario,turnaround_s:e.target.valueAsNumber||0})}/></label>
        </div>
        <label>{t("所选列车首发延误（秒）")}<input disabled={!selected} type="number" min={0} max={86400}
          value={scenario.delays[selected]??0} onChange={e=>setScenario({...scenario,delays:{...scenario.delays,[selected]:e.target.valueAsNumber||0}})}/></label>
        <div className="ops-actions">
          <button className="secondary" disabled={busy} onClick={()=>void execute(async()=>setScenario(await opsApi.saveScenario(snapshot,scenario)),t("场景已保存"))}>{t("保存场景")}</button>
          <button className="primary" disabled={busy||!runIds.length} onClick={()=>void execute(async()=>{
            const result=await opsApi.simulate(snapshot,runIds.slice(0,1000),clock.time_s,'l3',scenario,true)
            setLevel('l3');setAnalysis(result);setBottomTab('conflicts');setPopup(undefined);openBottom()
          },t("资源分析已计算"),false)}>{t("计算占用和冲突")}</button>
        </div>
        </div>}
      </OperationsDialog>}
      {bottomDock&&createPortal(<div hidden={!visible} className="ops-bottom">
      <div className="ops-time-panel">
        <div className="ops-time-top">
          <strong aria-label={t("运行时钟")}>{formatTime(clock.time_s)}</strong>
          <button className="icon framed" aria-label={clock.playing?t("暂停模拟"):t("播放模拟")} disabled={!runIds.length&&!editor?.corridor_id}
            onClick={()=>void control(clock.playing?'pause':'play',undefined,selected?contextMode:undefined)}>{clock.playing?<Pause size={16}/>:<Play size={16}/>}</button>
          <select aria-label={t("播放倍率")} value={clock.rate} onChange={e=>void control('rate',Number(e.target.value))}>
            {[.25,.5,1,2,5,10,30,60].map(r=><option key={r} value={r}>{r}×</option>)}
          </select>
          <button className="secondary compact" onClick={()=>void control('reset')}>{t("重置")}</button>
        </div>
        <form className="ops-time-seek" onSubmit={e=>{
          e.preventDefault()
          try {const time=parseTime(String(new FormData(e.currentTarget).get('seek')));if(time!=null)void control('seek',time)}
          catch(error){setError(describeError(error))}
        }}>
          <input name="seek" aria-label={t("跳转至时刻")} placeholder={t("HH:MM:SS，可跨日")} />
          <button className="secondary">{t("跳转")}</button>
        </form>
      </div>
      <div className="ops-map-controls">
        <div className="ops-train-layers" role="group" aria-label={t('列车图层')}>
          <label><input type="checkbox" checked={trainLayers.rail} onChange={event=>onTrainLayer('rail',event.target.checked)}/>{t('国铁列车')}</label>
          <label><input type="checkbox" checked={trainLayers.metro} onChange={event=>onTrainLayer('metro',event.target.checked)}/>{t('城轨列车')}</label>
        </div>
        <button className="secondary compact ops-follow" aria-pressed={follow} disabled={!selected} onClick={()=>{
          if(!follow)revealTrain(contextMode)
          setFollow(!follow)
        }}><Crosshair size={14}/>{follow?t('取消跟随 {0}',byRun.get(selected)?.train_number??detail?.run.train_number??''):
            t('跟随列车')}</button>
        <span className="muted">{follow?(selectedState?.lon==null?t('等待列车上线'):t('随车视角')):t('自由视角')}</span>
      </div>
      {editor&&draftFeedback&&<p className="muted" role="status">{t("草稿位置暂不可预览 ·")} {draftFeedback}</p>}
      <div className="bottom-tabs"><button className="secondary compact" aria-pressed={bottomTab==='table'} onClick={()=>setBottomTab('table')}>{t("时刻表")}</button>
        <button className="secondary compact" aria-pressed={bottomTab==='chart'} onClick={()=>setBottomTab('chart')}>{t("运行图")}</button>
        {(line?.mode==='metro'||detail?.service?.mode==='metro')&&<button className="secondary compact"
          aria-pressed={bottomTab==='metro'} onClick={()=>setBottomTab('metro')}>{t("地铁交路运行图")}</button>}
        {analysis&&<button className="secondary compact" aria-pressed={bottomTab==='conflicts'}
          onClick={()=>setBottomTab('conflicts')}>{t("占用与冲突")}</button>}</div>
      {bottomTab==='conflicts'&&<div className="ops-pane">
        {analysis&&<><p>{analysis.occupancies.length} {t("项占用 ·")} {analysis.conflicts.length} {t("项冲突")}</p>
          {analysis.diagnostics.map((d,i)=><p className="muted" key={i}>{d.message}</p>)}
          <ul className="ops-conflicts">{analysis.conflicts.slice(0,60).map((c,i)=><li key={i}>{t(resourceLabel[c.conflict_type]??c.conflict_type)}
            {' · '}{formatTime(c.start_time_s)} {t("至")} {formatTime(c.end_time_s)}
            <div className="ops-actions">{[c.train_run_a,c.train_run_b].map(id=><button key={id} className="secondary compact"
              onClick={()=>{changeRun(id);void control('seek',c.start_time_s)}}>{byRun.get(id)?.train_number??id.slice(0,8)}</button>)}
              {c.resource_type==='track'&&<button className="secondary compact" onClick={()=>inspectStop(c.resource_id)}>{t("定位轨道")}</button>}
            </div></li>)}</ul>
        </>}
      </div>}
      <div hidden={bottomTab!=='table'}>
        {detail?
          <table className="ops-table bottom-timetable"><thead><tr><th>{t("站序")}</th><th>{t("车站")}</th><th>{t("到达")}</th><th>{t("出发")}</th><th>{t("站台")}</th><th>{t("性质")}</th></tr></thead><tbody>
            {detail.chart.map((stop,i)=><tr key={i}><td>{i+1}</td><td><button className="station-link" onClick={()=>inspectStop(stop.platform_id??stop.station_id)}>{stop.station_name??stop.station_id}</button></td>
              <td>{formatTime(stop.arrival_time_s)}</td><td>{formatTime(stop.departure_time_s)}</td><td>{stop.platform_id??'—'}</td><td title={stop.timing_evidence}>{stop.pass_through?t("通过"):t("停站")}{stop.timing_evidence&&<small className="timing-evidence">{stop.timing_evidence}</small>}</td></tr>)}
          </tbody></table>:<p className="muted">{t("选择车次查看逐站到发时刻。")}</p>}
      </div>
      <div hidden={bottomTab!=='chart'}><RunGraph detail={detail} editor={editor} corridors={corridorList} time={clock.time_s}
        distance={(editor?draftPosition:selectedState)?.distance_m??null} seek={time=>void control('seek',time)} edit={updateDraft}/></div>
      {bottomTab==='metro'&&<MetroDiagram key={line?.id} lineName={line?.name} line7={line?.id==='metro:reference:shanghai-metro:7'}
        detail={detail} axis={metroAxis} runs={lineRuns} date={date} dates={data.dates_by_mode?.metro??[]} onDate={chooseDate}
        time={clock.time_s} select={changeRun} seek={time=>void control('seek',time)} locate={inspectStop} openTimetable={()=>setBottomTab('table')}/>}
      </div>,bottomDock)}
    </>}
  </div>
})

function NetworkField({mode,onChange}:{mode:NetworkMode;onChange:(mode:NetworkMode)=>void}){
  return <label>{t('运行制式')}<select value={mode} onChange={event=>onChange(event.target.value as NetworkMode)}>
    <option value="rail">{t(networkName.rail)}</option><option value="metro">{t(networkName.metro)}</option>
  </select></label>
}

function OperationsDialog({title,floating,close,children}:{title:string;floating:boolean;close:()=>void;children:ReactNode}){
  const ref=useRef<HTMLDialogElement>(null)
  const titleId=useId()
  useEffect(()=>{
    const dialog=ref.current
    if(!dialog)return
    if(floating)dialog.show()
    else dialog.showModal()
    return ()=>dialog.close()
  },[floating])
  return createPortal(<dialog ref={ref} className={floating?'operations-dialog map-editor':'operations-dialog'}
    aria-labelledby={titleId} onCancel={event=>{event.preventDefault();close()}}>
    <header className="panel-title"><h2 id={titleId}>{title}</h2>
      <button className="icon" aria-label={t("关闭")} onClick={close}><X size={17}/></button>
    </header>
    <div className="operations-dialog-body">{children}</div>
  </dialog>,document.body)
}

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
    <label>{t("已有通道")}<select value={current?.id??''} onChange={e=>setCurrent(corridors.find(c=>c.id===e.target.value))}>
      <option value="">{t("构建通道")}</option>{corridors.map(c=><option value={c.id} key={c.id}>{c.name} · v{c.version}</option>)}
    </select></label>
    <label>{t("通道名称")}<input value={name} onChange={e=>setName(e.target.value)}/></label>
    {[[t("起点边"),start,setStart],[t("终点边"),end,setEnd]].map(([label,value,set])=>
      <label key={String(label)}>{String(label)}<span className="ops-inline">
        <input value={String(value)} placeholder={t("物理边 UUID")} onChange={e=>(set as (value:string)=>void)(e.target.value)}/>
        <button className="secondary compact" disabled={!pickEdge} onClick={()=>(set as (value:string)=>void)(selection!.id)}>{t("从地图选取")}</button>
      </span></label>)}
    <label>{t("途经边 UUID")}<input value={waypoints} onChange={e=>setWaypoints(e.target.value)} placeholder={t("按走行顺序，可选")}/></label>
    <label>{t("限定线路 UUID")}<input value={lines} onChange={e=>setLines(e.target.value)} placeholder={t("由业务线路属性复制")}/>
      {selection?.kind==='line'&&<button className="secondary compact" onClick={()=>setLines(value=>[value,selection.id].filter(Boolean).join(','))}>{t("加入选中线路")}</button>}
    </label>
    <label className="ops-inline"><input type="checkbox" checked={strict} onChange={e=>setStrict(e.target.checked)}/>{t("仅在路径唯一时接受")}</label>
    <button className="primary" disabled={!start||!end||!name||busy} onClick={()=>void execute(async()=>{
      const resolved=await opsApi.resolve(snapshot,{start_id:start,end_id:end,strict,name,waypoints:list(waypoints),line_ids:list(lines)})
      setCurrent(current?.id?{...resolved,id:current.id,version:current.version}:resolved)
    },t("候选已计算，请核对走行方向"),false)}><ArrowRight size={14}/>{t("解析走行通道")}</button>
    {current&&<>
      <p>{current.edge_refs.length} {t("个物理区段 ·")} {t(trustLabel[current.verification_status])}</p>
      {current.warnings?.map(w=><p key={w} className="muted">{w}</p>)}
      {current.candidates?.map((candidate,i)=><details key={i}><summary>{t("候选路径")} {i+1} · {(candidate.meters/1000).toFixed(2)} km</summary>
        <p className="ops-identifiers">{candidate.edge_ids.join(' → ')}</p></details>)}
      <label>{t("物理区段与方向")}<textarea value={directionRows} onChange={e=>setDirectionRows(e.target.value)} rows={5}
        placeholder={t("+ UUID 表示正向，- UUID 表示反向")}/></label>
      <button className="secondary" disabled={busy||!name} onClick={()=>void execute(async()=>{
        const refs=directionRows.trim().split(/\n/).filter(r=>r.trim()).map((row,i)=>{
          const match=/^([+-])\s+([^\s]+)$/.exec(row.trim())
          if(!match)throw new Error(t("第 {0} 行需要 + 或 - 与物理边 UUID", i+1))
          return {edge_id:match[2],sequence:i,forward:match[1]==='+',start_distance_m:0,end_distance_m:0}
        })
        const changed=JSON.stringify(refs.map(e=>[e.edge_id,e.forward]))!==JSON.stringify(current.edge_refs.map(e=>[e.edge_id,e.forward]))
        setCurrent(await opsApi.saveCorridor(snapshot,{...current,name,edge_refs:refs,
          ...(changed?{verification_status:'automatic_reference' as const,resolution_mode:'manual',provenance:userSource}:{})}))
      },t("通道版本已保存"))}>{t("保存通道版本")}</button>
      {current.id&&<details>
        <summary>{t("创建仿真示例")}</summary>
        <p className="muted">{t("生成两趟沿已保存通道的演示计划。请指定示例起终站。")}</p>
        <label>{t("示例日期")}<input type="date" value={date} onChange={e=>setDate(e.target.value)}/></label>
        <label>{t("示例起站")}<input value={origin} onChange={e=>setOrigin(e.target.value)}/>
          <button className="secondary compact" disabled={selection?.kind!=='station'} onClick={()=>setOrigin(selection!.id)}>{t("填入选中车站")}</button></label>
        <label>{t("示例终站")}<input value={destination} onChange={e=>setDestination(e.target.value)}/>
          <button className="secondary compact" disabled={selection?.kind!=='station'} onClick={()=>setDestination(selection!.id)}>{t("填入选中车站")}</button></label>
        <button className="secondary" disabled={!origin||!destination||busy} onClick={()=>void execute(async()=>{
          const result=await opsApi.demo(snapshot,current,origin,destination,date);demoReady(result.runs)
        },t("已生成明确标注的仿真示例"))}>{t("生成两趟示例")}</button>
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
function RunEditor({snapshot,mode,run,services,corridors,stationNames,selection,setRun,save,close,busy,locate}:{
  snapshot:string;mode:'rail'|'metro';
  stationNames:Map<string,string>;
  run:Run;services:Service[];corridors:Corridor[];selection?:Entity;setRun:(r:Run)=>void;save:(r:Run)=>void;close:()=>void;busy:boolean;locate:(id:string)=>void
}){
  const corridor=corridors.find(c=>c.id===run.corridor_id && c.version===run.corridor_version)
  const [parseError,setParseError]=useState('')
  const [minutes,setMinutes]=useState(0)
  const [matching,setMatching]=useState(false)
  const [matchReport,setMatchReport]=useState('')
  const set=(i:number,value:Partial<Stop>)=>setRun({...run,stops:run.stops.map((stop,n)=>n===i?{...stop,...value}:stop)})
  const setStation=(i:number,station_id:string)=>set(i,{station_id,platform_id:null,stop_edge_id:null,stop_edge_sequence:null,stop_offset_m:null,anchor_source:null})
  async function suggest(){
    setMatching(true);setParseError('');setMatchReport('')
    try{
      const result=await opsApi.suggestAnchors(snapshot,run)
      setRun(result.run)
      setMatchReport(result.matches.length?t("已定位 {0} 站。最远站点偏移 {1} m，使用自动参考锚点。",result.matches.length,
        Math.max(...result.matches.map(item=>item.distance_m)).toFixed(1)):t("已有停站锚点已保留。"))
    }catch(error){setParseError(describeError(error))}finally{setMatching(false)}
  }
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
    <label>{t("车次服务")}<select value={run.service_id} onChange={e=>setRun({...run,service_id:e.target.value})}>
      {services.map(service=><option key={service.id} value={service.id}>{service.train_code} · {service.mode==='rail'?t("国铁"):t("上海地铁")}</option>)}
    </select></label>
    <label>{t("运营日期")}<input required type="date" value={run.service_date} onChange={e=>setRun({...run,service_date:e.target.value})}/></label>
    <label>{t("计划状态")}<select value={run.status} onChange={e=>setRun({...run,status:e.target.value})}>
      <option value="scheduled">{t("计划开行")}</option><option value="cancelled">{t("取消")}</option>
    </select></label>
    <label>{t("固定通道版本")}<select value={corridor?`${corridor.id}:${corridor.version}`:''} onChange={e=>{
      const c=corridors.find(c=>`${c.id}:${c.version}`===e.target.value)
      setRun({...run,corridor_id:c?.id,corridor_version:c?.version,
        stops:run.stops.map(stop=>({...stop,stop_edge_id:null,stop_edge_sequence:null,stop_offset_m:null,anchor_source:null}))})
    }}><option value="">{t("时刻表 · 暂无通道")}</option>
      {corridors.map(c=><option key={`${c.id}:${c.version}`} value={`${c.id}:${c.version}`}>{c.name} · v{c.version}</option>)}
    </select></label>
    <div className="ops-actions">
      <button type="button" className="secondary" disabled={busy||matching||!corridor||run.stops.some(stop=>!stop.station_id)}
        onClick={()=>void suggest()}>{t("按所选通道定位停站")}</button>
      {matchReport&&<small role="status">{matchReport}</small>}
    </div>
    <div className="ops-inline"><label>{t("整车平移（分钟）")}<input type="number" value={minutes} onChange={e=>setMinutes(e.target.valueAsNumber||0)}/></label>
      <button type="button" className="secondary compact" disabled={!minutes} onClick={()=>{
        const shift=(value:number|null|undefined)=>value==null?null:value+Math.round(minutes*60)
        const stops=run.stops.map(stop=>({...stop,arrival_time_s:shift(stop.arrival_time_s),departure_time_s:shift(stop.departure_time_s)}))
        if(stops.some(stop=>[stop.arrival_time_s,stop.departure_time_s].some(t=>t!=null&&(t<0||t>604800)))){
          setParseError(t("平移后时刻超出运营日范围。")); return
        }
        setParseError('');setRun({...run,stops})
      }}>{t("平移时刻")}</button></div>
    {run.stops.map((stop,i)=><fieldset key={i}>
      <legend>{t("停站")} {i+1}</legend>
      <StopStationPicker snapshot={snapshot} mode={mode} stationId={stop.station_id}
        knownName={selection?.id===stop.station_id?selection.name:stationNames.get(stop.station_id)} onPick={id=>setStation(i,id)}/>
      <div className="ops-actions">
        <button className="secondary compact" type="button" disabled={selection?.kind!=='station'||(selection.class==='metro_station')!==(mode==='metro')}
          onClick={()=>setStation(i,selection!.id)}>{t("填入选中车站")}</button>
        <button className="secondary compact" type="button" disabled={!stop.station_id} onClick={()=>locate(stop.platform_id??stop.station_id)}>{t("定位站点")}</button>
      </div>
      <div className="ops-fields two">
        <label>{t("到达")}<TimeInput name={`arrive-${i}`} value={stop.arrival_time_s} change={arrival_time_s=>set(i,{arrival_time_s})}/></label>
        <label>{t("出发")}<TimeInput name={`depart-${i}`} value={stop.departure_time_s} change={departure_time_s=>set(i,{departure_time_s})}/></label>
      </div>
      <label className="ops-inline"><input type="checkbox" checked={!!stop.pass_through} onChange={e=>set(i,{pass_through:e.target.checked})}/>{t("通过站")}</label>
      <label>{t("已确认站台")}<input value={stop.platform_id??''} onChange={e=>set(i,{platform_id:e.target.value||null})} placeholder={t("稳定站台 ID，可留空")}/></label>
      <button className="secondary compact" type="button" disabled={selection?.class!=='platform'} onClick={()=>set(i,{platform_id:selection!.id})}>{t("填入选中站台")}</button>
      {corridor&&<div className="ops-fields two">
        <label>{t("通道内区段序号")}<input value={stop.stop_edge_sequence==null?'':stop.stop_edge_sequence+1} type="number" min={1}
          max={corridor.edge_refs.length} onChange={e=>{
            const ref=corridor.edge_refs[e.target.valueAsNumber-1]
            set(i,{stop_edge_id:ref?.edge_id??null,stop_edge_sequence:ref?.sequence??null,stop_offset_m:ref?(stop.stop_offset_m??0):null,anchor_source:null})
          }}/></label>
        <label>{t("沿走行方向偏移（米）")}<input type="number" step="any" min={0} value={stop.stop_offset_m??''}
          onChange={e=>set(i,{stop_offset_m:e.target.value===''?null:e.target.valueAsNumber,anchor_source:null})}/></label>
      </div>}
    </fieldset>)}
    <div className="ops-actions">
      <button className="secondary compact" type="button" onClick={()=>setRun({...run,stops:[...run.stops,unboundStop(run.stops.length)]})}>{t("增加停站")}</button>
      <button className="secondary compact" type="button" disabled={run.stops.length<=2}
        onClick={()=>setRun({...run,stops:run.stops.slice(0,-1)})}>{t("删除末站")}</button>
      <button className="primary" disabled={busy}>{t("保存时刻")}</button><button className="secondary" onClick={close} type="button">{t("取消")}</button>
    </div>
    {parseError&&<p className="error-text">{parseError}</p>}
  </form>
}

function StopStationPicker({snapshot,mode,stationId,knownName,onPick}:{snapshot:string;mode:'rail'|'metro';stationId:string;knownName?:string;onPick:(id:string)=>void}){
  const [query,setQuery]=useState('')
  const [picked,setPicked]=useState<Summary>()
  const [items,setItems]=useState<Summary[]>([])
  const [error,setError]=useState('')
  useEffect(()=>{
    setItems([]);setError('')
    if(!query.trim())return
    let active=true
    const timer=window.setTimeout(()=>{
      const term=query.trim()
      const stationClass=mode==='metro'?'metro_station':'rail_station'
      const lookup=/^[a-f\d-]{36}$/i.test(term)
        ? api.entity(snapshot,term).then(({entity})=>entity.kind==='station'&&entity.class===stationClass?[entity]:[])
        : api.page(snapshot,{kind:'station',query:term,classes:[stationClass]}).then(page=>page.items.slice(0,30))
      void lookup.then(results=>{if(active)setItems(results)})
        .catch(error=>{if(active)setError(describeError(error))})
    },200)
    return ()=>{active=false;window.clearTimeout(timer)}
  },[snapshot,mode,query])
  return <>
    <strong>{knownName||(picked?.id===stationId?picked.name:stationId)||t("尚未选站")}</strong>
    <label>{t("搜索车站")}<input value={query} maxLength={128} onChange={e=>setQuery(e.target.value)}
      onKeyDown={e=>{if(e.key==='Enter')e.preventDefault()}}/></label>
    {!!items.length&&<select aria-label={t("选择停站")} value="" onChange={e=>{
      const station=items.find(item=>item.id===e.target.value)
      if(station){setPicked(station);onPick(station.id);setQuery('')}
    }}>
      <option value="">{t("选择停站")}</option>
      {items.map(item=><option key={item.id} value={item.id}>{item.name} · {item.bounds.slice(0,2).map(value=>value.toFixed(2)).join(', ')}</option>)}
    </select>}
    {error&&<p className="error-text">{error}</p>}
  </>
}

function TimeInput({name,value,change}:{name:string;value:number|null|undefined;change:(value:number|null)=>void}){
  const formatted=value==null?'':formatTime(value)
  const [text,setText]=useState(formatted)
  const [invalid,setInvalid]=useState(false)
  useEffect(()=>{setText(formatted);setInvalid(false)},[formatted])
  return <input name={name} value={text} aria-invalid={invalid} placeholder={t("可超过 24:00")} onChange={e=>setText(e.target.value)} onBlur={()=>{
    try {const next=parseTime(text)??null;setInvalid(false);if(next!==(value??null))change(next)}
    catch {setInvalid(true)}
  }}/>
}

function OperationsData({snapshot,data,selected,date,execute,busy,onImported}:{
  snapshot:string;data:OperationList;selected:string;date:string;execute:Execute;busy:boolean;onImported:(date:string)=>void
}){
  const [stock,setStock]=useState('')
  const [vehicle,setVehicle]=useState('')
  const [stockId,setStockId]=useState('')
  const [vehicleId,setVehicleId]=useState('')
  const [format,setFormat]=useState<ScheduleFormat>('json')
  const [stopMap,setStopMap]=useState('')
  const file=useRef<HTMLInputElement>(null)

  async function readFile(f:File){
    if(f.size>(format==='json'?32:16)*1024*1024)throw new Error(t("文件超出导入容量，请按日期或线路拆分。"))
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
    <h3>{t("时刻表导入与导出")}</h3>
    <p className="muted">{t("导入其他时刻计划时，请使用与当前快照匹配的文件。")}</p>
    <label>{t("文件格式")}<select value={format} onChange={e=>{setFormat(e.target.value as ScheduleFormat);if(file.current)file.current.value=''}}>
      <option value="json">RailScope JSON</option><option value="csv">CSV</option><option value="gtfs">GTFS ZIP</option>
    </select></label>
    {format==='gtfs'&&<label>{t("GTFS 站点映射")}<textarea value={stopMap} onChange={e=>setStopMap(e.target.value)} rows={3} placeholder={t("{\"feed-stop\":\"RailScope 车站 UUID\"}")}/></label>}
    <input aria-label={t("选择时刻表文件")} type="file" ref={file} accept={format==='gtfs'?'.zip':'.'+format}/>
    <div className="ops-actions">
      <button className="secondary" disabled={busy} onClick={()=>void execute(async()=>{
        const input=file.current?.files?.[0]
        if(!input)throw new Error(t("请选择时刻表文件。"))
        const imported=await opsApi.import(snapshot,format,await readFile(input),date,stopMap?JSON.parse(stopMap):undefined)
        if(!imported.runs)throw new Error(t("没有匹配的运行实例。"))
        if(imported.first_date)onImported(imported.first_date)
      },t("时刻表已导入"))}><Upload size={15}/>{t("导入")}</button>
      <button className="secondary" disabled={busy||!data.runs.length} onClick={()=>void execute(download,t("导出文件已生成"),false)}><Download size={15}/>{t("导出")}{selected?t("所选车次"):t("时刻表")}</button>
    </div>
    <h3>{t("车型、车组和担当")}</h3>
    <label>{t("车型名称")}<input value={stock} onChange={e=>setStock(e.target.value)}/></label>
    <button className="secondary" disabled={busy||!stock} onClick={()=>void execute(async()=>{
      const result=await opsApi.saveStock(snapshot,{id:'',name:stock,provenance:userSource});setStockId(result.id);setStock('')
    },t("车型已保存"))}>{t("新增车型")}</button>
    <label>{t("车组名称")}<input value={vehicle} onChange={e=>setVehicle(e.target.value)}/></label>
    <label>{t("车型")}<select value={stockId} onChange={e=>setStockId(e.target.value)}>
      <option value="">{t("未知")}</option>{data.stock_types.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
    <button className="secondary" disabled={busy||!vehicle} onClick={()=>void execute(async()=>{
      const result=await opsApi.saveTrainset(snapshot,{id:'',name:vehicle,stock_type_id:stockId||undefined,provenance:userSource})
      setVehicleId(result.id);setVehicle('')
    },t("车组已保存"))}>{t("新增车组")}</button>
    <label>{t("担当车组")}<select value={vehicleId} onChange={e=>setVehicleId(e.target.value)}>
      <option value="">{t("请选择")}</option>{data.trainsets.map(v=><option key={v.id} value={v.id}>{v.name}</option>)}</select></label>
    <button className="secondary" disabled={busy||!vehicleId||!selected}
      onClick={()=>void execute(()=>opsApi.assign(snapshot,selected,vehicleId),t("所选运行实例的担当已保存"))}>{t("分配给所选车次")}</button>
    {data.services.some(service=>service.mode==='metro')&&<CycleForm snapshot={snapshot} data={data} date={date} execute={execute} busy={busy}/>}
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
  return <details className="cycle-panel"><summary>{t("上海地铁往返循环")}</summary>
    <div className="task-summary"><p>{t("使用已有的有锚点单程车次，按两端折返与投放间隔生成新计划。")}</p>
      {!!data.cycles.length&&<label>{t("已保存配置")}<select value="" onChange={e=>useTemplate(data.cycles.find(item=>item.id===e.target.value))}>
        <option value="">{t("选择后载入参数")}</option>{data.cycles.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}
      <label>{t("循环名称")}<input value={name} onChange={e=>setName(e.target.value)}/></label>
      <label>{t("出程模板")}<select value={out} onChange={e=>{setOut(e.target.value);setBack('')}}><option value="">{t("选择有径路的城轨车次")}</option>
        {options.map(run=><option value={run.id} key={run.id}>{datedRunLabel(run)} · v{run.plan_version}</option>)}</select></label>
      <label>{t("返程模板")}<select value={back} onChange={e=>setBack(e.target.value)}><option value="">{t("选择反向接续车次")}</option>
        {returnOptions.map(run=><option value={run.id} key={run.id}>{datedRunLabel(run)} · v{run.plan_version}</option>)}</select></label>
      <div className="ops-fields two"><label>{t("投放开始")}<input value={values.first} onChange={e=>setValues({...values,first:e.target.value})}/></label>
        <label>{t("投放截止")}<input value={values.end} onChange={e=>setValues({...values,end:e.target.value})}/></label></div>
      <div className="ops-fields two"><label>{t("折返（秒）")}<input type="number" value={values.turnaround} onChange={e=>setValues({...values,turnaround:e.target.valueAsNumber||0})}/></label>
        <label>{t("投入间隔（秒）")}<input type="number" value={values.gap} onChange={e=>setValues({...values,gap:e.target.valueAsNumber||0})}/></label></div>
      <label>{t("投放车辆数")}<input type="number" min={1} max={100} value={values.vehicles} onChange={e=>setValues({...values,vehicles:e.target.valueAsNumber||1})}/></label>
      <label>{t("首组已核实车组")}<select value={values.vehicle} onChange={e=>setValues({...values,vehicle:e.target.value})}>
        <option value="">{t("未指定车组")}</option>{data.trainsets.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <button className="primary" disabled={busy||!name.trim()||!outRun||!backRun} onClick={()=>void execute(async()=>{
        if(!outRun||!backRun)return
        const first=parseTime(values.first);const end=parseTime(values.end)
        if(first==null||end==null)throw new Error(t("请输入完整运营时段"))
        const result=await opsApi.generateCycle(snapshot,{id:'',name,outbound_id:outRun.id,outbound_version:outRun.plan_version,
          return_id:backRun.id,return_version:backRun.plan_version,service_date:date,first_departure_s:first,end_s:end,
          turnaround_s:values.turnaround,launch_gap_s:values.gap,vehicles:values.vehicles,trainsets:values.vehicle?[values.vehicle]:[]})
        setMessage(t("已为 {0} 生成 {1} 趟可单独编辑的计划车次", date, result.runs))
      })}>{t("生成并保存计划")}</button>
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
  if(points.length<2)return <p className="muted">{t("运行图需要已锚定的时刻与通道。")}</p>
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
    <figcaption>{t("时间—距离运行图")}</figcaption>
    <svg viewBox="0 0 720 205" role="img" aria-label={t("所选列车计划线和当前时刻")} preserveAspectRatio="none"
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
        }}><title>{t("第 {0} 站 {1} {2}", item.index+1, item.field==='arrival_time_s'?'到达':'出发', formatTime(item.time))}</title></circle>)}
      {time>=begin&&time<=end&&<line x1={x(time)} x2={x(time)} y1="20" y2="175" stroke="#158962" strokeDasharray="3 2"/>}
      {time>=begin&&time<=end&&distance!=null&&<circle cx={x(time)} cy={y(distance)} r="4" fill="#158962"/>}
    </svg>
  </figure>
}
