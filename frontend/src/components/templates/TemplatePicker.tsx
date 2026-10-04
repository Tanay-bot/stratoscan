import { Link } from 'react-router-dom'
import { ArrowRight, Sparkles } from 'lucide-react'
import { STARTER_TEMPLATE_IDS, templateById, type StrategyTemplate } from '@/lib/templates'

interface TemplatePickerProps {
  /** Run the template (the caller owns navigation / the run itself). */
  onPick: (template: StrategyTemplate) => void
  disabled?: boolean
  /** Heading above the cards. */
  heading?: string
}

/**
 * Four ready-made strategies for an empty box (Day 8). One click runs a real
 * backtest — the fastest path from "I don't know what to type" to a result.
 */
export function TemplatePicker({
  onPick,
  disabled,
  heading = 'Or start from a ready-made strategy',
}: TemplatePickerProps) {
  const starters = STARTER_TEMPLATE_IDS.map((id) => templateById(id)).filter(
    (t): t is StrategyTemplate => Boolean(t),
  )
  return (
    <div className="w-full max-w-2xl text-left">
      <p className="mb-2 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-text-low">
        <Sparkles className="h-3.5 w-3.5 text-neon" aria-hidden="true" />
        {heading}
      </p>
      <ul className="grid gap-2 sm:grid-cols-2">
        {starters.map((t) => (
          <li key={t.id}>
            <button
              type="button"
              disabled={disabled}
              onClick={() => onPick(t)}
              className="group flex h-full w-full flex-col gap-1 rounded-xl border border-white/[0.08] bg-white/[0.03] p-3 text-left transition-colors hover:border-neon/40 hover:bg-neon/[0.05] focus-visible:outline focus-visible:outline-2 focus-visible:outline-neon disabled:cursor-not-allowed disabled:opacity-50"
            >
              <span className="flex items-center justify-between gap-2 text-sm font-semibold text-text-hi">
                {t.title}
                <ArrowRight
                  className="h-3.5 w-3.5 shrink-0 text-text-low transition-transform group-hover:translate-x-0.5 group-hover:text-neon"
                  aria-hidden="true"
                />
              </span>
              <span className="text-xs text-text-mid">{t.idea}</span>
            </button>
          </li>
        ))}
      </ul>
      <Link
        to="/strategies"
        className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-neon hover:underline"
      >
        Browse all strategy templates <ArrowRight className="h-3 w-3" aria-hidden="true" />
      </Link>
    </div>
  )
}
