# 010 — Day 4: see what users do, find out when it breaks, and the legal floor

## Goal

The plan's Day 4: *"You can see what users do, you find out when something breaks, and the
site is legally coherent for a product that touches investment decisions."*

Nothing of this exists today: no event tracking anywhere, errors go only to logs, and no
backtest result carries a disclaimer. The first cohort of users teaches its lesson exactly
once — this has to be in place before anyone arrives.

**Three parts, three separate commits** on one branch, in this order. Each part is
reviewable on its own; do not mix them.

---

## Part A — Events

### What to build

**Table `events`** (main DB). Alembic revision `f4a5b6c7d8e9_create_events.py`,
`down_revision = "e3f4a5b6c7d8"`, plus the same DDL as an `EVENTS_SCHEMA` constant wired
into `PostgresDB.init_schema()` (both, not either — as every previous table):

```sql
CREATE TABLE IF NOT EXISTS events (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    anon_id     TEXT,
    user_id     BIGINT REFERENCES users(id) ON DELETE SET NULL,
    props       JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_events_name_time ON events (name, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_user ON events (user_id);
CREATE INDEX IF NOT EXISTS idx_events_anon ON events (anon_id);
```

`ON DELETE SET NULL` is deliberate: deleting an account (Day 3) anonymises its events
instead of breaking the funnel counts.

**Module `src/analytics.py`** — the one entry point. Every event anywhere goes through
`track()`, so swapping in a hosted tool later is a one-function change.

```python
EVENTS: dict[str, frozenset[str]]   # event name -> the ONLY prop keys it may carry
CLIENT_EVENTS: frozenset[str]       # the subset the browser may send

def track(name: str, *, user_id: int | None = None, anon_id: str | None = None,
          props: dict | None = None) -> None:
    """Record one event. Never raises; a failure is logged and swallowed."""
```

`track()` must:
- drop unknown event names (log a warning, write nothing);
- keep only the prop keys whitelisted for that event, and only `int`/`float`/`bool`/short
  `str` values (≤ 64 chars) — anything else is dropped;
