import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  KeyRound,
  LogOut,
  Mail,
  MonitorX,
  ShieldCheck,
  Sparkles,
  User as UserIcon,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { fieldLabel } from '@/components/AuthShell'
import { changePassword, logoutEverywhere } from '@/lib/api/services'
import { Switch } from '@/components/ui/switch'
import { Separator } from '@/components/ui/separator'
import { useAuth } from '@/store/auth'
import { toast } from '@/store/notifications'
import { DEMO_MODE } from '@/lib/api/client'
import { fmtDate } from '@/lib/utils'

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-3.5">
      <span className="text-sm text-text-mid">{label}</span>
      {children}
    </div>
  )
}

function SecuritySection() {
  const { user, setSession, logout } = useAuth()
  const navigate = useNavigate()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [revoking, setRevoking] = useState(false)

  const onChange = async (e: FormEvent) => {
    e.preventDefault()
    if (next.length < 8) return setError('New password must be at least 8 characters')
    if (next !== confirm) return setError('New passwords do not match')
    setError(null)
    setSaving(true)
    try {
      const res = await changePassword(current, next)
      // The old token is revoked server-side — including on this device — so
      // the returned one must replace it or the next request would 401.
      if (user) {
        setSession({
          access_token: res.access_token,
          token_type: res.token_type,
          expires_in: res.expires_in,
          email_sent: false,
          user,
        })
      }
      setCurrent('')
      setNext('')
      setConfirm('')
      toast.success('Password changed', res.message)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not change your password.')
    } finally {
      setSaving(false)
    }
  }

  const onRevokeAll = async () => {
    setRevoking(true)
    try {
      await logoutEverywhere()
      logout()
      toast.info('Signed out everywhere', 'Every device, including this one, has been signed out.')
      navigate('/login')
    } catch (err) {
      toast.error(
        'Sign out everywhere',
        err instanceof Error ? err.message : 'Could not sign out other devices right now.',
      )
    } finally {
      setRevoking(false)
    }
  }

  return (
    <section className="glass rounded-2xl p-6" aria-label="Security">
      <h2 className="mb-1 flex items-center gap-2 text-base font-semibold">
        <KeyRound className="h-4 w-4 text-neon" aria-hidden="true" /> Password &amp; sessions
      </h2>
      <p className="text-sm text-text-mid">Changing your password signs out every other device.</p>
      <form
        className="mt-4 grid gap-3 sm:grid-cols-3"
        onSubmit={(e) => void onChange(e)}
        noValidate
      >
        <div className="space-y-1.5">
          <label htmlFor="current-password" className={fieldLabel}>
            Current
          </label>
          <Input
            id="current-password"
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <label htmlFor="new-password" className={fieldLabel}>
            New
          </label>
          <Input
            id="new-password"
            type="password"
            autoComplete="new-password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <label htmlFor="confirm-new-password" className={fieldLabel}>
            Confirm new
          </label>
          <Input
            id="confirm-new-password"
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
        </div>
        {error && (
          <p role="alert" className="text-xs text-loss-soft sm:col-span-3">
            {error}
          </p>
        )}
        <div className="sm:col-span-3">
          <Button type="submit" loading={saving} disabled={!current || !next}>
            Change password
          </Button>
        </div>
      </form>

      <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-white/[0.06] pt-5">
        <div>
          <p className="text-sm font-medium text-text-hi">Sign out everywhere</p>
          <p className="text-xs text-text-mid">
            Lost a device, or signed in somewhere you shouldn&apos;t have? End every session now.
          </p>
        </div>
        <Button variant="outline" onClick={() => void onRevokeAll()} loading={revoking}>
          <MonitorX className="h-4 w-4" aria-hidden="true" /> Sign out everywhere
        </Button>
      </div>
    </section>
  )
}

