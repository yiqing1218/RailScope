import { useState } from 'react'
import { Download, Upload } from 'lucide-react'
import { api, type Entity, type Override, type Pack } from './api'
import { statusName } from './catalog'
import { t } from './i18n'
import { chinaDate, describeError, opsApi, type ScheduleFormat } from './operations-api'
import stationTemplate from './import-templates/station.json?raw'
import nationalTemplate from './import-templates/national-line.json?raw'
import urbanTemplate from './import-templates/urban-line.json?raw'
import trackTemplate from './import-templates/track.json?raw'
import corridorTemplate from './import-templates/corridor.json?raw'
import planTemplate from './import-templates/plan.json?raw'
import csvTemplate from './import-templates/plan.csv?raw'
import stopMapTemplate from './import-templates/gtfs-stop-map.json?raw'
import manual from './import-templates/README.md?raw'

const templates = [
  ['station.json', '车站资料', stationTemplate],
  ['national-line.json', '国铁线路资料', nationalTemplate],
  ['urban-line.json', '城轨线路与分期开通', urbanTemplate],
  ['track.json', '物理轨道资料', trackTemplate],
  ['corridor.json', '连续通道 JSON', corridorTemplate],
  ['plan.json', '运行计划 JSON', planTemplate],
  ['plan.csv', '时刻表 CSV', csvTemplate],
  ['gtfs-stop-map.json', 'GTFS 站点映射 JSON', stopMapTemplate],
] as const

type ImportFormat = 'metadata' | ScheduleFormat
type ExportKind = 'station' | 'line' | 'rail' | 'metro' | 'corridors' | 'runs'

function download(filename:string, content:BlobPart, type='text/plain;charset=utf-8') {
  const url = URL.createObjectURL(new Blob([content], {type}))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 30000)
}

function templateContent(content:string, filename:string, pack:Pack|null, selected?:Entity) {
  if (!pack || filename.endsWith('.csv') || filename === 'gtfs-stop-map.json') return content
  const document = JSON.parse(content)
  document.snapshot_id = pack.snapshot
  const record = document.records?.[0]
  if (record && selected) {
    if (record.kind === selected.kind || filename === 'track.json' && selected.kind === 'metro') {
      record.entity_id = selected.id
      record.kind = selected.kind
    }
  }
  return JSON.stringify(document, null, 2) + '\n'
}

