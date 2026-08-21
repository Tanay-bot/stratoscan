#!/usr/bin/env node
/* global process, console, setTimeout, clearTimeout, fetch, AbortSignal */
/**
 * Self-test for scripts/capture.mjs — regression protection for the tool's
 * own exit-code contract:
 *
 *   real server  -> exit 0, 8 PASS   (everything renders with real titles)
 *   server down  -> exit 1, clear diagnostic (precondition unmet, NOT a tool failure)
 *   blank server -> exit 1, 0 PASS   (size check fires)
 *   404 server   -> exit 1, 0 PASS   (title/content check fires)
 *
 * The capture tool regressed once (a stdio bug made every route crash); this
 * test exists so that class of breakage is caught automatically, not by hand.
 *
 * Usage: npm run capture:self-test   (or: node scripts/capture.self-test.mjs)
 * Pure Node — spins up tiny http servers on ephemeral ports; no external deps.
 */
import { spawn } from 'node:child_process'
import { createServer } from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const CAPTURE = path.join(ROOT, 'scripts', 'capture.mjs')

function serve(handler) {
  return new Promise((resolve) => {
    const server = createServer(handler)
    server.listen(0, '127.0.0.1', () => resolve(server))
  })
}

function runCapture(baseUrl, routeArg) {
  // MUST be async (spawn, not spawnSync): the blank/404 servers below live in
  // THIS process, and spawnSync would block the event loop so they could never
  // answer Chrome's requests — a deadlock that hung every scenario. With spawn,
  // the loop stays free and the servers respond while capture.mjs runs.
  return new Promise((resolve) => {
    const args = routeArg ? [CAPTURE, `--route=${routeArg}`] : [CAPTURE]
    const child = spawn(process.execPath, args, {
      cwd: ROOT,
      env: { ...process.env, VITE_SITE_URL: baseUrl },
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    let stdout = ''
    child.stdout.on('data', (d) => (stdout += d))
    child.stderr.on('data', () => {}) // drain, don't buffer
    const timer = setTimeout(() => {
      child.kill('SIGKILL')
      resolve({ status: null, stdout })
    }, 180_000)
    child.on('error', () => {
      clearTimeout(timer)
      resolve({ status: null, stdout })
    })
    child.on('close', (code) => {
      clearTimeout(timer)
      resolve({ status: code, stdout })
    })
  })
}

function countPass(out) {
  return (out.match(/ {2}PASS {2}/g) || []).length
}

let failures = 0
const t0 = Date.now()
const mark = (label) => console.log(`  [${((Date.now() - t0) / 1000).toFixed(1)}s] ${label}`)
const check = (name, ok, detail) => {
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${name}  ${detail}`)
  if (!ok) failures++
}

// Scenario 1: real dev server (default VITE_SITE_URL or override).
const realUrl = process.env.VITE_SITE_URL || 'http://localhost:5173'
mark(`real server: starting (${realUrl})`)

// Precondition: when the server is down, Chrome renders a connection-error
// page (title "localhost") and capture.mjs exits 1 — which would falsely look
// like a capture-tool regression. Probe reachability first so a downed server
// gets a clear diagnostic instead of a misleading FAIL.
let reachable = false
try {
  reachable = (await fetch(realUrl, { signal: AbortSignal.timeout(4000) })).ok
} catch {
  reachable = false
}
if (!reachable) {
  console.log(
    `  FAIL  real server  unreachable at ${realUrl} — start the dev server or set VITE_SITE_URL (precondition, not a capture regression)`,
  )
  failures++
} else {
  const real = await runCapture(realUrl)
  mark(`real server: done exit=${real.status}`)
  check(
    'real server',
    real.status === 0 && countPass(real.stdout) === 8,
    `exit=${real.status}, ${countPass(real.stdout)}/8 PASS (expect 0, 8)`,
  )
}

// Scenario 2: blank page for every route — size check must fire.
const blank = await serve((req, res) => {
  res.writeHead(200, { 'Content-Type': 'text/html' })
  res.end('<!doctype html><html><body style="background:#fff"></body></html>')
})
try {
  // Negative scenarios respond identically for every route, so a single
  // route exercises the same size/title check paths in a fraction of the time.
  mark('blank server: starting')
  const b = await runCapture(`http://127.0.0.1:${blank.address().port}`, '/pricing')
  mark(`blank server: done exit=${b.status}`)
  check(
    'blank server',
    b.status === 1 && countPass(b.stdout) === 0,
    `exit=${b.status}, ${countPass(b.stdout)}/1 PASS (expect 1, 0)`,
  )
} finally {
  blank.close()
}

// Scenario 3: 404 for every route — the title/content check must fire.
const errs = await serve((req, res) => {
  res.writeHead(404, { 'Content-Type': 'text/html' })
  res.end('<!doctype html><html><body>Not Found</body></html>')
})
try {
  mark('404 server: starting')
  const e = await runCapture(`http://127.0.0.1:${errs.address().port}`, '/pricing')
  mark(`404 server: done exit=${e.status}`)
  check(
    '404 server',
    e.status === 1 && countPass(e.stdout) === 0,
    `exit=${e.status}, ${countPass(e.stdout)}/1 PASS (expect 1, 0)`,
  )
} finally {
  errs.close()
}

if (failures > 0) {
  console.error(`\n✗ capture self-test: ${failures} scenario(s) failed.`)
  process.exit(1)
}
console.log('\n✓ capture self-test: all 3 scenarios pass (real=0, blank=1, 404=1).')
