// Behavioral verification for the Stratoscan landing/HowItWorks animation work.
// Drives a real headless Chrome via raw CDP (Node 24 global WebSocket, no deps).
// NOTE: the Mac OS has "Reduce Motion" enabled, so headless Chrome inherits
// prefers-reduced-motion: reduce -> the app correctly freezes animations.
// We emulate 'no-preference' via CDP BEFORE loading to exercise the live paths,
// then re-emulate 'reduce' at the end to verify the a11y fallback.

import { spawn, spawnSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

// CHROME_PATH (or Playwright's PW_CHROMIUM_PATH) overrides the macOS default.
const CHROME =
  process.env.CHROME_PATH ||
  process.env.PW_CHROMIUM_PATH ||
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const PORT = 9333
const VITE_PORT = 5180
const BASE = `http://localhost:${VITE_PORT}`
// The landing's rotating prompts are strategy-template queries (Day 8).
const TEMPLATE_PROMPTS = [
  ...readFileSync(
    join(fileURLToPath(new URL('.', import.meta.url)), 'src/lib/templates.ts'),
    'utf8',
  ).matchAll(/query:\s*'([^']+)'/g),
].map((m) => m[1])
const userDir = mkdtempSync(join(tmpdir(), 'stratoscan-cdp-'))

// Never attach to a stale Chrome leaked by a crashed previous run (it carries
// localStorage from an earlier section, corrupting auth-dependent assertions).
spawnSync('sh', ['-c', `lsof -ti tcp:${PORT} 2>/dev/null | xargs -r kill -9 2>/dev/null || true`])

// Hermetic: spawn our OWN Vite dev server in demo mode (the shipped default is
// live API mode — see .env — but this harness drives the synthetic data path),
// so it never depends on whatever server the user happens to have on :5173.
spawnSync('sh', [
  '-c',
  `lsof -ti tcp:${VITE_PORT} 2>/dev/null | xargs -r kill -9 2>/dev/null || true`,
])
const FRONTEND_DIR = fileURLToPath(new URL('.', import.meta.url))
// Spawn the LOCAL vite binary by absolute path — subagent shells may not have npx on PATH.
const vite = spawn(
  join(FRONTEND_DIR, 'node_modules', '.bin', 'vite'),
  ['--port', String(VITE_PORT), '--strictPort'],
  {
    cwd: FRONTEND_DIR,
    env: { ...process.env, VITE_DEMO_MODE: 'true', VITE_API_URL: '/api' },
    stdio: 'ignore',
  },
)
const viteReady = await (async () => {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`${BASE}/`)
      if (r.ok) return true
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 250))
  }
  return false
})()
if (!viteReady) {
  console.error('HARNESS: own Vite server never came up on :' + VITE_PORT)
  process.exit(1)
}

const chrome = spawn(
  CHROME,
  [
    '--headless=new',
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${userDir}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-gpu',
    '--disable-dev-shm-usage',
    // Chromium refuses to start as root without this (CI containers, sandboxes).
    ...(process.getuid?.() === 0 ? ['--no-sandbox'] : []),
    '--window-size=1440,2600',
    'about:blank',
  ],
  { stdio: 'ignore' },
)

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function getTarget() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json`)
      const list = await r.json()
      const page = list.find((t) => t.type === 'page')
      if (page) return page
    } catch {
      /* not up yet */
    }
    await sleep(200)
  }
  throw new Error('Chrome CDP target never appeared')
}

const page = await getTarget()
const ws = new WebSocket(page.webSocketDebuggerUrl)
await new Promise((res, rej) => {
  ws.onopen = res
  ws.onerror = rej
})

let msgId = 0
const pending = new Map()
const consoleErrors = []
const pageErrors = []

ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data)
  if (msg.id && pending.has(msg.id)) {
    const p = pending.get(msg.id)
    pending.delete(msg.id)
    if (msg.error) p.reject(new Error(msg.error.message))
    else p.resolve(msg.result)
    return
  }
  if (msg.method === 'Runtime.exceptionThrown') {
    pageErrors.push(msg.params.exceptionDetails?.exception?.description ?? 'exception')
  }
  if (msg.method === 'Runtime.consoleAPICalled' && msg.params.type === 'error') {
    consoleErrors.push(msg.params.args.map((a) => a.value ?? a.description ?? '').join(' '))
  }
}

// Always reap the browser + temp dir + own vite server, even if a later step throws.
const cleanup = () => {
  try {
    ws.close()
  } catch {}
  try {
    chrome.kill('SIGKILL')
  } catch {}
  try {
    vite.kill('SIGKILL')
  } catch {}
  try {
    rmSync(userDir, { recursive: true, force: true })
  } catch {}
}
process.on('exit', cleanup)
process.on('uncaughtException', (err) => {
  cleanup()
  console.error('HARNESS CRASH:', err)
  process.exit(1)
})
process.on('unhandledRejection', (err) => {
  cleanup()
  console.error('HARNESS CRASH:', err)
  process.exit(1)
})

function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++msgId
    pending.set(id, { resolve, reject })
    ws.send(JSON.stringify({ id, method, params }))
  })
}

async function evalJS(expression) {
  const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
  if (r.exceptionDetails)
    throw new Error('eval failed: ' + JSON.stringify(r.exceptionDetails).slice(0, 400))
  return r.result.value
}

const results = []
function check(name, cond, detail = '') {
  results.push({ name, ok: !!cond })
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`)
}

