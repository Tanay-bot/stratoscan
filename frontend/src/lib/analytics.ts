import { API_URL, DEMO_MODE, getToken } from './api/client'

/**
 * Browser-side product analytics — the funnel's front half.
 *
 * The events that matter most happen BEFORE an account exists: a landing view,
 * a first screen, a signup form mount. Without them the funnel starts at
 * signup and cannot say where people were lost. So the browser reports five
 * events (the `CLIENT_EVENTS` allowlist on the server; anything else is
 * rejected with 400) and identifies itself with a locally generated UUID that
 * survives long enough to be joined to the account it becomes.
 *
 * Three rules this module follows, all of them deliberate:
 *
 *  1. `track()` is fire-and-forget. It never throws and never returns a
 *     promise anyone awaits — telemetry must not be able to slow down or break
 *     a click. Failures (offline, blocked, 500) are swallowed silently.
 *  2. No query text, no email, no symbol list is ever sent. Props are only
 *     ever counts, surface names, or template ids; the server's per-event
 *     whitelist drops anything else.
 *  3. The only identifier is `stratoscan_anon_id` in localStorage — not a
 *     cookie, not shared across sites, and cleared whenever the user clears
 *     site data. There is no third-party analytics tool, which is why there is
 *     no cookie banner (see the Privacy page).
 */

/** localStorage key holding this browser's anonymous visitor id. */
export const ANON_ID_KEY = 'stratoscan_anon_id'

/**
 * The events the browser is allowed to report.
 *
 * Kept in sync with `CLIENT_EVENTS` in `stratoscan/src/analytics.py` — the
 * server is the authority and answers 400 for anything outside its allowlist,
 * so this type is a compile-time mirror, not the enforcement.
 */
export type ClientEvent =
  'landing_view' | 'query_submitted' | 'template_used' | 'result_shared' | 'signup_started'

/** Last-resort id when localStorage is unavailable (private mode, blocked). */
let memoryAnonId: string | null = null

function newId(): string {
  const c = globalThis.crypto
  if (c && typeof c.randomUUID === 'function') return c.randomUUID()
  // Only reached in a browser old enough to lack randomUUID. The id is not a
  // secret — it exists to tell one anonymous visitor from another — so a
  // Math.random fallback is acceptable here.
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (ch) => {
    const r = Math.floor(Math.random() * 16)
    return (ch === 'x' ? r : (r & 0x3) | 0x8).toString(16)
  })
}

/**
 * This browser's anonymous id, created once and then stable forever.
 *
 * Read from localStorage on every call rather than cached in module scope: if
 * the user clears site data, the next call must mint a new id (the old one is
 * gone from the only place that persisted it), and a cached value would keep
 * reporting under an id nobody can see any more.
 *
 * Never throws: a browser that refuses localStorage gets a per-session id
 * instead, so the funnel still records the session — just without continuity.
 */
export function getAnonId(): string {
  try {
    const existing = window.localStorage.getItem(ANON_ID_KEY)
    if (existing) return existing
    const created = newId()
    window.localStorage.setItem(ANON_ID_KEY, created)
    return created
  } catch {
    memoryAnonId = memoryAnonId ?? newId()
    return memoryAnonId
  }
}

/**
 * Report one browser event. Fire-and-forget by contract.
 *
 * Uses `fetch` rather than the shared axios instance so the request can set
 * `keepalive` — `landing_view` and `result_shared` are typically the last
 * thing that happens before the page unloads, and a request that dies with the
 * document would lose exactly the events that bookend the funnel. The headers
 * the axios interceptor would have added (anonymous id, Bearer token) are set
 * explicitly for the same reason.
 *
 * @param name  One of the five client-reportable events.
 * @param props Counts, surface names, or template ids — never user content.
 */
export function track(name: ClientEvent, props?: Record<string, unknown>): void {
  // Demo mode serves synthetic data and talks to no backend; recording events
  // about it would pollute the real funnel with numbers nobody earned.
  if (DEMO_MODE) return
  try {
    const token = getToken()
    void fetch(`${API_URL}/events`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Anon-Id': getAnonId(),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ name, props: props ?? {} }),
      keepalive: true,
    }).catch(() => {
      /* Swallowed on purpose: an event that fails to send is not the user's problem. */
    })
  } catch {
    /* fetch can throw synchronously (for example a malformed URL); same rule. */
  }
}
