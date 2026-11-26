import { Database, Layers, Map, PanelLeft, RotateCcw, Search, SlidersHorizontal, TrainFront, X } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { Pack } from './api'
import { defaultTrackViews, thematic, type TrackViews } from './cartography'
import { defaultLayers, layerGroups, LayerTree, type LayerGroup, type Visibility } from './layers'
import type { MapTheme } from './map'
import type { Level } from './operations-api'

export const themes:Record<MapTheme,string>={light:'专业浅色',color:'常规彩色',dark:'深色',transport:'纯交通'}
export const simulationLevels:Record<Level,string>={l1:'L1 计划',l2:'L2 径路',l3:'L3 场景'}
type Mode='detail'|'operations'|'analysis'|'data'
const layoutNames:Record<Mode,string>={detail:'地图',operations:'运行',analysis:'分析与诊断',data:'数据'}
type Panel='hideLeft'|'hideRight'|'hideBottom'
const panels:Record<Panel,string>={hideLeft:'任务面板',hideRight:'对象面板',hideBottom:'底部工作区'}
export type SettingsSection='map'|'tracks'|'layers'|'layout'|'simulation'|'data'
type SettingsProps={
  open:boolean; section:SettingsSection; onSection:(section:SettingsSection)=>void; onClose:()=>void
  theme:MapTheme; setTheme:(theme:MapTheme)=>void; overview:boolean; setOverview:(overview:boolean)=>void
  trackViews:TrackViews; setTrackViews:(views:TrackViews)=>void
  visibility:Visibility; onLayer:(key:LayerGroup,value:boolean)=>void; onPreset:(layers:Partial<Visibility>)=>void
  mode:Mode; layouts:Record<Mode,Record<Panel,boolean>>
  onPanel:(mode:Mode,panel:Panel,visible:boolean)=>void; resetLayout:(mode:Mode)=>void
  level:Level; setLevel:(level:Level)=>void; pack:Pack|null; onData:()=>void
}

function Setting({title,description,children}:{title:string;description?:string;children:ReactNode}){
  return <label className="setting-row">
    <span><strong>{title}</strong>{description&&<small>{description}</small>}</span>
    {children}
  </label>
}

