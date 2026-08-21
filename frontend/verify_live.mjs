// Live-mode integration verification for the Stratoscan frontend.
// Spins up:
//   1. A stub FastAPI-compatible backend on :8000 (the exact response shapes
//      from src/api.py — auth JWT flow, /screen, /backtest, /news, /stocks).
//   2. A Vite dev server on :5181 in LIVE mode (VITE_DEMO_MODE=false,
//      VITE_API_URL=/api) so requests go through the configured /api proxy.
//   3. Headless Chrome via raw CDP (Node 24 global WebSocket, no deps).
//
// Proves the real integration path end-to-end: register -> JWT stored ->
// Authorization header attached -> screen returns real rows -> backtest KPIs ->
// news feed -> stock detail (live snapshot) -> dashboard market movers ->
// 401 auto-logout when the token is expired.

import { spawn, spawnSync } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createServer } from 'node:http'

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const CDP_PORT = 9334
const VITE_PORT = 5181
const STUB_PORT = 8000
const BASE = `http://localhost:${VITE_PORT}`
const userDir = mkdtempSync(join(tmpdir(), 'stratoscan-live-'))

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// ── 1. Stub backend ────────────────────────────────────────────────────────
// Mirrors src/api.py response models exactly (field-for-field) so the typed
// frontend services unmarshal real shapes.
const TOKEN = 'live-test-jwt-token'
const USER = { id: 42, email: 'live@test.app', plan: 'free', email_verified: true, is_admin: false, created_at: '2026-01-01T00:00:00Z' }

const STOCKS = [
  { symbol: 'NVDA', company_name: 'NVIDIA Corporation', sector: 'Technology', industry: 'Semiconductors', current_price: 142.5, price_change_pct: 2.4, market_cap: 3500000000000, pe_ratio: 55.2, rsi_14: 61.0, volume: 34000000, avg_volume: 52000000, dividend_yield: 0.03, beta: 1.7, sma_50: 138.1, sma_200: 121.4, high_52w: 152.0, low_52w: 82.3, pct_off_52w_high: 6.3, score: 82.5, score_components: { value: 71, momentum: 88, quality: 90 }, why_matched: ['above SMA-50', 'momentum > 2'], is_stale: false, data_age_minutes: 4 },
  { symbol: 'CRDO', company_name: 'Credo Technology', sector: 'Technology', industry: 'Semiconductors', current_price: 88.2, price_change_pct: -1.1, market_cap: 14500000000, pe_ratio: 41.0, rsi_14: 44.0, volume: 8200000, avg_volume: 6100000, dividend_yield: null, beta: 1.4, sma_50: 91.0, sma_200: 68.0, high_52w: 96.0, low_52w: 41.0, pct_off_52w_high: 8.1, score: 74.0, score_components: { value: 66, momentum: 70, quality: 84 }, why_matched: ['golden cross'], is_stale: false, data_age_minutes: 3 },
  { symbol: 'SOUN', company_name: 'SoundHound AI', sector: 'Technology', industry: 'Software', current_price: 9.8, price_change_pct: 5.6, market_cap: 3900000000, pe_ratio: null, rsi_14: 72.0, volume: 91000000, avg_volume: 61000000, dividend_yield: null, beta: 3.1, sma_50: 8.4, sma_200: 6.1, high_52w: 11.5, low_52w: 3.4, pct_off_52w_high: 14.8, score: 68.0, score_components: { value: 40, momentum: 92, quality: 30 }, why_matched: ['near 52w high', 'high volume'], is_stale: false, data_age_minutes: 6 },
  { symbol: 'AAPL', company_name: 'Apple Inc.', sector: 'Technology', industry: 'Consumer Electronics', current_price: 212.4, price_change_pct: 0.4, market_cap: 3200000000000, pe_ratio: 33.1, rsi_14: 52.0, volume: 41000000, avg_volume: 48000000, dividend_yield: 0.5, beta: 1.2, sma_50: 208.0, sma_200: 199.0, high_52w: 220.0, low_52w: 164.0, pct_off_52w_high: 3.5, score: 61.0, score_components: { value: 55, momentum: 58, quality: 72 }, why_matched: ['above SMA-200'], is_stale: false, data_age_minutes: 2 },
]

