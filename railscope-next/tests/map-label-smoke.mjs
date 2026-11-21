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
try {
  await mkdir(output,{recursive:true})
  const snapshot=await wait(()=>evaluate('document.querySelector(".snapshot")?.textContent'),'No active source pack')
  const viewport=await evaluate('({width:innerWidth,height:innerHeight,dpr:devicePixelRatio})')
  const views=[
    {name:'wuhan',lng:114.3247,lat:30.5658,zoom:9.3},
    {name:'shanghai',lng:121.4737,lat:31.2304,zoom:11.7},
    {name:'beijing',lng:116.395,lat:39.9042,zoom:10.4},
    {name:'jinan',lng:116.99,lat:36.67,zoom:11.2},
  ]
  for(const view of views){
    const start=performance.now()
    await camera(view.lng,view.lat,view.zoom)
    await wait(()=>evaluate(`Math.abs(Number(document.querySelector(".zoom")?.textContent.slice(2))-${view.zoom})<.11`),'Camera did not update')
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
    const nav=label=>evaluate(`[...document.querySelectorAll("header nav button")].find(node=>node.textContent.trim()===${JSON.stringify(label)}).click()`)
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
  }
  await writeFile(join(output,'map-checks.json'),JSON.stringify({baseline,snapshot,viewport,views},null,2)+'\n')
  console.log(JSON.stringify(views.map(({name,settled_ms,fps})=>({name,settled_ms,fps}))))
}finally{socket.close()}
