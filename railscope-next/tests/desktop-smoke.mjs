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
    if (localRequest(request.url)) void send('Fetch.continueRequest', { requestId })
    else void send('Fetch.failRequest', { requestId, errorReason: 'InternetDisconnected' })
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
  await click('诊断')
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
  await click('地图浏览')
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

async function verifyV3(snapshot, fixturePath) {
  const fixture=JSON.parse(await readFile(fixturePath,'utf8'))
  assert.equal(fixture.snapshot,snapshot)
  const ops=(action,args={})=>invoke('operations',{snapshot,request:{action,...args}})
  const source=await invoke('get_osm_source',{snapshot,reference:fixture.way_ref,offset:0})
  assert.ok(source.complete&&source.rows.length>1)
  assert.ok(source.rows.every(row=>Number.isFinite(row.longitude)))
  const results=await invoke('catalog_page',{snapshot,filter:{query:fixture.way_ref,limit:10}})
  assert.ok(results.items.some(row=>row.id===fixture.start_id))
  await evaluate('document.querySelector(".view-settings").open=true')
  await select('[aria-label=底图主题]','dark')
  await waitFor(()=>evaluate('document.querySelector(".app").dataset.theme==="dark"'),'local dark basemap')
  await select('[aria-label=底图主题]','light')
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
  const analysis=await ops('simulate',{run_ids,time_s:28875,level:'l3',resources:true})
  assert.ok(analysis.occupancies.length>=2&&analysis.conflicts.length>=1)
  assert.ok(analysis.diagnostics.some(note=>note.code==='UNVERIFIED_OPERATION_RULES'))
  const exported=await ops('export_timetable',{format:'json',run_ids})
  assert.equal(JSON.parse(exported.content).runs.length,2)
  const csv=await ops('export_timetable',{format:'csv',run_ids})
  assert.ok(csv.content.includes('arrival_time_s'))
  const gtfs=await ops('export_timetable',{format:'gtfs',run_ids})
  assert.equal(Buffer.from(gtfs.content,'base64').subarray(0,2).toString(),'PK')
  const received=await ops('import_timetable',{format:'gtfs',content:gtfs.content,service_date:'2026-11-21'})
  assert.equal(received.runs,2)
  await send('Page.reload',{ignoreCache:true})
  await waitFor(()=>evaluate('document.querySelector(".snapshot")?.textContent.length>0'),'workspace restart')
  await click('运行')
  await waitFor(()=>evaluate('document.querySelector(".ops-workbench")?.hidden===false'),'operations workbench')
  await click('车次')
  await input('[aria-label=运营日期]','2026-11-21')
  await waitFor(()=>evaluate('Boolean(document.querySelector(".ops-list-inner button"))'),'dated run rows')
  await evaluate('[...document.querySelectorAll(".ops-list-inner button")].find(el=>el.textContent.includes("示例"))?.click()')
  await waitFor(()=>evaluate('Boolean(document.querySelector(".run-graph svg"))'),'time-distance graph')
  await click('跳到发车')
  await click('播放模拟')
  await waitFor(()=>evaluate('Boolean(document.querySelector("[aria-label=暂停模拟]"))'),'Rust playback clock')
  await sleep(750)
  await click('暂停模拟')
  if(artifacts){
    const image=await send('Page.captureScreenshot',{format:'png'})
    await writeFile(join(artifacts,'operations.png'),Buffer.from(image.data,'base64'))
  }
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
  await click('车次')
  await input('[aria-label=运营日期]', result.historical_service_date)
  await waitFor(()=>evaluate(`Boolean([...document.querySelectorAll('.ops-list-inner button')].find(row=>row.textContent.includes(${JSON.stringify(result.run)})))`),'reference run details')
  await evaluate(`[...document.querySelectorAll('.ops-list-inner button')].find(row=>row.textContent.includes(${JSON.stringify(result.run)})).click()`)
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

if (process.env.RAILSCOPE_REFERENCE_FIXTURE) {
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
    await click('工作区')
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
    await click('来源与关联')
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
      await sleep(550)
      await evaluate('document.querySelector(".maplibregl-ctrl-zoom-in").click()')
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
    workspace_exercised: sandbox, switched_pack: switchTo ?? null, v3, national }
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
