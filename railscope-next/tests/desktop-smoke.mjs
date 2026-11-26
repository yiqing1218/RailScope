import { strict as assert } from 'node:assert'
import { execFile } from 'node:child_process'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { promisify } from 'node:util'

const origin = process.env.RAILSCOPE_CDP || 'http://127.0.0.1:9229'
const artifacts = process.argv[2]
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
const execute = promisify(execFile)

async function waitFor(test, label, attempts = 90) {
  for (let i = 0; i < attempts; i++) {
    const value = await test().catch(() => undefined)
    if (value) return value
    await sleep(350)
  }
  throw new Error(`Timed out: ${label}`)
}

const target = await waitFor(async () =>
  (await (await fetch(origin + '/json/list')).json()).find(page =>
    page.type === 'page' && page.webSocketDebuggerUrl &&
    (page.url.includes('tauri.localhost') || page.url.includes('127.0.0.1:1420'))), 'RailScope WebView2')
const socket = new WebSocket(target.webSocketDebuggerUrl)
await new Promise((resolve, reject) => {
  socket.addEventListener('open', resolve, { once: true })
  socket.addEventListener('error', reject, { once: true })
})
const pending = new Map()
const remote = []
const interceptionErrors = []
const localRequest = url => /^https?:\/\/(?:tauri|ipc|railscope-tile)\.localhost(?:\/|$)|^http:\/\/127\.0\.0\.1:1420(?:\/|$)/.test(url)
let commandId = 0
socket.addEventListener('close', () => {
  for (const handler of pending.values()) handler.reject(new Error('RailScope WebView2 disconnected'))
  pending.clear()
})
socket.addEventListener('message', ({ data }) => {
  const message = JSON.parse(data)
  if (message.id) {
    const handler = pending.get(message.id)
    pending.delete(message.id)
    if (message.error) handler?.reject(new Error(message.error.message))
    else handler?.resolve(message.result)
  } else if (message.method === 'Network.requestWillBeSent') {
    const url = message.params.request.url
    if (/^https?:/.test(url) && !localRequest(url)) remote.push(url)
  } else if (message.method === 'Fetch.requestPaused') {
    const { requestId, request } = message.params
    const response = localRequest(request.url) ? send('Fetch.continueRequest', { requestId })
      : send('Fetch.failRequest', { requestId, errorReason: 'InternetDisconnected' })
    void response.catch(error => {
      if (!error.message.includes('Invalid InterceptionId.')) interceptionErrors.push(error.message)
    })
  }
})

function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++commandId
    const timer = setTimeout(() => {
      pending.delete(id)
      reject(new Error(`No WebView2 response for ${method}`))
    }, 45_000)
    const complete = fn => value => { clearTimeout(timer); fn(value) }
    pending.set(id, { resolve:complete(resolve), reject:complete(reject) })
    try { socket.send(JSON.stringify({ id, method, params })) }
    catch (error) { pending.delete(id); complete(reject)(error) }
  })
}
async function evaluate(expression) {
  const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
  if (result.exceptionDetails) {
    const {exception, text} = result.exceptionDetails
    throw new Error(exception?.description ?? exception?.value ?? text)
  }
  return result.result.value
}
const invoke = (command, args = {}) => evaluate(
  `window.__TAURI_INTERNALS__.invoke(${JSON.stringify(command)}, ${JSON.stringify(args)})`,
)
const click = label => evaluate(`[...document.querySelectorAll('button')].find(
  b => b.innerText.trim() === ${JSON.stringify(label)} || b.getAttribute('aria-label') === ${JSON.stringify(label)}
)?.click()`)
const input = (selector, value) => evaluate(`(() => {
  const el = document.querySelector(${JSON.stringify(selector)});
  Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value').set.call(el, ${JSON.stringify(value)});
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
})()`)
const select = (selector, value) => evaluate(`(() => {
  const element=document.querySelector(${JSON.stringify(selector)})
  element.value=${JSON.stringify(value)}
  element.dispatchEvent(new Event('change',{bubbles:true}))
})()`)

async function jump(lon, lat, zoom) {
  await evaluate('document.querySelector(".view-settings").open=true')
  await input('[aria-label=跳转经度]', String(lon))
  await input('[aria-label=跳转纬度]', String(lat))
  await input('[aria-label=跳转缩放]', String(zoom))
  await evaluate('document.querySelector(".coordinate-jump").requestSubmit(); document.querySelector(".view-settings").open=false')
}

async function verifyStarter() {
  await waitFor(() => evaluate('document.querySelector(".welcome-dialog").open'), 'first-run welcome')
  assert.equal((await invoke('list_data_packs')).active?.snapshot, 'shanghai-osm-202609-v4')
  await select('.welcome-dialog .language-select select', 'en')
  assert.equal(await evaluate('document.documentElement.lang'), 'en')
  assert.ok(await evaluate('document.querySelector(".welcome-dialog").innerText.includes("Shanghai offline map is ready")'))
  if (artifacts) {
    await mkdir(artifacts, {recursive:true})
    const welcome = await send('Page.captureScreenshot',{format:'png'})
    await writeFile(join(artifacts,'welcome-en.png'),Buffer.from(welcome.data,'base64'))
  }
  await evaluate('document.querySelector(".welcome-primary").click()')
  await waitFor(() => evaluate('!document.querySelector(".welcome-dialog").open'), 'enter included map')
  assert.ok(await evaluate('document.querySelector(".map-title").innerText.includes("Shanghai")'))
  const before = (await invoke('diagnostics')).precise_tiles
  await evaluate(`(() => {
    const form = document.querySelector('.coordinate-jump');
    form.elements.lng.value = '121.474';
    form.elements.lat.value = '31.230';
    form.elements.zoom.value = '12';
    form.requestSubmit();
  })()`)
  await waitFor(async()=> (await invoke('diagnostics')).precise_tiles > before, 'Shanghai tiles work offline')
  await click('Operations')
  assert.ok(await evaluate('document.querySelector(".ops-workbench").innerText.includes("Shared corridors")'))
  await click('Data')
  assert.ok(await evaluate('document.querySelector(".local-pack-install").innerText.includes("Install a local snapshot")'))
  await click('Settings')
  await input('.settings-search input','Simulation level')
  await waitFor(() => evaluate('document.querySelector("#settings-section-title")?.textContent==="Operations and simulation"'),'English settings search')
  await click('Done')
  await send('Page.reload')
  await waitFor(() => evaluate('document.querySelector(".snapshot")?.textContent==="shanghai-osm-202609-v4"'),'reload included pack')
  assert.equal(await evaluate('document.documentElement.lang'),'en')
  assert.equal(await evaluate('document.querySelector(".welcome-dialog").open'),false)
  await select('.top .language-select select', 'zh')
  assert.equal(await evaluate('document.documentElement.lang'),'zh-CN')
  assert.ok(await evaluate('document.querySelector(".map-title").innerText.includes("上海")'))
}

