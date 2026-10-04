# Stratoscan API — Frontend Contract

> **Owner:** this file is the source of truth for the HTTP API — endpoints, auth
> levels, pagination, error shapes and units. Architecture and internals →
> `../CLAUDE.md`. Running the service → `README.md`. Rate-limit *design* →
> `../CLAUDE.md` §10 (the budgets themselves are listed in §7 below).

This is the single source of truth for the HTTP API the frontend consumes.
Every JSON endpoint, auth requirement, pagination rule, and error shape lives
here. The backend *also* serves a machine-readable manifest at `GET /meta`
derived from the live routes — if this doc and reality ever disagree,
**the server wins** (re-query `/meta`).

- Base URL (dev): `http://localhost:8000` (or the port from `server.port`)
- Interactive docs: `GET /docs` (Swagger) and `GET /redoc` (ReDoc)
- OpenAPI spec: `GET /openapi.json`

> **Security note:** under `APP_ENV=production` the interactive docs
> (`/docs`, `/redoc`) and the OpenAPI schema (`/openapi.json`) are disabled
> (attack-surface reduction), and the `docs` key in `GET /` and `GET /meta`
> is `{}`. Frontends should treat docs as dev-only tooling.

---

## 1. Auth model

All endpoints are rate-limited per IP (see §7). A small set additionally
require a JWT, sent as `Authorization: Bearer <token>`.

| Auth level | Meaning | Endpoints |
|---|---|---|
| `none` | Public (rate-limited only) | `/screen`, `/screen/export`, `/interpret`, `/enrich`, `/stocks`, `/news`, `/meta`, `/health`, `/config`, `/auth/register`, `/auth/login`, `/auth/verify-email`, `/auth/google/start`, `/auth/google/callback`, `/try/session`, `/r/{share_id}`, `/r/{share_id}.json` |
| `trial` | `X-Anon-Token` from `POST /try/session` (401 without); `/results/*` also accept a Bearer token instead | `/try/backtest`, `/try/backtest/{job_id}`, `/results/share`, `/results/share/{share_id}`, `/results/shares` |
| `bearer` | Valid JWT required (401 without) | `/try/claim` (plus `X-Anon-Token`), `/backtest`, `/backtest/async`, `/backtest/async/{job_id}`, `/backtest/export`, `/auth/me`, `/auth/resend-verification`, `/auth/logout-all`, `/auth/change-password`, `/auth/delete-account`, `/auth/refresh`, `/me/personal` |
| `admin` | JWT + `is_admin=true` (403 otherwise) | `/refresh`, `/ops/*`, `/admin/*` |

**Login/register flow:**
1. `POST /auth/register` or `POST /auth/login` → `{access_token, token_type: "bearer", expires_in, user, email_sent}`.
2. Store the token (e.g. `localStorage`), send it on every authed call.
3. `GET /auth/me` returns the fresh user profile (incl. `is_admin` — use it to
   show/hide the admin UI).
4. Email verification is optional (off by default). When on, `email_verified`
   is `false` until the user clicks the emailed link
   (`GET /auth/verify-email?token=...` or `POST /auth/verify-email`).
   `POST /auth/resend-verification` (bearer) re-sends.

**The `user` object (UserOut):**

```json
{
  "id": 1,
  "email": "me@example.com",
  "plan": "free",
  "email_verified": false,
  "is_admin": false,
  "created_at": "2026-08-03T04:00:00Z"
}
```

> `is_admin` was added so the frontend can gate admin routes from the login
> response itself — no separate admin check call needed.

---

## 2. Errors — one shape everywhere

Every non-2xx JSON response (auth, validation, rate limit, 500) uses the
same envelope:

```json
{ "error": true, "detail": "human-readable message", "status_code": 400 }
```

- **422 validation** errors from FastAPI/Pydantic are NOT rewritten by the
  backend — there is no `RequestValidationError` handler, so they ship as
  FastAPI's default `{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}`
  array. The frontend normalizes that array into the same envelope's `detail`
  message (one handler covers all errors).
- **429 rate limit** adds `retry_after` (seconds) and a `Retry-After` header:
  `{error: true, detail: "...", status_code: 429, retry_after: 53233}`.
