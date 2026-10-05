"""Day 15 — exactly one scheduler (golden rule 1), enforced, not hoped for.

The dress rehearsal started a second scheduler next to a running one and both
ran every job — the failure that doubles cost while looking normal. Now:

  1. the lease semantics (in-memory store mirrors the Postgres SQL): free →
     granted; held and fresh by someone else → refused; stale → granted;
     same owner (a restarted container) → granted; force → granted;
  2. a refused scheduler raises before running a single job, and alerts;
  3. a scheduler whose lease was taken over stops at its next heartbeat;
  4. a clean stop releases the lease so a replacement starts at once;
  5. an unreachable store never blocks the scheduler (monitoring never
     breaks a job) but is reported as unverified;
  6. the CLI exits 3 on a refusal.

Run: python -m tests.test_scheduler_lease
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("JWT_SECRET", "lease-dummy-secret")

from unittest import mock  # noqa: E402

import src.scheduler as sched  # noqa: E402
from src.job_monitor import InMemoryJobMonitorStore, JobMonitor, scheduler_owner  # noqa: E402

ME = scheduler_owner()
OTHER = "other-container:1"

# ── 1. Lease semantics ──────────────────────────────────────────────────────
st = InMemoryJobMonitorStore()
assert st.claim_scheduler(ME, 2700)["claimed"]                      # free → mine
assert st.claim_scheduler(ME, 2700)["claimed"]                      # mine again (restart)
r = st.claim_scheduler(OTHER, 2700)
assert not r["claimed"] and r["holder"] == ME and r["age_seconds"] < 5, r  # fresh → refused
st._heartbeat["last_beat_at"] = time.time() - 3000                  # holder silent 50 min
assert st.claim_scheduler(OTHER, 2700)["claimed"]                   # stale → taken
assert not st.claim_scheduler(ME, 2700)["claimed"]                  # and now I'm refused
assert st.claim_scheduler(ME, 2700, force=True)["claimed"]          # unless forced
st.release_scheduler(OTHER)                                         # not the holder: no-op
assert st.last_heartbeat()["owner"] == ME
st.release_scheduler(ME)
assert st.last_heartbeat()["owner"] is None
assert st.claim_scheduler(OTHER, 2700)["claimed"]                   # released → free at once
print("✅ lease: free/own/stale/forced → granted; fresh and someone else's → refused; release frees it")


def make(store=None, **kw):
    defaults = {"pool": mock.Mock(), "db": mock.Mock(), "news_db": mock.Mock(),
                "monitor": JobMonitor(store or InMemoryJobMonitorStore())}
    defaults.update(kw)
    return sched.DataRefreshScheduler(**defaults)


# ── 2. A second scheduler is refused before any job runs ────────────────────
held = InMemoryJobMonitorStore()
held.claim_scheduler(OTHER, 2700)
s = make(held)
with mock.patch.object(sched, "refresh_price_data") as m_refresh, \
     mock.patch("src.alerts.send_alert") as m_alert:
    try:
        s.start()
        raise AssertionError("a second scheduler must be refused")
    except sched.SchedulerAlreadyRunning as e:
        assert OTHER in str(e), e
assert not m_refresh.called and not s._running
assert m_alert.call_args.args[0] == "Second scheduler refused", m_alert.call_args
assert held.last_heartbeat()["owner"] == OTHER  # the refusal took nothing
print("✅ a second scheduler raises before running a job, alerts, and leaves the lease alone")

# --takeover / SCHEDULER_TAKEOVER force it.
s = make(held)
with mock.patch.object(sched, "fetch_us_stock_symbols", return_value=["AAPL"]), \
     mock.patch.object(sched, "refresh_price_data", return_value=[]), \
     mock.patch.object(sched.schedule, "every", return_value=mock.Mock()), \
     mock.patch.object(sched.schedule, "run_pending"), \
     mock.patch.object(sched.time, "sleep", side_effect=KeyboardInterrupt):
    s.start(takeover=True)
assert held.last_heartbeat()["owner"] is None  # took it, ran, released on the clean stop
print("✅ takeover forces the lease; a clean stop releases it")

# ── 3. Taken over mid-run → the loser stops at its next heartbeat ──────────
st3 = InMemoryJobMonitorStore()
s = make(st3)
ticks = []


def sleep_then_steal(_):
    ticks.append(1)
    st3._heartbeat["owner"] = OTHER  # someone forced a takeover
    if len(ticks) > 3:
        raise AssertionError("the loser kept running")


with mock.patch.object(sched, "fetch_us_stock_symbols", return_value=["AAPL"]), \
     mock.patch.object(sched, "refresh_price_data", return_value=[]), \
     mock.patch.object(sched.schedule, "every", return_value=mock.Mock()), \
     mock.patch.object(sched.schedule, "run_pending"), \
     mock.patch.object(sched.time, "sleep", side_effect=sleep_then_steal):
    s.start()
assert not s._running and len(ticks) == 1, ticks
assert st3.last_heartbeat()["owner"] == OTHER  # the loser's release did not touch it
print("✅ a scheduler whose lease was taken over stops at the next heartbeat")

# A stop that lands during the initial refresh still releases the lease.
st4 = InMemoryJobMonitorStore()
s = make(st4)
with mock.patch.object(sched, "fetch_us_stock_symbols", return_value=["AAPL"]), \
     mock.patch.object(sched, "refresh_price_data", side_effect=KeyboardInterrupt), \
     mock.patch.object(sched.schedule, "every", return_value=mock.Mock()):
    s.start()
assert st4.last_heartbeat()["owner"] is None and not s._running
print("✅ a stop during the initial refresh still releases the lease")

# ── 4/5. An unreachable store never blocks the scheduler ───────────────────
broken = InMemoryJobMonitorStore()
broken.claim_scheduler = mock.Mock(side_effect=RuntimeError("db down"))
lease = JobMonitor(broken).claim_scheduler()
assert lease["claimed"] and lease["unverified"], lease
print("✅ store unreachable → the scheduler starts, marked unverified")

# ── 6. The CLI exits 3 on a refusal ─────────────────────────────────────────
from src import main as cli  # noqa: E402

with mock.patch.object(cli, "DataRefreshScheduler") as m_cls:
    m_cls.return_value.start.side_effect = sched.SchedulerAlreadyRunning("held by x")
    try:
        cli.cmd_scheduler(mock.Mock(takeover=False))
        raise AssertionError("expected exit 3")
    except SystemExit as e:
        assert e.code == 3, e.code
print("✅ CLI: a refused scheduler exits 3 (a supervisor sees it)")

print("\nALL SCHEDULER-LEASE CHECKS PASSED")
