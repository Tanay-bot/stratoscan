/**
 * P1-11 — keyboard a11y skip link.
 *
 * Rendered as the FIRST focusable element in the app (see App.tsx, placed
 * before the router outlet). Visually hidden until a keyboard user tabs to
 * it, then it reveals pinned top-left. Activating it moves focus + scroll to
 * `#main-content` on the current page so keyboard users skip the nav.
 *
 * Each page's main container carries `id="main-content" tabIndex={-1}` — the
 * tabIndex makes it a programmatic focus target (focus moves there on
 * activation) without adding it to the tab order.
 */
export function SkipToContent() {
  return (
    <a
      href="#main-content"
      className="fixed -top-[9999px] left-4 z-[100] rounded-xl border border-neon/40 bg-ink-deep px-4 py-2.5 text-sm font-semibold text-neon shadow-glow focus:top-4 focus:outline-none focus:ring-2 focus:ring-neon/60"
    >
      Skip to content
    </a>
  )
}
