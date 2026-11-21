import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import type { Geometry } from 'geojson'
import { api, type Entity, type Summary } from './api'
import { describeError, opsApi, type ControlPoint, type ControlStep, type Corridor, type CorridorPreview } from './operations-api'

type Props={
  snapshot:string; corridors:Corridor[]; selection?:Entity; prefill?:{start?:Summary;end?:Summary}; current?:Corridor;
  setCurrent:(corridor?:Corridor)=>void; execute:(task:()=>Promise<unknown>,message?:string,refresh?:boolean)=>Promise<void>;
  busy:boolean; dirty:(value:boolean)=>void; show:(shape:Geometry|null)=>void; locate:(point:[number,number])=>void;
  dock:HTMLElement|null;visible:boolean;open:()=>void;
}
type Choice = {id:string;name:string}
const row=():ControlStep=>({node_id:'',line_id:null,via_edge:null})

export function ControlCorridorEditor({snapshot,corridors,selection,prefill,current,setCurrent,execute,busy,dirty,show,locate,dock,visible,open}:Props){
  const [name,setName]=useState('')
  const [catalogQuery,setCatalogQuery]=useState('')
  const [mode,setMode]=useState<'rail'|'metro'>('rail')
  const [strict,setStrict]=useState(false)
  const [steps,setSteps]=useState<ControlStep[]>(()=>[row(),row()])
  const [labels,setLabels]=useState<Record<string,string>>({})
  const [source,setSource]=useState<Partial<Record<number,Summary>>>({})
  const [preview,setPreview]=useState<CorridorPreview>()

  const invalidate=()=>{setPreview(undefined);dirty(true)}
  const setStep=(i:number,value:Partial<ControlStep>)=>{
    setSteps(old=>old.map((step,index)=>index===i?{...step,...value}:step));invalidate()
  }
  const choosePoint=(i:number,point:ControlPoint)=>{
    setLabels(old=>({...old,[point.id]:point.name}));setStep(i,{node_id:point.id,line_id:null,via_edge:null});locate(point.point)
  }
  const changeRows=(next:ControlStep[])=>{
    setSource({});setSteps(next.map((step,i)=>i===next.length-1?{...step,line_id:null,via_edge:null}:step));invalidate()
  }
  function usePreview(value:CorridorPreview, existing?:Corridor){
    const plan=existing?.id?{...value.corridor,id:existing.id,version:existing.version}:value.corridor
    setCurrent(plan);setPreview({...value,corridor:plan});setName(plan.name);show(value.geometry)
  }
  function reset(){setName('');setSteps([row(),row()]);setSource({});setPreview(undefined);setCurrent(undefined);show(null);dirty(false)}

  useEffect(()=>{
    if(!prefill)return
    reset()
    const start=prefill.start
    setSource({...(start&&{0:start}),...(prefill.end&&{1:prefill.end})})
    if(start){setName(`${start.name}出发`);setMode(start.kind==='metro'||start.class==='metro_station'?'metro':'rail');dirty(true)}
  },[prefill])

  function load(id:string){
    const found=corridors.find(item=>item.id===id)
    if(!found){reset();return}
    open()
    void execute(async()=>{
      const value=await opsApi.corridorQuery<CorridorPreview>(snapshot,'preview',{corridor:found})
      setSteps(found.intent?.length?found.intent:[row(),row()]);setSource({});usePreview(value)
      setMode(value.mode);setStrict(found.resolution_mode==='strict_unique');dirty(false)
    },undefined,false)
  }

  function save(){
    if(!preview)return
    void execute(async()=>{
      const saved=await opsApi.saveCorridor(snapshot,{...preview.corridor,name})
      setCurrent(saved);setPreview({...preview,corridor:saved});dirty(false)
    },'已保存通道版本')
  }

  function download(){
    if(!current?.id)return
    const href=URL.createObjectURL(new Blob([JSON.stringify(current,null,2)],{type:'application/json'}))
    const a=document.createElement('a');a.href=href;a.download='railscope-corridor.json';a.click();window.setTimeout(()=>URL.revokeObjectURL(href),1000)
  }

  const work=<div className="ops-pane corridor-form" hidden={!visible}>
    <div className="corridor-work-title">
      <strong>端点与线路编排</strong>
      <span>{current?.source_id.startsWith('legacy-import:')?current.source_id.slice(14):'连续有向物理轨道'}</span>
    </div>
    <div className="ops-actions">
      <button className="secondary compact" onClick={()=>{if(window.confirm('清除当前通道草稿？'))reset()}}>新建／清空草稿</button>
      <button className="secondary compact" disabled={!preview||busy} onClick={()=>{
        if(!preview)return
        usePreview({...preview,corridor:{...preview.corridor,id:'',version:0,name:`${name} · 副本`}});dirty(true)
      }}>复制</button>
      <button className="secondary compact" disabled={!preview||busy} onClick={()=>void execute(async()=>{
        const value=await opsApi.corridorQuery<CorridorPreview>(snapshot,'reverse',{corridor:preview!.corridor})
        setSteps([row(),row()]);usePreview(value);dirty(true)
      },'反向候选通过方向与连通性校验',false)}>反向候选</button>
    </div>
    <label>通道名称<input value={name} onChange={e=>{setName(e.target.value);dirty(true)}}/></label>
    <div className="ops-fields two">
      <label>制式<select value={mode} onChange={e=>{setMode(e.target.value as typeof mode);invalidate()}}><option value="rail">国铁</option><option value="metro">上海地铁</option></select></label>
      <label>解析方式<select value={strict?'strict':'reference'} onChange={e=>{setStrict(e.target.value==='strict');invalidate()}}>
        <option value="reference">自动参考</option><option value="strict">严格唯一</option>
      </select></label>
    </div>
    <div className="corridor-columns"><span>起点／换线端点</span><span>接轨线路</span><span>沿线可达端点</span></div>
    {steps.map((step,i)=><fieldset key={i} className="control-step"><legend>{i===0?'起点':i===steps.length-1?'终点':`途经控制点 ${i}`}</legend>
      <div className="control-table-row"><div className="control-endpoint">
      <ControlPicker snapshot={snapshot} pointId={step.node_id} name={labels[step.node_id]} initial={source[i]}
        selection={selection} onPick={point=>choosePoint(i,point)}/>
      </div>
      {i<steps.length-1&&<LegPicker snapshot={snapshot} mode={mode} step={step} setLine={id=>setStep(i,{line_id:id||null})}
        choose={point=>choosePoint(i+1,point)} setVia={value=>setStep(i,{via_edge:value||null})}/>}
      </div>
      <div className="ops-actions">
        <button className="secondary compact" disabled={i===0} onClick={()=>{const next=[...steps];[next[i-1],next[i]]=[next[i],next[i-1]];changeRows(next)}}>上移</button>
        <button className="secondary compact" disabled={i===steps.length-1} onClick={()=>{const next=[...steps];[next[i],next[i+1]]=[next[i+1],next[i]];changeRows(next)}}>下移</button>
        <button className="secondary compact" disabled={steps.length>=18} onClick={()=>changeRows([...steps.slice(0,i+1),row(),...steps.slice(i+1)])}>在后插入</button>
        <button className="secondary compact" disabled={steps.length<=2} onClick={()=>changeRows(steps.filter((_,n)=>n!==i))}>移除此行</button>
      </div>
    </fieldset>)}
    <div className="ops-actions">
      <button className="secondary" disabled={busy||!name.trim()||steps.some((step,i)=>!step.node_id||i<steps.length-1&&!step.line_id)} onClick={()=>void execute(async()=>{
        const value=await opsApi.corridorQuery<CorridorPreview>(snapshot,'resolve',{name,mode,steps,strict})
        usePreview(value,current);dirty(true)
      },'已显示校验通过的拓扑参考路径',false)}>校验并预览</button>
      <button className="primary" disabled={!preview||busy||!name.trim()} onClick={save}>保存通道版本</button>
      <button className="secondary compact" disabled={!current?.id||busy} onClick={()=>{
        if(current?.id&&window.confirm('归档此通道？已引用它的车次将保留现有版本。'))void execute(async()=>{await opsApi.archiveCorridor(snapshot,current.id);reset()},'已归档通道')
      }}>归档</button>
    </div>
    <details><summary>通道文件</summary><div className="ops-actions">
      <button className="secondary compact" disabled={!current?.id} onClick={download}>导出通道</button>
      <input aria-label="导入通道 JSON" type="file" accept=".json" onChange={e=>{
        const file=e.target.files?.[0];e.target.value='';if(!file)return
        void execute(async()=>{
          if(file.size>4*1024*1024)throw new Error('通道文件超过 4 MiB')
          const input=JSON.parse(await file.text())
          const value=await opsApi.corridorQuery<CorridorPreview>(snapshot,'preview',{corridor:{...input,id:'',version:0}})
          setSteps(input.intent?.length?input.intent:[row(),row()]);setSource({});usePreview(value);dirty(true)
        },'导入草稿已校验，请确认后保存',false)
      }}/>
    </div></details>
  </div>
  return <div className="ops-pane corridor-library">
    <label>检索共享通道<input value={catalogQuery} onChange={e=>setCatalogQuery(e.target.value)} placeholder="名称、端点或通道 ID"/></label>
    <label>当前通道<select value={current?.id??''} onChange={e=>load(e.target.value)}>
      <option value="">新建通道</option>{corridors.filter(item=>item.id===current?.id||
        [item.name,item.id,item.origin_node_id,item.destination_node_id,item.source_id].some(value=>value.toLowerCase().includes(catalogQuery.trim().toLowerCase())))
        .map(item=><option key={item.id} value={item.id}>{item.name} · v{item.version}</option>)}
    </select></label>
    <button className="primary" onClick={open}>打开通道编排</button>
    {dock&&createPortal(work,dock)}
  </div>
}

