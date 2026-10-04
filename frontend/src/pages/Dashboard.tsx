import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import {
  ArrowRight,
  BarChart3,
  Bookmark,
  Clock,
  LineChart,
  Link2,
  Plus,
  Search,
  Star,
  TrendingUp,
  Trash2,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState } from '@/components/EmptyState'
import { SharedLinks } from '@/components/dashboard/SharedLinks'
import { TEMPLATES } from '@/lib/templates'
import { Sparkline } from '@/components/charts/Sparkline'
import { usePersonal } from '@/store/personal'
import { listStocks, stockDetail } from '@/lib/api/services'
import { DEMO_MODE } from '@/lib/api/client'
import { priceHistory } from '@/lib/api/demo'
import { toast } from '@/store/notifications'
import type { StockResult } from '@/lib/api/types'
import { fmtCurrency, fmtPct, timeAgo } from '@/lib/utils'
import { cn } from '@/lib/utils'

function Widget({
  title,
  icon: Icon,
  action,
  children,
}: {
  title: string
  icon?: typeof Search
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="glass rounded-2xl p-5" aria-label={title}>
      <header className="mb-4 flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-text-hi">
          {Icon && <Icon className="h-4 w-4 text-neon" aria-hidden="true" />}
          {title}
        </h2>
        {action}
      </header>
      {children}
    </section>
  )
}

