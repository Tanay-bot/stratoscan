import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { ExternalLink, Newspaper } from 'lucide-react'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState } from '@/components/EmptyState'
import { SentimentBadge } from '@/components/SentimentBadge'
import { newsFeed } from '@/lib/api/services'
import { toApiError } from '@/lib/api/client'
import { safeExternalUrl } from '@/lib/safeUrl'
import type { NewsArticle } from '@/lib/api/types'
import { timeAgo } from '@/lib/utils'
import { cn } from '@/lib/utils'

type Filter = 'all' | 'bullish' | 'neutral' | 'bearish'

const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'bullish', label: 'Bullish' },
  { key: 'neutral', label: 'Neutral' },
  { key: 'bearish', label: 'Bearish' },
]

export function News() {
  const [articles, setArticles] = useState<NewsArticle[] | null>(null)
  const [filter, setFilter] = useState<Filter>('all')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    setLoading(true)
    void newsFeed(48)
      .then((res) => {
        if (alive) setArticles(res.articles)
      })
      .catch((err) => {
        if (alive) setError(toApiError(err).message)
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [])

  const filtered = useMemo(() => {
    if (!articles) return []
    if (filter === 'all') return articles
    return articles.filter((a) => a.sentiment === filter)
  }, [articles, filter])

  const counts = useMemo(() => {
    const c = { bullish: 0, neutral: 0, bearish: 0 }
    for (const a of articles ?? []) {
      if (a.sentiment === 'bullish') c.bullish++
      else if (a.sentiment === 'bearish') c.bearish++
      else c.neutral++
    }
    return c
  }, [articles])

  return (
    <div className="mx-auto max-w-3xl">
      <div className="mb-6">
        <h1 className="text-2xl font-bold">News & sentiment</h1>
        <p className="mt-1 text-sm text-text-mid">
          Market coverage scored by an LLM sentiment pipeline — every article tagged bullish,
          neutral or bearish.
        </p>
      </div>

      {/* Sentiment toggles */}
      <div className="mb-6 flex flex-wrap gap-2" role="group" aria-label="Filter by sentiment">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => setFilter(f.key)}
            aria-pressed={filter === f.key}
            className={cn(
              'glass-hover flex items-center gap-2 rounded-full px-4 py-2 text-xs font-semibold transition-all duration-200',
              filter === f.key
                ? f.key === 'bullish'
                  ? 'border-gain/40 bg-gain/15 text-gain-soft'
                  : f.key === 'bearish'
                    ? 'border-loss/40 bg-loss/15 text-loss-soft'
                    : f.key === 'neutral'
                      ? 'border-white/25 bg-white/10 text-text-hi'
                      : 'border-neon/40 bg-neon/15 text-neon'
                : 'text-text-mid',
            )}
          >
            {f.label}
            {f.key !== 'all' && (
              <span className="font-mono text-2xs tabular opacity-70">
                {counts[f.key as 'bullish' | 'neutral' | 'bearish']}
              </span>
            )}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="space-y-3" aria-busy="true" aria-label="Loading news">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="glass space-y-3 rounded-2xl p-5">
              <div className="flex items-center gap-2">
                <Skeleton className="h-4 w-16" />
                <Skeleton className="h-4 w-24" />
              </div>
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-3 w-40" />
            </div>
          ))}
        </div>
      ) : error ? (
        <EmptyState icon={Newspaper} title="Couldn't load the news feed" description={error} />
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={Newspaper}
          title="No articles in this bucket"
          description="Try another sentiment filter, or check back after the next news refresh."
        />
      ) : (
        <div className="space-y-3">
          <AnimatePresence initial={false}>
            {filtered.map((a, i) => {
              const href = safeExternalUrl(a.url)
              return (
                <motion.article
                  data-news-card
                  key={a.url + a.published_at}
                  layout
                  initial={{ opacity: 0, y: 14 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, scale: 0.98 }}
                  transition={{
                    duration: 0.3,
                    delay: Math.min(i * 0.03, 0.4),
                    ease: [0.16, 1, 0.3, 1],
                  }}
                  className="glass-hover rounded-2xl p-5"
                >
                  <div className="mb-2.5 flex flex-wrap items-center gap-2.5">
                    <Link
                      to={`/app/stocks/${a.symbol}`}
                      className="rounded-md bg-white/[0.06] px-2 py-0.5 font-mono text-2xs font-bold text-neon transition-colors hover:bg-neon/15"
                    >
                      {a.symbol}
                    </Link>
                    <span className="text-2xs text-text-low">{a.source}</span>
                    <span className="text-2xs text-text-low">·</span>
                    <time className="text-2xs text-text-low" dateTime={a.published_at ?? undefined}>
                      {timeAgo(a.published_at)}
                    </time>
                    <span className="ml-auto">
                      <SentimentBadge score={a.sentiment_score} label={a.sentiment ?? undefined} />
                    </span>
                  </div>
                  <h2 className="text-base font-semibold leading-snug text-text-hi">
                    {a.headline}
                  </h2>
                  {a.summary && (
                    <p className="mt-1.5 line-clamp-2 text-sm leading-relaxed text-text-mid">
                      {a.summary}
                    </p>
                  )}
                  {href ? (
                    <a
                      href={href}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="mt-3 inline-flex items-center gap-1.5 text-2xs font-medium text-neon transition-colors hover:underline"
                    >
                      Read article <ExternalLink className="h-3 w-3" aria-hidden="true" />
                    </a>
                  ) : (
                    <span className="mt-3 inline-block text-2xs text-text-low">
                      Source link unavailable
                    </span>
                  )}
                </motion.article>
              )
            })}
          </AnimatePresence>
        </div>
      )}
    </div>
  )
}
