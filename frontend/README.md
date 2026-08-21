# Stratoscan Frontend

React + Vite single-page app for Stratoscan — an AI-powered stock screener and
backtesting platform.

## Core Web Vitals → Sentry

The app captures [Core Web Vitals](https://web.dev/vitals/) (LCP, CLS, INP, FCP,
TTFB) and routes them into Sentry's performance feed via **two complementary
paths**:

1. **`Sentry.browserTracingIntegration()`** (in `src/main.tsx`) — attaches Web
   Vitals as measurements to pageload transactions. This is the source of
   Sentry's **Web Vitals module** dashboard. Sampled at `tracesSampleRate` (10%).
2. **`src/lib/webVitals.ts`** — the `web-vitals` package callbacks forward every
   metric through `Sentry.metrics.distribution()` at **100% coverage**
   (unsampled), tagged with the metric name, for the Metrics feed.

### How to enable

Both paths are gated on `VITE_SENTRY_DSN` being set — with no DSN, the app runs
with zero telemetry and no network calls:

```bash
# .env.local (dev) or your Vercel project env (prod)
VITE_SENTRY_DSN=https://<key>@o<org>.ingest.sentry.io/<project>
```

The reporter registers itself in `main.tsx` right after `Sentry.init`:

```ts
initWebVitals() // no-op unless VITE_SENTRY_DSN is set
```

### Metric names

| Metric | Sentry metric name | Unit |
| --- | --- | --- |
| Largest Contentful Paint | `web-vitals.lcp` | millisecond |
| Cumulative Layout Shift | `web-vitals.cls` | (unitless) |
| Interaction to Next Paint | `web-vitals.inp` | millisecond |
| First Contentful Paint | `web-vitals.fcp` | millisecond |
| Time to First Byte | `web-vitals.ttfb` | millisecond |

### Verifying it works

- **Local probe**: with `VITE_SENTRY_DSN` set, reload and open the Network tab —
  you'll see metric envelopes flushed to Sentry's ingest endpoint.
- **Sentry UI**: `Performance → Web Vitals` shows LCP/CLS/INP over time (from
  BrowserTracing); `Metrics` shows the unsampled `web-vitals.*` distributions.
  Confirm the latter appears under your project's Metrics feed once a few page
  loads have happened with the DSN set.
- **Tests**: `src/lib/webVitals.test.ts` asserts all five callbacks are
  registered and forward `name`/`value`/`unit` correctly, that disabled mode is
  a no-op, and that duplicate init calls are ignored.

### Costs / sampling

- BrowserTracing transactions are sampled at 10% (`tracesSampleRate: 0.1`) to
  stay free-tier friendly.
- The `Sentry.metrics.distribution` calls are lightweight metric values sent
  per page load; they are not transaction-sampled, so they give 100% coverage
  of the five vitals even when tracing sampling skips a load.

## Sourcemap upload (@sentry/vite-plugin)

Production stack traces stay readable instead of minified garbage. The
`@sentry/vite-plugin` uploads build sourcemaps to Sentry during `npm run build`
and deletes them from `dist/**/*.map` afterwards, so the shipped bundle never
exposes readable source to browsers.

### How it's gated

- The plugin is only added when **`SENTRY_AUTH_TOKEN`** is set (and only for
  real builds, never `vite dev`). Token-less local builds and CI jobs skip it
  entirely — no missing-credential failures.
- `build.sourcemap: true` is always on; with a token the maps are uploaded
  then removed, without a token they simply remain in `dist/`.
- The release name comes from `VITE_RELEASE_VERSION` (defaults to `dev`) and
  is injected into the bundle, so runtime events and uploaded maps always
  associate.

### Set it up (deploy platform / CI secrets)

```bash
SENTRY_ORG=stratoscan
SENTRY_PROJECT=stratoscan-frontend
SENTRY_AUTH_TOKEN=sntrys_...   # Sentry → Settings → Auth Tokens → Create New
                               # Token — scopes: project:releases, project:write
VITE_RELEASE_VERSION=2026.08.1 # optional; else 'dev'
```

The DSN (`VITE_SENTRY_DSN`) is a public client identifier; the auth token
(`SENTRY_AUTH_TOKEN`) is a **secret** — keep it out of the client bundle and
repo. Both are documented in `.env.example`.

