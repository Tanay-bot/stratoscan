"""Background job monitoring for Stratoscan's schedulers.

Gives operators always-on visibility into every background job: what ran,
when, for how long, how many records it touched, and whether it failed —
plus a scheduler heartbeat so a silently-dead refresh process is never
mistaken for a healthy one.

Tracked jobs (see :data:`JOB_DEFINITIONS`):
  - ``price_refresh``        every 15 min (quotes + volume)
  - ``technical_refresh``    every 4th price cycle (~60 min, RSI/SMA)
  - ``fundamental_refresh``  daily at 02:00 (sector, market cap, PE)
  - ``news_refresh``         every NEWS_REFRESH_MINUTES (news+sentiment)
  - ``manual_refresh``       on demand (POST /refresh)

Storage is pluggable:
  - :class:`PostgresJobMonitorStore` — persists to the main Neon database
    (``job_runs`` + ``scheduler_heartbeat`` tables). Heartbeat writes are
    throttled to ``settings.job_heartbeat_persist_minutes`` so Neon can
    auto-suspend between refresh cycles (writing every 30s tick would keep
    the serverless compute awake 24/7 and blow the free-tier budget).
  - :class:`InMemoryJobMonitorStore` — dev / hermetic-test fallback when
    no database is configured.

Monitoring NEVER breaks a job: every store call is wrapped so a database
outage degrades to a logged warning, never a failed refresh.

Exposed via the API: ``GET /ops/jobs`` (live status + staleness per job)
and ``GET /ops/jobs/{job_name}`` (recent run history).
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.config import settings

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# Job Definitions
# ═══════════════════════════════════════════════════════════════════════════════

# Single source of truth for the jobs the scheduler runs. Used both to
# document the schedule and to compute staleness: a job whose last
# successful run finished more than ``interval_seconds * stale_multiplier``
# ago is flagged ``stale`` so a broken refresh is visible immediately.
# NOTE: the two hardcoded intervals below MUST stay in sync with the
# scheduler's cadence constants in src/scheduler.py — price_refresh mirrors
# PRICE_REFRESH_MINUTES (15 min) and technical_refresh mirrors
# PRICE_REFRESH_MINUTES * TECHNICAL_REFRESH_INTERVAL (60 min). A stale
# threshold is only meaningful if it matches the real schedule, so if you
# retune the scheduler, update these too. news_refresh already reads
# settings.news_refresh_minutes so it stays in sync automatically.
JOB_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "price_refresh": {
        "label": "Price/Volume refresh",
        "schedule": "every 15 min",
        "interval_seconds": 15 * 60,
        "stale_multiplier": 3,   # stale after ~45 min without success
    },
    "technical_refresh": {
        "label": "Technical indicators (RSI/SMA)",
        "schedule": "every 4th price cycle (~60 min)",
        "interval_seconds": 60 * 60,
        "stale_multiplier": 3,
    },
    "fundamental_refresh": {
        "label": "Fundamental profiles",
        "schedule": "daily at 02:00",
        "interval_seconds": 24 * 60 * 60,
        "stale_multiplier": 3,
    },
    "news_refresh": {
        "label": "News + sentiment",
        "schedule": f"every {settings.news_refresh_minutes} min",
        "interval_seconds": settings.news_refresh_minutes * 60,
        "stale_multiplier": 3,
    },
    "backtest_cache_warm": {
        "label": "Backtest history cache warmer",
        "schedule": f"daily at {settings.backtest_cache_warmer_time}",
        "interval_seconds": 24 * 60 * 60,
        "stale_multiplier": 3,
    },
    "weekly_digest": {
        "label": "Weekly strategy digest (once per user per week)",
        "schedule": f"daily at {settings.digest_time}",
        "interval_seconds": 24 * 60 * 60,
        "stale_multiplier": 3,
    },
    "manual_refresh": {
        "label": "Manual refresh (POST /refresh)",
        "schedule": "on demand",
        "interval_seconds": None,  # on-demand: never stale by clock time
        "stale_multiplier": None,
    },
    "historical_backfill": {
        "label": "Historical OHLCV backfill (Neon bulk load)",
        "schedule": "on demand (python -m src.main backfill-history)",
        "interval_seconds": None,  # on-demand: never stale by clock time
        "stale_multiplier": None,
    },
}

# Scheduler liveness: judged against this many heartbeat intervals. With a
# 15-min persist cadence, the scheduler is "not alive" after ~45 min of no
# heartbeat — well within a price-refresh cycle's worth of slack.
HEARTBEAT_STALE_MULTIPLIER: int = 3


# ═══════════════════════════════════════════════════════════════════════════════
# Store Interface
# ═══════════════════════════════════════════════════════════════════════════════

def scheduler_owner() -> str:
    """This process's lease identity: ``host:pid`` (Day 15).

    A container's hostname is its id, so two scheduler containers never share
    an owner even though both run as pid 1; a restarted container keeps its
    hostname and pid, so it re-claims its own lease without waiting.
    """
    return f"{socket.gethostname()}:{os.getpid()}"


class JobMonitorStore(ABC):
    """Storage backend for job runs + scheduler heartbeat."""

    @abstractmethod
    def record_start(self, job_name: str) -> Optional[int]:
        """Persist a 'running' row; return its run id (None on failure)."""

    @abstractmethod
    def record_finish(
        self,
        run_id: int,
        status: str,
        duration_ms: Optional[float] = None,
        records: Optional[int] = None,
        details: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """Mark a run finished with its outcome."""

    @abstractmethod
    def record_heartbeat(self) -> Optional[bool]:
        """Refresh the scheduler liveness beacon (throttled by the store).

        Returns False only when another scheduler now holds the lease (the
        caller must stop); True/None otherwise (None = throttled, not written).
        """

    @abstractmethod
    def claim_scheduler(self, owner: str, stale_after_seconds: float, force: bool = False) -> Dict[str, Any]:
        """Take the single-scheduler lease (Day 15).

        Granted when nobody holds it, ``owner`` already does, the holder's
        last beat is older than ``stale_after_seconds``, or ``force``.
        Returns ``{"claimed": bool, "holder": str|None, "age_seconds": float|None}``.
        """

    @abstractmethod
    def release_scheduler(self, owner: str) -> None:
        """Give the lease up (clean shutdown) — only if ``owner`` holds it."""

    @abstractmethod
    def recent_runs(self, job_name: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Most recent runs for a job, newest first."""

    @abstractmethod
    def last_heartbeat(self) -> Optional[Dict[str, Any]]:
        """Latest heartbeat row (``last_beat_at`` epoch + ``pid``) or None."""

    @abstractmethod
    def close(self) -> None:
        """Release any pooled connections."""


