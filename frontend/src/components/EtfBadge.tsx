/** Day 14: a fund is always labelled as one — it has no market cap or P/E. */
export function EtfBadge() {
  return (
    <span
      className="rounded-md border border-grape/30 bg-grape/15 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-grape-soft"
      title="Exchange-traded fund — no market cap, P/E or fundamentals"
    >
      ETF
    </span>
  )
}
