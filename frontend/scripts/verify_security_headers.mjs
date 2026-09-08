/* global console, process */
/**
 * Guard: production security headers are actually served.
 *
 * ROOT CAUSE (fixed): every security header — CSP, HSTS, X-Frame-Options,
 * X-Content-Type-Options, Referrer-Policy, Permissions-Policy — was defined
 * ONLY in `public/_headers`. That file is a Cloudflare Pages / Netlify
 * convention; **Vercel does not read it**, and `vercel.json` carried only the
 * SPA rewrite. The site was documented as hardened (DEPLOYMENT.md §2) while
 * shipping with no security headers at all. Nothing in CI checked, because
 * neither file was referenced by a single test.
 *
 * This guard asserts the FIXED wiring: vercel.json declares all six headers
 * for every route, the CSP keeps its teeth, and `_headers` stays a faithful
 * mirror so the two can never quietly disagree.
 *
 * Run: node scripts/verify_security_headers.mjs
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const here = path.dirname(fileURLToPath(import.meta.url))
const frontend = path.resolve(here, '..')

const REQUIRED_HEADERS = [
  'Content-Security-Policy',
  'X-Content-Type-Options',
  'X-Frame-Options',
  'Referrer-Policy',
  'Permissions-Policy',
  'Strict-Transport-Security',
]

// CSP directives that must survive any future edit. `script-src 'self'` with
// no 'unsafe-inline' is the single biggest XSS mitigation here — the bundle is
// 100% external scripts, so there is nothing inline to permit.
const REQUIRED_CSP_DIRECTIVES = [
  "default-src 'self'",
  "script-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
]

// The API origin the app actually calls. If the backend ever moves, this and
// public/_headers must move together or every API call is blocked at runtime.
const BACKEND_ORIGIN = 'https://stratoscan-api.fly.dev'

// ── vercel.json — the file Vercel actually reads ───────────────────────────
const vercel = JSON.parse(readFileSync(path.join(frontend, 'vercel.json'), 'utf8'))

assert.ok(
  Array.isArray(vercel.headers) && vercel.headers.length > 0,
  'vercel.json has no "headers" block — production would ship with NO security headers. ' +
    'Defining them in public/_headers is not enough: Vercel ignores that file.',
)

// A rule whose source matches every path. Vercel matches `source` against the
// request path, so `/(.*)` (or `/:path*`) is the catch-all.
const catchAll = vercel.headers.find((r) => r.source === '/(.*)' || r.source === '/:path*')
assert.ok(
  catchAll,
  `vercel.json headers exist but none use a catch-all source; found: ${vercel.headers
    .map((r) => r.source)
    .join(', ')}`,
)

const vercelHeaders = new Map(catchAll.headers.map((h) => [h.key, h.value]))

for (const name of REQUIRED_HEADERS) {
  assert.ok(vercelHeaders.has(name), `vercel.json is missing the ${name} header`)
  assert.ok(
    String(vercelHeaders.get(name)).trim().length > 0,
    `vercel.json declares ${name} with an empty value`,
  )
}

const csp = vercelHeaders.get('Content-Security-Policy')
for (const directive of REQUIRED_CSP_DIRECTIVES) {
  assert.ok(csp.includes(directive), `CSP is missing "${directive}"`)
}

// Pull script-src out on its own: 'unsafe-inline' is legitimate in style-src
// (Framer Motion injects keyframes at runtime) but never in script-src.
const scriptSrc = csp.split(';').find((d) => d.trim().startsWith('script-src'))
assert.ok(scriptSrc, 'CSP has no script-src directive')
assert.ok(
  !scriptSrc.includes('unsafe-inline') && !scriptSrc.includes('unsafe-eval'),
  `CSP script-src must not allow unsafe-inline/unsafe-eval — got: ${scriptSrc.trim()}`,
)

assert.ok(
  csp.includes(BACKEND_ORIGIN),
  `CSP connect-src must include the backend origin ${BACKEND_ORIGIN}, or every API call is blocked`,
)

// ── public/_headers — the portable mirror ──────────────────────────────────
// Parsed as: a `/*` path block, then indented "Name: value" lines.
const headersFile = readFileSync(path.join(frontend, 'public', '_headers'), 'utf8')
const mirrored = new Map()
let inBlock = false
for (const rawLine of headersFile.split('\n')) {
  const line = rawLine.trimEnd()
  if (!line.trim() || line.trim().startsWith('#')) continue
  if (!line.startsWith(' ') && !line.startsWith('\t')) {
    inBlock = line.trim() === '/*'
    continue
  }
  if (!inBlock) continue
  const idx = line.indexOf(':')
  if (idx === -1) continue
  mirrored.set(line.slice(0, idx).trim(), line.slice(idx + 1).trim())
}

for (const name of REQUIRED_HEADERS) {
  assert.ok(mirrored.has(name), `public/_headers is missing ${name} (it must mirror vercel.json)`)
  assert.equal(
    mirrored.get(name),
    vercelHeaders.get(name),
    `public/_headers and vercel.json disagree on ${name}. vercel.json is what ships; ` +
      'update both or neither.',
  )
}

console.log(`✅ vercel.json serves all ${REQUIRED_HEADERS.length} security headers on /(.*)`)
console.log("✅ CSP keeps script-src 'self' with no unsafe-inline/unsafe-eval")
console.log(`✅ CSP connect-src allows the backend origin (${BACKEND_ORIGIN})`)
console.log('✅ public/_headers mirrors vercel.json byte-for-byte')
console.log('\n✅ SECURITY HEADER GUARD PASSED')
process.exit(0)
