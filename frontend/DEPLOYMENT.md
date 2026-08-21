# Stratoscan Frontend — Deployment & Security Headers

This document covers how the frontend is deployed to **Vercel** and how the
**Content-Security-Policy (CSP)** and hardening headers are applied and kept
correct.

---

## 1. Deployment model

- **Host**: Vercel (production + PR previews).
- **CI**: `.github/workflows/frontend-deploy.yml` gates (typecheck → lint →
  format → test → build) then deploys with `amondnet/vercel-action` (`--prod`
  on `main`, ephemeral previews on PRs).
- **Build output**: Vite builds into `dist/`; Vercel serves it.
- **Backend origin**: `https://stratoscan-api.fly.dev` (Fly.io). The Vite dev
  proxy (`/api → localhost:8000`) is dev-only; production talks cross-origin
  with `VITE_API_URL` and CORS handled by the backend.

### How the SPA gets deep links

`vercel.json` rewrites every non-`/api` route to `/index.html` so client-side
routes like `/pricing` or `/app/screener` work on refresh instead of 404ing.
Static assets (`/assets/*`, `/og-image.png`, `/robots.txt`, `/sitemap.xml`)
are served by Vercel's filesystem before the rewrite is considered.

---

## 2. Security headers (applied to every route)

All security headers live in **`public/_headers`** (Vercel's `_headers` file
format, copied into `dist/` by Vite). `vercel.json` intentionally carries
**only** the SPA rewrite — duplicating headers there would create a second
source of truth that can silently drift.

| Header | Value | Why |
| --- | --- | --- |
| `Content-Security-Policy` | (see §3) | Restrict what the page may load/execute. |
| `X-Content-Type-Options` | `nosniff` | Prevent MIME sniffing (e.g. serving HTML as JS). |
| `X-Frame-Options` | `DENY` | No framing anywhere (defense-in-depth with `frame-ancestors 'none'`). |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | Full URL to same-origin, origin-only to cross-origin, nothing on downgrade. |
| `Permissions-Policy` | `geolocation=(), microphone=(), camera=()` | Disable the three high-value sensor permissions the app never uses. |
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains; preload` | Force HTTPS for 2 years (add the domain to the HSTS preload list once live). |

> **CSP alternative**: the header is the right vehicle (it applies to all
> responses including `/index.html` itself, and supports `frame-ancestors`,
> which a `<meta http-equiv>` CSP cannot). Do **not** add a meta CSP.

---

## 3. The Content-Security-Policy, directive by directive

```
default-src 'self';
script-src 'self';
style-src 'self' 'unsafe-inline';
img-src 'self' data: https:;
font-src 'self' data:;
connect-src 'self' https://api.stratoscan.com https://stratoscan-api.fly.dev https://*.sentry.io;
object-src 'none';
base-uri 'self';
form-action 'self';
frame-ancestors 'none';
worker-src 'self';
upgrade-insecure-requests
```

| Directive | Value | Rationale |
| --- | --- | --- |
| `default-src` | `'self'` | Fallback for every not-listed source type. |
| `script-src` | `'self'` | **No `'unsafe-inline'`, no `'unsafe-eval'`.** The production build ships only external bundled scripts (`index.html` has no inline `<script>`). If you ever add an inline script (e.g. a third-party widget), switch to a hash/nonce instead of loosening this. |
| `style-src` | `'self' 'unsafe-inline'` | `'unsafe-inline'` is required: Framer Motion injects `<style>` keyframe rules at runtime. Tailwind compiles to an external stylesheet (covered by `'self'`). |
| `img-src` | `'self' data: https:` | Local + generated OG image + data-URIs (chart favicons/sparkline fallbacks) + external news thumbnails. |
| `font-src` | `'self' data:` | Fonts are bundled locally (`@fontsource-variable/inter`, `@fontsource/jetbrains-mono`) — no Google Fonts, no third-party CDN. `data:` allows any inlined font-face. |
| `connect-src` | `'self' https://api.stratoscan.com https://stratoscan-api.fly.dev https://*.sentry.io` | API calls (Fly.io origin + canonical domain) and Sentry ingest. **If the backend origin ever changes, update this directive — otherwise all API calls get blocked.** |
| `object-src` | `'none'` | No plugins/embeds. |
| `base-uri` | `'self'` | Block `<base>` hijacking. |
| `form-action` | `'self'` | Forms only submit same-origin. |
| `frame-ancestors` | `'none'` | Cannot be framed (pairs with `X-Frame-Options: DENY`). |
| `worker-src` | `'self'` | Web workers only from own origin. |
| `upgrade-insecure-requests` | — | Browsers rewrite any `http://` subresource to `https://`. |

