import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { createPortal } from 'react-dom'
import { Maximize2, Minus, Plus, X } from 'lucide-react'
import { t } from './i18n'
import { formatTime, type Run, type RunDetail } from './operations-api'
import { fitTimeRange, metroPresets, metroRuns, metroStations, midnight, operatingRange, timeTicks,
  type EvidenceKind, type MetroTrace, type TimeRange } from './metro-timeline'

type Props={
  lineName?:string; line7:boolean; detail?:RunDetail; axis?:RunDetail; runs:Run[]; time:number;
  date:string; dates:string[]; onDate:(date:string)=>void; select:(id:string)=>void;
  seek:(time:number)=>void; locate:(id:string)=>void; openTimetable:()=>void;
}
type Preset='all'|keyof typeof metroPresets
type Hover={trace:MetroTrace; stop:MetroTrace['points'][number]; x:number; y:number}
type Pan={startX:number; range:TimeRange; runId?:string; moved:boolean}

const periods={all:'全天',first:'首班',morning:'早高峰',midday:'平峰',evening:'晚高峰',last:'收车'} as const
const evidenceLabels={boundary:'公开首末班',map:'地图时刻参考',field:'现场站台屏'} as const
const routeColors=['#bb4526','#087b79','#653fa0','#2362ad','#955016','#43596a']
const keyStations7=new Set(['美兰湖','祁华路','常熟路','杨高南路','花木路'])
const width=1060
const left=107
const right=width-27
const top=35
const shortTime=(seconds:number)=>formatTime(seconds).slice(0,-3)
const overlaps=(range:TimeRange,other:TimeRange)=>other[0]<range[1]&&other[1]>range[0]

function evidenceMark(kind:EvidenceKind,x:number,y:number){
  if(kind==='field')return <rect x={x-2.6} y={y-2.6} width={5.2} height={5.2}/>
  if(kind==='boundary')return <path d={'M'+x+' '+(y-3.5)+' l3.5 3.5 -3.5 3.5 -3.5 -3.5z'}/>
  return <circle cx={x} cy={y} r={2.25}/>
}