async function roam(snapshot, minutes) {
  const cameras = [
    { name: '济南指定中心', lon: 116.9003, lat: 36.0676 },
    { name: '泰安邻近铁路', lon: 116.9058643, lat: 36.2033374 },
    { name: '上海虹桥', lon: 121.3198411, lat: 31.1988798 },
    { name: '北京南站', lon: 116.378, lat: 39.865 },
    { name: '京沪高铁苏皖邻近区间', lon: 118.784, lat: 32.188 },
    { name: '江苏高速互通', lon: 120.556, lat: 31.365 },
  ]
  const zooms = [4, 8, 11, 12.9, 13, 15, 17, 19, 21, 22]
  const started = performance.now()
  const samples = []
  const errors = []
  const pid = Number(process.env.RAILSCOPE_TEST_PID)
  const viewport = await evaluate('({width:innerWidth,height:innerHeight,dpr:devicePixelRatio,userAgent:navigator.userAgent,visibility:document.visibilityState})')
  const flush = async visits => {
    if (artifacts) await writeFile(join(artifacts,'roaming.json'), JSON.stringify({snapshot, minutes, elapsed_ms:performance.now()-started,
      viewport, visits, samples, errors, external_requests:remote.length},null,2)+'\n')
  }
  const probes = await send('Runtime.getIsolateId')
  await click('分析与诊断')
  await click('系统性能')
  let visits = 0
  do {
    const camera = cameras[visits % cameras.length]
    const zoom = zooms[Math.floor(visits / cameras.length) % zooms.length]
    await jump(camera.lon, camera.lat, zoom)
    await sleep(1600)
    const ui = await evaluate(`({zoom:Number(document.querySelector('.zoom').textContent.slice(2)),
      fps:document.querySelector('.metrics strong')?.textContent, errors:[...document.querySelectorAll('.map-error')].map(el=>el.textContent)})`)
    if (ui.errors.length || Math.abs(ui.zoom - zoom) > 0.11) errors.push({camera:camera.name, zoom, ui})
    if (visits % 20 === 0 || (visits < cameras.length * zooms.length && zoom >= 15)) {
      const heap = await send('Runtime.getHeapUsage')
      let processMemory = null
      if (Number.isSafeInteger(pid) && pid > 0 && process.platform === 'win32') {
        const result = await execute('powershell.exe',['-NoProfile','-Command',
          `Get-Process -Id ${pid} | Select-Object WorkingSet64,PrivateMemorySize64,CPU | ConvertTo-Json -Compress`])
        processMemory = JSON.parse(result.stdout)
      }
      samples.push({elapsed_ms:performance.now()-started,isolate:probes.id,camera:camera.name,zoom,heap,processMemory,
        fps:ui.fps,tiles:await invoke('diagnostics')})
      await flush(visits+1)
    }
    visits++
  } while (performance.now() - started < minutes * 60000)
  await flush(visits)
  assert.deepEqual(errors, [], 'map and camera during timed roaming')
  assert.deepEqual(remote, [], 'offline traffic during roaming')
}

async function verifyNationalSource(snapshot) {
  const ref = 'osm:way:840021768'
  const source = await invoke('get_osm_source',{snapshot,reference:ref,offset:0})
  assert.ok(source.complete && source.rows.length === 42)
  const matches = await invoke('catalog_page',{snapshot,filter:{query:ref,kind:'rail'}})
  assert.ok(matches.items.some(item=>item.id==='9b430853-3225-55bf-97cb-c944865a1e20'))
  const cameras = [
    [116.9003,36.0676,12.9,'specified-jinan'],
    [116.9058643,36.2033374,12.9,'sample-jinan'],
    [116.9058643,36.2033374,21,'sample-z21'],
    [116.9058643,36.2033374,22,'sample-z22'],
  ]
  await click('地图')
  const checks = []
  for (const [lon, lat, zoom, label] of cameras) {
    const first = (await invoke('diagnostics')).precise_tiles
    await jump(lon,lat,zoom)
    await waitFor(async()=> (await invoke('diagnostics')).precise_tiles>first, `national geometry at Z${zoom}`)
    await sleep(700)
    assert.deepEqual(await evaluate('[...document.querySelectorAll(".map-error")].map(e => e.textContent)'), [])
    assert.equal(await evaluate('Number(document.querySelector(".zoom").textContent.slice(2))'), zoom)
    checks.push({lon,lat,zoom,tiles:await invoke('diagnostics')})
    if (artifacts) {
      const image = await send('Page.captureScreenshot',{format:'png'})
      await writeFile(join(artifacts,`${label}.png`),Buffer.from(image.data,'base64'))
    }
  }
  assert.deepEqual(remote,[])
  return {snapshot,source_ref:ref,source_vertices:source.rows.length,checks,external_requests:remote.length}
}

