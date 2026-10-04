import { lazy, Suspense, useEffect } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { TooltipProvider } from '@/components/ui/tooltip'
import { Toaster } from '@/components/ui/toaster'
import { ProtectedRoute } from '@/components/ProtectedRoute'
import { AppShell } from '@/components/layout/AppShell'
import { Seo } from '@/components/Seo'
import { SkipToContent } from '@/components/SkipToContent'
import { ShortcutHelpDialog } from '@/components/ShortcutHelpDialog'
import { useKeyboardShortcuts } from '@/hooks/useKeyboardShortcuts'

// Route-level code splitting: every page loads on demand (vendor chunks stay
// shared via manualChunks in vite.config.ts). Keeps the initial paint bundle
// to the shell + landing instead of all 17 pages.
const Landing = lazy(() => import('@/pages/Landing').then((m) => ({ default: m.Landing })))
const HowItWorks = lazy(() => import('@/pages/HowItWorks').then((m) => ({ default: m.HowItWorks })))
const Features = lazy(() => import('@/pages/Features').then((m) => ({ default: m.Features })))
const About = lazy(() => import('@/pages/About').then((m) => ({ default: m.About })))
const Pricing = lazy(() => import('@/pages/Pricing').then((m) => ({ default: m.Pricing })))
const Contact = lazy(() => import('@/pages/Contact').then((m) => ({ default: m.Contact })))
const Terms = lazy(() => import('@/pages/Terms').then((m) => ({ default: m.Terms })))
const Privacy = lazy(() => import('@/pages/Privacy').then((m) => ({ default: m.Privacy })))
const Risk = lazy(() => import('@/pages/Risk').then((m) => ({ default: m.Risk })))
const Auth = lazy(() => import('@/pages/Auth').then((m) => ({ default: m.Auth })))
const Try = lazy(() => import('@/pages/Try').then((m) => ({ default: m.Try })))
const Dashboard = lazy(() => import('@/pages/Dashboard').then((m) => ({ default: m.Dashboard })))
const Screener = lazy(() => import('@/pages/Screener').then((m) => ({ default: m.Screener })))
const Backtest = lazy(() => import('@/pages/Backtest').then((m) => ({ default: m.Backtest })))
const StockDetail = lazy(() =>
  import('@/pages/StockDetail').then((m) => ({ default: m.StockDetail })),
)
const News = lazy(() => import('@/pages/News').then((m) => ({ default: m.News })))
const Settings = lazy(() => import('@/pages/Settings').then((m) => ({ default: m.Settings })))
const VerifyEmail = lazy(() =>
  import('@/pages/VerifyEmail').then((m) => ({ default: m.VerifyEmail })),
)
const GoogleComplete = lazy(() =>
  import('@/pages/GoogleComplete').then((m) => ({ default: m.GoogleComplete })),
)
const ForgotPassword = lazy(() =>
  import('@/pages/ForgotPassword').then((m) => ({ default: m.ForgotPassword })),
)
const ResetPassword = lazy(() =>
  import('@/pages/ResetPassword').then((m) => ({ default: m.ResetPassword })),
)
const NotFound = lazy(() => import('@/pages/NotFound').then((m) => ({ default: m.NotFound })))

/** Global keyboard shortcuts (Cmd/Ctrl+K search, Cmd/Ctrl+/ help, Esc, Cmd+Enter). */
function KeyboardShortcuts() {
  const { helpOpen, setHelpOpen } = useKeyboardShortcuts()
  return <ShortcutHelpDialog open={helpOpen} onOpenChange={setHelpOpen} />
}

function ScrollToTop() {
  const { pathname } = useLocation()
  useEffect(() => {
    window.scrollTo({ top: 0 })
  }, [pathname])
  return null
}

/** Minimal branded fallback while a lazy page chunk loads. */
function PageLoader() {
  return (
    <div
      className="flex min-h-screen items-center justify-center bg-[#0a0a0f]"
      role="status"
      aria-label="Loading page"
    >
      <div className="flex flex-col items-center gap-3">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-neon/20 border-t-neon" />
        <span className="text-2xs font-semibold uppercase tracking-widest text-text-low">
          Stratoscan
        </span>
      </div>
    </div>
  )
}

export default function App() {
  return (
    <TooltipProvider delayDuration={200}>
      <BrowserRouter>
        {/* Keyboard a11y: first focusable element — lets Tab users skip the nav. */}
        <SkipToContent />
        <Seo />
        <ScrollToTop />
        <KeyboardShortcuts />
        <Suspense fallback={<PageLoader />}>
          <Routes>
            {/* Public */}
            <Route path="/" element={<Landing />} />
            <Route path="/how-it-works" element={<HowItWorks />} />
            <Route path="/features" element={<Features />} />
            <Route path="/about" element={<About />} />
            <Route path="/pricing" element={<Pricing />} />
            <Route path="/contact" element={<Contact />} />
            <Route path="/terms" element={<Terms />} />
            <Route path="/privacy" element={<Privacy />} />
            <Route path="/risk" element={<Risk />} />
            {/* Day 6: the backtester with no signup wall (3 free runs). */}
            <Route path="/try" element={<Try />} />
            <Route path="/login" element={<Auth mode="login" />} />
            <Route path="/signup" element={<Auth mode="signup" />} />
            <Route path="/verify-email" element={<VerifyEmail />} />
            {/* Landing hop for the Google redirect: the API sends the JWT in
                the URL fragment, this page stores it and moves on. Public —
                the visitor is not signed in until this page finishes. */}
            <Route path="/auth/google/complete" element={<GoogleComplete />} />
            <Route path="/forgot-password" element={<ForgotPassword />} />
            <Route path="/reset-password" element={<ResetPassword />} />

            {/* Protected app */}
            <Route
              path="/app"
              element={
                <ProtectedRoute>
                  <AppShell />
                </ProtectedRoute>
              }
            >
              <Route index element={<Dashboard />} />
              <Route path="screener" element={<Screener />} />
              <Route path="backtest" element={<Backtest />} />
              <Route path="stocks/:symbol" element={<StockDetail />} />
              <Route path="news" element={<News />} />
              <Route path="settings" element={<Settings />} />
            </Route>

            <Route path="/404" element={<NotFound />} />
            <Route path="*" element={<Navigate to="/404" replace />} />
          </Routes>
        </Suspense>
        <Toaster />
      </BrowserRouter>
    </TooltipProvider>
  )
}
