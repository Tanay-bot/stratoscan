"""Hermetic tests for background job monitoring (src/job_monitor.py).

Run: python -m tests.test_job_monitor

Fully hermetic — zero network, zero Neon writes, zero real scheduler runs:

1. InMemoryJobMonitorStore semantics: start → finish (success/failed/skipped),
   heartbeat, history ordering, close.
2. PostgresJobMonitorStore wire format via a mock connection (the SQL the
   store issues against Neon: INSERT running → UPDATE finish, heartbeat
   upsert, recent-runs SELECT). Validates the store without a database.
3. JobMonitor facade: run context manager (success/skip/exception), staleness
   detection per job (fresh vs stale vs stuck vs never-ran), scheduler
   liveness from the heartbeat, and graceful degradation when the store
   raises (monitoring must never break the job).
4. Scheduler integration: a DataRefreshScheduler with fake pool/db/news-db
   records price/technical/fundamental/news runs into an in-memory store,
   marks skipped cycles, and emits a heartbeat.
5. API surface: GET /ops/jobs + GET /ops/jobs/{job_name} through the REAL
   FastAPI app with the store patched to in-memory; unknown job → 404.
6. Migration chain: the new revision is linear and creates job_runs +
   scheduler_heartbeat (static file check, no DB).

No external calls are made anywhere — the Postgres store test swaps in a
fake psycopg2 pool so not even a socket is opened.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Hermetic: keep the API rate limiter OFF and force the in-memory job-monitor
# store so this suite never touches the network or real rate-limit counters.
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["REDIS_RATE_LIMIT_ENABLED"] = "false"

from unittest import mock  # noqa: E402

from src.job_monitor import (  # noqa: E402
    JOB_DEFINITIONS,
    InMemoryJobMonitorStore,
    JobMonitor,
    PostgresJobMonitorStore,
)
from src.scheduler import DataRefreshScheduler  # noqa: E402

NOW = 1_800_000_000.0  # deterministic clock for all tests


# ═══════════════════════════════════════════════════════════════════════════════
# 1. In-memory store semantics
# ═══════════════════════════════════════════════════════════════════════════════
store = InMemoryJobMonitorStore()
rid = store.record_start("price_refresh")
assert rid is not None and isinstance(rid, int)
store.record_finish(rid, "success", duration_ms=1234.5, records=5000, details="quotes only")
runs = store.recent_runs("price_refresh")
assert len(runs) == 1, runs
assert runs[0]["status"] == "success" and runs[0]["records"] == 5000
assert runs[0]["duration_ms"] == 1234.5 and runs[0]["finished_at"] is not None
assert runs[0]["started_at"] is not None and runs[0]["error"] is None
store.record_heartbeat()
hb = store.last_heartbeat()
assert hb and hb["last_beat_at"] is not None and hb["pid"] is not None
assert len(store.recent_runs("news_refresh")) == 0  # per-job isolation
store.close()
print("✅ In-memory store: start/finish/heartbeat/history correct")

# ── 2. Postgres store wire format (fake psycopg2 pool — no network) ───────────
class FakeCursor:
    def __init__(self, rows=None):
        self._rows = rows or []
        self.description = [("id",)] if rows and rows[0] == ["id"] else None
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self):
        self.cursor_calls = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, cursor_factory=None):
        cur = FakeCursor()
        self.cursor_calls.append(cur)
        return cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakePool:
    def __init__(self, conn):
        self._conn = conn
        self.checked_out = 0

    def getconn(self):
        self.checked_out += 1
        return self._conn

    def putconn(self, conn):
        self.checked_out -= 1

    def closeall(self):
        self.checked_out = 0


def _fake_store(conn):
    """PostgresJobMonitorStore with the pool + schema-init swapped out.

    The pool is injected via the CONSTRUCTOR so ``_owns_pool`` is False —
    the store then never auto-closes the fake (each op keeps the same fake
    connection and accumulates cursor calls) and never falls back to a real
    Neon pool.
    """
    store = PostgresJobMonitorStore(pool=FakePool(conn))
    store._schema_ready = True  # skip the real CREATE TABLE
    store._last_heartbeat_persist = 0.0  # allow immediate heartbeat write
    return store


conn = FakeConn()
pg = _fake_store(conn)
pg.record_start("price_refresh")
sql0, params0 = conn.cursor_calls[0].executed[0]
assert "INSERT INTO job_runs" in sql0 and "running" in sql0, sql0
assert params0 == ("price_refresh",), params0

pg.record_finish(1, "failed", duration_ms=50.0, records=0, details="x", error="boom")
sql1, params1 = conn.cursor_calls[1].executed[0]
assert "UPDATE job_runs" in sql1 and "status=%s" in sql1, sql1
assert params1 == ("failed", 50.0, 0, "x", "boom", 1), params1

pg.record_heartbeat()
sql2, params2 = conn.cursor_calls[2].executed[0]
assert "INSERT INTO scheduler_heartbeat" in sql2 and "ON CONFLICT" in sql2, sql2
assert params2 == (os.getpid(), os.getpid()), params2

# Throttle: a second heartbeat within the interval is a NO-OP (no SQL).
conn.cursor_calls.clear()
pg.record_heartbeat()
assert conn.cursor_calls == [], "heartbeat must be throttled within interval"

pg.close()
assert pg._pool is None
print("✅ Postgres store: wire format + heartbeat throttling correct")

# ── 3. JobMonitor facade ──────────────────────────────────────────────────────
def make_monitor():
    return JobMonitor(InMemoryJobMonitorStore())


def _run_price(monitor, records=10, status=None, exc=None):
    with monitor.run("price_refresh") as run:
        run.records = records
        run.details = "quotes only"
        if status == "skipped":
            run.status = "skipped"
        elif exc:
            raise exc
    return monitor


# 3a. Success run → recorded with duration + records.
# time.time() is consumed 4x by JobMonitor.run: in-memory record_start,
# facade `started`, duration calc, and in-memory record_finish.
m = make_monitor()
with mock.patch("time.time", side_effect=[NOW, NOW + 1.0, NOW + 2.0, NOW + 2.0]):
    _run_price(m)
runs = m.history("price_refresh")
assert len(runs) == 1 and runs[0]["status"] == "success", runs
assert runs[0]["records"] == 10 and runs[0]["duration_ms"] == 1000.0, runs[0]
print("✅ JobMonitor.run: success recorded with duration + records")

# 3b. Skipped run → status skipped.
m = make_monitor()
_run_price(m, status="skipped")
assert m.history("price_refresh")[0]["status"] == "skipped"
print("✅ JobMonitor.run: skipped recorded")

# 3c. Exception → failed + re-raised (job must still see the error).
m = make_monitor()
try:
    _run_price(m, exc=RuntimeError("simulated fetch failure"))
    raise AssertionError("exception must propagate out of run()")
except RuntimeError:
    pass
runs = m.history("price_refresh")
assert runs and runs[0]["status"] == "failed", runs
assert "simulated fetch failure" in (runs[0]["error"] or ""), runs[0]
print("✅ JobMonitor.run: failure recorded with error, exception propagates")

# 3d. Store failure degrades gracefully (monitoring never breaks the job).
class BrokenStore:
    def record_start(self, job_name):
        raise RuntimeError("db down")

    def record_finish(self, *a, **k):
        raise RuntimeError("db down")

    def record_heartbeat(self):
        raise RuntimeError("db down")

    def recent_runs(self, job_name, limit=50):
        raise RuntimeError("db down")

    def last_heartbeat(self):
        raise RuntimeError("db down")

    def close(self):
        raise RuntimeError("db down")


m = JobMonitor(BrokenStore())
with m.run("price_refresh") as run:
    run.records = 5  # job work completes fine
m.heartbeat()  # must not raise
summary = m.summary(now=NOW)
assert summary["jobs"] and summary["scheduler"] is not None
m.close()
print("✅ JobMonitor: store outage degrades to warnings, job unaffected")

# ── 4. Staleness + scheduler liveness ─────────────────────────────────────────
def _seed_success(monitor, job_name, finished_ago_s, records=10):
    """Backdate a successful run by ``finished_ago_s`` from NOW."""
    started = NOW - finished_ago_s - 5
    finished = NOW - finished_ago_s
    with monitor.run(job_name) as run:
        run.records = records
    # Rewrite timestamps after the fact (store is in-memory).
    rows = monitor._store._runs
    rows[-1]["started_at"] = started
    rows[-1]["finished_at"] = finished


m = make_monitor()
_seed_success(m, "price_refresh", finished_ago_s=60)  # 1 min ago — fresh
s = m.summary(now=NOW)
price = next(j for j in s["jobs"] if j["job_name"] == "price_refresh")
assert price["stale"] is False and price["stale_reason"] is None, price
assert price["consecutive_failures"] == 0
assert price["last_status"] == "success"

m = make_monitor()
_seed_success(m, "price_refresh", finished_ago_s=46 * 60)  # 46 min > 45 min threshold
s = m.summary(now=NOW)
price = next(j for j in s["jobs"] if j["job_name"] == "price_refresh")
assert price["stale"] is True and "45 min" in (price["stale_reason"] or ""), price

# Never ran → stale with explicit reason.
m = make_monitor()
s = m.summary(now=NOW)
news = next(j for j in s["jobs"] if j["job_name"] == "news_refresh")
assert news["stale"] is True and "never completed" in (news["stale_reason"] or ""), news

# On-demand job (manual_refresh) is never stale by clock time.
s = m.summary(now=NOW)
manual = next(j for j in s["jobs"] if j["job_name"] == "manual_refresh")
assert manual["stale"] is False and manual["stale_reason"] is None, manual

def _fail_price(monitor):
    """Run a price refresh that raises; the run context records failed and
    re-raises, which the caller must catch (same as a real job caller)."""
    try:
        _run_price(monitor, exc=RuntimeError("boom"))
    except RuntimeError:
        pass


# Consecutive failures: 2 fails then a success → 0 trailing fails.
m = make_monitor()
_fail_price(m)
_fail_price(m)
_run_price(m)
s = m.summary(now=NOW)
price = next(j for j in s["jobs"] if j["job_name"] == "price_refresh")
assert price["consecutive_failures"] == 0, price

# 3 fails in a row → 3.
m = make_monitor()
for _ in range(3):
    _fail_price(m)
s = m.summary(now=NOW)
price = next(j for j in s["jobs"] if j["job_name"] == "price_refresh")
assert price["consecutive_failures"] == 3, price
assert price["last_error"] == "boom", price
print("✅ Staleness + consecutive-failure detection correct")

# Scheduler liveness from the heartbeat beacon.
m = make_monitor()
m._store.record_heartbeat()
m._store._heartbeat["last_beat_at"] = NOW - 60  # fresh
s = m.summary(now=NOW)
assert s["scheduler"]["alive"] is True, s["scheduler"]

m = make_monitor()
m._store.record_heartbeat()
m._store._heartbeat["last_beat_at"] = NOW - 60 * 60  # 1h old → dead
s = m.summary(now=NOW)
assert s["scheduler"]["alive"] is False, s["scheduler"]
assert s["scheduler"]["heartbeat_age_seconds"] == 3600.0

m = make_monitor()
s = m.summary(now=NOW)  # never beat → not alive, no age
assert s["scheduler"]["alive"] is False and s["scheduler"]["last_heartbeat_at"] is None
print("✅ Scheduler liveness from heartbeat correct")

# ── 5. Scheduler integration (fake pool/db — no network) ─────────────────────
class FakeSchedulerPool:
    pass


class FakeDB:
    def __init__(self):
        self.upserts = 0
        self.closed = 0

    def bulk_upsert_stocks(self, records):
        self.upserts += len(records)

    def get_all_symbols(self):
        return ["AAPL", "MSFT"]

    def get_top_symbols(self, limit=10):
        return ["AAPL", "MSFT", "GOOG"]

    def close(self):
        self.closed += 1


def _scheduler(monitor):
    return DataRefreshScheduler(
        pool=FakeSchedulerPool(),
        db=FakeDB(),
        news_db=FakeDB(),
        monitor=monitor,
    )


# 5a. Price refresh success → monitored run with records.
m = make_monitor()
sched = _scheduler(m)
with mock.patch("src.scheduler.refresh_price_data", return_value=[{"symbol": "AAPL"}] * 3), \
     mock.patch("src.scheduler.fetch_us_stock_symbols", return_value=["AAPL", "MSFT", "GOOG"]):
    sched.job_refresh_prices()
runs = m.history("price_refresh")
assert runs and runs[0]["status"] == "success" and runs[0]["records"] == 3, runs
assert sched._db.upserts == 3, "db upsert must still happen"
# Cycle 1: technicals NOT included (1 % 4 != 0).
assert m.history("technical_refresh") == [], "technical job must not run on cycle 1"
print("✅ Scheduler: price refresh recorded, db upsert preserved")

# 5b. Technical refresh on the 4th cycle.
m = make_monitor()
sched = _scheduler(m)
sched._refresh_count = 3  # next run is cycle 4 → includes technicals
with mock.patch("src.scheduler.refresh_price_data", return_value=[{"symbol": "AAPL"}] * 3), \
     mock.patch("src.scheduler.fetch_us_stock_symbols", return_value=["AAPL"]):
    sched.job_refresh_prices()
assert m.history("price_refresh")[0]["status"] == "success"
tech = m.history("technical_refresh")
assert tech and tech[0]["status"] == "success" and tech[0]["records"] == 3, tech
print("✅ Scheduler: technical refresh recorded on the 4th cycle")

# 5c. Failure → failed run, error surfaced, and the job does NOT re-raise
#     (the scheduler swallows + closes the pool, monitoring records it).
m = make_monitor()
sched = _scheduler(m)
with mock.patch("src.scheduler.refresh_price_data", side_effect=RuntimeError("finnhub down")), \
     mock.patch("src.scheduler.fetch_us_stock_symbols", return_value=["AAPL"]):
    sched.job_refresh_prices()  # must not raise
runs = m.history("price_refresh")
assert runs and runs[0]["status"] == "failed" and "finnhub down" in runs[0]["error"], runs
assert sched._db.closed == 1, "db pool must be closed on failure"
print("✅ Scheduler: failure recorded, job swallows, pool closed")

# 5d. No symbols → skipped run.
m = make_monitor()
sched = _scheduler(m)
with mock.patch("src.scheduler.fetch_us_stock_symbols", return_value=[]), \
     mock.patch.object(sched._db, "get_all_symbols", return_value=[]):
    sched.job_refresh_prices()
runs = m.history("price_refresh")
assert runs and runs[0]["status"] == "skipped", runs
print("✅ Scheduler: empty universe → skipped run")

# 5e. Fundamentals + news jobs also record runs.
m = make_monitor()
sched = _scheduler(m)
with mock.patch("src.scheduler.refresh_fundamental_data", return_value=[{"symbol": "AAPL"}] * 2), \
     mock.patch("src.scheduler.fetch_us_stock_symbols", return_value=["AAPL"]):
    sched.job_refresh_fundamentals()
frun = m.history("fundamental_refresh")
assert frun and frun[0]["status"] == "success" and frun[0]["records"] == 2, frun

m = make_monitor()
sched = _scheduler(m)
with mock.patch("src.news.refresh_news_batch", return_value=7), \
     mock.patch("src.scheduler.fetch_us_stock_symbols", return_value=["AAPL"]):
    sched.job_refresh_news()
nrun = m.history("news_refresh")
assert nrun and nrun[0]["status"] == "success" and nrun[0]["records"] == 7, nrun
print("✅ Scheduler: fundamental + news jobs recorded")

# 5f. Heartbeat emitted on start/loop (throttled persistence is store-side).
m = make_monitor()
sched = _scheduler(m)
sched._monitor.heartbeat()
assert m._store.last_heartbeat() is not None
print("✅ Scheduler: heartbeat path works")

# ── 6. API surface (real FastAPI app, store patched to in-memory) ────────────
from fastapi.testclient import TestClient  # noqa: E402
from tests.fakes import FakeLLMRouter, FakeUserDB  # noqa: E402
from src.auth import create_access_token  # noqa: E402

# VULN-003: /ops/* is admin-gated now. Seed an admin user in a fake DB and
# mint a JWT so this section exercises the REAL authorization path (and
# locks the new security contract: unauthenticated → 401).
_ops_db = FakeUserDB()
_ops_admin_id = _ops_db.create_user(
    "ops-admin@stratoscan.app", "x", plan="free",
    email_verified=True, is_admin=True,
)
mock.patch("src.database.PostgresDB", return_value=_ops_db).start()
_ops_token = create_access_token(_ops_admin_id, "ops-admin@stratoscan.app")
_ops_headers = {"Authorization": f"Bearer {_ops_token}"}

mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()
from src.api import app  # noqa: E402

client = TestClient(app)

# Unauthenticated → 401 (VULN-003: job telemetry is admin-only).
r0 = client.get("/ops/jobs")
assert r0.status_code == 401, f"unauthenticated /ops/jobs: {r0.status_code} (want 401)"

# Seed an in-memory store with data, patch get_job_monitor_store to return it.
seed = InMemoryJobMonitorStore()
_runner = JobMonitor(seed)
with _runner.run("price_refresh") as run:
    run.records = 42
    run.details = "quotes only"
_seed_success(_runner, "news_refresh", finished_ago_s=30, records=7)

with mock.patch(
    "src.job_monitor.get_job_monitor_store", return_value=seed
):
    r = client.get("/ops/jobs", headers=_ops_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    names = {j["job_name"] for j in body["jobs"]}
    assert names == set(JOB_DEFINITIONS), names
    price = next(j for j in body["jobs"] if j["job_name"] == "price_refresh")
    assert price["last_status"] == "success" and price["last_records"] == 42
    assert price["stale"] is False, price
    assert body["scheduler"]["alive"] is False  # never heartbeated
    assert "persist_interval_minutes" in body["scheduler"]

    r2 = client.get("/ops/jobs/price_refresh", headers=_ops_headers)
    assert r2.status_code == 200, r2.text
    hb = r2.json()
    assert hb["job_name"] == "price_refresh" and len(hb["runs"]) == 1
    assert hb["runs"][0]["records"] == 42

    r3 = client.get("/ops/jobs/nonexistent_job", headers=_ops_headers)
    assert r3.status_code == 404, r3.text

mock.patch.stopall()
print("✅ API: /ops/jobs + /ops/jobs/{job_name} + 404 via real app")

# ── 7. Migration chain is linear + creates the new tables ────────────────────
from pathlib import Path  # noqa: E402
import re  # noqa: E402

_VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"
migs = []
for f in sorted(_VERSIONS.glob("*.py")):
    if f.name.startswith("__"):
        continue
    src = f.read_text(encoding="utf-8")
    rev = re.search(r'^revision: str = "([^"]+)"', src, re.M)
    down = re.search(r'^down_revision: Union\[str, Sequence\[str\], None\] = "([^"]+)"', src, re.M)
    migs.append((f.name, rev.group(1) if rev else "", down.group(1) if down else None))
revs = [r for _, r, _ in migs]
downs = [d for _, _, d in migs if d]
heads = [r for r in revs if r not in downs]
assert len(heads) == 1 and heads[0] == "c1d2e3f4a5b6", heads
monitor_mig = next(m for m in migs if m[1] == "f7a8b9c0d1e2")
assert monitor_mig[2] == "f1a2b3c4d5e6", monitor_mig  # chains after screener-quality
body = (_VERSIONS / monitor_mig[0]).read_text(encoding="utf-8")
assert "CREATE TABLE IF NOT EXISTS job_runs" in body
assert "CREATE TABLE IF NOT EXISTS scheduler_heartbeat" in body
assert "DROP TABLE IF EXISTS job_runs" in body  # downgrade reverses
# Runtime schemas must agree with the migration (single source of truth).
from src.database import JOB_RUNS_SCHEMA, SCHEDULER_HEARTBEAT_SCHEMA  # noqa: E402
assert "job_runs" in JOB_RUNS_SCHEMA and "scheduler_heartbeat" in SCHEDULER_HEARTBEAT_SCHEMA
print("✅ Migration: linear head c1d2e3f4a5b6, job tables present, schema synced")

print()
print("🎯 All job-monitor tests pass!")