- **500** returns a generic message (no stack trace / no internals leaked).

Handle in the frontend as:

```ts
if (!res.ok) {
  const body = await res.json();
  const msg = body.detail ?? `HTTP ${res.status}`;
  if (res.status === 429) showRetry(body.retry_after);
  throw new ApiError(res.status, msg);
}
```

---

## 3. Pagination contract (shared)

All list endpoints share the same `limit`/`offset` semantics:

| Param | Default | Max | Meaning |
|---|---|---|---|
| `limit` | 50 | 500 | Page size |
| `offset` | 0 | 1,000,000 | Rows to skip (page start) |

The screener's page responses include the full contract:

```json
{
  "total_matches": 1234,
  "limit": 50,
  "offset": 0,
  "has_more": true,
  "next_offset": 50
}
```

- `total_matches` = the TRUE total for the query (not just this page).
- `has_more` = another page exists; `next_offset` = the offset to request
  (`offset + limit`), or `null` when done.
- Simple "load more": request `{limit, offset: next_offset}` until
  `has_more` is false.

---

## 4. Core endpoints

### `GET /` — API identity
```json
{
  "name": "Stratoscan API",
  "version": "0.1.0",
  "description": "...",
  "docs": {"/docs": "Swagger UI", "/redoc": "ReDoc"},
  "meta": "/meta",
  "endpoint_count": 33
}
```
Use at boot to confirm the API is reachable and link to docs/meta.

### `GET /meta` — capability manifest (render the whole UI from this)
```json
{
  "name": "Stratoscan API",
  "version": "0.1.0",
  "docs": {"swagger": "/docs", "redoc": "/redoc", "openapi": "/openapi.json"},
  "endpoints": [
    {"path": "/screen", "methods": ["POST"], "summary": "...", "tags": ["Screener"], "auth": "none"}
  ],
  "sortable_columns": ["atr_14", "avg_volume", "...", "score"],
  "filter_fields": [
    {"field": "pe_ratio", "operators": ["gte", "lte"], "type": "number", "unit": "x", "label": "P/E ratio", "query_key": "min_pe_ratio/max_pe_ratio"}
  ],
  "example_queries": ["undervalued small-cap tech stocks with rising momentum", "..."],
  "rate_limits": {"screen": {"burst": 5, "daily": 50, "weekly": 250}, "...": {}},
  "pagination": {"default_limit": 50, "max_limit": 500, "max_offset": 1000000, "fields": {...}},
  "auth": {"jwt_expire_hours": 168, "password_min_length": 8, "email_verification_required": false, "email_verification_enabled": false}
}
```
- `endpoints` is derived from the actual registered routes — it cannot drift.
- Use `filter_fields` to build a filter/query-builder UI (field + operators +
  unit + label) and `sortable_columns` for the sort dropdown.
- Use `example_queries` for "try one of these" presets.
- Use `rate_limits` to surface remaining budget to the user.


`templates[]` (Day 8) is the strategy template library — `{id, title, query,
idea, category, buy_rules, sell_rules, simulates}`. Every query parses on the
free deterministic path, `buy_rules`/`sell_rules` are exactly what the engine
trades, and `simulates` says so in a sentence: show it next to the title so a
template never promises more than the backtest tests. Run one by sending its
`query` to `/try/backtest` or `/backtest/async`.

### `GET /meta/sectors` and `GET /meta/industries` — dropdown reference data
```json
{ "total": 11, "values": [{"name": "Technology", "count": 1902}, {"name": "Finance", "count": 1693}] }
```
`values` is the exact set of sector/industry values the screener filters on,
with live stock counts. Sorted by count descending. `503` when the database
is not configured.

### `POST /screen` — natural-language screener
Request: `{query: string (1-4000), limit?: 50, offset?: 0, sort_by?: "market_cap", sort_desc?: true}`

`sort_by` accepts any of `/meta`'s `sortable_columns`, or `"score"` for the
multi-factor composite. Response (`ScreenResponse`):

