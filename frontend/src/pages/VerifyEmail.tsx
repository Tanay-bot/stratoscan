import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { CheckCircle2, Mail, XCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Logo } from '@/components/Logo'
import { api, unwrap } from '@/lib/api/client'
import { resendVerification } from '@/lib/api/services'
import { toast } from '@/store/notifications'
import type { ApiErrorDetail } from '@/lib/api/types'

type State = 'verifying' | 'success' | 'error'

export function VerifyEmail() {
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const [state, setState] = useState<State>('verifying')
  const [message, setMessage] = useState('')
  const [sending, setSending] = useState(false)

  const onResend = async () => {
    setSending(true)
    try {
      const res = await resendVerification()
      toast.info('Verification email', res.message)
    } catch {
      toast.error(
        'Verification email',
        'Could not send the verification email right now. Try again later.',
      )
    } finally {
      setSending(false)
    }
  }

  useEffect(() => {
    if (!token) {
      setState('error')
      setMessage(
        'This verification link is missing its token. Check the link in your email and try again.',
      )
      return
    }
    let alive = true
    void unwrap<ApiErrorDetail>(api.get('/auth/verify-email', { params: { token } }))
      .then((res) => {
        if (!alive) return
        setState('success')
        setMessage(res.message ?? 'Email verified successfully. You can now log in.')
      })
      .catch(() => {
        if (!alive) return
        setState('error')
        setMessage(
          'This verification link is invalid or has expired. Request a new one and try again.',
        )
      })
    return () => {
      alive = false
    }
  }, [token])

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
          {state === 'verifying' && (
            <>
              <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-neon/10">
                <Mail className="h-6 w-6 animate-pulse text-neon" aria-hidden="true" />
              </div>
              <h1 className="text-lg font-semibold">Verifying your email…</h1>
            </>
          )}

          {state === 'success' && (
            <>
              <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-gain/10">
                <CheckCircle2 className="h-7 w-7 text-gain-soft" aria-hidden="true" />
              </div>
              <h1 className="text-lg font-semibold">Email verified</h1>
              <p className="mt-2 text-sm leading-relaxed text-text-mid">{message}</p>
              <Link to="/login" className="mt-6 inline-block">
                <Button>Go to login</Button>
              </Link>
            </>
          )}

          {state === 'error' && (
            <>
              <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-loss/10">
                <XCircle className="h-7 w-7 text-loss-soft" aria-hidden="true" />
              </div>
              <h1 className="text-lg font-semibold">Link not valid</h1>
              <p className="mt-2 text-sm leading-relaxed text-text-mid">{message}</p>
              <div className="mt-6 flex justify-center gap-3">
                <Button variant="outline" onClick={() => void onResend()} loading={sending}>
                  Resend link
                </Button>
                <Link to="/login">
                  <Button>Log in</Button>
                </Link>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