const ARTICLES = [
  { symbol: 'NVDA', headline: 'NVIDIA announces next-gen data center GPU', summary: 'Chipmaker unveiled its Blackwell Ultra platform.', source: 'Reuters', url: 'https://example.com/nvda-1', published_at: '2026-08-02T14:00:00Z', sentiment: 'bullish', sentiment_score: 0.82 },
  { symbol: 'NVDA', headline: 'Analysts raise price targets after earnings', summary: 'Consensus moved higher post-print.', source: 'Bloomberg', url: 'https://example.com/nvda-2', published_at: '2026-08-01T10:30:00Z', sentiment: 'bullish', sentiment_score: 0.71 },
  { symbol: 'NVDA', headline: 'Regulators review export controls', summary: 'Policy risk remains on the table.', source: 'FT', url: 'https://example.com/nvda-3', published_at: '2026-07-31T09:00:00Z', sentiment: 'bearish', sentiment_score: -0.4 },
]

const json = (res, code, body, authSeen) => {
  const headers = {
    'Content-Type': 'application/json',
    // Echo the request origin so the stub works from any Vite port (the
    // frontend's axios sends the Authorization header, which CORS treats as a
    // credentialed request — an explicit origin is required, never a wildcard).
    'Access-Control-Allow-Origin': reqOrigin ?? '*',
    'Access-Control-Allow-Credentials': 'true',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    'Access-Control-Allow-Methods': 'GET, POST, PATCH, OPTIONS',
  }
  if (authSeen !== undefined) headers['X-Test-Auth-Seen'] = String(authSeen)
  res.writeHead(code, headers)
  res.end(JSON.stringify(body))
}

let reqOrigin = null

const authRecord = [] // collect whether protected routes saw a Bearer token

