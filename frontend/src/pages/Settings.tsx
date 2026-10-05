import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import {
  KeyRound,
  LogOut,
  Mail,
  MonitorX,
  ShieldCheck,
  Sparkles,
  Trash2,
  User as UserIcon,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { fieldLabel } from '@/components/AuthShell'
import {
  changePassword,
  deleteAccount,
  getEmailPreferences,
  logoutEverywhere,
  setWeeklyDigest,
} from '@/lib/api/services'
import { toApiError } from '@/lib/api/client'
import { Switch } from '@/components/ui/switch'
import { Separator } from '@/components/ui/separator'
import { useAuth } from '@/store/auth'
import { toast } from '@/store/notifications'
import { DEMO_MODE } from '@/lib/api/client'
import { useEntitlements } from '@/store/entitlements'
import { fmtDate } from '@/lib/utils'

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

/** "12 of 50" / "unlimited" — one line of the plan card. */
function Allowance({ label, used, limit }: { label: string; used: number; limit: number | null }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2 text-sm">
      <span className="text-text-mid">{label}</span>
      <span className="font-mono tabular text-text-hi">
        {limit === null ? `${used} · unlimited` : `${used} of ${limit}`}
      </span>
    </div>
  )
}

function Included({ label, on }: { label: string; on: boolean }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2 text-sm">
      <span className="text-text-mid">{label}</span>
      <span className={on ? 'text-gain-soft' : 'text-text-low'}>
        {on ? 'Included' : 'Pro plan'}
      </span>
    </div>
  )
}

/**
 * Day 12: the account's real plan — from GET /me/entitlements, the same
 * decision the API enforces. No prices: see lib/pricing.ts.
 */
export function PlanSection() {
  const ent = useEntitlements()
  if (!ent) {
    return (
      <section className="glass rounded-2xl p-6" aria-label="Plan">
        <h2 className="flex items-center gap-2 text-base font-semibold">
          <Sparkles className="h-4 w-4 text-neon" aria-hidden="true" /> Plan
        </h2>
        <p className="mt-2 text-sm text-text-low">Loading your plan…</p>
      </section>
    )
  }
  const resets = ent.resets_at ? fmtDate(ent.resets_at) : ''
  return (
    <section className="glass rounded-2xl p-6" aria-label="Plan">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-base font-semibold">
            <Sparkles className="h-4 w-4 text-neon" aria-hidden="true" /> {ent.plan_label} plan
          </h2>
          <p className="mt-1 text-sm text-text-mid">
            {ent.grandfathered
              ? 'Pro for life — this is one of the first accounts, and that does not expire.'
              : ent.plan === 'pro'
                ? 'Every capability, with no monthly backtest limit.'
                : 'Free forever. The numbers, the robustness checks and the code behind every result are never part of a paid plan.'}
          </p>
        </div>
        {ent.plan === 'free' && (
          <Link to="/contact" className="text-sm font-medium text-neon hover:underline">
            Ask about Pro
          </Link>
        )}
      </div>
      {ent.pending_plan && ent.change_at && (
        <p className="mt-3 rounded-lg bg-white/[0.04] px-3 py-2 text-xs text-text-mid">
          {ent.in_grace
            ? `Pro access continues until ${fmtDate(ent.change_at)}, then this account moves to the ${ent.pending_plan === 'free' ? 'Free' : 'Pro'} plan.`
            : `Changes to the ${ent.pending_plan === 'free' ? 'Free' : 'Pro'} plan on ${fmtDate(ent.change_at)} — the end of this period.`}
        </p>
      )}
      <div className="mt-3 divide-y divide-white/[0.06]">
        <Allowance
          label={`Backtests this month${resets ? ` (resets ${resets})` : ''}`}
          used={ent.usage.backtests}
          limit={ent.limits.backtests_per_month}
        />
        <Allowance
          label="Saved strategies"
          used={ent.usage.saved_strategies}
          limit={ent.limits.saved_strategies}
        />
        <Included label="CSV export" on={ent.limits.csv_export} />
        <Included label="Weekly emails on saved strategies" on={ent.limits.monitoring_emails} />
      </div>
    </section>
  )
}

/**
 * Day 10: the one email this product sends on a schedule — the weekly update
 * on saved strategies. The switch is server state (every digest run re-reads
 * it right before sending), so turning it off takes effect at once.
 */