function ControlPicker({snapshot,pointId,name,initial,selection,onPick}:{snapshot:string;pointId:string;name?:string;initial?:Summary;selection?:Summary;onPick:(point:ControlPoint)=>void}){
  const [term,setTerm]=useState('')
  const [matches,setMatches]=useState<Summary[]>([])
  const [controls,setControls]=useState<ControlPoint[]>([])
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  const [label,setLabel]=useState('')
  async function pick(item:Summary){
    setLabel(item.name);setError('');setControls([]);setBusy(true)
    try {
      const reply=await opsApi.corridorQuery<ControlPoint[]>(snapshot,'controls',{id:item.id})
      setControls(reply)
      if(!reply.length)setError('该对象缺少可确认的轨道控制端点。可在地图中选取明确的物理边。')
    }catch(error){setError(describeError(error))}finally{setBusy(false)}
  }
  useEffect(()=>{if(initial)void pick(initial)},[initial?.id])
  return <>
    <small className="control-selected" title={pointId}>{pointId?name??pointId:label||'尚未指定物理控制端点'}</small>
    <div className="ops-inline"><input aria-label="搜索控制端点" placeholder="车站、线路所或稳定 ID" value={term} onChange={e=>setTerm(e.target.value)}
      onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();void search()}}}/>
      <button className="secondary compact" disabled={!term.trim()||busy} onClick={()=>void search()}>查找</button></div>
    {matches.length>0&&<select aria-label="匹配对象" value="" onChange={e=>{
      const found=matches.find(item=>item.id===e.target.value);if(found)void pick(found)
    }}><option value="">选择来源对象…</option>{matches.map(item=><option value={item.id} key={item.id}>{item.name} · {item.bounds.slice(0,2).map(n=>n.toFixed(2)).join(', ')} · {item.id.slice(0,8)}</option>)}</select>}
    <button className="secondary compact" disabled={busy||!selection||!['rail','metro','station','facility'].includes(selection.kind)}
      onClick={()=>{if(selection)void pick(selection)}}>从地图读取选中对象</button>
    {!!controls.length&&<select aria-label="明确路径锚点" value={controls.some(item=>item.id===pointId)?pointId:''} onChange={e=>{
      const found=controls.find(item=>item.id===e.target.value);if(found)onPick(found)
    }}><option value="">选择明确路径锚点…</option>{controls.map(item=><option key={item.id} value={item.id}>{item.name} · {item.point.map(n=>n.toFixed(4)).join(', ')}</option>)}</select>}
    {error&&<p className="error-text" role="alert">{error}</p>}
  </>

  async function search(){
    setBusy(true);setError('')
    try {
      const query=term.trim()
      if(/^[a-f\d-]{36}$/i.test(query)){
        const reply=await opsApi.corridorQuery<ControlPoint[]>(snapshot,'controls',{id:query})
        setMatches([]);setControls(reply)
        if(!reply.length)setError('找不到当前快照的控制端点')
      }else{
        const reply=await api.page(snapshot,{query})
        const items=reply.items.filter(item=>['rail','metro','station','facility'].includes(item.kind))
        setMatches(items)
        if(!items.length)setError('找不到匹配的车站或轨道对象')
      }
    }catch(error){setError(describeError(error))}finally{setBusy(false)}
  }
}