const stub = createServer((req, res) => {
  reqOrigin = req.headers.origin ?? null
  const url = new URL(req.url, `http://127.0.0.1:${STUB_PORT}`)
  const path = url.pathname
  let body = ''
  req.on('data', (c) => (body += c))
  req.on('end', () => {
    const auth = req.headers.authorization ?? ''
    const authed = auth === `Bearer ${TOKEN}`
    if (req.method === 'OPTIONS') return json(res, 204, {})

    // ── Auth ──
    if (req.method === 'POST' && path === '/auth/register') {
      return json(res, 200, { access_token: TOKEN, token_type: 'bearer', expires_in: 604800, email_sent: false, user: USER })
    }
    if (req.method === 'POST' && path === '/auth/login') {
      const p = JSON.parse(body || '{}')
      if (p.email === 'bad@test.app') return json(res, 401, { detail: 'Invalid email or password.' })
      return json(res, 200, { access_token: TOKEN, token_type: 'bearer', expires_in: 604800, user: USER })
    }
    if (req.method === 'GET' && path === '/auth/me') {
      authRecord.push({ path, authed })
      if (!authed) return json(res, 401, { detail: 'Not authenticated' }, authed)
      return json(res, 200, USER, authed)
    }

    // ── Screen ──
    if (req.method === 'POST' && path === '/screen') {
      authRecord.push({ path, authed })
      const p = JSON.parse(body || '{}')
      return json(res, 200, {
        query: p.query ?? '',
        interpreted_filters: { sector: 'Technology', momentum: 'rising' },
        total_matches: STOCKS.length,
        results: STOCKS,
        limit: p.limit ?? 50,
        offset: p.offset ?? 0,
        has_more: false,
        next_offset: null,
        sort_by: 'score',
        sort_desc: true,
        applied_filters: ['sector = Technology'],
        data_quality: { coverage_pct: 99, stale_pct: 0.1, demo: false },
      }, authed)
    }

    // ── Backtest ──
    if (req.method === 'POST' && path === '/backtest') {
      authRecord.push({ path, authed })
      if (!authed) return json(res, 401, { detail: 'Not authenticated' }, authed)
      const eq = Array.from({ length: 60 }, (_, i) => ({
        date: new Date(Date.UTC(2016, 0, 1 + i * 61)).toISOString().slice(0, 10),
        value: 100000 * (1 + i * 0.006 + Math.sin(i / 9) * 0.02),
      }))
      return json(res, 200, {
        query: (JSON.parse(body || '{}').query) ?? '',
        strategy: { entry: 'RSI below 30', exit: 'take profit 20%' },
        total_symbols_tested: STOCKS.length,
        results: Object.fromEntries(
          STOCKS.map((s, i) => [
            s.symbol,
            {
              symbol: s.symbol,
              initial_capital: 100000,
              final_value: 143200,
              total_return_pct: 43.2,
              sharpe_ratio: 1.24,
              max_drawdown_pct: -11.8,
              win_rate_pct: 57.0,
              total_trades: 24 + i,
              equity_curve: eq,
              drawdown_curve: eq.map((p) => ({ date: p.date, value: -Math.abs(Math.sin(p.value / 5000)) })),
              warnings: [],
              benchmark_total_return_pct: 31.5,
              alpha_pct: 11.7,
              beta: 0.9,
              excess_return_pct: 11.7,
              buy_hold_price_return_pct: 28.0,
              buy_hold_total_return_pct: 33.1,
              dividend_contribution_pct: 1.2,
              data_quality: [],
              trades: [
                { symbol: s.symbol, buy_date: '2016-03-14', buy_price: 42.1, sell_date: '2016-04-02', sell_price: 45.8, shares: 2000, pnl: 7400, pnl_pct: 8.8, side: 'long', exit_reason: 'take_profit' },
                { symbol: s.symbol, buy_date: '2017-08-01', buy_price: 51.2, sell_date: null, sell_price: null, shares: 1900, pnl: null, pnl_pct: null, side: 'long', exit_reason: 'signal' },
              ],
            },
          ]),
        ),
      }, authed)
    }

    // ── News ──
    if (req.method === 'GET' && path === '/news') {
      return json(res, 200, { total_articles: ARTICLES.length, returned: ARTICLES.length, articles: ARTICLES })
    }
    const newsMatch = path.match(/^\/news\/([A-Z]+)$/)
    if (req.method === 'GET' && newsMatch) {
      const sym = newsMatch[1]
      const articles = ARTICLES.map((a) => ({ ...a, symbol: sym }))
      return json(res, 200, { symbol: sym, buzz: 0.85, company_news_score: 0.65, sector_news_score: 0.4, article_count: articles.length, updated_at: '2026-08-02T14:00:00Z', articles })
    }

    // ── Stocks ──
    if (req.method === 'GET' && path === '/stocks') {
      const limit = Number(url.searchParams.get('limit') ?? 50)
      const offset = Number(url.searchParams.get('offset') ?? 0)
      const slice = STOCKS.slice(offset, offset + limit)
      return json(res, 200, { total_in_database: STOCKS.length, returned: slice.length, limit, offset, results: slice })
    }
    const stockMatch = path.match(/^\/stocks\/([A-Z]+)$/)
    if (req.method === 'GET' && stockMatch) {
      const s = STOCKS.find((x) => x.symbol === stockMatch[1])
      if (!s) return json(res, 404, { detail: `Symbol '${stockMatch[1]}' not found` })
      return json(res, 200, s)
    }

    if (req.method === 'GET' && path === '/health') {
      return json(res, 200, { status: 'ok', version: '0.1.0', finnhub_keys_configured: 1, screener_llm_configured: true, backtest_llm_configured: true, database_configured: true, database_alive: true, database_latency_ms: 12, uptime_seconds: 900 })
    }
    return json(res, 404, { detail: 'Not found' })
  })
})

