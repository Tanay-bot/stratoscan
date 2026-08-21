# Stratoscan — Scheduler Watchdog & Operations

This document describes how the background data-refresh scheduler runs, how a
silently-dead scheduler is detected, and how the system recovers. Read this
before touching the scheduler in production.

> **One correction up front:** the scheduler is **NOT** a thread inside the API
> process. It is its own long-lived process (`python -m src.main scheduler`).
> The container entrypoint (`docker-entrypoint.sh`) starts **only** uvicorn —
> nothing in the API process runs the scheduled jobs. Treat them as two
> separate processes that happen to share the same environment.

---

## 1. Architecture

```
┌──────────────────────────────┐        ┌──────────────────────────────┐
│  API process (uvicorn)       │        │  Scheduler process           │
│  python -m src.main serve    │        │  python -m src.main scheduler│
│  └─ FastAPI /screen /backtest│        │  └─ DataRefreshScheduler     │
│  └─ /ops/jobs (read-only)    │        │     ├─ price_refresh  (15m)  │
│  └─ heartbeat staleness view │        │     ├─ technicals    (60m)   │
└──────────────────────────────┘        │     ├─ fundamentals  (02:00) │
                                        │     ├─ news_refresh (N min)  │
                                        │     └─ cache warmer  (03:30) │
                                        └──────────────┬───────────────┘
                                                       │ JobMonitor
                                                       ▼
                                        ┌──────────────────────────────┐
                                        │  Neon — job_runs table        │
                                        │  Neon — scheduler_heartbeat   │
                                        └──────────────────────────────┘
```

- **Scheduler process** (`src/scheduler.py`): runs the recurring jobs
  (price refresh every 15 min, technicals every 4th cycle, fundamentals daily
  at 02:00, news+sentiment every `NEWS_REFRESH_MINUTES`, backtest cache
  warmer daily at `BACKTEST_CACHE_WARMER_TIME`).
- **JobMonitor** (`src/job_monitor.py`): every job run is recorded to the

- **Data-source limitation — `volume` stays NULL (verified live 2026-08-06):**
  this Finnhub plan's `/quote` response omits the `v` field entirely (keys are
  `c d dp h l o pc t` — no `v`), so `fetch_stock_quote` maps it to None for
  every symbol. The candles endpoint that *does* carry volume returns 403
  (paid tier), and `avgVolume` from `/stock/metric` is also None on this plan.
  **This is not a code bug** — the fetcher maps the field correctly; the
  source simply never sends it. Volume-based screener filters will match
  nothing until a plan/data source that provides volume is configured. The
  one populated row (`A` = 123456) is stale placeholder data from an early
  fill — treat it as noise. Technicals (`sma_50`/`rsi_14`/`high_52w`) are
  unaffected: they come from `/technical_indicator` and populate normally.
  `job_runs` table (status, duration, record count, error); the scheduler
  writes a throttled liveness heartbeat to `scheduler_heartbeat`.
- **API surface**: `GET /ops/jobs` (admin-only) shows every job's latest run,
  consecutive failures, **staleness flag**, and scheduler liveness — so an
  operator can *see* a dead scheduler without ever looking at logs.
- **Neon auto-suspend**: DB pools are closed after every cycle so Neon can
  suspend between refreshes (free-tier compute). The heartbeat is persisted
  at most every `JOB_HEARTBEAT_PERSIST_MINUTES` (default 15) for the same
  reason.

---

## 2. How a dead scheduler is detected (the heartbeat)

The scheduler's main loop ticks every 30 seconds and calls
`JobMonitor.heartbeat()`. The store persists the beat at most every
`JOB_HEARTBEAT_PERSIST_MINUTES` (15 min) — a single-row upsert on
`scheduler_heartbeat` (`id=1`, `last_beat_at`, `pid`).

**Liveness rule** (`HEARTBEAT_STALE_MULTIPLIER = 3`): a scheduler is judged
**not alive** when the last heartbeat is older than
`JOB_HEARTBEAT_PERSIST_MINUTES × 3` = **45 minutes**. This is deliberate
headroom: with a 15-min persist cadence, a healthy scheduler never looks dead,
and a crashed one is flagged within ~45 minutes — inside a single
price-refresh cycle's worth of slack.