# ═══════════════════════════════════════════════════════════════════════════════
# Postgres Store (production — Neon)
# ═══════════════════════════════════════════════════════════════════════════════

class PostgresJobMonitorStore(JobMonitorStore):
    """Persist job runs + heartbeat to the main Neon database.

    Uses its own small connection pool (the scheduler's shared pool is
    closed between cycles for Neon auto-suspend, so the monitor must be
    self-sufficient). Tables are created idempotently on first use so the
    store works even if Alembic hasn't run yet.
    """

    def __init__(self, pool=None) -> None:
        """Args:
            pool: Optional injected pool for tests. When None (production)
                the store lazily creates a real psycopg2 pool AND closes it
                after every operation — a pool that lingered would keep a
                live connection open in the long-running scheduler and
                prevent Neon from auto-suspending between refresh cycles.
        """
        self._pool = pool
        self._owns_pool = pool is None
        self._last_heartbeat_persist: float = 0.0
        self._schema_ready: bool = False
        self._owner: Optional[str] = None

    def _ensure_pool_and_schema(self):
        """Lazily create the pool + tables (idempotent)."""
        from src.database import (
            JOB_RUNS_INDEXES,
            JOB_RUNS_SCHEMA,
            SCHEDULER_HEARTBEAT_BACKFILL,
            SCHEDULER_HEARTBEAT_SCHEMA,
            _sanitize_dsn,
        )

        if self._pool is None:
            if not settings.has_database_url:
                raise RuntimeError("DATABASE_URL not configured")
            import psycopg2.pool

            self._pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=1,
                maxconn=2,
                dsn=_sanitize_dsn(settings.database_url),
                connect_timeout=settings.db_connect_timeout,
            )
        if not self._schema_ready:
            conn = self._pool.getconn()
            ok = False
            try:
                with conn.cursor() as cur:
                    cur.execute(JOB_RUNS_SCHEMA)
                    for idx in JOB_RUNS_INDEXES:
                        cur.execute(idx)
                    cur.execute(SCHEDULER_HEARTBEAT_SCHEMA)
                    for stmt in SCHEDULER_HEARTBEAT_BACKFILL:
                        cur.execute(stmt)
                conn.commit()
                self._schema_ready = True
                ok = True
            except Exception:
                conn.rollback()
                raise
            finally:
                try:
                    self._pool.putconn(conn)
                finally:
                    # If schema init failed, release the pool too — otherwise
                    # the scheduler would hold a live Neon connection forever
                    # (auto-suspend defeated). The next op recreates + retries.
                    if not ok:
                        self._maybe_close_pool()

    def _maybe_close_pool(self) -> None:
        """Release the pool after an operation (production only).

        Injected test pools are left alone so the caller owns their
        lifecycle; the real pool is closed after every op so Neon can
        auto-suspend between scheduler cycles. Heartbeat throttling already
        limits how often a heartbeat write wakes the DB.
        """
        if self._owns_pool and self._pool is not None:
            self._pool.closeall()
            self._pool = None

    def _execute(self, sql: str, params: tuple = ()) -> Any:
        """Run a single statement against a checked-out connection."""
        self._ensure_pool_and_schema()
        assert self._pool is not None
        conn = self._pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone() if cur.description else None
            conn.commit()
            return row
        except Exception:
            conn.rollback()
            raise
        finally:
            try:
                self._pool.putconn(conn)
            finally:
                self._maybe_close_pool()

    def record_start(self, job_name: str) -> Optional[int]:
        row = self._execute(
            "INSERT INTO job_runs (job_name, status) "
            "VALUES (%s, 'running') RETURNING id",
            (job_name,),
        )
        return int(row[0]) if row else None

    def record_finish(
        self,
        run_id: int,
        status: str,
        duration_ms: Optional[float] = None,
        records: Optional[int] = None,
        details: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        self._execute(
            "UPDATE job_runs SET status=%s, finished_at=NOW(), "
            "duration_ms=%s, records=%s, details=%s, error=%s "
            "WHERE id=%s",
            (status, duration_ms, records, details, error, run_id),
        )

    def record_heartbeat(self) -> Optional[bool]:
        """Persist a heartbeat, but at most once per persist interval.

        The write only lands while this process holds the lease (or nobody
        does); a row owned by someone else means a takeover happened, and the
        False return tells the scheduler loop to stop.
        """
        interval = max(
            1, settings.job_heartbeat_persist_minutes * 60
        )
        now = time.time()
        if now - self._last_heartbeat_persist < interval:
            return None
        owner = self._owner or scheduler_owner()
        row = self._execute(
            "INSERT INTO scheduler_heartbeat (id, last_beat_at, pid, owner) "
            "VALUES (1, NOW(), %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET last_beat_at=NOW(), pid=EXCLUDED.pid, owner=EXCLUDED.owner "
            "WHERE scheduler_heartbeat.owner IS NULL OR scheduler_heartbeat.owner = EXCLUDED.owner "
            "RETURNING owner",
            (os.getpid(), owner),
        )
        self._last_heartbeat_persist = now
        return row is not None

    def claim_scheduler(self, owner: str, stale_after_seconds: float, force: bool = False) -> Dict[str, Any]:
        row = self._execute(
            "INSERT INTO scheduler_heartbeat (id, last_beat_at, pid, owner) "
            "VALUES (1, NOW(), %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET last_beat_at=NOW(), pid=EXCLUDED.pid, owner=EXCLUDED.owner "
            "WHERE %s OR scheduler_heartbeat.owner IS NULL OR scheduler_heartbeat.owner = EXCLUDED.owner "
            "OR scheduler_heartbeat.last_beat_at < NOW() - make_interval(secs => %s) "
            "RETURNING owner",
            (os.getpid(), owner, bool(force), float(stale_after_seconds)),
        )
        if row is not None:
            self._owner = owner
            self._last_heartbeat_persist = time.time()  # the claim is a beat
            return {"claimed": True, "holder": owner, "age_seconds": 0.0}
        held = self._execute(
            "SELECT owner, EXTRACT(EPOCH FROM (NOW() - last_beat_at)) "
            "FROM scheduler_heartbeat WHERE id=1"
        )
        return {"claimed": False, "holder": held[0] if held else None,
                "age_seconds": float(held[1]) if held and held[1] is not None else None}

    def release_scheduler(self, owner: str) -> None:
        self._execute(
            "UPDATE scheduler_heartbeat SET owner = NULL WHERE id = 1 AND owner = %s",
            (owner,),
        )

    def recent_runs(self, job_name: str, limit: int = 50) -> List[Dict[str, Any]]:
        self._ensure_pool_and_schema()
        assert self._pool is not None
        conn = self._pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, job_name, status, "
                    "EXTRACT(EPOCH FROM started_at) AS started_at, "
                    "EXTRACT(EPOCH FROM finished_at) AS finished_at, "
                    "duration_ms, records, details, error "
                    "FROM job_runs WHERE job_name=%s "
                    "ORDER BY started_at DESC LIMIT %s",
                    (job_name, limit),
                )
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                # psycopg2 returns EXTRACT(EPOCH …) as decimal.Decimal, but the
                # in-memory store uses plain floats — normalize at the read
                # boundary so _staleness/_liveness arithmetic never mixes
                # types (float - Decimal raises TypeError).
                for row in rows:
                    for key in ("started_at", "finished_at"):
                        if row.get(key) is not None:
                            row[key] = float(row[key])
                return rows
        finally:
            try:
                self._pool.putconn(conn)
            finally:
                self._maybe_close_pool()

    def last_heartbeat(self) -> Optional[Dict[str, Any]]:
        self._ensure_pool_and_schema()
        assert self._pool is not None
        conn = self._pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT EXTRACT(EPOCH FROM last_beat_at) AS last_beat_at, "
                    "pid, owner FROM scheduler_heartbeat WHERE id=1"
                )
                row = cur.fetchone()
            if row and row[0] is not None:
                # Same Decimal → float normalization as recent_runs().
                return {"last_beat_at": float(row[0]), "pid": row[1], "owner": row[2]}
            return None
        finally:
            try:
                self._pool.putconn(conn)
            finally:
                self._maybe_close_pool()

    def close(self) -> None:
        if self._pool:
            self._pool.closeall()
            self._pool = None