async function poll(expr, expect, timeoutMs = 5000) {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    if ((await evalJS(expr)) === expect) return true
    await sleep(120)
  }
  return false
}

await send('Page.enable')
await send('Runtime.enable')

async function navigate(url, readySelector = 'input[aria-label="Idea to test"]') {
  await send('Page.navigate', { url })
  for (let i = 0; i < 100; i++) {
    if (await evalJS("document.readyState === 'complete'")) break
    await sleep(100)
  }
  for (let i = 0; i < 80; i++) {
    if (await evalJS(`!!document.querySelector(${JSON.stringify(readySelector)})`)) break
    await sleep(100)
  }
}

// Force motion ON before the page's modules read matchMedia at load time.
await send('Emulation.setEmulatedMedia', {
  features: [{ name: 'prefers-reduced-motion', value: 'no-preference' }],
})

// ─────────────────────────────────────────────────────────────────────────────
console.log('\n=== 0. Env probe ===')
await navigate(BASE + '/')
const reduceNow = await evalJS("matchMedia('(prefers-reduced-motion: reduce)').matches")
check(
  'env: reduced-motion emulated OFF (animations will run)',
  reduceNow === false,
  `reduce=${reduceNow}`,
)

// ─────────────────────────────────────────────────────────────────────────────
console.log('\n=== 1. Landing: hero market-floor is LIVE ===')
// Chart draw-in: sample the reveal clip rect's width rapidly after load to catch it mid-flight
const revealW = []
for (let i = 0; i < 8; i++) {
  revealW.push(
    await evalJS("parseFloat(getComputedStyle(document.querySelector('[data-reveal]')).width)"),
  )
  await sleep(120)
}
const caughtMid = revealW.some((w) => w > 0 && w < 1200)
const revealLate = parseFloat(
  await evalJS("getComputedStyle(document.querySelector('[data-reveal]')).width"),
)
const revealAnim = await evalJS(
  "getComputedStyle(document.querySelector('[data-reveal]')).animationName",
)
check(
  'hero: chart draws in left→right on first load',
  (caughtMid || revealLate >= 1200) && revealAnim === 'hero-reveal',
  `mid=${caughtMid} end=${revealLate} anim=${revealAnim} samples=[${revealW.join(',')}]`,
)
check(
  'hero: candlestick chart painted',
  (await evalJS("document.querySelectorAll('rect[data-candle]').length")) >= 30,
)
check(
  'hero: close-line + gradient area drawn',
  await evalJS(
    "!!document.querySelector('path[data-closeline]') && !!document.querySelector('path[data-area]')",
  ),
)
check('hero: now-beam present', await evalJS("!!document.querySelector('[data-beam]')"))
check(
  'hero: price gridlines + labels',
  await evalJS(
    "document.querySelectorAll('line[data-grid]').length >= 3 && document.querySelectorAll('[data-gridlabel]').length >= 3",
  ),
)

