import { ArrowRight, Database, Globe, Map, TrainFront, Upload, X } from 'lucide-react'
import { useEffect, useRef } from 'react'
import type { Pack } from './api'
import { setLanguage, t, useLanguage, type Language } from './i18n'

export function LanguageSelect() {
  const language = useLanguage()
  return <label className="language-select" title={t("界面语言")}>
    <Globe size={16} />
    <select aria-label={t("界面语言")} value={language} onChange={event=>setLanguage(event.target.value as Language)}>
      <option value="zh" lang="zh-CN">中文</option>
      <option value="en" lang="en">English</option>
    </select>
  </label>
}

export function Welcome({open,pack,onClose,onMap,onOperations,onData,onExternal}:{
  open:boolean;pack:Pack|null;onClose:()=>void;onMap:()=>void;onOperations:()=>void;onData:()=>void;onExternal:()=>void
}) {
  const dialog=useRef<HTMLDialogElement>(null)
  useEffect(()=>{
    const element=dialog.current
    if(open&&!element?.open) element?.showModal()
    else if(!open&&element?.open) element.close()
  },[open])
  return <dialog className="welcome-dialog" ref={dialog} aria-labelledby="welcome-title" onClose={onClose}>
    {open&&<>
      <header className="welcome-header">
        <div className="brand-icon"><TrainFront size={25} /></div>
        <LanguageSelect />
        <button className="icon" aria-label={t("退出介绍")} onClick={onClose}><X size={20} /></button>
      </header>
      <h2 id="welcome-title">{t("欢迎使用 RailScope Next")}</h2>
      <p className="welcome-intro">{pack?.region==='上海'?t("已加载上海离线地图，可以直接浏览铁路、地铁与场站。")
        :pack?t("已加载本地快照。"):t("安装版内含上海离线地图。也可以从数据管理安装已有快照。")}</p>
      <div className="welcome-actions">
        {[
          {icon:Map,title:t("打开地图"),description:t("地图上可点击线路和车站，或从目录搜索。"),action:onMap,enabled:!!pack},
          {icon:TrainFront,title:t("打开运行工作台"),description:t("创建共享通道、编辑时刻并查看运行图。"),action:onOperations,enabled:!!pack},
          {icon:Database,title:t("管理离线数据"),description:t("查看随附上海数据，安装或切换本地快照。"),action:onData,enabled:true},
          {icon:Upload,title:t("导入与导出"),description:t("导出当前资料，填写后导回工作区。"),action:onExternal,enabled:true},
        ].map(({icon:Icon,title,description,action,enabled},index)=>
          <button key={index} className={index===0?'welcome-action welcome-primary':'welcome-action'} disabled={!enabled} onClick={action}>
            <Icon size={20}/><span><strong>{title}</strong><small>{description}</small></span><ArrowRight size={17}/>
          </button>)}
      </div>
    </>}
  </dialog>
}
