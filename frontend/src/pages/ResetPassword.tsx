import { useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { CheckCircle2, KeyRound, XCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { AuthShell, fieldLabel } from '@/components/AuthShell'
import { confirmPasswordReset } from '@/lib/api/services'

const MIN_LENGTH = 8

export function ResetPassword() {
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [fieldError, setFieldError] = useState<string | null>(null)
  const [linkError, setLinkError] = useState<string | null>(
    token ? null : 'This reset link is missing its token. Open the link from your email again.',
  )
  const [saving, setSaving] = useState(false)
  const [done, setDone] = useState<string | null>(null)

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (password.length < MIN_LENGTH) {
      setFieldError(`Password must be at least ${MIN_LENGTH} characters`)
      return
    }
    if (password !== confirm) {
      setFieldError('Passwords do not match')
      return
    }
    setFieldError(null)
    setSaving(true)
    try {
      const res = await confirmPasswordReset(token, password)
      setDone(res.message)
    } catch (err) {
      // 400 = invalid / used / expired link; the server's detail says which.
      setLinkError(
        err instanceof Error ? err.message : 'This reset link is invalid or has expired.',
      )
    } finally {
      setSaving(false)
    }
  }

  if (done) {
    return (
      <AuthShell>
        <div className="text-center">
          <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-gain/10">
            <CheckCircle2 className="h-7 w-7 text-gain-soft" aria-hidden="true" />
          </div>
          <h1 className="text-lg font-semibold">Password updated</h1>
          <p role="status" className="mt-2 text-sm leading-relaxed text-text-mid">
            {done}
          </p>
          <Link to="/login" className="mt-6 inline-block">
            <Button>Log in</Button>
          </Link>
        </div>
      </AuthShell>
    )
  }

  if (linkError) {
    return (
      <AuthShell>
        <div className="text-center">
          <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-loss/10">
            <XCircle className="h-7 w-7 text-loss-soft" aria-hidden="true" />
          </div>
          <h1 className="text-lg font-semibold">This link can&apos;t be used</h1>
          <p role="alert" className="mt-2 text-sm leading-relaxed text-text-mid">
            {linkError}
          </p>
          <div className="mt-6 flex justify-center gap-3">
            <Link to="/forgot-password">
              <Button>Send a new link</Button>
            </Link>
            <Link to="/login">
              <Button variant="outline">Log in</Button>
            </Link>
          </div>
        </div>
      </AuthShell>
    )
  }

  return (
    <AuthShell>
      <h1 className="text-lg font-semibold">Choose a new password</h1>
      <p className="mt-1 text-sm text-text-mid">
        Every device signed in to this account will be signed out.
      </p>
      <form className="mt-6 space-y-4" onSubmit={(e) => void onSubmit(e)} noValidate>
        <div className="space-y-1.5">
          <label htmlFor="new-password" className={fieldLabel}>
            New password
          </label>
          <Input
            id="new-password"
            type="password"
            autoComplete="new-password"
            placeholder={`At least ${MIN_LENGTH} characters`}
            icon={<KeyRound aria-hidden="true" />}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            aria-invalid={!!fieldError}
          />
        </div>
        <div className="space-y-1.5">
          <label htmlFor="confirm-password" className={fieldLabel}>
            Confirm password
          </label>
          <Input
            id="confirm-password"
            type="password"
            autoComplete="new-password"
            placeholder="Repeat your new password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            aria-invalid={!!fieldError}
          />
          {fieldError && (
            <p role="alert" className="text-xs text-loss-soft">
              {fieldError}
            </p>
          )}
        </div>
        <Button type="submit" className="w-full" loading={saving}>
          Set new password
        </Button>
      </form>
    </AuthShell>
  )
}