export function ExternalData({pack, selected, onMetadata, onPlan}:{
  pack:Pack|null; selected?:Entity; onMetadata:()=>void; onPlan:(date?:string)=>void
}) {
  const [tab, setTab] = useState<'export'|'import'>('export')
  const [format, setFormat] = useState<ImportFormat>('metadata')
  const [date, setDate] = useState(chinaDate)
  const [stopMap, setStopMap] = useState('')
  const [file, setFile] = useState<File>()
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [corridorIds, setCorridorIds] = useState<Record<string,{id:string;version:number}>>()

  function reset() {
    setMessage('')
    setError('')
    setCorridorIds(undefined)
  }

  async function load(preview:boolean) {
    if (!pack || !file || busy) return
    setBusy(true)
    reset()
    try {
      const limit = format === 'json' ? 32 : 16
      if (file.size > limit * 1024 * 1024) throw new Error(t("文件超出导入容量，请按日期或线路拆分。"))
      const content = format === 'gtfs' ? await encodeZip(file) : await file.text()
      const prefix = preview ? t("校验通过") : t("已写入工作区")
      if (format === 'metadata') {
        const result = await api.importMetadata(pack.snapshot, content, preview)
        setMessage(t("{0} · {1} 条资料，{2} 条变更", prefix, result.records, result.changed))
        if (!preview && result.changed > 0) onMetadata()
      } else {
        const mapping = format==='gtfs' && stopMap.trim() ? JSON.parse(stopMap) : undefined
        const result = await opsApi.importExternal(pack.snapshot, format, content, preview, format==='gtfs'?date:undefined, mapping)
        setMessage(result.duplicate ? t("相同的运行计划已导入。") : result.changed===0 ? t("没有需要更新的记录。") :
          t("{0} · {1} 条通道，{2} 个车次，{3} 条运行实例", prefix, result.corridors, result.services, result.runs))
        if (!preview) {
          setCorridorIds(result.corridor_ids)
          onPlan(result.first_date || undefined)
        }
      }
    } catch (cause) {
      setError(format==='metadata' ? String(cause) : describeError(cause))
    } finally {
      setBusy(false)
    }
  }

  return <div className="property-content task-summary data-import external-data">
    <p>{pack ? t("目标快照 · {0}", pack.snapshot) : t("先加载数据包，再导入资料。")}</p>
    <div className="tabs" role="group" aria-label={t("导入与导出")}>
      <button type="button" className={tab==='export'?'active':''} aria-pressed={tab==='export'} onClick={()=>setTab('export')}>{t("导出")}</button>
      <button type="button" className={tab==='import'?'active':''} aria-pressed={tab==='import'} onClick={()=>setTab('import')}>{t("导入")}</button>
    </div>
    <div hidden={tab!=='export'}><ExportData pack={pack} selected={selected}/></div>
    <div hidden={tab!=='import'} className="exchange-fields">
    <label>{t("文件格式")}<select value={format} disabled={busy} onChange={event=>{
      setFormat(event.target.value as ImportFormat)
      setFile(undefined)
      reset()
    }}>
      <option value="metadata">{t("对象资料 JSON")}</option>
      <option value="json">{t("通道与运行计划 JSON")}</option>
      <option value="csv">{t("时刻表 CSV")}</option>
      <option value="gtfs">GTFS ZIP</option>
    </select></label>
    {format==='gtfs'&&<>
      <label>{t("运营日期")}<input type="date" value={date} disabled={busy} onChange={event=>{setDate(event.target.value);reset()}}/></label>
      <label>{t("GTFS 站点映射")}<textarea value={stopMap} disabled={busy} rows={3} onChange={event=>{setStopMap(event.target.value);reset()}}/></label>
    </>}
    <label>{t("选择资料文件")}<input key={format} type="file" disabled={busy||!pack}
      accept={format==='gtfs'?'.zip':format==='csv'?'.csv':'.json'}
      onChange={event=>{setFile(event.target.files?.[0]);reset()}}/></label>
    <div className="ops-actions">
      <button className="secondary compact" disabled={busy||!pack||!file||(format==='gtfs'&&!date)} onClick={()=>void load(true)}>{t("只校验")}</button>
      <button className="primary compact" disabled={busy||!pack||!file||(format==='gtfs'&&!date)} onClick={()=>void load(false)}><Upload size={15}/>{t("校验并导入")}</button>
    </div>
    {busy&&<p role="status">{t("正在处理资料…")}</p>}
    {error&&<p className="error-text" role="alert">{error}</p>}
    {message&&<p role="status">{message}</p>}
    {corridorIds&&Object.keys(corridorIds).length>0&&<details>
      <summary>{t("已保存通道 ID")}</summary>
      <ul>{Object.entries(corridorIds).map(([key, {id,version}])=><li key={key}>{key} · <code>{id}</code> · v{version}</li>)}</ul>
    </details>}
    </div>
    <details open={!pack || undefined}>
      <summary>{t("模板与导入手册")}</summary>
      <p>{t("对象属性的「来源」页提供实体 ID。模板会填入当前快照和匹配的所选对象。")}</p>
      <div className="import-templates">
        {templates.map(([filename,label,content])=><button key={filename} className="secondary compact"
          onClick={()=>download(filename,templateContent(content,filename,pack,selected))}><Download size={14}/>{t(label)}</button>)}
        <button className="secondary compact" onClick={()=>download('RailScope-导入手册.md',manual)}><Download size={14}/>{t("导入手册")}</button>
      </div>
    </details>
  </div>
}

const exportKinds = [
  ['station','车站资料'],['line','线路与分期'],['rail','国铁轨道资料'],
  ['metro','城轨轨道资料'],['corridors','连续通道'],['runs','运行计划与时刻表'],
] as const