- accept `anon_id` only if it is a UUID (`uuid.UUID(anon_id)` parses) — otherwise store NULL;
- be a no-op when `settings.analytics_enabled` is False (new setting, default `True`,
  documented in `.env.example` and `.env.local.example` — golden rule #4);
- never raise into the caller, whatever happens (wrap the whole body).

### The fifteen events — exact names, where each fires, allowed props

| Event | Fired by | Props (whitelist) |
|---|---|---|
| `landing_view` | browser, Landing mount | — |
| `query_submitted` | browser, Screener + Backtest submit | `surface` (`"screener"`\|`"backtest"`) |
| `interpret_deterministic` | server, `/screen` + `/backtest` | `confidence` |
| `interpret_llm` | server, same | — |
| `screen_returned` | server, `/screen` | `result_count`, `total_matches` |
| `backtest_started` | server, `/backtest` + `/backtest/async` start | `symbols`, `mode` (`"sync"`\|`"async"`) |
| `backtest_completed` | server, on success | `symbols`, `mode` |
| `backtest_failed` | server, on error | `mode`, `reason` (a short fixed code, never exception text) |
| `backtest_duration` | server, alongside completed | `duration_ms`, `mode` |
| `template_used` | browser, a Screener query chip or example | `template` (the chip's id/index, not its text) |
| `result_shared` | browser, the Backtest "copy link" (`Backtest.tsx` ~line 1015) | — |
| `signup_started` | browser, `/signup` mount and the Google button click | `method` (`"password"`\|`"google"`) |
| `signup_completed` | server, `/auth/register` and a Google *created* account | `method` |
| `second_backtest` | server, **derived** — see below | — |
| `returned_day_2` / `returned_day_7` | server, **derived** — see below | — |

(`returned_day_2` and `returned_day_7` are two events; the plan counts them as one row.)

**⚠️ No query text, email, symbol list, or exception message in any prop, ever.** Query
text is user content and may be personal. That is why `query_submitted` carries only the
surface, and `backtest_failed` a fixed reason code. The whitelist in `EVENTS` is what
enforces this — a test must prove a disallowed key is dropped.

**Interpreter source.** To know which of `interpret_deterministic`/`interpret_llm` to fire,
add one field to `InterpretedQuery` (`src/interpreter.py:157`): `source: str = ""`, set to
`"deterministic"` or `"llm"` where each path returns. A cache hit keeps the source the entry
was created with. Nothing else in the interpreter changes. **Do not touch
`src/backtester.py`** — golden rule #6.

**Derived events** — fired inside `track()` after a successful insert, at most once per
identity (identity = `user_id` if set, else `anon_id`; skip if neither):
- `second_backtest`: when a `backtest_completed` insert makes that identity's count exactly 2.
- `returned_day_2`: on any event, if the identity's first event was on the previous UTC
  calendar day and no `returned_day_2` exists for it yet.
- `returned_day_7`: same, when the first event was 6, 7 or 8 UTC days earlier.

**Browser → server: `POST /events`** — public, body `{name, props}`.
- `name` must be in `CLIENT_EVENTS`; a server-only name (e.g. `backtest_completed`) → **400**.
  Otherwise a script could forge the funnel.
- Reads `X-Anon-Id` header for `anon_id`, and the Bearer token if present for `user_id`
  (optional: use `verify_access_token`, never 401 if missing or bad — just no user_id).
- Always returns `204` for an allowed name, even if the write failed.
- Rate limit: add it to the **`read`** scope by default — do not add a new scope.

**Frontend.**
- `lib/analytics.ts`: `getAnonId()` (a `crypto.randomUUID()` stored in localStorage
  `stratoscan_anon_id`, created once), and `track(name, props?)` which POSTs to `/events`
  and **never throws or awaits in the UI path** (fire-and-forget, errors swallowed). No-op
  in demo mode.
- `client.ts`: the request interceptor adds `X-Anon-Id: getAnonId()` to every API call, so
  server events link to pre-signup activity. Add `X-Anon-Id` to the backend's CORS
  `allow_headers` if it is not already allowed — check, don't assume.
- Wire the five browser events at the call sites in the table.

**Admin read-out.** `GET /admin/events?limit=100` (`require_admin`) → the latest events,
newest first: `{name, occurred_at, anon_id, user_id, props}`. This is how the owner "walks
the funnel and sees all fifteen land in order".

---

## Part B — Errors

**Table `error_log`** (same revision as `events`, same init_schema wiring):
`id BIGSERIAL, occurred_at TIMESTAMPTZ DEFAULT NOW(), method TEXT, route TEXT,
error_type TEXT, trace TEXT`.

**Capture in `generic_exception_handler`** (`api.py` ~line 4412): after logging, write one
row — `route` is the route *template* (`request.scope.get("route").path` when present, e.g.
`/backtest/async/{job_id}`), never the raw URL (it can carry tokens in a query string).
`trace` is `traceback.format_exception(...)` joined, then **scrubbed** by a pure function
`scrub(text) -> str` in `src/analytics.py`:
- email addresses → `<email>`
- anything JWT-shaped (`eyJ…\.…\.…`) → `<jwt>`
- `Bearer <anything>` → `Bearer <redacted>`
- truncated to 8,000 chars

Never store request bodies or headers. The client still gets the same generic
`{error, detail, status_code}` envelope — **no change to what the client sees** (golden
rule #12). A failure to write the row must not change the response either.

`GET /admin/errors?limit=50` (`require_admin`) → latest rows.

---

## Part C — The legal floor (frontend only)

- **`components/legal/RiskDisclaimer.tsx`**: one short block, same visual weight as the
  metrics next to it: *"Simulated results on historical data, including fees and slippage.
  Past performance does not predict future results. This is not investment advice."* with a
  link to `/risk`.
- Place it **inside the backtest result block** in `Backtest.tsx` — next to the KPI cards,
  not in a footer. The plan is explicit: *"The disclaimer belongs next to the number, in the
  same visual block, not in 8pt grey at the bottom of the page."* It will also go on the
  Day 6 share page; build it as a reusable component for that reason.
- **`pages/Risk.tsx`** at `/risk`, built on the existing `components/legal/LegalPage.tsx`
  like Terms and Privacy. Plain English: what a backtest is and is not, survivorship and
  look-ahead limits, data can be wrong or stale, no advice, no guarantee.
- Footer (`components/landing/Footer.tsx`): add **Risk Disclaimer** next to Terms/Privacy.
- **Privacy page**: add a section *"What we store and how to delete it"* — account email and
  password hash; saved watchlist/screens/backtests; anonymous usage events (no query text);
  how to delete: **Settings → Delete account**, which removes the account and its data and
  anonymises its events. Make it true to Parts A and B.
- No cookie banner: there is no third-party analytics tool. The Privacy section above states
  the one local identifier (`stratoscan_anon_id`) plainly.

---

## Files

Part A: `stratoscan/src/analytics.py` (new), `src/interpreter.py` (one field + two
assignments), `src/api.py`, `src/database.py`, `src/config.py`, `.env.example`,
`.env.local.example`, alembic `f4a5b6c7d8e9_create_events.py` (new),
`tests/fakes.py`, `tests/test_analytics.py` (new), `frontend/src/lib/analytics.ts` (new),
`frontend/src/lib/analytics.test.ts` (new), `lib/api/client.ts`, and the five call-site pages.
Part B: `src/api.py`, `src/analytics.py`, `tests/test_error_capture.py` (new).
Part C: `frontend/src/components/legal/RiskDisclaimer.tsx` (new), `pages/Risk.tsx` (new),
`pages/Backtest.tsx`, `pages/Privacy.tsx`, `components/landing/Footer.tsx`, `App.tsx`, tests.
All parts: `test_api_smoke.py` route pin, `openapi.json`/`openapi.pretty.json` (regenerate),
`FRONTEND_API.md`, `CLAUDE.md` route table.

## Non-goals

- **No third-party analytics or error tool** (no PostHog, GA, Sentry backend SDK). No new
  dependency in `requirements*.txt` or `package.json`.
- Do not touch `src/backtester.py`, rate-limit numbers, or any auth logic.
- No dashboards or charts — the admin endpoints are enough.
- No reformatting or drive-by cleanup.

## Baseline

Measure (see `knowledge.md` §2). At the time of writing: **46/46** backend, **222/222** frontend.

## Verify

Hermetic tests — each rejection paired with the acceptance next to it:

- `track()` with an unknown name writes nothing; a known name writes one row.
- A disallowed prop key (e.g. `query`, `email`) is **dropped**, an allowed one kept.
- A non-UUID `anon_id` is stored as NULL; a UUID is kept.
- `track()` raising inside (patch the DB to raise) → **no exception reaches the caller**.
- `POST /events` with a client event → 204 + row; with `backtest_completed` → **400** + no row.
- `/screen` with the fake interpreter fires exactly one `interpret_*` and one `screen_returned`.
- `second_backtest` fires on the 2nd `backtest_completed` and **not** on the 1st or 3rd.
- `returned_day_2` fires once when the first event was yesterday, and not when it was today.
- `analytics_enabled=False` → nothing written.
- Error capture: a route that raises `ValueError("user alice@x.com token eyJa.b.c")` →
  client gets the generic 500 envelope **and** one `error_log` row whose `trace` contains the
  error type but **not** `alice@x.com` and **not** `eyJa.b.c`.
- `scrub()` unit cases for email, JWT, Bearer, and truncation.
- Frontend: `track()` never throws when the POST fails; `getAnonId()` is stable across calls;
  `RiskDisclaimer` renders inside the backtest result; `/risk` route renders.

**Observe it fail** (the standing rule): remove the prop whitelist → the PII test must fail;
remove `scrub()` from the capture → the error-capture test must fail. Paste both failures.

Full gate:

```bash
cd stratoscan && export JWT_SECRET=ci-dummy-jwt-secret
for t in tests/test_*.py; do python -m "tests.$(basename "$t" .py)" >/dev/null 2>&1 || echo "FAIL $t"; done
ruff check .
python -c "from src.config import validate_env_template as v; print(v() or 'complete')"
git diff --exit-code requirements.txt requirements-lock.txt && echo "deps unchanged"
cd ../frontend && npm run typecheck && npm run lint && npm run format:check && npx vitest run && npm run build
```

Then live: `make rebuild`, open the site as a brand-new visitor, go landing → signup →
screen → backtest → copy share link → backtest again, then `GET /admin/events` with an admin
token and **paste the event names in order** in your report.

## Done

Push branch `task/010-day4-instrumentation-and-legal` with three commits (A, B, C).
**Do not merge.** Claude reviews it first — particularly the prop whitelist, the client-event
allowlist on `POST /events`, and `scrub()`.