const cc = []
for (let i = 0; i < 5; i++) {
  cc.push(await evalJS("document.querySelectorAll('rect[data-candle]').length"))
  await sleep(700)
}
check('hero: tape scrolls (candle window slides)', new Set(cc).size >= 2, cc.join(' -> '))
// Flash remounts each tick (1.2s) and animates opacity 0.85→0 over 950ms.
// NOTE: headless Chrome marks the tab occluded after navigation and suspends
// rAF, which freezes CSS animations (currentTime stays 0ms) — the same issue
// section 7 hits. Wake the tab FIRST, then sample at fine granularity across
// a full tick cycle and require the values to vary.
await send('Page.bringToFront')
await sleep(300)
const flashSamples = []
for (let i = 0; i < 10; i++) {
  flashSamples.push(
    await evalJS(`(() => {
    const el = document.querySelector('circle[data-flash]')
    if (!el) return 'none'
    return getComputedStyle(el).opacity
  })()`),
  )
  await sleep(150)
}
check(
  'hero: trade flash pulses at newest candle',
  new Set(flashSamples).size >= 2,
  flashSamples.join(' -> '),
)

// ─────────────────────────────────────────────────────────────────────────────
console.log('\n=== 2. Landing: typewriter lifecycle ===')
// NOTE: a FIXED sampling window collides with the typewriter's 2400ms hold phase
// (a working typewriter can look frozen if every sample lands in the hold) — the
// same trap line ~544 documents. Sample across 12 × 400ms = 4.8s, which always
// spans a hold + the following delete/type transitions, so a working typewriter
// yields >= 3 distinct values and a genuinely frozen one still fails.
const samples = []
for (let i = 0; i < 12; i++) {
  samples.push(await evalJS('document.querySelector(\'input[aria-label="Idea to test"]\').value'))
  await sleep(400)
}
const distinct = new Set(samples)
check(
  'typewriter: value changes over 4.8s (typing)',
  distinct.size >= 3,
  samples.map((s) => s.slice(0, 24)).join(' | '),
)

const FULL_PROMPTS = [
  'find undervalued small-cap tech stocks',
  'high-dividend energy companies',
  'momentum breakout stocks',
  'oversold healthcare with low beta',
]
let sawFull = false
let sawFullPrev = ''
let sawDelete = false
let sawNext = false
for (let i = 0; i < 50; i++) {
  const v = await evalJS('document.querySelector(\'input[aria-label="Idea to test"]\').value')
  if (FULL_PROMPTS.includes(v)) {
    if (!sawFull) {
      sawFull = true
      sawFullPrev = v
    } else if (v !== sawFullPrev) {
      sawNext = true
    }
  }
  if (sawFull && v.length > 0 && v.length < 20) sawDelete = true
  if (sawFull && sawDelete && sawNext) break
  await sleep(300)
}
check('typewriter: reaches a full prompt (type phase)', sawFull)
check('typewriter: deletes back down (delete phase)', sawDelete)
check('typewriter: advances to next prompt', sawNext)
check(
  'typewriter: blinking block caret rendered',
  await evalJS('!!document.querySelector(\'span[style*="caret-blink"]\')'),
)

// ─────────────────────────────────────────────────────────────────────────────
console.log('\n=== 3. Landing: focus/blur/submit behavior (real CDP mouse/keyboard) ===')
// NOTE: programmatic el.focus() does not fire React's synthetic focusin in
// headless Chrome, so we simulate real clicks via the CDP Input domain.
const click = async (expr) => {
  const { x, y } = await evalJS(
    `(() => { const r = ${expr}.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 } })()`,
  )
  await send('Input.dispatchMouseEvent', {
    type: 'mousePressed',
    x,
    y,
    button: 'left',
    clickCount: 1,
  })
  await send('Input.dispatchMouseEvent', {
    type: 'mouseReleased',
    x,
    y,
    button: 'left',
    clickCount: 1,
  })
}
const inputExpr = 'document.querySelector(\'input[aria-label="Idea to test"]\')'
const h1Expr = "document.querySelector('h1')"

// Wait until the typewriter is mid-text (deterministic start), THEN click in.
await poll(`${inputExpr}.value.length > 5`, true, 6000)
const preFocus = await evalJS(`${inputExpr}.value`)
await click(inputExpr)
await sleep(200)
const afterFocus = await evalJS(`${inputExpr}.value`)
check(
  'focus: field clears for user typing',
  afterFocus === '',
  `pre='${preFocus.slice(0, 20)}' post='${afterFocus}'`,
)
const v1 = await evalJS(`${inputExpr}.value`)
await sleep(500)
const v2 = await evalJS(`${inputExpr}.value`)
check('focus: rotation paused while focused', v1 === v2, `'${v1}' vs '${v2}'`)