export function Dashboard() {
  const navigate = useNavigate()
  const {
    watchlist,
    savedScreeners,
    recentBacktests,
    toggleWatch,
    removeScreener,
    clearBacktests,
  } = usePersonal()
  const [overview, setOverview] = useState<StockResult[] | null>(null)
  // Live quotes for the watchlist: symbol → real price/change from /stocks.
  const [quotes, setQuotes] = useState<Record<string, StockResult>>({})

  useEffect(() => {
    let alive = true
    void listStocks(200).then((res) => {
      if (!alive) return
      const sorted = [...res.results]
        .sort((a, b) => Math.abs(b.price_change_pct ?? 0) - Math.abs(a.price_change_pct ?? 0))
        .slice(0, 6)
      setOverview(sorted)
      const bySym: Record<string, StockResult> = {}
      for (const s of res.results) bySym[s.symbol] = s
      setQuotes(bySym)
    })
    return () => {
      alive = false
    }
  }, [])

  // The /stocks list is capped at 200 rows; a watched symbol outside that page
  // (e.g. an obscure small cap) gets its quote fetched individually instead.
  useEffect(() => {
    if (DEMO_MODE) return
    const missing = watchlist.filter((sym) => !quotes[sym])
    if (missing.length === 0) return
    let alive = true
    void Promise.allSettled(missing.map((sym) => stockDetail(sym))).then((results) => {
      if (!alive) return
      setQuotes((prev) => {
        const next = { ...prev }
        results.forEach((r) => {
          if (r.status === 'fulfilled' && r.value) next[r.value.symbol] = r.value
        })
        return next
      })
    })
    return () => {
      alive = false
    }
    // DEMO_MODE is a module constant — mutating it never re-renders, so it's
    // not a valid effect dependency (react-hooks/exhaustive-deps).
  }, [watchlist, quotes])

  // Watchlist rows: in demo mode a synthetic curve + price; in live mode the
  // real current price / change from /stocks (no invented curve, no fake data).
  const watchData = useMemo(
    () =>
      watchlist.map((sym) => {
        const hist = DEMO_MODE ? priceHistory(sym, 40).map((p) => p.value) : []
        const q = quotes[sym]
        const last = DEMO_MODE ? (hist[hist.length - 1] ?? null) : (q?.current_price ?? null)
        const first = hist[0] ?? null
        const chg = DEMO_MODE
          ? first
            ? ((last! - first) / first) * 100
            : 0
          : (q?.price_change_pct ?? 0)
        return { sym, hist, last, chg, quote: q }
      }),
    [watchlist, quotes],
  )

  const summary = (res: { results: Record<string, { total_return_pct?: number | null }> }) => {
    const vals = Object.values(res.results).map((r) => r.total_return_pct ?? 0)
    return vals.reduce((a, b) => a + b, 0) / Math.max(1, vals.length)
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      {/* Welcome row */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Command center</h1>
          <p className="mt-1 text-sm text-text-mid">
            Your ideas, screens and strategies — one place.
          </p>
        </div>
        <Button onClick={() => navigate('/app/screener')}>
          <Search className="h-4 w-4" aria-hidden="true" /> New screen
        </Button>
      </div>

      {/* Market overview */}
      <motion.section
        initial={{ opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.45 }}
        className="glass rounded-2xl p-5"
        aria-label="Market overview"
      >
        <header className="mb-4 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-text-hi">
            <TrendingUp className="h-4 w-4 text-neon" aria-hidden="true" /> Market movers
          </h2>
          <Link
            to="/app/news"
            className="text-xs font-medium text-neon transition-colors hover:underline"
          >
            News →
          </Link>
        </header>
        {!overview ? (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-[104px] rounded-2xl" />
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            {overview.map((s) => {
              const up = (s.price_change_pct ?? 0) >= 0
              return (
                <Link
                  key={s.symbol}
                  to={`/app/stocks/${s.symbol}`}
                  className="glass-hover rounded-2xl p-3.5"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-sm font-bold text-text-hi">{s.symbol}</span>
                    <span
                      className={cn(
                        'font-mono text-xs font-semibold tabular',
                        up ? 'text-gain-soft' : 'text-loss-soft',
                      )}
                    >
                      {fmtPct(s.price_change_pct)}
                    </span>
                  </div>
                  <p className="mt-0.5 truncate text-2xs text-text-low">{s.company_name}</p>
                  <p className="mt-2 font-mono text-sm tabular text-text-mid">
                    {fmtCurrency(s.current_price)}
                  </p>
                </Link>
              )
            })}
          </div>
        )}
      </motion.section>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Saved screeners */}
        <motion.div
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.45, delay: 0.08 }}
        >
          <Widget title="Saved screeners" icon={Bookmark}>
            {savedScreeners.length === 0 ? (
              <EmptyState
                icon={Bookmark}
                title="No saved screens yet"
                description="Run a screen in plain English, then press Save to keep it here."
                action={
                  <Button variant="outline" size="sm" onClick={() => navigate('/app/screener')}>
                    Open the screener
                  </Button>
                }
              />
            ) : (
              <ul className="space-y-2">
                {savedScreeners.map((s) => (
                  <li key={s.id} className="glass-hover flex items-center gap-3 rounded-xl p-3">
                    <button
                      onClick={() => navigate(`/app/screener?q=${encodeURIComponent(s.query)}`)}
                      className="flex min-w-0 flex-1 items-center gap-2.5 text-left"
                    >
                      <span className="text-xs text-text-low">❝</span>
                      <span className="truncate text-sm text-text-hi hover:text-neon">
                        {s.query}
                      </span>
                    </button>
                    <span className="hidden text-2xs text-text-low sm:block">
                      {timeAgo(s.savedAt)}
                    </span>
                    <button
                      onClick={() => removeScreener(s.id)}
                      aria-label={`Delete saved screener ${s.query}`}
                      className="rounded-lg p-1.5 text-text-low transition-colors hover:bg-white/10 hover:text-loss-soft"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Widget>
        </motion.div>

        {/* Watchlist */}
        <motion.div
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.45, delay: 0.14 }}
        >
          <Widget
            title="Watchlist"
            icon={Star}
            action={
              <Button variant="ghost" size="sm" onClick={() => navigate('/app/screener')}>
                <Plus className="h-3.5 w-3.5" aria-hidden="true" /> Add
              </Button>
            }
          >
            {watchData.length === 0 ? (
              <EmptyState
                icon={Star}
                title="Watchlist is empty"
                description="Add stocks you're tracking from any screener result — they show live prices here."
                action={
                  <Button variant="outline" size="sm" onClick={() => navigate('/app/screener')}>
                    Find stocks to watch
                  </Button>
                }
              />
            ) : (
              <ul className="space-y-1.5">
                {watchData.map((w) => (
                  <li key={w.sym} className="glass-hover flex items-center gap-3 rounded-xl p-2.5">
                    <Link
                      to={`/app/stocks/${w.sym}`}
                      className="flex min-w-0 flex-1 items-center gap-3"
                    >
                      <span className="font-mono text-sm font-bold text-text-hi">{w.sym}</span>
                      <span
                        className={cn(
                          'font-mono text-xs font-semibold tabular',
                          w.chg >= 0 ? 'text-gain-soft' : 'text-loss-soft',
                        )}
                      >
                        {fmtPct(w.chg)}
                      </span>
                      <span className="ml-auto font-mono text-xs tabular text-text-mid">
                        {w.last != null ? fmtCurrency(w.last) : '—'}
                      </span>
                    </Link>
                    {DEMO_MODE && <Sparkline data={w.hist} positive={w.chg >= 0} />}
                    {!DEMO_MODE && w.quote && (
                      <span className="hidden text-2xs text-text-low sm:block">
                        {w.quote.sector ?? ''}
                      </span>
                    )}
                    <button
                      onClick={() => {
                        toggleWatch(w.sym)
                        toast.info('Removed from watchlist', w.sym)
                      }}
                      aria-label={`Remove ${w.sym} from watchlist`}
                      className="rounded-lg p-1.5 text-text-low transition-colors hover:bg-white/10 hover:text-loss-soft"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Widget>
        </motion.div>
      </div>

      {/* Recent backtests */}
      <motion.div
        initial={{ opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.45, delay: 0.2 }}
      >
        <Widget
          title="Recent backtests"
          icon={BarChart3}
          action={
            recentBacktests.length > 0 ? (
              <Button variant="ghost" size="sm" onClick={clearBacktests}>
                Clear
              </Button>
            ) : undefined
          }
        >
          {recentBacktests.length === 0 ? (
            <EmptyState
              icon={LineChart}
              title="No backtests yet"
              description={`Not sure what to test? Start from one of ${TEMPLATES.length} ready-made strategies — one click runs ten years of history against SPY.`}
              action={
                <div className="flex flex-wrap justify-center gap-2">
                  <Button onClick={() => navigate('/strategies')}>Browse strategy templates</Button>
                  <Button variant="outline" onClick={() => navigate('/app/backtest')}>
                    Write my own
                  </Button>
                </div>
              }
            />
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {recentBacktests.map((b) => {
                const first = Object.values(b.results)[0]
                const avg = summary(b)
                const up = avg >= 0
                const hist = first?.equity_curve?.map((p) => p.value) ?? []
                return (
                  <button
                    key={b.query + b.savedAt}
                    onClick={() => navigate(`/app/backtest?q=${encodeURIComponent(b.query)}`)}
                    className="glass-hover rounded-2xl p-4 text-left"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <p className="line-clamp-2 min-w-0 text-xs font-medium text-text-hi">
                        “{b.query}”
                      </p>
                      <Badge variant={up ? 'gain' : 'loss'} className="shrink-0">
                        {fmtPct(avg, 1)}
                      </Badge>
                    </div>
                    <div className="mt-3">
                      <Sparkline
                        data={hist.length ? hist : [100, 110]}
                        positive={up}
                        width={200}
                        height={44}
                      />
                    </div>
                    <p className="mt-2 flex items-center gap-1.5 text-2xs text-text-low">
                      <Clock className="h-3 w-3" aria-hidden="true" /> {timeAgo(b.savedAt)} ·{' '}
                      {b.total_symbols_tested} symbols
                    </p>
                  </button>
                )
              })}
            </div>
          )}
        </Widget>
      </motion.div>

      {/* Day 6/8: what this account has made public, and the off switch. */}
      {!DEMO_MODE && (
        <motion.div
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.45, delay: 0.23 }}
        >
          <Widget title="Your share links" icon={Link2}>
            <SharedLinks />
          </Widget>
        </motion.div>
      )}

      {/* Quick CTA */}
      <motion.div
        initial={{ opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.45, delay: 0.26 }}
        className="glass-strong flex flex-wrap items-center justify-between gap-4 rounded-2xl p-6"
      >
        <div>
          <h2 className="text-base font-semibold">Turn an idea into a validated strategy</h2>
          <p className="mt-1 text-sm text-text-mid">
            Screen the universe, then backtest the top candidates — free.
          </p>
        </div>
        <Button onClick={() => navigate('/app/backtest')}>
          Open backtester <ArrowRight className="h-4 w-4" aria-hidden="true" />
        </Button>
      </motion.div>
    </div>
  )
}