export function Settings(props:SettingsProps){
  const {open,section,onSection,onClose,theme,setTheme,overview,setOverview,trackViews,setTrackViews,
    visibility,onLayer,onPreset,layouts,onPanel,resetLayout,level,setLevel,pack,onData}=props
  const [query,setQuery]=useState('')
  const [layoutMode,setLayoutMode]=useState<Mode>(props.mode)
  const dialog=useRef<HTMLDialogElement>(null)
  const search=useRef<HTMLInputElement>(null)
  const body=useRef<HTMLDivElement>(null)
  useEffect(()=>{
    const element=dialog.current
    if(open&&!element?.open){
      setLayoutMode(props.mode)
      setQuery('')
      element?.showModal()
      search.current?.focus()
    }else if(!open&&element?.open)element.close()
  },[open,props.mode])
  const sections=[
    {id:'map',title:'地图与底图',icon:Map,description:'设置底图外观和离线地图的显示精度。',
      keywords:'底图主题 专业浅色 常规彩色 深色 纯交通 地图精度 离线源精度 概览 水域',
      reset:()=>{setTheme('light');setOverview(false)},content:<>
        <Setting title="底图主题" description="浅色、彩色和深色保留地理参考。纯交通只显示交通对象。">
          <select aria-label="底图主题" value={theme} onChange={event=>setTheme(event.target.value as MapTheme)}>
            {Object.entries(themes).map(([id,name])=><option key={id} value={id}>{name}</option>)}
          </select>
        </Setting>
        <Setting title="地图精度" description="离线源精度随缩放从概览切换到详细原始对象，概览适合大范围浏览。">
          <select aria-label="地图精度" value={overview?'overview':'source'} onChange={event=>setOverview(event.target.value==='overview')}>
            <option value="source">离线源精度</option><option value="overview">仅概览</option>
          </select>
        </Setting>
      </>},
    {id:'tracks',title:'轨道配色',icon:TrainFront,description:'国铁和城市轨道的配色可独立选择。',
      keywords:'国铁配色 地铁配色 城轨配色 线路颜色 分级 高铁 运营线路颜色 最高速度 轨距 供电 生命周期 车站技术',
      reset:()=>setTrackViews(defaultTrackViews),content:<>
        <Setting title="国铁配色" description="按高速铁路、干线、支线分级，或选择专题属性。">
          <select aria-label="国铁配色" value={trackViews.rail} onChange={event=>setTrackViews({...trackViews,rail:event.target.value as TrackViews['rail']})}>
            <option value="infrastructure">按铁路等级</option>
            <optgroup label="专题">{Object.entries(thematic).map(([id,item])=><option key={id} value={id}>{item.name}</option>)}</optgroup>
          </select>
        </Setting>
        <Setting title="城市轨道配色" description="有运营线路色时按线路显示，共线区段并列绘制。">
          <select aria-label="城市轨道配色" value={trackViews.metro} onChange={event=>setTrackViews({...trackViews,metro:event.target.value as TrackViews['metro']})}>
            <option value="lines">按运营线路</option><option value="infrastructure">按轨道类型</option>
          </select>
        </Setting>
      </>},
    {id:'layers',title:'图层与标注',icon:Layers,description:'选择地图上显示的线路、站点、名称和运行叠加。',
      keywords:`地图预设 ${layerGroups.flatMap(group=>[group.name,...group.items.map(([,label])=>label)]).join(' ')}`,
      reset:()=>onPreset(defaultLayers),content:<LayerTree visibility={visibility} onLayer={onLayer} onPreset={onPreset}/>},
    {id:'layout',title:'工作台布局',icon:PanelLeft,description:'地图、运行、分析与数据工作台各自保存面板的尺寸和显示状态。',
      keywords:'三栏 面板 尺寸 宽度 高度 左侧 右侧 底部 任务面板 对象面板 底部工作区',
      reset:()=>resetLayout(layoutMode),content:<>
        <Setting title="工作台"><select aria-label="工作台布局对象" value={layoutMode} onChange={event=>setLayoutMode(event.target.value as Mode)}>
          {Object.entries(layoutNames).map(([id,name])=><option key={id} value={id}>{name}</option>)}
        </select></Setting>
        {(Object.entries(panels) as [Panel,string][]).map(([panel,name])=><Setting key={panel} title={name}>
          <input type="checkbox" aria-label={name} checked={!layouts[layoutMode][panel]} onChange={event=>onPanel(layoutMode,panel,event.target.checked)}/>
        </Setting>)}
      </>},
    {id:'simulation',title:'运行与仿真',icon:SlidersHorizontal,description:'设置运行工作台使用的仿真层级。',
      keywords:'仿真层级 模拟 计划 径路 场景 L1 L2 L3',reset:()=>setLevel('l2'),content:
      <Setting title="仿真层级" description="这个设置也可在运行工作台调整。">
        <select aria-label="仿真层级" value={level} onChange={event=>setLevel(event.target.value as Level)}>
          {Object.entries(simulationLevels).map(([id,name])=><option key={id} value={id}>{name}</option>)}
        </select>
      </Setting>},
    {id:'data',title:'本地数据',icon:Database,description:'查看当前离线快照，或打开数据工作台管理数据包。',
      keywords:'本地数据包 全国 上海 快照 OSM 版本 数据源 切换 安装',content:
      <div className="setting-row"><span><strong>{pack?.region??'当前快照'}</strong><small>{pack?.snapshot??'尚未加载本地数据包'}</small></span>
        <button className="secondary compact" onClick={onData}>打开数据工作台</button>
      </div>},
  ] satisfies {id:SettingsSection;title:string;icon:typeof Map;description:string;keywords:string;reset?:()=>void;content:ReactNode}[]
  const words=query.toLowerCase().trim().split(/\s+/).filter(Boolean)
  const matches=sections.filter(item=>words.every(word=>`${item.title} ${item.keywords}`.toLowerCase().includes(word)))
  const active=matches.find(item=>item.id===section)??matches[0]
  useEffect(()=>{if(body.current)body.current.scrollTop=0},[active?.id])
  return <dialog ref={dialog} className="settings-dialog" aria-labelledby="settings-title" onClose={onClose}>
    {open&&<>
      <header className="settings-header">
        <h2 id="settings-title">设置</h2>
        <label className="settings-search"><Search size={16}/><input ref={search} aria-label="搜索设置" placeholder="搜索设置" value={query} onChange={event=>setQuery(event.target.value)}/>
          {query&&<button className="icon" aria-label="清除设置搜索" onClick={()=>{setQuery('');search.current?.focus()}}><X size={14}/></button>}
        </label>
        <button className="icon" aria-label="关闭设置" title="关闭设置 (Esc)" onClick={onClose}><X size={19}/></button>
      </header>
      <div className="settings-main">
        <nav className="settings-nav" aria-label="设置分类">{matches.map(({id,title,icon:Icon})=>
          <button key={id} aria-current={active?.id===id?'page':undefined} onClick={()=>onSection(id)}><Icon size={16}/>{title}</button>)}</nav>
        <div className="settings-content" ref={body}>
          {active?<section aria-labelledby="settings-section-title">
            <div className="settings-section-header"><div><h3 id="settings-section-title">{active.title}</h3><p>{active.description}</p></div>
              {active.reset&&<button className="secondary compact" onClick={active.reset}><RotateCcw size={13}/>恢复本类默认</button>}
            </div>
            {active.content}
          </section>:<p role="status">没有匹配的设置。</p>}
        </div>
      </div>
      <footer className="settings-footer"><span>设置即时生效并保存在本机</span><button className="primary compact" onClick={onClose}>完成</button></footer>
    </>}
  </dialog>
}