// Type like a real user (CDP insertText -> real input event)
await send('Input.insertText', { text: 'AAPL momentum' })
await sleep(150)
check('user text: typed value sticks', (await evalJS(`${inputExpr}.value`)) === 'AAPL momentum')
await click(h1Expr) // blur by clicking the headline
await sleep(400)
check(
  'blur with text: keeps user text (no overwrite)',
  (await evalJS(`${inputExpr}.value`)) === 'AAPL momentum',
)

// Focus again, clear while focused (real key events), then blur -> rotation resumes
await click(inputExpr)
await sleep(150)
await send('Input.dispatchKeyEvent', {
  type: 'keyDown',
  modifiers: 2,
  key: 'a',
  code: 'KeyA',
  windowsVirtualKeyCode: 65,
}) // meta/ctrl+A
await send('Input.dispatchKeyEvent', {
  type: 'keyUp',
  modifiers: 2,
  key: 'a',
  code: 'KeyA',
  windowsVirtualKeyCode: 65,
})
await send('Input.dispatchKeyEvent', {
  type: 'keyDown',
  key: 'Backspace',
  code: 'Backspace',
  windowsVirtualKeyCode: 8,
})
await send('Input.dispatchKeyEvent', {
  type: 'keyUp',
  key: 'Backspace',
  code: 'Backspace',
  windowsVirtualKeyCode: 8,
})
await sleep(150)
await click(h1Expr)
const resumed = await poll(`${inputExpr}.value.length > 0`, true, 4000)
check('blur with empty: rotation resumes', resumed)

// Submit while auto-typing: must open a trial backtest with the FULL current
// prompt (Day 6: no signup wall; Day 8: the prompts are template queries).
await navigate(BASE + '/')
await sleep(1400)
const partialNow = await evalJS(
  'document.querySelector(\'input[aria-label="Idea to test"]\').value',
)
await evalJS('document.querySelector(\'form[role="search"] button[type="submit"]\').click()')
await sleep(900)
const qInNext = await evalJS("new URLSearchParams(location.search).get('q')")
const fullPrompts = TEMPLATE_PROMPTS
check(
  'submit mid-type: opens a trial backtest',
  (await evalJS('location.pathname')) === '/try',
  `path=${await evalJS('location.pathname')}`,
)
check(
  'submit mid-type: q is a COMPLETE prompt',
  fullPrompts.includes(qInNext),
  `partial='${partialNow.slice(0, 28)}…' q='${qInNext}'`,
)

// ─────────────────────────────────────────────────────────────────────────────
console.log('\n=== 4. Landing: BacktestShowcase behaves ===')
await navigate(BASE + '/')
await evalJS(
  "document.querySelector('[aria-label=\"Illustrative equity curve versus SPY benchmark\"]').scrollIntoView({ block: 'center' })",
)
await sleep(3300)

// The count-up runs over 1.4s once in view; a fixed sample can land mid-count
// (+38.1%). Poll until the counter lands on its final value instead.
const kpiDone = await poll(
  "(() => { const card = document.querySelector('[aria-label=\"Illustrative equity curve versus SPY benchmark\"]')?.closest('.glass-strong'); if (!card) return false; return [...card.querySelectorAll('p')].some(p => p.textContent.trim() === '+38.2%') })()",
  true,
  8000,
)
check('showcase: KPI values counted up (Total return +38.2%)', kpiDone)
check(
  'showcase: all four KPI labels rendered',
  await evalJS(`
  (() => {
    const card = document.querySelector('[aria-label="Illustrative equity curve versus SPY benchmark"]').closest('.glass-strong')
    return ['Total return', 'Sharpe', 'Max drawdown', 'Win rate'].every(k => card.innerText.includes(k))
  })()
`),
)
check(
  'showcase: strategy line + area + benchmark + end dot drawn',
  await evalJS(`
  (() => {
    const svg = document.querySelector('[aria-label="Illustrative equity curve versus SPY benchmark"]')
    return svg.querySelectorAll('path').length >= 3 && !!svg.querySelector('circle')
  })()
`),
)
const progress = await evalJS(`
  (() => {
    const card = document.querySelector('[aria-label="Illustrative equity curve versus SPY benchmark"]').closest('.glass-strong')
    const bar = card.querySelector('.bg-gradient-to-r.from-neon.to-grape')
    return bar ? parseFloat(getComputedStyle(bar).width) : 0
  })()
`)
check('showcase: progress bar filled', progress > 200, `width=${progress}px`)
const tradeCount = await evalJS(`
  (() => {
    const card = document.querySelector('[aria-label="Illustrative equity curve versus SPY benchmark"]').closest('.glass-strong')
    return [...card.querySelectorAll('div')].filter(d => /day 1\\d\\d/.test(d.textContent)).length
  })()
`)
check('showcase: trade log rows visible', tradeCount >= 3, `rows=${tradeCount}`)