// ── 2. Spawn everything ────────────────────────────────────────────────────
// NOTE: NEVER kill anything on the stub port. If port 8000 is occupied it is
// almost certainly the user's REAL FastAPI backend — destroying it would be
// catastrophic. Instead: prefer 8000 (exercises the /api proxy path), and if
// it's taken, fall back to a dedicated stub port with a direct VITE_API_URL
// (which exercises the CORS path — equally valid live-mode wiring).
spawnSync('sh', ['-c', `lsof -ti tcp:${CDP_PORT} 2>/dev/null | xargs -r kill -9 2>/dev/null || true`])
spawnSync('sh', ['-c', `lsof -ti tcp:${VITE_PORT} 2>/dev/null | xargs -r kill -9 2>/dev/null || true`])

let PROXY_MODE = true
const bound = await new Promise((resolve) => {
  stub.once('error', () => resolve(false))
  stub.listen(STUB_PORT, '127.0.0.1', () => resolve(true))
})
if (!bound) {
  PROXY_MODE = false
  STUB_PORT = 8099
  await new Promise((resolve) => {
    stub.once('error', () => resolve(false))
    stub.listen(STUB_PORT, '127.0.0.1', resolve)
  })
  console.log(`live: :8000 occupied — stub on :${STUB_PORT}, direct VITE_API_URL (CORS path)`)
}

const API_MODE_URL = PROXY_MODE ? '/api' : `http://localhost:${STUB_PORT}`

const FRONTEND_DIR = fileURLToPath(new URL('.', import.meta.url))
// Spawn the LOCAL vite binary by absolute path — subagent shells may not have npx on PATH.
const vite = spawn(join(FRONTEND_DIR, 'node_modules', '.bin', 'vite'), ['--port', String(VITE_PORT), '--strictPort'], {
  cwd: FRONTEND_DIR,
  env: { ...process.env, VITE_DEMO_MODE: 'false', VITE_API_URL: API_MODE_URL },
  stdio: 'ignore',
})
const viteReady = await (async () => {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`${BASE}/`)
      if (r.ok) return true
    } catch { /* not up yet */ }
    await sleep(250)
  }
  return false
})()
if (!viteReady) { console.error('HARNESS: live Vite never came up on :' + VITE_PORT); process.exit(1) }

const chrome = spawn(
  CHROME,
  [
    '--headless=new',
    `--remote-debugging-port=${CDP_PORT}`,
    `--user-data-dir=${userDir}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-gpu',
    '--disable-dev-shm-usage',
    '--window-size=1440,2000',
    'about:blank',
  ],
  { stdio: 'ignore' },
)

const cleanup = () => {
  try { ws?.close() } catch {}
  try { chrome.kill('SIGKILL') } catch {}
  try { vite.kill('SIGKILL') } catch {}
  try { stub.close() } catch {}
  try { rmSync(userDir, { recursive: true, force: true }) } catch {}
}
process.on('exit', cleanup)
process.on('uncaughtException', (err) => { cleanup(); console.error('HARNESS CRASH:', err); process.exit(1) })
process.on('unhandledRejection', (err) => { cleanup(); console.error('HARNESS CRASH:', err); process.exit(1) })

async function getTarget() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${CDP_PORT}/json`)
      const list = await r.json()
      const page = list.find((t) => t.type === 'page')
      if (page) return page
    } catch { /* not up yet */ }
    await sleep(200)
  }
  throw new Error('Chrome CDP target never appeared')
}

