# Stratoscan — Release Gate

> **Owner:** this file is the source of truth for the release gate and the probe inventory — the probe table here is the canonical list.
> `run_probes.py` defines what actually runs, and `tests/test_docs_contract.py` fails if the table below drifts from it. Staging setup → `STAGING.md`. Drill detail → `FAILURE_DRILLS.md`.

The release gate is the **last line of defense before any public push**: it
runs all 18 behavior probes against the isolated staging environment and
**blocks deployment if any probe fails**.

It complements (not replaces) the hermetic CI suite: CI (`.github/workflows/ci.yml`)
lints and runs the fast, fake-everything `tests/test_*.py` modules on every push. The gate
runs the slow, real-environment probes (backtest accuracy, trade P&L,
load/throughput, LLM-router lifecycle, screener lifecycle, Neon lifecycle,
screen-cache, security) that CI deliberately cannot run.

---

## How to run it

```bash
cd stratoscan
scripts/run_release_gate.sh                # all 18 probes
scripts/run_release_gate.sh --report /tmp/gate.md   # + markdown report
scripts/run_release_gate.sh --only verify_throughput.py   # one probe
```

Exit code: **0** = all probes passed → deploy allowed. **1** = at least one
probe failed → **deploy is blocked**. **2** = usage/environment error.

### Prerequisites

- **Local dev:** `staging.env` in the repo root, with `STAGING_DATABASE_URL`
  pointing at the **`stratoscan_probes`** database (create + seed it with
  `seed_probes_db.py` — see `STAGING.md`). The runner loads `staging.env` and
  forces `DATABASE_URL` to the probes DB so no probe can ever touch
  production or the E2E staging DB.
- **CI (GitHub Actions):** the same vars injected as secrets; `staging.env`
  is optional (the runner falls back to the process environment).
- `verify_screen_cache_live.py` hardcodes port **8099** — the gate refuses to
  run (rather than silently mis-test) if something is squatting on it.

## The 18 probes

| # | Probe | What it verifies |
|---|---|---|
| 1 | `verify_audit_behavior.py` | Admin audit trail records auth/screen/backtest events |
| 2 | `verify_backtest_accuracy.py` | Backtest P&L math matches hand-computed expected values |
| 3 | `verify_backtest_edge_paths.py` | Backtester handles degenerate inputs (no data, empty, single bar) |
| 4 | `verify_backtest_neon_lifecycle.py` | Full Neon backtest DB lifecycle (schema, write, read, clean) |
| 5 | `verify_data_accuracy.py` | Stored prices/RSI/SMA/52w vs yfinance on the same as-of date (25 symbols, documented tolerances) |
| 6 | `verify_derived_fix.py` | Derived fields (RSI/SMA/52w/volume) populate from raw data |
| 7 | `verify_failure_drills.py` | Failure injection: all Finnhub keys dead, all LLM providers down, yfinance timeout, Upstash down — system degrades gracefully (retries/clean 4xx/in-memory rate limit), never a 500 |
| 8 | `verify_full_backend_behavior.py` | End-to-end API flow: auth → screen → backtest → news |
| 9 | `verify_llm_router_lifecycle.py` | LLM router failover, caching, provider rotation |
| 10 | `verify_load_behavior.py` | Real rate-limit buckets (429s, headers) under load |
| 11 | `verify_screen_cache_live.py` | Live server screen-cache hit/miss behavior (port 8099) |
| 12 | `verify_screener_lifecycle.py` | Screener universe refresh → screen → enrich lifecycle |
| 13 | `verify_scheduler_watchdog.py` | Scheduler watchdog: healthy silent, crash → webhook alert, recovery silent, failed-job alert (real HTTP sink) |
| 14 | `verify_security_findings.py` | Known-vulnerability patterns absent from the codebase |
| 15 | `verify_security_fixes_behavior.py` | Security fixes behave correctly at runtime |
| 16 | `verify_throughput.py` | p99 latency + no 502s under a ~1,200-request bench |
| 17 | `verify_trade_pnl.py` | Per-trade P&L, fees, and portfolio equity are exact |
| 18 | `verify_upstash_ratelimit.py` | Upstash/Redis rate-limit backend path |

