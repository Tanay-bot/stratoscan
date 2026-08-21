#!/usr/bin/env node
/* global process, console */
/**
 * Pixel + content verification for the Stratoscan frontend via headless Chrome.
 *
 * Why this exists: the Freebuff desktop app's embedded-browser screenshot
 * capture can wedge (proven environment-level; DOM/AX channels keep working),
 * so visual verification uses a healthy browser instead. This script captures
 * every public route and FAILS the run if a capture is blank, missing, or an
 * error page.
 *
 * Two checks per route (defense in depth):
 *   1. Pixel: PNG size >= MIN_BYTES — catches blank/near-blank renders.
 *   2. Content: rendered DOM <title> must contain the route's expected
 *      marker — catches 404/error pages that a byte-size check alone would
 *      pass (Chrome's error page renders ~20KB).
 *
 * Usage:
 *   npm run capture                  # capture all public routes
 *   node scripts/capture.mjs --route /pricing   # single route
 *   CHROME_BIN=/path/to/chrome npm run capture  # override browser
 *   VITE_SITE_URL=http://localhost:5173 npm run capture
 *
 * Output: PNGs in .captures/ (add to .gitignore), PASS/FAIL table on stdout,
 * exit code 1 if any route failed.
 *
 * Platform note: the Chrome binary is auto-detected for macOS/Linux; set
 * CHROME_BIN for other platforms (Windows paths are not in the candidate
 * list). --route matches the exact route path (a trailing slash is tolerated).
 */
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, statSync } from 'node:fs'
import path from 'node:path'

const CHROME_CANDIDATES = [
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/Applications/Chromium.app/Contents/MacOS/Chromium',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
  '/usr/bin/google-chrome-stable',
]

const ROUTES = [
  { path: '/', name: 'home', title: 'Bridge from idea' },
  { path: '/features', name: 'features', title: 'Features — Stratoscan' },
  { path: '/how-it-works', name: 'how-it-works', title: 'How It Works — Stratoscan' },
  { path: '/about', name: 'about', title: 'About — Stratoscan' },
  { path: '/pricing', name: 'pricing', title: 'Pricing — Stratoscan' },
  { path: '/contact', name: 'contact', title: 'Contact — Stratoscan' },
  { path: '/terms', name: 'terms', title: 'Terms of Service — Stratoscan' },
  { path: '/privacy', name: 'privacy', title: 'Privacy Policy — Stratoscan' },
]

const BASE = process.env.VITE_SITE_URL || 'http://localhost:5173'
const OUT_DIR = path.resolve(process.cwd(), '.captures')
const WINDOW = process.env.CAPTURE_WINDOW || '1280,800'
const MIN_BYTES = 15000 // below this = blank/near-blank render
const BUDGET_MS = 4000

function findChrome() {
  if (process.env.CHROME_BIN && existsSync(process.env.CHROME_BIN)) return process.env.CHROME_BIN
  const found = CHROME_CANDIDATES.find((c) => existsSync(c))
  if (!found) {
    console.error('✗ No Chrome/Chromium found. Set CHROME_BIN to your browser binary.')
    process.exit(2)
  }
  return found
}

function runChrome(chrome, extraArgs, stdio = 'pipe') {
  // Only --dump-dom writes to stdout; the screenshot pass needs no output, so
  // it passes stdio 'ignore' to avoid buffering Chrome's stderr noise.
  return execFileSync(
    chrome,
    ['--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars', ...extraArgs],
    { encoding: 'utf8', stdio, maxBuffer: 10 * 1024 * 1024 },
  )
}

function capture(chrome, url, outFile) {
  runChrome(
    chrome,
    [
      `--virtual-time-budget=${BUDGET_MS}`,
      `--screenshot=${outFile}`,
      `--window-size=${WINDOW}`,
      url,
    ],
    'ignore',
  )
}

function renderedTitle(chrome, url) {
  const dom = runChrome(chrome, [`--virtual-time-budget=${BUDGET_MS}`, '--dump-dom', url])
  const m = dom.match(/<title>([^<]*)<\/title>/)
  return m ? m[1].trim() : ''
}

const chrome = findChrome()
mkdirSync(OUT_DIR, { recursive: true })

const only = process.argv.find((a) => a.startsWith('--route='))
const routeArg = only ? only.split('=')[1] : null
const targets = routeArg
  ? ROUTES.filter((r) => r.path === routeArg || r.path + '/' === routeArg)
  : ROUTES
if (routeArg && targets.length === 0) {
  console.error(`✗ Unknown route: ${routeArg}`)
  process.exit(2)
}

let failures = 0
console.log(`Capturing ${targets.length} route(s) via ${chrome}`)
for (const route of targets) {
  const outFile = path.join(OUT_DIR, `${route.name}.png`)
  const problems = []
  try {
    capture(chrome, BASE + route.path, outFile)
    const bytes = statSync(outFile).size
    if (bytes < MIN_BYTES) problems.push(`${bytes} bytes (< ${MIN_BYTES} — likely blank)`)
    const title = renderedTitle(chrome, BASE + route.path)
    if (!title.includes(route.title))
      problems.push(`title "${title || '(none)'}" missing "${route.title}"`)
    if (problems.length > 0) {
      console.error(`  FAIL  ${route.path}  ${problems.join('; ')}`)
      failures++
    } else {
      console.log(`  PASS  ${route.path}  ${bytes} bytes — "${title}"`)
    }
  } catch (err) {
    console.error(`  FAIL  ${route.path}  capture error: ${err.message.split('\n')[0]}`)
    failures++
  }
}

if (failures > 0) {
  console.error(`\n✗ ${failures} capture(s) failed. Inspect ${OUT_DIR}/`)
  process.exit(1)
}
console.log(`\n✓ All ${targets.length} route(s) verified (pixels + content) in ${OUT_DIR}/`)
