import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { zodResolver } from '@hookform/resolvers/zod'
import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { ArrowLeft, ArrowRight, Mail, ShieldCheck, Sparkles } from 'lucide-react'
import { Button, buttonVariants } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Logo } from '@/components/Logo'
import { useAuth } from '@/store/auth'
import { toast } from '@/store/notifications'
import { API_ORIGIN, DEMO_MODE } from '@/lib/api/client'
import { cn } from '@/lib/utils'

/**
 * Friendly copy for each `oauth_error` code the API redirects back with
 * (see src/api.py + src/google_oauth.py). One sentence each, never the raw
 * code: the codes exist to route, not to be read.
 */
const OAUTH_ERROR_MESSAGES: Record<string, string> = {
  unavailable: 'Google sign-in is not set up on this server. Use your email and password instead.',
  state: 'That sign-in attempt could not be verified and has expired. Please try again.',
  exchange: 'Google sign-in could not be completed. Please try again.',
  token: "We couldn't verify the response from Google. Please try again.",
  unverified: "Your Google account's email isn't verified",
  failed: 'Google sign-in failed. Please try again.',
}
const OAUTH_ERROR_FALLBACK = 'Google sign-in failed. Please try again.'

/** Google's official four-colour "G", inline (no icon dependency). */
function GoogleMark() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" aria-hidden="true">
      <path
        fill="#4285F4"
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
      />
      <path
        fill="#34A853"
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
      />
      <path
        fill="#FBBC05"
        d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
      />
      <path
        fill="#EA4335"
        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
      />
    </svg>
  )
}

const loginSchema = z.object({
  email: z.string().email('Enter a valid email address'),
  password: z.string().min(1, 'Password is required'),
})
const signupSchema = z
  .object({
    email: z.string().email('Enter a valid email address'),
    password: z.string().min(8, 'Password must be at least 8 characters'),
    confirm: z.string(),
  })
  .refine((d) => d.password === d.confirm, {
    path: ['confirm'],
    message: 'Passwords do not match',
  })

interface AuthValues {
  email: string
  password: string
  confirm: string
}