## Report format

With `--report`, the gate writes a self-contained markdown file:

```markdown
# Stratoscan — Release Gate Report

- **Ran:** 2026-08-06 14:00 UTC
- **Environment:** staging (isolated stratoscan_probes DB)
- **Result:** ✅ PASS          (or ❌ FAIL (2/18))
- **Probes:** 16/18 passed

| Probe | Exit |
|---|---|
| verify_audit_behavior.py | 0 |
| ... | ... |

## Failures            ← only present when something failed

### verify_throughput.py (exit 1)
``` (tail of output) ```
```

The console prints the same summary plus the tail of each failing probe's
output, so a red gate tells you *what* to fix, not just *that* it failed.

## How it isolates staging

1. Load `staging.env` (override) → LLM keys, Finnhub keys, CORS, timeouts all
   resolve to staging resources.
2. Force `DATABASE_URL = STAGING_DATABASE_URL` (the probes DB) → any probe
   that boots a server or opens `src.config` touches **only**
   `stratoscan_probes`.
3. Blank Upstash/Redis creds + `REDIS_RATE_LIMIT_ENABLED=false` → no probe can
   reach the shared Redis.
4. `verify_throughput` gets its own freshly-booted uvicorn (rate limiter ON,
   in-memory store) so its 429/header assertions are meaningful and isolated.

**One intentional exception:** `verify_backtest_neon_lifecycle.py` drives the
shared Neon **backtest** DB (staging reuses prod's backtest project). It is
self-cleaning by design (probe-only symbols, snapshot-and-restore counts), so
no real backtest data is at risk.

## Wiring into deployment

`.github/workflows/deploy.yml` has an optional **release-gate** job that runs
before `deploy`:

- Triggered by `workflow_dispatch` input `run_release_gate: true` **or** a
  repository variable `RUN_RELEASE_GATE=true`.
- Needs the staging secrets (`STAGING_DATABASE_URL`, `SCREENER_LLM_KEY`, …).
- If the gate fails, the `deploy` job is skipped → **no release**.
- If the gate is not enabled, `deploy` runs as before (gate skipped, not failed).

## When to run it

- **Always** before a push to production (R2 in the release checklist).
- After any change to the screener, backtester, news pipeline, LLM router,
  auth, or rate limiting.
- After any migration that touches stock_master / backtest / news schema.
- After any **PITR restore** (P1-4 drill) — and quarterly as a scheduled drill.
- On demand: `workflow_dispatch` with `run_release_gate=true`.

## Migration dry-run (P1-6) — separate gate step

Distinct from the 18 probes: before any push, run `alembic upgrade head`
against a **copy of the production database** to prove migrations are safe
before the container entrypoint runs them against the real DB at boot.

```bash
cd stratoscan
/usr/local/bin/python3.14 scripts/migration_dryrun.py   # exit 0 = safe to push
```

Full docs (copy mechanics, safety invariants, known prod state) live in
`STAGING.md` → "Migration dry-run on a copy of production (P1-6)". The
dry-run is **not** one of the 18 probes because it needs a faithful copy of
the **production** schema — the probes DB is intentionally throwaway/abused.
It is a release-blocking step in its own right: if `upgrade head` fails or
changes any pre-existing table's data on the copy, do not push.

## PITR restore drill (P1-4) — separate gate step

Before any push, prove that a **restored copy of the primary database**
serves real screens and backtests — the drill that would catch a bad
restore at the exact moment you need it (after an incident).

```bash
cd stratoscan
/usr/local/bin/python3.14 scripts/pitr_restore_drill.py           # clone-equivalent (no console)
/usr/local/bin/python3.14 scripts/pitr_restore_drill.py \
    --scratch-url postgresql://.../stratoscan_pitr?sslmode=require  # your real restore
```

Exit 0 = the restored DB serves real screens + a hand-verified AAPL equity
curve and stayed read-only. The real Neon Console restore is a human/API
step (documented in `STAGING.md` → "PITR restore drill") — the drill's
default mode performs the restore-equivalent clone and verifies every
downstream guarantee. Run it after any restore, and quarterly as a drill.