### Verifying the upload

1. Build with the token set: `SENTRY_AUTH_TOKEN=... npm run build`.
2. Confirm Sentry CLI logs "Source Maps uploaded" for each chunk (or check
   `Settings → Source Maps` / the release in Sentry).
3. Throw a test error with the DSN active, then confirm the event's stack
   trace shows original `src/...` paths with line numbers, not minified names.

## Authentication & silent refresh

The app uses JWT auth (`Authorization: Bearer <token>`, 7-day TTL). To avoid a
mid-session forced logout when the token nears expiry, the frontend silently
refreshes it via **`POST /auth/refresh`** (see `stratoscan/FRONTEND_API.md`
§5 for the contract):

- **Proactive timer** (`src/store/auth.ts`): 60s before expiry, exchanges the
  token; success → session continues, timer re-arms for the next cycle;
  failure → clean logout + redirect to `/login`.
- **Request interceptor** (`src/lib/api/client.ts`): any request made within
  60s of expiry refreshes first, so the outbound call always carries a live
  token.
- **401 response interceptor**: on a 401 from a live session, refreshes once
  and retries the original request; if refresh fails (endpoint missing,
  network down, revoked token) it degrades to logout — identical to the
  pre-refresh behavior, so the frontend is safe to ship before the backend
  endpoint exists.
- **Single-flight**: concurrent refresh triggers coalesce into one request;
  a logout that races an in-flight refresh never resurrects the session.
- **Bootstrap**: a persisted session whose deadline already passed is
  resurrected with one refresh attempt before `/auth/me` revalidates.
- **Multi-tab**: rotation in one tab propagates to all others via the
  localStorage `storage` event.

