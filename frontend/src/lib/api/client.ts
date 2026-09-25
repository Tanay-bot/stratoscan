import axios, { AxiosError } from 'axios'
import { ApiError } from './types'

/**
 * Base URL for the backend.
 *
 * - Dev (default): the relative `/api` path is proxied by Vite to the FastAPI
 *   server (see vite.config.ts), so the browser never deals with CORS.
 * - Prod / direct: set VITE_API_URL to the deployed backend origin, e.g.
 *   https://api.stratoscan.com — axios then hits it cross-origin and the
 *   backend's CORS allowlist handles the handshake.
 */
export const API_URL = import.meta.env.VITE_API_URL ?? '/api'

/**
 * Origin of the backend admin dashboard — served by the API itself at
 * `/admin` as a self-contained HTML page (src/admin_dashboard.py), not a
 * frontend route. Resolves the API origin so the nav can link to it:
 * absolute VITE_API_URL in prod/staging; the documented dev backend
 * (http://localhost:8000) when the relative `/api` proxy is in use.
 */
export const ADMIN_DASHBOARD_URL = (() => {
  const origin = API_URL.startsWith('http') ? API_URL : 'http://localhost:8000'
  return `${origin.replace(/\/+$/, '')}/admin`
})()

/**
 * Demo mode serves realistic synthetic data through the same typed API
 * interface so the UI is fully demoable without a running backend. Off by
 * default — production builds never ship fake data unless explicitly opted in.
 */
export const DEMO_MODE = (import.meta.env.VITE_DEMO_MODE ?? 'false') === 'true'

export const TOKEN_KEY = 'stratoscan_token'
export const TOKEN_EXPIRES_KEY = 'stratoscan_token_expires_at'

export function getToken(): string | null {
  return typeof window !== 'undefined' ? window.localStorage.getItem(TOKEN_KEY) : null
}

export function setToken(token: string | null) {
  if (typeof window === 'undefined') return
  if (token) window.localStorage.setItem(TOKEN_KEY, token)
  else window.localStorage.removeItem(TOKEN_KEY)
}

export const api = axios.create({
  baseURL: API_URL,
  timeout: 120_000,
})

/**
 * Timeout for long-running compute endpoints (backtest). A backtest
 * downloads 10 years of daily OHLCV per symbol and then runs the
 * simulation + walk-forward + parameter-sensitivity per symbol — on a cold
 * cache that legitimately takes minutes (verified live: a single symbol's
 * data fetch can take 40s–2m30s, and a top_n=5 backtest ~1–4 min with the
 * parallel engine). The 120s default would abort a legitimate run and show
 * a fake error, so these calls get a 20-minute budget. The backend streams
 * nothing yet, so the trade-off is a longer wait vs. a guaranteed-false
 * timeout.
 */
export const LONG_TIMEOUT_MS = 20 * 60 * 1000

