import { useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import {
  Bell,
  ChevronDown,
  Gauge,
  LayoutDashboard,
  LineChart,
  LogOut,
  Menu,
  Newspaper,
  Search,
  Settings,
  ShieldCheck,
  Star,
  X,
} from 'lucide-react'
import { ADMIN_DASHBOARD_URL } from '@/lib/api/client'
import { AnimatePresence, motion } from 'framer-motion'
import { Logo } from '@/components/Logo'
import { useAuth } from '@/store/auth'
import { toast } from '@/store/notifications'
import { cn } from '@/lib/utils'
import { useRegisterDismisser, useRegisterSearchInput } from '@/hooks/useKeyboardShortcuts'

const NAV = [
  { to: '/app', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/app/screener', label: 'Screener', icon: Search },
  { to: '/app/backtest', label: 'Backtester', icon: LineChart },
  { to: '/app/news', label: 'News', icon: Newspaper },
  { to: '/app/settings', label: 'Settings', icon: Settings },
]

function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  return (
    <div className="flex h-full flex-col">
      <div className="px-5 pb-6 pt-6">
        <NavLink to="/" aria-label="Stratoscan home">
          <Logo />
        </NavLink>
      </div>          <nav className="flex-1 space-y-1 px-3" aria-label="Primary">
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            onClick={onNavigate}
            className={({ isActive }) =>
              cn(
                'group flex items-center gap-3 rounded-xl px-3.5 py-2.5 text-sm font-medium transition-all duration-200',
                isActive
                  ? 'bg-white/[0.08] text-neon shadow-[inset_0_0_0_1px_rgba(0,212,255,0.2)]'
                  : 'text-text-mid hover:bg-white/[0.05] hover:text-text-hi',
              )
            }
          >
            {({ isActive }) => (
              <>
                <Icon
                  className={cn(
                    'h-[18px] w-[18px]',
                    isActive ? 'text-neon' : 'text-text-low group-hover:text-text-mid',
                  )}
                  aria-hidden="true"
                />
                <span>{label}</span>
                {isActive && (
                  <span
                    className="ml-auto h-1.5 w-1.5 rounded-full bg-neon shadow-glow-sm"
                    aria-hidden="true"
                  />
                )}
              </>
            )}
          </NavLink>
        ))}
        {user?.is_admin && (
          <a
            href={ADMIN_DASHBOARD_URL}
            target="_blank"
            rel="noopener noreferrer"
            aria-label="Open admin dashboard (new tab)"
            className="group mt-1 flex items-center gap-3 rounded-xl border border-white/[0.06] bg-neon/[0.06] px-3.5 py-2.5 text-sm font-medium text-neon transition-all duration-200 hover:bg-neon/[0.12] hover:text-neon"
          >
            <ShieldCheck className="h-[18px] w-[18px] text-neon/80 group-hover:text-neon" aria-hidden="true" />
            <span>Admin</span>
            <span
              className="ml-auto text-2xs font-semibold uppercase tracking-wider text-neon/60 group-hover:text-neon"
              aria-hidden="true"
            >
              Dashboard
            </span>
          </a>
        )}
      </nav>

      <div className="p-3">
        <div className="glass mb-2 rounded-xl p-3.5">
          <p className="text-2xs font-semibold uppercase tracking-wider text-text-low">Plan</p>
          <div className="mt-1 flex items-center justify-between">
            <span className="text-sm font-semibold capitalize text-text-hi">
              {user?.plan ?? 'free'}
            </span>
            <span className="flex items-center gap-1 text-2xs font-medium text-neon">
              <Star className="h-3 w-3 fill-neon text-neon" aria-hidden="true" /> Free forever
            </span>
          </div>
        </div>
        <div className="flex items-center gap-3 rounded-xl px-3 py-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-full bg-gradient-to-br from-neon/30 to-grape/40 text-sm font-bold text-text-hi">
            {(user?.email ?? 'U').slice(0, 1).toUpperCase()}
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-text-hi">{user?.email}</p>
            <p className="text-2xs text-text-low">
              Member since {user ? new Date(user.created_at ?? Date.now()).getFullYear() : '2026'}
            </p>
          </div>
          <button
            onClick={() => {
              logout()
              navigate('/')
            }}
            aria-label="Sign out"
            className="rounded-lg p-2 text-text-low transition-colors hover:bg-white/10 hover:text-loss-soft focus-visible:outline-2 focus-visible:outline-neon"
          >
            <LogOut className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  )
}