// ─────────────────────────────────────────────────────────────────────────────
console.log('\n=== 5. HowItWorks: step 3 + backtest loop ===')
await navigate(BASE + '/how-it-works')

const step3 = await evalJS(`
  (() => {
    const t = document.body.innerText.toLowerCase()
    return {
      title: t.includes('get candidates + validation'),
      copy: t.includes('see a ranked list of matching stocks with real-time context'),
      chips: ['results: 10 stocks', 'click “backtest”', 'simulated performance'].every(s => t.includes(s)),
      quote: t.includes('the key difference') && t.includes('translates your thesis into criteria'),
      rows: ['soun', 'crdo', 'alab', 'avgo', 'qtwo'].every(s => t.includes(s)),
      btn: t.includes('backtest strategy'),
    }
  })()
`)
check('step3: title + exact requested copy', step3.title && step3.copy)
check('step3: example chips rendered from data array', step3.chips)
check('step3: key-difference quote present', step3.quote)
check('step3: results table rows present', step3.rows)
check('step3: Backtest strategy button present', step3.btn)

const btLoop = await evalJS(`
  (() => {
    const t = document.body.innerText.toLowerCase()
    return {
      header: t.includes('the validation loop') && t.includes('candidates are a hypothesis'),
      cards: ['bt-01', 'bt-02', 'bt-03'].every(s => t.includes(s)),
      chips: ['rsi < 30 → buy', 'sma-50 > sma-200', 'stop-loss −8%'].every(s => t.includes(s)),
      kpis: ['total return', 'sharpe', 'max drawdown', 'win rate'].every(s => t.includes(s)),
    }
  })()
`)
check('btloop: header + 3 cards', btLoop.header && btLoop.cards)
check('btloop: strategy chips', btLoop.chips)
check('btloop: KPI labels', btLoop.kpis)

await evalJS(
  "[...document.querySelectorAll('svg')].find(s => s.getAttribute('viewBox') === '0 0 100 80')?.scrollIntoView({ block: 'center' })",
)
await sleep(2300)
const miniCurve = await evalJS(`
  (() => {
    const svg = [...document.querySelectorAll('svg')].find(s => s.getAttribute('viewBox') === '0 0 100 80')
    if (!svg) return { found: false }
    const gradPaths = [...svg.querySelectorAll('path')].filter(p => (p.getAttribute('stroke') || '').includes('url(')).length
    return { found: true, paths: svg.querySelectorAll('path').length, gradPaths }
  })()
`)
check(
  'btloop: mini curve svg draws after scroll',
  miniCurve.found && miniCurve.gradPaths >= 1,
  JSON.stringify(miniCurve),
)

// ─────────────────────────────────────────────────────────────────────────────
console.log('\n=== 6. Backtester workspace: 3-pane layout, streaming code, chat, resizable ===')
// Seed demo auth (ProtectedRoute needs a stored token + user in demo mode), then load the route.
await navigate(BASE + '/')
await evalJS(
  `localStorage.setItem('stratoscan_token', 'demo-token'); localStorage.setItem('stratoscan_user', JSON.stringify({ id: 1, email: 'you@stratoscan.app', plan: 'free', email_verified: true, created_at: '2026-01-01' }))`,
)
await navigate(
  BASE + '/app/backtest?q=' + encodeURIComponent('oversold healthcare with low beta'),
  'input[aria-label="Strategy message"]',
)

// 1. Three panes exist
check(
  'backtester: chat pane present',
  await evalJS('!!document.querySelector(\'[aria-label="Strategy chat"]\')'),
)
check(
  'backtester: results pane present',
  await evalJS('!!document.querySelector(\'[aria-label="Backtest results"]\')'),
)
check(
  'backtester: code pane present',
  await evalJS('!!document.querySelector(\'[aria-label="Generated strategy code"]\')'),
)
check(
  'backtester: two splitter dividers (role=separator)',
  (await evalJS("document.querySelectorAll('[role=separator]').length")) >= 2,
)

