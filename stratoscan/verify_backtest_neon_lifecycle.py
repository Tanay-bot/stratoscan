"""Headless lifecycle probe: real BacktestEngine on the real Neon BacktestDB.

Drives the ACTUAL storage swap end-to-end (no test fakes for the units under
test). yfinance is mocked with a deterministic synthetic OHLCV frame so the
probe is network-free and reproducible; the DATABASE is the real dedicated
Neon backtest DB from .env (BACKTEST_DATABASE_URL).

The engine's benchmark symbol is PATCHED to a probe-only value (PRBENCH) so
the probe's synthetic benchmark rows can never collide with a real cached
SPY benchmark — cleanup is two plain deletes and real data is never at risk.

Lifecycle traced, with the transitions that are easy to get wrong:
  1. Engine default: BacktestEngine() with NO injected db must pick BacktestDB
     (production Neon), not SQLite — the whole point of the migration.
  2. First run  : history MISS -> yfinance fetch (mocked, counted) -> stored
     into Neon via store_historical_data -> simulated -> result cached in
     backtest_results.
  3. Second run : history HIT -> get_historical_prices from Neon, NO yfinance
     call (fetch counter must not move), DataFrame rebuilt from stored dict
     rows with the correct columns (Close/Open/High/Low/Volume/raw_close) ->
     simulated again with the same data (fixed seed 42 -> deterministic).
  4. Benchmark  : PRBENCH fetched once on run 1, served from cache on run 2.
  5. Cleanup    : probe rows deleted; historical_prices + backtest_results
     restored to their pre-probe counts (snapshotted at start, so the probe
     is safe against a populated DB).

Asserts every transition; exits non-zero on any failure.

Run: python verify_backtest_neon_lifecycle.py
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

import numpy as np
import pandas as pd
from unittest import mock

from src.config import settings
from src.database import BacktestDB, SQLiteDB
from src.interpreter import InterpretedQuery

PASS = 0

def ok(label: str) -> None:
    global PASS
    PASS += 1
    print(f"  ✅ {label}")

def check(cond: bool, label: str, detail: Any = "") -> None:
    global PASS
    if not cond:
        print(f"  ❌ FAIL: {label} {detail}")
        sys.exit(1)
    ok(label)

PROBE = "PRBE"
BENCH = "PRBENCH"  # probe-only benchmark symbol (never the real SPY)

# ── 0. Preconditions ────────────────────────────────────────────────────────
print("0. Preconditions...")
check(settings.has_backtest_database, "BACKTEST_DATABASE_URL set in .env")
assert "ep-autumn-union" in settings.backtest_database_url, "not the dedicated new DB"
ok("dedicated Neon backtest DB configured")

db = BacktestDB()
db.init_schema()  # idempotent

# Snapshot pre-probe counts so cleanup restores EXACTLY the prior state even
# on a populated DB (never assume the table starts empty).
def _count(table: str) -> int:
    with db._get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            return cur.fetchone()[0]

_pre_hist = _count("historical_prices")
_pre_results = _count("backtest_results")

# ── 1. Engine default selection ────────────────────────────────────────────
print("1. BacktestEngine() with no injected db picks BacktestDB (Neon)...")

from src.backtester import BacktestEngine

with mock.patch.object(SQLiteDB, "init_schema"):
    engine = BacktestEngine(db=None)
check(isinstance(engine._db, BacktestDB), f"engine._db is {type(engine._db).__name__}")
ok("production default = Neon BacktestDB, not SQLite")

# ── 2. Synthetic yfinance frame + fetch counter ────────────────────────────
print("2. Mock yfinance with a counted fetch (network-free, reproducible)...")

def _frame(symbol: str, n: int = 700) -> pd.DataFrame:
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    base = 50 + 20 * np.sin(np.arange(n) / 30) + np.linspace(0, 15, n)
    df = pd.DataFrame({
        "Open": base - 1, "High": base + 2, "Low": base - 2, "Close": base,
        "Volume": np.full(n, 2_000_000, dtype=float),
        "Adj Close": base, "Dividends": 0.0, "Stock Splits": 1.0,
    }, index=idx)
    return df

FRAMES = {PROBE: _frame(PROBE), BENCH: _frame(BENCH)}
fetch_calls: Dict[str, int] = {}

def _fake_fetch(symbol: str, years: int = 10):
    fetch_calls[symbol] = fetch_calls.get(symbol, 0) + 1
    return FRAMES.get(symbol)

iq = InterpretedQuery(
    raw_input="oversold mean-reversion",
    filters=[],
    momentum="neutral",
)

# The engine reads BENCHMARK_SYMBOL at call time from the module namespace,
# so patching it makes the benchmark a probe-only symbol for both runs.
bench_patch = mock.patch("src.backtester.BENCHMARK_SYMBOL", BENCH)
bench_patch.start()

try:
    # ── 3. FIRST run: miss -> fetch -> store -> Neon -> simulate ──────────
    print("3. First run: history MISS -> yfinance fetch -> store to Neon...")

    with mock.patch("src.backtester.fetch_historical_data", side_effect=_fake_fetch):
        res1 = engine.backtest(iq, [PROBE])

    check(PROBE in res1, f"backtest produced a result for {PROBE}")
    r1 = res1[PROBE]
    check(r1.total_trades > 0 and r1.final_value > 0,
          f"valid sim: trades={r1.total_trades}, final=${r1.final_value:,.0f}")
    check(fetch_calls.get(PROBE, 0) == 1,
          f"yfinance called exactly once on miss (got {fetch_calls.get(PROBE, 0)})")
    check(fetch_calls.get(BENCH, 0) >= 1, "PRBENCH benchmark fetched once")
    ok(f"{PROBE} simulated (final=${r1.final_value:,.0f}, return={r1.total_return*100:.1f}%)")

    # History persisted to Neon?
    hist = db.get_historical_prices(PROBE, "1900-01-01", "2999-12-31")
    check(len(hist) >= 600, f"{len(hist)} history rows stored in Neon")
    check(all(isinstance(h, dict) and "close" in h and "raw_close" in h for h in hist[:3]),
          "stored rows are dicts with close + raw_close")
    ok(f"{len(hist)} OHLCV rows now in Neon historical_prices")

    # ── 4. SECOND run: cache HIT -> no yfinance call -> rebuild from Neon ──
    print("4. Second run: history HIT -> reads Neon, NO yfinance call...")

    with mock.patch("src.backtester.fetch_historical_data", side_effect=_fake_fetch):
        res2 = engine.backtest(iq, [PROBE])

    check(PROBE in res2, "second run produced a result")
    check(
        fetch_calls.get(PROBE, 0) == 1,
        f"yfinance NOT called again on cache hit (calls={fetch_calls.get(PROBE, 0)})",
    )
    check(
        fetch_calls.get(BENCH, 0) == 1,
        f"PRBENCH benchmark also served from cache on run 2 (calls={fetch_calls.get(BENCH, 0)})",
    )
    r2 = res2[PROBE]
    check(abs(r2.final_value - r1.final_value) < 0.01,
          f"same data -> same result (final1=${r1.final_value:,.0f} vs final2=${r2.final_value:,.0f})")
    check(r2.buy_hold_total_return is not None,
          "raw_close round-tripped -> buy-and-hold breakdown available on cache hit")
    ok("cache hit: identical sim from Neon-stored rows (incl. raw_close)")

    # ── 5. Result cache in Neon ────────────────────────────────────────────
    print("5. backtest_results persisted to Neon...")

    with db._get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM backtest_results WHERE symbol = %s", (PROBE,))
            cached = cur.fetchone()[0]
    check(cached >= 2, f"{cached} cached result rows for {PROBE} (one per run)")
    ok("results cached in Neon backtest_results")

    print("\n🎯 NEON BACKTEST LIFECYCLE PROBE PASSED — %d assertions" % PASS)
finally:
    # ── 6. Cleanup ALWAYS runs (pass OR fail) so a failed assertion can
    # never strand probe rows in the real Neon database. Two plain deletes:
    # PRBE (probe symbol) + PRBENCH (probe-only benchmark). Real SPY cache
    # is never touched because the benchmark symbol was patched to PRBENCH.
    bench_patch.stop()
    with db._get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM historical_prices WHERE symbol IN (%s, %s)", (PROBE, BENCH))
            cur.execute("DELETE FROM backtest_results WHERE symbol = %s", (PROBE,))
        conn.commit()
    left = db.get_historical_prices(PROBE, "1900-01-01", "2999-12-31")
    check(len(left) == 0, "probe history removed")
    check(_count("historical_prices") == _pre_hist,
          f"historical_prices restored to pre-probe state ({_pre_hist} rows)")
    check(_count("backtest_results") == _pre_results,
          f"backtest_results restored to pre-probe state ({_pre_results} rows)")
    ok("Neon backtest DB restored exactly as found (real SPY cache untouched)")
    db.close()
    print()
    print("Cleanup complete — probe data removed")
