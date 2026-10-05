import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { motion } from 'framer-motion'
import {
  ArrowLeft,
  ArrowDownRight,
  ArrowUpRight,
  CalendarDays,
  Globe,
  Newspaper,
  Radio,
  Star,
  TrendingUp,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState } from '@/components/EmptyState'
import { EtfBadge } from '@/components/EtfBadge'
import { StockPriceChart } from '@/components/charts/StockPriceChart'
import { SentimentBadge } from '@/components/SentimentBadge'
import { stockDetail, newsForSymbol } from '@/lib/api/services'
import { toApiError } from '@/lib/api/client'
import { safeExternalUrl } from '@/lib/safeUrl'
import { usePersonal } from '@/store/personal'
import { toast } from '@/store/notifications'
import type { StockResult, SentimentSummary } from '@/lib/api/types'
import { fmtCurrency, fmtMarketCap, fmtNumber, fmtPct, timeAgo } from '@/lib/utils'
import { cn } from '@/lib/utils'

function Metric({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="glass-hover rounded-xl p-3.5">
      <p className="text-2xs font-semibold uppercase tracking-wider text-text-low">{label}</p>
      <p className="mt-1.5 font-mono text-lg font-bold tabular text-text-hi">{value}</p>
      {sub && <p className="mt-0.5 text-2xs text-text-mid">{sub}</p>}
    </div>
  )
}

