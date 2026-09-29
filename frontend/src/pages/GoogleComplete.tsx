import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { Logo } from '@/components/Logo'
import { setToken } from '@/lib/api/client'
import * as services from '@/lib/api/services'
import { useAuth } from '@/store/auth'

/**
 * Landing page for "Continue with Google".
 *
 * The API finishes the OAuth flow server-side and then redirects here as
 * `/auth/google/complete#token=<jwt>&expires_in=<seconds>` — the token is in
 * the FRAGMENT on purpose: fragments are never sent to a server, so the token
 * cannot end up in an access log, a Referer header or a proxy cache. That
 * protection only holds if this page deletes it immediately, which is the
 * first thing the effect below does.
 *
 * This page is public (the user is not signed in yet) and holds no UI beyond
 * a spinner: it either hands off to /app or bounces to /login with a reason.
 */
export function GoogleComplete() {
  const navigate = useNavigate()
  const [message, setMessage] = useState('Signing you in…')
  // React 18 StrictMode runs effects twice in dev; without this guard the
  // fragment would already be gone on the second run and the flow would look
  // like a failure.
  const started = useRef(false)

  useEffect(() => {
    if (started.current) return
    started.current = true

    const params = new URLSearchParams(window.location.hash.replace(/^#/, ''))
    const token = params.get('token') ?? ''
    const expiresIn = Number(params.get('expires_in') ?? 0)

    // Wipe the credential from the address bar before anything else can read
    // it — the token is already in this closure, and it must not survive a
    // screenshot, a bookmark or a shared link.
    if (window.location.hash) {
      window.history.replaceState(null, '', window.location.pathname)
    }

    if (!token) {
      navigate('/login?oauth_error=failed', { replace: true })
      return
    }

    let alive = true
    // The axios interceptor reads the token out of storage, so it has to be
    // stored before /auth/me is called.
    setToken(token)
    void services
      .me()
      .then((user) => {
        if (!alive) return
        useAuth.getState().setSession({
          access_token: token,
          token_type: 'bearer',
          expires_in: expiresIn,
          user,
          email_sent: false,
        })
        navigate('/app', { replace: true })
      })
      .catch(() => {
        if (!alive) return
        setMessage('Signing you in failed. Taking you back to login…')
        // Never leave a token behind that nothing can use: a stored token with
        // no session attached would make the next page look signed-in and then
        // 401 on the first request.
        setToken(null)
        navigate('/login?oauth_error=failed', { replace: true })
      })

    return () => {
      alive = false
    }
  }, [navigate])

  return (
    <div
      id="main-content"
      tabIndex={-1}
      className="relative flex min-h-screen items-center justify-center px-5 outline-none"
    >
      <div
        className="pointer-events-none absolute -top-24 left-1/2 h-72 w-[480px] -translate-x-1/2 rounded-full bg-neon/[0.09] blur-[90px]"
        aria-hidden="true"
      />
      <div className="relative w-full max-w-md text-center">
        <div className="mb-8 flex justify-center">
          <Logo />
        </div>
        <div className="glass-strong rounded-3xl p-8 shadow-card">
          <div
            className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-neon/10"
            aria-hidden="true"
          >
            <Loader2 className="h-6 w-6 animate-spin text-neon" />
          </div>
          <h1 className="text-lg font-semibold">Almost there</h1>
          <p role="status" className="mt-2 text-sm leading-relaxed text-text-mid">
            {message}
          </p>
        </div>
      </div>
    </div>
  )
}
