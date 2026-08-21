# Design — Stratoscan Frontend

<!-- impeccable:design-schema 1 -->

Recorded from the built world (`frontend/`), not from intention. This describes what ships.

## World

Bloomberg Terminal density inside Instagram-grade frosted glass. Deep-space ink ground, neon cyan primary, grape purple secondary. Data is the hero: bold tabular numerals in JetBrains Mono on frosted panels, with light uppercase micro-labels. Motion is orchestrated, not scattered — one authored hero animation (drawing equity lines + scanline + floating symbols), then restrained micro-interactions everywhere else.

## Tokens

- **Ground:** `#0a0a0f` (ink), `#07070b` (deep), panel `#0e0e16`/`#14141f`. Body background carries two faint radial glows (cyan upper-left, purple right) over the flat ground.
- **Accents:** `#00d4ff` neon (primary actions, active nav, live dots), `#7c3aed` grape (secondary), gains `#16c784`, losses `#ea3943`, amber for warnings.
- **Text:** hi `#f2f4fb`, mid `#9aa3b8`, low `#7c86a0` (≥4.5:1 on the ground).
- **Type:** Inter (UI), JetBrains Mono (all numerals, tickers, symbols, tabular columns via `font-variant-numeric: tabular-nums`). Tracking −0.03em on display headings; 2xs uppercase micro-labels for card headers.
- **Elevation:** glass panels use a 1px border + backdrop blur, never border-plus-wide-shadow (ghost-card ban). Glow shadows (`0 0 24px rgba(0,212,255,.35)`) reserved for the primary button and live indicators. Radii 12–16px; pills only for small controls.

## Glass system

- `.glass` / `.glass-strong`: layered `linear-gradient` white sheen + translucent panel + `backdrop-blur` — blur is functional (there are always gradient orbs/grid behind it to frost).
- `.glass-hover`: hover lifts 2px, warms the border toward neon, adds a soft cyan/purple halo. Applied to every interactive card.
- Surfaces consistently: sidebar, topbar, KPI cards, stock rows, news cards, auth modal, charts.

## Components

- **Logo:** orbit/radar glyph — neon ring, dashed grape inner ring, cyan core, drawn sine path.
- **Buttons:** primary (neon fill, dark text, glow, 1px lift on hover), grape, secondary (glass), ghost, outline, danger; sizes sm/default/lg/icon; built-in spinner on `loading`.
- **Data grid rows:** rank badge (gradient, mono index), symbol/company, price + arrowed signed change (green/red), market cap, P/E, RSI bar with tone coloring (≤30 gain, ≥70 loss, else neon), composite score pill (tone by tier), sentiment dot.
- **Charts:** Recharts, custom glass tooltips, gradient area fills keyed to gain/loss stroke, benchmark as dashed grape line, drawdown toggle. Sparklines are hand-rolled SVG with gradient under-fill.
- **Skeletons:** shimmer `linear-gradient` sweep; used for screen results, news feed, backtest, stock detail, dashboard movers.
- **Toasts:** glass-strong cards, bottom-right, icon per kind, auto-dismiss, `aria-live="polite"`.

## Motion

- Hero: equity lines draw via `stroke-dashoffset` transitions (staggered 0.3s+), `pulse-ring` on the line end, `scanline` sweep every 7s, floating symbols with `floaty` drift — all gated on IntersectionObserver, all disabled under `prefers-reduced-motion`.
- Tick-er: infinite CSS translate loop of a duplicated row (single shuffle, seamless −50% seam).
- Entrances: `motion.div` fade+rise with cubic-bezier(0.16,1,0.3,1); screen rows stagger ≤0.5s; KPI cards stagger 0.07s.
- Drawer (filters) and mobile sidebar: spring `x` slide with masked overlay; auth mode swap is a 0.22s directional crossfade.

## States

Loading (skeletons), error (EmptyState with the API's message + Retry), empty (dashed-border EmptyState with next action), rate-limited (toast with retry countdown), stale data (warning badge), verified/unverified email, signed-out redirects.

## Demo honesty

`VITE_DEMO_MODE` serves a deterministic synthetic universe through the typed API interface; demo surfaces carry a "Demo data" label; the Landing shows a "Live demo — synthetic data" badge. Synthetic price-series consumers (ticker, stock chart, watchlist sparklines) render nothing or a labeled placeholder when demo mode is off — production never shows invented prices beside real data.

## Accessibility

Global `:focus-visible` neon outline, labeled forms, `aria-label` on all icon buttons, `aria-pressed` on toggles, semantic `<a>`/`<button>`/`<table>`, `color-scheme: dark`, `tabular-nums`, `prefers-reduced-motion`, skip to content via main landmark.
