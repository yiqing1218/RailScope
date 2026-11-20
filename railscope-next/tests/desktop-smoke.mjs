import { strict as assert } from 'node:assert'
import { mkdir, writeFile } from 'node:fs/promises'
import { join } from 'node:path'

const origin = process.env.RAILSCOPE_CDP || 'http://127.0.0.1:9229'
const artifacts = process.argv[2]
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))

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
    pending.set(id, { resolve, reject })
    socket.send(JSON.stringify({ id, method, params }))
  })
}
async function evaluate(expression) {
  const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description ?? result.exceptionDetails.text)
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

try {
  const hasCanvas = () => evaluate('Boolean(window.__TAURI_INTERNALS__ && document.querySelector("canvas"))')
  await waitFor(hasCanvas, 'initial map canvas')
  await send('Network.enable')
  await send('Network.setCacheDisabled', { cacheDisabled: true })
  await send('Fetch.enable', { patterns: [{ urlPattern: 'http://*' }, { urlPattern: 'https://*' }] })
  await send('Page.enable')
  await send('Page.reload', { ignoreCache: true })
  await waitFor(hasCanvas, 'offline map canvas')
  const sandbox = process.env.RAILSCOPE_SMOKE_WRITABLE === '1'
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
    await activate(snapshot)
  }
  const summary = { snapshot, elapsed_search_ms: elapsed, tile_stats: await invoke('diagnostics'), remote_requests: remote.length,
    workspace_exercised: sandbox, switched_pack: switchTo ?? null }
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