Demo mode never refreshes (fake tokens don't expire).

## Reduced motion & accessibility

The app honors the user's **`prefers-reduced-motion`** setting on every
animated surface, using two complementary layers:

1. **Global CSS freeze** (`src/index.css`): an `@media (prefers-reduced-motion:
   reduce)` block pins `animation-duration: 0.01ms` and
   `animation-iteration-count: 1` on all elements, which freezes every
   **CSS-driven** animation — marquees, spinners, pulses, shimmer, the
   caret blink — in one place.
2. **`useReducedMotion` hook** (`src/hooks/useReducedMotion.ts`) for
   **JS-driven** animation: a reactive `matchMedia` subscription (reacts to
   live OS/browser toggles, not just initial load) that gated components read
   to skip their animation entirely:

   | Component | Behavior under reduced motion |
   | --- | --- |
   | `CountUp` | Jumps straight to the final number (no rAF count-up) |
   | `TickerTape` | Renders a single static row (no marquee loop / doubled copy) |
   | `HeroViz`, `TerminalHero`, `BacktestShowcase`, `BacktestLoop` | Motion off via their `REDUCED` gate |

**Rule of thumb for new animations:** pure CSS → the global block already
covers it; anything state/timer/rAF-driven → read `useReducedMotion()` and
render the non-animated state when it's `true`. The one deliberate exception
is the landing search typewriter — it's a character-by-character *text reveal*
(the text doesn't move), so it's kept even under reduced motion, consistent
with the documented intent in `Landing.tsx`.

## Development

```bash
npm install
npm run dev        # http://localhost:5173, proxies /api → http://localhost:8000
npm test           # vitest (unit + component tests)
npm run typecheck  # tsc --noEmit
npm run lint       # eslint
npm run build      # typecheck + production build
```

### Environment

See `.env.example` for all variables. Key ones:

| Variable | Purpose |
| --- | --- |
| `VITE_API_URL` | Absolute backend URL (prod). Unset in dev → Vite proxy used. |
| `VITE_SITE_URL` | Public origin for canonical/OG URLs. Defaults to `https://stratoscan.com`. |
| `VITE_SENTRY_DSN` | Enables Sentry error + performance monitoring. |
| `VITE_DEMO_MODE` | `true` serves synthetic data — **build fails in prod if set**. |

## SEO

- **Static head** (`index.html`): OG/Twitter tags, canonical, robots meta,
  theme-color — rendered for every crawl of `/`.
- **Per-route metadata** (`src/components/Seo.tsx` + `src/lib/seo.ts`): a
  dependency-free Helmet stand-in updates `document.title`, description,
  OG/Twitter, canonical, and robots on every route change. Public pages are
  `index, follow`; protected `/app/*` routes are `noindex, nofollow`.
- **`public/robots.txt`**: allows all crawlers, points to the sitemap.
- **`public/sitemap.xml`**: all public pages with `lastmod`/`changefreq`/
  `priority`.
- **Share image**: `public/og-image.png` (1200×630). Regenerate the branded
  placeholder with `node scripts/generate-og-image.mjs` (zero deps) or replace
  it with a designed asset.
- Set `VITE_SITE_URL` to the real deployed origin so canonical and OG URLs
  point at the production domain.

### Quality gates

- `verify_prod_build.mjs` — builds and asserts the prod bundle is demo-free.
- `verify_behavior.mjs` / `verify_live.mjs` — headless browser behavioral checks.
- CI (`.github/workflows/frontend-ci.yml`) runs typecheck → lint → test → build
  on every push.

## Vercel deployment

`.github/workflows/frontend-deploy.yml` deploys automatically:

- **Production**: every push to `main` → gate (typecheck/lint/format/test/build)
  → `amondnet/vercel-action` with `--prod` → healthcheck.
- **Preview**: every pull request → ephemeral preview deploy with a comment
  linking the URL.
- **Env propagation**: Vercel rebuilds the app **server-side** on deploy, so the
  workflow forwards the production vars to that build via
  `--build-env VITE_*=…` (backend URL, site URL, Sentry DSN, release SHA,
  demo-mode off). The gate job also builds locally with the same values, so the
  gated artifact and the deployed artifact are configured identically.

### One-time Vercel project setup

1. **Import the repo** at vercel.com → New Project → pick the GitHub repo.
   Framework preset: **Vite** (auto-detected). Root directory: **`frontend`**.
2. **Grab the IDs** (Settings → General):
   - Team/org id → `VERCEL_ORG_ID`
   - Project id → `VERCEL_PROJECT_ID`
3. **Create an access token** (Account → Settings → Tokens) → `VERCEL_TOKEN`.
4. **Add GitHub secrets** (Settings → Secrets and variables → Actions):

   | Secret | Value |
   | --- | --- |
   | `VERCEL_TOKEN` | Vercel access token |
   | `VERCEL_ORG_ID` | Team id from step 2 |
   | `VERCEL_PROJECT_ID` | Project id from step 2 |
   | `VITE_API_URL` | `https://stratoscan-api.fly.dev` (backend origin) |
   | `VITE_SITE_URL` | `https://stratoscan.com` (public origin) |
   | `VITE_SENTRY_DSN` | Sentry client DSN |
   | `SENTRY_AUTH_TOKEN` | Sentry token (releases + write) — enables sourcemap upload on main builds |
   | `SENTRY_ORG` / `SENTRY_PROJECT` | Sentry slugs |
   | `VERCEL_PRODUCTION_URL` | Optional — used by the healthcheck instead of the deployment URL |

5. **Production domain**: add your custom domain (Project → Settings → Domains)
   and set `VITE_SITE_URL` to match.

> **Why env vars live in two places**: the *local* gate build reads the GitHub
> secrets above; the *Vercel* build reads `--build-env` flags forwarded by the
> workflow. Both are fed from the same secrets, so there is one source of
> truth. (If you ever deploy from the Vercel dashboard instead of CI, set the
> same `VITE_*` vars in Vercel Project → Settings → Environment Variables.)

## Security headers & CSP

Production responses carry a strict Content-Security-Policy plus hardening
headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
`Permissions-Policy`, HSTS). They're defined in **`public/_headers`** — the
single source of truth — and Vercel applies them to every route, including
deep links that rewrite to `index.html` (`vercel.json` carries only the SPA
rewrite).

- `script-src 'self'` with **no** `'unsafe-inline'` (the bundle is 100%
  external scripts); `style-src` allows inline for Framer Motion's runtime
  keyframes.
- `connect-src` allows the backend origin and Sentry ingest only.

Full policy rationale, the apply/verify flow (curl + DevTools + CSP
Evaluator), and a maintenance checklist live in **[DEPLOYMENT.md](./DEPLOYMENT.md)**.