export function EmailSection() {
  const [prefs, setPrefs] = useState<{ weekly_digest: boolean; email_verified: boolean } | null>(
    null,
  )
  const [failed, setFailed] = useState(false)
  const [saving, setSaving] = useState(false)
  const switchId = useId()
  const ref = useRef<HTMLElement>(null)
  const { hash } = useLocation()

  useEffect(() => {
    let live = true
    getEmailPreferences()
      .then((p) => live && setPrefs(p))
      .catch(() => live && setFailed(true))
    return () => {
      live = false
    }
  }, [])

  // Links from the dashboard and the email land on #email.
  useEffect(() => {
    if (hash === '#email') ref.current?.scrollIntoView?.({ block: 'start' })
  }, [hash, prefs])

  const change = async (enabled: boolean) => {
    if (!prefs) return
    const before = prefs
    setPrefs({ ...prefs, weekly_digest: enabled })
    setSaving(true)
    try {
      await setWeeklyDigest(enabled)
      toast.success(enabled ? 'Weekly updates on' : 'Weekly updates off')
    } catch (err) {
      setPrefs(before)
      toast.error('Could not save', toApiError(err).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <section
      ref={ref}
      id="email"
      className="glass scroll-mt-24 rounded-2xl p-6"
      aria-labelledby={`${switchId}-title`}
    >
      <h2 id={`${switchId}-title`} className="mb-2 flex items-center gap-2 text-base font-semibold">
        <Mail className="h-4 w-4 text-grape-soft" aria-hidden="true" /> Email
      </h2>
      <div className="flex items-center justify-between gap-4 py-3.5">
        <label htmlFor={switchId} className="text-sm text-text-mid">
          Weekly update on my{' '}
          <Link to="/app" className="text-neon hover:underline">
            saved strategies
          </Link>
        </label>
        <Switch
          id={switchId}
          checked={prefs?.weekly_digest ?? false}
          disabled={!prefs || saving}
          onCheckedChange={(v) => void change(v)}
        />
      </div>
      <p className="text-xs text-text-low">
        {failed
          ? 'Couldn’t load your email settings. Reload the page to try again.'
          : prefs && !prefs.email_verified
            ? 'Verify your email address to receive it — we only send to confirmed addresses.'
            : 'At most one email a week, only when you follow a strategy. It shows how each one has done since you saved it — losses included — next to SPY. Account emails like password resets always arrive.'}
      </p>
    </section>
  )
}

function DeleteAccountSection() {
  const { logout } = useAuth()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [password, setPassword] = useState('')
  const [typed, setTyped] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  const onDelete = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    setDeleting(true)
    try {
      const res = await deleteAccount(password)
      logout()
      toast.info('Account deleted', res.message)
      navigate('/')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete your account.')
    } finally {
      setDeleting(false)
    }
  }

  return (
    <section className="glass rounded-2xl border-loss/20 p-6" aria-label="Delete account">
      <h2 className="flex items-center gap-2 text-base font-semibold text-loss-soft">
        <Trash2 className="h-4 w-4" aria-hidden="true" /> Delete account
      </h2>
      <p className="mt-1 text-sm text-text-mid">
        Permanently deletes your account, watchlist, saved screens and backtest history. This cannot
        be undone.
      </p>
      {!open ? (
        <Button variant="outline" className="mt-4" onClick={() => setOpen(true)}>
          Delete my account…
        </Button>
      ) : (
        <form className="mt-4 space-y-3" onSubmit={(e) => void onDelete(e)} noValidate>
          <div className="space-y-1.5">
            <label htmlFor="delete-password" className={fieldLabel}>
              Your password
            </label>
            <Input
              id="delete-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <label htmlFor="delete-confirm" className={fieldLabel}>
              Type DELETE to confirm
            </label>
            <Input
              id="delete-confirm"
              autoComplete="off"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
            />
          </div>
          {error && (
            <p role="alert" className="text-xs text-loss-soft">
              {error}
            </p>
          )}
          <div className="flex gap-3">
            <Button
              type="submit"
              variant="danger"
              loading={deleting}
              disabled={!password || typed !== 'DELETE'}
            >
              Permanently delete
            </Button>
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
          </div>
        </form>
      )}
    </section>
  )
}

export function Settings() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

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

      {/* Plan (Day 12) */}
      {!DEMO_MODE && <PlanSection />}

      {!DEMO_MODE && <SecuritySection />}

      {!DEMO_MODE && <EmailSection />}

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

      {!DEMO_MODE && <DeleteAccountSection />}
    </div>
  )
}
