import { api, getToken } from './api/client'

/**
 * Anonymous trial sessions — backtests before an account exists (Day 6).
 *
 * The server mints a signed trial token (`POST /try/session`); the id inside
 * it is chosen by the server, never by the browser, so nobody can claim
 * another visitor's results. The token goes on `/try/*` and `/results/*`
 * calls as `X-Anon-Token` — never as a Bearer token, which the server would
 * refuse — and is cleared once its work has been claimed into an account.
 *
 * It is NOT the analytics id (`stratoscan_anon_id`): that one is a public
 * label the browser invents for the funnel; this one is a credential that
 * owns jobs and share links.
 */

export const TRIAL_TOKEN_KEY = 'stratoscan_trial_token'
export const TRIAL_EXPIRES_KEY = 'stratoscan_trial_token_expires_at'

/** Header the server reads the trial token from. */
export const TRIAL_HEADER = 'X-Anon-Token'

function storage(): Storage | null {
  try {
    return typeof window !== 'undefined' ? window.localStorage : null
  } catch {
    return null
  }
}

/** The stored trial token, or null when absent or past its deadline. */
export function getTrialToken(): string | null {
  const s = storage()
  if (!s) return null
  const token = s.getItem(TRIAL_TOKEN_KEY)
  const exp = Number(s.getItem(TRIAL_EXPIRES_KEY))
  if (!token) return null
  if (Number.isFinite(exp) && exp > 0 && exp <= Date.now()) {
    clearTrialToken()
    return null
  }
  return token
}

export function clearTrialToken(): void {
  const s = storage()
  s?.removeItem(TRIAL_TOKEN_KEY)
  s?.removeItem(TRIAL_EXPIRES_KEY)
}

/** True when this browser is not signed in, i.e. backtests run as a trial. */
export function isTrial(): boolean {
  return getToken() === null
}

let inFlight: Promise<string> | null = null

/**
 * The trial token, minting one first if needed. Concurrent callers share one
 * request so two quick clicks cannot start two sessions.
 */
export async function ensureTrialToken(): Promise<string> {
  const existing = getTrialToken()
  if (existing) return existing
  if (!inFlight) {
    inFlight = api
      .post<{ anon_token: string; expires_in: number }>('/try/session')
      .then((res) => {
        const s = storage()
        s?.setItem(TRIAL_TOKEN_KEY, res.data.anon_token)
        s?.setItem(TRIAL_EXPIRES_KEY, String(Date.now() + res.data.expires_in * 1000))
        return res.data.anon_token
      })
      .finally(() => {
        inFlight = null
      })
  }
  return inFlight
}

/** Headers for a call made as the trial session (empty when there is none). */
export function trialHeaders(): Record<string, string> {
  const token = getTrialToken()
  return token ? { [TRIAL_HEADER]: token } : {}
}
