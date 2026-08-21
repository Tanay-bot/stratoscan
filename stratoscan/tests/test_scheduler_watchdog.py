"""Hermetic tests for the scheduler watchdog alert path (src/scheduler.py
_alert_if_stale + src/job_monitor.py _alert_failed) — no network, no live DB.

Run: python -m tests.test_scheduler_watchdog

Covers the "watchdog" contract (P0-6):
1. _alert_if_stale: no webhook configured -> no-op (send_alert never called).
2. _alert_if_stale: fully healthy scheduler (all jobs fresh + fresh heartbeat)
   -> NO alert fires.
3. _alert_if_stale: one stale job (last success older than interval x3)
   -> exactly one "Scheduler health alert" naming the job, level error.
4. _alert_if_stale: dead scheduler (heartbeat older than persist x3, all jobs
   fresh) -> exactly one alert reporting "alive: False".
5. _alert_if_stale: stale job + dead scheduler together -> still one dispatch
   (throttling is the sink's job).
6. _alert_if_stale: store outage degrades to a WARNING and the alert FIRES
   (fail-safe: when monitoring can't confirm health, the operator is paged),
   and it never raises into the scheduler loop.
7. JobMonitor.run: a FAILED job fires "Background job failed: <job>" with the
   error surfaced and the correct throttle key.
8. JobMonitor.run: success / skipped runs fire NO alert.

The real JobMonitor + real InMemoryJobMonitorStore + real DataRefreshScheduler
are used throughout (only send_alert and the clock are mocked), so the wiring
under test is the actual production wiring.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Hermetic: no webhook configured by default, no rate limiter, no Redis,
# and no email-alert channel (ALERT_EMAIL must be blank too — otherwise
# alerts_enabled would be True via the email channel and the "no channel
# -> no-op" assertions would flip).
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["REDIS_RATE_LIMIT_ENABLED"] = "false"
os.environ["ALERT_WEBHOOK_URL"] = ""
os.environ["ALERT_EMAIL"] = ""
os.environ["SENDGRID_API_KEY"] = ""
os.environ["EMAIL_FROM_ADDRESS"] = ""

from unittest import mock  # noqa: E402

from src.job_monitor import (  # noqa: E402
    InMemoryJobMonitorStore,
    JobMonitor,
    JOB_DEFINITIONS,
    settings as _jm_settings,  # shared singleton, used by _alert_failed
)
from src.scheduler import DataRefreshScheduler  # noqa: E402
from src.config import settings as sched_settings  # noqa: E402  (shared singleton)

# Deterministic clock. IMPORTANT: every staleness/liveness decision inside
# _alert_if_stale() flows through JobMonitor.summary(), which reads
# time.time() from ITS module — so the clock patch must target
# src.job_monitor.time.time, and every seeded timestamp must be expressed in
# the same epoch.
NOW = 1_800_000_000.0

results = []
ok = True


def check(name, cond, detail=""):
    global ok
    results.append((name, cond, detail))
    if not cond:
        ok = False


def _make_scheduler(monitor=None):
    """Real scheduler with fake pool/db/news-db and a REAL in-memory monitor."""
    return DataRefreshScheduler(
        pool=mock.Mock(),
        db=mock.Mock(),
        news_db=mock.Mock(),
        monitor=monitor or JobMonitor(InMemoryJobMonitorStore()),
    )


# Every scheduled job (the 5 with an interval). manual_refresh and
# historical_backfill are on-demand and never stale by clock time.
_SCHEDULED = [
    n for n, d in JOB_DEFINITIONS.items() if d.get("interval_seconds") is not None
]


def _seed_all_fresh(monitor):
    """Record a fresh success for every scheduled job (60s before NOW)."""
    for job in _SCHEDULED:
        with monitor.run(job) as run:
            run.records = 10
            run.details = "seeded"
        rows = [r for r in monitor._store._runs if r["job_name"] == job]
        rows[-1]["started_at"] = NOW - 120
        rows[-1]["finished_at"] = NOW - 60


def _age_run(monitor, job_name, finished_ago_s):
    """Backdate a job's most recent run to `finished_ago_s` before NOW."""
    rows = [r for r in monitor._store._runs if r["job_name"] == job_name]
    assert rows, f"no {job_name} runs to age"
    rows[-1]["started_at"] = NOW - finished_ago_s - 5
    rows[-1]["finished_at"] = NOW - finished_ago_s


def _fresh_heartbeat(monitor):
    monitor._store.record_heartbeat()
    monitor._store._heartbeat["last_beat_at"] = NOW - 60


def _aged_heartbeat(monitor, age_s):
    monitor._store.record_heartbeat()
    monitor._store._heartbeat["last_beat_at"] = NOW - age_s


def _alert(scheduler, webhook="https://hooks.example.com/x"):
    """Run the real _alert_if_stale with a fixed clock + webhook; return the
    send_alert mock."""
    with mock.patch("src.job_monitor.time.time", return_value=NOW), \
         mock.patch("src.alerts.send_alert") as m_alert, \
         mock.patch.object(sched_settings, "alert_webhook_url", webhook):
        scheduler._alert_if_stale()
    return m_alert


# ── 1. No webhook -> no-op ─────────────────────────────────────────────────
print("1. no webhook configured -> no-op")
m = JobMonitor(InMemoryJobMonitorStore())
s = _make_scheduler(m)
with mock.patch("src.job_monitor.time.time", return_value=NOW), \
     mock.patch("src.alerts.send_alert") as m_alert, \
     mock.patch.object(sched_settings, "alert_webhook_url", ""), \
     mock.patch.object(sched_settings, "alert_email", ""):
    s._alert_if_stale()
check("no channel: send_alert never called", not m_alert.called)