```json
{
  "query": "undervalued tech stocks",
  "interpreted_filters": {"sector": "Technology", "pe_ratio": {"operator": "lte", "value": 15}},
  "total_matches": 546,
  "results": [{"symbol": "NVDA", "company_name": "NVIDIA Corp", "current_price": 200.75, "market_cap": 4858149832767, "pe_ratio": 55.2, "rsi_14": 62, "score": 0.83, "score_components": {...}, "why_matched": ["..."], "is_stale": false, "data_age_minutes": 4, "sentiment": {...}}],
  "limit": 50, "offset": 0, "has_more": true, "next_offset": 50,
  "sort_by": "market_cap", "sort_desc": true,
  "applied_filters": ["pe_ratio <= 15", "sector = Technology"],
  "data_quality": {}
}
```
- On an LLM interpretation failure the response is still **200** but
  `interpreted_filters.llm_error` is set, `total_matches` is `0`, and
  `data_quality.error` explains — never a bare 500.
- `market_cap` is in **USD**; `current_price` in USD; yields/margins/growth
  in **percent**.

### `POST /interpret` — debug the NL parser (no DB query)
`{query: string}` → `{raw_input, filters, filter_count, llm_error, display}`.

### `POST /enrich` — screen + news/sentiment
`{query, max_stocks?: 10, limit?: 50}` → `{query, screening: ScreenResponse, enrichment: {SYMBOL: {symbol, buzz, company_news_score, sector_news_score, articles: [{headline, source, datetime, sentiment}]}}, stocks_enriched}`.

### `POST /backtest` — backtest a strategy (bearer)
`{query, top_n?: 5, stop_loss_pct?, take_profit_pct?, trailing_stop_pct?, position_sizing?: "all_in"|"fixed_pct"|"risk_based", position_pct?, risk_per_trade_pct?, allow_short?, max_leverage?, start_date?: "YYYY-MM-DD", end_date?: "YYYY-MM-DD"}`

Response per symbol includes `total_return_pct`, `sharpe_ratio`,
`max_drawdown_pct`, `win_rate_pct`, `total_trades`, `trades[]`, `equity_curve[]`
(`[{date, value}]`), `drawdown_curve[]`, `benchmark_total_return_pct`,
`alpha_pct`, `beta`, `excess_return_pct`, `buy_hold_*`, `monte_carlo`,
`confidence_intervals`, `walk_forward`, `parameter_sensitivity`, `warnings`,
`data_quality[]`.

### `POST /backtest/async` and `GET /backtest/async/{job_id}` — long backtests (bearer)

`POST /backtest/async` takes the same body as `/backtest` and returns
`{job_id, status, phase}` immediately instead of blocking. Poll
`GET /backtest/async/{job_id}` until `status` leaves `queued`/`running`:

```json
{"job_id": "…", "status": "running", "phase": "backtesting",
 "done_symbols": ["AAPL"], "total_symbols": 5, "result": null, "error": null}
```

`status` is one of `queued` → `running` → `done` (with the full `/backtest`
payload in `result`) | `error` | `interrupted`. `interrupted` means the server
restarted mid-job — re-submit. Jobs are persisted (`backtest_jobs`), so a poll
survives a restart.

A failed job's `error` is safe to show: a request problem (4xx) carries its
own sentence; anything unexpected is a generic message — never exception text.

### No signup wall — `/try/*` (trial token)

A visitor without an account gets a small free allowance of real backtests.

1. `POST /try/session` → `{anon_token, expires_in, allowance}` (allowance `3`,
   token valid 30 days). Store it; send it as **`X-Anon-Token`** — never as
   `Authorization`, where it is refused. It is a credential (it owns jobs and
   share links), not the analytics id `X-Anon-Id`.
2. `POST /try/backtest` — same body and response as `/backtest/async`, plus
   `anon_remaining`. `top_n` and `symbols` are capped at 5. Once the allowance
   is spent it answers **403** with a sentence to show next to a signup button.
   Errored runs do not spend the allowance.
3. `GET /try/backtest/{job_id}` — poll, same shape as `/backtest/async/{job_id}`.
   404 for any job this trial session does not own.