// 2. Default ratios: chat ~35% of width, results ~72% of the right column height
const ratios = await evalJS(`
  (() => {
    const panes = [...document.querySelectorAll('[data-pane-ratio]')]
    const chat = panes[0].getBoundingClientRect()
    const results = panes[1].getBoundingClientRect()
    const width = panes[0].parentElement.getBoundingClientRect().width
    const rightH = panes[1].parentElement.getBoundingClientRect().height
    return { chat: chat.width / width, results: results.height / rightH }
  })()
`)
check(
  'backtester: chat pane ~35% of width (default)',
  Math.abs(ratios.chat - 0.35) < 0.08,
  `chat=${(ratios.chat * 100).toFixed(1)}%`,
)
check(
  'backtester: results ~72% of right column (default)',
  Math.abs(ratios.results - 0.72) < 0.1,
  `results=${(ratios.results * 100).toFixed(1)}%`,
)

// 3. Auto-run from ?q= pushed a user message; code streams while the request is in flight
await poll("document.querySelectorAll('[data-chat-msg][data-role=user]').length > 0", true, 4000)
const codeLines1 = await evalJS(
  'document.querySelectorAll(\'[aria-label="Generated strategy code"] code\').length',
)
await sleep(700)
const codeLines2 = await evalJS(
  'document.querySelectorAll(\'[aria-label="Generated strategy code"] code\').length',
)
check(
  'backtester: code streams line-by-line while running',
  codeLines2 > codeLines1,
  `${codeLines1} -> ${codeLines2}`,
)

// 4. Run completes -> results KPIs + assistant reply + code status done
await poll(
  '[...document.querySelectorAll(\'[aria-label="Backtest results"]\')].length > 0',
  true,
  2000,
)
const resultsOk = await poll(
  "document.querySelector('[aria-label=\"Backtest results\"]')?.innerText.includes('Total return') ?? false",
  true,
  12000,
)
check('backtester: KPI cards render after run (Total return)', resultsOk)
const assistOk = await poll(
  "document.querySelectorAll('[data-chat-msg][data-role=assistant]').length > 0",
  true,
  12000,
)
check('backtester: assistant reply appears in chat', assistOk)
const codeDone = await poll(
  "document.querySelector('[data-code-status]')?.textContent.trim() === 'done'",
  true,
  12000,
)
check('backtester: code status reaches done', codeDone)

// 5. Send a new message via the composer (real CDP keyboard) -> new user + assistant turns
// NOTE: the page renders two layouts (mobile stacked + desktop split). querySelector
// returns the hidden mobile copy (rect 0×0) — target the VISIBLE instance instead.
const visibleEl = (sel) =>
  `[...document.querySelectorAll(${JSON.stringify(sel)})].find((el) => el.getClientRects().length > 0)`
const userBefore = await evalJS(
  "document.querySelectorAll('[data-chat-msg][data-role=user]').length",
)
const composer = visibleEl('input[aria-label="Strategy message"]')
await click(composer)
await sleep(150)
await send('Input.insertText', { text: 'buy oversold tech stocks with RSI below 30' })
// Browser default actions (implicit form submit) need rawKeyDown, not keyDown.
await send('Input.dispatchKeyEvent', {
  type: 'rawKeyDown',
  key: 'Enter',
  code: 'Enter',
  windowsVirtualKeyCode: 13,
  nativeVirtualKeyCode: 13,
})
await send('Input.dispatchKeyEvent', {
  type: 'char',
  key: 'Enter',
  text: '\r',
  unmodifiedText: '\r',
  windowsVirtualKeyCode: 13,
})
await send('Input.dispatchKeyEvent', {
  type: 'keyUp',
  key: 'Enter',
  code: 'Enter',
  windowsVirtualKeyCode: 13,
  nativeVirtualKeyCode: 13,
})
const sent = await poll(
  `document.querySelectorAll('[data-chat-msg][data-role=user]').length > ${userBefore}`,
  true,
  5000,
)
check('backtester: sending a chat message appends a user bubble', sent)

// 6. Tune drawer opens/closes (visible instance; headers are CSS-uppercased -> case-insensitive)
const tuneBtn = visibleEl('button[aria-label="Show tuning parameters"]')
await click(tuneBtn)
await sleep(350)
check(
  'backtester: Tune drawer opens (strategy builder visible)',
  await evalJS(
    "document.body.innerText.toLowerCase().includes('strategy builder') && document.body.innerText.toLowerCase().includes('risk & execution')",
  ),
)
const tuneAgain = visibleEl('button[aria-label="Hide tuning parameters"]')
await click(tuneAgain)
await sleep(250)

