import { useEffect } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '@/store/auth'
import { LogoMark } from './Logo'

export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, token, bootstrapped, bootstrap } = useAuth()
  const location = useLocation()

  useEffect(() => {
    if (!bootstrapped) void bootstrap()
  }, [bootstrapped, bootstrap])

  if (!bootstrapped) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <LogoMark className="animate-pulse" />
          <p className="text-xs text-text-low">Loading workspace…</p>
        </div>
      </div>
    )
  }

  if (!token || !user) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  }

  return <>{children}</>
}