4. After signup or login, `POST /try/claim` with **both** `Authorization` and
   `X-Anon-Token` → `{claimed_backtests, claimed_shares}`: the trial's jobs and
   share links move into the account. Then discard the trial token. A 401 here
   means the *trial* token is missing or stale — not that the session expired.

### Share links — `/results/*` and the public `/r/{share_id}`

Nothing is public until its owner asks. Owner = the Bearer account, else the
trial session (a Bearer token that fails validation is a 401, never a silent
fallback to the trial identity).

- `POST /results/share` `{job_id}` → `{share_id, url}` for a finished job you
  own (404 otherwise, 409 while running, 422 when it matched no stocks).
  Sharing the same job again returns the same link. The browser sends only the
  job id: the public numbers are built server-side from the engine's stored
  result by a field whitelist.
- `DELETE /results/share/{share_id}` — revoke; the link is dead for everyone,
  and sharing again mints a new id. 404 for a link you do not own.
- `GET /results/shares` → `{shares: [{share_id, url, query, created_at, headline}]}`.
- `GET /r/{share_id}` — the public page: server-rendered HTML (so link
  previews see real content), `noindex`, a script-free CSP, the risk
  disclaimer next to the numbers, and a "run this yourself" link to
  `{APP_BASE_URL}/try?q=…`. `GET /r/{share_id}.json` is the same summary as
  JSON: `{share_id, query, basis, symbols, symbols_tested, headline{total_return_pct,
  benchmark_total_return_pct, excess_return_pct, sharpe_ratio, max_drawdown_pct,
  win_rate_pct, total_trades}, period{start,end}, equity_curve}` — no email,
  user id, job id, trial id or trade log, ever.
- `GET /r/{share_id}.png` — the 1200×630 preview card the page's `og:image` /
  `twitter:image` point at (`summary_large_image`): query, headline return,
  where SPY ended, the curve, and the disclaimer on the image itself. Sends an
  `ETag` (answer `If-None-Match` with 304) and `Cache-Control: public,
  max-age=3600`; a revoked link's image 404s at once (browsers/CDNs may keep a
  copy for up to that hour).