# ── 2. Fully healthy -> silent ─────────────────────────────────────────────
print("2. healthy scheduler -> no alert")
m = JobMonitor(InMemoryJobMonitorStore())
_seed_all_fresh(m)
_fresh_heartbeat(m)
s = _make_scheduler(m)
m_alert = _alert(s)
check("healthy: send_alert NOT called", not m_alert.called)

# ── 3. Stale job -> exactly one alert naming the job ───────────────────────
print("3. stale job -> Scheduler health alert")
m = JobMonitor(InMemoryJobMonitorStore())
_seed_all_fresh(m)
_age_run(m, "price_refresh", finished_ago_s=46 * 60)  # > 45 min threshold
_fresh_heartbeat(m)  # scheduler itself alive
s = _make_scheduler(m)
m_alert = _alert(s)
check("stale: send_alert called once", m_alert.call_count == 1)
if m_alert.called:
    args = m_alert.call_args.args
    kwargs = m_alert.call_args.kwargs
    check("stale: title 'Scheduler health alert'", "Scheduler health alert" in args)
    check("stale: level=error", kwargs.get("level") == "error")
    check("stale: throttle_key scheduler-health", kwargs.get("throttle_key") == "scheduler-health")
    detail = kwargs.get("message") or args[1]
    check("stale: detail names the stale job", "price_refresh" in detail)
    check("stale: scheduler reported alive", "alive: True" in detail)

# ── 4. Dead scheduler (all jobs fresh) -> alert ────────────────────────────
print("4. dead scheduler (heartbeat aged) -> alert")
m = JobMonitor(InMemoryJobMonitorStore())
_seed_all_fresh(m)
_aged_heartbeat(m, 46 * 60)  # > 45 min liveness rule
s = _make_scheduler(m)
m_alert = _alert(s)
check("dead: send_alert called once", m_alert.call_count == 1)
if m_alert.called:
    kwargs = m_alert.call_args.kwargs
    detail = kwargs.get("message") or m_alert.call_args.args[1]
    check("dead: detail reports scheduler not alive", "alive: False" in detail)

# ── 5. Stale + dead together -> still one dispatch ─────────────────────────
print("5. stale job AND dead scheduler -> one alert")
m = JobMonitor(InMemoryJobMonitorStore())
_seed_all_fresh(m)
_age_run(m, "news_refresh", finished_ago_s=46 * 60)
_aged_heartbeat(m, 46 * 60)
s = _make_scheduler(m)
m_alert = _alert(s)
check("stale+dead: single dispatch", m_alert.call_count == 1)

# ── 6. Store outage -> fail-safe alert, never raises ───────────────────────
print("6. store outage -> warning + fail-safe alert, no raise")
class BrokenStore:
    def recent_runs(self, job_name, limit=50):
        raise RuntimeError("db down")

    def last_heartbeat(self):
        raise RuntimeError("db down")

    def record_heartbeat(self):
        raise RuntimeError("db down")

    def record_start(self, job_name):
        raise RuntimeError("db down")

    def record_finish(self, *a, **k):
        raise RuntimeError("db down")

    def close(self):
        raise RuntimeError("db down")


m = JobMonitor(BrokenStore())
s = _make_scheduler(m)
with mock.patch("src.job_monitor.time.time", return_value=NOW), \
     mock.patch("src.alerts.send_alert") as m_alert, \
     mock.patch.object(sched_settings, "alert_webhook_url", "https://hooks.example.com/x"):
    try:
        s._alert_if_stale()  # must NOT raise
        check("store outage: no raise", True)
    except Exception as e:
        check("store outage: no raise", False, str(e))
check("store outage: fail-safe alert dispatched", m_alert.call_count == 1)

# ── 7. Failed job -> Background job failed alert ───────────────────────────
print("7. failed job -> 'Background job failed: <job>'")

m = JobMonitor(InMemoryJobMonitorStore())
with mock.patch("src.alerts.send_alert") as m_alert, \
     mock.patch.object(_jm_settings, "alert_webhook_url", "https://hooks.example.com/x"):
    try:
        with m.run("price_refresh") as run:
            run.details = "quotes only"
            raise RuntimeError("finnhub down")
    except RuntimeError:
        pass
check("failed job: alert dispatched", m_alert.call_count == 1)
if m_alert.called:
    args = m_alert.call_args.args
    kwargs = m_alert.call_args.kwargs
    check("failed job: title names the job",
          any("Background job failed" in str(a) and "price_refresh" in str(a) for a in args))
    check("failed job: error surfaced",
          "finnhub down" in (kwargs.get("message") or ""))
    check("failed job: throttle_key job:price_refresh",
          kwargs.get("throttle_key") == "job:price_refresh")

# ── 8. Success / skipped runs fire no alert ────────────────────────────────
print("8. success + skipped runs -> no alert")
m = JobMonitor(InMemoryJobMonitorStore())
with mock.patch("src.alerts.send_alert") as m_alert, \
     mock.patch.object(_jm_settings, "alert_webhook_url", "https://hooks.example.com/x"):
    with m.run("price_refresh") as run:
        run.records = 10
    with m.run("news_refresh") as run:
        run.status = "skipped"
        run.details = "no symbols"
check("success+skipped: no alerts", not m_alert.called)

# ── Results ────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("RESULTS")
print("=" * 60)
for name, cond, detail in results:
    icon = "✅" if cond else "❌"
    print(f"  {icon}  {name}: {detail}")
print("\n" + "=" * 60)
if ok:
    print("✅  ALL WATCHDOG TESTS PASSED")
else:
    print("❌  SOME WATCHDOG TESTS FAILED")
print("=" * 60)
sys.exit(0 if ok else 1)