const page = await getTarget()
const ws = new WebSocket(page.webSocketDebuggerUrl)
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })

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
  if (msg.method === 'Runtime.exceptionThrown') pageErrors.push(msg.params.exceptionDetails?.exception?.description ?? 'exception')
  if (msg.method === 'Runtime.consoleAPICalled' && msg.params.type === 'error') consoleErrors.push(msg.params.args.map((a) => a.value ?? a.description ?? '').join(' '))
}
function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++msgId
    pending.set(id, { resolve, reject })
    ws.send(JSON.stringify({ id, method, params }))
  })
}
async function evalJS(expression) {
  const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
  if (r.exceptionDetails) throw new Error('eval failed: ' + JSON.stringify(r.exceptionDetails).slice(0, 400))
  return r.result.value
}
const results = []
function check(name, cond, detail = '') {
  results.push({ name, ok: !!cond })
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`)
}
async function poll(expr, expect, timeoutMs = 6000) {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    if ((await evalJS(expr)) === expect) return true
    await sleep(150)
  }
  return false
}
const click = async (expr) => {
  const { x, y } = await evalJS(`(() => { const r = ${expr}.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 } })()`)
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 })
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 })
}
async function navigate(url, readySelector) {
  await send('Page.navigate', { url })
  for (let i = 0; i < 100; i++) {
    if (await evalJS("document.readyState === 'complete'")) break
    await sleep(100)
  }
  if (readySelector) {
    for (let i = 0; i < 80; i++) {
      if (await evalJS(`!!document.querySelector(${JSON.stringify(readySelector)})`)) break
      await sleep(100)
    }
  }
}

await send('Page.enable')
await send('Runtime.enable')

console.log('\n=== 0. Health via the Vite proxy ===')
await navigate(BASE + '/login')
if (PROXY_MODE) {
  const healthViaProxy = await fetch(`${BASE}/api/health`).then((r) => r.json()).catch(() => null)
  check('proxy: /api/health reaches the stub through vite', healthViaProxy?.status === 'ok', JSON.stringify(healthViaProxy))
} else {
  check('direct: /health reaches the stub over CORS', (await fetch(`http://localhost:${STUB_PORT}/health`).then((r) => r.json()).catch(() => null))?.status === 'ok')
}
check('app: booted in LIVE mode (no demo badge on login)', !(await evalJS("document.body.innerText.includes('Demo mode')")))

console.log('\n=== 1. Register -> JWT stored -> redirected to /app ===')
await navigate(BASE + '/signup')
await click("document.querySelector('#email')")
await send('Input.insertText', { text: 'live@test.app' })
await click("document.querySelector('#password')")
await send('Input.insertText', { text: 'password123' })
await click("document.querySelector('#confirm')")
await send('Input.insertText', { text: 'password123' })
await click("document.querySelector('form button[type=submit]')")
const toApp = await poll("location.pathname.startsWith('/app')", true, 8000)
const storedToken = await evalJS("localStorage.getItem('stratoscan_token')")
check('register: redirected to /app workspace', toApp, `path=${await evalJS('location.pathname')}`)
check('register: JWT persisted in localStorage', storedToken === TOKEN, `len=${(storedToken ?? '').length}`)
check('register: user persisted', await evalJS("localStorage.getItem('stratoscan_user')?.includes('live@test.app') ?? false"))
check('register: toast shown', await poll("document.body.innerText.includes('Account created')", true, 4000))

console.log('\n=== 2. Screener: POST /screen through proxy, real rows render ===')
await navigate(BASE + '/app/screener?q=' + encodeURIComponent('undervalued tech stocks'), 'input[aria-label="Screen query"]')
const rows = await poll("document.querySelectorAll('[data-stock-row]').length >= 3", true, 10000)
check('screen: results rows rendered from API', rows, `rows=${await evalJS("document.querySelectorAll('[data-stock-row]').length")}`)
check('screen: real symbol + price on screen', await evalJS("document.body.innerText.includes('NVDA') && document.body.innerText.includes('142.50')"))
check('screen: API query echoed', await evalJS("document.body.innerText.includes('undervalued tech stocks')"))
check('screen: no demo data disclaimer', !(await evalJS("document.body.innerText.includes('Demo data')")))