export function StockDetail() {
  const { symbol = '' } = useParams()
  const [stock, setStock] = useState<StockResult | null>(null)
  const [sentiment, setSentiment] = useState<SentimentSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const { watchlist, toggleWatch } = usePersonal()
  const watched = watchlist.includes(symbol.toUpperCase())

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError(null)
    setStock(null)
    setSentiment(null)
    const key = symbol.toUpperCase()
    void Promise.allSettled([stockDetail(key), newsForSymbol(key)]).then(([s, n]) => {
      if (!alive) return
      if (s.status === 'fulfilled') setStock(s.value)
      else setError(toApiError(s.reason).message)
      if (n.status === 'fulfilled') setSentiment(n.value)
      setLoading(false)
    })
    return () => {
      alive = false
    }
  }, [symbol])

  const up = (stock?.price_change_pct ?? 0) >= 0

  const metrics = useMemo(() => {
    if (!stock) return []
    return [
      { label: 'Market cap', value: fmtMarketCap(stock.market_cap) },
      { label: 'P/E ratio', value: stock.pe_ratio?.toFixed(1) ?? '—' },
      {
        label: 'Volume',
        value: fmtNumber(stock.volume),
        sub: `Avg ${fmtNumber(stock.avg_volume)}`,
      },
      {
        label: '52-week range',
        value: fmtCurrency(stock.high_52w),
        sub: `Low ${fmtCurrency(stock.low_52w)}`,
      },
      { label: 'RSI (14)', value: stock.rsi_14?.toFixed(0) ?? '—', sub: 'momentum' },
      {
        label: 'Dividend yield',
        value: stock.dividend_yield ? `${stock.dividend_yield.toFixed(2)}%` : '—',
      },
      { label: 'Beta', value: stock.beta?.toFixed(2) ?? '—' },
      { label: 'EPS (TTM)', value: fmtCurrency(stock.eps_ttm) },
    ]
  }, [stock])

  if (loading) {
    return (
      <div className="mx-auto max-w-6xl space-y-5">
        <Skeleton className="h-8 w-40 rounded-xl" />
        <div className="grid gap-5 lg:grid-cols-[1fr_360px]">
          <Skeleton className="h-[340px] w-full rounded-2xl" />
          <div className="space-y-3">
            <Skeleton className="h-24 w-full rounded-2xl" />
            <Skeleton className="h-48 w-full rounded-2xl" />
          </div>
        </div>
      </div>
    )
  }

  if (error && !stock) {
    return (
      <div className="mx-auto max-w-6xl">
        <EmptyState
          icon={Newspaper}
          title="Stock not found"
          description={error}
          action={
            <Link to="/app/screener" className="text-sm font-medium text-neon hover:underline">
              ← Back to screener
            </Link>
          }
        />
      </div>
    )
  }

  if (!stock) return null

  return (
    <div className="mx-auto max-w-6xl">
      <Link
        to="/app/screener"
        className="mb-5 inline-flex items-center gap-2 text-xs text-text-low transition-colors hover:text-text-hi"
      >
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" /> Back to screener
      </Link>

      {/* Header */}
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-center gap-4">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-neon/20 to-grape/30 font-mono text-sm font-bold text-neon shadow-[inset_0_0_0_1px_rgba(0,212,255,0.25)]">
            {stock.symbol}
          </div>
          <div>
            <h1 className="flex items-center gap-2 text-2xl font-bold">
              {stock.company_name}
              {stock.asset_type === 'etf' && <EtfBadge />}
            </h1>
            <p className="mt-0.5 text-sm text-text-mid">
              {[
                stock.asset_type === 'etf' ? 'Exchange-traded fund' : null,
                stock.sector,
                stock.industry,
              ]
                .filter(Boolean)
                .join(' · ')}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <Badge variant={stock.is_stale ? 'warning' : 'neon'} dot>
            {stock.is_stale ? `Stale ${stock.data_age_minutes?.toFixed(0)}m` : 'Live data'}
          </Badge>
          <button
            onClick={() => {
              toggleWatch(symbol.toUpperCase())
              toast.success(
                watched ? 'Removed from watchlist' : 'Added to watchlist',
                symbol.toUpperCase(),
              )
            }}
            aria-label={watched ? `Remove ${symbol} from watchlist` : `Add ${symbol} to watchlist`}
            aria-pressed={watched}
            className={cn(
              'rounded-xl border p-2.5 transition-colors',
              watched
                ? 'border-neon/50 bg-neon/10 text-neon'
                : 'border-white/10 bg-white/[0.04] text-text-mid hover:border-neon/40 hover:text-neon',
            )}
          >
            <Star className={cn('h-4 w-4', watched && 'fill-neon')} aria-hidden="true" />
          </button>
        </div>
      </div>

      <div className="grid gap-5 lg:grid-cols-[1fr_360px]">
        {/* Left: price chart */}
        <div className="min-w-0 space-y-5">
          <motion.div
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
            className="glass rounded-2xl p-5"
          >
            <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
              <div>
                <p className="font-mono text-4xl font-bold tabular text-text-hi">
                  {fmtCurrency(stock.current_price)}
                </p>
                <p
                  className={cn(
                    'mt-1 flex items-center gap-1 font-mono text-sm font-semibold tabular',
                    up ? 'text-gain-soft' : 'text-loss-soft',
                  )}
                >
                  {up ? (
                    <ArrowUpRight className="h-4 w-4" aria-hidden="true" />
                  ) : (
                    <ArrowDownRight className="h-4 w-4" aria-hidden="true" />
                  )}
                  {fmtPct(stock.price_change_pct)} today
                </p>
              </div>
              <div className="flex items-center gap-4 text-2xs text-text-low">
                <span className="flex items-center gap-1.5">
                  <span className="h-1.5 w-3 rounded-full bg-neon" aria-hidden="true" /> Price
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-1.5 w-3 rounded-full bg-grape-soft/70" aria-hidden="true" />{' '}
                  SMA-50
                </span>
              </div>
            </div>
            <StockPriceChart stock={stock} />
          </motion.div>

          {/* Why it matched */}
          {stock.why_matched && stock.why_matched.length > 0 && (
            <motion.div
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5, delay: 0.08 }}
              className="glass rounded-2xl p-5"
            >
              <p className="mb-3 text-2xs font-semibold uppercase tracking-wider text-text-low">
                Why it matched
              </p>
              <ul className="space-y-2">
                {stock.why_matched.map((w) => (
                  <li key={w} className="flex items-start gap-2.5 text-sm text-text-mid">
                    <span
                      className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-neon"
                      aria-hidden="true"
                    />
                    {w}
                  </li>
                ))}
              </ul>
            </motion.div>
          )}

          {/* Score breakdown */}
          {stock.score_components && (
            <motion.div
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5, delay: 0.12 }}
              className="glass rounded-2xl p-5"
            >
              <p className="mb-4 flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wider text-text-low">
                <TrendingUp className="h-3.5 w-3.5" aria-hidden="true" /> Composite score
              </p>
              <div className="flex items-center gap-6">
                <p className="font-mono text-5xl font-bold tabular text-neon">
                  {stock.score?.toFixed(0)}
                </p>
                <div className="flex-1 space-y-2.5">
                  {Object.entries(stock.score_components).map(([k, v]) => (
                    <div key={k}>
                      <div className="mb-1 flex justify-between text-2xs">
                        <span className="capitalize text-text-mid">{k}</span>
                        <span className="font-mono tabular text-text-hi">{v}</span>
                      </div>
                      <div className="h-1.5 overflow-hidden rounded-full bg-white/10">
                        <div
                          className="h-full rounded-full bg-gradient-to-r from-neon to-grape"
                          style={{ width: `${Math.min(100, v)}%` }}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </motion.div>
          )}
        </div>

        {/* Right: metrics + sentiment + news */}
        <div className="space-y-5">
          <div className="grid grid-cols-2 gap-3">
            {metrics.map((m) => (
              <Metric key={m.label} {...m} />
            ))}
          </div>

          {sentiment && (
            <motion.div
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5, delay: 0.15 }}
              className="glass rounded-2xl p-5"
            >
              <p className="mb-3 flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wider text-text-low">
                <Radio className="h-3.5 w-3.5" aria-hidden="true" /> Sentiment
              </p>
              <div className="mb-3 flex items-center justify-between">
                <SentimentBadge score={sentiment.company_news_score} />
                <span className="text-2xs text-text-low">buzz {sentiment.buzz.toFixed(2)}</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-white/10">
                <div
                  className={cn(
                    'h-full rounded-full',
                    sentiment.company_news_score >= 0 ? 'bg-gain' : 'bg-loss',
                  )}
                  style={{
                    width: `${Math.min(100, Math.abs(sentiment.company_news_score) * 100)}%`,
                  }}
                />
              </div>
            </motion.div>
          )}

          {sentiment && (
            <motion.div
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5, delay: 0.2 }}
              className="glass rounded-2xl p-5"
            >
              <p className="mb-3 flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wider text-text-low">
                <Globe className="h-3.5 w-3.5" aria-hidden="true" /> News
              </p>
              <div className="space-y-3">
                {sentiment.articles.slice(0, 5).map((a) => {
                  const href = safeExternalUrl(a.url)
                  const content = (
                    <>
                      <div className="flex items-start justify-between gap-2">
                        <p className="line-clamp-2 text-xs font-medium leading-relaxed text-text-hi group-hover:text-neon">
                          {a.headline}
                        </p>
                        <SentimentBadge score={a.sentiment_score} compact />
                      </div>
                      <p className="mt-1.5 flex items-center gap-1.5 text-2xs text-text-low">
                        <CalendarDays className="h-3 w-3" aria-hidden="true" /> {a.source} ·{' '}
                        {timeAgo(a.published_at)}
                      </p>
                    </>
                  )
                  // Article URLs come from the news pipeline — render a real
                  // link only when the scheme is a safe http(s) URL.
                  return href ? (
                    <a
                      key={a.url + a.published_at}
                      href={href}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="group block rounded-xl p-2.5 transition-colors hover:bg-white/[0.05]"
                    >
                      {content}
                    </a>
                  ) : (
                    <div
                      key={a.url + a.published_at}
                      className="block rounded-xl p-2.5"
                      aria-label="Article link unavailable"
                    >
                      {content}
                    </div>
                  )
                })}
              </div>
            </motion.div>
          )}
        </div>
      </div>
    </div>
  )
}
