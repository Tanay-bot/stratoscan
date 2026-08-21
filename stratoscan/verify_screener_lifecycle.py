"""Headless lifecycle probe for the screener systematic-debugging fixes.

Drives the REAL code paths (no test fakes for the units under test):

  1. FETCH SIDE  : fetch_stock_profile() converts Finnhub MILLIONS → dollars
  2. INTERPRET   : _convert_to_filter_criteria() emits BOTH min+max bounds
  3. SCREEN      : screen()'s base loop merges same-column criteria and
                   converts billions → dollars at the DB boundary
  4. REAL SQL    : the exact db_filters screen() produced compile through
                   the REAL PostgresDB._build_filter_where() into correct
                   parameterized SQL (no DB connection required)
  5. END-TO-END  : a full Finnhub-shaped record survives fetch → store →
                   screen → match lifecycle
  6. STRING COERCE: LLM emitting market_cap as a string still converts

Asserts every expected state transition; exits non-zero on any failure.

Run: python verify_screener_lifecycle.py
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from typing import Any, Dict, List, Optional
from unittest import mock

from src.data_fetcher import fetch_stock_profile
from src.interpreter import (
    ComparisonOp,
    FilterCriterion,
    InterpretedQuery,
    _convert_to_filter_criteria,
)
from src.screener import StockScreener
from tests.fakes import FakeQualityDB

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

# ── 1. FETCH SIDE: Finnhub millions → dollars at the source ────────────────
print("1. fetch_stock_profile: Finnhub MILLIONS → canonical DOLLARS")

class _FakeClient:
    def company_profile2(self, symbol: str = ""):
        return {
            "name": "FakeCorp", "marketCapitalization": 2500,  # $2.5B in millions
            "peRatio": 15.0, "epsTTM": 2.0, "dividendYield": 1.5,
            "beta": 1.1, "high52": 100.0, "low52": 50.0,
            "sector": "Tech", "industry": "Software",
        }

class _FakePool:
    def next(self):
        return _FakeClient()

with mock.patch("src.data_fetcher._api_call_with_retry", side_effect=lambda fn: fn()):
    prof = fetch_stock_profile(_FakePool(), "FAKE")

check(
    prof["market_cap"] == 2_500_000_000,
    f"market_cap stored in DOLLARS ({prof['market_cap']:,})",
)
check(prof["market_cap"] == int(2500 * 1_000_000), "conversion math exact")

# Missing / non-positive market cap → None (completeness guard will exclude)
class _NoCapClient:
    def company_profile2(self, symbol: str = ""):
        return {"name": "NoCap", "marketCapitalization": 0}

class _NoCapPool:
    def next(self):
        return _NoCapClient()

with mock.patch("src.data_fetcher._api_call_with_retry", side_effect=lambda fn: fn()):
    prof0 = fetch_stock_profile(_NoCapPool(), "NOCAP")
check(prof0["market_cap"] is None, "marketCapitalization=0 → market_cap=None (no crash)")

# ── 2. INTERPRET: min+max pair survives translation ─────────────────────────
print("2. interpreter: both bounds emitted for a min+max pair")
crit = _convert_to_filter_criteria({
    "min_market_cap_billions": 0.3, "max_market_cap_billions": 2.0,
})
pairs = {(c.operator.value, c.value) for c in crit}
check(("gte", 0.3) in pairs and ("lte", 2.0) in pairs,
      f"both market_cap bounds present: {sorted(pairs)}")

# ── 3. SCREEN boundary: billions → dollars + same-column merge ─────────────
print("3. screen(): merged bounds + billions→dollars conversion at DB boundary")

# The filter-execution stand-in reuses tests.fakes.FakeQualityDB
# (byte-identical semantics to database._column_conditions — never fork it),
# extended ONLY with last_filters capture so the probe can assert the exact
# filter dict that reached the DB layer.
class LifecycleDB(FakeQualityDB):
    def __init__(self, records: List[Dict[str, Any]]):
        super().__init__(records)
        self.last_filters: Dict[str, Any] = {}

    def count_stocks(self, filters: Optional[Dict[str, Any]] = None) -> int:
        filters = filters or {}
        self.last_filters = filters
        return super().count_stocks(filters)

    def query_stocks(self, filters=None, limit=50, offset=0,
                     sort_by="market_cap", sort_desc=True):
        filters = filters or {}
        self.last_filters = filters
        return super().query_stocks(filters, limit, offset, sort_by, sort_desc)


def _record(sym: str, cap: int, price: float = 50.0, **extra) -> Dict[str, Any]:
    return {
        "symbol": sym, "company_name": f"{sym} Corp", "sector": "Technology",
        "current_price": price, "sma_50": price * 0.9, "sma_200": price * 0.8,
        "high_52w": price * 1.2, "low_52w": price * 0.6, "pe_ratio": 12.0,
        "rsi_14": 55.0, "market_cap": cap,
        "updated_at": "2026-08-01T12:00:00+00:00", **extra,
    }

# Canonical storage: DOLLARS. Small-cap query = 0.3B ≤ cap ≤ 2.0B.
db = LifecycleDB([
    _record("TINY", 100_000_000),      # $100M  → below min 0.3B
    _record("MID", 500_000_000),        # $500M  → inside 0.3B..2.0B ✓
    _record("HUGE", 5_000_000_000),     # $5B    → above max 2.0B
])

q_small = InterpretedQuery(
    raw_input="small cap",
    filters=[
        FilterCriterion("market_cap", ComparisonOp.GTE, 0.3),
        FilterCriterion("market_cap", ComparisonOp.LTE, 2.0),
    ],
)
screener = StockScreener(db=db)
r = screener.screen(q_small, limit=10)

check(
    {x.symbol for x in r.results} == {"MID"},
    f"BOTH bounds enforced → {[x.symbol for x in r.results]}",
)

# The db_filters that reached the DB must carry the CONVERTED dollar values.
mc = db.last_filters["market_cap"]
if not isinstance(mc, list):
    mc = [mc]
check(
    ("gte", 300_000_000.0) in mc and ("lte", 2_000_000_000.0) in mc,
    f"billions→dollars at DB boundary: {mc}",
)
check(("gte", 0.3) not in mc and ("lte", 2.0) not in mc,
      "raw billions never leak into the DB filter")

# applied echo keeps the human-readable BILLIONS values.
check(
    any("market_cap ≥ 0.3" in f for f in r.applied_filters)
    and any("market_cap ≤ 2.0" in f for f in r.applied_filters),
    f"echo keeps billions: {[f for f in r.applied_filters if 'market_cap' in f]}",
)

# ── 4. REAL SQL compiler: screen()'s filters → real parameterized SQL ──────
print("4. real PostgresDB._build_filter_where compiles screen()'s filters")

from src.database import PostgresDB

pg = PostgresDB.__new__(PostgresDB)  # no connection needed for the builder
where, params = pg._build_filter_where(db.last_filters)
check(
    "market_cap >= %s" in where and "market_cap <= %s" in where,
    f"compiled WHERE: {where}",
)
check(
    300_000_000.0 in params and 2_000_000_000.0 in params,
    f"compiled params (dollars): {params}",
)
# The completeness guards screen() adds must compile too.
where2, params2 = pg._build_filter_where({"current_price": ("neq", None),
                                          "market_cap": ("neq", None)})
check("IS NOT NULL" in where2, f"NULL-safe guards compile: {where2}")

# ── 5. END-TO-END lifecycle: Finnhub shape → fetch → store → screen ────────
print("5. full lifecycle: Finnhub response → fetch → store → screen → match")

# Simulate exactly what the refresh pipeline stores (fetch output, in dollars)
# — same mock context as section 1 so the retry wrapper is bypassed explicitly.
with mock.patch("src.data_fetcher._api_call_with_retry", side_effect=lambda fn: fn()):
    stored = fetch_stock_profile(_FakePool(), "AAPL")
stored.update({"current_price": 150.0, "price_change_pct": 1.2, "volume": 5_000_000,
               "rsi_14": 60.0, "sma_50": 145.0, "sma_200": 140.0,
               "high_52w": 160.0, "low_52w": 100.0,
               "updated_at": "2026-08-01T12:00:00+00:00"})

db2 = LifecycleDB([stored])
q2 = InterpretedQuery(
    raw_input="tech under 3B",
    filters=[
        FilterCriterion("sector", ComparisonOp.EQ, "Tech"),
        FilterCriterion("market_cap", ComparisonOp.LTE, 3.0),  # ≤ $3B
    ],
)
r2 = StockScreener(db=db2).screen(q2, limit=10)
check(
    [x.symbol for x in r2.results] == ["AAPL"],
    f"AAPL ($2.5B) matches '≤ $3B' with sector Tech → {[x.symbol for x in r2.results]}",
)
check(r2.results[0].market_cap == 2_500_000_000,
      f"result carries dollar cap: {r2.results[0].market_cap:,}")

# ── 6. STRING coercion: LLM emits market_cap as a string ───────────────────
print("6. defensive float coercion for string market_cap values")

q_str = InterpretedQuery(
    raw_input="string cap",
    filters=[FilterCriterion("market_cap", ComparisonOp.LTE, "2")],  # str!
)
db3 = LifecycleDB([
    _record("OK1", 1_000_000_000),
    _record("BIG", 3_000_000_000),
])
r3 = StockScreener(db=db3).screen(q_str, limit=10)
check(
    {x.symbol for x in r3.results} == {"OK1"},
    f"string '2' coerced to 2e9 dollars → {[x.symbol for x in r3.results]}",
)

print()
print(f"🎯 LIFECYCLE PROBE PASSED — {PASS} assertions")
