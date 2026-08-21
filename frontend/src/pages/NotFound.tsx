import { Link } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Logo } from '@/components/Logo'

export function NotFound() {
  return (
    <div
      id="main-content"
      tabIndex={-1}
      className="relative flex min-h-screen items-center justify-center px-5 outline-none"
    >
      <div
        className="pointer-events-none absolute -top-24 left-1/2 h-72 w-[480px] -translate-x-1/2 rounded-full bg-grape/[0.14] blur-[90px]"
        aria-hidden="true"
      />
      <div className="relative w-full max-w-md text-center">
        <div className="mb-8 flex justify-center">
          <Logo />
        </div>
        <div className="glass-strong rounded-3xl p-10 shadow-card">
          <p className="font-mono text-6xl font-bold tabular text-gradient">404</p>
          <h1 className="mt-4 text-lg font-semibold">This page isn't in the universe</h1>
          <p className="mt-2 text-sm leading-relaxed text-text-mid">
            The symbol, screen or route you're looking for doesn't exist — or has been delisted.
          </p>
          <Link to="/" className="mt-6 inline-block">
            <Button>
              <ArrowLeft className="h-4 w-4" aria-hidden="true" /> Back home
            </Button>
          </Link>
        </div>
      </div>
    </div>
  )
}