### Why these are the exact values

- **No `'unsafe-inline'` in `script-src`** — the single biggest XSS mitigation.
  The bundle is 100% external scripts; there is nothing inline to permit.
- **`style-src 'unsafe-inline'` is a deliberate, required exception** — Framer
  Motion's runtime style injection would be blocked without it. This is a
  common, accepted trade-off (styles alone can't exfiltrate data).
- **Vercel/static hosting means no HMR CSP concerns** — `vite dev` HMR uses
  inline scripts + websockets, but the CSP header is only served by Vercel on
  the production/preview deployments, never by the dev server. Dev is
  unaffected.

---

## 4. How the headers reach production

1. `frontend/public/_headers` is copied verbatim into `dist/_headers` by Vite
   on `npm run build`.
2. The deploy workflow uploads `dist/` (root directory `frontend`) to Vercel.
3. Vercel applies the header rules to every matching route — the `/*` rule
   covers both top-level pages and the SPA-rewritten deep links.
4. SPA fallback: unmatched client routes rewrite to `/index.html` (from
   `vercel.json`), which is served with the same headers via the `/*` rule.

---

## 5. Verifying the headers

### After deploy — `curl` (go-live acceptance check)

> This is a **post-deploy verification**: it can only be run once the workflow
> has deployed the frontend to Vercel. It's the acceptance check for the
> "headers are applied" criterion — do not consider this task closed until a
> real deploy returns all six headers.

```bash
curl -sI https://<your-deployment-url>/ | grep -iE 'content-security|x-content|x-frame|referrer|permissions|strict-transport'
```

Every header from §2 must appear.

### In the browser

1. Open the deployed site → DevTools → **Network** → click the document
   request → **Headers** tab → scroll to **Response Headers**.
2. Confirm `Content-Security-Policy` and the rest are present.
3. Watch the **Console** for CSP violation reports while exercising the app:
   the screener, backtester, news feed (external images), and a Sentry
   test-error. A clean console = the policy allows everything the app needs.
4. Google's **CSP Evaluator** (`https://csp-evaluator.withgoogle.com`) is a
   good sanity check that the policy is meaningful (no `'unsafe-inline'`
   scripts, etc.).

### Local — static preview

`npm run build && npm run preview` serves `dist/` but does **not** apply
`_headers` (that's a Vercel feature), so headers will be absent locally. Use
the deployed URL or a local proxy that reads `_headers` to verify.

---

## 6. Maintenance checklist

- **Backend origin change** → update `connect-src` in `public/_headers`.
  Everything else can stay.
- **Adding a third-party script** (analytics, chat widget) → add the origin to
  `script-src` (or better: load it as a module from your own bundle) and add
  its connect origins to `connect-src`. Never reach for `'unsafe-inline'`.
- **Adding Google Fonts** → move `fonts.googleapis.com` / `fonts.gstatic.com`
  into `font-src` (prefer keeping fonts bundled as they are now).
- **HSTS preload** → once the apex domain is confirmed https-only, submit it
  at `https://hstspreload.org/` and keep `Strict-Transport-Security` present
  (it already is, via both files).
- **CSP reporting** → if you later want visibility into violations, add
  `report-uri`/`report-to` pointing at Sentry or a reporting endpoint, and
  keep the policy otherwise unchanged.
