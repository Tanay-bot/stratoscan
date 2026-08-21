"""Hermetic tests for the Tier 3 historical OHLCV backfill (src/backfill.py).

Run: python -m tests.test_backfill

Drives the REAL ``backfill_history`` planner/fetcher/store orchestration
with faked primitives (no network, no DB):

  1. Explicit symbols are deduped + uppercased.
  2. Missing symbols get fetched + stored; fresh symbols are skipped
     (resumability).
  3. Failed fetches are isolated (logged, cycle continues).
  4. Row counts are reported and the sample cross-check runs.
  5. The tool REFUSES to run without BACKTEST_DATABASE_URL (never writes
     to the local SQLite dev fallback).
  6. CLI wrapper returns a stats dict and closes the owned DB.
"""

from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["REDIS_RATE_LIMIT_ENABLED"] = "false"

# Hermetic: point the backfill tool at a dummy backtest DB so its
# "BACKTEST_DATABASE_URL not set" config guard doesn't fire BEFORE the
# fake BacktestDB below is even constructed (mirrors test_api_dirty_data's
# settings redirection). The patched FakeBacktestDB serves every call.
from src.config import settings  # noqa: E402
settings.backtest_database_url = "postgresql://ci-dummy-stratoscan"


class FakeBacktestDB:
    """Duck-typed stand-in for BacktestDB (the real contract)."""

    def __init__(self, existing=None) -> None:
        # symbol -> list of (symbol, date) tuples
        self.rows: dict = {}
        self.closed = False
        self.init_called = False
        for sym, dates in (existing or {}).items():
            self.rows[sym] = [(sym, d) for d in dates]

    def init_schema(self) -> None:
        self.init_called = True

    def insert_historical_prices(self, records) -> int:
        for rec in records:
            sym, date = rec[0], rec[1]
            self.rows.setdefault(sym, []).append((sym, date))
        return len(records)

    def get_symbol_max_dates(self) -> dict:
        return {sym: items[-1][1] for sym, items in self.rows.items() if items}

    def count_prices(self, symbol=None) -> int:
        if symbol:
            return len(self.rows.get(symbol, []))
        return sum(len(v) for v in self.rows.values())

    def close(self) -> None:
        self.closed = True


def _fake_fetch(symbol: str, years: int = 10):
    """Deterministic synthetic OHLCV frame matching the real shape.

    Bars END at ``now`` so the resumability test works: after a first
    backfill, the newest cached bar is recent and a re-run skips it. If
    bars ended in 2017, everything would look stale forever relative to
    a 2026 "today" and the skip logic could never trigger.
    """
    import pandas as pd

    dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=years * 250)
    n = len(dates)
    import numpy as np

    df = pd.DataFrame(
        {
            "Open": np.linspace(10, 100, n),
            "High": np.linspace(11, 101, n),
            "Low": np.linspace(9, 99, n),
            "Close": np.linspace(10.5, 100.5, n),
            "Adj Close": np.linspace(10.5, 100.5, n),
            "Volume": np.full(n, 1_000_000),
            "Dividends": np.zeros(n),
            "Stock Splits": np.ones(n),
        },
        index=dates,
    )
    return df