export function Auth({ mode: initialMode }: { mode: 'login' | 'signup' }) {
  const [mode, setMode] = useState<'login' | 'signup'>(initialMode)
  const navigate = useNavigate()
  const location = useLocation()
  const { login, register } = useAuth()
  const [submitting, setSubmitting] = useState(false)

  const next = (location.state as { from?: string } | null)?.from
  const params = new URLSearchParams(location.search)
  const nextParam = next ?? params.get('next') ?? '/app'
  // "Continue with Google" comes back through /login?oauth_error=… — the API
  // redirects here with a code, and this is the only place it is turned into
  // something a person can act on.
  const oauthErrorCode = params.get('oauth_error')
  const oauthError = oauthErrorCode
    ? (OAUTH_ERROR_MESSAGES[oauthErrorCode] ?? OAUTH_ERROR_FALLBACK)
    : null

  const form = useForm<AuthValues>({
    resolver: zodResolver(mode === 'login' ? loginSchema : signupSchema) as never,
    defaultValues: { email: '', password: '', confirm: '' },
  })

  useEffect(() => {
    form.reset({ email: '', password: '', confirm: '' })
    form.clearErrors()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode])

  const onSubmit = async (values: AuthValues) => {
    setSubmitting(true)
    try {
      const user =
        mode === 'login'
          ? await login(values.email, values.password)
          : await register(values.email, values.password)
      toast.success(
        mode === 'login' ? 'Welcome back' : 'Account created',
        `Signed in as ${user.email}`,
      )
      navigate(nextParam, { replace: true })
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Something went wrong. Please try again.'
      toast.error(mode === 'login' ? 'Sign in failed' : 'Sign up failed', message)
    } finally {
      setSubmitting(false)
    }
  }

  const isLogin = mode === 'login'

  return (
    <div
      id="main-content"
      tabIndex={-1}
      className="relative flex min-h-screen items-center justify-center overflow-hidden px-5 py-10 outline-none"
    >
      {/* Ambient background */}
      <div
        className="pointer-events-none absolute -top-32 left-1/2 h-96 w-[640px] -translate-x-1/2 rounded-full bg-neon/[0.08] blur-[100px]"
        aria-hidden="true"
      />
      <div
        className="pointer-events-none absolute bottom-0 right-0 h-80 w-80 rounded-full bg-grape/[0.12] blur-[90px]"
        aria-hidden="true"
      />

      <div className="relative w-full max-w-md">
        <Link
          to="/"
          className="mb-6 inline-flex items-center gap-2 text-xs text-text-low transition-colors hover:text-text-hi"
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" /> Back to home
        </Link>

        <div className="glass-strong overflow-hidden rounded-3xl shadow-card">
          {/* Header + mode tabs */}
          <div className="px-8 pb-2 pt-8">
            <div className="mb-6 flex justify-center">
              <Logo />
            </div>

            <div className="mb-6 grid grid-cols-2 rounded-xl bg-white/[0.05] p-1">
              {(['login', 'signup'] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => setMode(m)}
                  aria-pressed={mode === m}
                  className={cn(
                    'rounded-lg py-2 text-sm font-semibold transition-all duration-200',
                    mode === m
                      ? 'bg-white/[0.09] text-neon shadow-[inset_0_0_0_1px_rgba(0,212,255,0.25)]'
                      : 'text-text-mid hover:text-text-hi',
                  )}
                >
                  {m === 'login' ? 'Log in' : 'Sign up'}
                </button>
              ))}
            </div>
          </div>

          <AnimatePresence mode="wait">
            <motion.form
              key={mode}
              initial={{ opacity: 0, x: isLogin ? -14 : 14 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: isLogin ? 14 : -14 }}
              transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
              onSubmit={form.handleSubmit(onSubmit)}
              className="space-y-4 px-8 pb-8"
              noValidate
            >
              <h1 className="text-center text-xl font-bold text-text-hi">
                {isLogin ? 'Welcome back' : 'Create your free account'}
              </h1>
              <p className="text-center text-sm text-text-mid">
                {isLogin
                  ? 'Screen and backtest — free, always.'
                  : 'Free forever. No paywall, no credits.'}
              </p>

              {oauthError && (
                <p
                  role="alert"
                  className="rounded-xl border border-loss/30 bg-loss/10 px-3 py-2 text-xs text-loss-soft"
                >
                  {oauthError}
                </p>
              )}

              {/*
                A plain link, not an axios call: the browser has to NAVIGATE to
                the API's /auth/google/start so the 302 to Google (and the state
                cookie it sets) are handled by the browser itself.
              */}
              {!DEMO_MODE && (
                <>
                  <a
                    href={`${API_ORIGIN}/auth/google/start`}
                    className={cn(buttonVariants({ variant: 'secondary' }), 'w-full')}
                  >
                    <GoogleMark />
                    Continue with Google
                  </a>

                  <div className="flex items-center gap-3" aria-hidden="true">
                    <span className="h-px flex-1 bg-white/10" />
                    <span className="text-2xs uppercase tracking-wider text-text-low">or</span>
                    <span className="h-px flex-1 bg-white/10" />
                  </div>
                </>
              )}

              <div className="space-y-1.5">
                <label
                  htmlFor="email"
                  className="text-2xs font-semibold uppercase tracking-wider text-text-low"
                >
                  Email
                </label>
                <Input
                  id="email"
                  type="email"
                  icon={<Mail className="text-text-low" aria-hidden="true" />}
                  placeholder="you@example.com"
                  autoComplete="email"
                  spellCheck={false}
                  {...form.register('email')}
                  aria-invalid={!!form.formState.errors.email}
                />
                {form.formState.errors.email && (
                  <p role="alert" className="text-xs text-loss-soft">
                    {form.formState.errors.email.message}
                  </p>
                )}
              </div>

              <div className="space-y-1.5">
                <div className="flex items-baseline justify-between">
                  <label
                    htmlFor="password"
                    className="text-2xs font-semibold uppercase tracking-wider text-text-low"
                  >
                    Password
                  </label>
                  {isLogin && (
                    <Link
                      to="/forgot-password"
                      className="text-2xs text-text-mid transition-colors hover:text-neon"
                    >
                      Forgot password?
                    </Link>
                  )}
                </div>
                <Input
                  id="password"
                  type="password"
                  placeholder={isLogin ? 'Your password' : 'At least 8 characters'}
                  autoComplete={isLogin ? 'current-password' : 'new-password'}
                  {...form.register('password')}
                  aria-invalid={!!form.formState.errors.password}
                />
                {form.formState.errors.password && (
                  <p role="alert" className="text-xs text-loss-soft">
                    {form.formState.errors.password.message}
                  </p>
                )}
              </div>

              {!isLogin && (
                <div className="space-y-1.5">
                  <label
                    htmlFor="confirm"
                    className="text-2xs font-semibold uppercase tracking-wider text-text-low"
                  >
                    Confirm password
                  </label>
                  <Input
                    id="confirm"
                    type="password"
                    placeholder="Repeat your password"
                    autoComplete="new-password"
                    {...form.register('confirm')}
                    aria-invalid={!!form.formState.errors.confirm}
                  />
                  {form.formState.errors.confirm && (
                    <p role="alert" className="text-xs text-loss-soft">
                      {form.formState.errors.confirm.message}
                    </p>
                  )}
                </div>
              )}

              <Button type="submit" size="lg" className="w-full" loading={submitting}>
                {submitting ? 'Please wait…' : isLogin ? 'Log in' : 'Create account'}
                {!submitting && <ArrowRight className="h-4 w-4" aria-hidden="true" />}
              </Button>

              {DEMO_MODE && (
                <p className="flex items-center justify-center gap-1.5 text-center text-2xs text-text-low">
                  <Sparkles className="h-3 w-3 text-neon" aria-hidden="true" />
                  Demo mode — any email + 8-char password works
                </p>
              )}
            </motion.form>
          </AnimatePresence>
        </div>

        <p className="mt-6 flex items-center justify-center gap-1.5 text-center text-xs text-text-low">
          <ShieldCheck className="h-3.5 w-3.5 text-gain-soft" aria-hidden="true" />
          Your data is encrypted in transit. Password hashed with PBKDF2.
        </p>

        <p className="mt-3 flex items-center justify-center gap-3 text-center text-xs text-text-low">
          <Link to="/terms" className="transition-colors hover:text-text-hi">
            Terms of Service
          </Link>
          <span aria-hidden="true">·</span>
          <Link to="/privacy" className="transition-colors hover:text-text-hi">
            Privacy Policy
          </Link>
        </p>
      </div>
    </div>
  )
}