export function MetroDiagram({lineName,line7,detail,axis,runs,time,date,dates,onDate,select,seek,locate,openTimetable}:Props){
  const [preset,setPreset]=useState<Preset>('all')
  const [custom,setCustom]=useState<TimeRange>()
  const [follow,setFollow]=useState(false)
  const [byDistance,setByDistance]=useState(false)
  const [direction,setDirection]=useState('all')
  const [hiddenRoutes,setHiddenRoutes]=useState<Set<string>>(()=>new Set())
  const [overnight,setOvernight]=useState(false)
  const [evidence,setEvidence]=useState(true)
  const [hover,setHover]=useState<Hover>()
  const [expanded,setExpanded]=useState(false)
  const clipId=useId()
  const dialog=useRef<HTMLDialogElement>(null)
  const svg=useRef<SVGSVGElement>(null)
  const pan=useRef<Pan|undefined>(undefined)
  const plot=useRef<HTMLDivElement>(null)
  const base=detail?.service?.mode==='metro'?detail:undefined
  const chart=axis?.chart??base?.chart
  const rows=useMemo(()=>metroStations(chart??[],line7),[chart,line7])
  const traces=useMemo(()=>metroRuns(runs,rows),[runs,rows])
  const limits=useMemo(()=>operatingRange(traces),[traces])
  const routeGroups=useMemo(()=>{
    const groups=new Map<string,{route:string; label:string; title:string; total:number; order:number}>()
    for(const trace of traces){
      const existing=groups.get(trace.route)
      if(existing){existing.total++;continue}
      const from=rows[trace.low].station_name??t('起点')
      const to=rows[trace.high].station_name??t('终点')
      const title=from+' ↔ '+to
      const shortWorking=line7&&from==='祁华路'&&to==='杨高南路'
      const qihua=line7&&from==='祁华路'&&to==='花木路'
      const label=trace.short?(shortWorking?t('杨高南路小交路'):qihua?t('祁华路区间'):title):t('大交路')
      groups.set(trace.route,{route:trace.route,label,title,total:1,order:!trace.short?0:qihua?1:shortWorking?2:3})
    }
    const ordered=[...groups.values()].sort((a,b)=>a.order-b.order||b.total-a.total||a.title.localeCompare(b.title))
    return ordered.map((group,i)=>({...group,color:routeColors[line7&&group.order<3?group.order:i%routeColors.length]}))
  },[line7,rows,traces])
  const colors=useMemo(()=>new Map(routeGroups.map(group=>[group.route,group.color])),[routeGroups])
  const matching=useMemo(()=>traces.filter(trace=>!hiddenRoutes.has(trace.route)&&
    (!overnight||trace.last.departure>=midnight)&&(direction==='all'||(direction==='back')===trace.reverse)),[traces,hiddenRoutes,overnight,direction])
  const selected=base?.run.id
  const selectedTrace=traces.find(trace=>trace.run.id===selected)
  const lastPreset=metroPresets[preset==='all'?'first':preset]
  const fixed:TimeRange=custom?fitTimeRange(custom[0],custom[1]-custom[0],limits):
    preset==='all'||!overlaps(limits,lastPreset)?limits:[Math.max(limits[0],lastPreset[0]),Math.min(limits[1],lastPreset[1])]
  const followSpan=fixed[1]-fixed[0]
  const [start,end]=follow?fitTimeRange(time-followSpan*.26,followSpan,limits):fixed
  const [limitStart,limitEnd]=limits
  const span=end-start
  const firstDistance=rows[0]?.distance_m??0
  const lastDistance=rows.at(-1)?.distance_m
  const totalDistance=lastDistance==null?0:Math.abs(lastDistance-firstDistance)
  const canUseDistance=totalDistance>0&&rows.every(row=>row.distance_m!=null)
  const height=Math.max(415,rows.length*17+60)
  const bottom=height-35
  const x=(seconds:number)=>left+(seconds-start)/span*(right-left)
  const y=(index:number)=>top+(byDistance&&canUseDistance?Math.abs(rows[index].distance_m!-firstDistance)/totalDistance:
    index/Math.max(1,rows.length-1))*(bottom-top)
  const shown=useMemo(()=>matching.filter(trace=>trace.first.arrival<=end&&trace.last.departure>=start)
    .sort((a,b)=>Number(a.run.id===selected)-Number(b.run.id===selected)),[matching,start,end,selected])
  const seenKinds=useMemo(()=>new Set(traces.flatMap(trace=>trace.points.flatMap(point=>point.kind?[point.kind]:[]))),[traces])
  const {step,ticks}=timeTicks([start,end])
  const firstStation=rows[0]?.station_name??t('起点')
  const lastStation=rows.at(-1)?.station_name??t('终点')
  const ratioAt=(clientX:number,element:SVGSVGElement)=>{
    const area=element.getBoundingClientRect()
    return ((clientX-area.left)/area.width*width-left)/(right-left)
  }
  function manual(leftTime:number,duration:number){
    setCustom(fitTimeRange(leftTime,duration,limits));setFollow(false);setHover(undefined)
  }
  function zoom(factor:number){
    const duration=span*factor
    manual(start+(span-duration)*.5,duration)
  }
  useEffect(()=>{
    const element=svg.current
    if(!element)return
    const onWheel=(event:WheelEvent)=>{
      if(!event.ctrlKey&&!event.metaKey)return
      const fraction=ratioAt(event.clientX,element)
      if(fraction<0||fraction>1)return
      event.preventDefault()
      const activeSpan=end-start
      const duration=Math.min(limitEnd-limitStart,Math.max(1200,activeSpan*Math.exp(event.deltaY*.0015)))
      setCustom(fitTimeRange(start+fraction*(activeSpan-duration),duration,[limitStart,limitEnd]))
      setFollow(false);setHover(undefined)
    }
    element.addEventListener('wheel',onWheel,{passive:false})
    return ()=>element.removeEventListener('wheel',onWheel)
  },[start,end,limitStart,limitEnd,rows.length,traces.length,expanded])
  useEffect(()=>{
    const element=dialog.current
    if(!expanded||!element)return
    element.showModal()
    return ()=>element.close()
  },[expanded])
  const plotted=useMemo(()=>shown.map(trace=>({trace,coords:trace.points.flatMap(stop=>[
    x(stop.arrival)+','+y(stop.index),x(stop.departure)+','+y(stop.index),
  ]).join(' ')})),[shown,start,end,rows,byDistance,canUseDistance,totalDistance,height])
  function switchPeriod(value:Preset){
    setPreset(value);setCustom(undefined);setFollow(false);setHover(undefined)
  }
  function keyboard(event:KeyboardEvent<SVGSVGElement>){
    if(event.target!==event.currentTarget)return
    switch(event.key){
      case 'ArrowLeft':manual(start-span*.2,span);break
      case 'ArrowRight':manual(start+span*.2,span);break
      case '+':case '=':zoom(.65);break
      case '-':zoom(1.5);break
      case 'Home':switchPeriod('all');break
      default:return
    }
    event.preventDefault()
  }
  function handleHover(event:PointerEvent<SVGSVGElement>){
    const viewport=plot.current?.getBoundingClientRect()
    if(!viewport||event.pointerType==='touch')return
    const target=(event.target as Element).closest<SVGElement>('[data-run]')
    const trace=shown.find(item=>item.run.id===target?.dataset.run)
    if(!trace){setHover(undefined);return}
    const area=event.currentTarget.getBoundingClientRect()
    const graphY=(event.clientY-area.top)/area.height*height
    const stop=trace.points.reduce((closest,point)=>Math.abs(y(point.index)-graphY)<Math.abs(y(closest.index)-graphY)?point:closest)
    setHover({trace,stop,x:Math.min(event.clientX-viewport.left+12,Math.max(0,viewport.width-244)),
      y:Math.max(0,Math.min(viewport.height-100,event.clientY-viewport.top+14))})
  }
  function selectTrace(trace:MetroTrace,seconds:number){
    select(trace.run.id)
    seek(Math.max(trace.first.arrival,Math.min(seconds,trace.last.departure)))
  }
  const startPan=(event:PointerEvent<SVGSVGElement>)=>{
    const fraction=ratioAt(event.clientX,event.currentTarget)
    if(event.button!==0||fraction<0||fraction>1)return
    const target=(event.target as Element).closest<SVGElement>('[data-run]')
    pan.current={startX:event.clientX,range:[start,end],runId:target?.dataset.run,moved:false}
    event.currentTarget.setPointerCapture(event.pointerId)
  }
  const movePan=(event:PointerEvent<SVGSVGElement>)=>{
    const input=pan.current
    if(!input){handleHover(event);return}
    if(Math.abs(event.clientX-input.startX)>4)input.moved=true
    if(!input.moved)return
    const duration=input.range[1]-input.range[0]
    const graphWidth=event.currentTarget.getBoundingClientRect().width*(right-left)/width
    manual(input.range[0]-(event.clientX-input.startX)*duration/graphWidth,duration)
  }
  const finishPan=(event:PointerEvent<SVGSVGElement>)=>{
    const input=pan.current
    pan.current=undefined
    if(!input||input.moved)return
    const fraction=ratioAt(event.clientX,event.currentTarget)
    if(fraction<0||fraction>1)return
    const seconds=Math.round(start+fraction*span)
    const trace=shown.find(item=>item.run.id===input.runId)
    if(trace)selectTrace(trace,seconds)
    else seek(seconds)
  }
  const firstBand=rows.findIndex(row=>row.station_name==='祁华路')
  const lastBand=rows.findIndex(row=>row.station_name==='杨高南路')
  const routeText=(trace:MetroTrace)=>(rows[trace.first.index].station_name??t('起点'))+' → '+(rows[trace.last.index].station_name??t('终点'))
  const outboundCount=matching.filter(trace=>!trace.reverse).length
  const content=<section className="metro-diagram" aria-label={t('上海地铁交路运行图')}>
    <header className="metro-diagram-head">
      <div><strong>{lineName??t('地铁交路运行图')} <span>{rows.length>1&&firstStation+' ↔ '+lastStation}</span></strong>
        <small>{t('全天 {0} 趟 · 当前视窗 {1} 趟',traces.length,shown.length)}
          {matching.length!==traces.length&&<> · {t('筛选后 {0} 趟',matching.length)}</>}</small>
      </div>
      <div className="metro-header-actions">
        <label className="metro-date">{t('运营日期')}<select aria-label={t('运行图日期')} value={date} onChange={event=>onDate(event.target.value)}>
          {(dates.includes(date)?dates:[date,...dates]).map(day=><option value={day} key={day}>{day} · {t(['周日','周一','周二','周三','周四','周五','周六'][new Date(day+'T00:00:00Z').getUTCDay()]??'')}</option>)}
        </select></label>
        <button type="button" className="secondary compact" onClick={()=>{setHover(undefined);setExpanded(!expanded)}}>
          {expanded?<X size={14}/>:<Maximize2 size={14}/>} {t(expanded?'关闭大图':'展开大图')}
        </button>
      </div>
    </header>
    {rows.length<2||!traces.length?<div className="metro-empty">{t('所选线路当日暂无可绘制的车次。')}</div>:<>
      <div className="metro-toolbar">
        <div className="metro-presets" role="group" aria-label={t('时段')}>
          {(Object.keys(periods) as Preset[]).map(value=><button key={value} type="button" className="secondary compact"
            disabled={value!=='all'&&!overlaps(limits,metroPresets[value])}
            aria-pressed={!custom&&!follow&&preset===value} onClick={()=>switchPeriod(value)}>{t(periods[value])}</button>)}
        </div>
        <div className="metro-diagram-tools">
          <label>{t('方向')}<select aria-label={t('运行图方向')} value={direction} onChange={event=>{setDirection(event.target.value);setHover(undefined)}}>
            <option value="all">{t('双向')}</option><option value="out">{t('{0}方向',lastStation)}</option><option value="back">{t('{0}方向',firstStation)}</option>
          </select></label>
          {canUseDistance&&<label>{t('纵轴')}<select value={byDistance?'distance':'station'} onChange={event=>setByDistance(event.target.value==='distance')}>
            <option value="station">{t('站序')}</option><option value="distance">{t('里程')}</option>
          </select></label>}
          <label><input type="checkbox" checked={follow} onChange={event=>{
            if(event.target.checked){
              if(followSpan>5400)setCustom(fitTimeRange(time-1350,5400,limits))
              setFollow(true);setHover(undefined)
            }else{setCustom([start,end]);setFollow(false)}
          }}/>{t('时窗跟随时钟')}</label>
        </div>
      </div>
      <div className="metro-route-filters" role="group" aria-label={t('交路筛选')}>
        {routeGroups.map(group=><label key={group.route} title={group.title}>
          <input type="checkbox" checked={!hiddenRoutes.has(group.route)} onChange={event=>{
            const next=new Set(hiddenRoutes)
            if(event.target.checked)next.delete(group.route)
            else next.add(group.route)
            setHiddenRoutes(next);setHover(undefined)
          }}/>
          <i style={{background:group.color}}/>{group.label} <small>{group.total}</small>
        </label>)}
        {traces.some(trace=>trace.last.departure>=midnight)&&<label><input type="checkbox" checked={overnight}
          onChange={event=>{setOvernight(event.target.checked);setHover(undefined)}}/>{t('仅跨午夜')}</label>}
      </div>
      {!!seenKinds.size&&<div className="metro-evidence-key">
        <label title={t('标记位置是计划时刻，形状表示来源。')}><input type="checkbox" checked={evidence} onChange={event=>setEvidence(event.target.checked)}/>{t('时刻依据')}</label>
        {evidence&&(Object.keys(evidenceLabels) as EvidenceKind[]).filter(kind=>seenKinds.has(kind)).map(kind=><span key={kind}>
          <svg viewBox="0 0 12 12" className={'metro-evidence '+kind} aria-hidden="true">{evidenceMark(kind,6,6)}</svg>{t(evidenceLabels[kind])}
        </span>)}
      </div>}
      <div className="metro-plot" ref={plot}>
        <div className="metro-plot-bar">
          <div className="metro-keys">
            <span className="metro-out">{t('{0}方向',lastStation)} {outboundCount}</span>
            <span className="metro-return">{t('{0}方向',firstStation)} {matching.length-outboundCount}</span>
            <span className="metro-now">{t('模拟时钟')}</span>
          </div>
          <div className="metro-zoom">
            <output aria-label={t('当前图幅')}>{shortTime(start)} – {shortTime(end)}</output>
            <button type="button" className="icon framed" aria-label={t('缩小时间轴')} onClick={()=>zoom(1.5)} disabled={span>=limitEnd-limitStart}><Minus size={13}/></button>
            <button type="button" className="icon framed" aria-label={t('放大时间轴')} onClick={()=>zoom(.65)} disabled={span<=1200}><Plus size={13}/></button>
          </div>
        </div>
        <div className="metro-diagram-scroll">
          <svg ref={svg} viewBox={'0 0 '+width+' '+height} data-overview={span>8*3600} tabIndex={0} role="group" aria-label={t('时间与站序运行图')}
            onPointerDown={startPan} onPointerMove={movePan} onPointerUp={finishPan}
            onPointerCancel={()=>{pan.current=undefined;setHover(undefined)}} onPointerLeave={()=>setHover(undefined)} onKeyDown={keyboard}>
            <defs><clipPath id={clipId}><rect x={left} y={top-3} width={right-left} height={bottom-top+6}/></clipPath></defs>
            <rect x={left} y={top} width={right-left} height={bottom-top} className="metro-paper"/>
            {line7&&firstBand>0&&lastBand>=0&&lastBand<rows.length-1&&<>
              <rect x={left} y={top} width={right-left} height={y(firstBand)-top} className="metro-band"/>
              <rect x={left} y={y(lastBand)} width={right-left} height={bottom-y(lastBand)} className="metro-band"/>
            </>}
            {ticks.map(value=><g key={value}>
              <line x1={x(value)} x2={x(value)} y1={top} y2={bottom} className={value===midnight?'metro-midnight':'metro-grid metro-hour'}/>
              <text x={x(value)} y={top-11} textAnchor="middle">{shortTime(value)}</text>
              <text x={x(value)} y={bottom+19} textAnchor="middle">{shortTime(value)}</text>
            </g>)}
            {step>=900&&ticks.filter(value=>value!==midnight||midnight%step===0).flatMap(value=>[.25,.5,.75]
              .map(fraction=>value+step*fraction).filter(value=>value<end&&value!==midnight)
              .map(value=><line key={value} x1={x(value)} x2={x(value)} y1={top} y2={bottom} className="metro-grid metro-minor"/>))}
            {rows.map((row,index)=>{
              const key=index===0||index===rows.length-1||line7&&keyStations7.has(row.station_name??'')
              return <g key={row.station_id} className={key?'metro-key-station':undefined}>
                <line x1={left} x2={right} y1={y(index)} y2={y(index)} className="metro-grid"/>
                <text x={left-9} y={y(index)+4} textAnchor="end" className="metro-station-link" tabIndex={0} role="button"
                  onClick={()=>locate(row.station_id)} onKeyDown={event=>{if(event.key==='Enter')locate(row.station_id)}}>
                  {row.station_name??t('第 {0} 站',index+1)}</text>
              </g>
            })}
            <g clipPath={'url(#'+clipId+')'} className={hover?'metro-traces has-hover':'metro-traces'}>
              {plotted.map(({trace,coords})=>{
                const focus=trace.run.id===selected||trace.run.id===hover?.trace.run.id
                const style={color:colors.get(trace.route)??routeColors[0]}
                return <g key={trace.run.id} className={focus?'metro-trace highlighted':'metro-trace'} style={style}>
                  <polyline points={coords} className={['metro-plan',trace.reverse&&'return',trace.short&&'short',trace.run.id===selected&&'current'].filter(Boolean).join(' ')}/>
                  <polyline points={coords} className="metro-hit" data-run={trace.run.id} role="button"
                    tabIndex={trace.run.id===selected?0:-1} aria-label={trace.run.train_number+' · '+routeText(trace)}
                    onKeyDown={event=>{if(event.key==='Enter')selectTrace(trace,trace.first.departure)}}/>
                </g>
              })}
              {evidence&&shown.flatMap(trace=>trace.points.flatMap((stop,index)=>{
                if(!stop.kind||stop.arrival<start||stop.arrival>end)return []
                return <g key={trace.run.id+':'+index} className={'metro-evidence '+stop.kind} data-evidence={stop.kind}
                  opacity={!hover||hover.trace.run.id===trace.run.id?1:.3}>
                  {evidenceMark(stop.kind,x(stop.arrival),y(stop.index))}
                </g>
              }))}
              {time>=start&&time<=end&&<line x1={x(time)} x2={x(time)} y1={top} y2={bottom} className="metro-time"/>}
            </g>
          </svg>
          {!shown.length&&<p className="metro-empty">{t('此时段暂无符合筛选条件的车次。')}</p>}
        </div>
        {hover&&<div className="metro-tooltip" role="status" style={{left:hover.x,top:hover.y}}>
          <strong>{hover.trace.run.train_number} · {routeText(hover.trace)}</strong>
          <span>{rows[hover.stop.index].station_name} · {formatTime(hover.stop.arrival)}{hover.stop.arrival!==hover.stop.departure&&' / '+formatTime(hover.stop.departure)}</span>
          {hover.stop.evidence&&<small>{hover.stop.evidence}</small>}
        </div>}
      </div>
      <footer className="metro-plot-footer">
        <small>{t('拖动平移 · Ctrl 滚轮缩放 · 24:00 后沿用当日运营日期')}</small>
      </footer>
      {selectedTrace&&<div className="metro-selection">
        <strong>{selectedTrace.run.train_number}</strong><span>{routeText(selectedTrace)} · {shortTime(selectedTrace.first.departure)} – {shortTime(selectedTrace.last.arrival)}</span>
        <button type="button" className="secondary compact" onClick={()=>{setExpanded(false);openTimetable()}}>{t('查看逐站时刻')}</button>
      </div>}
    </>}
  </section>
  return expanded?createPortal(<dialog ref={dialog} className="metro-dialog" aria-label={t('上海地铁交路运行图')}
    onCancel={event=>{event.preventDefault();setExpanded(false)}}>{content}</dialog>,document.body):content
}