def main() -> int:
    from src import backfill
    from src.backfill import backfill_history, build_backfill_history

    results = []
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        results.append((name, cond, detail))
        if not cond:
            ok = False

    # ── 1. Explicit symbols → deduped + uppercased ─────────────────────
    print("\n1. Symbol normalization")
    from src.backtester import store_historical_data as real_store

    db = FakeBacktestDB()
    with mock.patch.object(backfill, "BacktestDB", return_value=db):
        stats = backfill_history(
            symbols=["aapl", "AAPL", " msft "],
            years=2,
            delay=0,
            today="2026-01-01",
            fetch_fn=_fake_fetch,
            store_fn=real_store,
            verify=False,
        )
    check("both unique symbols backfilled", stats["backfilled"] == 2, str(stats))
    check("no failed", stats["failed"] == 0, str(stats))
    check("rows stored > 0", stats["rows"] > 0, str(stats))
    check("per-symbol counts", all(
        v.get("total_for_symbol", 0) > 0
        for v in stats["per_symbol"].values()
    ), str(stats["per_symbol"]))

    # ── 2. Resumability: fresh symbols are skipped ──────────────────────
    print("\n2. Resumable (fresh skipped)")
    # Re-run with the same DB: everything is now 'fresh' (newest bar is
    # recent), so backfilled must be 0 and skipped 2.
    from src.backtester import store_historical_data as real_store

    with mock.patch.object(backfill, "BacktestDB", return_value=db):
        stats2 = backfill_history(
            symbols=["AAPL", "MSFT"],
            years=2,
            delay=0,
            today="2026-01-01",
            fetch_fn=_fake_fetch,
            store_fn=real_store,
            verify=False,
        )
    check("second run backfills nothing", stats2["backfilled"] == 0, str(stats2))
    check("second run skips all", stats2["skipped"] == 2, str(stats2))

    # ── 3. Failed fetch is isolated ────────────────────────────────────
    print("\n3. Failure isolation")
    db3 = FakeBacktestDB()

    def fetch_half(symbol, years=10):
        if symbol == "BROKEN":
            return None
        return _fake_fetch(symbol, years)

    from src.backtester import store_historical_data as real_store

    with mock.patch.object(backfill, "BacktestDB", return_value=db3):
        stats3 = backfill_history(
            symbols=["GOOD", "BROKEN"],
            years=2,
            delay=0,
            today="2026-01-01",
            fetch_fn=fetch_half,
            store_fn=real_store,
            verify=False,
        )
    check("good symbol backfilled", "GOOD" in stats3["symbols"], str(stats3))
    check("broken symbol flagged failed", stats3["failed"] == 1, str(stats3))
    check("broken not in backfilled list", "BROKEN" not in stats3["symbols"], str(stats3))

    # ── 4. Sample verification runs + reports ──────────────────────────
    print("\n4. Verification")
    db4 = FakeBacktestDB()
    from src.backtester import store_historical_data as real_store

    with mock.patch.object(backfill, "BacktestDB", return_value=db4):
        stats4 = backfill_history(
            symbols=["AAPL"],
            years=2,
            delay=0,
            today="2026-01-01",
            fetch_fn=_fake_fetch,
            store_fn=real_store,
            verify=True,
        )
    v = stats4.get("verification", {})
    check("verification present", v.get("symbol") == "AAPL", str(v))
    check("verification ok (fresh re-download matches)", v.get("ok") is True, str(v))

    # ── 5. Refuses to run without Neon backtest DB ─────────────────────
    print("\n5. Neon-only enforcement")
    # has_backtest_database is a pydantic property — patch the CLASS
    # property (new_callable), never patch.object on the instance (that
    # trips pydantic's __delattr__).
    with mock.patch.object(
        type(backfill.settings), "has_backtest_database", new_callable=mock.PropertyMock, return_value=False
    ):
        stats5 = backfill_history(
            symbols=["AAPL"], years=2, verify=False,
        )
    check("refuses without BACKTEST_DATABASE_URL", "error" in stats5, str(stats5))
    check("error mentions Neon", "BACKTEST_DATABASE_URL" in stats5.get("error", ""), str(stats5))

    # ── 6. CLI wrapper + owned-DB close ────────────────────────────────
    print("\n6. CLI wrapper")
    db6 = FakeBacktestDB()
    with mock.patch.object(backfill, "BacktestDB", return_value=db6):
        stats6 = build_backfill_history(
            symbols=["NVDA"], years=2, verify=False,
        )
    check("wrapper returns stats", stats6.get("backfilled") == 1, str(stats6))
    check("owned DB closed", db6.closed is True)

    # ── Results ────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    for name, cond, detail in results:
        icon = "✅" if cond else "❌"
        print(f"  {icon}  {name}: {detail}")
    print("\n" + "=" * 60)
    if ok:
        print("✅  ALL CHECKS PASSED")
    else:
        print("❌  SOME CHECKS FAILED")
    print("=" * 60)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