async function verifyV4(snapshot) {
  const corridor = (action, args) => invoke('corridor_query', {
    snapshot, job: crypto.randomUUID(), request: { action, ...args },
  })
  const line = '408e1caa-58f5-5cc5-b0b5-48d0633616df'
  const edge = '1aa9451a-b36f-508e-acbd-2510eb50a0a1'
  const start = 'be352ca4-88d8-5c00-8fa3-f4f6dea2f4a7'
  const end = 'b1a549cd-ee05-5c3f-aa3b-a1ef48563d27'
  assert.deepEqual(new Set((await corridor('controls', { id: edge })).map(row => row.id)), new Set([start, end]))
  assert.ok((await corridor('lines', { node: start, mode: 'metro' })).some(row => row.id === line))
  assert.ok((await corridor('destinations', { node: start, line, mode: 'metro' })).some(row => row.id === end))
  const preview = await corridor('resolve', { name: '编排验收 · 上海地铁', mode: 'metro', strict: true,
    steps: [{ node_id: start, line_id: line, via_edge: edge }, { node_id: end, line_id: null }] })
  assert.equal(preview.corridor.verification_status, 'automatic_reference')
  assert.equal(preview.corridor.edge_refs.length, 1)
  assert.equal(preview.corridor.edge_refs[0].edge_id, edge)
  assert.equal(preview.geometry.type, 'GeometryCollection')
  const reversed = await corridor('reverse', { corridor: preview.corridor })
  assert.equal(reversed.corridor.origin_node_id, end)
  assert.equal(reversed.corridor.edge_refs[0].forward, !preview.corridor.edge_refs[0].forward)
  const first = '0a868143-39c9-5423-9424-60ee8b8deda3'
  const second = '3841e3dc-0e96-5c86-9bcf-3a6cd8a6b5cf'
  const crossing = await corridor('resolve',{name:'衔接线路验收',mode:'rail',strict:false,steps:[
    {node_id:'6309e0d5-aac5-5809-81b8-4dcb7b1d8f53',line_id:'a659c2ba-0ee4-5995-986c-9fa614dc6104',via_edge:first},
    {node_id:'7743cf3c-35e8-5ff9-88ef-29485f8c78ab',line_id:'5ebc4f61-5c04-5e14-b06b-9dee1cdb2e8b',via_edge:second},
    {node_id:'197817ef-f174-504f-8982-93e4b8eee234',line_id:null},
  ]})
  assert.deepEqual(crossing.corridor.edge_refs.map(item=>item.edge_id),[first,second])
  const ops = (action, args = {}) => invoke('operations', {snapshot, request: {action, ...args}})
  const out = await ops('save_corridor', {corridor:preview.corridor})
  const back = await ops('save_corridor', {corridor:reversed.corridor})
  const provenance = {source_kind:'demo',source_url_or_reference:'合成时刻和停车锚点 · 上海地铁真实物理边'}
  const stations = ['b94f34a1-c456-589d-bdc9-4bed8134f269','d2529f5c-6465-55d8-9999-227661dc1044']
  async function template(path, stationIds, label) {
    const service = await ops('save_service', {service:{id:'',train_code:label,mode:'metro',provenance}})
    const stops = stationIds.map((station_id,sequence)=>({station_id,sequence,arrival_time_s:18000+90*sequence,
      departure_time_s:18000+90*sequence,stop_edge_id:edge,stop_edge_sequence:0,
      stop_offset_m:sequence===0?0:path.edge_refs[0].end_distance_m}))
    return ops('save_run', {run:{id:'',service_id:service.id,train_number:label,service_date:'2026-11-20',
      time_zone:'Asia/Shanghai',plan_version:0,status:'scheduled',corridor_id:path.id,corridor_version:path.version,stops,provenance}})
  }
  const outbound = await template(out,stations,'循环验收去程')
  const inbound = await template(back,[...stations].reverse(),'循环验收返程')
  const cycle = await ops('generate_cycle', {cycle:{id:'',name:'循环验收',outbound_id:outbound.id,outbound_version:1,
    return_id:inbound.id,return_version:1,service_date:'2026-11-20',first_departure_s:36000,end_s:36400,
    turnaround_s:60,launch_gap_s:90,vehicles:1}})
  assert.equal(cycle.runs,3)
  await ops('archive_corridor',{id:out.id})
  assert.ok(!(await ops('list')).corridors.some(item=>item.id===out.id))
  assert.equal((await ops('get_run',{id:outbound.id})).corridor.id,out.id)
  const old = JSON.stringify({schema:'railscope.rail-corridors.v1',corridors:[{id:'legacy-test',name:'旧版迁移验收',
    path:[{edge_id:'old-edge',direction:'forward'}]}]})
  const issue = await ops('import_legacy',{content:old,service_date:'2026-11-20'})
  assert.equal(issue.pending,true)
  assert.ok((await ops('legacy_issues')).some(item=>item.reason===issue.issue))
  const resolved = await ops('import_legacy',{content:old,service_date:'2026-11-20',aliases:{'edge:old-edge':edge}})
  assert.equal(resolved.corridors,1)
  assert.ok(!(await ops('legacy_issues')).some(item=>item.reason===issue.issue))

  await send('Page.reload',{ignoreCache:true})
  await waitFor(()=>evaluate('Boolean(document.querySelector(".snapshot")?.textContent)'),'cycle workspace reloaded')
  await click('运行')
  await click('城市轨道')
  await click('车次')
  await input('[aria-label=运营日期]','2026-11-20')
  const metroDeparture='[...document.querySelectorAll(".ops-list-inner button")].find(el=>el.querySelector("small")?.textContent==="10:00:00")'
  await waitFor(()=>evaluate(`Boolean(${metroDeparture})`),'Shanghai cycle departures')
  await evaluate(`(${metroDeparture}).click()`)
  await waitFor(()=>evaluate('document.querySelectorAll(".metro-plan").length===3&&Boolean(document.querySelector(".metro-plan.return"))'),
    'Shanghai chart shows both directions')
  if(artifacts){
    const image=await send('Page.captureScreenshot',{format:'png'})
    await writeFile(join(artifacts,'metro-cycle.png'),Buffer.from(image.data,'base64'))
  }

  await click('地图')
  await evaluate('window.__railCanvas = document.querySelector(".maplibregl-canvas")')
  const sidebar = 'document.querySelector(".task-dock").hidden'
  const initial = await evaluate(sidebar)
  await click('任务面板')
  assert.equal(await evaluate(sidebar), !initial)
  for (const [label,mode] of [['运行','operations'],['分析与诊断','analysis'],['数据','data'],['地图','detail']]) {
    await click(label)
    await waitFor(() => evaluate(`document.querySelector(".workspace").dataset.mode===${JSON.stringify(mode)}`), label)
    assert.equal(await evaluate('window.__railCanvas===document.querySelector(".maplibregl-canvas")'), true)
    if (artifacts) {
      const image = await send('Page.captureScreenshot', { format: 'png' })
      await writeFile(join(artifacts, `mode-${mode}.png`), Buffer.from(image.data, 'base64'))
    }
  }
  assert.equal(await evaluate(sidebar), !initial)
  await click('任务面板')
  const right = 'document.querySelector(".inspector").hidden'
  await click('对象面板')
  assert.equal(await evaluate(right), true)
  assert.equal(await evaluate(sidebar), initial)
  await click('任务面板')
  assert.equal(await evaluate(sidebar), !initial)
  assert.equal(await evaluate(right), true)
  await click('任务面板')
  await click('对象面板')
  await send('Emulation.setDeviceMetricsOverride',{width:1040,height:760,deviceScaleFactor:2,mobile:false})
  await waitFor(()=>evaluate('innerWidth===1040&&Math.abs(devicePixelRatio-2)<.01'),'compact high-DPI layout')
  assert.ok(await evaluate('document.querySelector(".map-panel").getBoundingClientRect().width>250'))
  assert.ok(await evaluate('document.documentElement.scrollWidth<=innerWidth'))
  await send('Emulation.clearDeviceMetricsOverride')
  await click('设置')
  await click('轨道配色')
  await select('[aria-label=国铁配色]', 'speed')
  await select('[aria-label=城市轨道配色]', 'infrastructure')
  assert.equal(await evaluate('document.querySelector("[aria-label=国铁配色]").value'), 'speed')
  await click('恢复本类默认')
  await click('完成')
  assert.deepEqual(await evaluate('[...document.querySelectorAll(".map-error")].map(e => e.textContent)'), [])
  async function inspectEdge(id, source, pointIndex) {
    const {entity} = await invoke('get_entity', {snapshot, id})
    await click('地图')
    await input('input[aria-label="搜索交通实体"]', source)
    const findRow = `[...document.querySelectorAll(".result-row")].find(el=>el.textContent.includes(${JSON.stringify(entity.name)}))`
    await waitFor(()=>evaluate(`Boolean(${findRow})`),'exact physical edge in catalogue')
    await evaluate(`(${findRow}).click()`)
    await waitFor(()=>evaluate(`document.querySelector(".object-head h3")?.textContent===${JSON.stringify(entity.name)}`),'edge inspector')
    await click('分析与诊断')
    await evaluate(`document.querySelectorAll(".path-point button")[${pointIndex}].click()`)
  }
  await inspectEdge(first,'osm:way:347270717',0)
  await inspectEdge(second,'osm:way:252713497',1)
  await click('计算连接路径')
  await waitFor(()=>evaluate('!document.querySelector(".bottom-dock").hidden&&Boolean(document.querySelector(".bottom-dock .result-path"))'),
    'cross-line analysis in bottom dock')
  if (artifacts) {
    const image = await send('Page.captureScreenshot',{format:'png'})
    await writeFile(join(artifacts,'analysis-result.png'),Buffer.from(image.data,'base64'))
  }
  await click('转到运行 · 新建通道')
  await waitFor(()=>evaluate('document.querySelector(".workspace").dataset.mode==="operations"&&[...document.querySelectorAll(".corridor-form > label input")].some(el=>el.value.endsWith("出发"))'),
    'analysis transfers into editable corridor')
  await evaluate(`(() => {
    const confirm = window.confirm;
    try {
      window.confirm = () => true;
      [...document.querySelectorAll('.corridor-form button')].find(el=>el.textContent==='新建／清空草稿').click();
    } finally {
      window.confirm = confirm;
    }
  })()`)
  await click('地图')
  return { controls: [start, end], line, edge, cross_line_edges:[first,second],
    modes: 4, canvas_preserved: true, cycle_runs:cycle.runs, legacy_retry:true, analysis_transfer:true }
}