function ExportData({pack, selected}:{pack:Pack|null;selected?:Entity}) {
  const [kind, setKind] = useState<ExportKind>(
    exportKinds.find(([key])=>key===selected?.kind)?.[0]??'station',
  )
  const [query,setQuery] = useState('')
  const [serviceDate,setServiceDate] = useState('')
  const [format,setFormat] = useState<ScheduleFormat>('json')
  const [onlyLine,setOnlyLine] = useState(false)
  const [page,setPage] = useState<{next:string; batch:number}>()
  const [busy,setBusy] = useState(false)
  const [error,setError] = useState('')
  const [message,setMessage] = useState('')
  const operation = kind==='corridors'||kind==='runs'
  const selectedLine = (kind==='rail'||kind==='metro')&&selected?.kind==='line' ? selected : undefined

  function reset() {
    setPage(undefined)
    setError('')
    setMessage('')
  }

  async function save(next=false, id?:string) {
    if (!pack||busy) return
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const cursor = next ? page?.next : undefined
      const batch = next&&page ? page.batch+1 : 1
      const fileFormat = kind==='runs' ? format : 'json'
      const result = operation
        ? await opsApi.exportExternal(pack.snapshot,kind,fileFormat,serviceDate||undefined,cursor)
        : await api.exportMetadata(pack.snapshot,{
            kind,query:id??query.trim(),cursor,parent:!id&&onlyLine?selectedLine?.id:undefined,
          })
      const filename = `railscope-${kind}-${pack.snapshot}-${batch}.${fileFormat==='gtfs'?'zip':fileFormat}`
      if (fileFormat==='gtfs') {
        download(filename, Uint8Array.from(atob(result.content),c=>c.charCodeAt(0)), 'application/zip')
      } else {
        download(filename,result.content)
      }
      setPage(result.next?{next:result.next,batch}:undefined)
      setMessage(t("已导出 {0} 条。",result.count))
    } catch(cause) {
      setError(operation?describeError(cause):String(cause))
    } finally {
      setBusy(false)
    }
  }

  async function exportTrackIndex() {
    if (!pack||selected?.kind!=='line') return
    setError('')
    try {
      const tracks = await api.lineTracks(pack.snapshot, selected.id)
      download(`line-tracks-${selected.id}.json`, JSON.stringify({
        snapshot_id:pack.snapshot, line_id:selected.id, tracks,
      }, null, 2) + '\n')
    } catch(cause) {
      setError(String(cause))
    }
  }

  return <div className="exchange-fields">
    <label>{t("导出内容")}<select value={kind} disabled={busy} onChange={event=>{setKind(event.target.value as ExportKind);reset()}}>
      {exportKinds.map(([key,label])=><option key={key} value={key}>{t(label)}</option>)}
    </select></label>
    {!operation&&<label>{t("名称或实体 ID")}<input value={query} maxLength={128} disabled={busy}
      onChange={event=>{setQuery(event.target.value);reset()}}/></label>}
    {selectedLine&&<label className="exchange-checkbox"><input type="checkbox" checked={onlyLine} disabled={busy}
      onChange={event=>{setOnlyLine(event.target.checked);reset()}}/>{t("仅导出所选线路的轨道")}</label>}
    {kind==='runs'&&<>
      <label>{t("运营日期（可选）")}<input type="date" value={serviceDate} disabled={busy}
        onChange={event=>{setServiceDate(event.target.value);reset()}}/></label>
      <label>{t("文件格式")}<select value={format} disabled={busy} onChange={event=>{setFormat(event.target.value as ScheduleFormat);reset()}}>
        <option value="json">{t("可回填的 RailScope JSON")}</option><option value="csv">CSV</option><option value="gtfs">GTFS ZIP</option>
      </select></label>
    </>}
    <p className="muted">{!operation?t("在 JSON 的 changes 中填空项或修改已有值，保存后到「导入」页回填。"):
      kind==='runs'&&format!=='json'?t("CSV 和 GTFS 可与其他工具交换时刻。回填现有版本请选择 RailScope JSON。"):
      t("JSON 保留稳定 ID 和版本，可编辑后导回同一工作区。")}</p>
    <div className="ops-actions">
      <button className="primary compact" disabled={busy||!pack} onClick={()=>void save()}><Download size={15}/>{t("导出资料")}</button>
      {page&&<button className="secondary compact" disabled={busy} onClick={()=>void save(true)}>{t("导出下一批")}</button>}
      {pack&&selected?.kind===kind&&<button className="secondary compact" disabled={busy} onClick={()=>void save(false,selected.id)}>{t("导出所选对象")}</button>}
      {pack&&selected?.kind==='line'&&<button className="secondary compact" disabled={busy} onClick={()=>void exportTrackIndex()}>{t("导出所选线路轨道索引")}</button>}
    </div>
    {busy&&<p role="status">{t("正在导出资料…")}</p>}
    {error&&<p className="error-text" role="alert">{error}</p>}
    {message&&<p role="status">{message}</p>}
  </div>
}

