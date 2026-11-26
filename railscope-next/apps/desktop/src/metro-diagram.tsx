import { t } from './i18n'
import { useEffect, useMemo, useState } from 'react'
import { formatTime, type Run, type RunDetail } from './operations-api'

type Props={lineName?:string;detail?:RunDetail;axis?:RunDetail;runs:Run[];time:number;playing:boolean;
  select:(id:string)=>void;seek:(time:number)=>void;locate:(id:string)=>void}

export function MetroDiagram({lineName,detail,axis,runs,time,playing,select,seek,locate}:Props){
  const [scale,setScale]=useState<'station'|'distance'>('station')
  const [direction,setDirection]=useState<'all'|'out'|'back'>('all')
  const [span,setSpan]=useState(90)
  const [follow,setFollow]=useState(true)
  const base=detail?.service?.mode==='metro'?detail:undefined
  useEffect(()=>{
    const departure=base?.chart[0]?.departure_time_s??base?.chart[0]?.arrival_time_s
    const last=base?.chart.at(-1)
    const arrival=last?.arrival_time_s??last?.departure_time_s
    if(playing||departure==null||(departure<=time&&arrival!=null&&time<=arrival))return
    const start=Math.max(0,Math.floor((time-span*15)/300)*300)
    if(departure<start||departure>start+span*60)setFollow(false)
  },[base?.run.id])
  const chart=axis?.chart??base?.chart
  const rows=useMemo(()=>{
    const known=new Set<string>()
    return (chart??[]).filter(stop=>{
      if(known.has(stop.station_id))return false
      known.add(stop.station_id)
      return true
    })
  },[chart])
  const indices=useMemo(()=>new Map(rows.map((stop,index)=>[stop.station_id,index])),[rows])
  const plans=useMemo(()=>runs.flatMap(run=>{
    if(run.status==='cancelled')return []
    const stops=run.stops.flatMap(stop=>{
      const index=indices.get(stop.station_id)
      const arrive=stop.arrival_time_s??stop.departure_time_s
      return index==null||arrive==null?[]:[{index,arrive,depart:stop.departure_time_s??arrive}]
    })
    if(stops.length<2||stops.length!==run.stops.length)return []
    const reverse=stops[0].index>stops.at(-1)!.index
    if(stops.some((stop,i)=>i>0&&(reverse?stop.index>=stops[i-1].index:stop.index<=stops[i-1].index)))return []
    const startIndex=stops[0].index
    const endIndex=stops.at(-1)!.index
    const short=Math.min(startIndex,endIndex)!==0||Math.max(startIndex,endIndex)!==rows.length-1
    return [{run,stops,reverse,short}]
  }),[runs,indices,rows.length])
  if(rows.length<2)return <div className="metro-empty">{t('所选线路当日暂无可绘制的车次。')}</div>
  const anchor=base?.chart[0]?.departure_time_s??base?.chart[0]?.arrival_time_s??time
  const start=Math.max(0,Math.floor(((follow?time:anchor)-span*15)/300)*300)
  const end=start+span*60
  const firstDist=rows[0].distance_m??0
  const maxDist=(rows.at(-1)?.distance_m??0)-firstDist
  const byDistance=scale==='distance'&&maxDist>0&&rows.every(stop=>stop.distance_m!=null)
  const width=960
  const label=126
  const height=Math.max(220,rows.length*23+58)
  const x=(t:number)=>label+(t-start)/(end-start)*(width-label-22)
  const y=(i:number)=>32+(byDistance?(rows[i].distance_m!-firstDist)/maxDist:i/(rows.length-1))*(height-58)
  const shown=plans.filter(({stops,reverse})=>stops[0].arrive<=end&&stops.at(-1)!.depart>=start&&
    (direction==='all'||(direction==='back')===reverse)).sort((a,b)=>Number(b.run.id===base?.run.id)-Number(a.run.id===base?.run.id)).slice(0,240)
  const ticks=Array.from({length:1+span/15},(_,i)=>start+i*900)
  const firstStation=rows[0].station_name??t('起点')
  const lastStation=rows.at(-1)?.station_name??t('终点')
  const timeAt=(clientX:number,svg:SVGSVGElement)=>{
    const box=svg.getBoundingClientRect()
    const fraction=((clientX-box.left)/box.width*width-label)/(width-label-22)
    return fraction>=0&&fraction<=1?Math.round(start+fraction*(end-start)):undefined
  }
  return <section className="metro-diagram" aria-label={t("上海地铁交路运行图")}>
    <header className="metro-diagram-head">
      <div><strong>{lineName??t('地铁交路运行图')} · {firstStation} ↔ {lastStation}</strong>
        <small>{(base??axis)?.run.service_date} · {t('当前时窗 {0} 趟 / 全天 {1} 趟',shown.length,plans.length)}</small></div>
      <div className="metro-diagram-tools">
        <label>{t("方向")}<select value={direction} onChange={e=>setDirection(e.target.value as typeof direction)}><option value="all">{t("双向")}</option>
          <option value="out">{t('{0}方向',lastStation)}</option><option value="back">{t('{0}方向',firstStation)}</option></select></label>
        <label>{t("纵轴")}<select value={scale} onChange={e=>setScale(e.target.value as typeof scale)}><option value="station">{t("站序")}</option><option value="distance" disabled={!maxDist}>{t("里程")}</option></select></label>
        <label>{t("时窗")}<select value={span} onChange={e=>setSpan(Number(e.target.value))}><option value={45}>{t("45 分钟")}</option><option value={90}>{t("90 分钟")}</option><option value={180}>{t("3 小时")}</option></select></label>
        <label><input type="checkbox" checked={follow} onChange={event=>setFollow(event.target.checked)}/>{t('时窗跟随时钟')}</label>
      </div>
    </header>
    <div className="metro-keys"><span className="metro-out">{t('{0}方向',lastStation)}</span><span className="metro-return">{t('{0}方向',firstStation)}</span>
      <span className="metro-short">{t('区间车')}</span><span className="metro-now">{t("模拟时钟")}</span></div>
    <div className="metro-diagram-scroll">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={t("时间与站序运行图")}
        onClick={event=>{
          const point=timeAt(event.clientX,event.currentTarget)
          if(point!=null)seek(point)
        }}>
        <defs><clipPath id="metro-time-window"><rect x={label} y="26" width={width-label-22} height={height-46}/></clipPath></defs>
        {ticks.map(t=><g key={t}><line x1={x(t)} x2={x(t)} y1="26" y2={height-22} className="metro-grid"/>
          <text x={x(t)} y="16" textAnchor="middle">{formatTime(t).split(':').slice(0,2).join(':')}</text></g>)}
        {rows.map((row,i)=><g key={row.station_id}><line x1={label} x2={width-22} y1={y(i)} y2={y(i)} className="metro-grid"/>
          <text x={label-9} y={y(i)+4} textAnchor="end" className="metro-station-link" tabIndex={0} role="button"
            onClick={event=>{event.stopPropagation();locate(row.station_id)}}
            onKeyDown={event=>{if(event.key==='Enter')locate(row.station_id)}}>
            {row.station_name??t("第 {0} 站", i+1)}</text></g>)}
        <g clipPath="url(#metro-time-window)">
          {[...shown].reverse().map(({run,stops,reverse,short})=><polyline key={run.id} points={stops.flatMap(stop=>[
            `${x(stop.arrive)},${y(stop.index)}`,`${x(stop.depart)},${y(stop.index)}`,
          ]).join(' ')} className={`metro-plan ${reverse?'return':''} ${run.id===base?.run.id?'current':''} ${short?'short':''}`}
            tabIndex={0} role="button" aria-label={run.train_number}
            onClick={event=>{
              event.stopPropagation();select(run.id)
              const point=timeAt(event.clientX,event.currentTarget.ownerSVGElement!)??stops[0].depart
              seek(Math.max(stops[0].arrive,Math.min(point,stops.at(-1)!.depart)))
            }}
            onKeyDown={event=>{if(event.key==='Enter'){select(run.id);seek(stops[0].depart)}}}>
            <title>{run.train_number} · {rows[stops[0].index].station_name} {formatTime(stops[0].depart)} → {rows[stops.at(-1)!.index].station_name} {formatTime(stops.at(-1)!.arrive)}</title></polyline>)}
          {time>=start&&time<=end&&<line x1={x(time)} x2={x(time)} y1="26" y2={height-22} className="metro-time"/>}
        </g>
      </svg>
    </div>
  </section>
}
