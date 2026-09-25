import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, Mail, MailCheck } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { AuthShell, fieldLabel } from '@/components/AuthShell'
import { requestPasswordReset } from '@/lib/api/services'

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export function ForgotPassword() {
  const [email, setEmail] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  // The server's message is shown verbatim. It is deliberately identical for
  // addresses with and without an account, so this page must never branch on
  // anything but success vs. transport failure.
  const [sentMessage, setSentMessage] = useState<string | null>(null)

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (!EMAIL_RE.test(email.trim())) {
      setError('Enter a valid email address')
      return
    }
    setError(null)
    setSending(true)
    try {
      const res = await requestPasswordReset(email.trim())
      setSentMessage(res.message)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong. Please try again.')
    } finally {
      setSending(false)
    }
  }

  if (sentMessage) {
    return (
      <AuthShell>
        <div className="text-center">
          <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-neon/10">
            <MailCheck className="h-6 w-6 text-neon" aria-hidden="true" />
          </div>
          <h1 className="text-lg font-semibold">Check your inbox</h1>
          <p role="status" className="mt-2 text-sm leading-relaxed text-text-mid">
            {sentMessage}
          </p>
          <p className="mt-3 text-xs text-text-low">
            Didn&apos;t get it? Check spam, or try again in a minute.
          </p>
          <Link to="/login" className="mt-6 inline-block">
            <Button variant="outline">Back to login</Button>
          </Link>
        </div>
      </AuthShell>
    )
  }

  return (
    <AuthShell>
      <h1 className="text-lg font-semibold">Reset your password</h1>
      <p className="mt-1 text-sm text-text-mid">
        Enter your account email and we&apos;ll send you a link to choose a new password.
      </p>
      <form className="mt-6 space-y-4" onSubmit={(e) => void onSubmit(e)} noValidate>
        <div className="space-y-1.5">
          <label htmlFor="email" className={fieldLabel}>
            Email
          </label>
          <Input
            id="email"
            type="email"
            autoComplete="email"
            placeholder="you@example.com"
            icon={<Mail aria-hidden="true" />}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            aria-invalid={!!error}
          />
          {error && (
            <p role="alert" className="text-xs text-loss-soft">
              {error}
            </p>
          )}
        </div>
        <Button type="submit" className="w-full" loading={sending}>
          Send reset link
        </Button>
      </form>
      <Link
        to="/login"
        className="mt-5 inline-flex items-center gap-1.5 text-xs text-text-mid transition-colors hover:text-text-hi"
      >
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" /> Back to login
      </Link>
    </AuthShell>
  )
}