async function encodeZip(file:File) {
  const bytes = new Uint8Array(await file.arrayBuffer())
  let binary = ''
  for (let offset=0;offset<bytes.length;offset+=8192) binary += String.fromCharCode(...bytes.subarray(offset,offset+8192))
  return btoa(binary)
}

const profileLabels:Record<string,string> = {
  former_names:'曾用名',other_names:'其他用名',business:'运输业务',technical:'技术类型',station_grade:'车站等级',
  rail_type:'铁路类型',railway_grade:'客货共线铁路等级',urban_system:'城轨制式',city:'城市',opened_on:'开通日期',
  speed_kmh:'速度',gauge_mm:'轨距',electrification:'供电',track_type:'轨道用途',sections:'分期开通区段',extra:'自定义属性',
  name:'工作区名称',note:'备注',category:'目录分类',group:'工作区分组',designation:'编号',
  operator:'运营管理',associated_lines:'车站关联线路',
}

const profileValues:Record<string,string> = {
  special:'特等',high_speed:'高速铁路',intercity:'城际铁路',mixed_traffic:'客货共线铁路',heavy_haul:'重载铁路',suburban:'市域（郊）铁路',
  metro:'地铁',light_rail:'轻轨',suburban_rapid:'市域快轨',maglev:'磁浮',straddle_monorail:'跨座式单轨',suspended_monorail:'悬挂式单轨',
  automated_guideway:'自动导向轨道',tram:'有轨电车',guided_rubber_tire:'导轨式胶轮',electronic_guided_rubber_tire:'电子导向胶轮',
  main:'正线',arrival_departure:'到发线',siding:'侧线',yard:'站场股道',depot:'车辆基地轨道',crossover:'渡线',other:'其他',
  user_asserted:'用户录入',verified:'已核验',automatic_reference:'自动参考',
}

export function ProfileDetails({value, openEdge}:{value:Override;openEdge:(id:string)=>void}) {
  const profile = value.profile
  const sources = Object.entries(value.import_sources??{})
  if (!profile && !sources.length) return null
  return <section className="site-coverage import-profile">
    <h4>{t("外部资料")}</h4>
    {profile&&<>
      <dl>
        {profile.former_names.length>0&&<><dt>{t("曾用名")}</dt><dd>{profile.former_names.join('、')}</dd></>}
        {profile.other_names.length>0&&<><dt>{t("其他用名")}</dt><dd>{profile.other_names.join('、')}</dd></>}
        {(['station_grade','rail_type','railway_grade','urban_system','city','opened_on','track_type'] as const).map(field=>{
          const val = profile[field]
          return val ? <div key={field}><dt>{t(profileLabels[field])}</dt><dd>{t(profileValues[val]??val)}</dd></div> : null
        })}
        {Object.entries(profile.extra).map(([name,val])=><div key={name}><dt>{name}</dt><dd>{val}</dd></div>)}
      </dl>
      {profile.sections.length>0&&<details><summary>{t("分期开通区段")}</summary><ul>
        {profile.sections.map(section=><li key={section.code}><strong>{section.name} · {section.code}</strong>
          <span>{statusName(section.status)}{section.opened_on&&' · '+section.opened_on}</span>
          <button className="secondary compact" onClick={()=>openEdge(section.edge_ids[0])}>{t("查看首段轨道")}</button></li>)}
      </ul></details>}
    </>}
    {sources.length>0&&<details><summary>{t("字段来源")}</summary><ul>{sources.map(([field,source])=>
      <li key={field}><strong>{t(profileLabels[field.replace(/^profile\./,'')]??field)}</strong>
        <span>{source.reference} · {source.version} · {t(profileValues[source.verification_status]??source.verification_status)}
          {source.confidence!=null&&' · '+source.confidence}</span></li>)}</ul></details>}
  </section>
}
