import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowRight, Menu, X } from 'lucide-react'
import { Logo } from '@/components/Logo'
import { Button } from '@/components/ui/button'
import { useAuth } from '@/store/auth'
import { cn } from '@/lib/utils'

const LINKS = [
  { label: 'How it works', to: '/how-it-works' },
  { label: 'Features', to: '/features' },
  { label: 'About', to: '/about' },
  { label: 'Pricing', to: '/pricing' },
  { label: 'Contact', to: '/contact' },
]

export function LandingNav() {
  const navigate = useNavigate()
  const { user } = useAuth()
  const [open, setOpen] = useState(false)

  const go = (to: string) => {
    setOpen(false)
    navigate(to)
  }

  return (
    <nav className="sticky top-0 z-40 border-b border-white/[0.06] bg-ink/70 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-5">
        <Link to="/" aria-label="Stratoscan home" onClick={() => setOpen(false)}>
          <Logo />
        </Link>

        <div className="hidden items-center gap-7 text-sm font-medium text-text-mid lg:flex">
          {LINKS.map((l) => (
            <button
              key={l.label}
              onClick={() => go(l.to)}
              className="transition-colors hover:text-text-hi"
            >
              {l.label}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-3">
          {user ? (
            <Button onClick={() => navigate('/app')} size="sm">
              Open dashboard <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
            </Button>
          ) : (
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => navigate('/login')}
                className="hidden sm:inline-flex"
              >
                Log in
              </Button>
              <Button size="sm" onClick={() => navigate('/signup')}>
                Get started
              </Button>
            </>
          )}
          <button
            onClick={() => setOpen((v) => !v)}
            aria-label="Toggle navigation menu"
            aria-expanded={open}
            className="rounded-lg p-2 text-text-mid transition-colors hover:bg-white/10 hover:text-text-hi lg:hidden"
          >
            {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
        </div>
      </div>

      {/* Mobile menu */}
      <div
        className={cn(
          'overflow-hidden border-white/[0.06] transition-all duration-300 lg:hidden',
          open ? 'max-h-96 border-t' : 'max-h-0',
        )}
      >
        <div className="space-y-1 px-5 py-4">
          {LINKS.map((l) => (
            <button
              key={l.label}
              onClick={() => go(l.to)}
              className="block w-full rounded-lg px-3 py-2.5 text-left text-sm font-medium text-text-mid transition-colors hover:bg-white/[0.06] hover:text-text-hi"
            >
              {l.label}
            </button>
          ))}
        </div>
      </div>
    </nav>
  )
}
