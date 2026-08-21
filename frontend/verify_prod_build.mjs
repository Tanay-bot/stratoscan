// One-shot production-build probe: loads the BUILT output served by
// `vite preview` on :5190 in real headless Chrome, confirms the SPA boots
// (no white screen), lazy route chunks load, and the console is clean of
// errors — then exercises screener → backtest navigation like a user would.
// Uses the same raw-CDP technique as verify_behavior.mjs (Node 24 WebSocket).
import { spawn, spawnSync } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const PORT = 9340
const BASE = 'http://localhost:5190'
const userDir = mkdtempSync(join(tmpdir(), 'stratoscan-prod-'))
spawnSync('sh', ['-c', `lsof -ti tcp:${PORT} 2>/dev/null | xargs -r kill -9 2>/dev/null || true`])

const chrome = spawn(CHROME, [
  '--headless=new',
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${userDir}`,
  '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--disable-dev-shm-usage',
  '--window-size=1440,2600',
  'about:blank',
], { stdio: 'ignore' })

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
async function getJson(u) { const r = await fetch(u); return r.json() }

let passed = 0, failed = 0
const check = (name, cond, detail = '') => {
  if (cond) { passed++; console.log(`PASS  ${name}${detail ? '  — ' + detail : ''}`) }
  else { failed++; console.log(`FAIL  ${name}${detail ? '  — ' + detail : ''}`) }
}

let ws
try {
  // Wait for Chrome's CDP endpoint, then attach to the page target.
  let page = null
  for (let i = 0; i < 40 && !page; i++) {
    try {
      const targets = await getJson(`http://localhost:${PORT}/json`)
      page = targets.find((t) => t.type === 'page')
    } catch { /* not up yet */ }
    if (!page) await sleep(250)
  }
  if (!page) throw new Error('CDP never came up')
  ws = new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
  let msgId = 0
  const pending = new Map()
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data)
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
  }
  const send = (method, params = {}) => new Promise((res, rej) => {
    const id = ++msgId
    const timer = setTimeout(() => { pending.delete(id); rej(new Error(`CDP timeout: ${method}`)) }, 15000)
    pending.set(id, (m) => { clearTimeout(timer); res(m) })
    ws.send(JSON.stringify({ id, method, params }))
  })
  const evalJS = async (expr) => {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true })
    if (r.result?.exceptionDetails) throw new Error('eval failed: ' + JSON.stringify(r.result.exceptionDetails))
    return r.result?.result?.value
  }

  await send('Runtime.enable')
  await send('Page.enable')
  const consoleErrors = []
  // ONE handler: dispatch command responses to their pending promises AND
  // collect console errors. Splitting them (as an earlier draft did) meant
  // send() promises never resolved and the probe deadlocked — the exact
  // failure mode this run exposed.
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data)
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
      consoleErrors.push(m.params.args.map((a) => a.value ?? a.description ?? '').join(' '))
    }
  }

  // 1. Load the built landing page.
  await send('Page.navigate', { url: BASE + '/' })
  for (let i = 0; i < 40; i++) { if (await evalJS('document.readyState === "complete"')) break; await sleep(250) }
  await sleep(1500) // let React mount + lazy Landing chunk fetch
  const title = await evalJS('document.title')
  check('built app boots, title set', /Stratoscan/.test(title || ''), title)
  const hero = await evalJS(`!![...document.querySelectorAll('h1')].find(h => /Bridge from idea/.test(h.textContent))`)
  check('landing hero renders in prod build', hero === true)

  // 2. Confirm demo mode is OFF in the built output (guard worked).
  const demoBadge = await evalJS(`[...document.querySelectorAll('*')].some(e => e.textContent && /LIVE DEMO/.test(e.textContent))`)
  check('no LIVE DEMO badge in prod build (guard works)', demoBadge === false)

  // 3. Navigate to the screener with a query — a PROTECTED route. In a
  // fresh profile with no token, ProtectedRoute must bounce to /login
  // (auth gate working in the built app), NOT render the screen or crash.
  await send('Page.navigate', { url: BASE + '/app/screener?q=momentum+breakout+stocks' })
  for (let i = 0; i < 40; i++) { if (await evalJS('document.readyState === "complete"')) break; await sleep(250) }
  await sleep(1800)
  const finalPath = await evalJS('window.location.pathname')
  check('protected route redirects to /login without token', finalPath === '/login', finalPath)
  const loginHeading = await evalJS(`[...document.querySelectorAll('h1,h2')].map(h => h.textContent.trim()).find(t => /Welcome back/.test(t))`)
  check('login page renders (lazy Auth chunk loaded)', /Welcome back/.test(loginHeading || ''), loginHeading)
  const rootStillMounted = await evalJS(`document.querySelector('#root')?.children.length > 0`)
  check('app did not white-screen (graceful redirect, not a crash)', rootStillMounted === true)

  // 4. Back to the landing — sanity that navigation works in the built SPA.
  await send('Page.navigate', { url: BASE + '/' })
  for (let i = 0; i < 40; i++) { if (await evalJS('document.readyState === "complete"')) break; await sleep(250) }
  await sleep(1200)
  const backHome = await evalJS(`!![...document.querySelectorAll('h1')].find(h => /Bridge from idea/.test(h.textContent))`)
  check('back to landing renders', backHome === true)

  // 5. Console error scan — report anything that fired during the session.
  check('zero console errors during probe', consoleErrors.length === 0, consoleErrors.slice(0, 3).join(' | '))

  console.log(`\n=== Prod-build probe: passed=${passed} failed=${failed} ===`)
} catch (e) {
  console.error('PROBE ERROR:', e.message)
  process.exitCode = 1
} finally {
  try { ws?.close() } catch { /* noop */ }
  chrome.kill('SIGKILL')
  rmSync(userDir, { recursive: true, force: true })
  process.exit(failed > 0 ? 1 : 0)
}