export function AppShell() {
  const [mobileOpen, setMobileOpen] = useState(false)
  const navigate = useNavigate()
  const searchRef = useRegisterSearchInput(0)
  // Global Esc closes the mobile nav drawer when it's open.
  useRegisterDismisser(mobileOpen ? () => setMobileOpen(false) : null)

  return (
    <div className="flex min-h-screen">
      {/* Desktop sidebar */}
      <aside className="sticky top-0 hidden h-screen w-64 shrink-0 border-r border-white/[0.06] bg-ink-deep/70 backdrop-blur-xl lg:block">
        <SidebarContent />
      </aside>

      {/* Mobile drawer */}
      <AnimatePresence>
        {mobileOpen && (
          <>
            <motion.div
              className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm lg:hidden"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setMobileOpen(false)}
              aria-hidden="true"
            />
            <motion.aside
              className="fixed inset-y-0 left-0 z-50 w-72 border-r border-white/[0.06] bg-ink-deep lg:hidden"
              initial={{ x: -280 }}
              animate={{ x: 0 }}
              exit={{ x: -280 }}
              transition={{ type: 'spring', damping: 30, stiffness: 300 }}
            >
              <button
                onClick={() => setMobileOpen(false)}
                aria-label="Close navigation"
                className="absolute right-3 top-5 rounded-lg p-2 text-text-low hover:bg-white/10 hover:text-text-hi"
              >
                <X className="h-4 w-4" />
              </button>
              <SidebarContent onNavigate={() => setMobileOpen(false)} />
            </motion.aside>
          </>
        )}
      </AnimatePresence>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Topbar */}
        <header className="sticky top-0 z-30 flex h-16 items-center gap-4 border-b border-white/[0.06] bg-ink/80 px-4 backdrop-blur-xl sm:px-6">
          <button
            onClick={() => setMobileOpen(true)}
            aria-label="Open navigation"
            className="rounded-lg p-2 text-text-mid hover:bg-white/10 hover:text-text-hi lg:hidden"
          >
            <Menu className="h-5 w-5" />
          </button>

          <div className="relative hidden max-w-md flex-1 sm:block">
            <Search
              className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-low"
              aria-hidden="true"
            />
            <input
              ref={searchRef}
              type="search"
              placeholder="Search stocks…"
              onKeyDown={(e) => {
                if (e.key === 'Enter' && e.currentTarget.value.trim()) {
                  navigate(
                    `/app/screener?q=${encodeURIComponent('momentum ' + e.currentTarget.value.trim())}`,
                  )
                }
              }}
              className="h-10 w-full rounded-xl border border-white/10 bg-white/[0.04] pl-10 pr-4 text-sm text-text-hi placeholder:text-text-low transition-colors hover:border-white/20 focus:border-neon/60 focus:outline-none focus:ring-2 focus:ring-neon/25"
              spellCheck={false}
            />
          </div>

          <div className="ml-auto flex items-center gap-2">
            <span className="hidden items-center gap-1.5 rounded-full border border-gain/25 bg-gain/10 px-3 py-1 text-2xs font-semibold text-gain-soft sm:flex">
              <Gauge className="h-3 w-3" aria-hidden="true" /> Markets live
            </span>
            <button
              onClick={() => toast.info('No new notifications', 'You are all caught up.')}
              aria-label="Notifications"
              className="relative rounded-lg p-2.5 text-text-mid transition-colors hover:bg-white/10 hover:text-text-hi"
            >
              <Bell className="h-[18px] w-[18px]" aria-hidden="true" />
              <span
                className="absolute right-2 top-2 h-1.5 w-1.5 rounded-full bg-neon shadow-glow-sm"
                aria-hidden="true"
              />
            </button>
            <button
              onClick={() => navigate('/app/settings')}
              aria-label="Open account settings"
              className="flex items-center gap-2 rounded-lg p-1.5 text-text-mid transition-colors hover:bg-white/10 hover:text-text-hi"
            >
              <span className="flex h-8 w-8 items-center justify-center rounded-full bg-gradient-to-br from-neon/30 to-grape/40 text-xs font-bold text-text-hi">
                {(useAuth.getState().user?.email ?? 'U').slice(0, 1).toUpperCase()}
              </span>
              <ChevronDown className="hidden h-4 w-4 sm:block" aria-hidden="true" />
            </button>
          </div>
        </header>

        <main
          id="main-content"
          tabIndex={-1}
          className="min-w-0 flex-1 px-4 py-6 outline-none sm:px-6 lg:px-8"
        >
          <Outlet />
        </main>
      </div>
    </div>
  )
}