console.log('\n=== 3. Backtester: POST /backtest (auth) -> KPIs + code + chat ===')
await navigate(BASE + '/app/backtest?q=' + encodeURIComponent('momentum breakout stocks'), 'input[aria-label="Strategy message"]')
const kpis = await poll("document.querySelector('[aria-label=\"Backtest results\"]')?.innerText.includes('Total return') ?? false", true, 12000)
check('backtest: KPI cards render from real response', kpis)
check('backtest: equity curve chart rendered', await poll("document.querySelectorAll('svg').length >= 2", true, 8000))
check('backtest: assistant reply in chat', await poll("document.querySelectorAll('[data-chat-msg][data-role=assistant]').length > 0", true, 12000))
check('backtest: code status done', await poll("document.querySelector('[data-code-status]')?.textContent.trim() === 'done'", true, 12000))

console.log('\n=== 4. News feed + stock detail ===')
await navigate(BASE + '/app/news')
const newsRows = await poll("document.querySelectorAll('[data-news-card]').length >= 2", true, 10000)
check('news: feed articles rendered from API', newsRows, `cards=${await evalJS("document.querySelectorAll('[data-news-card]').length")}`)
check('news: sentiment badges present', await evalJS("document.body.innerText.includes('Bullish')"))

await navigate(BASE + '/app/stocks/AAPL')
const detailName = await poll("document.body.innerText.includes('Apple Inc.')", true, 10000)
check('stock: profile fetched (company name)', detailName)
check('stock: live price snapshot rendered', await evalJS("!!document.querySelector('[data-live-snapshot]')"))
check('stock: key metrics rendered (52w range)', await evalJS("document.body.innerText.toLowerCase().includes('52-week range')"))
check('stock: news list rendered', await poll("document.body.innerText.includes('NVIDIA announces')", true, 6000))

console.log('\n=== 5. Dashboard: market movers + watchlist from /stocks ===')
await navigate(BASE + '/app')
const movers = await poll("document.body.innerText.includes('Market movers') && document.body.innerText.includes('NVIDIA Corporation')", true, 10000)
check('dashboard: market movers from real /stocks', movers)
check('dashboard: watchlist shows real prices', await evalJS("document.body.innerText.includes('142.50')"))

console.log('\n=== 6. Auth headers + 401 auto-logout ===')
// All protected calls must have carried the Bearer token
const meSeen = authRecord.find((r) => r.path === '/auth/me')
const btSeen = authRecord.find((r) => r.path === '/backtest')
check('auth: /auth/me saw the Authorization header', meSeen?.authed === true)
check('auth: /backtest saw the Authorization header', btSeen?.authed === true)

// Expire the token -> next protected hit 401s -> auto-logout -> /login
await evalJS(`localStorage.setItem('stratoscan_token', 'expired-token'); localStorage.setItem('stratoscan_token_expires_at', String(Date.now() + 60000))`)
await navigate(BASE + '/app')
const bounced = await poll("location.pathname === '/login'", true, 8000)
check('auth: expired token bounces to /login (401 auto-logout)', bounced, `path=${await evalJS('location.pathname')}`)
check('auth: session cleared after logout', await evalJS("localStorage.getItem('stratoscan_token') === null"))

console.log('\n=== Summary ===')
const fails = results.filter((r) => !r.ok)
console.log(`passed=${results.length - fails.length} failed=${fails.length}`)
if (pageErrors.length) console.log('PAGE ERRORS:', pageErrors)
if (consoleErrors.length) console.log('CONSOLE ERRORS:', consoleErrors.slice(0, 5))
fails.forEach((f) => console.log('FAILED:', f.name))
cleanup()
process.exit(fails.length || pageErrors.length || consoleErrors.length ? 1 : 0)
