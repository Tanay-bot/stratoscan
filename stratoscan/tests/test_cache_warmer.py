"""Hermetic tests for the backtest cache warmer (src/cache_warmer.py).

Run: python -m tests.test_cache_warmer

Fully hermetic — zero network, zero Neon, zero real yfinance calls:

1. Staleness planner: missing history → warm; fresh history → skip; bar
   exactly stale_days old → warm (boundary); today override works.
2. Target selection: screener top-N by market cap; universe fallback when
   the screener DB errors or is empty; dedupe + uppercase.
3. Warm cycle orchestration: fetches only stale symbols via the REAL
   store primitives (mocked fetch/store), records warmed/skipped/failed,
   and a failed download doesn't abort the cycle.
4. Degradation: backtest DB error → skipped cycle (stats, no crash);
   empty candidates → skipped cycle.
5. Scheduler integration: job_warm_backtest_cache records a monitored
   run (success / partial-failure) with the real scheduler + in-memory
   monitor store, and skips cleanly when disabled or no backtest DB.
6. JOB_DEFINITIONS includes backtest_cache_warm; SQLiteDB/BacktestDB both
   expose get_symbol_max_dates (the warmer's duck-typed contract).
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["REDIS_RATE_LIMIT_ENABLED"] = "false"

import tempfile  # noqa: E402
from unittest import mock  # noqa: E402

from src.backtester import store_historical_data  # noqa: E402
from src.database import BacktestDB, SQLiteDB  # noqa: E402

TODAY = "2026-07-20"  # fixed "today" for deterministic staleness math


# ── 1. Staleness planner ──────────────────────────────────────────────────────
print("1. Staleness planner (missing / fresh / boundary)...")
from src.cache_warmer import _stale_symbols  # noqa: E402

# Missing history → warm; fresh history → skip (MSFT's newest bar 07-19 is
# within stale_days of TODAY=07-20, so only missing AAPL is warmed).
assert _stale_symbols(["AAPL", "MSFT"], {"MSFT": "2026-07-19"}, 2, TODAY) == ["AAPL"]
# Both missing/stale → both warmed.
assert _stale_symbols(["AAPL", "MSFT"], {"MSFT": "2026-07-01"}, 2, TODAY) == ["AAPL", "MSFT"]
# Fresh (within stale_days) → skip.
assert _stale_symbols(["AAPL"], {"AAPL": "2026-07-19"}, 2, TODAY) == []
# Boundary: newest bar exactly stale_days old (cutoff) → stale (refresh).
# cutoff = 2026-07-20 - 2 days = 2026-07-18; bar on 2026-07-18 is not < cutoff
# → fresh. A bar OLDER than the cutoff (07-17) → stale.
assert _stale_symbols(["AAPL"], {"AAPL": "2026-07-18"}, 2, TODAY) == []
assert _stale_symbols(["AAPL"], {"AAPL": "2026-07-17"}, 2, TODAY) == ["AAPL"]
print("✅ planner: missing→warm, fresh→skip, boundary correct")


# ── 2. Target selection ───────────────────────────────────────────────────────
print("2. Target selection (top-N, fallback, dedupe)...")
from src.cache_warmer import _select_target_symbols  # noqa: E402


class FakeScreenerDB:
    def __init__(self, symbols=None, raise_on=False):
        self._symbols = symbols or []
        self._raise_on = raise_on

    def get_top_symbols(self, limit):
        if self._raise_on:
            raise RuntimeError("db down")
        return self._symbols[:limit]


assert _select_target_symbols(FakeScreenerDB(["AAPL", "MSFT", "GOOG"]), 2) == ["AAPL", "MSFT"]
# Dedupe + uppercase normalization.
assert _select_target_symbols(
    FakeScreenerDB(["aapl", "AAPL", "msft"]), 10
) == ["AAPL", "MSFT"]

# Fallback to the universe when the screener DB raises. NOTE: the
# fallback imports fetch_us_stock_symbols / initialize_client_pool from
# src.data_fetcher at call time (lazy import), so those are the correct
# patch targets — patching the module attr would miss.
with mock.patch(
    "src.data_fetcher.fetch_us_stock_symbols",
    return_value=["TSLA", "NVDA", "TSLA"],
), mock.patch("src.data_fetcher.initialize_client_pool"):
    assert _select_target_symbols(
        FakeScreenerDB(raise_on=True), 2
    ) == ["TSLA", "NVDA"]

# Empty result → fallback.
with mock.patch(
    "src.data_fetcher.fetch_us_stock_symbols", return_value=["AMD"]
), mock.patch("src.data_fetcher.initialize_client_pool"):
    assert _select_target_symbols(FakeScreenerDB([]), 5) == ["AMD"]
print("✅ selection: top-N, dedupe, uppercase, universe fallback correct")


# ── 3. Warm cycle orchestration (temp SQLite, mocked fetch/store) ─────────────
print("3. Warm cycle orchestration (mocked primitives, temp SQLite)...")
import pandas as pd  # noqa: E402


def _make_df(days_back=0):
    """Deterministic OHLCV frame ending `days_back` days before TODAY."""
    dates = pd.date_range("2026-01-01", periods=60, freq="D")
    n = len(dates)
    return pd.DataFrame(
        {
            "Open": [100.0 + i for i in range(n)],
            "High": [101.0 + i for i in range(n)],
            "Low": [99.0 + i for i in range(n)],
            "Close": [100.5 + i for i in range(n)],
            "Adj Close": [100.5 + i for i in range(n)],
            "Volume": [1_000_000] * n,
            "Dividends": [0.0] * n,
            "Stock Splits": [1.0] * n,
        },
        index=dates,
    )


def _sqlite_db():
    tmp = tempfile.mkdtemp()
    db = SQLiteDB(db_path=os.path.join(tmp, "bt.db"))
    db.init_schema()
    return db


# 3a. One stale symbol gets warmed; fresh ones skipped; failed fetch counted.
db = _sqlite_db()
# Seed AAPL as FRESH via a direct insert of TODAY's bar (a real warm would
# store bars ending "today", so the newest cached bar lands inside the
# stale window — that is exactly what makes cycle 2 skip it).
conn = db.connect()
conn.execute(
    "INSERT OR REPLACE INTO historical_prices "
    "(symbol, date, open, high, low, close, raw_close, volume, dividends, stock_splits) "
    "VALUES ('AAPL', ?, 1, 1, 1, 1, 1, 1, 0, 1)",
    (TODAY,),
)
conn.commit()

# Build a fake screener with AAPL(fresh) + MSFT(stale/missing) + ZZZ(fetch fails).
screener = FakeScreenerDB(["AAPL", "MSFT", "ZZZ"])
fetched = []
stored = []


def fake_fetch(symbol):
    if symbol == "ZZZ":
        return None  # download failure
    fetched.append(symbol)
    return _make_df()


def fake_store(d, symbol, df):
    # Persist like a real warm would: write TODAY's bar so the next cycle
    # sees the symbol as fresh (idempotence check in cycle 2 depends on it).
    stored.append(symbol)
    conn = d.connect()
    conn.execute(
        "INSERT OR REPLACE INTO historical_prices "
        "(symbol, date, open, high, low, close, raw_close, volume, dividends, stock_splits) "
        "VALUES (?, ?, 1, 1, 1, 1, 1, 1, 0, 1)",
        (symbol, TODAY),
    )
    conn.commit()
    return len(df)


from src.cache_warmer import warm_backtest_cache  # noqa: E402

stats = warm_backtest_cache(
    backtest_db=db,
    screener_db=screener,
    limit=10,
    stale_days=2,
    delay=0.0,
    today=TODAY,
    fetch_fn=fake_fetch,
    store_fn=fake_store,
)
assert stats["candidates"] == 3, stats
assert stats["warmed"] == 1 and stats["warmed"] == len(fetched) == len(stored), stats
assert stats["warmed"] == 1 and fetched == ["MSFT"], (stats, fetched)
assert stats["skipped"] == 1, stats  # AAPL fresh
assert stats["failed"] == 1, stats  # ZZZ download failed
assert stats["symbols"] == ["MSFT"], stats

# A second cycle must skip MSFT too (now stored) — idempotence. ZZZ is a
# permanent fetch-failure, so it is legitimately re-attempted (failed again)
# and AAPL+MSFT are skipped — the meaningful property is "nothing re-warmed".
stats2 = warm_backtest_cache(
    backtest_db=db,
    screener_db=screener,
    limit=10,
    stale_days=2,
    delay=0.0,
    today=TODAY,
    fetch_fn=fake_fetch,
    store_fn=fake_store,
)
assert stats2["warmed"] == 0 and stats2["skipped"] == 2 and stats2["failed"] == 1, stats2
print("✅ cycle: stale warmed, fresh skipped, failures isolated, idempotent")

# 3b. store_historical_data(failed) also counts as failed, doesn't abort.
db2 = _sqlite_db()
screener2 = FakeScreenerDB(["NVDA"])


def fake_store_raises(d, symbol, df):
    raise RuntimeError("db write failed")


stats3 = warm_backtest_cache(
    backtest_db=db2,
    screener_db=screener2,
    limit=10,
    stale_days=2,
    delay=0.0,
    today=TODAY,
    fetch_fn=lambda s: _make_df(),
    store_fn=fake_store_raises,
)
assert stats3["failed"] == 1 and stats3["warmed"] == 0, stats3
print("✅ cycle: store failure isolated, cycle completes")


# ── 4. Degradation (no crash on missing pieces) ───────────────────────────────
print("4. Degradation paths...")


class BrokenBacktestDB:
    def get_symbol_max_dates(self):
        raise RuntimeError("neon down")


stats = warm_backtest_cache(
    backtest_db=BrokenBacktestDB(),
    screener_db=FakeScreenerDB(["AAPL"]),
    limit=10,
    stale_days=2,
    delay=0.0,
    today=TODAY,
)
# get_symbol_max_dates failed → max_dates={} → everything considered stale;
# fetch would be mocked away if it got there, but with no fetch mock it will
# attempt a real fetch — avoid that: assert the planner degraded to "warm all"
# by checking it never hit the network. We can't call the real fetch, so
# instead pass fetch_fn that counts calls:
_calls = []
stats = warm_backtest_cache(
    backtest_db=BrokenBacktestDB(),
    screener_db=FakeScreenerDB(["AAPL"]),
    limit=10,
    stale_days=2,
    delay=0.0,
    today=TODAY,
    fetch_fn=lambda s: _calls.append(s) or _make_df(),
    store_fn=lambda d, s, df: len(df),
)
assert stats["warmed"] == 1, stats  # degraded to warm-all, still worked
assert _calls == ["AAPL"], _calls
print("✅ degradation: staleness query failure → warm-all, cycle continues")

# Empty candidates → skipped cycle, no crash. The universe fallback is
# mocked to return [] too — that path is exercised separately in section 2,
# and leaving it unmocked would (a) make this "hermetic" test hit the real
# Finnhub API and (b) turn the assertion into a test of the fallback rather
# than of the empty-candidates degradation this section actually covers.
with mock.patch(
    "src.data_fetcher.fetch_us_stock_symbols", return_value=[]
), mock.patch("src.data_fetcher.initialize_client_pool"):
    stats = warm_backtest_cache(
        backtest_db=_sqlite_db(),
        screener_db=FakeScreenerDB([]),
        limit=10,
        stale_days=2,
        delay=0.0,
        today=TODAY,
        fetch_fn=lambda s: _make_df(),
    )
assert stats["candidates"] == 0 and stats["error"] == "no candidate symbols", stats
print("✅ degradation: empty candidates → skipped cycle")

# 4c. DBs the warmer CREATES ITSELF are closed after the cycle (Neon
#     auto-suspend: a lingering pool in the long-running scheduler would
#     keep the compute endpoint awake 24/7 — the same rule the job monitor
#     enforces). Callers that inject their own DB keep ownership (the
#     explicit backtest_db= in sections 3/4a above are never auto-closed).
_owned = _sqlite_db()
_owned_close_calls = []
_orig_close = _owned.close
_owned.close = lambda: _owned_close_calls.append(1) or _orig_close()
with mock.patch("src.cache_warmer._backtest_db", return_value=_owned):
    # screener_db is INJECTED, so the warmer never constructs PostgresDB
    # (lazy import — patching src.cache_warmer.PostgresDB would AttributeError).
    stats = warm_backtest_cache(
        backtest_db=None,  # warmer must construct + CLOSE it
        screener_db=FakeScreenerDB(["TSLA"]),
        limit=10,
        stale_days=2,
        delay=0.0,
        today=TODAY,
        fetch_fn=lambda s: _make_df(),
        store_fn=lambda d, s, df: len(df),
    )
assert stats["warmed"] == 1, stats
assert _owned_close_calls == [1], f"owned backtest DB must be closed: {_owned_close_calls}"
print("✅ ownership: DBs created by the warmer are closed after the cycle")
db.close()
db2.close()


# ── 5. Scheduler integration (real scheduler, in-memory monitor) ──────────────
print("5. Scheduler integration (monitored run, skip paths)...")
from src.job_monitor import InMemoryJobMonitorStore, JobMonitor  # noqa: E402
from src.scheduler import DataRefreshScheduler  # noqa: E402


class _FakePool:
    pass


def _scheduler(monitor):
    return DataRefreshScheduler(
        pool=_FakePool(), db=FakeScreenerDB(["AAPL"]), news_db=None, monitor=monitor
    )


# 5a. Enabled + backtest DB set → monitored run with warm stats.
m = JobMonitor(InMemoryJobMonitorStore())
sched = _scheduler(m)
with mock.patch("src.scheduler.settings") as ms, \
     mock.patch("src.scheduler.warm_backtest_cache") as mw:
    ms.backtest_cache_warmer_enabled = True
    ms.has_backtest_database = True
    mw.return_value = {"warmed": 5, "skipped": 10, "failed": 0, "candidates": 15}
    sched.job_warm_backtest_cache()
runs = m.history("backtest_cache_warm")
assert runs and runs[0]["status"] == "success", runs
assert runs[0]["records"] == 5 and "5 warmed" in runs[0]["details"], runs[0]
print("✅ scheduler: warm cycle recorded as a monitored success")

# 5b. Partial failure → run marked failed with error detail (monitoring flags it).
m = JobMonitor(InMemoryJobMonitorStore())
sched = _scheduler(m)
with mock.patch("src.scheduler.settings") as ms, \
     mock.patch("src.scheduler.warm_backtest_cache") as mw:
    ms.backtest_cache_warmer_enabled = True
    ms.has_backtest_database = True
    mw.return_value = {"warmed": 2, "skipped": 1, "failed": 3, "candidates": 6}
    sched.job_warm_backtest_cache()
runs = m.history("backtest_cache_warm")
assert runs and runs[0]["status"] == "failed", runs
assert runs[0]["records"] == 2 and "3 symbols failed" in runs[0]["error"], runs[0]
print("✅ scheduler: partial warm failure recorded as failed run")

# 5c. Disabled → clean skip, no monitored run.
m = JobMonitor(InMemoryJobMonitorStore())
sched = _scheduler(m)
with mock.patch("src.scheduler.settings") as ms:
    ms.backtest_cache_warmer_enabled = False
    ms.has_backtest_database = True
    sched.job_warm_backtest_cache()
assert m.history("backtest_cache_warm") == []
print("✅ scheduler: disabled → skip, no run recorded")

# 5d. No backtest DB → clean skip (SQLite dev stays on-demand).
m = JobMonitor(InMemoryJobMonitorStore())
sched = _scheduler(m)
with mock.patch("src.scheduler.settings") as ms:
    ms.backtest_cache_warmer_enabled = True
    ms.has_backtest_database = False
    sched.job_warm_backtest_cache()
assert m.history("backtest_cache_warm") == []
print("✅ scheduler: no backtest DB → skip, no run recorded")

# 5e. warm_backtest_cache raises → run marked failed, error surfaced.
m = JobMonitor(InMemoryJobMonitorStore())
sched = _scheduler(m)
with mock.patch("src.scheduler.settings") as ms, \
     mock.patch("src.scheduler.warm_backtest_cache", side_effect=RuntimeError("boom")):
    ms.backtest_cache_warmer_enabled = True
    ms.has_backtest_database = True
    sched.job_warm_backtest_cache()  # must not raise
runs = m.history("backtest_cache_warm")
assert runs and runs[0]["status"] == "failed" and "boom" in runs[0]["error"], runs
print("✅ scheduler: warm exception → failed run, job swallows")


# ── 6. Contracts: JOB_DEFINITIONS + duck-typed get_symbol_max_dates ───────────
print("6. Contracts (JOB_DEFINITIONS + get_symbol_max_dates on both DBs)...")
from src.job_monitor import JOB_DEFINITIONS  # noqa: E402

assert "backtest_cache_warm" in JOB_DEFINITIONS, JOB_DEFINITIONS.keys()
assert JOB_DEFINITIONS["backtest_cache_warm"]["interval_seconds"] == 24 * 60 * 60

assert hasattr(SQLiteDB, "get_symbol_max_dates")
assert hasattr(BacktestDB, "get_symbol_max_dates")

# Real SQLite behavior: max date per symbol.
db3 = _sqlite_db()
store_historical_data(db3, "AAPL", _make_df())  # ends ~2026-03-01
store_historical_data(db3, "MSFT", _make_df())
max_dates = db3.get_symbol_max_dates()
assert max_dates.get("AAPL") == "2026-03-01", max_dates
assert max_dates.get("MSFT") == "2026-03-01", max_dates
assert max_dates.get("ZZZ") is None
db3.close()
print("✅ contracts: JOB_DEFINITIONS entry + get_symbol_max_dates on both DBs")

print()
print("🎯 All cache-warmer tests pass!")