**Staleness rule** (per job): a scheduled job whose last successful run
finished more than `interval × 3` ago is flagged `stale`. Examples:

| Job | Interval | Stale after |
|---|---|---|
| price_refresh | 15 min | 45 min |
| technical_refresh | 60 min | 3 h |
| fundamental_refresh | 24 h | 72 h |
| news_refresh | `NEWS_REFRESH_MINUTES` | 3× that |

---

## 3. How the alert fires

Two independent alert paths — both end at `src/alerts.py`, both fire-and-forget
(daemon thread, never blocks the job), both throttled per key
(`ALERT_WEBHOOK_MIN_INTERVAL_SECONDS`, default 900 s). Delivery channels
(`src/alerts.py`, wired in P1-8): `ALERT_WEBHOOK_URL` (Slack/Discord/ntfy,
preferred when set) or `ALERT_EMAIL` via the existing SendGrid client when no
webhook is configured. With **neither channel configured, all of this is a
no-op** (dev-friendly).

### 3a. Scheduler-health alert (dead / stale scheduler)

Every 10 minutes the scheduler loop calls `_alert_if_stale()`:

```python
summary = self._monitor.summary()          # reads job_runs + heartbeat
stale_jobs = [j for j in summary["jobs"] if j["stale"]]
scheduler_alive = summary["scheduler"]["alive"]
if stale_jobs or not scheduler_alive:
    send_alert("Scheduler health alert",
               message=f"stale jobs: {...} | scheduler alive: {scheduler_alive}",
               level="error", throttle_key="scheduler-health")
```

- Title: `Scheduler health alert`
- Level: `error` (red attachment)
- Throttle key: `scheduler-health` → at most one per 15 min per webhook
- This path is in `src/scheduler.py::DataRefreshScheduler._alert_if_stale`.

### 3b. Job-failure alert (a specific job failed)

`JobMonitor.run()` records every run; a run that ends `failed` triggers
`_alert_failed`:

```python
send_alert(f"Background job failed: {job_name}",
           message=error_or_details[:1000],
           level="error", throttle_key=f"job:{job_name}")
```

- Title: `Background job failed: <job_name>`
- Throttle key: `job:<job_name>` → a job failing every 15 min pages once, not 96×/day

### 3c. The payload

`src/alerts.py` POSTs a Slack-format incoming-webhook JSON (`text`, `title`,
`level`, timestamp, `attachments`) plus a generic `{text,title,level}` body —
accepted by Slack, Discord, and ntfy. Unreachable webhook → logged WARNING,
never a raise, never a crash.

---

## 4. Crash recovery (the watchdog)

There are **two layers** of recovery, and they cover different failures:

### Layer 1 — Fly machine restart (process crash)

Fly runs the container with `auto_stop_machines = "off"` (keeps the machine
running 24/7) and a `/health` HTTP check every 30 s. If the **API process**
crashes, Fly restarts the machine — the entrypoint re-runs migrations and
brings uvicorn back. That covers the API.

> ⚠️ **The scheduler is NOT covered by this today.** Because the scheduler is
> a separate process that the entrypoint does not start, a machine restart
> brings back the API but **not** the scheduler. The heartbeat alert (3a) is
> what tells you it happened; restarting the scheduler is a manual step (4.3)
> until the Fly `[processes]` supervision in section 5 is adopted.

### Layer 2 — Heartbeat alert (silent death)

A crash *outside* the machine (OOM kill of just the scheduler, a hung loop, a
`kill` command, a deploy that forgets the scheduler) produces **no** HTTP
failure — without the heartbeat you would never know. The 45-minute liveness
rule + `Scheduler health alert` is the safety net that pages the operator.

### Layer 3 — Alert on missed jobs (degradation)

Even while the scheduler process is alive, an individual job that keeps
failing fires `Background job failed: <job>` (3b). Jobs that never complete
push their `stale` flag, which also feeds the health alert.