async function verifyV3(snapshot, fixturePath) {
  const fixture=JSON.parse(await readFile(fixturePath,'utf8'))
  assert.equal(fixture.snapshot,snapshot)
  const ops=(action,args={})=>invoke('operations',{snapshot,request:{action,...args}})
  const source=await invoke('get_osm_source',{snapshot,reference:fixture.way_ref,offset:0})
  assert.ok(source.complete&&source.rows.length>1)
  assert.ok(source.rows.every(row=>Number.isFinite(row.longitude)))
  const results=await invoke('catalog_page',{snapshot,filter:{query:fixture.way_ref,limit:10}})
  assert.ok(results.items.some(row=>row.id===fixture.start_id))
  await click('设置')
  await click('地图与底图')
  await select('[aria-label=底图主题]','dark')
  await waitFor(()=>evaluate('document.querySelector(".app").dataset.theme==="dark"'),'local dark basemap')
  await select('[aria-label=底图主题]','light')
  await click('完成')
  await jump(fixture.lon, fixture.lat, 17)
  await waitFor(async()=> (await invoke('diagnostics')).precise_tiles>0,'generated high-zoom tiles')
  assert.deepEqual(await evaluate('[...document.querySelectorAll(".map-error")].map(e => e.textContent)'), [])
  if(artifacts){
    const image=await send('Page.captureScreenshot',{format:'png'})
    await writeFile(join(artifacts,'high-zoom.png'),Buffer.from(image.data,'base64'))
  }
  const lineEdit = (action, args = {}) => invoke('edit_business_line', {snapshot, request: {action, ...args}})
  const line = await invoke('get_entity', {snapshot, id:fixture.line_id})
  assert.equal(line.entity.kind, 'line')
  const made = await lineEdit('create', {name:'验收线路', sources:[line.entity.id], split_at:1})
  assert.equal(made.created, 2)
  const editState = await lineEdit('list')
  const derived = editState.lines.find(row => row.name === '验收线路（1）')
  assert.equal(derived.member_count, 1)
  assert.equal((await lineEdit('members',{id:derived.id,offset:0})).members.length, 1)
  assert.ok((await lineEdit('undo')).applied)
  assert.ok(!(await lineEdit('list')).lines.some(row=>row.id===derived.id))
  assert.ok((await lineEdit('redo')).applied)
  if (fixture.previous_snapshot) {
    await invoke('activate_data_pack', {snapshot:fixture.previous_snapshot})
    const replayed = await invoke('edit_business_line', {snapshot:fixture.previous_snapshot,request:{action:'list'}})
    assert.ok(replayed.lines.some(row=>row.id===derived.id))
    await invoke('activate_data_pack', {snapshot})
  }
  const merged = await lineEdit('create', {name:'验收合并线路', sources:[fixture.line_id, fixture.merge_line_id]})
  assert.equal(merged.created,1)
  const mergedLine = (await lineEdit('list')).lines.find(row=>row.name==='验收合并线路')
  assert.ok(mergedLine.member_count>derived.member_count)
  const mergedMembers = (await lineEdit('members', {id:mergedLine.id,offset:0})).members
  assert.equal(new Set(mergedMembers.map(item=>item.id)).size,mergedMembers.length)
  const candidate=await ops('resolve_corridor',{name:'验证通道',start_id:fixture.start_id,end_id:fixture.end_id,strict:false})
  assert.equal(candidate.verification_status,'automatic_reference')
  assert.ok(candidate.edge_refs.length>=2)
  await assert.rejects(ops('save_corridor',{corridor:{...candidate,verification_status:'verified'}}), /UNVERIFIED|核验/)
  const corridor=await ops('save_corridor',{corridor:candidate})
  assert.equal(corridor.version,1)
  const demo=await ops('seed_demo',{corridor_id:corridor.id,version:corridor.version,
    origin_station_id:fixture.origin_station_id,destination_station_id:fixture.destination_station_id,service_date:'2026-11-21'})
  const run_ids=demo.runs.map(run=>run.id)
  const updated=await ops('save_corridor',{corridor:{...corridor,name:'验证通道第二版'}})
  assert.equal(updated.version,2)
  assert.equal((await ops('get_run',{id:run_ids[0]})).run.corridor_version,1)
  const simulated=await ops('simulate',{run_ids,time_s:28875,level:'l2'})
  assert.equal(simulated.states.length,2)
  assert.ok(simulated.states.every(state=>state.status==='moving'&&Number.isFinite(state.lon)&&Number.isFinite(state.speed_mps)))
  assert.deepEqual((await ops('simulate',{run_ids,time_s:28875,level:'l2'})).states,simulated.states)
  const run = (await ops('get_run', {id:run_ids[0]})).run
  const draft = {...run, stops:run.stops.map(stop=>({...stop,
    arrival_time_s:stop.arrival_time_s==null?null:stop.arrival_time_s+60,
    departure_time_s:stop.departure_time_s==null?null:stop.departure_time_s+60,
  }))}
  const planned = (await ops('simulate',{run_ids:[run.id],time_s:28875,level:'l1'})).states[0]
  const preview = await ops('preview_run',{run:draft,time_s:28875})
  assert.ok(preview.distance_m<planned.distance_m)
  assert.deepEqual((await ops('get_run',{id:run.id})).run,run)
  const trainset = await ops('save_trainset',{trainset:{id:'',name:'验收车组',provenance:run.provenance}})
  await ops('assign',{assignment:{run_id:run_ids[0],trainset_id:trainset.id}})
  await assert.rejects(ops('assign',{assignment:{run_id:run_ids[1],trainset_id:trainset.id}}),/重叠|占用|VEHICLE/)
  const analysis=await ops('simulate',{run_ids,time_s:28875,level:'l3',resources:true})
  assert.ok(analysis.occupancies.length>=2&&analysis.conflicts.length>=1)
  assert.ok(analysis.diagnostics.some(note=>note.code==='UNVERIFIED_OPERATION_RULES'))
  const exported=await ops('export_timetable',{format:'json',run_ids})
  assert.equal(JSON.parse(exported.content).runs.length,2)
  const csv=await ops('export_timetable',{format:'csv',run_ids})
  assert.ok(csv.content.includes('arrival_time_s'))
  const gtfs=await ops('export_timetable',{format:'gtfs',run_ids})
  assert.equal(Buffer.from(gtfs.content,'base64').subarray(0,2).toString(),'PK')
  await send('Page.reload',{ignoreCache:true})
  await waitFor(()=>evaluate('document.querySelector(".snapshot")?.textContent.length>0'),'workspace restart')
  await click('运行')
  await waitFor(()=>evaluate('document.querySelector(".ops-workbench")?.hidden===false'),'operations workbench')
  await click('车次')
  await input('[aria-label=运营日期]','2026-11-21')
  await waitFor(()=>evaluate('Boolean(document.querySelector(".ops-list-inner button"))'),'dated run rows')
  await evaluate('[...document.querySelectorAll(".ops-list-inner button")].find(el=>el.textContent.includes("演示")&&el.querySelector("small")?.textContent==="08:00:00")?.click()')
  await waitFor(()=>evaluate('Boolean(document.querySelector(".run-graph svg"))'),'time-distance graph')
  await click('跳到发车')
  await click('仿真场景')
  await evaluate('document.querySelector(".operations-dialog header button").click()')
  await click('播放模拟')
  await waitFor(()=>evaluate('Boolean(document.querySelector("[aria-label=暂停模拟]"))'),'Rust playback clock')
  await click('分析与诊断')
  await waitFor(()=>evaluate('document.querySelector(".status-action")?.textContent.includes("暂停")'),'clock survives mode change')
  await sleep(750)
  await click('运行')
  await click('暂停模拟')
  await click('车次')
  await click('编辑时刻')
  await click('运行图')
  await click('聚焦底部工作区')
  const handle = await waitFor(()=>evaluate(`(() => {
    const node = [...document.querySelectorAll('.time-handle')].at(-1);
    if (!node) return null;
    node.scrollIntoView({block:'nearest'});
    const {x,y,width,height} = node.getBoundingClientRect();
    const point = {x:x+width/2,y:y+height/2};
    return document.elementFromPoint(point.x,point.y)===node?point:null;
  })()`),'visible chart handle')
  const readDeparture = () => evaluate('document.querySelector("input[name=depart-1]").value')
  const beforeDrag = await readDeparture()
  await send('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',buttons:1,clickCount:1,...handle})
  await send('Input.dispatchMouseEvent',{type:'mouseMoved',button:'left',buttons:1,x:handle.x+16,y:handle.y})
  await send('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',buttons:0,clickCount:1,x:handle.x+16,y:handle.y})
  const afterDrag = await waitFor(async()=>{const value=await readDeparture();return value!==beforeDrag&&value},'chart updates timetable')
  await click('撤销时刻修改')
  await waitFor(async()=>await readDeparture()===beforeDrag,'chart undo')
  await click('重做时刻修改')
  await waitFor(async()=>await readDeparture()===afterDrag,'chart redo')
  await click('时刻表')
  await click('保存时刻')
  await waitFor(()=>evaluate('[...document.querySelectorAll(".ops-message.success")].some(e=>e.textContent.includes("已保存计划版本"))'),
    'save anchored timetable with full source precision')
  assert.equal((await ops('get_run',{id:run.id})).run.plan_version,2)
  await click('运行图')
  await click('聚焦底部工作区')
  if(artifacts){
    const image=await send('Page.captureScreenshot',{format:'png'})
    await writeFile(join(artifacts,'operations.png'),Buffer.from(image.data,'base64'))
  }
  await send('Page.reload',{ignoreCache:true})
  await waitFor(()=>evaluate('document.querySelector(".snapshot")?.textContent.length>0'),'edited running plan restart')
  const persistedRun = (await ops('get_run',{id:run.id})).run
  assert.equal(persistedRun.plan_version,2)
  assert.notEqual(persistedRun.stops.at(-1).departure_time_s,run.stops.at(-1).departure_time_s)
  const received=await ops('import_timetable',{format:'gtfs',content:gtfs.content,service_date:'2026-11-21'})
  assert.equal(received.runs,2)
  const stress=[]
  if(process.env.RAILSCOPE_STRESS==='1'){
    const bundle=JSON.parse(exported.content)
    bundle.runs=Array.from({length:1000},()=>bundle.runs[0])
    const seeded=await ops('import_timetable',{format:'json',content:JSON.stringify(bundle)})
    assert.equal(seeded.runs,1000)
    const {runs}=await ops('list')
    const ids=runs.filter(run=>!run_ids.includes(run.id)&&run.corridor_id===corridor.id).slice(0,1000).map(run=>run.id)
    assert.equal(ids.length,1000)
    for(const count of [100,500,1000]){
      const stamp=performance.now()
      const states=await ops('simulate',{run_ids:ids.slice(0,count),time_s:28875,level:'l2'})
      assert.equal(states.states.length,count)
      stress.push({runs:count,ipc_ms:performance.now()-stamp,response_bytes:Buffer.byteLength(JSON.stringify(states))})
    }
  }
  return {source_vertices_sampled:source.rows.length,business_line_split:made.created,business_line_merge:merged.created,
    corridor_edges:corridor.edge_refs.length,
    preserved_corridor_version:1,
    run_count:simulated.states.length, resource_conflicts:analysis.conflicts.length,gtfs_roundtrip:received.runs,stress}
}

