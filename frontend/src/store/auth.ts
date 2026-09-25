import { create } from 'zustand'
import type { AuthResponse, UserOut } from '@/lib/api/types'
import {
  DEMO_MODE,
  getToken,
  setToken,
  setUnauthorizedHandler,
  TOKEN_EXPIRES_KEY,
  TOKEN_KEY,
} from '@/lib/api/client'
import * as services from '@/lib/api/services'
import { usePersonal } from './personal'

const USER_KEY = 'stratoscan_user'
// Single source of truth: the expiry key lives in client.ts (token storage).
const EXPIRES_KEY = TOKEN_EXPIRES_KEY

/** Renew the token once it is within this window of its deadline. */
export const REFRESH_WHEN_WITHIN_MS = 2 * 24 * 60 * 60 * 1000
let refreshInFlight = false

interface AuthState {
  user: UserOut | null
  token: string | null
  bootstrapped: boolean
  login: (email: string, password: string) => Promise<UserOut>
  register: (email: string, password: string) => Promise<UserOut>
  logout: () => void
  bootstrap: () => Promise<void>
  setSession: (res: AuthResponse) => UserOut
  refreshIfStale: () => Promise<void>
}

function readStoredUser(): UserOut | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.localStorage.getItem(USER_KEY)
    return raw ? (JSON.parse(raw) as UserOut) : null
  } catch {
    return null
  }
}

export const useAuth = create<AuthState>((set, get) => ({
  user: readStoredUser(),
  token: getToken(),
  bootstrapped: false,

  setSession: (res) => {
    setToken(res.access_token)
    window.localStorage.setItem(USER_KEY, JSON.stringify(res.user))
    // Persist the absolute expiry so a stale-but-unexpired-looking token from
    // a previous session is never trusted after its deadline (backend JWT TTL).
    const expiresAt = res.expires_in ? Date.now() + res.expires_in * 1000 : null
    if (expiresAt) window.localStorage.setItem(EXPIRES_KEY, String(expiresAt))
    else window.localStorage.removeItem(EXPIRES_KEY)
    set({ token: res.access_token, user: res.user, bootstrapped: true })
    // Pull the user's saved watchlist/screeners/backtests from the backend
    // (previously localStorage-only, so state died with the device). Local
    // cache wins while empty on the server; server data wins otherwise.
    void usePersonal.getState().hydrateFromServer()
    return res.user
  },

  login: async (email, password) => {
    const res = await services.login({ email, password })
    return get().setSession(res)
  },

  register: async (email, password) => {
    const res = await services.register({ email, password })
    return get().setSession(res)
  },

  logout: () => {
    setToken(null)
    window.localStorage.removeItem(USER_KEY)
    window.localStorage.removeItem(EXPIRES_KEY)
    set({ token: null, user: null, bootstrapped: true })
  },

  refreshIfStale: async () => {
    // Sliding session: while the user is active, renew a token that is close
    // to its deadline so a long working session never ends in a hard logout.
    // The server refuses to extend a sign-in older than SESSION_MAX_AGE_DAYS,
    // and any failure here is silent — the normal expiry path still applies.
    if (DEMO_MODE || !get().token || refreshInFlight) return
    const expRaw = window.localStorage.getItem(EXPIRES_KEY)
    const expiresAt = expRaw ? Number(expRaw) : NaN
    if (!Number.isFinite(expiresAt) || expiresAt <= Date.now()) return
    if (expiresAt - Date.now() > REFRESH_WHEN_WITHIN_MS) return
    refreshInFlight = true
    try {
      const res = await services.refreshSession()
      setToken(res.access_token)
      window.localStorage.setItem(EXPIRES_KEY, String(Date.now() + res.expires_in * 1000))
      set({ token: res.access_token })
    } catch {
      // Refused (too old / revoked) or offline: leave the session as it is.
    } finally {
      refreshInFlight = false
    }
  },

  bootstrap: async () => {
    // In demo mode a stored user is trusted; otherwise re-validate with /auth/me.
    if (get().bootstrapped) return
    if (get().token) {
      if (!DEMO_MODE) {
        const expRaw = window.localStorage.getItem(EXPIRES_KEY)
        if (expRaw && Number(expRaw) <= Date.now()) {
          // Deadline passed — the JWT is already invalid server-side. Skip
          // the doomed /auth/me call (it would 401) and log out immediately.
          get().logout()
          set({ bootstrapped: true })
          return
        }
        try {
          const user = await services.me()
          window.localStorage.setItem(USER_KEY, JSON.stringify(user))
          set({ user, bootstrapped: true })
          // Refresh the user's server-synced personal data on cold start.
          void usePersonal.getState().hydrateFromServer()
          void get().refreshIfStale()
          return
        } catch {
          get().logout()
          set({ bootstrapped: true })
          return
        }
      }
    }
    set({ bootstrapped: true })
  },
}))

// Wire the 401 auto-logout: any 401 outside the auth endpoints (expired/revoked
// JWT mid-session) clears the session. ProtectedRoute then redirects to /login
// because token+user become null. Navigating via window.location keeps this
// working even when the caller isn't inside a router context.
if (typeof window !== 'undefined') {
  setUnauthorizedHandler(() => {
    const store = useAuth.getState()
    const wasBootstrapped = store.bootstrapped
    store.logout()
    // During initial bootstrap a 401 from /auth/me is the *expected* signal for
    // a stale token — ProtectedRoute already redirects to /login the moment
    // token+user become null, so a hard page reload would be wasteful. Only
    // hard-navigate for genuine mid-session 401s (token expired while using
    // the app), and never loop if we're already on /login.
    if (
      wasBootstrapped &&
      window.location.pathname !== '/login' &&
      !window.location.pathname.startsWith('/auth')
    ) {
      window.location.assign('/login')
    }
  })

  // Activity-driven renewal: coming back to the tab, and a slow heartbeat
  // while it stays visible. Cheap — refreshIfStale is a no-op until the
  // token is inside its renewal window.
  const renew = () => {
    if (document.visibilityState === 'visible') void useAuth.getState().refreshIfStale()
  }
  window.addEventListener('focus', renew)
  document.addEventListener('visibilitychange', renew)
  window.setInterval(renew, 30 * 60 * 1000)

  // Multi-tab session sync: logging out (or in) in ANY tab updates every
  // other tab instantly via the localStorage `storage` event — no more stale
  // authenticated tabs after logout elsewhere.
  window.addEventListener('storage', (e) => {
    const store = useAuth.getState()

    // Token rotated (or a session appeared) in another tab.
    if (e.key === TOKEN_KEY || e.key === EXPIRES_KEY) {
      const token = getToken()
      if (!store.token) {
        // A session appeared in another tab — adopt it. (The USER_KEY event
        // that follows carries the user profile.)
        useAuth.setState({ token, bootstrapped: true })
      } else if (token) {
        useAuth.setState({ token })
      }
      return
    }

    if (e.key !== USER_KEY) return
    if (e.newValue === null) {
      // Logged out in another tab — mirror it here.
      if (store.token) store.logout()
    } else {
      // Logged in / user refreshed in another tab — adopt the user + token.
      try {
        const user = JSON.parse(e.newValue) as UserOut
        const token = getToken()
        useAuth.setState({ user, token, bootstrapped: true })
      } catch {
        /* malformed payload — ignore */
      }
    }
  })
}