`url` uses `SHARE_BASE_URL` (the API's public origin) when set, else the
request's own origin. Share ids are 128-bit random; deleting an account
deletes its links.

### `GET /me/personal` and `PUT /me/personal` — per-user saved state (bearer)

`{watchlist: string[], saved_screeners: [...], recent_backtests: [...]}`.
`PUT` replaces the whole document. Backed by the `user_data` table, so a
user's watchlist and saved screeners follow them across devices.

### `POST /backtest/export` — same pipeline, CSV of closed trades (bearer)

### `POST /screen/export` — same pipeline, CSV of results

### `GET /stocks` and `GET /stocks/{symbol}` — browse the universe
`GET /stocks?sector=Technology&limit=50&offset=0` →
`{total_in_database, returned, limit, offset, results: [StockResult]}`.
`GET /stocks/{symbol}` → one `StockResult` plus `sentiment` when available.

### `GET /news` and `GET /news/{symbol}` — news feed + per-stock sentiment
`GET /news?symbol=AAPL&limit=50` → `{total_articles, returned, articles: [{symbol, headline, summary, source, url, published_at, sentiment, sentiment_score}]}`. `503` if the news database is unconfigured.

### `GET /health` — liveness + DB probe
`{status: "ok"|"degraded", version, finnhub_keys_configured, screener_llm_configured, backtest_llm_configured, database_configured, database_alive, database_latency_ms, uptime_seconds}`.

### `GET /config` — service configuration + warnings
`{finnhub_key_count, screener_llm_configured, backtest_llm_configured, database_configured, warnings: []}`.

### `POST /events` — browser analytics (fire-and-forget, no response body)

Body `{name, props?}`; always `204` for an allowed name. The frontend calls this
from `lib/analytics.ts` (`track()`), which never waits for it and swallows every
failure — an event that does not arrive is not the user's problem.

- `name` must be one of the five **client-reportable** events: `landing_view`,
  `query_submitted` (`{surface}`), `template_used` (`{template}`, the chip's id,
  never its text), `result_shared`, `signup_started` (`{method}`). A server-only
  name (`backtest_completed`, `second_backtest`, …) is refused with **`400`** and
  writes nothing: those events are derived from real work, and accepting them
  from a browser would let a script forge the funnel.
- `X-Anon-Id` header (optional) — the browser's `stratoscan_anon_id` UUID. Not a
  UUID → stored as NULL. It is what joins pre-signup activity to the account the
  visitor becomes. Every axios call sends it too (`client.ts` interceptor).
- `Authorization: Bearer …` (optional) — attributes the event to the account.
  **Never 401s**: a missing or malformed token just means no `user_id`.
- Props are filtered server-side against a per-event allowlist. Unknown keys,
  non-scalar values, and strings over 64 characters are dropped. No query text,
  email, symbol list or exception message is ever stored.
- Rate limit: the default `read` scope (no dedicated bucket).

---

## 5. Auth endpoints

| Method | Path | Auth | Body / Query | Returns |
|---|---|---|---|---|
| POST | `/auth/register` | none | `{email, password}` | `AuthResponse` (JWT + user + `email_sent`) |
| POST | `/auth/login` | none | `{email, password}` | `AuthResponse` (JWT + user) |
| GET | `/auth/me` | bearer | — | `UserOut` |
| GET | `/auth/verify-email` | none | `?token=` | `{error: false, verified: true, email, message}` |
| POST | `/auth/verify-email` | none | `{token}` | same as GET |
| POST | `/auth/resend-verification` | bearer | — | `{error: false, email_sent, message}` |
| POST | `/auth/logout-all` | bearer | — | `{error: false, token_version, message}` — the caller's own token dies too |
| POST | `/auth/password-reset/request` | none | `{email}` | `{error: false, message}` — **identical** for unknown addresses |
| POST | `/auth/password-reset/confirm` | none | `{token, new_password}` | `{error: false, message}`; 400 invalid/used/expired, 422 weak password |
| POST | `/auth/change-password` | bearer | `{current_password, new_password}` | `{error: false, access_token, token_type, expires_in, message}` |
| POST | `/auth/delete-account` | bearer | `{password}` | `{error: false, message}` — permanent; wrong password is 400 |
| POST | `/auth/refresh` | bearer | — | `{error: false, access_token, token_type, expires_in}`; 401 once the sign-in is older than `SESSION_MAX_AGE_DAYS` (30) |
| GET | `/auth/google/start` | none | — | **302** to Google's consent screen; sets the signed `g_oauth` state cookie |
| GET | `/auth/google/callback` | none | `?code=&state=` | **302** to `{APP_BASE_URL}/auth/google/complete#token=…&expires_in=…`, or to `/login?oauth_error=<code>` |

Register/login return the same shape: `{access_token, token_type, expires_in, user, email_sent}`.

### Sessions are revocable

Every JWT carries a `ver` claim matched against the account's `token_version`
on each authenticated request. Bumping it — `/auth/logout-all`, a completed
password reset, or a password change — makes every previously issued token
return **401**. The client needs no new code for this: the existing 401
handler (§2) already clears the session. After `/auth/change-password`,
store the returned `access_token`: the old one is dead, including on the
device that made the change.

### Password reset flow

1. `POST /auth/password-reset/request {email}` → always 200 with the same
   message, after the same minimum time, whether or not the account exists.
   Show that message verbatim; never infer anything from it.
2. The email links to `{APP_BASE_URL}/reset-password?token=…` (45-minute,
   single-use; requesting another link cancels the previous one).
3. `POST /auth/password-reset/confirm {token, new_password}` → 200, then send
   the user to `/login`. Every existing session is signed out.

A wrong password on `/auth/change-password` or `/auth/delete-account` is **400, not 401** —
deliberately, so a typo does not trip the 401 auto-logout.

### Continue with Google — `GET /auth/google/start` → `/auth/google/callback`

Not JSON endpoints: both return **302s** for a plain `<a href>`. The link the
frontend renders is `{API_ORIGIN}/auth/google/start` — a real browser
navigation, not an axios call (the 302 to Google and the state cookie have to
be handled by the browser). No endpoint accepts an ID token from the browser;
the code exchange happens server-side.

1. `/auth/google/start` → 302 to `accounts.google.com` with `state` + `nonce`,
   and sets one `HttpOnly`, `SameSite=Lax`, `Path=/auth/google` cookie carrying
   both, HMAC-signed. Unconfigured (`GOOGLE_CLIENT_ID`/`SECRET` blank) → 302 to
   `/login?oauth_error=unavailable`.
2. Google returns to `/auth/google/callback?code=…&state=…`. The cookie's
   signature and `state` are verified first (CSRF); the code is exchanged
   server-side; the ID token's `iss`/`aud`/`exp`/`nonce`/`email_verified` are
   checked; then:
   - the account with that Google `sub` signs in (even if the Google email
     changed since);
   - an **existing verified** account with that email is linked — its password
     keeps working;
   - an **existing unverified** account with that email is **taken over**: the
     password is replaced with an unusable hash, pending tokens are cleared and
     `token_version` is bumped, so whoever registered the address without
     proving the inbox loses both the password and every live session;
   - otherwise an account is created verified, with no usable password
     (a Google-only user who wants one uses "Forgot password").
3. Success redirects to `{APP_BASE_URL}/auth/google/complete#token=<jwt>&expires_in=<seconds>`.
   **The token is in the fragment, never the query** — fragments are not sent
   to servers, so it cannot reach an access log. The page
   (`frontend/src/pages/GoogleComplete.tsx`) stores it, calls `/auth/me`,
   starts the session and immediately strips the fragment from the URL.
4. Every failure redirects to `{APP_BASE_URL}/login?oauth_error=<code>` where
   `<code>` is one of `unavailable`, `state`, `exchange`, `token`, `unverified`,
   `failed`. Map each to a sentence; never show the code itself.

The flow needs `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `GOOGLE_REDIRECT_URI`
(the last must match the Google console entry exactly). The state cookie is
signed with `JWT_SECRET`, so Google sign-in also requires auth to be configured.

### Sliding sessions — `POST /auth/refresh`

The client renews its token while the user is active (tab focus, and a
30-minute heartbeat while visible) once it is within 2 days of its deadline
(`store/auth.ts: refreshIfStale`). A refresh:

- requires a token valid **now**, including `token_version` — it can never
  revive a revoked session;
- keeps the same `ver`, so "log out everywhere" still ends it;
- carries `auth_time` (last password entry) through unchanged, and is refused
  with 401 once that is older than `SESSION_MAX_AGE_DAYS` (default 30). The
  existing token keeps working until its own `exp`; the client ignores the
  refusal (`/auth/refresh` is in the 401 allowlist) and the normal expiry
  path takes over. So one sign-in lasts at most ~37 days, not forever.

---

## 6. Admin endpoints (auth: `admin`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/admin/overview` | Dashboard aggregates: uptime, DB health, stock universe, user stats, job monitor, config |
| GET | `/admin/users?limit&offset&search` | Paginated user list (`{total, limit, offset, users}`) |
| PATCH | `/admin/users/{id}` | Update `plan` / `is_admin` / `email_verified` (partial) |
| GET | `/admin/jobs` | Job-monitor summary |
| GET | `/admin/events?limit=100` | Latest product events, newest first (`{count, events: [{name, occurred_at, anon_id, user_id, props}]}`) — how the funnel is walked |
| GET | `/admin/errors?limit=50` | Latest unhandled errors, newest first (`{count, errors: [{occurred_at, method, route, error_type, trace}]}`) — `route` is the route TEMPLATE, `trace` is scrubbed |
| GET | `/admin` | Self-contained HTML dashboard |

`GET /ops/jobs` and `GET /ops/jobs/{job_name}` are admin-only too (job
telemetry), while `POST /refresh` (admin) fires a full background data refresh.

### What happens on an unhandled error

The client's response is **unchanged** by Day 4: a 500 with the fixed envelope
`{error: true, detail: "Internal server error. Please try again later.",
status_code: 500}`. Raw exception text never leaves the process.

What is new is that `generic_exception_handler` also writes one `error_log` row
(server-side only, readable via `GET /admin/errors`):

- `route` is the route **template** (`/backtest/async/{job_id}`), never the raw
  URL — a query string can carry a token, and a concrete path would give every
  job id its own row;
- `error_type` is the exception class name (e.g. `ValueError`);
- `trace` is `traceback.format_exception(...)` joined and then **scrubbed** by
  `analytics.scrub()`: email addresses → `<email>`, JWT-shaped strings → `<jwt>`,
  `Bearer …` → `Bearer <redacted>`, capped at 8,000 characters (substitutions
  run before the cut, so a secret cannot survive as a half-redacted prefix).

No request bodies and no headers are stored. A failure to write the row also
changes nothing the client sees — the same 500 is returned either way.

---

## 7. Rate limiting

Every request is rate-limited per IP across burst (1 min), daily (24h), and
weekly (rolling 7d) windows. Response headers on every allowed call:

```
ratelimit-limit: 1000
ratelimit-remaining: 999
ratelimit-reset: 1785946800
```

Budgets (per scope, from `/meta`): `screen 5/min · 50/day`,
`interpret 10/min · 100/day`, `enrich 3/min · 20/day`,
`backtest 2/min · 10/day` (starting a job), `backtest_poll 120/min` (`GET /backtest/async/{job_id}` and `GET /try/backtest/{job_id}` — polling never spends the backtest budget),
`anon_backtest 2/min · 6/day` (`POST /try/backtest`; per IP, so a fresh trial token does not reset it; the daily cap sits above the 3-run allowance so the friendly 403 comes first),
`share 10/min · 100/day` (`/results/*`; the public `/r/*` pages are `read`), `refresh 1/min · 10/day`, `read 60/min · 1000/day`,
`auth 10/min · 50/day`, `register 3/min · 10/day`, `auth_mail 3/min · 10/day`
(`/auth/password-reset/*`, `/auth/resend-verification`). Global shared buckets additionally cap usage
across all users (`screener_llm` 900/day, `backtest_llm` 200/day — shared by
`backtest` and `anon_backtest` — and
`auth_register` 50/day, which `register` and `auth_mail` share because both
send email). `OPTIONS` preflights are never counted.

The per-IP default (the `read` bucket — every endpoint not explicitly
scoped above) is env-configurable in production: `RATE_LIMIT_PER_MINUTE`
(default `60`) and `RATE_LIMIT_PER_DAY` (default `1000`; weekly = 5× daily).
The LLM/Finnhub/email-costly scopes (screen, enrich, backtest, refresh, register, auth_mail)
are cost shields and keep their own tighter caps — the two env vars only
move the per-IP default, never the per-scope shields.

A 429 returns the standard error envelope plus `retry_after` (seconds) and
a `Retry-After` HTTP header — treat it as "wait and retry", not a failure:

```json
{"error": true, "detail": "Rate limit exceeded...", "status_code": 429, "retry_after": 53233}
```

---

## 8. CORS

`Access-Control-Allow-Origin` is restricted to the configured origins
(`CORS_ALLOW_ORIGINS` env, comma-separated). Dev defaults cover:

```
http://localhost:5173   http://127.0.0.1:5173    (Vite)
http://localhost:3000   http://127.0.0.1:3000    (Create React App)
```

If the frontend runs on another origin (e.g. a deployed domain), that origin
must be added to `CORS_ALLOW_ORIGINS` or browser requests will be blocked.

---

## 9. Data units (display-critical)

| Field | Unit |
|---|---|
| `market_cap` | USD (whole dollars) |
| `current_price`, `eps_ttm`, `high_52w`, `low_52w`, SMA values | USD |
| `dividend_yield` | percent (e.g. `2.5` = 2.5%) |
| `roe_ttm`, `roa_ttm`, margins, growth, `price_change_pct` | percent |
| `pe_ratio`, `ps_ratio`, `pb_ratio`, `ev_ebitda`, `beta` | dimensionless multiple |
| `rsi_14` | 0–100 index |
| `volume`, `avg_volume` | shares |
| `total_return_pct`, `max_drawdown_pct`, `win_rate_pct`, alpha/beta/excess | percent |
| `market_cap_billions` (query key only) | billions of USD (LLM emits this; server converts to USD) |