async function verifyHistoricalRun(path) {
  const fixture = JSON.parse(await readFile(path,'utf8'))
  await waitFor(()=>evaluate('Boolean(window.__TAURI_INTERNALS__?.invoke)'),'native reference API')
  const snapshot = (await invoke('list_data_packs')).active.snapshot
  assert.equal(snapshot, fixture.corridor.snapshot_id)
  const ops = (action, args = {}) => invoke('operations', {snapshot, request: {action, ...args}})
  const corridor = await ops('save_corridor', {corridor:fixture.corridor})
  assert.equal(corridor.verification_status,'automatic_reference')
  assert.equal(corridor.edge_refs.length,fixture.corridor.edge_refs.length)
  const bundle = fixture.bundle
  const plan = bundle.runs[0]
  plan.corridor_id = corridor.id
  plan.corridor_version = corridor.version
  const imported = await ops('import_timetable', {format:'json',content:JSON.stringify(bundle)})
  assert.equal(imported.runs,1)
  const run = (await ops('list')).runs.find(item => item.corridor_id === corridor.id)
  const detail = await ops('get_run', {id:run.id})
  assert.equal(detail.chart.length,plan.stops.length)
  assert.ok(detail.chart.every((stop,index) => Number.isFinite(stop.distance_m) && (!index || stop.distance_m > detail.chart[index-1].distance_m)))
  const time_s = plan.stops[0].departure_time_s + (detail.service.mode === 'metro' ? 45 : 600)
  const {states} = await ops('simulate', {run_ids:[run.id],time_s,level:'l2'})
  assert.equal(states[0].status, 'moving')
  assert.ok(Number.isFinite(states[0].lon) && Number.isFinite(states[0].speed_mps))
  return {snapshot, run:run.train_number, historical_service_date:run.service_date,
    corridor_edges:corridor.edge_refs.length, total_m:corridor.edge_refs.at(-1).end_distance_m,
    chart:detail.chart.map(({station_name, distance_m, arrival_time_s,departure_time_s}) => ({station_name,distance_m,arrival_time_s,departure_time_s})),
    reference_anchors:fixture.reference_anchors, sample:states[0]}
}