api.interceptors.request.use((config) => {
  const token = getToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// ── 401 auto-logout ────────────────────────────────────────────────────────
// A 401 from any endpoint other than the auth entry points means the stored
// JWT expired or was revoked mid-session. Clear the session and bounce to
// /login instead of letting the user stare at a dead screen. The handler is
// registered by the auth store (see store/auth.ts) to avoid a circular import.
type UnauthorizedHandler = () => void
let unauthorizedHandler: UnauthorizedHandler | null = null

export function setUnauthorizedHandler(fn: UnauthorizedHandler | null) {
  unauthorizedHandler = fn
}

// Paths where a 401 is a *normal* response (bad credentials / verification
// state), not an expired session — those never trigger auto-logout.
const AUTH_PATHS = [
  '/auth/login',
  '/auth/register',
  '/auth/verify-email',
  '/auth/resend-verification',
  // A refused refresh (session past its maximum age) is not an expired
  // session: the current token still works until its own deadline, and the
  // normal expiry path handles that. Never auto-logout on it.
  '/auth/refresh',
]

api.interceptors.response.use(
  (res) => res,
  (err) => {
    if (axios.isAxiosError(err)) {
      const status = err.response?.status
      const path = err.config?.url ?? ''
      const isAuthPath = AUTH_PATHS.some((p) => path.startsWith(p))
      const shouldHandle = status === 401 && !isAuthPath && getToken() !== null
      if (shouldHandle) unauthorizedHandler?.()
    }
    return Promise.reject(err)
  },
)

/** Normalize any axios/network failure into an ApiError with status + retryAfter. */
export function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err
  if (axios.isAxiosError(err)) {
    const ax = err as AxiosError<{ detail?: unknown; retry_after?: number }>
    const data = ax.response?.data
    // Network-level failure (backend down, DNS, TLS) — axios reports it as a
    // generic "Network Error" with no response body. Give users an actionable
    // message instead of a raw axios string.
    if (!ax.response && ax.code === 'ERR_NETWORK') {
      return new ApiError(
        'Cannot reach the Stratoscan API. Check that the backend is running and VITE_API_URL points at it.',
        0,
      )
    }
    // The dev proxy returns an HTML error page (500) when the backend is
    // down — never surface raw HTML to the user. Same for any other non-JSON
    // body: use the backend detail when present, otherwise a friendly line.
    const looksLikeHtml = typeof data === 'string' && /<\/?[a-z][\s\S]*>/i.test(data)
    // FastAPI/Pydantic 422s carry `detail` as an ARRAY of validation issues
    // ({loc, msg, type, ...}) — the backend has no RequestValidationError
    // handler to normalize them, so that raw array is what ships. Join it
    // into a readable message instead of String()-coercing the array.
    let detail: string | null = null
    if (Array.isArray(data?.detail)) {
      detail =
        data.detail
          .map((issue) => {
            // The backend's HTTPException handler wraps `detail` verbatim, so
            // an array here may hold Pydantic issue objects OR plain strings.
            if (typeof issue === 'string') return issue
            const issueObj = (issue ?? {}) as { loc?: unknown; msg?: unknown }
            const loc = Array.isArray(issueObj.loc) ? issueObj.loc.join('.') : 'body'
            const msg = typeof issueObj.msg === 'string' ? issueObj.msg : 'Invalid value'
            return `${loc}: ${msg}`
          })
          .join('; ') || 'The request was invalid.'
    } else if (typeof data?.detail === 'string') {
      detail = data.detail
    } else if (typeof data === 'string' && !looksLikeHtml) {
      detail = data
    }
    // 429 contract: the backend ships `Retry-After: <seconds>` (or an
    // HTTP-date) on the response — that header is the authoritative wait
    // time for the rate-limit gate. Fall back to the body's `retry_after`
    // for proxies that strip response headers.
    let retryAfter: number | undefined
    const header = ax.response?.headers?.['retry-after']
    if (typeof header === 'string') {
      const seconds = Number(header)
      if (Number.isFinite(seconds) && seconds > 0) retryAfter = seconds
      else {
        const at = Date.parse(header)
        if (!Number.isNaN(at)) retryAfter = Math.max(0, Math.ceil((at - Date.now()) / 1000))
      }
    }
    if (retryAfter === undefined && typeof data?.retry_after === 'number') {
      retryAfter = data.retry_after
    }
    return new ApiError(
      detail ?? ax.message ?? 'Something went wrong. Please try again.',
      ax.response?.status ?? 0,
      retryAfter,
    )
  }
  return new ApiError(
    err instanceof Error ? err.message : 'Something went wrong. Please try again.',
    0,
  )
}

export async function unwrap<T>(promise: Promise<{ data: T }>): Promise<T> {
  try {
    const res = await promise
    return res.data
  } catch (err) {
    throw toApiError(err)
  }
}

/** Simulated latency so demo mode shows real loading/skeleton states. */
export function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}