export function Settings() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const [notifications, setNotifications] = useState({ news: true, weekly: false, product: true })

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Settings</h1>
        <p className="mt-1 text-sm text-text-mid">Manage your account and preferences.</p>
      </div>

      {/* Profile */}
      <section className="glass rounded-2xl p-6" aria-label="Profile">
        <div className="flex items-center gap-4">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-neon/25 to-grape/35 text-lg font-bold text-text-hi">
            {(user?.email ?? 'U').slice(0, 1).toUpperCase()}
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="flex items-center gap-2 text-base font-semibold">
              {user?.email}
              <span className="inline-flex items-center gap-1 rounded-full border border-gain/25 bg-gain/10 px-2 py-0.5 text-2xs font-semibold text-gain-soft">
                <ShieldCheck className="h-3 w-3" aria-hidden="true" /> Verified
              </span>
            </h2>
            <p className="mt-0.5 flex items-center gap-1.5 text-xs text-text-mid">
              <UserIcon className="h-3.5 w-3.5 text-text-low" aria-hidden="true" />
              {user?.plan} plan · joined {fmtDate(user?.created_at)}
            </p>
          </div>
        </div>
      </section>

      {/* Plan */}
      <section className="glass rounded-2xl p-6" aria-label="Plan">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h2 className="flex items-center gap-2 text-base font-semibold">
              <Sparkles className="h-4 w-4 text-neon" aria-hidden="true" /> Free plan
            </h2>
            <p className="mt-1 text-sm text-text-mid">
              Everything is free — screening, backtesting, news. No paywall, no credits, no limits.
            </p>
          </div>
          <Button variant="secondary" disabled>
            Current plan
          </Button>
        </div>
      </section>

      {!DEMO_MODE && <SecuritySection />}

      {/* Notifications */}
      <section className="glass rounded-2xl p-6" aria-label="Notifications">
        <h2 className="mb-2 flex items-center gap-2 text-base font-semibold">
          <Mail className="h-4 w-4 text-grape-soft" aria-hidden="true" /> Notifications
        </h2>
        <div className="divide-y divide-white/[0.06]">
          <Row label="Product updates">
            <Switch
              checked={notifications.product}
              onCheckedChange={(v) => setNotifications((s) => ({ ...s, product: v }))}
            />
          </Row>
          <Row label="News digests for your watchlist">
            <Switch
              checked={notifications.news}
              onCheckedChange={(v) => setNotifications((s) => ({ ...s, news: v }))}
            />
          </Row>
          <Row label="Weekly backtest recap">
            <Switch
              checked={notifications.weekly}
              onCheckedChange={(v) => setNotifications((s) => ({ ...s, weekly: v }))}
            />
          </Row>
        </div>
      </section>

      {DEMO_MODE && (
        <section className="glass rounded-2xl border-neon/20 p-6" aria-label="Demo mode">
          <h2 className="text-base font-semibold text-neon">Demo mode</h2>
          <p className="mt-1 text-sm text-text-mid">
            This build is serving synthetic data through the typed API interface. Set{' '}
            <code className="rounded bg-white/[0.07] px-1.5 py-0.5 font-mono text-xs text-neon">
              VITE_DEMO_MODE=false
            </code>{' '}
            and point{' '}
            <code className="rounded bg-white/[0.07] px-1.5 py-0.5 font-mono text-xs text-neon">
              VITE_API_URL
            </code>{' '}
            at your Stratoscan backend to go live.
          </p>
        </section>
      )}

      <Separator />

      {/* Danger zone */}
      <section className="glass rounded-2xl border-loss/20 p-6" aria-label="Danger zone">
        <h2 className="text-base font-semibold text-loss-soft">Sign out</h2>
        <p className="mt-1 text-sm text-text-mid">
          You can sign back in any time — your watchlist and screens are kept locally.
        </p>
        <Button
          variant="danger"
          className="mt-4"
          onClick={() => {
            logout()
            toast.info('Signed out', 'See you soon.')
            navigate('/')
          }}
        >
          <LogOut className="h-4 w-4" aria-hidden="true" /> Sign out
        </Button>
      </section>
    </div>
  )
}