async function captureReferenceRun(artifactFolder, result) {
  await send('Page.enable')
  await send('Page.reload',{ignoreCache:true})
  await waitFor(()=>evaluate('document.querySelector(".snapshot")?.textContent.length>0'),'reference snapshot')
  await click('运行')
  if (result.run.includes('地铁')) await click('城市轨道')
  await click('车次')
  await input('[aria-label=运营日期]', result.historical_service_date)
  await waitFor(()=>evaluate(`Boolean([...document.querySelectorAll('.ops-list-inner button')].find(row=>row.textContent.includes(${JSON.stringify(result.run)})))`),'reference run details')
  await evaluate(`[...document.querySelectorAll('.ops-list-inner button')].find(row=>row.textContent.includes(${JSON.stringify(result.run)})).click()`)
  if(result.run.includes('地铁')){
    await waitFor(()=>evaluate('Boolean(document.querySelector(".metro-diagram svg .metro-plan"))'),'Shanghai metro diagram')
    const metro=await send('Page.captureScreenshot',{format:'png'})
    await writeFile(join(artifactFolder,'metro-diagram.png'),Buffer.from(metro.data,'base64'))
    await click('运行图')
  }
  await waitFor(()=>evaluate('Boolean(document.querySelector(".run-graph svg"))'),'reference time–distance diagram')
  const seconds = Math.floor(result.sample.time_s)
  const time = [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60]
    .map(part => String(part).padStart(2,'0')).join(':')
  await input('[placeholder^="HH:MM:SS"]', time)
  await click('跳转')
  await waitFor(()=>evaluate(`document.querySelector('[aria-label="运行时钟"]')?.textContent===${JSON.stringify(time)}`),'reference clock seek')
  await jump(result.sample.lon,result.sample.lat,13)
  await sleep(800)
  const image=await send('Page.captureScreenshot',{format:'png'})
  await writeFile(join(artifactFolder,'reference-run.png'),Buffer.from(image.data,'base64'))
}

async function verifyWorkbenchRefresh(planPath) {
  const snapshot = 'shanghai-osm-202609-v4'
  await waitFor(() => evaluate('Boolean(window.__TAURI_INTERNALS__?.invoke)'), 'Workbench API')
  await invoke('activate_data_pack', {snapshot})
  await send('Page.reload', {ignoreCache:true})
  await waitFor(() => evaluate('Boolean(document.querySelector(".snapshot")?.textContent.includes("shanghai-osm-202609-v4"))'), 'Shanghai snapshot')
  await select('.top .language-select select', 'zh')
  await evaluate('document.querySelector(".welcome-dialog button[aria-label=退出介绍]")?.click()')

  await evaluate('[...document.querySelectorAll(".categories button")].find(button=>button.querySelector("span")?.textContent==="车站").click()')
  await click('地铁车站')
  assert.ok(await evaluate('document.querySelector(".catalog-subtypes [aria-pressed=true]").textContent==="地铁车站"'))
  await input('input[aria-label="搜索交通实体"]', '美兰湖')
  await waitFor(() => evaluate('Boolean(document.querySelector(".result-row strong")?.textContent.includes("美兰湖"))'), 'Station directory result')
  await evaluate('document.querySelector(".result-row").click()')
  await waitFor(() => evaluate('document.querySelector(".object-head h3")?.textContent==="美兰湖"'), 'Station overview')
  assert.deepEqual(await evaluate('[...document.querySelectorAll(".property-tabs button")].map(button=>button.innerText)'), ['概览','关联','来源','编辑'])
  await click('来源')
  await evaluate('document.querySelector(".properties details").open=true')
  await waitFor(() => evaluate('Boolean(document.querySelector(".raw-source-tags > div"))'), 'Raw tags')
  assert.ok(await evaluate('document.querySelector(".inspector").scrollWidth<=document.querySelector(".inspector").clientWidth+1'))
  await click('关联')
  await input('.manual-links input', '7号线')
  await waitFor(() => evaluate('Boolean(document.querySelector(".manual-links > button"))'), 'Line search for station association')
  await evaluate('document.querySelector(".manual-links > button").click()')
  await click('保存工作区关联')
  await waitFor(() => evaluate('Boolean(document.querySelector(".properties .success"))'), 'Station association saved')
  const station = (await invoke('catalog_page', {snapshot,filter:{kind:'station',classes:['metro_station'],query:'美兰湖'}})).items[0]
  const relation = (await invoke('get_entity', {snapshot,id:station.id})).workspace.value.associated_lines[0]
  const linked = await invoke('get_entity', {snapshot,id:relation})
  assert.ok(linked.workspace_stations.some(item => item.id===station.id))
  if (artifacts) {
    await mkdir(artifacts, {recursive:true})
    const image = await send('Page.captureScreenshot', {format:'png'})
    await writeFile(join(artifacts,'inspector.png'), Buffer.from(image.data,'base64'))
  }
  await evaluate('document.querySelector(".manual-link .station-link").click()')
  await waitFor(() => evaluate('Boolean(document.querySelector(".object-head h3")?.textContent.includes("7号线"))'), 'Open linked line')
  await click('编辑')
  await input('.edit-form input', '7号线测试工作区名称')
  await input('.edit-form input[list="workspace-group-names"]', '上海城市轨道测试分组')
  await evaluate('document.querySelector(".edit-actions .primary").click()')
  await waitFor(() => evaluate('document.querySelector(".object-head h3")?.textContent==="7号线测试工作区名称"'), 'Workspace line rename')
  const renamed = await invoke('catalog_page', {snapshot,filter:{kind:'line',query:'测试工作区名称'}})
  assert.equal(renamed.items[0].id, relation)
  assert.ok((await invoke('workspace_groups', {snapshot})).some(group=>group.name==='上海城市轨道测试分组'))

  await click('运行')
  await click('城市轨道')
  assert.equal(await evaluate('document.querySelector(".properties").closest("[hidden]")!==null'), true)
  await click('计划与车辆')
  const {root} = await send('DOM.getDocument')
  const {nodeId} = await send('DOM.querySelector', {nodeId:root.nodeId,selector:'.operations-dialog input[aria-label="选择时刻表文件"]'})
  await send('DOM.setFileInputFiles', {nodeId,files:[planPath]})
  await click('导入')
  await waitFor(() => evaluate('document.querySelector("[aria-label=运营日期]")?.value==="2026-11-23"'), 'Line 7 plan imported in UI', 300)
  await waitFor(() => evaluate('Boolean(document.querySelector(".ops-list-inner button"))'), 'Line 7 run list')
  await evaluate('document.querySelector(".ops-list-inner button").click()')
  await waitFor(() => evaluate('Boolean(document.querySelector(".metro-plan.return"))'), 'Both Line 7 directions on a shared station axis')
  await waitFor(() => evaluate('Boolean([...document.querySelectorAll(".ops-inspection button")].find(button=>button.innerText.includes("从发车开始模拟")&&!button.disabled))'), 'Line 7 run detail')
  await click('从发车开始模拟')
  await waitFor(() => evaluate('Boolean(document.querySelector(".statusbar .status-action"))'), 'Line 7 clock running')
  if (artifacts) {
    const image = await send('Page.captureScreenshot', {format:'png'})
    await writeFile(join(artifacts,'shanghai-operations.png'), Buffer.from(image.data,'base64'))
  }
  console.log(JSON.stringify({snapshot,station_link:true,workspace_rename:true,source_overflow:false,line7_import_and_play:true}))
}

