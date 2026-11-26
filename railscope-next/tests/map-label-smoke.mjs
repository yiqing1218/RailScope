import { strict as assert } from 'node:assert'
import { mkdir,writeFile } from 'node:fs/promises'
import { join } from 'node:path'

const origin=process.env.RAILSCOPE_CDP
const output=process.argv[2]
const baseline=process.env.RAILSCOPE_BASELINE==='1'
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms))
async function wait(sample,label){
  for(let i=0;i<120;i++){
    const result=await sample().catch(()=>null)
    if(result)return result
    await sleep(250)
  }
  throw Error(label)
}
const page=await wait(async()=>(await(await fetch(`${origin}/json/list`)).json())
  .find(tab=>tab.type==='page'&&tab.url.includes('tauri.localhost')),'WebView unavailable')
const socket=new WebSocket(page.webSocketDebuggerUrl)
await new Promise((resolve,reject)=>{socket.onopen=resolve;socket.onerror=reject})
const pending=new Map()
let sequence=0
socket.addEventListener('message',({data})=>{
  const result=JSON.parse(data)
  if(!pending.has(result.id))return
  const {resolve,reject}=pending.get(result.id)
  pending.delete(result.id)
  if(result.error)reject(Error(result.error.message))
  else resolve(result.result)
})
function send(method,params={}){
  return new Promise((resolve,reject)=>{
    const id=++sequence
    const timer=setTimeout(()=>{pending.delete(id);reject(Error(method))},20000)
    pending.set(id,{resolve:(value)=>{clearTimeout(timer);resolve(value)},reject:(error)=>{clearTimeout(timer);reject(error)}})
    socket.send(JSON.stringify({id,method,params}))
  })
}
async function evaluate(expression){
  const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true})
  if(r.exceptionDetails)throw Error(r.exceptionDetails.exception?.description??r.exceptionDetails.text)
  return r.result.value
}
const invoke=(command,args={})=>evaluate(`window.__TAURI_INTERNALS__.invoke(${JSON.stringify(command)},${JSON.stringify(args)})`)
const click=(selector)=>evaluate(`document.querySelector(${JSON.stringify(selector)})?.click()`)
const nav=label=>evaluate(`[...document.querySelectorAll(".top nav button")].find(node=>node.textContent.trim()===${JSON.stringify(label)}).click()`)
async function camera(lng,lat,zoom){
  await evaluate(`(()=>{
    const settings=document.querySelector('.view-settings');
    settings.open=true;
    const form=settings.querySelector('.coordinate-jump');
    for(const [key,value] of Object.entries(${JSON.stringify({lng,lat,zoom})}))form.elements.namedItem(key).value=value;
    form.requestSubmit();
    settings.open=false;
  })()`)
}
async function geographicTile(snapshot,{lng,lat,zoom}){
  const z=Math.floor(zoom)
  const scale=2**z
  const x=Math.floor((lng+180)/360*scale)
  const y=Math.floor((1-Math.asinh(Math.tan(lat*Math.PI/180))/Math.PI)/2*scale)
  return evaluate(`(async()=>{
    const res=await fetch('http://railscope-tile.localhost/${snapshot}/${z}/${x}/${y}/context');
    if(!res.ok) throw Error(res.status);
    return new TextDecoder().decode(await res.arrayBuffer());
  })()`)
}
try {
  await mkdir(output,{recursive:true})
  const snapshot=await wait(()=>evaluate('document.querySelector(".snapshot")?.textContent'),'No active source pack')
  await nav('地图')
  const viewport=await evaluate('({width:innerWidth,height:innerHeight,dpr:devicePixelRatio})')
  const views=[
    {name:'wuhan',lng:114.3247,lat:30.5658,zoom:9.3},
    {name:'shanghai',lng:121.4737,lat:31.2304,zoom:11.7},
    {name:'beijing',lng:116.395,lat:39.9042,zoom:10.4},
    {name:'jinan',lng:116.99,lat:36.67,zoom:11.2},
    ...(!baseline?[
      {name:'national-overview',lng:106,lat:33,zoom:4.2},
      {name:'nanjing-overview',lng:118.5396,lat:32.0136,zoom:8.9},
      {name:'nanjing-rail-stations',lng:118.8007,lat:32.0284,zoom:10.3},
      {name:'nanjing-city-detail',lng:118.7560,lat:32.0474,zoom:13.9},
    ]:[]),
  ]
  for(const view of views){
    const start=performance.now()
    await camera(view.lng,view.lat,view.zoom)
    await wait(()=>evaluate(`Math.abs(Number(document.querySelector(".zoom")?.textContent.slice(2))-${view.zoom})<.11`),'Camera did not update')
    if(!baseline&&view.zoom<9){
      const basemap=await geographicTile(snapshot,view)
      for(const layer of ['base_admin','base_place','base_road','base_water']) assert.ok(basemap.includes(layer),`${view.name} · ${layer}`)
    }
    await wait(async()=> (await invoke('diagnostics')).pending===0,'Tile work did not drain')
    await sleep(450)
    view.settled_ms=Math.round(performance.now()-start)
    view.stats=await invoke('diagnostics')
    const errors=await evaluate('[...document.querySelectorAll(".map-error")].map(e=>e.textContent)')
    assert.deepEqual(errors,[])
    view.fps=await evaluate(`new Promise(resolve=>{
      let first=null,count=0;
      function frame(now){first??=now;if(now-first>=1000)resolve(Math.round(count*1000/(now-first)));else{count++;requestAnimationFrame(frame)}}
      requestAnimationFrame(frame)
    })`)
    const image=await send('Page.captureScreenshot',{format:'png'})
    await writeFile(join(output,`${view.name}.png`),Buffer.from(image.data,'base64'))
  }
  if(!baseline){
    await evaluate('document.querySelectorAll(".map-tools > .view-settings")[1].open=true')
    const key=name=>`.map-tools input[aria-label="${name}"]`
    const checked=name=>evaluate(`document.querySelector(${JSON.stringify(key(name))}).checked`)
    assert.equal(await checked('乡镇与村庄名'),false)
    await click(key('铁路线路名称'))
    assert.equal(await checked('铁路线'),true)
    assert.equal(await checked('铁路线路名称'),false)
    await click(key('城轨车站名称'))
    assert.equal(await checked('城轨车站符号'),true)
    assert.equal(await checked('城轨车站名称'),false)
    await evaluate('window.savedCanvas=document.querySelector(".maplibregl-canvas")')
    await nav('运行')
    assert.equal(await evaluate('window.savedCanvas===document.querySelector(".maplibregl-canvas")'),true)
    assert.equal(await checked('铁路线路名称'),false)
    await nav('地图')
    await click(key('铁路线路名称'))
    await click(key('城轨车站名称'))
    const station=(await invoke('catalog_page',{snapshot,filter:{query:'上海虹桥',kind:'station',limit:8}})).items[0]
    const detail=(await invoke('get_entity',{snapshot,id:station.id})).entity
    assert.ok(detail.station_links.length)
    assert.ok(detail.station_links.every(link=>link.source_ref.startsWith('osm:relation:')))
    const line=detail.station_links.find(link=>link.relation==='infrastructure')?.line
    assert.ok(line)
    const geometry=await invoke('get_geometry',{snapshot,id:line.id})
    assert.ok(geometry?.geometries?.length)

    for(const [reference,classification] of [
      ['osm:node:1606243206','bus_station'],
      ['osm:node:1608084341','bus_station'],
      ['osm:way:1511458439','unclassified_transport_station'],
    ]){
      const row=(await invoke('catalog_page',{snapshot,filter:{query:reference,limit:10}})).items[0]
      assert.equal((await invoke('get_entity',{snapshot,id:row.id})).entity.semantics.render_class,classification)
    }

    await evaluate('document.querySelectorAll(".map-tools > .view-settings")[1].open=false')
    await nav('设置')
    await wait(()=>evaluate('document.querySelector(".settings-dialog").open'),'Settings did not open')
    const settingsClick=label=>evaluate(`[...document.querySelectorAll(".settings-dialog button")].find(node=>node.textContent.trim()===${JSON.stringify(label)}).click()`)
    const setChoice=(label,value)=>evaluate(`(()=>{
      const el=document.querySelector('.settings-dialog select[aria-label="${label}"]');
      el.value=${JSON.stringify(value)};
      el.dispatchEvent(new Event('change',{bubbles:true}));
    })()`)
    await settingsClick('轨道配色')
    const settingsImage=await send('Page.captureScreenshot',{format:'png'})
    await writeFile(join(output,'settings-colours.png'),Buffer.from(settingsImage.data,'base64'))
    await setChoice('国铁配色','speed')
    await setChoice('城市轨道配色','infrastructure')
    await setChoice('城市轨道配色','lines')
    assert.equal(await evaluate('localStorage.getItem("railscope-next.rail-style")'),'speed')
    assert.equal(await evaluate('localStorage.getItem("railscope-next.metro-style")'),'lines')
    await settingsClick('完成')
    assert.equal(await evaluate('window.savedCanvas===document.querySelector(".maplibregl-canvas")'),true)
    await send('Page.reload')
    await wait(()=>evaluate('document.querySelector(".snapshot")?.textContent'),'Reload did not select pack')
    await nav('设置')
    await settingsClick('轨道配色')
    assert.equal(await evaluate('document.querySelector(".settings-dialog select[aria-label=国铁配色]").value'),'speed')
    await settingsClick('恢复本类默认')
    assert.deepEqual(await evaluate(`[...document.querySelectorAll(".settings-dialog select")].map(el=>el.value)`),['infrastructure','lines'])
    await settingsClick('图层与标注')
    const settingLabel='.settings-dialog input[aria-label="城市与重要地名"]'
    const citiesBefore=await evaluate(`document.querySelector(${JSON.stringify(settingLabel)}).checked`)
    await click(settingLabel)
    assert.equal(await checked('城市与重要地名'),!citiesBefore)
    await click(settingLabel)
    await evaluate(`(()=>{
      const search=document.querySelector('[aria-label="搜索设置"]');
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(search,'仿真层级');
      search.dispatchEvent(new Event('input',{bubbles:true}));
    })()`)
    assert.equal(await evaluate('document.querySelector("#settings-section-title").textContent'),'运行与仿真')
    await setChoice('仿真层级','l1')
    await settingsClick('完成')
    await nav('运行')
    assert.equal(await evaluate('document.querySelector(".ops-bottom [aria-label=仿真层级]").value'),'l1')
    await nav('地图')

    for(const [reference,role] of [
      ['osm:node:4610023440','station'],
      ['osm:way:768017614','junction'],
      ['osm:way:407071295','marshalling'],
      ['osm:way:157295605','depot'],
    ]){
      const item=(await invoke('catalog_page',{snapshot,filter:{query:reference,limit:10}})).items[0]
      const inspection=await invoke('get_entity',{snapshot,id:item.id})
      assert.equal(inspection.entity.site?.role,role,reference)
      assert.ok(inspection.entity.site.areas.length>0,reference)
      if(role==='station'){
        const building=inspection.entity.site.areas.find(area=>area.kind==='building')
        assert.ok(building,'station building linked from station')
        const reverse=await invoke('get_entity',{snapshot,id:building.object.id})
        assert.equal(reverse.entity.site.owner.id,item.id)
        assert.ok(inspection.entity.station_links.some(link=>link.relation==='infrastructure'))
      }
      if(role==='junction') assert.ok(inspection.entity.station_links.some(link=>link.relation==='area_track'))
      await evaluate(`(()=>{
        const search=document.querySelector('input[aria-label="搜索交通实体"]');
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(search,${JSON.stringify(reference)});
        search.dispatchEvent(new Event('input',{bubbles:true}));
      })()`)
      const row=`[...document.querySelectorAll(".result-row")].find(el=>el.textContent.includes(${JSON.stringify(item.name)}))`
      await wait(()=>evaluate(`Boolean(${row})`),`Station search: ${reference}`)
      await evaluate(`(${row}).click()`)
      await wait(()=>evaluate(`document.querySelector(".object-head h3")?.textContent===${JSON.stringify(item.name)}`),`Station inspector: ${reference}`)
      await click('.site-overview button')
      await sleep(1250)
      await wait(async()=> (await invoke('diagnostics')).pending===0,`Station tiles: ${reference}`)
      await sleep(700)
      await click('.property-tabs button:nth-child(2)')
      assert.equal(await evaluate('document.querySelector(".property-tabs button:nth-child(2)").getAttribute("aria-pressed")'),'true')
      if(inspection.entity.station_links.length) assert.ok(await evaluate('Boolean(document.querySelector(".station-evidence button"))'))
      const siteImage=await send('Page.captureScreenshot',{format:'png'})
      await writeFile(join(output,`site-${role}.png`),Buffer.from(siteImage.data,'base64'))
    }
    const hongqiao=(await invoke('catalog_page',{snapshot,filter:{query:'osm:node:4610023440',limit:10}})).items[0]
    const hongqiaoDetail=(await invoke('get_entity',{snapshot,id:hongqiao.id})).entity
    const building=hongqiaoDetail.site.areas.find(area=>area.kind==='building').object
    const footprint=await invoke('get_geometry',{snapshot,id:building.id})
    const [x,y]=footprint.type==='Polygon'?footprint.coordinates[0][0]:footprint.coordinates[0][0][0]
    await click('.inspector [aria-label="清除选中"]')
    await camera(x,y,16)
    await sleep(1500)
    await wait(async()=> (await invoke('diagnostics')).pending===0,'Station footprint tiles')
    const point=await evaluate(`(()=>{
      const {left,top,width,height}=document.querySelector('.maplibregl-canvas').getBoundingClientRect();
      return {x:left+width/2,y:top+height/2};
    })()`)
    await send('Input.dispatchMouseEvent',{...point,type:'mousePressed',button:'left',clickCount:1})
    await send('Input.dispatchMouseEvent',{...point,type:'mouseReleased',button:'left',clickCount:1})
    await wait(()=>evaluate('document.querySelector(".object-head h3")?.textContent==="上海虹桥"'),'Clicking the footprint opens its station')
  }
  await writeFile(join(output,'map-checks.json'),JSON.stringify({baseline,snapshot,viewport,views},null,2)+'\n')
  console.log(JSON.stringify(views.map(({name,settled_ms,fps})=>({name,settled_ms,fps}))))
}finally{socket.close()}