# ═══════════════════════════════════════════════════════════════════════════════
# In-Memory Store (dev / hermetic tests)
# ═══════════════════════════════════════════════════════════════════════════════

class InMemoryJobMonitorStore(JobMonitorStore):
    """Thread-safe in-memory fallback — no persistence across restarts.

    Mirrors the Postgres store's return shapes exactly so the JobMonitor
    is storage-agnostic and hermetic tests need no database.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: List[Dict[str, Any]] = []
        self._next_id: int = 1
        self._heartbeat: Optional[Dict[str, Any]] = None
        self._owner: Optional[str] = None

    def record_start(self, job_name: str) -> Optional[int]:
        with self._lock:
            run_id = self._next_id
            self._next_id += 1
            self._runs.append(
                {
                    "id": run_id,
                    "job_name": job_name,
                    "status": "running",
                    "started_at": time.time(),
                    "finished_at": None,
                    "duration_ms": None,
                    "records": None,
                    "details": None,
                    "error": None,
                }
            )
            return run_id

    def record_finish(
        self,
        run_id: int,
        status: str,
        duration_ms: Optional[float] = None,
        records: Optional[int] = None,
        details: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        with self._lock:
            for run in self._runs:
                if run["id"] == run_id:
                    run["status"] = status
                    run["finished_at"] = time.time()
                    run["duration_ms"] = duration_ms
                    run["records"] = records
                    run["details"] = details
                    run["error"] = error
                    return

    def record_heartbeat(self) -> Optional[bool]:
        with self._lock:
            owner = self._owner or scheduler_owner()
            holder = (self._heartbeat or {}).get("owner")
            if holder not in (None, owner):
                return False  # taken over — mirrors the Postgres WHERE clause
            self._heartbeat = {"last_beat_at": time.time(), "pid": os.getpid(), "owner": owner}
            return True

    def claim_scheduler(self, owner: str, stale_after_seconds: float, force: bool = False) -> Dict[str, Any]:
        with self._lock:
            hb = self._heartbeat or {}
            holder, age = hb.get("owner"), (time.time() - hb["last_beat_at"]) if hb else None
            if force or holder in (None, owner) or (age is not None and age > stale_after_seconds):
                self._heartbeat = {"last_beat_at": time.time(), "pid": os.getpid(), "owner": owner}
                self._owner = owner
                return {"claimed": True, "holder": owner, "age_seconds": 0.0}
            return {"claimed": False, "holder": holder, "age_seconds": age}

    def release_scheduler(self, owner: str) -> None:
        with self._lock:
            if self._heartbeat and self._heartbeat.get("owner") == owner:
                self._heartbeat = {**self._heartbeat, "owner": None}

    def recent_runs(self, job_name: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            runs = [r for r in self._runs if r["job_name"] == job_name]
            runs.sort(key=lambda r: r["started_at"], reverse=True)
            return runs[:limit]

    def last_heartbeat(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._heartbeat) if self._heartbeat else None

    def close(self) -> None:
        pass


def get_job_monitor_store() -> JobMonitorStore:
    """Pick the store: Postgres in production, in-memory otherwise."""
    if settings.job_monitor_enabled and settings.has_database_url:
        return PostgresJobMonitorStore()
    return InMemoryJobMonitorStore()


# ═══════════════════════════════════════════════════════════════════════════════
# JobMonitor Facade
# ═══════════════════════════════════════════════════════════════════════════════

class JobMonitor:
    """High-level monitor: record runs, emit heartbeats, report status.

    Every store interaction is wrapped so monitoring can never take down a
    refresh job — a store failure logs a warning and the job continues.
    """

    def __init__(self, store: Optional[JobMonitorStore] = None) -> None:
        self._store = store or get_job_monitor_store()

    # ── Recording ─────────────────────────────────────────────────────

    @contextmanager
    def run(self, job_name: str):
        """Record a job execution.

        Yields a mutable holder with ``records`` / ``details`` attributes;
        the run is recorded as ``success`` on clean exit. Set
        ``holder.status = \"skipped\"`` to mark a skipped run; if an
        exception escapes the body it is recorded as ``failed`` and
        re-raised.
        """
        holder = _RunHolder()
        run_id = self._try(self._store.record_start, job_name)
        started = time.time()
        try:
            yield holder
        except Exception as e:
            holder.status = "failed"
            holder.error = str(e)
            raise
        finally:
            if run_id is not None:
                duration_ms = (time.time() - started) * 1000
                self._try(
                    self._store.record_finish,
                    run_id,
                    holder.status,
                    duration_ms,
                    holder.records,
                    holder.details,
                    holder.error,
                )
            # Alerting (Tier 2 ops): a FAILED run is the #1 thing an
            # operator wants to know about at 3am. Fire-and-forget +
            # throttled per job (src/alerts.py) — never blocks the job.
            if holder.status == "failed":
                self._alert_failed(job_name, holder.error, holder.details)

    def _alert_failed(
        self,
        job_name: str,
        error: Optional[str],
        details: Optional[str],
    ) -> None:
        """Send a throttled webhook alert for a failed job run."""
        if not settings.alerts_enabled:
            return
        try:
            from src.alerts import send_alert

            message = error or details or "see /ops/jobs for details"
            send_alert(
                f"Background job failed: {job_name}",
                message=message[:1000],
                level="error",
                throttle_key=f"job:{job_name}",
            )
        except Exception as e:
            # Alerting must never break the job monitor itself.
            logger.warning("Job-failure alert failed: %s", e)

    def heartbeat(self) -> bool:
        """Emit a scheduler liveness beat (store throttles persistence).

        Returns False only when another scheduler has taken the lease — the
        caller must stop. A store failure counts as still holding it
        (monitoring never stops a job).
        """
        return self._try(self._store.record_heartbeat) is not False

    def claim_scheduler(self, force: bool = False) -> Dict[str, Any]:
        """Take the single-scheduler lease before any job runs (Day 15).

        The lease is fresh for 3x the heartbeat persist interval (the same
        window ``summary()`` uses to call the scheduler alive). If the store
        cannot be reached the claim is granted and marked ``unverified`` —
        a scheduler with no database cannot do any harm to it anyway.
        """
        stale_after = max(1, settings.job_heartbeat_persist_minutes) * 60 * 3
        owner = scheduler_owner()
        try:
            result = self._store.claim_scheduler(owner, stale_after, force=force)
        except Exception as e:  # noqa: BLE001
            logger.warning("Scheduler lease could not be checked (%s) — starting unverified", e)
            return {"claimed": True, "holder": owner, "age_seconds": None, "unverified": True}
        result.setdefault("owner", owner)
        return result

    def release_scheduler(self) -> None:
        """Give the lease up on a clean stop so a replacement starts at once."""
        self._try(self._store.release_scheduler, scheduler_owner())

    # ── Reporting ─────────────────────────────────────────────────────

    def history(self, job_name: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Recent runs for a job (newest first), with epochs converted to ISO."""
        return self._iso_runs(self._try(self._store.recent_runs, job_name, limit) or [])

    def summary(self, now: Optional[float] = None) -> Dict[str, Any]:
        """Build the live status payload for ``GET /ops/jobs``.

        For every job in :data:`JOB_DEFINITIONS`: latest run, consecutive
        failures, and staleness (last success older than
        ``interval * stale_multiplier``). Also reports scheduler liveness
        from the heartbeat beacon.

        Args:
            now: Epoch override for deterministic tests.

        Returns:
            Dict with ``jobs`` (list) and ``scheduler`` (dict).
        """
        now = now if now is not None else time.time()
        jobs: List[Dict[str, Any]] = []
        for name, defn in JOB_DEFINITIONS.items():
            runs = self._try(self._store.recent_runs, name, 50) or []
            latest = runs[0] if runs else None

            last_success_finished: Optional[float] = None
            consecutive_failures = 0
            for r in runs:
                if r.get("status") == "success":
                    last_success_finished = r.get("finished_at")
                    break
                # Only hard failures count toward the streak — a skipped
                # cycle (empty universe) or an in-progress run is not a
                # failure.
                if r.get("status") == "failed":
                    consecutive_failures += 1

            stale, stale_reason = self._staleness(
                defn, latest, last_success_finished, now
            )

            jobs.append(
                {
                    "job_name": name,
                    "label": defn["label"],
                    "schedule": defn["schedule"],
                    "last_status": (latest or {}).get("status"),
                    "last_started_at": self._iso((latest or {}).get("started_at")),
                    "last_finished_at": self._iso((latest or {}).get("finished_at")),
                    "last_duration_ms": (latest or {}).get("duration_ms"),
                    "last_records": (latest or {}).get("records"),
                    "last_error": (latest or {}).get("error"),
                    "consecutive_failures": consecutive_failures,
                    "stale": stale,
                    "stale_reason": stale_reason,
                }
            )

        heartbeat = self._try(self._store.last_heartbeat)
        scheduler_alive, heartbeat_age = self._scheduler_liveness(heartbeat, now)

        return {
            "jobs": jobs,
            "scheduler": {
                "alive": scheduler_alive,
                "last_heartbeat_at": self._iso(
                    (heartbeat or {}).get("last_beat_at")
                ),
                "heartbeat_age_seconds": heartbeat_age,
                "pid": (heartbeat or {}).get("pid"),
                # Day 15: the lease holder ("host:pid"; None = released). Every
                # container is pid 1, so the pid alone can't tell two apart.
                "lease_owner": (heartbeat or {}).get("owner"),
                "persist_interval_minutes": settings.job_heartbeat_persist_minutes,
            },
        }

    # ── Helpers ───────────────────────────────────────────────────────

    def _staleness(
        self,
        defn: Dict[str, Any],
        latest: Optional[Dict[str, Any]],
        last_success_finished: Optional[float],
        now: float,
    ) -> tuple:
        interval = defn.get("interval_seconds")
        if interval is None:
            return False, None  # on-demand job — never stale by clock

        threshold = interval * defn.get("stale_multiplier", 3)

        if latest and latest.get("status") == "running":
            # Stuck run (started but never finished) is as bad as stale.
            age = now - (latest.get("started_at") or now)
            if age > threshold:
                return True, f"run in progress for {int(age // 60)} min"

        if last_success_finished is None:
            return True, "never completed successfully"

        age = now - last_success_finished
        if age > threshold:
            return True, (
                f"last success {int(age // 60)} min ago "
                f"(> {int(threshold // 60)} min)"
            )
        return False, None

    def _scheduler_liveness(
        self, heartbeat: Optional[Dict[str, Any]], now: float
    ) -> tuple:
        if not heartbeat or heartbeat.get("last_beat_at") is None:
            return False, None
        age = now - heartbeat["last_beat_at"]
        threshold = (
            max(1, settings.job_heartbeat_persist_minutes * 60)
            * HEARTBEAT_STALE_MULTIPLIER
        )
        return age <= threshold, round(age, 1)

    def _iso(self, epoch: Optional[float]) -> Optional[str]:
        if epoch is None:
            return None
        return (
            datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
        )

    def _iso_runs(self, runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for r in runs:
            row = dict(r)
            row["started_at"] = self._iso(row.get("started_at"))
            row["finished_at"] = self._iso(row.get("finished_at"))
            out.append(row)
        return out

    @staticmethod
    def _try(fn, *args, **kwargs) -> Any:
        """Run a store call; on ANY error log and return None.

        Monitoring must never break a job, so failures degrade to a logged
        warning. ``exc_info`` is included so genuine defects (SQL typos,
        schema drift) remain debuggable instead of vanishing.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            logger.warning("Job monitor store call failed: %s", e, exc_info=True)
            return None

    def close(self) -> None:
        self._try(self._store.close)


class _RunHolder:
    """Mutable context handed to ``JobMonitor.run`` bodies."""

    def __init__(self) -> None:
        self.status: str = "success"
        self.records: Optional[int] = None
        self.details: Optional[str] = None
        self.error: Optional[str] = None