function LegPicker({snapshot,mode,step,setLine,choose,setVia}:{snapshot:string;mode:string;step:ControlStep;setLine:(id:string)=>void;choose:(point:ControlPoint)=>void;setVia:(id:string)=>void}){
  const [lines,setLines]=useState<Choice[]>([])
  const [ends,setEnds]=useState<ControlPoint[]>([])
  const [busy,setBusy]=useState('')
  const [error,setError]=useState('')
  const [query,setQuery]=useState('')
  useEffect(()=>{setLines([]);setEnds([]);setError('')},[step.node_id,mode])
  useEffect(()=>{setEnds([])},[step.line_id])
  async function load(action:'lines'|'destinations'){
    setBusy(action);setError('')
    try {
      const args={node:step.node_id,line:step.line_id,mode}
      if(action==='lines'){
        const list=await opsApi.corridorQuery<Choice[]>(snapshot,action,args);setLines(list)
        if(!list.length)setError('该方向没有确认接轨的业务线路')
      }else{
        const list=await opsApi.corridorQuery<ControlPoint[]>(snapshot,action,args);setEnds(list)
        if(!list.length)setError('该线路没有可达的下一控制端点')
      }
    }catch(error){setError(describeError(error))}finally{setBusy('')}
  }
  const matches=ends.filter(item=>`${item.name} ${item.id}`.toLowerCase().includes(query.toLowerCase())).slice(0,100)
  return <>
    <div className="control-line">
    <div className="ops-inline"><button className="secondary compact" disabled={!step.node_id||!!busy} onClick={()=>void load('lines')}>查询接轨线路</button>
      <select aria-label="驶向下一端点的线路" value={step.line_id??''} onChange={e=>setLine(e.target.value)}>
        <option value="">驶向下一端点的线路</option>
        {step.line_id&&!lines.some(item=>item.id===step.line_id)&&<option value={step.line_id}>{step.line_id.slice(0,8)}</option>}
        {lines.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}
      </select></div>
    <details><summary>指定物理区段</summary><label>区段 ID<input value={step.via_edge??''} onChange={e=>setVia(e.target.value.trim())}/></label></details>
    </div><div className="control-next">
    <button className="secondary compact" disabled={!step.node_id||!step.line_id||!!busy} onClick={()=>void load('destinations')}>查询可达下一端点</button>
    {!!ends.length&&<><input aria-label="筛选可达端点" placeholder={`筛选 ${ends.length} 个可达端点`} value={query} onChange={e=>setQuery(e.target.value)}/>
      <select aria-label="可达的下一端点" value="" onChange={e=>{const found=ends.find(item=>item.id===e.target.value);if(found)choose(found)}}>
        <option value="">选择下一端点</option>{matches.map(item=><option key={item.id} value={item.id}>{item.name} · {((item.meters??0)/1000).toFixed(2)} km</option>)}
      </select></>}
    {busy&&<small role="status">查询显式轨道连接…</small>}
    {error&&<p className="error-text" role="alert">{error}</p>}
    </div>
  </>
}
