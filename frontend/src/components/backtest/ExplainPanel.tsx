import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, Info, RotateCw, ScrollText } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { explainResult } from '@/lib/api/services'
import type { Explanation, ExplanationFinding } from '@/lib/api/types'

type State = { kind: 'loading' } | { kind: 'error' } | { kind: 'ready'; explanation: Explanation }

const TONE: Record<ExplanationFinding['tone'], { icon: typeof Info; className: string }> = {
  caution: { icon: AlertTriangle, className: 'text-amber-300' },
  positive: { icon: CheckCircle2, className: 'text-gain-soft' },
  info: { icon: Info, className: 'text-text-low' },
}

/**
 * Day 11: what actually happened, in plain English.
 *
 * The findings come straight from the result's numbers and are always there;
 * the written summary is a model's reading of the same numbers, shown only
 * when it passed the server's compliance and number checks. If it is missing
 * the result still stands — this panel never blocks or hides anything.
 *
 * Render it with `key={jobId}` so a new run starts a new reading.
 */
export function ExplainPanel({ jobId }: { jobId: string }) {
  const [state, setState] = useState<State>({ kind: 'loading' })

  const load = useCallback(async () => {
    setState({ kind: 'loading' })
    try {
      setState({ kind: 'ready', explanation: await explainResult(jobId) })
    } catch {
      setState({ kind: 'error' })
    }
  }, [jobId])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <section className="glass rounded-2xl p-4" aria-labelledby="explain-title" aria-live="polite">
      <h3
        id="explain-title"
        className="mb-3 flex items-center gap-2 text-2xs font-semibold uppercase tracking-wider text-text-low"
      >
        <ScrollText className="h-3.5 w-3.5" aria-hidden="true" /> What happened
      </h3>

      {state.kind === 'loading' && (
        <div className="space-y-2" aria-busy="true" aria-label="Reading the result">
          <Skeleton className="h-4 w-11/12" />
          <Skeleton className="h-4 w-9/12" />
          <Skeleton className="h-4 w-10/12" />
        </div>
      )}

      {state.kind === 'error' && (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-text-mid">
            Couldn&apos;t read this result just now. The numbers above are unaffected.
          </p>
          <Button variant="outline" size="sm" onClick={() => void load()}>
            <RotateCw className="h-3.5 w-3.5" aria-hidden="true" /> Try again
          </Button>
        </div>
      )}

      {state.kind === 'ready' && (
        <>
          {state.explanation.narrative && (
            <div className="mb-3 space-y-2">
              <p className="text-sm leading-relaxed text-text-hi">
                {state.explanation.narrative.summary}
              </p>
              {state.explanation.narrative.points.length > 0 && (
                <ul className="list-disc space-y-1 pl-5 text-sm leading-relaxed text-text-mid">
                  {state.explanation.narrative.points.map((p) => (
                    <li key={p}>{p}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
          <ul className="space-y-1.5" aria-label="Findings from the numbers">
            {state.explanation.findings.map((f) => {
              const { icon: Icon, className } = TONE[f.tone] ?? TONE.info
              return (
                <li
                  key={f.code}
                  className="flex items-start gap-2 rounded-lg bg-white/[0.03] px-3 py-2 text-xs leading-relaxed text-text-mid"
                >
                  <Icon className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${className}`} aria-hidden="true" />
                  {f.text}
                </li>
              )
            })}
          </ul>
          {!state.explanation.narrative && (
            <p className="mt-2 text-2xs text-text-low">
              The written summary isn&apos;t available right now — the points above come straight
              from the numbers.
            </p>
          )}
          <p className="mt-3 text-2xs text-text-low">{state.explanation.disclaimer}</p>
        </>
      )}
    </section>
  )
}
