"""Day 14 — volume is real, not advertised-and-empty.

Finnhub's /quote has no volume field, so `stock_master.volume` was NULL for
every stock while /meta advertised a volume filter and sort. Now:
  - compute_indicators() takes the latest session's volume (and the 63-day
    average) from the daily bars technicals already download;
  - backfill_volume_from_history() copies both from the backtest DB's stored
    bars — the real SQL, run here on SQLite (the same text runs on Postgres);
  - the quote record no longer pretends to carry a volume.

Run: python -m tests.test_volume_backfill
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("JWT_SECRET", "volume-dummy-secret")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src import cache_warmer, data_fetcher  # noqa: E402
from src.database import SQLiteDB  # noqa: E402

# 1. compute_indicators: last bar's volume + the 63-session mean.
idx = pd.bdate_range("2025-01-02", periods=120)
vol = np.arange(1, 121) * 1000
df = pd.DataFrame({"Open": 10.0, "High": 11.0, "Low": 9.0, "Close": 10.0, "Volume": vol}, index=idx)
ind = data_fetcher.compute_indicators(df)
assert ind["volume"] == 120_000, ind["volume"]
assert ind["avg_volume"] == int(round(vol[-63:].mean())), ind["avg_volume"]
assert "volume" in data_fetcher.TECH_COLUMNS
assert data_fetcher.compute_indicators(df.drop(columns=["Volume"]))["volume"] is None
print("✅ compute_indicators: volume = the latest session, avg_volume = trailing 63 sessions")

# 2. The real SQL on SQLite: recent symbols only, ≥5 bars, onto existing rows.
tmp = tempfile.mkdtemp()
bt = SQLiteDB(db_path=os.path.join(tmp, "bt.db"))
bt.init_schema()
today = date(2026, 10, 5)


def bars(sym, n, last_day, base):
    days = [last_day - timedelta(days=i) for i in range(n)][::-1]
    # The store's column order: symbol, date, open, high, low, close, raw_close,
    # volume, dividends, stock_splits.
    return [(sym, d.isoformat(), 1.0, 1.0, 1.0, 1.0, 1.0, base + i, 0.0, 0.0) for i, d in enumerate(days)]


rows = bars("FRESH", 90, today, 1000) + bars("STALE", 90, today - timedelta(days=60), 5000) \
    + bars("THIN", 3, today, 9000)
bt.insert_historical_prices(rows)


class Main:
    def __init__(self):
        self.written = None

    def update_volumes(self, r):
        self.written = r
        return len(r)


main = Main()
stats = cache_warmer.backfill_volume_from_history(backtest_db=bt, screener_db=main, today=today.isoformat())
got = {s: (v, a) for s, v, a in main.written}
assert set(got) == {"FRESH"}, got  # STALE: no recent bar; THIN: < 5 recent bars
fresh_vols = [1000 + i for i in range(90)]
assert got["FRESH"][0] == fresh_vols[-1]
# The average is over the trailing 63 sessions, not just the freshness window.
assert got["FRESH"][1] == int(round(float(np.mean(fresh_vols[-63:])))), got
assert stats == {"symbols": 1, "updated": 1}
print("✅ backfill: the latest volume + average for symbols with recent bars; stale and thin symbols skipped")

# 3. The quote record no longer carries a fake (always-None) volume.
src = open(data_fetcher.__file__).read()
assert '"volume": quote.get("v")' not in src
print("✅ the quote record no longer carries an always-empty volume")
# 4. A volume filter says how much of the universe it can see.
from datetime import datetime, timezone  # noqa: E402

from src.interpreter import ComparisonOp, FilterCriterion, InterpretedQuery  # noqa: E402
from src.screener import StockScreener  # noqa: E402
from tests.fakes import FakeQualityDB  # noqa: E402

now_ts = datetime.now(timezone.utc).isoformat()
recs = [{"symbol": f"S{i}", "company_name": "x", "sector": "Technology", "current_price": 10.0,
         "market_cap": 1e9, "updated_at": now_ts, "volume": (2_000_000 if i < 3 else None)}
        for i in range(10)]


def vq(filters):
    return InterpretedQuery(raw_input="t", filters=filters, sector=None, industry=None, momentum=None)


rep = StockScreener(db=FakeQualityDB(recs)).screen(vq([FilterCriterion("volume", ComparisonOp.GTE, 1_000_000)]))
assert rep.total_matches == 3, rep.total_matches
assert rep.data_quality["partial_coverage"] == {"volume": {"known": 3, "total": 10}}, rep.data_quality
assert "known for 3 of 10 stocks" in rep.data_quality["message"], rep.data_quality
rep2 = StockScreener(db=FakeQualityDB(recs)).screen(vq([FilterCriterion("market_cap", ComparisonOp.GTE, 0.5)]))
assert "partial_coverage" not in rep2.data_quality, rep2.data_quality
print("✅ a volume filter reports its coverage (3 of 10); other filters say nothing")

print("\nALL VOLUME CHECKS PASSED")
