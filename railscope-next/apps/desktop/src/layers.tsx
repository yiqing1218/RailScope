import { t } from './i18n'
import { useEffect, useRef, useState } from 'react'

export const layerGroups = [
  {name:'底图与地理参考', items:[
    ['geography','地表、水域与道路骨架'],['boundaries','行政边界'],['cities','城市与重要地名'],
    ['villages','乡镇与村庄名'],['localNames','普通道路名'],['localPoi','普通兴趣点名'],
  ]},
  {name:'国家铁路', items:[
    ['rail','铁路线'],['railNames','铁路线路名称'],['station','国铁车站符号'],['railStationNames','国铁车站名称'],
    ['facility','站台、站房、站区与股道'],['technical','线路所与道岔'],['signals','信号设备'],['relics','废弃与拆除轨道'],
  ]},
  {name:'城市轨道交通', items:[
    ['metro','城市轨道线路'],['metroNames','运营线路名称'],['metroStations','城轨车站符号'],
    ['metroStationNames','城轨车站名称'],['interchanges','换乘标志'],['metroFacilities','出入口与城轨设施'],
  ]},
  {name:'高速公路', items:[
    ['road','高速公路线路'],['roadNames','公路编号与名称'],['roadSites','服务区与交通设施'],
  ]},
  {name:'专业叠加', items:[
    ['route','所选车次走行路径'],['corridor','共享通道'],['railTrains','国铁列车'],['metroTrains','城轨列车'],['edit','编辑节点与分析结果'],
  ]},
] as const

export type LayerGroup = typeof layerGroups[number]['items'][number][0]
export type Visibility = Record<LayerGroup,boolean>
const keys = layerGroups.flatMap(group=>group.items.map(([key])=>key))
const optional = ['villages','localNames','localPoi','signals','relics']
export const defaultLayers = Object.fromEntries(keys.map(key=>[key,!optional.includes(key)])) as Visibility
export const presets = {
  '默认交通地图':defaultLayers,
  '国家铁路':{...defaultLayers,metro:false,metroNames:false,metroStations:false,metroStationNames:false,interchanges:false,metroFacilities:false,road:false,roadNames:false,roadSites:false},
  '城市轨道交通':{...defaultLayers,rail:false,railNames:false,station:false,railStationNames:false,facility:false,technical:false,road:false,roadNames:false,roadSites:false},
  '综合交通':{...defaultLayers,localNames:true},
  '详细地理参考':{...defaultLayers,villages:true,localNames:true,localPoi:true},
} satisfies Record<string,Visibility>

export function LayerTree({visibility,onLayer,onPreset}:{visibility:Visibility;onLayer:(key:LayerGroup,value:boolean)=>void;onPreset:(value:Partial<Visibility>)=>void}){
  return <div className="layer-controls">
    <label className="layer-preset">{t("地图预设")}<select aria-label={t("地图预设")} value="" onChange={event=>{
      const key=event.target.value as keyof typeof presets
      if(key)onPreset(presets[key])
    }}><option value="">{t("选择预设")}</option>{Object.keys(presets).map(key=><option key={key} value={key}>{t(key)}</option>)}</select></label>
    {layerGroups.map((group,index)=><LayerSection key={group.name} group={group} visibility={visibility}
      onLayer={onLayer} onPreset={onPreset} initiallyOpen={index<3}/>)}
  </div>
}

function LayerSection({group,visibility,onLayer,onPreset,initiallyOpen}:{
  group:typeof layerGroups[number];visibility:Visibility;onLayer:(key:LayerGroup,value:boolean)=>void;
  onPreset:(value:Partial<Visibility>)=>void;initiallyOpen:boolean
}){
  const enabled=group.items.filter(([key])=>visibility[key]).length
  const [expanded,setExpanded]=useState(initiallyOpen)
  const checkbox=useRef<HTMLInputElement>(null)
  useEffect(()=>{if(checkbox.current)checkbox.current.indeterminate=enabled>0&&enabled<group.items.length},[enabled,group])
  return <details className="layer-section" open={expanded} onToggle={event=>setExpanded(event.currentTarget.open)}>
    <summary><input ref={checkbox} type="checkbox" aria-label={t(group.name)} checked={enabled===group.items.length}
      onClick={event=>event.stopPropagation()} onChange={event=>onPreset(Object.fromEntries(group.items.map(([key])=>[key,event.target.checked])))}/>{t(group.name)}</summary>
    {group.items.map(([key,name])=><label className="layer-control" key={key}><span>{t(name)}</span>
      <input aria-label={t(name)} type="checkbox" checked={visibility[key]} onChange={event=>onLayer(key,event.target.checked)}/></label>)}
  </details>
}