// 7. Drag the vertical divider -> chat pane width changes
// NOTE: the page renders two layouts (mobile stacked + desktop split). Bare
// querySelector can return the hidden mobile copy (rect 0×0), so mouse events
// land on nothing. Target the VISIBLE separator + pane (same helper the composer
// uses above), wake the tab first (occluded tabs drop mouse dispatch), and poll
// for the width to actually change after the release.
await send('Page.bringToFront')
await sleep(300)
const chatWidthBefore = await evalJS(
  `${visibleEl('[data-pane-ratio]')}.getBoundingClientRect().width`,
)
const vdiv = await evalJS(
  `(() => { const r = ${visibleEl('[role=separator]')}.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 } })()`,
)
await send('Input.dispatchMouseEvent', {
  type: 'mousePressed',
  x: vdiv.x,
  y: vdiv.y,
  button: 'left',
  clickCount: 1,
})
for (let i = 1; i <= 4; i++) {
  await send('Input.dispatchMouseEvent', {
    type: 'mouseMoved',
    x: vdiv.x + i * 25,
    y: vdiv.y,
    button: 'left',
    buttons: 1,
  })
  await sleep(16)
}
await send('Input.dispatchMouseEvent', {
  type: 'mouseReleased',
  x: vdiv.x + 100,
  y: vdiv.y,
  button: 'left',
  clickCount: 1,
})
const chatWidthAfter = await poll(
  `${visibleEl('[data-pane-ratio]')}.getBoundingClientRect().width > ${chatWidthBefore} + 40`,
  true,
  3000,
)
check(
  'backtester: dragging vertical divider widens chat pane',
  chatWidthAfter,
  `${Math.round(chatWidthBefore)} -> ${Math.round(await evalJS(`${visibleEl('[data-pane-ratio]')}.getBoundingClientRect().width`))}px`,
)

// 8. Divider is keyboard-accessible (arrow key nudges the split)
await evalJS("document.querySelectorAll('[role=separator]')[0].focus()")
const vn1 = await evalJS(
  "document.querySelectorAll('[role=separator]')[0].getAttribute('aria-valuenow')",
)
await send('Input.dispatchKeyEvent', {
  type: 'keyDown',
  key: 'ArrowRight',
  code: 'ArrowRight',
  windowsVirtualKeyCode: 39,
})
await send('Input.dispatchKeyEvent', {
  type: 'keyUp',
  key: 'ArrowRight',
  code: 'ArrowRight',
  windowsVirtualKeyCode: 39,
})
await sleep(150)
const vn2 = await evalJS(
  "document.querySelectorAll('[role=separator]')[0].getAttribute('aria-valuenow')",
)
check('backtester: arrow-key resize works on divider', vn2 !== vn1, `${vn1} -> ${vn2}`)

