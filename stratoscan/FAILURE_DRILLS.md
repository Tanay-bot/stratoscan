# Stratoscan — Failure-Injection Drills (P1-7)

> **Owner:** this file is the source of truth for the seven failure-injection drills and their expected degradation behavior.
> Probe inventory and gate wiring → `RELEASE_GATE.md`.

Prove the system **degrades gracefully** when upstreams die. Each drill is
repeatable, documents expected outcomes, and is wired into the release gate as
a hermetic probe (`verify_failure_drills.py`, probe #8 of the 19). Drills 5–7 (Day 13)
live in the hermetic suite `tests/test_failure_drills.py`, which every deploy's gate runs.

**Guiding contract:** a dead upstream must produce *retries, logs, and clean
error surfaces* — never a 500, never corrupted data, never an unhandled
exception. If any drill fails, the release gate blocks.

---

## Running the drills

```bash
cd stratoscan
/usr/local/bin/python3.14 verify_failure_drills.py   # drills 1–4, hermetic
python -m tests.test_failure_drills                  # drills 5–7, hermetic
```

The drills are **hermetic**: zero network, zero quota burn. Failures are
injected at the real code boundaries (the retry loop, the LLM router entry
point, the yfinance fetch, the Redis PING) with `unittest.mock` — the pipeline
code executed is the real production code, not a stub.

Exit code: **0** = all drills passed · **1** = at least one drill failed.

---

## Drill 1 — All 10 Finnhub Keys Dead

### Scenario
Every key in the round-robin pool is invalid (e.g. revoked/expired).

### Steps
1. Inject 10 invalid keys into the client pool (`finnhub_keys` property).
2. Assert the pool still initializes (10 clients — key validation is
   deferred to the API call, not the pool).
3. Drive `_api_call_with_retry` with a 429 response → assert **retries
   happen** (exponential backoff; attempt counter grows).
4. Drive it with a hard 401 → assert a **clean `RuntimeError`** surfaces and
   is **not retried pointlessly** (a 401 will never succeed on retry).
5. Run a full `refresh_price_data` pass with dead keys → assert **zero
   records produced** and the **database is untouched** (freshness/rows
   identical before and after).

### Expected outcome
- Retryable statuses (429/5xx/network) are retried with backoff.
- Hard 4xx (401) fails fast with a clean error.
- A dead-key refresh writes nothing and never corrupts existing data.
- Users see the documented error surface — never a 500.

### Verification
```
✅ pool initializes with the 10 dead keys — 10 clients
✅ 429 is retried (attempt counter grew) — 3 attempts (initial + 2 retries)
✅ 401 raises clean RuntimeError (not retried pointlessly) — attempts=1
✅ refresh with dead keys yields zero records (nothing written) — 0 records
✅ DB untouched on total key failure
DRILL 1: PASS ✅
```

---

## Drill 2 — All LLM Providers Down

### Scenario
Every provider key in the LLM router is invalid/expired, or all providers are
unreachable.

### Steps
1. Inject a router whose `chat_completion` always raises.
2. Call `interpret_query("oversold tech stocks")` → assert it **returns an
   `InterpretedQuery` with `.error` set** (never raises).
3. `POST /screen` via the real FastAPI app → assert a **non-500** response
   that surfaces `data_quality.error` ("Could not interpret your query…")
   and `llm_error` in `interpreted_filters`.
4. `POST /backtest` via the real app → assert a **non-500** response with a
   **generic** detail (the raw exception text must never leak).

### Expected outcome
- The interpretation layer degrades to a structured `.error`, never throws.
- `/screen` returns a graceful "could not interpret" payload (not a 500).
- `/backtest` returns a generic 502 "Could not interpret strategy" — no
  exception internals leaked to the client.

### Verification
```
✅ interpret_query degrades to .error (never raises) — LLM call failed: all providers down
✅ /screen is NOT a 500 when all LLM providers are down — status 200
✅ /screen surfaces data_quality.error — Could not interpret your query. Please try rephrasing.
✅ /screen surfaces llm_error in interpreted_filters
✅ /backtest is NOT a 500 when the LLM is down — status 401 (unauth'd) / 502 (auth'd)
✅ /backtest detail is generic (no raw exception text)
DRILL 2: PASS ✅
```

---

## Drill 3 — yfinance Timeout

### Scenario
The yfinance data source times out (network blip, upstream outage).

### Steps
1. Force `yf.Ticker` to raise (timeout) → assert `fetch_historical_data`
   **returns `None`** (documented failure signal, never raises).
2. Call `run_backtest` with a `None` frame → assert a **clean zero-data
   result** (`final_value == 0`, `total_return == -1.0`, empty equity curve)
   — no raise, no NaN crash.
3. Drive the engine's top-N path with a dead data source → assert it returns
   a dict result without raising (per-symbol failures are logged, not fatal).
4. At the HTTP surface this maps to the documented 502 "Backtest failed" —
   never a 500.

### Expected outcome
- Fetch failures are a return value (`None`), not an exception.
- The backtester degrades to a clean empty result instead of NaN-poisoning
  or crashing the whole top-N run.
- Users get a clean error message, not a 500.

### Verification
```
✅ fetch_historical_data returns None on timeout (never raises) — None
✅ run_backtest returns a zero-data result (no raise, no NaN crash) — final=0 ret=-1.0
✅ top-N backtest survives a dead data source (no raise) — dict
DRILL 3: PASS ✅
```

---

## Drill 4 — Upstash (Redis) Down

### Scenario
The Upstash Redis endpoint is unreachable / not answering (network, outage,
account suspension).

### Steps
1. Configure Redis rate limiting ON, but make the Upstash client fail its
   PING → assert `get_rate_limit_store()` **falls back to the in-memory
   store** (fail-open, logged warning).
2. With the in-memory store active, hammer a rate-limited endpoint → assert
   **429s still happen** (the limiter keeps limiting, just less accurately
   across instances).

### Expected outcome
- Redis is **fail-open**: a dead Redis never takes down the API.
- Rate limiting **continues to work** via the in-process store (bounded per
  instance instead of globally accurate).
- The fallback is logged so operators can see it happened.

### Verification
```
✅ Upstash down → falls back to the in-memory store — InMemoryRateLimitStore
✅ in-memory backend still enforces the read burst (429s happen) — 60 x 200, 2 x 429 of 62
DRILL 4: PASS ✅
```

---

## Drill 5 — Mail transport down (Day 13)

### Scenario
SendGrid unreachable (DNS/network/5xx), or the SMTP relay refusing connections.

### Expected outcome
- `POST /auth/register` → 200 with `email_sent: false` (the account exists; the UI offers "resend").
- `POST /auth/password-reset/request` → the **same** 200 body for a known and an unknown
  address — a mail outage must not become an account-existence oracle.
- `POST /auth/resend-verification` → a clean non-500 status, no transport text.
- The weekly digest stops after 3 consecutive send failures (circuit breaker,
  `tests/test_weekly_digest.py`) and retries tomorrow without duplicates.

## Drill 6 — Error capture failing (Day 13)

### Scenario
The `error_log` insert and the analytics `events` insert both raise (database for
telemetry unreachable).

### Expected outcome
- Ordinary requests are unaffected (signup, `/meta` → 200): telemetry never breaks a request.
- A genuine bug still returns the same generic 500 body
  (`{"error": true, "detail": "Internal server error. Please try again later.", "status_code": 500}`)
  — the failed capture adds nothing and leaks nothing.

## Drill 7 — Database unreachable mid-request (Day 13)

### Scenario
The account loads, then the next query fails: Neon waking from suspend, a dropped
socket (`OperationalError`, `InterfaceError`) or every pooled connection busy
(`PoolError`).

### Expected outcome
- **503 + `Retry-After: 10`**, body "The database is briefly unavailable…" — never 500,
  never driver text (`generic_exception_handler` → `_is_db_outage`).
- A failed **account lookup** is 503 too, never a 401: a 401 makes the frontend sign the
  person out, so a database blip used to log everyone out (`auth._fetch_user`, fixed Day 13).
- Once the database is back, the same session works — nobody was logged out.
- A busy pool now **waits** (up to `POOL_CHECKOUT_WAIT_SECONDS`, 15s) for a connection to
  come back instead of failing at once (`database._get_shared_conn`, `tests/test_db_pool.py`);
  the load test found 8 concurrent users enough to exhaust it before.

---

## Release-gate wiring

**Explain, every LLM provider down (Day 11):** `POST /explain` still answers 200
with the deterministic findings and `narrative: null`
(`narrative_status: "unavailable"`); no raw exception text, and nothing is cached
so the next call tries again. Drilled hermetically in `tests/test_explain.py`
(router raising) and live in `verify_explain.py` (which fails if EVERY reading is
unavailable, so a dead provider set cannot pass the gate silently).

`verify_failure_drills.py` is probe **#8** of the 19 probes run by
`run_probes.py` / `scripts/run_release_gate.sh` against the isolated
`stratoscan_probes` staging DB. It is hermetic (no network, no quota burn),
so it runs in every gate pass.

If any drill fails the gate, the deploy is blocked — a system that cannot
prove it degrades gracefully is not ready to face real upstream outages.
