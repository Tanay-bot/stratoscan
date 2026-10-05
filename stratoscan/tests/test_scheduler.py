"""Hermetic tests for the background scheduler (src/scheduler.py) — no network,
no live DB.

Run: python -m tests.test_scheduler

Covers the scheduler that had ZERO direct test coverage:

1. job_refresh_prices: happy path (records upserted, pool closed, monitor
   records), skipped when no symbols, failed + pool-close on exception,
   technicals only on every 4th cycle (cadence contract).
2. job_refresh_fundamentals: happy / skip / fail.
3. job_refresh_news: no news DB -> early skip, happy path (top symbols from
   screener DB, pool closed), fallback to cached symbols when the top-symbols
   query fails, exception -> failed.
4. job_warm_backtest_cache: disabled -> skip, no backtest DB -> skip,
   happy path, partial-failure stats -> status failed.
5. start(): schedules jobs, runs the initial refresh, handles KeyboardInterrupt.
6. stop(): flips _running off.

The real JobMonitor backed by the in-memory store is used throughout, so the
run-status contract (success/skipped/failed) is exercised for real.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest import mock  # noqa: E402

import src.scheduler as sched  # noqa: E402
from src.job_monitor import InMemoryJobMonitorStore, JobMonitor  # noqa: E402


class _TopCapDB:
    """Fake DB whose get_top_symbols returns a market-cap-ranked universe."""

    def __init__(self, symbols):
        self._symbols = symbols
        self.bulk_upsert_stocks = mock.Mock()
        self.close = mock.Mock()

    def get_top_symbols(self, limit):
        return self._symbols[:limit]

    def get_all_symbols(self):
        return []


def _make_scheduler(**kwargs):
    """Build a scheduler with fake pool/db and a REAL in-memory monitor."""
    defaults = {
        "pool": mock.Mock(),
        "db": mock.Mock(),
        "news_db": mock.Mock(),
        "monitor": JobMonitor(InMemoryJobMonitorStore()),
    }
    defaults.update(kwargs)
    return sched.DataRefreshScheduler(**defaults)


def _last_status(mon: JobMonitor, job: str) -> str:
    runs = mon._store.recent_runs(job, 5)
    return runs[0]["status"] if runs else "NO_RUNS"


# ── 1. Price refresh ────────────────────────────────────────────────────────
print("1. job_refresh_prices...")

s = _make_scheduler()
mon = s._monitor
records = [{"symbol": "AAPL"}, {"symbol": "MSFT"}]
with mock.patch.object(sched, "fetch_us_stock_symbols", return_value=["AAPL", "MSFT"]), \
     mock.patch.object(sched, "refresh_price_data", return_value=records) as m_rpd:
    s.job_refresh_prices()

assert _last_status(mon, "price_refresh") == "success"
assert mon._store.recent_runs("price_refresh")[0]["records"] == 2
s._db.bulk_upsert_stocks.assert_called_once_with(records)
s._db.close.assert_called_once()  # Neon auto-suspend contract
# include_technicals False on the first cycle (1 % 4 != 0); tech_symbols
# is None on non-technical cycles (no DB query needed)
m_rpd.assert_called_once_with(
    s._pool, ["AAPL", "MSFT"], include_technicals=False, tech_symbols=None
)

# Skipped when no symbols -> no DB work, status skipped
s2 = _make_scheduler()
with mock.patch.object(sched, "fetch_us_stock_symbols", return_value=[]):
    s2.job_refresh_prices()
assert _last_status(s2._monitor, "price_refresh") == "skipped"
assert not s2._db.bulk_upsert_stocks.called

# Failure -> status failed + DB pool still closed (never leaks a connection)
s3 = _make_scheduler()
with mock.patch.object(sched, "fetch_us_stock_symbols", return_value=["AAPL"]), \
     mock.patch.object(sched, "refresh_price_data", side_effect=RuntimeError("finnhub down")):
    s3.job_refresh_prices()
assert _last_status(s3._monitor, "price_refresh") == "failed"
assert s3._db.close.called, "pool must close even on failure"

# Technicals cadence: every 4th cycle (4 % 4 == 0) records a technical run
s4 = _make_scheduler()
with mock.patch.object(sched, "fetch_us_stock_symbols", return_value=["AAPL"]), \
     mock.patch.object(sched, "refresh_price_data", return_value=records):
    for _ in range(4):
        s4.job_refresh_prices()
assert _last_status(s4._monitor, "technical_refresh") == "success", \
    "4th cycle must also refresh technicals"
assert len(s4._monitor._store.recent_runs("technical_refresh")) == 1

# Technicals UNIVERSE fix: on the 4th cycle the scheduler must pass the
# top-N-by-market-cap symbols (db.get_top_symbols) as tech_symbols — NOT
# the alphabetical symbols[:500] slice, which was refreshing the wrong 500
# (measured live: 243/500 vs 494/500 sma_50 coverage).
top_cap = [f"CAP{i}" for i in range(500)]
s5 = _make_scheduler(db=_TopCapDB(top_cap))
with mock.patch.object(sched, "fetch_us_stock_symbols", return_value=["A", "B", "C"]), \
     mock.patch.object(sched, "refresh_price_data", return_value=records) as m_rpd5:
    for _ in range(4):
        s5.job_refresh_prices()
last_call = m_rpd5.call_args
assert last_call.kwargs.get("tech_symbols") == top_cap, \
    f"4th cycle must pass top-by-cap symbols, got {last_call}"
assert last_call.kwargs.get("include_technicals") is True
print("✅ technicals universe = top-N by market cap (not alphabetical slice)")


# ── 2. Fundamental refresh ──────────────────────────────────────────────────
print("2. job_refresh_fundamentals...")

s = _make_scheduler()
with mock.patch.object(sched, "fetch_us_stock_symbols", return_value=["AAPL"]), \
     mock.patch.object(sched, "refresh_fundamental_data", return_value=records) as m_rfd:
    s.job_refresh_fundamentals()
assert _last_status(s._monitor, "fundamental_refresh") == "success"
assert m_rfd.called and s._db.close.called

s2 = _make_scheduler()
with mock.patch.object(sched, "fetch_us_stock_symbols", return_value=[]):
    s2.job_refresh_fundamentals()
assert _last_status(s2._monitor, "fundamental_refresh") == "skipped"

s3 = _make_scheduler()
with mock.patch.object(sched, "fetch_us_stock_symbols", return_value=["AAPL"]), \
     mock.patch.object(sched, "refresh_fundamental_data",
                       side_effect=RuntimeError("boom")):
    s3.job_refresh_fundamentals()
assert _last_status(s3._monitor, "fundamental_refresh") == "failed"
print("✅ happy, skip, fail")


# ── 3. News refresh ─────────────────────────────────────────────────────────
print("3. job_refresh_news...")

# No news DB configured -> early skip, no monitor run at all
with mock.patch.object(
    type(sched.settings), "has_news_database", mock.PropertyMock(return_value=False)
):
    s_none = _make_scheduler(news_db=None)
assert s_none._news_db is None
s_none.job_refresh_news()
assert not s_none._monitor._store.recent_runs("news_refresh"), \
    "no news DB must skip without recording a run"

# Happy path: top symbols from the screener DB, stored, news pool closed
s = _make_scheduler()
s._db.get_top_symbols.return_value = ["AAPL", "MSFT"]
with mock.patch.object(sched.settings, "news_symbols_limit", 150), \
     mock.patch("src.news.refresh_news_batch", return_value=2) as m_rnb:
    s.job_refresh_news()
assert _last_status(s._monitor, "news_refresh") == "success"
m_rnb.assert_called_once_with(["AAPL", "MSFT"], s._news_db)
assert s._news_db.close.called
assert s._monitor._store.recent_runs("news_refresh")[0]["records"] == 2

# Fallback: top-symbols query fails -> cached symbol list (never empty-skip)
s2 = _make_scheduler()
s2._db.get_top_symbols.side_effect = RuntimeError("screener db down")
with mock.patch.object(sched.settings, "news_symbols_limit", 150), \
     mock.patch.object(sched, "fetch_us_stock_symbols", return_value=["A", "B", "C"]), \
     mock.patch("src.news.refresh_news_batch", return_value=3) as m_rnb2:
    s2.job_refresh_news()
assert _last_status(s2._monitor, "news_refresh") == "success"
assert m_rnb2.call_args[0][0] == ["A", "B", "C"], "fallback must use cached symbols"

# No symbols at all -> skipped
s3 = _make_scheduler()
s3._db.get_top_symbols.side_effect = RuntimeError("down")
with mock.patch.object(sched.settings, "news_symbols_limit", 150), \
     mock.patch.object(sched, "fetch_us_stock_symbols", return_value=[]):
    s3.job_refresh_news()
assert _last_status(s3._monitor, "news_refresh") == "skipped"

# Failure -> status failed + news pool still closed
s4 = _make_scheduler()
s4._db.get_top_symbols.return_value = ["AAPL"]
with mock.patch("src.news.refresh_news_batch", side_effect=RuntimeError("llm down")):
    s4.job_refresh_news()
assert _last_status(s4._monitor, "news_refresh") == "failed"
assert s4._news_db.close.called
print("✅ no-DB skip, happy, DB-fallback, empty skip, fail+close")


# ── 4. Cache warmer job ─────────────────────────────────────────────────────
print("4. job_warm_backtest_cache...")

# Disabled -> skip (no run)
s = _make_scheduler()
with mock.patch.object(sched.settings, "backtest_cache_warmer_enabled", False), \
     mock.patch.object(
         type(sched.settings), "has_backtest_database", mock.PropertyMock(return_value=True)
     ):
    s.job_warm_backtest_cache()
assert not s._monitor._store.recent_runs("backtest_cache_warm")

# No backtest DB -> skip
s = _make_scheduler()
with mock.patch.object(sched.settings, "backtest_cache_warmer_enabled", True), \
     mock.patch.object(
         type(sched.settings), "has_backtest_database", mock.PropertyMock(return_value=False)
     ):
    s.job_warm_backtest_cache()
assert not s._monitor._store.recent_runs("backtest_cache_warm")

# Happy path
s = _make_scheduler()
with mock.patch.object(sched.settings, "backtest_cache_warmer_enabled", True), \
     mock.patch.object(
         type(sched.settings), "has_backtest_database", mock.PropertyMock(return_value=True)
     ), \
     mock.patch.object(
         sched, "warm_backtest_cache",
         return_value={"warmed": 3, "skipped": 1, "failed": 0, "candidates": 4},
     ), \
     mock.patch.object(sched, "backfill_volume_from_history", return_value={"symbols": 3, "updated": 2}):
    s.job_warm_backtest_cache()
run = s._monitor._store.recent_runs("backtest_cache_warm")[0]
assert run["status"] == "success" and run["records"] == 3
assert "volume backfilled for 2 symbols" in (run["details"] or ""), run

# Day 14: a volume-backfill failure is noted but never fails the warm.
s = _make_scheduler()
with mock.patch.object(sched.settings, "backtest_cache_warmer_enabled", True), \
     mock.patch.object(
         type(sched.settings), "has_backtest_database", mock.PropertyMock(return_value=True)
     ), \
     mock.patch.object(
         sched, "warm_backtest_cache",
         return_value={"warmed": 3, "skipped": 1, "failed": 0, "candidates": 4},
     ), \
     mock.patch.object(sched, "backfill_volume_from_history", side_effect=RuntimeError("db down")):
    s.job_warm_backtest_cache()
run = s._monitor._store.recent_runs("backtest_cache_warm")[0]
assert run["status"] == "success" and "volume backfill failed" in (run["details"] or ""), run

# Partial failures -> status failed with the error surfaced
s2 = _make_scheduler()
with mock.patch.object(sched.settings, "backtest_cache_warmer_enabled", True), \
     mock.patch.object(
         type(sched.settings), "has_backtest_database", mock.PropertyMock(return_value=True)
     ), \
     mock.patch.object(
         sched, "warm_backtest_cache",
         return_value={"warmed": 1, "skipped": 0, "failed": 2, "candidates": 3},
     ):
    s2.job_warm_backtest_cache()
run2 = s2._monitor._store.recent_runs("backtest_cache_warm")[0]
assert run2["status"] == "failed" and "2 symbols failed" in run2["error"]
print("✅ disabled/no-DB skips, happy, partial-failure flagged")


# ── 5 + 6. start() / stop() ─────────────────────────────────────────────────
print("5. start() / stop()...")

s = _make_scheduler()
with mock.patch.object(sched, "fetch_us_stock_symbols", return_value=["AAPL"]), \
     mock.patch.object(sched, "refresh_price_data", return_value=records), \
     mock.patch.object(sched.schedule, "every", return_value=mock.Mock()) as m_every, \
     mock.patch.object(sched.schedule, "run_pending"), \
     mock.patch.object(sched.time, "sleep", side_effect=KeyboardInterrupt):
    s.start()

assert not s._running, "KeyboardInterrupt must stop the loop cleanly"
# Initial price refresh ran as part of start()
assert _last_status(s._monitor, "price_refresh") == "success"
# schedule.every was used to register the jobs (at least the price one)
assert m_every.called
# heartbeat persisted (in-memory store records it)
assert s._monitor._store.last_heartbeat() is not None

s.stop()
assert not s._running
print("✅ start schedules + initial refresh + KeyboardInterrupt; stop flips off")

mock.patch.stopall()
print()
print("🎯 All scheduler tests pass!")