// ─────────────────────────────────────────────────────────────────────────────
console.log(
  '\n=== 7. Reduced-motion fallback renders safely (tape still scrolls, strobes suppressed) ===',
)
await send('Emulation.setEmulatedMedia', {
  features: [{ name: 'prefers-reduced-motion', value: 'reduce' }],
})
await navigate(BASE + '/')
// After many CDP navigations headless Chrome can mark the tab occluded, which
// suspends IntersectionObserver/rAF delivery (visibility:hidden). Bring it back.
await send('Page.bringToFront')
await sleep(300)
// ── DIAGNOSTIC: why don't inView-gated effects fire here? ──
const s7diag = await evalJS(`(async () => {
  const el = document.querySelector('[data-reveal]')?.closest('section')
  if (!el) return 'NO_HERO'
  const rect = el.getBoundingClientRect()
  let rafFired = false
  const rafPromise = new Promise((r) => { requestAnimationFrame(() => { rafFired = true; r() }) })
  const ioPromise = new Promise((r) => {
    const io = new IntersectionObserver((entries) => {
      r(JSON.stringify({ fired: true, intersecting: entries[0].isIntersecting, ratio: entries[0].intersectionRatio }))
      io.disconnect()
    }, { threshold: 0.1 })
    io.observe(el)
    setTimeout(() => r(JSON.stringify({ fired: false, intersecting: null, ratio: null })), 5000)
  })
  const ioResult = await ioPromise
  await Promise.race([rafPromise, new Promise((r) => setTimeout(r, 500))])
  return JSON.stringify({
    visibility: document.visibilityState,
    hidden: document.hidden,
    hasFocus: document.hasFocus(),
    scrollY: Math.round(window.scrollY),
    innerH: window.innerHeight,
    rect: rect ? { top: Math.round(rect.top), h: Math.round(rect.height), w: Math.round(rect.width) } : null,
    io: JSON.parse(ioResult),
    rafFired,
    reduce: matchMedia('(prefers-reduced-motion: reduce)').matches,
    path: location.pathname,
    candles: document.querySelectorAll('rect[data-candle]').length,
  })
})()`)
console.log('S7 DIAG:', s7diag)
// Typewriter: poll until it produces text, then confirm the text ADVANCES (fixed
// samples can collide with the 2.4s hold phase, making a working typewriter look frozen).
const typed = await poll(
  "(document.querySelector('input[aria-label=\"Idea to test\"]')?.value ?? '').length > 0",
  true,
  6000,
)
const t0len = await evalJS(
  'document.querySelector(\'input[aria-label="Idea to test"]\').value.length',
)
const advanced = await poll(
  `document.querySelector('input[aria-label="Idea to test"]').value.length !== ${t0len}`,
  true,
  6000,
)
check(
  'reduced-motion: typewriter still types (text reveal, not motion)',
  typed && advanced,
  `typed=${typed} adv=${advanced}`,
)
check(
  'reduced-motion: no trade flash (suppressed with motion off)',
  (await evalJS("document.querySelectorAll('circle[data-flash]').length")) === 0,
)
// The tape scroll is the same mild horizontal drift as the typewriter: kept for everyone.
// Poll for the transient 47-candle window (present each 1.2s tick once the tape runs) —
// fixed samples can miss it when the first tick lands late after a long prior section.
const saw47 = await poll("document.querySelectorAll('rect[data-candle]').length === 47", true, 8000)
check('reduced-motion: tape still scrolls (real-chart slide kept)', saw47)
// The one-shot draw-in runs for everyone too.
const revealRan = await poll(
  "getComputedStyle(document.querySelector('[data-reveal]')).animationName === 'hero-reveal'",
  true,
  8000,
)
const rm = await evalJS(`
  (() => ({
    flash: document.querySelectorAll('circle[data-flash]').length,
    beam: !!document.querySelector('[data-beam]'),
    beamAnimated: (getComputedStyle(document.querySelector('[data-beam]')).animationName || '').includes('beam-pulse'),
    particleAnimated: (getComputedStyle(document.querySelector('[data-particle]')).animationName || '').includes('float-up'),
    closeline: !!document.querySelector('path[data-closeline]'),
    revealAnim: getComputedStyle(document.querySelector('[data-reveal]')).animationName,
    animateMotionCount: document.querySelectorAll('animateMotion').length,
    showcase: document.body.innerText.includes('Backtest the thesis before you trust it'),
  }))()
`)
check('reduced-motion: trade flash suppressed', rm.flash === 0, `flash=${rm.flash}`)
check('reduced-motion: beam static (no pulse under reduce)', rm.beam && !rm.beamAnimated)
check('reduced-motion: particles static under reduce', !rm.particleAnimated)
check('reduced-motion: close-line static chart present', rm.closeline)
check(
  'reduced-motion: chart draw-in still runs (one-shot reveal)',
  revealRan,
  `anim=${rm.revealAnim}`,
)
check('reduced-motion: animateMotion suppressed', rm.animateMotionCount === 0)
check('reduced-motion: showcase still renders', rm.showcase)

// ─────────────────────────────────────────────────────────────────────────────
console.log('\n=== Summary ===')
const fails = results.filter((r) => !r.ok)
console.log(`passed=${results.length - fails.length} failed=${fails.length}`)
if (pageErrors.length) console.log('PAGE ERRORS:', pageErrors)
if (consoleErrors.length) console.log('CONSOLE ERRORS:', consoleErrors.slice(0, 5))
fails.forEach((f) => console.log('FAILED:', f.name))

ws.close()
chrome.kill('SIGKILL')
rmSync(userDir, { recursive: true, force: true })
process.exit(fails.length || pageErrors.length || consoleErrors.length ? 1 : 0)