---

## 5. Restart & recovery procedures

### 5.1 Manual restart of the scheduler

```bash
# On the host where the scheduler runs (or via Fly machine exec):
pkill -f "src.main scheduler" || true
nohup python3 -m src.main scheduler >> /var/log/stratoscan-scheduler.log 2>&1 &
```

On Fly (if you run the scheduler on a separate Fly machine):

```bash
flyctl machines list -a stratoscan-scheduler   # find the machine id
flyctl machines restart <machine-id> -a stratoscan-scheduler
```

If the scheduler runs as a second process on the API machine, restarting the
machine itself restarts whatever its init runs:

```bash
flyctl machines list -a stratoscan-api
flyctl machines restart <machine-id> -a stratoscan-api
```

### 5.2 Recommended hardening — let Fly supervise the scheduler

The clean fix (so machine restart covers the scheduler automatically) is a
`[processes]` group in `fly.toml` — two processes, one machine, one
restart-on-crash:

```toml
[processes]
  api = "python -m src.main serve --no-reload"
  scheduler = "python -m src.main scheduler"
```

Then `fly deploy` boots both and Fly restarts **both** on crash. **Do not**
scale to more than one machine (duplicate scheduler = duplicate jobs).

> ⚠️ **Migration trap.** The `[processes]` block replaces the container CMD,
> so `docker-entrypoint.sh` (which runs `alembic upgrade head` before
> uvicorn) would **not** run for the `api` process — every future deploy
> would silently skip migrations. If you adopt `[processes]`, keep the
> entrypoint as the `api` command (e.g. `api = "./docker-entrypoint.sh"`)
> or run `alembic upgrade head` explicitly as a release step before the
> deploy finishes (the deploy workflow's healthcheck gate would catch a
> broken schema, but that's the hard way to learn).

### 5.3 How to confirm recovery

```bash
# 1. Scheduler process is alive:
pgrep -f "src.main scheduler" && echo ALIVE

# 2. The heartbeat is fresh (via the API — admin token required):
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \
     https://stratoscan-api.fly.dev/ops/jobs | python3 -m json.tool
#    → "scheduler": { "alive": true, "heartbeat_age_seconds": < 2700 }
#    → every scheduled job shows "stale": false

# 3. No new alerts: the webhook received no "Scheduler health alert"
#    for ~15+ minutes after restart (throttle window).
```

---

## 6. Config reference

| Env var | Default | Meaning |
|---|---|---|
| `ALERT_WEBHOOK_URL` | empty | Slack/Discord/ntfy incoming webhook. Empty = alerting fully off |
| `ALERT_WEBHOOK_MIN_INTERVAL_SECONDS` | 900 | min seconds between alerts per throttle key |
| `JOB_MONITOR_ENABLED` | true | master switch for job_runs + heartbeat |
| `JOB_HEARTBEAT_PERSIST_MINUTES` | 15 | heartbeat persist cadence (liveness judged at 3×) |
| `NEWS_REFRESH_MINUTES` | — | news job interval (drives its staleness threshold) |
| `BACKTEST_CACHE_WARMER_ENABLED` | true | cache-warmer master switch |
| `BACKTEST_CACHE_WARMER_TIME` | 03:30 | daily cache-warm time |

---

## 7. Verification

Two automated checks prove the watchdog works:

- **Hermetic** — `python -m tests.test_scheduler_watchdog` (no network, no DB):
  asserts `_alert_if_stale()` fires exactly when a job is stale or the
  scheduler is dead, stays silent when healthy, no-ops without a webhook, and
  that a failed job fires `Background job failed: <job>`.
- **Live probe** — `python verify_scheduler_watchdog.py` (runs under
  `run_probes.py`, part of the release gate): boots a local webhook sink,
  simulates a scheduler crash (heartbeat aged past the 45-min liveness rule),
  and asserts the **real** `Scheduler health alert` POST arrives at the sink;
  then simulates restart (fresh heartbeat) and asserts silence. Exit 0 = the
  watchdog works end to end.
