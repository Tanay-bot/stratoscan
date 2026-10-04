import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { motion } from 'framer-motion'
import {
  AlertTriangle,
  BadgePercent,
  BarChart3,
  CalendarRange,
  CheckCircle2,
  DollarSign,
  Download,
  GitCompareArrows,
  Layers,
  LineChart,
  Link2,
  Link2Off,
  Loader2,
  PieChart,
  RotateCw,
  SearchX,
  ShieldAlert,
  Target,
  TrendingDown,
  TrendingUp,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { KpiCard } from '@/components/KpiCard'
import { EmptyState } from '@/components/EmptyState'
import { EquityCurve } from '@/components/charts/EquityCurve'
import { Robustness } from '@/components/backtest/Robustness'
import { ComparePanel } from '@/components/backtest/ComparePanel'
import {
  StrategyBuilder,
  strategyToSentence,
  type StrategyCondition,
} from '@/components/StrategyBuilder'
import { SplitPane } from '@/components/backtest/SplitPane'
import { ChatPanel, type ChatMessage } from '@/components/backtest/ChatPanel'
import { RiskDisclaimer } from '@/components/legal/RiskDisclaimer'
import { CodePanel, type CodeParams } from '@/components/backtest/CodePanel'
import { TrialBanner } from '@/components/backtest/TrialBanner'
import { TemplatePicker } from '@/components/templates/TemplatePicker'
import type { StrategyTemplate } from '@/lib/templates'
import {
  backtest,
  backtestAsync,
  backtestJobStatus,
  downloadBlob,
  exportBacktestCsv,
  revokeShare,
  shareResult,
} from '@/lib/api/services'
import { delay, toApiError, DEMO_MODE } from '@/lib/api/client'
import { track } from '@/lib/analytics'
import { toast } from '@/store/notifications'
import { usePersonal } from '@/store/personal'
import { useAuth } from '@/store/auth'
import { useRateLimit, useRateLimitCountdown } from '@/store/rateLimit'
import type {
  BacktestJobStatus,
  BacktestRequest,
  BacktestResponse,
  BacktestResultItem,
} from '@/lib/api/types'
import { fmtCurrency, fmtPct } from '@/lib/utils'
import { serializeParams, parseParams } from '@/lib/backtestParams'

export function Backtest() {
  const [params, setParams] = useSearchParams()
  const urlQuery = params.get('q') ?? ''
  const [result, setResult] = useState<BacktestResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activeSymbol, setActiveSymbol] = useState<string | null>(null)
  const runId = useRef(0)
  const lastQuery = useRef('')
  // 429 countdown: while the backend says "slow down", every submit surface
  // disables itself and the pending retry auto-fires when the window ends.
  const { pending, waitSeconds } = useRateLimitCountdown()
  useEffect(() => () => useRateLimit.getState().clear(), [])

  // Chat state
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const msgId = useRef(0)
  const [tuneOpen, setTuneOpen] = useState(false)

  // Strategy builder state
  const [conditions, setConditions] = useState<StrategyCondition[]>([])

  // Parameters
  // Shareable links: the tune drawer starts FROM the URL. (It used to be
  // hydrated by a mount effect, but the ?q= auto-run fires in the same commit
  // with the pre-hydration state — so a shared link's settings never applied
  // to the run it triggered.)
  const [initial] = useState(() => parseParams(params))
  const [startDate, setStartDate] = useState(initial.startDate)
  const [endDate, setEndDate] = useState(initial.endDate)
  // Day 9: Tune values are OVERRIDES. null = "use what the strategy text
  // says" (and the engine's default: no stop, no target). Only values the
  // user actually set are sent — previously an 8% stop and 20% target were
  // silently applied to every run, and the RSI/SMA fields were appended to
  // the query as text the parser never read.
  const [rsiBuy, setRsiBuy] = useState<number | null>(initial.rsiBuy)
  const [rsiSell, setRsiSell] = useState<number | null>(initial.rsiSell)
  const [stopLoss, setStopLoss] = useState<number | null>(initial.stopLoss)
  const [takeProfit, setTakeProfit] = useState<number | null>(initial.takeProfit)
  const [allowShort, setAllowShort] = useState(initial.allowShort)
  // Portfolio breadth: how many top-ranked matches the engine backtests.
  // The screener ranks the WHOLE universe against the strategy and the
  // engine backtests the top N (filling in next-ranked candidates when a
  // pick has no usable price history). More stocks = longer runs.
  const [topN, setTopN] = useState(initial.topN)
  // Tier 5 portfolio: shared pool + allocation + rebalance schedule
  const [portfolioAllocation, setPortfolioAllocation] = useState<'equal' | 'risk_based'>(
    initial.allocation,
  )
  const [rebalanceFrequency, setRebalanceFrequency] = useState<
    'none' | 'monthly' | 'quarterly' | 'yearly'
  >(initial.rebalance)
  // Tier 5 cash accounting + diversification
  const [dividendReinvest, setDividendReinvest] = useState(initial.reinvest)
  const [borrowRate, setBorrowRate] = useState(initial.borrow)
  const [maxSector, setMaxSector] = useState(initial.sector)
  // Tier 3: async job progress + compare mode + shareable links
  const [jobStatus, setJobStatus] = useState<BacktestJobStatus | null>(null)
  const [baseline, setBaseline] = useState<BacktestResponse | null>(null)
  const [exporting, setExporting] = useState(false)
  const [copied, setCopied] = useState(false)
  // Day 6: signed-out visitors run as a free trial; a finished run can be
  // published as a public link (server-built page) and un-published again.
  const signedIn = useAuth((s) => s.token !== null)
  const [lastJobId, setLastJobId] = useState<string | null>(null)
  const [share, setShare] = useState<{ share_id: string; url: string } | null>(null)
  const [sharing, setSharing] = useState(false)
  const [trialLeft, setTrialLeft] = useState<number | null>(null)
  const [trialExhausted, setTrialExhausted] = useState(false)

  const pushMsg = (role: ChatMessage['role'], text: string, meta?: string[]) => {
    setMessages((m) => [...m, { id: ++msgId.current, role, text, meta }])
  }

  /** Build the full BacktestRequest from the current tune-drawer state. */
  const buildRequest = (q: string): BacktestRequest => {
    // Fold strategy-builder conditions into the query so the demo + LLM both use them
    // Strategy-builder conditions are the user's own words, folded into the
    // query; numeric overrides travel as structured fields below.
    const builderClause = strategyToSentence(conditions)
    const fullQuery = conditions.length
      ? `${q} — strategy: ${builderClause.replace('When ', '')}`
      : q
    return {
      query: fullQuery,
      top_n: topN,
      stop_loss_pct: stopLoss,
      take_profit_pct: takeProfit,
      rsi_buy_below: rsiBuy,
      rsi_sell_above: rsiSell,
      allow_short: allowShort,
      start_date: startDate || null,
      end_date: endDate || null,
      portfolio_allocation: portfolioAllocation,
      rebalance_frequency: rebalanceFrequency,
      dividend_reinvest: dividendReinvest || null,
      borrow_rate_pct: borrowRate > 0 ? borrowRate : null,
      max_sector_exposure: maxSector > 0 ? maxSector : null,
      // Tier 3 compare mode: when a baseline exists, the challenger re-runs
      // on the SAME symbol set so the A/B is genuine.
      symbols: baseline ? Object.keys(baseline.results) : null,
    }
  }

  const run = async (q: string) => {
    const trimmed = q.trim()
    if (!trimmed) return
    // The same event the Screener fires, distinguished by `surface` so the
    // two entry points can be compared. Again: no query text.
    track('query_submitted', { surface: 'backtest' })
    if (trimmed !== lastQuery.current) {
      lastQuery.current = trimmed
      setParams({ q: trimmed }, { replace: false })
    }
    const id = ++runId.current
    setLoading(true)
    setError(null)
    setJobStatus(null)
    setLastJobId(null)
    setShare(null)
    pushMsg('user', trimmed)
    try {
      const req = buildRequest(trimmed)
      let res: BacktestResponse
      if (DEMO_MODE) {
        res = await backtest(req)
      } else {
        // Tier 3 async: start the job, then poll per-symbol progress so the
        // user sees live status instead of a silent 20-minute wait.
        const started = await backtestAsync(req)
        if (id !== runId.current) return
        if (typeof started.anon_remaining === 'number') setTrialLeft(started.anon_remaining)
        setJobStatus({
          job_id: started.job_id,
          status: started.status as BacktestJobStatus['status'],
          phase: started.phase,
          total_symbols: started.total_symbols,
          done_symbols: [],
        })
        // Poll until the job terminal state (demo mode never reaches here).
        for (;;) {
          await delay(1500)
          if (id !== runId.current) return
          const status = await backtestJobStatus(started.job_id)
          setJobStatus(status)
          if (status.status === 'done') {
            res = status.result as BacktestResponse
            setLastJobId(started.job_id)
            break
          }
          if (status.status === 'error') {
            throw new Error(status.error ?? 'Backtest failed')
          }
        }
      }
      if (id !== runId.current) return
      setResult(res)
      setJobStatus(null)
      setActiveSymbol(Object.keys(res.results)[0] ?? null)
      // Tier 3 shareable links: persist the run to the personal store, which
      // debounced-syncs to GET/PUT /me/personal — the run follows the user
      // across devices and the share link can be reconstructed anytime.
      usePersonal.getState().addRecentBacktest(res)
      const top = Object.values(res.results)[0]
      if (res.total_symbols_tested === 0) {
        // Honest zero-match: the strategy screened to 0 candidates (e.g.
        // contradictory filters like oversold + rising momentum). Say why
        // instead of the fake "Done — replayed 10 years" success message.
        pushMsg(
          'assistant',
          res.message ??
            'No stocks matched your strategy — 0 candidates passed the screen. Try a broader query or looser filters.',
          ['0 symbols tested · nothing was simulated'],
        )
      } else {
        pushMsg(
          'assistant',
          'Done. I translated that into entry signals, generated the scan code, and replayed 10 years of daily bars against SPY.',
          top
            ? [
                `strategy: ${top.symbol} · ${fmtPct(top.total_return_pct)} (SPY ${fmtPct(top.benchmark_total_return_pct)})`,
                `sharpe ${top.sharpe_ratio?.toFixed(2) ?? '—'} · max DD ${fmtPct(top.max_drawdown_pct)} · ${top.total_trades} trades`,
                `${res.total_symbols_tested} symbols tested · top_n=${topN}`,
              ]
            : [`${res.total_symbols_tested} symbols tested`],
        )
      }
    } catch (err) {
      if (id !== runId.current) return
      const apiErr = toApiError(err)
      setError(apiErr.message)
      if (apiErr.status === 403 && !signedIn) {
        // The trial allowance is spent: the server's own sentence says so and
        // the banner turns into the signup ask. Not an error in the UI sense.
        setTrialExhausted(true)
        setTrialLeft(0)
        pushMsg('assistant', apiErr.message)
        return
      }
      pushMsg('assistant', `Something went wrong: ${apiErr.message}`)
      if (apiErr.status === 429) {
        // Hold the shared gate for the backend's Retry-After window, then
        // re-issue the exact same strategy query once it elapses. Fall back
        // to a fixed 10s when the wait info is missing (proxy stripped it).
        const wait = apiErr.retryAfter ?? 10
        useRateLimit.getState().hold(wait, () => void run(trimmed))
        toast.error('Too many requests', `Please wait ${Math.ceil(wait)} seconds.`)
      }
    } finally {
      if (id === runId.current) setLoading(false)
    }
  }

  /** Day 8: one click from an empty box to a real result. */
  const runTemplate = (t: StrategyTemplate) => {
    // The template id only — never its text (the analytics whitelist).
    track('template_used', { template: t.id })
    void run(t.query)
  }

  useEffect(() => {
    if (urlQuery && urlQuery !== lastQuery.current) {
      void run(urlQuery)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlQuery])

  const active = result?.results[activeSymbol ?? '']

  const kpis = useMemo(() => {
    if (!active) return null
    return [
      {
        label: 'Total return',
        value: fmtPct(active.total_return_pct),
        tone: (active.total_return_pct ?? 0) >= 0 ? 'gain' : 'loss',
        icon: TrendingUp,
        sub: `Benchmark ${fmtPct(active.benchmark_total_return_pct)}`,
      },
      {
        label: 'Sharpe ratio',
        value: (active.sharpe_ratio ?? 0).toFixed(2),
        tone: (active.sharpe_ratio ?? 0) >= 1 ? 'neon' : 'neutral',
        icon: BarChart3,
        sub: `Alpha ${fmtPct(active.alpha_pct)}`,
      },
      {
        label: 'Max drawdown',
        value: fmtPct(active.max_drawdown_pct),
        tone: 'loss',
        icon: TrendingDown,
        sub: `Beta ${active.beta?.toFixed(2) ?? '—'}`,
      },
      {
        label: 'Win rate',
        value: fmtPct(active.win_rate_pct, 0, false),
        tone: (active.win_rate_pct ?? 0) >= 50 ? 'gain' : 'neutral',
        icon: Target,
        sub: `${active.total_trades} trades`,
      },
    ] as const
  }, [active])

  const codeParams: CodeParams = {
    capital: 100000,
    startDate,
    endDate,
    rsiBuy,
    rsiSell,
    stopLoss,
    takeProfit,
    allowShort,
    topN,
  }
  const codeStatus: 'idle' | 'running' | 'done' | 'error' = loading
    ? 'running'
    : error
      ? 'error'
      : result
        ? 'done'
        : 'idle'

  // ── Tune drawer content (parameters + strategy builder) ─────────────────────
  const tunePanel = (
    <div className="space-y-4">
      <div className="glass rounded-2xl p-4">
        <StrategyBuilder conditions={conditions} onChange={setConditions} />
      </div>

      <div className="glass rounded-2xl p-4">
        <p className="flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wider text-text-low">
          <Layers className="h-3.5 w-3.5 text-neon" aria-hidden="true" /> Stocks to test
        </p>
        <div className="mt-3 flex flex-wrap gap-1.5">
          {[3, 5, 10, 15, 20, 30, 50].map((n) => (
            <button
              key={n}
              type="button"
              onClick={() => setTopN(n)}
              aria-pressed={topN === n}
              className={[
                'rounded-lg border px-2.5 py-1 text-xs font-medium transition-all duration-150',
                topN === n
                  ? 'border-neon/40 bg-neon/[0.12] text-neon'
                  : 'border-white/10 bg-white/[0.03] text-text-mid hover:border-white/25 hover:text-text-hi',
              ].join(' ')}
            >
              {n}
            </button>
          ))}
        </div>
        <p className="mt-2 text-2xs text-text-low">
          The engine ranks the whole universe against your strategy and backtests the top {topN}{' '}
          matches. More stocks = longer runs.
        </p>
      </div>

      <div className="glass rounded-2xl p-4">
        <p className="flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wider text-text-low">
          <PieChart className="h-3.5 w-3.5 text-neon" aria-hidden="true" /> Portfolio simulation
        </p>
        <p className="mt-1 text-2xs text-text-low">
          Combine all {topN} picks into ONE shared account — combined equity curve, drawdown and
          Sharpe — instead of {topN} separate $100k accounts.
        </p>
        <div className="mt-3 space-y-3">
          <div>
            <p className="text-xs text-text-mid">Allocation</p>
            <div className="mt-1.5 flex gap-1.5">
              {[
                ['equal', 'Equal weight'],
                ['risk_based', 'Risk-based'],
              ].map(([v, label]) => (
                <button
                  key={v}
                  type="button"
                  onClick={() => setPortfolioAllocation(v as 'equal' | 'risk_based')}
                  aria-pressed={portfolioAllocation === v}
                  className={[
                    'rounded-lg border px-2.5 py-1 text-xs font-medium transition-all duration-150',
                    portfolioAllocation === v
                      ? 'border-neon/40 bg-neon/[0.12] text-neon'
                      : 'border-white/10 bg-white/[0.03] text-text-mid hover:border-white/25 hover:text-text-hi',
                  ].join(' ')}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          <div>
            <p className="text-xs text-text-mid">Rebalance</p>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {['none', 'monthly', 'quarterly', 'yearly'].map((f) => (
                <button
                  key={f}
                  type="button"
                  onClick={() =>
                    setRebalanceFrequency(f as 'none' | 'monthly' | 'quarterly' | 'yearly')
                  }
                  aria-pressed={rebalanceFrequency === f}
                  className={[
                    'rounded-lg border px-2.5 py-1 text-xs font-medium capitalize transition-all duration-150',
                    rebalanceFrequency === f
                      ? 'border-neon/40 bg-neon/[0.12] text-neon'
                      : 'border-white/10 bg-white/[0.03] text-text-mid hover:border-white/25 hover:text-text-hi',
                  ].join(' ')}
                >
                  {f}
                </button>
              ))}
            </div>
          </div>
          <div className="flex items-center justify-between">
            <label htmlFor="reinvest" className="text-xs text-text-mid">
              Reinvest cash dividends
            </label>
            <Switch
              id="reinvest"
              checked={dividendReinvest}
              onCheckedChange={setDividendReinvest}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor="borrow" className="text-xs text-text-mid">
                Borrow rate % (shorts)
              </label>
              <Input
                id="borrow"
                type="number"
                min={0}
                max={100}
                step={0.5}
                value={borrowRate}
                onChange={(e) => setBorrowRate(Number(e.target.value))}
                className="mt-1 h-9"
              />
            </div>
            <div>
              <label htmlFor="sectorCap" className="text-xs text-text-mid">
                Max stocks per sector
              </label>
              <Input
                id="sectorCap"
                type="number"
                min={0}
                max={50}
                step={1}
                value={maxSector}
                onChange={(e) => setMaxSector(Number(e.target.value))}
                className="mt-1 h-9"
              />
            </div>
          </div>
          <p className="text-2xs text-text-low">
            0 = unlimited sector exposure · borrow rate only applies when short selling is on
          </p>
        </div>
      </div>

      <div className="glass rounded-2xl p-4">
        <p className="text-2xs font-semibold uppercase tracking-wider text-text-low">
          Risk & execution
        </p>
        <div className="mt-3 space-y-3">
          <p className="flex items-center justify-between text-xs text-text-mid">
            <span className="flex items-center gap-1.5">
              <DollarSign className="h-3.5 w-3.5 text-neon" aria-hidden="true" /> Starting capital
            </span>
            <span className="font-mono tabular text-text-hi">
              {fmtCurrency(100000, 0)} <span className="text-text-low">(fixed)</span>
            </span>
          </p>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor="stop" className="text-xs text-text-mid">
                Stop loss %
              </label>
              <Input
                id="stop"
                type="number"
                value={stopLoss ?? ''}
                placeholder="off"
                min={0}
                onChange={(e) => setStopLoss(e.target.value === '' ? null : Number(e.target.value))}
                className="mt-1 h-9"
              />
            </div>
            <div>
              <label htmlFor="profit" className="text-xs text-text-mid">
                Take profit %
              </label>
              <Input
                id="profit"
                type="number"
                value={takeProfit ?? ''}
                placeholder="off"
                min={0}
                onChange={(e) =>
                  setTakeProfit(e.target.value === '' ? null : Number(e.target.value))
                }
                className="mt-1 h-9"
              />
            </div>
          </div>

          <div className="flex items-center justify-between">
            <label htmlFor="short" className="text-xs text-text-mid">
              Allow short selling
            </label>
            <Switch id="short" checked={allowShort} onCheckedChange={setAllowShort} />
          </div>
        </div>
      </div>

      <div className="glass rounded-2xl p-4">
        <p className="text-2xs font-semibold uppercase tracking-wider text-text-low">
          Signal thresholds
        </p>
        <p className="mt-1 text-2xs text-text-low">
          Leave blank to use what your strategy says. A value here replaces the strategy&apos;s RSI
          entry or exit, and the result shows exactly which rules ran.
        </p>
        <div className="mt-3 grid grid-cols-2 gap-3">
          <div>
            <label htmlFor="rsiBuy" className="text-xs text-text-mid">
              RSI buy below
            </label>
            <Input
              id="rsiBuy"
              type="number"
              value={rsiBuy ?? ''}
              placeholder="from strategy"
              min={0}
              onChange={(e) => setRsiBuy(e.target.value === '' ? null : Number(e.target.value))}
              className="mt-1 h-9"
            />
          </div>
          <div>
            <label htmlFor="rsiSell" className="text-xs text-text-mid">
              RSI sell above
            </label>
            <Input
              id="rsiSell"
              type="number"
              value={rsiSell ?? ''}
              placeholder="from strategy"
              min={0}
              onChange={(e) => setRsiSell(e.target.value === '' ? null : Number(e.target.value))}
              className="mt-1 h-9"
            />
          </div>
        </div>
      </div>

      <div className="glass rounded-2xl p-4">
        <p className="flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wider text-text-low">
          <CalendarRange className="h-3.5 w-3.5" aria-hidden="true" /> Date range
        </p>
        <div className="mt-3 grid grid-cols-2 gap-3">
          <div>
            <label htmlFor="start" className="text-xs text-text-mid">
              Start
            </label>
            <input
              id="start"
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              className="mt-1 h-9 w-full rounded-xl border border-white/10 bg-white/[0.04] px-3 text-xs text-text-hi focus:border-neon/60 focus:outline-none focus:ring-2 focus:ring-neon/25 [color-scheme:dark]"
            />
          </div>
          <div>
            <label htmlFor="end" className="text-xs text-text-mid">
              End
            </label>
            <input
              id="end"
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
              className="mt-1 h-9 w-full rounded-xl border border-white/10 bg-white/[0.04] px-3 text-xs text-text-hi focus:border-neon/60 focus:outline-none focus:ring-2 focus:ring-neon/25 [color-scheme:dark]"
            />
          </div>
        </div>
        <p className="mt-2 text-2xs text-text-low">Leave blank for the full 10-year window.</p>
      </div>
    </div>
  )

  // ── Right-top: results ──────────────────────────────────────────────────────
  const resultsPane = (
    <section
      aria-label="Backtest results"
      className="flex h-full min-h-0 flex-col gap-3 overflow-y-auto"
    >
      {!result && !loading && !error && (
        <div className="flex h-full items-center">
          <EmptyState
            icon={LineChart}
            title="Backtest a strategy for free"
            description="Describe a strategy in the chat — e.g. “buy oversold tech stocks” — and I'll generate the scan code, run 10 years of simulated trading, and show you the verdict."
            className="w-full"
            action={<TemplatePicker onPick={runTemplate} disabled={pending} />}
          />
        </div>
      )}
      {loading && !result && !jobStatus && (
        <div className="space-y-3">
          <p className="text-sm text-text-low">
            Running 10 years of simulated trading across up to {topN} stocks —{' '}
            {topN >= 20
              ? 'this is a heavy run and can take several minutes to ~15+ on a cold cache.'
              : 'this can take a few minutes on a cold cache.'}
          </p>
          <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-24 rounded-2xl" />
            ))}
          </div>
          <Skeleton className="h-[280px] w-full rounded-2xl" />
          <Skeleton className="h-40 w-full rounded-2xl" />
        </div>
      )}
      {/* Tier 3: live per-symbol progress while the async job runs */}
      {loading && jobStatus && jobStatus.status !== 'done' && (
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className="glass rounded-2xl p-4"
        >
          <div className="mb-3 flex items-center justify-between">
            <p className="flex items-center gap-2 text-sm font-medium text-text-hi">
              {jobStatus.status === 'error' ? (
                <AlertTriangle className="h-4 w-4 text-loss" aria-hidden="true" />
              ) : (
                <Loader2 className="h-4 w-4 animate-spin text-neon" aria-hidden="true" />
              )}
              {jobStatus.status === 'error'
                ? 'Backtest failed'
                : jobStatus.phase === 'interpreting'
                  ? 'Interpreting your strategy…'
                  : `Backtesting symbols… ${(jobStatus.done_symbols ?? []).length}/${jobStatus.total_symbols ?? topN}`}
            </p>
            <Badge variant="neutral" className="text-[11px] normal-case">
              {jobStatus.phase === 'interpreting' ? 'LLM' : 'engine'}
            </Badge>
          </div>
          <div className="flex flex-wrap gap-2">
            {(jobStatus.done_symbols ?? []).map((sym) => (
              <span
                key={sym}
                className="flex items-center gap-1 rounded-lg bg-gain/[0.08] px-2.5 py-1 font-mono text-xs font-semibold text-gain-soft ring-1 ring-inset ring-gain/25"
              >
                <CheckCircle2 className="h-3 w-3" aria-hidden="true" /> {sym}
              </span>
            ))}
            {jobStatus.status !== 'error' && (
              <span className="flex items-center gap-1.5 rounded-lg bg-white/[0.04] px-2.5 py-1 font-mono text-xs text-text-low ring-1 ring-inset ring-white/10">
                <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
                {jobStatus.phase === 'interpreting' ? 'interpreting' : 'next…'}
              </span>
            )}
          </div>
          <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
            <div
              className="h-full rounded-full bg-neon transition-all duration-500"
              style={{
                width: `${((jobStatus.done_symbols ?? []).length / Math.max(1, jobStatus.total_symbols ?? topN)) * 100}%`,
              }}
            />
          </div>
        </motion.div>
      )}
      {error && !result && (
        <EmptyState
          icon={AlertTriangle}
          title="Backtest failed"
          description={error}
          action={
            <Button onClick={() => run(lastQuery.current)} disabled={pending}>
              {pending ? `Retry in ${waitSeconds}s` : 'Retry'}
            </Button>
          }
        />
      )}
      {result && result.total_symbols_tested === 0 && (
        <EmptyState
          icon={SearchX}
          title="No stocks matched this strategy"
          description={
            result.message ??
            'Your strategy screened to 0 candidates — nothing was simulated. Try a broader query or looser filters (e.g. drop the momentum condition).'
          }
          className="w-full"
          action={
            <TemplatePicker
              onPick={runTemplate}
              disabled={pending}
              heading="Or run a ready-made strategy instead"
            />
          }
        />
      )}{' '}
      {result && result.total_symbols_tested > 0 && (
        <>
          {/* Tier 3 compare mode: baseline pinned, this run is the challenger */}
          {baseline && (
            <motion.div
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
            >
              <ComparePanel
                baseline={baseline}
                challenger={result}
                onClear={() => setBaseline(null)}
              />
            </motion.div>
          )}

          {kpis && (
            <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
              {kpis.map((k, i) => (
                <KpiCard key={k.label} {...k} delay={i * 0.06} />
              ))}
            </div>
          )}

          {/* Risk disclaimer: directly under the KPI cards, inside the result
              block. The spec is explicit that it belongs next to the number,
              in the same visual block, and not in 8pt grey at the bottom of
              the page — a disclaimer nobody reads is not a disclaimer. */}
          <RiskDisclaimer />

          {active && (
            <motion.div
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
              className="glass rounded-2xl p-4"
            >
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <span className="text-2xs font-semibold uppercase tracking-wider text-text-low">
                  Simulated strategy
                </span>
                <Badge variant="grape" className="text-[11px] normal-case tracking-normal">
                  {(result?.strategy?.entry as string | undefined) ??
                    strategyToSentence(conditions)}
                </Badge>
              </div>
              <EquityCurve item={active} height={260} />
              {active.data_quality && active.data_quality.length > 0 && (
                <div className="mt-3 space-y-1.5">
                  {active.data_quality.map((note) => (
                    <p
                      key={note}
                      className="flex items-start gap-2 rounded-lg bg-white/[0.03] px-3 py-2 text-2xs leading-relaxed text-text-low"
                    >
                      <ShieldAlert
                        className="mt-0.5 h-3 w-3 shrink-0 text-text-low"
                        aria-hidden="true"
                      />
                      {note}
                    </p>
                  ))}
                </div>
              )}
            </motion.div>
          )}

          {active &&
            (active.monte_carlo || active.walk_forward || active.parameter_sensitivity) && (
              <motion.div
                initial={{ opacity: 0, y: 16 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, delay: 0.04, ease: [0.16, 1, 0.3, 1] }}
                className="glass rounded-2xl p-4"
              >
                <Robustness item={active} />
              </motion.div>
            )}

          {result.portfolio && result.portfolio.equity_curve.length > 0 && (
            <motion.div
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5, delay: 0.08, ease: [0.16, 1, 0.3, 1] }}
              className="rounded-2xl border border-neon/20 bg-gradient-to-b from-neon/[0.06] to-transparent p-4"
            >
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <span className="flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wider text-text-low">
                  <PieChart className="h-3.5 w-3.5 text-neon" aria-hidden="true" /> Combined
                  portfolio ({result.portfolio.symbols.length} symbols)
                </span>
                <Badge variant="neutral" className="text-[11px] normal-case">
                  {result.portfolio.allocation === 'equal' ? 'Equal weight' : 'Risk-based'} ·
                  rebalance {result.portfolio.rebalance}
                </Badge>
              </div>
              <div className="mb-4 grid grid-cols-2 gap-3 xl:grid-cols-4">
                <div className="rounded-xl bg-white/[0.04] px-3 py-2.5">
                  <p className="text-2xs uppercase tracking-wider text-text-low">Total return</p>
                  <p
                    className={`font-mono text-lg font-bold tabular ${(result.portfolio.total_return_pct ?? 0) >= 0 ? 'text-gain-soft' : 'text-loss-soft'}`}
                  >
                    {fmtPct(result.portfolio.total_return_pct)}
                  </p>
                </div>
                <div className="rounded-xl bg-white/[0.04] px-3 py-2.5">
                  <p className="text-2xs uppercase tracking-wider text-text-low">Sharpe</p>
                  <p className="font-mono text-lg font-bold tabular text-text-hi">
                    {result.portfolio.sharpe_ratio?.toFixed(2) ?? '—'}
                  </p>
                </div>
                <div className="rounded-xl bg-white/[0.04] px-3 py-2.5">
                  <p className="text-2xs uppercase tracking-wider text-text-low">Max drawdown</p>
                  <p className="font-mono text-lg font-bold tabular text-loss-soft">
                    {fmtPct(result.portfolio.max_drawdown_pct)}
                  </p>
                </div>
                <div className="rounded-xl bg-white/[0.04] px-3 py-2.5">
                  <p className="text-2xs uppercase tracking-wider text-text-low">Final value</p>
                  <p className="font-mono text-lg font-bold tabular text-text-hi">
                    {fmtCurrency(result.portfolio.final_value, 0)}
                  </p>
                </div>
              </div>
              <EquityCurve
                item={
                  {
                    symbol: 'PORTFOLIO',
                    equity_curve: result.portfolio.equity_curve,
                    drawdown_curve: result.portfolio.drawdown_curve ?? [],
                    total_return_pct: result.portfolio.total_return_pct,
                  } as unknown as BacktestResultItem
                }
                showBenchmark={false}
                height={240}
              />
              <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1">
                {Object.entries(result.portfolio.weights).map(([sym, w]) => (
                  <span key={sym} className="font-mono text-2xs text-text-low">
                    {sym} <span className="text-text-mid">{fmtPct(w * 100, 1)}</span>
                  </span>
                ))}
              </div>
            </motion.div>
          )}

          <div className="glass rounded-2xl p-4">
            <p className="mb-3 text-2xs font-semibold uppercase tracking-wider text-text-low">
              Per-symbol results ({result.total_symbols_tested})
            </p>
            <div className="flex flex-wrap gap-2">
              {Object.values(result.results).map((r) => (
                <button
                  key={r.symbol}
                  onClick={() => setActiveSymbol(r.symbol)}
                  aria-pressed={r.symbol === activeSymbol}
                  className={[
                    'flex min-w-[120px] flex-col rounded-xl border p-3 text-left transition-all duration-200',
                    r.symbol === activeSymbol
                      ? 'border-neon/40 bg-neon/[0.08] shadow-[0_0_16px_rgba(0,212,255,0.15)]'
                      : 'border-white/10 bg-white/[0.03] hover:border-white/25 hover:bg-white/[0.05]',
                  ].join(' ')}
                >
                  <div className="flex items-center gap-1.5">
                    <span className="font-mono text-sm font-bold text-text-hi">{r.symbol}</span>
                    {r.is_stale && (
                      <span className="rounded-full bg-amber-400/[0.12] px-1.5 py-0.5 text-[9px] font-medium text-amber-300 ring-1 ring-inset ring-amber-400/30">
                        Stale
                      </span>
                    )}
                  </div>
                  <span
                    className={`font-mono text-xs tabular ${(r.total_return_pct ?? 0) >= 0 ? 'text-gain-soft' : 'text-loss-soft'}`}
                  >
                    {fmtPct(r.total_return_pct)}
                  </span>
                </button>
              ))}
            </div>

            {active && active.warnings.length > 0 && (
              <div className="mt-3 space-y-2">
                {active.warnings.map((w) => (
                  <p
                    key={w}
                    className="flex items-center gap-2 rounded-lg bg-amber-400/[0.07] px-3 py-2 text-xs text-amber-300"
                  >
                    <ShieldAlert className="h-3.5 w-3.5 shrink-0" aria-hidden="true" /> {w}
                  </p>
                ))}
              </div>
            )}

            {active && active.trades.length > 0 && (
              <div className="mt-4">
                <p className="mb-2 text-2xs font-semibold uppercase tracking-wider text-text-low">
                  Recent trades
                </p>
                <div className="max-h-44 overflow-y-auto rounded-xl border border-white/[0.06]">
                  <table className="w-full text-xs">
                    <thead className="sticky top-0 bg-ink-panel/95 backdrop-blur">
                      <tr className="text-left text-text-low">
                        <th className="px-3 py-2 font-medium">Side</th>
                        <th className="px-3 py-2 font-medium">Buy</th>
                        <th className="px-3 py-2 font-medium">Sell</th>
                        <th className="px-3 py-2 text-right font-medium">PnL</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-white/[0.05]">
                      {active.trades
                        .slice(-8)
                        .reverse()
                        .map((t, i) => (
                          <tr key={i}>
                            <td className="px-3 py-2">
                              <span
                                className={`font-mono text-[11px] font-bold ${t.side === 'long' ? 'text-neon' : 'text-grape-soft'}`}
                              >
                                {t.side.toUpperCase()}
                              </span>
                            </td>
                            <td className="px-3 py-2 font-mono tabular text-text-mid">
                              {fmtCurrency(t.buy_price)}
                            </td>
                            <td className="px-3 py-2 font-mono tabular text-text-mid">
                              {t.sell_price != null ? fmtCurrency(t.sell_price) : '—'}
                            </td>
                            <td
                              className={`px-3 py-2 text-right font-mono tabular ${(t.pnl ?? 0) >= 0 ? 'text-gain-soft' : 'text-loss-soft'}`}
                            >
                              {fmtPct(t.pnl_pct)}
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            <div className="mt-4 flex flex-wrap items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => run(lastQuery.current)}
                loading={loading}
                disabled={pending}
              >
                {pending ? (
                  `Please wait ${waitSeconds}s`
                ) : (
                  <>
                    {!loading && <RotateCw className="h-3.5 w-3.5" aria-hidden="true" />} Re-run
                    with parameters
                  </>
                )}
              </Button>
              {/* Tier 3: CSV export of simulated trades */}
              <Button
                variant="outline"
                size="sm"
                onClick={async () => {
                  setExporting(true)
                  try {
                    const blob = await exportBacktestCsv(buildRequest(lastQuery.current))
                    downloadBlob(blob, `stratoscan_trades_${Date.now()}.csv`)
                  } catch {
                    toast.error('Export failed', 'Could not generate the CSV. Please try again.')
                  } finally {
                    setExporting(false)
                  }
                }}
                loading={exporting}
                disabled={pending || (!signedIn && !DEMO_MODE)}
                title={
                  !signedIn && !DEMO_MODE ? 'Create a free account to export trades' : undefined
                }
              >
                {!exporting && <Download className="h-3.5 w-3.5" aria-hidden="true" />} Export CSV
              </Button>
              {/* Tier 3: compare mode — pin this run as the baseline */}
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setBaseline(result)
                  toast.success(
                    'Baseline set',
                    'Run another strategy to compare against this one on the same symbol set.',
                  )
                }}
                disabled={!result || loading}
              >
                <GitCompareArrows className="h-3.5 w-3.5" aria-hidden="true" /> Set as baseline
              </Button>
              {/* Day 6: publish this result as a public, server-rendered page.
                  Demo mode (no server) falls back to the setup link, which
                  re-runs the same strategy rather than showing this result. */}
              <Button
                variant="outline"
                size="sm"
                loading={sharing}
                onClick={async () => {
                  let url: string
                  if (!DEMO_MODE && lastJobId) {
                    setSharing(true)
                    try {
                      const link = await shareResult(lastJobId)
                      setShare(link)
                      url = link.url
                    } catch (err) {
                      toast.error('Could not share', toApiError(err).message)
                      return
                    } finally {
                      setSharing(false)
                    }
                  } else {
                    url = `${window.location.origin}${window.location.pathname}?${serializeParams(buildRequest(lastQuery.current))}`
                  }
                  try {
                    await navigator.clipboard.writeText(url)
                    setCopied(true)
                    setTimeout(() => setCopied(false), 2000)
                    toast.success(
                      'Link copied',
                      'Anyone with the link can see this result. You can stop sharing anytime.',
                    )
                  } catch {
                    window.prompt('Copy your shareable backtest link:', url)
                  }
                  // Reached in both branches — clipboard or the prompt — so it
                  // means "the link was put in the user's hands", which is the
                  // sharing signal worth counting. No props: a share has
                  // nothing to describe that would not be user content.
                  track('result_shared')
                }}
                disabled={!result || loading || sharing || result.total_symbols_tested === 0}
              >
                {!sharing &&
                  (copied ? (
                    <CheckCircle2 className="h-3.5 w-3.5 text-gain-soft" aria-hidden="true" />
                  ) : (
                    <Link2 className="h-3.5 w-3.5" aria-hidden="true" />
                  ))}
                {copied ? 'Copied' : share ? 'Copy link' : 'Share result'}
              </Button>
              {share && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={async () => {
                    try {
                      await revokeShare(share.share_id)
                      setShare(null)
                      toast.success('Sharing stopped', 'That link no longer works for anyone.')
                    } catch (err) {
                      toast.error('Could not stop sharing', toApiError(err).message)
                    }
                  }}
                >
                  <Link2Off className="h-3.5 w-3.5" aria-hidden="true" /> Stop sharing
                </Button>
              )}
              {result && (
                <Badge variant="neutral" className="text-[11px] normal-case">
                  {fmtPct(active?.total_return_pct ?? 0)} · {active?.total_trades ?? 0} trades · 10y
                  daily
                </Badge>
              )}
            </div>

            {DEMO_MODE && (
              <p className="mt-3 flex items-center gap-1.5 text-2xs text-text-low">
                <BadgePercent className="h-3 w-3" aria-hidden="true" />
                Demo data — simulated on synthetic history. Connect the real API for verified
                yfinance bars.
              </p>
            )}
          </div>
        </>
      )}
    </section>
  )

  const chatPane = (
    <ChatPanel
      messages={messages}
      loading={loading}
      onSend={(q) => void run(q)}
      tuneOpen={tuneOpen}
      onToggleTune={() => setTuneOpen((v) => !v)}
      tune={tunePanel}
      // buildRequest prepends ~100 chars of strategy params; cap the
      // user's raw input so the final query stays under 4000.
      maxLength={4000 - 100}
    />
  )

  const codePane = (
    <CodePanel
      status={codeStatus}
      query={lastQuery.current}
      params={codeParams}
      symbols={result?.total_symbols_tested}
    />
  )

  return (
    <div className="flex h-[calc(100vh-7rem)] flex-col">
      {!signedIn && !DEMO_MODE && <TrialBanner left={trialLeft} exhausted={trialExhausted} />}
      {/* Mobile: stacked */}
      <div data-layout="mobile" className="flex min-h-0 flex-1 flex-col gap-3 lg:hidden">
        <div className="h-[360px] shrink-0">{chatPane}</div>
        <div className="min-h-[280px] flex-1 rounded-2xl border border-white/[0.07] bg-ink-panel/50 p-3">
          {resultsPane}
        </div>
        <div className="h-[240px] shrink-0">{codePane}</div>
      </div>

      {/* Desktop: 3-pane resizable workspace */}
      <div data-layout="desktop" className="hidden min-h-0 flex-1 lg:block">
        <SplitPane
          direction="row"
          defaultRatio={0.35}
          minRatio={0.22}
          maxRatio={0.5}
          storageKey="bt-chat-split"
          labels={{ first: 'Chat', second: 'Results & code' }}
          className="h-full"
          first={chatPane}
          second={
            <SplitPane
              direction="column"
              defaultRatio={0.72}
              minRatio={0.5}
              maxRatio={0.88}
              storageKey="bt-results-split"
              labels={{ first: 'Results', second: 'Generated code' }}
              className="h-full"
              paneClassName="p-1"
              first={resultsPane}
              second={codePane}
            />
          }
        />
      </div>
    </div>
  )
}