async function verifyNationalOperationsUI() {
  const snapshot='china-osm-202609-v3'
  await waitFor(()=>evaluate('Boolean(window.__TAURI_INTERNALS__ && document.querySelector(".snapshot"))'),'native workspace')
  assert.equal((await invoke('list_data_packs')).active.snapshot,snapshot)
  await select('.top .language-select select','zh')
  await evaluate('document.querySelector(".welcome-dialog button[aria-label=退出介绍]")?.click()')
  await click('运行')
  await click('城市轨道')
  await input('[aria-label=运营日期]','2026-11-26')
  await waitFor(()=>evaluate('document.querySelector(".ops-summary").textContent.includes("434")'),'preloaded national metro plans')
  assert.deepEqual(await evaluate('[...document.querySelectorAll(".ops-workbench [role=tab]")].map(button=>button.textContent)'), ['车次','通道'])
  assert.equal(await evaluate('document.querySelector(".ops-workbench input[aria-label=选择时刻表文件]")'), null)
  await click('仿真场景')
  await waitFor(()=>evaluate('Boolean(document.querySelector(".operations-dialog[open] input[type=number]"))'),'scenario settings dialog')
  assert.equal(await evaluate('Boolean(document.querySelector(".ops-workbench input[type=number]"))'), false)
  await evaluate('document.querySelector(".operations-dialog header button").click()')
  await click('计划与车辆')
  await waitFor(()=>evaluate('Boolean(document.querySelector(".operations-dialog[open] input[type=file]"))'),'optional plans dialog')
  await evaluate('document.querySelector(".operations-dialog header button").click()')
  const trainLayer='document.querySelector(".map-tools input[aria-label=动态列车]")'
  await evaluate(`if(${trainLayer}.checked)${trainLayer}.click()`)
  await waitFor(()=>evaluate(`!${trainLayer}.checked`),'train layer turned off before playback')
  await click('播放当日计划')
  await waitFor(()=>evaluate('Boolean(document.querySelector(".statusbar .status-action") && document.querySelector(".metro-plan.return"))'),
    'national snapshot playback and both directions')
  await waitFor(()=>evaluate(`${trainLayer}.checked`),'playback enables the train layer')
  await waitFor(()=>evaluate('Number.parseFloat(document.querySelector(".coordinates").textContent)>120.6 && Number.parseFloat(document.querySelector(".coordinates").textContent)<122.3'),
    'Playback locates Shanghai within the nationwide map')
  await waitFor(()=>evaluate('document.querySelector(".ops-inspection").textContent.includes("运行中")'), 'Moving metro train status')
  await evaluate(`${trainLayer}.click()`)
  await waitFor(()=>evaluate(`!${trainLayer}.checked`),'train layer can be turned off during playback')
  await click('定位列车')
  await waitFor(()=>evaluate(`${trainLayer}.checked`),'locating the train enables its layer again')
  assert.ok(await evaluate('Boolean(document.querySelector(".ops-bottom [aria-label=运行时钟]"))'))
  if(artifacts){
    await mkdir(artifacts,{recursive:true})
    const image=await send('Page.captureScreenshot',{format:'png'})
    await writeFile(join(artifacts,'national-metro.png'),Buffer.from(image.data,'base64'))
  }
  await click('编辑时刻')
  await waitFor(()=>evaluate('Boolean(document.querySelector(".operations-dialog.map-editor[open] .ops-editor"))'),'floating timetable editor')
  assert.equal(await evaluate('Boolean(document.querySelector(".ops-bottom .ops-editor"))'),false)
  await evaluate('[...document.querySelectorAll(".operations-dialog .ops-editor button")].find(button=>button.textContent==="取消").click()')
  await waitFor(()=>evaluate('!document.querySelector(".operations-dialog") && !document.querySelector(".ops-side-tools button:nth-child(3)")'),
    'Timetable draft cancelled')
  await evaluate('document.querySelector(".operation-modes button:first-child").click()')
  await waitFor(()=>evaluate('document.querySelector(".ops-list").textContent.includes("G1")'),'preloaded G1 plan',60)
  console.log(JSON.stringify({snapshot,metroRuns:434,timetableInDialog:true,metroPlayback:true,preloadedG1:true}))
}

