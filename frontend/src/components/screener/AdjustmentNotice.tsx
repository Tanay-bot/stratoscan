import { Info } from 'lucide-react'

function strings(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string' && x.length > 0) : []
}

/**
 * Says when the screener did not run exactly what was asked (Day 8).
 *
 * Two server-side rescues change a screen: the zero-match auto-relax drops
 * the least-covered filters (`data_quality.relaxed_from`), and the
 * contradiction resolver drops a condition that cannot coexist with another
 * (`data_quality.contradiction_resolved`). Showing different results than
 * were asked for, silently, is a trust bug — so both are spelled out.
 */
export function AdjustmentNotice({ dataQuality }: { dataQuality?: Record<string, unknown> }) {
  const relaxed = strings(dataQuality?.relaxed_from)
  const contradictions = strings(dataQuality?.contradiction_resolved)
  if (!relaxed.length && !contradictions.length) return null
  return (
    <div
      role="status"
      className="mb-4 flex gap-3 rounded-xl border border-amber-400/25 bg-amber-400/[0.07] px-3.5 py-3 text-sm text-text-mid"
    >
      <Info className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" aria-hidden="true" />
      <div className="space-y-1">
        {relaxed.length > 0 && (
          <p>
            <span className="font-semibold text-text-hi">We loosened your screen.</span> No stock
            matched every condition, so these were dropped to show the nearest matches:{' '}
            {relaxed.map((label, i) => (
              <span key={label}>
                {i > 0 && ', '}
                <code className="rounded bg-white/[0.06] px-1 text-xs text-text-hi">{label}</code>
              </span>
            ))}
            .
          </p>
        )}
        {contradictions.map((note) => (
          <p key={note}>
            <span className="font-semibold text-text-hi">One condition was dropped:</span> {note}.
          </p>
        ))}
      </div>
    </div>
  )
}
