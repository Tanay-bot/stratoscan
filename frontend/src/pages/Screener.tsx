import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import {
  ArrowDownUp,
  ArrowRight,
  ChevronLeft,
  ChevronRight,
  Download,
  Filter,
  Search,
  SlidersHorizontal,
  Sparkles,
  X,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { StockRow } from '@/components/StockRow'
import { EmptyState } from '@/components/EmptyState'
import { downloadBlob, exportScreenCsv, screen } from '@/lib/api/services'
import { toApiError } from '@/lib/api/client'
import { toast } from '@/store/notifications'
import { useRateLimit, useRateLimitCountdown } from '@/store/rateLimit'
import type { ScreenResponse } from '@/lib/api/types'
import { cn } from '@/lib/utils'
import { useRegisterDismisser, useRegisterSearchInput } from '@/hooks/useKeyboardShortcuts'

const SORT_OPTIONS = [
  { key: 'market_cap', label: 'Market cap' },
  { key: 'score', label: 'Composite score' },
  { key: 'current_price', label: 'Price' },
  { key: 'price_change_pct', label: 'Daily change' },
  { key: 'pe_ratio', label: 'P/E ratio' },
  { key: 'dividend_yield', label: 'Dividend yield' },
  { key: 'rsi_14', label: 'RSI (14)' },
]

const PAGE_SIZE = 20

export function Screener() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const query = params.get('q') ?? ''
  const [draft, setDraft] = useState(query)
  const [data, setData] = useState<ScreenResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [sortBy, setSortBy] = useState('market_cap')
  const [sortDesc, setSortDesc] = useState(true)
  const [minScore, setMinScore] = useState(0)
  const [minCap, setMinCap] = useState(0)
  const [onlyUp, setOnlyUp] = useState(false)
  const [exporting, setExporting] = useState(false)
  const runId = useRef(0)
  const searchRef = useRegisterSearchInput(1)
  // Global Esc closes the advanced-filters drawer when it's open.
  useRegisterDismisser(filtersOpen ? () => setFiltersOpen(false) : null)
  // 429 countdown: while the backend says "slow down", every submit surface
  // disables itself and the pending retry auto-fires when the window ends.
  const { pending, waitSeconds } = useRateLimitCountdown()
  useEffect(() => () => useRateLimit.getState().clear(), [])

  const run = async (q: string, offset = 0) => {
    if (!q.trim()) return
    const id = ++runId.current
    setLoading(true)
    setError(null)
    try {
      const res = await screen({
        query: q,
        limit: PAGE_SIZE,
        offset,
        sort_by: sortBy,
        sort_desc: sortDesc,
      })
      if (id !== runId.current) return
      setData(res)
      if (res.applied_filters?.length) setFiltersOpen(false)
    } catch (err) {
      if (id !== runId.current) return
      const apiErr = toApiError(err)
      setError(apiErr.message)
      if (apiErr.status === 429) {
        // Hold the gate for the backend's Retry-After window, then re-issue
        // the exact same query — the countdown is live on the submit button.
        // Fall back to a fixed 10s when the wait info is missing (a proxy
        // stripped the header/body) so the UX never silently degrades.
        const wait = apiErr.retryAfter ?? 10
        useRateLimit.getState().hold(wait, () => void run(q, offset))
        toast.error('Too many requests', `Please wait ${Math.ceil(wait)} seconds.`)
      }
    } finally {
      if (id === runId.current) setLoading(false)
    }
  }

  // Load / reload whenever the URL query changes (including saved-screen
  // navigation while this page is already mounted)
  useEffect(() => {
    if (query) {
      setDraft(query)
      setData(null)
      void run(query)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query])

  const submit = (q: string) => {
    const trimmed = q.trim()
    if (!trimmed) return
    if (trimmed === query) {
      // Same query: the URL-param effect won't re-fire, so run directly.
      // The changed-query path is owned by the [query] effect — running
      // here TOO would double-fire every submit and burn the screen burst
      // budget twice as fast (each request counts against the rate limit).
      setData(null)
      void run(trimmed)
    } else {
      setParams({ q: trimmed }, { replace: false })
    }
  }

  const changePage = (offset: number) => {
    if (!query) return
    window.scrollTo({ top: 0, behavior: 'smooth' })
    void run(query, offset)
  }

  const filtered = useMemo(() => {
    if (!data) return []
    let rows = [...data.results]
    if (minScore > 0) rows = rows.filter((r) => (r.score ?? 0) >= minScore)
    if (minCap > 0) rows = rows.filter((r) => (r.market_cap ?? 0) >= minCap * 1e9)
    if (onlyUp) rows = rows.filter((r) => (r.price_change_pct ?? 0) >= 0)
    return rows
  }, [data, minScore, minCap, onlyUp])

  const appliedFilters = data?.applied_filters ?? []

  return (
    <div className="mx-auto max-w-6xl">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold">Screener</h1>
        <p className="mt-1 text-sm text-text-mid">
          Describe the stocks you want — the interpreter does the rest.
        </p>
      </div>

      {/* Search bar */}
      <form
        onSubmit={(e) => {
          e.preventDefault()
          submit(draft)
        }}
        className="glass-strong mb-5 flex items-center gap-2 rounded-2xl p-2"
        role="search"
      >
        <Search className="ml-3 h-5 w-5 shrink-0 text-neon" aria-hidden="true" />
        <input
          ref={searchRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          maxLength={4000}
          placeholder="e.g. undervalued small-cap tech stocks with rising momentum"
          aria-label="Screen query"
          className="h-11 min-w-0 flex-1 bg-transparent text-sm text-text-hi placeholder:text-text-low focus:outline-none"
          spellCheck={false}
        />
        {draft.length > 3200 && (
          <span
            className={cn(
              'shrink-0 font-mono text-2xs tabular',
              draft.length >= 4000 ? 'text-loss-soft' : 'text-amber-300',
            )}
          >
            {draft.length}/4k
          </span>
        )}
        <Button type="submit" loading={loading && !data} disabled={pending}>
          {pending ? (
            `Please wait ${waitSeconds}s`
          ) : (
            <>
              {!loading && <ArrowRight className="h-4 w-4" aria-hidden="true" />}
              Screen
            </>
          )}
        </Button>
      </form>

      {/* Query chips */}
      <div className="mb-6 flex flex-wrap gap-2">
        {[
          'undervalued small-cap tech stocks',
          'high-dividend energy companies',
          'momentum breakout stocks',
        ].map((ex) => (
          <button
            key={ex}
            onClick={() => {
              setDraft(ex)
              submit(ex)
            }}
            className="glass-hover rounded-full px-3 py-1.5 text-xs text-text-mid"
          >
            {ex}
          </button>
        ))}
      </div>

      {!query && !data && !loading && (
        <EmptyState
          icon={Sparkles}
          title="Screen the universe in plain English"
          description="Try an example above, or type your own idea. Results rank by a composite score of value, momentum and quality."
        />
      )}

      {query && (
        <>
          {/* Results toolbar */}
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm text-text-mid">
                <span className="text-text-low">Query: </span>
                <span className="font-medium text-text-hi">“{data?.query ?? query}”</span>
              </p>
              {data && (
                <p className="mt-0.5 text-2xs text-text-low">
                  {data.total_matches} matches · page{' '}
                  {Math.floor((data.offset ?? 0) / PAGE_SIZE) + 1} · ranked by{' '}
                  {SORT_OPTIONS.find((s) => s.key === data.sort_by)?.label ?? data.sort_by}
                </p>
              )}
            </div>

            <div className="flex items-center gap-2">
              <div className="relative">
                <select
                  value={sortBy}
                  onChange={(e) => {
                    setSortBy(e.target.value)
                    if (data) void run(query, data.offset ?? 0)
                  }}
                  disabled={pending}
                  aria-label="Sort results by"
                  className="h-10 appearance-none rounded-xl border border-white/10 bg-white/[0.04] pl-3.5 pr-9 text-sm text-text-hi transition-colors hover:border-white/20 focus:border-neon/60 focus:outline-none focus:ring-2 focus:ring-neon/25 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {SORT_OPTIONS.map((o) => (
                    <option key={o.key} value={o.key} className="bg-ink-panel">
                      {o.label}
                    </option>
                  ))}
                </select>
                <ArrowDownUp
                  className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-low"
                  aria-hidden="true"
                />
              </div>
              <button
                onClick={() => setSortDesc((d) => !d)}
                disabled={pending}
                aria-label={sortDesc ? 'Sort ascending' : 'Sort descending'}
                aria-pressed={sortDesc}
                className="flex h-10 items-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.04] px-3 text-xs font-medium text-text-mid transition-colors hover:border-white/20 hover:text-text-hi disabled:cursor-not-allowed disabled:opacity-40"
              >
                {sortDesc ? 'Desc' : 'Asc'}
              </button>
              <Button variant="secondary" onClick={() => setFiltersOpen(true)} className="h-10">
                <SlidersHorizontal className="h-4 w-4" aria-hidden="true" /> Filters
              </Button>
              <Button
                variant="outline"
                className="h-10"
                loading={exporting}
                disabled={!data || loading || pending}
                onClick={async () => {
                  setExporting(true)
                  try {
                    const blob = await exportScreenCsv({
                      query: data?.query ?? query,
                      limit: data?.total_matches ?? PAGE_SIZE,
                      sort_by: data?.sort_by,
                      sort_desc: data?.sort_desc,
                    })
                    downloadBlob(blob, `stratoscan_screen_${Date.now()}.csv`)
                  } catch {
                    toast.error('Export failed', 'Could not generate the CSV. Please try again.')
                  } finally {
                    setExporting(false)
                  }
                }}
              >
                {!exporting && <Download className="h-4 w-4" aria-hidden="true" />} Export
              </Button>
            </div>
          </div>

          {/* Applied filters */}
          {appliedFilters.length > 0 && (
            <div className="mb-4 flex flex-wrap items-center gap-2">
              <span className="text-2xs font-semibold uppercase tracking-wider text-text-low">
                Applied
              </span>
              {appliedFilters.map((f, i) => (
                <Badge key={`${i}-${f}`} variant="neon" dot>
                  {f}
                </Badge>
              ))}
            </div>
          )}

          {/* Results */}
          {loading && !data ? (
            <div className="space-y-2.5" aria-busy="true" aria-label="Loading results">
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="glass flex items-center gap-4 rounded-2xl p-4">
                  <Skeleton className="h-10 w-10 rounded-xl" />
                  <div className="flex-1 space-y-2">
                    <Skeleton className="h-3.5 w-24" />
                    <Skeleton className="h-3 w-40" />
                  </div>
                  <Skeleton className="h-4 w-16" />
                </div>
              ))}
            </div>
          ) : error ? (
            <EmptyState
              icon={Filter}
              title="Couldn't screen that query"
              description={error}
              action={
                <Button onClick={() => submit(query)} disabled={pending}>
                  {pending ? `Retry in ${waitSeconds}s` : 'Retry'}{' '}
                  <ArrowRight className="h-4 w-4" aria-hidden="true" />
                </Button>
              }
            />
          ) : filtered.length === 0 ? (
            <EmptyState
              icon={Search}
              title="No stocks match"
              description="Loosen the filters in the drawer or try a different phrasing."
              action={<Button onClick={() => navigate('/app/backtest')}>Backtest anyway</Button>}
            />
          ) : (
            <>
              <div className="mb-4 flex justify-end">
                <Button
                  onClick={() =>
                    navigate(`/app/backtest?q=${encodeURIComponent(data?.query ?? query)}`)
                  }
                >
                  Backtest strategy <ArrowRight className="h-4 w-4" aria-hidden="true" />
                </Button>
              </div>
              <div className="space-y-2.5">
                {filtered.map((s, i) => (
                  <motion.div
                    key={s.symbol}
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.3, delay: Math.min(i * 0.04, 0.5) }}
                  >
                    <StockRow stock={s} index={i + (data?.offset ?? 0)} />
                  </motion.div>
                ))}
              </div>

              {/* Pagination */}
              {(data?.has_more || (data?.offset ?? 0) > 0) && (
                <div className="mt-6 flex items-center justify-center gap-4">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={(data?.offset ?? 0) === 0 || pending}
                    onClick={() => changePage(Math.max(0, (data?.offset ?? 0) - PAGE_SIZE))}
                  >
                    <ChevronLeft className="h-4 w-4" aria-hidden="true" /> Previous
                  </Button>
                  <span className="text-xs text-text-low">
                    Page {Math.floor((data?.offset ?? 0) / PAGE_SIZE) + 1}
                    {data?.has_more ? ` · ${data.total_matches} total` : ' · end'}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!data?.has_more || pending}
                    onClick={() => changePage((data?.offset ?? 0) + PAGE_SIZE)}
                  >
                    Next <ChevronRight className="h-4 w-4" aria-hidden="true" />
                  </Button>
                </div>
              )}
            </>
          )}
        </>
      )}

      {/* Filters drawer */}
      <AnimatePresence>
        {filtersOpen && (
          <>
            <motion.div
              className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setFiltersOpen(false)}
              aria-hidden="true"
            />
            <motion.aside
              role="dialog"
              aria-modal="true"
              aria-label="Advanced filters"
              className="glass-strong fixed inset-y-0 right-0 z-50 flex w-full max-w-sm flex-col overflow-hidden overscroll-contain"
              initial={{ x: 360 }}
              animate={{ x: 0 }}
              exit={{ x: 360 }}
              transition={{ type: 'spring', damping: 32, stiffness: 320 }}
            >
              <div className="flex items-center justify-between border-b border-white/[0.08] px-6 py-5">
                <h2 className="text-base font-semibold">Advanced filters</h2>
                <button
                  onClick={() => setFiltersOpen(false)}
                  aria-label="Close filters"
                  className="rounded-lg p-2 text-text-low transition-colors hover:bg-white/10 hover:text-text-hi"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="flex-1 space-y-7 overflow-y-auto px-6 py-6">
                <div>
                  <div className="mb-3 flex items-center justify-between">
                    <label htmlFor="score" className="text-sm font-medium text-text-hi">
                      Minimum composite score
                    </label>
                    <span className="font-mono text-xs tabular text-neon">{minScore}+</span>
                  </div>
                  <input
                    id="score"
                    type="range"
                    min={0}
                    max={100}
                    value={minScore}
                    onChange={(e) => setMinScore(Number(e.target.value))}
                  />
                  <div className="mt-1 flex justify-between text-2xs text-text-low">
                    <span>0</span>
                    <span>100</span>
                  </div>
                </div>

                <div>
                  <div className="mb-3 flex items-center justify-between">
                    <label htmlFor="cap" className="text-sm font-medium text-text-hi">
                      Min market cap
                    </label>
                    <span className="font-mono text-xs tabular text-neon">
                      {minCap === 0 ? 'Any' : `$${minCap}B+`}
                    </span>
                  </div>
                  <input
                    id="cap"
                    type="range"
                    min={0}
                    max={200}
                    step={10}
                    value={minCap}
                    onChange={(e) => setMinCap(Number(e.target.value))}
                  />
                  <div className="mt-1 flex justify-between text-2xs text-text-low">
                    <span>Any</span>
                    <span>$200B+</span>
                  </div>
                </div>

                <button
                  onClick={() => setOnlyUp((v) => !v)}
                  aria-pressed={onlyUp}
                  className={cn(
                    'flex w-full items-center justify-between rounded-xl border px-4 py-3 text-sm transition-colors',
                    onlyUp
                      ? 'border-gain/40 bg-gain/10 text-gain-soft'
                      : 'border-white/10 bg-white/[0.03] text-text-mid hover:border-white/20',
                  )}
                >
                  <span>Gainers only</span>
                  <span
                    className={cn(
                      'h-2 w-2 rounded-full',
                      onlyUp ? 'bg-gain shadow-[0_0_8px_rgba(22,199,132,0.8)]' : 'bg-white/20',
                    )}
                    aria-hidden="true"
                  />
                </button>
              </div>

              <div className="border-t border-white/[0.08] p-5">
                <Button className="w-full" onClick={() => setFiltersOpen(false)}>
                  Apply filters <ArrowRight className="h-4 w-4" aria-hidden="true" />
                </Button>
              </div>
            </motion.aside>
          </>
        )}
      </AnimatePresence>
    </div>
  )
}