if (process.env.RAILSCOPE_NATIONAL_OPERATIONS_UI === '1') {
  try {
    await verifyNationalOperationsUI()
  } finally {
    socket.close()
  }
} else if (process.env.RAILSCOPE_WORKBENCH_PLAN) {
  try {
    await verifyWorkbenchRefresh(process.env.RAILSCOPE_WORKBENCH_PLAN)
  } finally {
    socket.close()
  }
} else if (process.env.RAILSCOPE_REFERENCE_FIXTURE) {
  try {
    const result = await verifyHistoricalRun(process.env.RAILSCOPE_REFERENCE_FIXTURE)
    if (artifacts) {
      await mkdir(artifacts,{recursive:true})
      await writeFile(join(artifacts,'reference-run.json'),JSON.stringify(result,null,2)+'\n')
      if (process.env.RAILSCOPE_REFERENCE_UI === '1') await captureReferenceRun(artifacts, result)
    }
    console.log(JSON.stringify({snapshot:result.snapshot,run:result.run,stops:result.chart.length,edges:result.corridor_edges,
      length_km:result.total_m/1000,simulation:result.sample.status}))
  } finally {
    socket.close()
  }
} else try {
  const hasCanvas = () => evaluate('Boolean(window.__TAURI_INTERNALS__ && document.querySelector("canvas"))')
  await waitFor(hasCanvas, 'initial map canvas')
  await send('Network.enable')
  await send('Network.setCacheDisabled', { cacheDisabled: true })
  await send('Fetch.enable', { patterns: [{ urlPattern: 'http://*' }, { urlPattern: 'https://*' }] })
  await send('Page.enable')
  await send('Page.reload', { ignoreCache: true })
  await waitFor(hasCanvas, 'offline map canvas')
  const starter = process.env.RAILSCOPE_STARTER_SMOKE === '1'
  if (starter) await verifyStarter()
  else {
    await select('.top .language-select select', 'zh')
    await waitFor(() => evaluate('!document.querySelector(".map-loading")'),'first-run startup')
    await evaluate('document.querySelector(".welcome-dialog button[aria-label=退出介绍]")?.click()')
  }
  const sandbox = process.env.RAILSCOPE_SMOKE_WRITABLE === '1'
  const sourcePack = process.env.RAILSCOPE_INSTALL_FIXTURE
  if (sourcePack) {
    const job = crypto.randomUUID()
    const installed = await invoke('install_data_pack', {path:sourcePack,job})
    assert.ok(installed.snapshot)
    await invoke('activate_data_pack', {snapshot:installed.snapshot})
    await send('Page.reload', {ignoreCache:true})
    await waitFor(() => evaluate(`document.querySelector('.snapshot')?.textContent===${JSON.stringify(installed.snapshot)}`), 'installed snapshot in workbench')
  }
  const listing = await invoke('list_data_packs')
  assert.ok(listing.active?.counts.station)
  const snapshot = listing.active.snapshot
  await waitFor(async () => (await invoke('diagnostics'))?.misses > 0, 'offline PMTiles requests')
  if (artifacts) {
    await mkdir(artifacts, { recursive: true })
    await sleep(850)
    const overview = await send('Page.captureScreenshot', { format: 'png' })
    await writeFile(join(artifacts, 'overview.png'), Buffer.from(overview.data, 'base64'))
  }
  const readStart = performance.now()
  const matches = await invoke('catalog_page', {
    snapshot, filter: { query: '上海虹桥', kind: 'station', limit: 10 },
  })
  const elapsed = performance.now() - readStart
  assert.ok(matches.items.length > 0, 'Chinese FTS search')
  const pinyin = await invoke('catalog_page', { snapshot, filter: { query: 'shanghaihongqiao', kind: 'station' } })
  assert.ok(pinyin.items.length > 0, 'pinyin search')
  const first = matches.items[0]
  const detail = await invoke('get_entity', { snapshot, id: String(first.render_id) })
  assert.equal(detail.entity.id, first.id)
  const geometry = await invoke('get_geometry', { snapshot, id: first.id })
  assert.ok(geometry?.type, 'original geometry')
  await input('input[aria-label="搜索交通实体"]', '上海虹桥')
  await waitFor(() => evaluate('document.querySelector(".result-row")?.innerText.includes("虹桥")'), 'search results in workbench')
  await evaluate('document.querySelector(".result-row").click()')
  await waitFor(() => evaluate('document.querySelector(".object-head h3")?.textContent.includes("虹桥")'), 'inspector')
  await click('查看精确几何')
  if (sandbox) {
    await click('编辑')
    await input('.edit-form textarea', '离线工作区验收')
    await input('.edit-form input[placeholder=留空则移出分组]', '验收分组')
    await click('保存')
    await waitFor(() => evaluate('document.querySelector(".success")?.textContent.includes("已保存")'), 'workspace save')
    assert.equal((await invoke('get_entity', { snapshot, id: first.id })).workspace.value.note, '离线工作区验收')
    assert.ok((await invoke('workspace_group_members', { snapshot, group: '验收分组' })).items.some(item => item.id === first.id))
    await click('撤销')
    await waitFor(() => evaluate('document.querySelector(".success")?.textContent.includes("已撤销")'), 'workspace undo')
    assert.equal((await invoke('get_entity', { snapshot, id: first.id })).workspace.value.note, detail.workspace.value.note)
    await click('重做')
    await waitFor(() => evaluate('document.querySelector(".success")?.textContent.includes("已重做")'), 'workspace redo')
    assert.equal((await invoke('get_entity', { snapshot, id: first.id })).workspace.value.note, '离线工作区验收')
    await click('来源')
  }
  await click('图层')
  const selector = '.layer-control input'
  const before = await evaluate(`document.querySelector('${selector}').checked`)
  await evaluate(`document.querySelector('${selector}').click()`)
  assert.equal(await evaluate(`document.querySelector('${selector}').checked`), !before)
  await evaluate(`document.querySelector('${selector}').click()`)
  await click('目录')
  const count = await evaluate('document.querySelectorAll(".result-row").length')
  assert.ok(count < 80, 'virtual list window')
  const errors = await evaluate('[...document.querySelectorAll(".map-error, .error-text")].map(e => e.innerText)')
  assert.deepEqual(errors, [])
  assert.deepEqual(remote, [])
  const switchTo = process.env.RAILSCOPE_SMOKE_SWITCH
  let v3 = null
  let national = null
  const v4 = process.env.RAILSCOPE_V4_WORKBENCH === '1' ? await verifyV4(snapshot) : null
  if(process.env.RAILSCOPE_V3_FIXTURE) v3=await verifyV3(snapshot,process.env.RAILSCOPE_V3_FIXTURE)
  if (switchTo) {
    const selected = listing.packs.find(pack => pack.snapshot === switchTo)
    assert.ok(selected, 'target data pack available')
    const activate = async id => {
      await evaluate('document.querySelector(".pack-trigger").click()')
      await evaluate(`[...document.querySelectorAll(".pack-choice")].find(
        b => b.querySelector("small")?.textContent === ${JSON.stringify(id)}
      )?.click()`)
      await waitFor(() => evaluate(`document.querySelector(".snapshot")?.textContent === ${JSON.stringify(id)}`), 'data pack switch')
      await waitFor(async () => (await invoke('diagnostics'))?.misses > 0, 'tiles after pack switch')
    }
    await activate(switchTo)
    assert.equal((await invoke('list_data_packs')).active.snapshot, switchTo)
    if (selected.region === '全国') {
      await click('地图')
      await jump(106, 34, 4.2)
      await waitFor(() => evaluate('Number(document.querySelector(".zoom")?.textContent.slice(2)) >= 3.5'), 'national transport zoom')
    }
    await sleep(1750)
    assert.deepEqual(await evaluate('[...document.querySelectorAll(".map-error")].map(e => e.innerText)'), [])
    if (artifacts) {
      const switched = await send('Page.captureScreenshot', { format: 'png' })
      await writeFile(join(artifacts, 'pack-switch.png'), Buffer.from(switched.data, 'base64'))
    }
    if (sandbox && selected.region === listing.active.region) {
      assert.equal((await invoke('get_entity', { snapshot: switchTo, id: first.id })).workspace.value.note, '离线工作区验收')
    }
    if (process.env.RAILSCOPE_NATIONAL_PRECISION === '1') national = await verifyNationalSource(switchTo)
    const minutes = Number(process.env.RAILSCOPE_ROAM_MINUTES)
    if (Number.isFinite(minutes) && minutes > 0) await roam(switchTo, minutes)
    await activate(snapshot)
  }
  const summary = { snapshot, elapsed_search_ms: elapsed, tile_stats: await invoke('diagnostics'), remote_requests: remote.length,
    workspace_exercised: sandbox, switched_pack: switchTo ?? null, v3, v4, national }
  assert.deepEqual(interceptionErrors, [], 'offline request interception')
  console.log(JSON.stringify(summary, null, 2))
  if (artifacts) {
    await sleep(480)
    const shot = await send('Page.captureScreenshot', { format: 'png' })
    await writeFile(join(artifacts, 'desktop.png'), Buffer.from(shot.data, 'base64'))
    await writeFile(join(artifacts, 'smoke.json'), JSON.stringify(summary, null, 2) + '\n')
  }
} finally {
  socket.close()
}
