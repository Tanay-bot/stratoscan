"""Hermetic quality tests for the screener Tiers 1-3 — no network, no DB.

Run: python -m tests.test_screener_quality

Covers:
  Tier 1 (credibility):
    1. Trend filters translate into correct SQL semantics: price vs
       SMA50/SMA200, golden/death cross, 52w proximity (frac).
    2. Momentum is a REAL trend composite (price > SMA50), not RSI-only,
       and never overwrites an existing price filter (precedence).
    3. sort_by/sort_desc thread through to the DB query and the response.
    4. applied_filters echo + data_quality notes in the report.
  Tier 3 (robustness):
    5. Composite score is 0-100, z-scored against the match set, with
       per-factor components.
    6. is_stale / data_age_minutes staleness flags.
    7. why_matched explanation list.
    8. Minimum-completeness guard (rows missing price+cap are excluded).
  API surface:
    9. /screen accepts sort params, returns new fields; /screen/export
       returns CSV.

The live PostgresDB is replaced with FakeQualityDB, which mirrors the
EXACT filter semantics of database._build_filter_where (ranges,
column-compare ops, frac ops, IN, exact, NULL guards) over an in-memory
list, plus aggregate_stats with REAL mean/std — so the score math and
filter translation are exercised, not stubbed.
"""

from __future__ import annotations

import csv
import io
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest import mock

os.environ["RATE_LIMIT_ENABLED"] = "false"

from src.interpreter import (
    ComparisonOp,
    FilterCriterion,
    InterpretedQuery,
)
from src.screener import StockScreener
from tests.fakes import FakeLLMRouter, FakeQualityDB


# ── Fixtures ────────────────────────────────────────────────────────────────
def _fresh_ts(minutes_ago: int = 2) -> str:
    """A UTC ISO timestamp a few minutes in the past (never ages out of the
    screener's freshness guard, unlike a hardcoded date)."""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def _record(symbol: str, price: float, sma50: float, sma200: float,
            high52: float, low52: float, pe: float, rsi: float,
            cap: int, **extra) -> Dict[str, Any]:
    return {
        "symbol": symbol, "company_name": f"{symbol} Corp", "sector": "Technology",
        "current_price": price, "sma_50": sma50, "sma_200": sma200,
        "high_52w": high52, "low_52w": low52, "pe_ratio": pe, "rsi_14": rsi,
        "market_cap": cap, "updated_at": _fresh_ts(),
        **extra,
    }


def _query(**kwargs) -> InterpretedQuery:
    base = dict(
        raw_input="test", filters=[], sector=None, industry=None, momentum=None,
        above_sma_50=None, below_sma_50=None, above_sma_200=None,
        below_sma_200=None, golden_cross=None, death_cross=None,
        near_52w_high=None, near_52w_low=None, macd_bullish=None,
        bollinger_breakout=None, bollinger_oversold=None,
    )
    base.update(kwargs)
    return InterpretedQuery(**base)


# ── 1. Trend filters → SQL semantics ───────────────────────────────────────
print("1. Tier 1 trend filters translate correctly...")

db = FakeQualityDB([
    # Uptrend: price above both SMAs, near 52w high
    _record("UP1", 100.0, 95.0, 90.0, 102.0, 70.0, 12.0, 65.0, 8_000_000_000),
    # Downtrend: price below both SMAs
    _record("DOWN1", 40.0, 55.0, 60.0, 90.0, 30.0, 20.0, 35.0, 2_000_000_000),
    # Golden cross, price above 50 but below 200 (cross between smas)
    _record("GCROSS", 80.0, 70.0, 100.0, 110.0, 50.0, 15.0, 55.0, 5_000_000_000),
    # Near 52w low
    _record("NEARLOW", 31.0, 45.0, 50.0, 80.0, 30.0, 25.0, 30.0, 1_000_000_000),
])
screener = StockScreener(db=db)

# above_sma_50 → only UP1, GCROSS (price > sma50)
r = screener.screen(_query(above_sma_50=True), limit=10)
assert {x.symbol for x in r.results} == {"UP1", "GCROSS"}, r.results
assert any("price > SMA50" in f for f in r.applied_filters), r.applied_filters
print(f"✅ above_sma_50 → {[x.symbol for x in r.results]}")

# golden_cross → sma_50 > sma_200: UP1 only
r = screener.screen(_query(golden_cross=True), limit=10)
assert {x.symbol for x in r.results} == {"UP1"}, r.results
assert any("golden cross" in f for f in r.applied_filters), r.applied_filters
print(f"✅ golden_cross → {[x.symbol for x in r.results]}")

# near_52w_high (price >= high52*0.95): UP1 (100 >= 96.9) yes; NEARLOW no
r = screener.screen(_query(near_52w_high=True), limit=10)
assert {x.symbol for x in r.results} == {"UP1"}, r.results
print(f"✅ near_52w_high → {[x.symbol for x in r.results]}")

# near_52w_low (price <= low52*1.05): NEARLOW (31 <= 31.5) yes
r = screener.screen(_query(near_52w_low=True), limit=10)
assert {x.symbol for x in r.results} == {"NEARLOW"}, r.results
print(f"✅ near_52w_low → {[x.symbol for x in r.results]}")

# ── 2. Momentum = real trend composite, with precedence ──────────────────
print("2. Tier 1 momentum composite + precedence...")

# bullish momentum alone → price > SMA50 (UP1, GCROSS)
r = screener.screen(_query(momentum="bullish"), limit=10)
assert {x.symbol for x in r.results} == {"UP1", "GCROSS"}, r.results
assert any("momentum: price > SMA50" in f for f in r.applied_filters), r.applied_filters
print(f"✅ momentum=bullish → price > SMA50 → {[x.symbol for x in r.results]}")

# near_52w_high + momentum=bullish: momentum must NOT clobber the proximity
# filter (UP1 is the only one near its 52w high AND above SMA50)
r = screener.screen(_query(near_52w_high=True, momentum="bullish"), limit=10)
assert {x.symbol for x in r.results} == {"UP1"}, r.results
print(f"✅ near_52w_high + momentum → both applied → {[x.symbol for x in r.results]}")

# ── 3. Sorting ─────────────────────────────────────────────────────────────
print("3. Tier 1 sort_by / sort_desc...")

r = screener.screen(_query(), limit=10, sort_by="market_cap", sort_desc=True)
assert [x.symbol for x in r.results] == ["UP1", "GCROSS", "DOWN1", "NEARLOW"], \
    [x.symbol for x in r.results]
r = screener.screen(_query(), limit=10, sort_by="market_cap", sort_desc=False)
assert [x.symbol for x in r.results][0] == "NEARLOW", [x.symbol for x in r.results]
r = screener.screen(_query(), limit=10, sort_by="current_price", sort_desc=True)
assert [x.symbol for x in r.results][0] == "UP1", [x.symbol for x in r.results]
assert r.sort_by == "current_price" and r.sort_desc is True
print("✅ market_cap desc/asc + current_price desc sort verified")

# Unknown sort falls back to market_cap (no crash)
r = screener.screen(_query(), limit=10, sort_by="; DROP TABLE", sort_desc=True)
assert r.sort_by == "market_cap", r.sort_by
print("✅ unknown sort_by → safe fallback to market_cap")

# ── 4. applied_filters + data_quality echo ────────────────────────────────
print("4. Tier 1 echo: applied_filters + data_quality...")

r = screener.screen(_query(golden_cross=True, momentum="bullish"), limit=10)
assert any("golden cross" in f for f in r.applied_filters), r.applied_filters
assert any("momentum" in f for f in r.applied_filters), r.applied_filters
assert any("completeness guard" in f for f in r.applied_filters), r.applied_filters
assert isinstance(r.data_quality, dict)
print(f"✅ applied_filters={r.applied_filters}")
print(f"✅ data_quality={r.data_quality}")

# ── 5. Composite score ─────────────────────────────────────────────────────
print("5. Tier 3 composite score (0-100, z-scored against match set)...")

db5 = FakeQualityDB([
    # Cheapest + strongest momentum + most profitable → highest score
    _record("BEST", 50.0, 45.0, 40.0, 52.0, 30.0, 8.0, 75.0, 10_000_000_000,
            roe_ttm=30.0, net_margin_ttm=20.0, eps_growth_ttm=40.0,
            price_change_pct=3.0, beta=0.8),
    # Mid
    _record("MID", 50.0, 48.0, 47.0, 60.0, 40.0, 15.0, 55.0, 5_000_000_000,
            roe_ttm=15.0, net_margin_ttm=10.0, eps_growth_ttm=15.0,
            price_change_pct=0.5, beta=1.2),
    # Expensive + weak momentum + low quality
    _record("WORST", 50.0, 55.0, 60.0, 70.0, 45.0, 40.0, 40.0, 2_000_000_000,
            roe_ttm=4.0, net_margin_ttm=2.0, eps_growth_ttm=-10.0,
            price_change_pct=-2.0, beta=2.0),
])
s5 = StockScreener(db5)
r5 = s5.screen(_query(), limit=10, sort_by="score", sort_desc=True)
scores = {x.symbol: x.score for x in r5.results}
assert scores["BEST"] >= scores["MID"] >= scores["WORST"], scores
for x in r5.results:
    assert 0 <= x.score <= 100, f"{x.symbol} score {x.score} out of range"
    assert isinstance(x.score_components, dict), x.symbol
assert r5.results[0].symbol == "BEST", r5.results
print(f"✅ score ranking: {scores}")
print(f"✅ BEST components: {r5.results[0].score_components}")

# ── 6. Staleness flags + freshness exclusion ───────────────────────────────
print("6. Tier 3 staleness flags + freshness guard...")

_now = datetime.now(timezone.utc)
fresh_stamp = (_now - timedelta(minutes=2)).isoformat()
flag_stamp = (_now - timedelta(days=1)).isoformat()    # >15min → flagged, but within the guard window
gone_stamp = (_now - timedelta(days=12)).isoformat()   # beyond the guard → EXCLUDED entirely

db6 = FakeQualityDB([
    _record("FRESH", 100.0, 95.0, 90.0, 110.0, 70.0, 12.0, 60.0, 5_000_000_000,
            updated_at=fresh_stamp),
    _record("FLAGGED", 60.0, 65.0, 70.0, 90.0, 40.0, 18.0, 48.0, 2_000_000_000,
            updated_at=flag_stamp),
    _record("GONE", 50.0, 55.0, 60.0, 90.0, 30.0, 20.0, 45.0, 1_000_000_000,
            updated_at=gone_stamp),
])
s6 = StockScreener(db6)
r6 = s6.screen(_query(), limit=10)
by_sym = {x.symbol: x for x in r6.results}
assert "GONE" not in by_sym, "row older than freshness guard must be excluded entirely"
assert by_sym["FRESH"].is_stale is False, "fresh row flagged stale"
assert by_sym["FLAGGED"].is_stale is True, "old-but-within-window row not flagged"
assert any("freshness guard" in f for f in r6.applied_filters), r6.applied_filters
assert r6.data_quality.get("stale_exclusion_days") == 7, r6.data_quality
print(f"✅ FRESH age={by_sym['FRESH'].data_age_minutes:.0f}m stale={by_sym['FRESH'].is_stale}")
print(f"✅ FLAGGED age={by_sym['FLAGGED'].data_age_minutes:.0f}m stale={by_sym['FLAGGED'].is_stale}")
print(f"✅ GONE (12d old) excluded; guard={r6.data_quality.get('stale_exclusion_days')}d")

# ── 7. why_matched ─────────────────────────────────────────────────────────
print("7. Tier 3 why_matched...")

r7 = screener.screen(_query(golden_cross=True), limit=10)
up1 = next(x for x in r7.results if x.symbol == "UP1")
assert up1.why_matched, "why_matched empty"
assert any("golden cross" in w for w in up1.why_matched), up1.why_matched
print(f"✅ UP1 why_matched={up1.why_matched[:4]}")

# ── 8. Completeness guard ──────────────────────────────────────────────────
print("8. Tier 3 minimum-completeness guard...")

db8 = FakeQualityDB([
    _record("OK", 100.0, 95.0, 90.0, 110.0, 70.0, 12.0, 60.0, 5_000_000_000),
    {"symbol": "NOCAP", "company_name": "No Cap", "sector": "Tech",
     "current_price": 100.0, "sma_50": 95.0, "sma_200": 90.0,
     "updated_at": _fresh_ts()},  # no market_cap
])
s8 = StockScreener(db8)
r8 = s8.screen(_query(), limit=10)
assert {x.symbol for x in r8.results} == {"OK"}, r8.results
print(f"✅ rows missing price/cap excluded → {[x.symbol for x in r8.results]}")

# ── 9. API surface: sort params, new fields, CSV export ───────────────────
print("9. API surface (sort, new fields, CSV export)...")

# FakeLLMRouter's default JSON is {"sector": "Technology", "max_pe_ratio": 15}
# — records must pass sector=Technology AND pe_ratio ≤ 15 to show up.
fake_db = FakeQualityDB([
    _record("AAPL", 150.0, 140.0, 135.0, 155.0, 100.0, 12.0, 62.0, 3_000_000_000_000),
    _record("MSFT", 300.0, 310.0, 280.0, 320.0, 200.0, 14.0, 58.0, 2_500_000_000_000),
])
mock.patch("src.screener.PostgresDB", return_value=fake_db).start()
mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()
mock.patch("src.news.get_sentiment_for_screen", return_value={}).start()

from src.api import app
from fastapi.testclient import TestClient

client = TestClient(app)

r = client.post("/screen", json={
    "query": "tech stocks", "limit": 5, "offset": 0,
    "sort_by": "current_price", "sort_desc": True,
})
assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
body = r.json()
assert body["sort_by"] == "current_price"
assert body["sort_desc"] is True
assert body["applied_filters"], "applied_filters empty"
assert "data_quality" in body
first = body["results"][0]
assert first["symbol"] == "MSFT", f"expected MSFT first (price desc): {first['symbol']}"
assert "sma_50" in first and first["sma_50"] == 310.0, "sma_50 not in result"
assert "pct_off_52w_high" in first
assert "score" in first and "why_matched" in first and "is_stale" in first
print(f"✅ /screen sort + new fields → first={first['symbol']}, "
      f"score={first['score']}, sma50={first['sma_50']}")

# Ascending
r = client.post("/screen", json={
    "query": "tech stocks", "limit": 5, "offset": 0,
    "sort_by": "current_price", "sort_desc": False,
})
assert r.json()["results"][0]["symbol"] == "AAPL", "ascending sort wrong"
print("✅ /screen sort_desc=False → ascending")

# CSV export
r = client.post("/screen/export", json={"query": "tech stocks", "limit": 5})
assert r.status_code == 200, f"csv: {r.status_code} {r.text[:200]}"
assert r.headers["content-type"].startswith("text/csv"), r.headers["content-type"]
rows = list(csv.DictReader(io.StringIO(r.text)))
assert len(rows) == 2, f"expected 2 csv rows, got {len(rows)}"
assert {"symbol", "current_price", "score"} <= set(rows[0].keys()), list(rows[0].keys())
assert rows[0]["symbol"] in {"AAPL", "MSFT"}
print(f"✅ /screen/export → {len(rows)} rows, columns include symbol/score")

# ── 10. Interpreter field mapping ──────────────────────────────────────────
print("10. Interpreter: new field mapping...")

from src.interpreter import _convert_to_filter_criteria
criteria = _convert_to_filter_criteria({
    "min_avg_volume": 1_000_000,
    "min_eps_ttm": 2.5,
    "min_ps_ratio": 0.5,
    "max_ev_ebitda": 15.0,
    "min_roe_ttm": 10.0,
    "min_revenue_growth_ttm": 5.0,
})
fields = {c.field: (c.operator.value, c.value) for c in criteria}
assert fields["avg_volume"] == ("gte", 1_000_000), fields
assert fields["eps_ttm"] == ("gte", 2.5), fields
assert fields["ps_ratio"] == ("gte", 0.5), fields
assert fields["ev_ebitda"] == ("lte", 15.0), fields
assert fields["roe_ttm"] == ("gte", 10.0), fields
assert fields["revenue_growth_ttm"] == ("gte", 5.0), fields
print(f"✅ mapped criteria: {fields}")

# ── 11. SYSTEMATIC-DEBUGGING REGRESSIONS ──────────────────────────────────
# Phase 1 root-cause findings: (A) same-column filter overwrite in
# screen()'s base loop (min+max on one field — the dict keeps only the
# last); (B) market-cap unit mismatch (fetch stores Finnhub millions,
# interpreter emits billions, display assumes dollars).
print("11. Regressions: same-column filters + market-cap units...")

from src.interpreter import _convert_to_filter_criteria

# A1. Interpreter must emit BOTH bounds for a min+max pair
crit = _convert_to_filter_criteria({
    "min_market_cap_billions": 0.3, "max_market_cap_billions": 2.0,
})
pairs = {(c.operator.value, c.value) for c in crit}
assert ("gte", 0.3) in pairs, f"min bound missing: {pairs}"
assert ("lte", 2.0) in pairs, f"max bound missing: {pairs}"
print(f"✅ interpreter emits both bounds: {sorted(pairs)}")

# A2. screen() must enforce BOTH bounds (no silent overwrite). Records are
# stored in DOLLARS (canonical): $100M, $500M (passes), $5B.

dbA = FakeQualityDB([
    _record("TINY", 10.0, 9.0, 8.0, 20.0, 5.0, 10.0, 50.0, 100_000_000),
    _record("MID", 50.0, 45.0, 40.0, 60.0, 30.0, 12.0, 55.0, 500_000_000),
    _record("HUGE", 200.0, 150.0, 100.0, 300.0, 80.0, 15.0, 60.0, 5_000_000_000),
])
q_small = InterpretedQuery(
    raw_input="small cap", filters=[
        FilterCriterion("market_cap", ComparisonOp.GTE, 0.3),
        FilterCriterion("market_cap", ComparisonOp.LTE, 2.0),
    ],
)
sA = StockScreener(db=dbA)
rA = sA.screen(q_small, limit=10)
# BUG: values are interpreted in BILLIONS, but with canonical DOLLAR storage
# a small-cap filter of 0.3-2.0 (billions) should become 0.3e9-2e9 dollars.
# The screener boundary converts billions→dollars.
assert {x.symbol for x in rA.results} == {"MID"}, (
    f"both bounds must be enforced (dollars conversion): {[x.symbol for x in rA.results]}"
)
print(f"✅ small-cap bounds both enforced → {[x.symbol for x in rA.results]}")

# B1. Interpreter keeps BILLIONS (human-friendly echo/display); the
# billions→dollars conversion happens in screen() at the DB boundary.
crit_d = _convert_to_filter_criteria({"max_market_cap_billions": 2.0})
assert crit_d[0].field == "market_cap" and crit_d[0].value == 2.0, crit_d
print(f"✅ interpreter keeps billions for display: {crit_d[0].value} (converted in screen())")

# B2. fetch_stock_profile converts Finnhub millions → dollars
from unittest import mock
from src import data_fetcher

class _FakeClient:
    def company_profile2(self, symbol=""):
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
    prof = data_fetcher.fetch_stock_profile(_FakePool(), "FAKE")
assert abs(prof["market_cap"] - 2_500_000_000) < 1, (
    f"fetch must convert millions→dollars: {prof['market_cap']}"
)
print(f"✅ fetch market_cap in dollars: {prof['market_cap']}")

# B3. End-to-end: max_market_cap_billions=2.0 excludes a $3B name
crit_max = _convert_to_filter_criteria({"max_market_cap_billions": 2.0})
q_max = InterpretedQuery(raw_input="max 2B", filters=crit_max)
dbB = FakeQualityDB([
    _record("OK1", 50.0, 45.0, 40.0, 60.0, 30.0, 12.0, 55.0, 1_000_000_000),
    _record("BIG", 200.0, 150.0, 100.0, 300.0, 80.0, 15.0, 60.0, 3_000_000_000),
])
sB = StockScreener(db=dbB)
rB = sB.screen(q_max, limit=10)
assert {x.symbol for x in rB.results} == {"OK1"}, (
    f"max 2B must exclude $3B name: {[x.symbol for x in rB.results]}"
)
print(f"✅ max-2B cap excludes $3B → {[x.symbol for x in rB.results]}")

# ── 12. Zero-match auto-relax (Tier 3 #6) ────────────────────────────────
# A query whose filter sits on a DEAD column (avg_volume never populated)
# returns 0 today. The auto-relax must drop the least-covered filter and
# re-run, returning nearest matches with an honest message instead of an
# empty list.
print("12. Zero-match auto-relax drops least-covered filter...")

db12 = FakeQualityDB([
    _record("A1", 100.0, 95.0, 90.0, 110.0, 70.0, 12.0, 60.0, 8_000_000_000),
    _record("A2", 80.0, 85.0, 80.0, 100.0, 60.0, 14.0, 55.0, 5_000_000_000),
])
# avg_volume is absent from every record → coverage 0 → dropped first.
q12 = InterpretedQuery(
    raw_input="high volume",
    filters=[FilterCriterion("avg_volume", ComparisonOp.GTE, 1_000_000)],
)
s12 = StockScreener(db=db12)
r12 = s12.screen(q12, limit=10)
assert r12.total_matches == 2, f"relax should rescue to all rows: {r12.total_matches}"
assert {x.symbol for x in r12.results} == {"A1", "A2"}, [x.symbol for x in r12.results]
assert r12.data_quality.get("relaxed_from") == ["avg_volume ≥ 1000000"], \
    r12.data_quality.get("relaxed_from")
assert "relaxed" in (r12.data_quality.get("message") or "").lower(), \
    r12.data_quality.get("message")
assert not any("avg_volume" in f for f in r12.applied_filters), r12.applied_filters
print(f"✅ avg_volume filter (0% coverage) dropped → {[x.symbol for x in r12.results]}")
print(f"✅ relaxed_from={r12.data_quality.get('relaxed_from')}")
print(f"✅ message={r12.data_quality.get('message')!r}")

# Relaxation respects RELAX_MAX_FILTERS: two dead-column filters → still
# rescued (both dropped within the cap), and the count/echo stay honest.
q12b = InterpretedQuery(
    raw_input="deep value",
    filters=[
        FilterCriterion("avg_volume", ComparisonOp.GTE, 1_000_000),
        FilterCriterion("ev_ebitda", ComparisonOp.LTE, 15.0),
    ],
)
r12b = s12.screen(q12b, limit=10)
assert r12b.total_matches == 2, r12b.total_matches
assert len(r12b.data_quality.get("relaxed_from", [])) == 2, r12b.data_quality
print(f"✅ both dead-column filters relaxed → {[x.symbol for x in r12b.results]}")
print(f"✅ relaxed_from={r12b.data_quality.get('relaxed_from')}")

# A query with NO relaxable criteria (only sector) → 0 stays 0, no crash.
q12c = InterpretedQuery(raw_input="biotech", filters=[], sector="Biotech")
r12c = s12.screen(q12c, limit=10)
assert r12c.total_matches == 0, r12c.total_matches
assert "relaxed_from" not in r12c.data_quality, r12c.data_quality
print(f"✅ no relaxable criteria → honest 0 (sector=Biotech has no rows)")

# ── 13. Contradiction resolver (Tier 3 #7) ────────────────────────────────
# "Oversold + rising momentum" asks for RSI ≤ 35 AND price > SMA50 — two
# conditions that barely coexist. The resolver keeps the explicit numeric
# RSI and drops the trend condition, so the screen returns real oversold
# stocks instead of 0 (or a 1-stock fluke).
print("13. Contradiction resolver drops trend vs oversold RSI...")

db13 = FakeQualityDB([
    # Truly oversold: RSI 30, price BELOW sma50 (classic oversold shape)
    _record("OVERSOLD", 30.0, 45.0, 50.0, 60.0, 25.0, 20.0, 30.0, 2_000_000_000),
    # RSI 30 but price ABOVE sma50 — the contradictory "rebound" shape
    _record("REBOUND", 50.0, 45.0, 48.0, 70.0, 30.0, 22.0, 30.0, 3_000_000_000),
    # Not oversold at all
    _record("NORMAL", 90.0, 85.0, 80.0, 100.0, 60.0, 15.0, 65.0, 5_000_000_000),
])
q13 = InterpretedQuery(
    raw_input="oversold rebound",
    filters=[FilterCriterion("rsi_14", ComparisonOp.LTE, 35)],
    above_sma_50=True,
)
s13 = StockScreener(db=db13)
# WITHOUT the resolver this would be RSI≤35 AND price>SMA50 → only REBOUND.
# WITH it, the trend filter is dropped → both oversold names survive.
r13 = s13.screen(q13, limit=10)
assert {x.symbol for x in r13.results} == {"OVERSOLD", "REBOUND"}, \
    [x.symbol for x in r13.results]
assert r13.data_quality.get("contradiction_resolved"), r13.data_quality
assert any("price > SMA50" not in f for f in r13.applied_filters), r13.applied_filters
print(f"✅ trend dropped, both oversold names returned → {[x.symbol for x in r13.results]}")
print(f"✅ contradiction_resolved={r13.data_quality.get('contradiction_resolved')}")

# Momentum variant: "oversold" + momentum=bullish must also resolve.
q13b = InterpretedQuery(
    raw_input="oversold momentum",
    filters=[FilterCriterion("rsi_14", ComparisonOp.LTE, 35)],
    momentum="bullish",
)
r13b = s13.screen(q13b, limit=10)
assert {x.symbol for x in r13b.results} == {"OVERSOLD", "REBOUND"}, \
    [x.symbol for x in r13b.results]
assert r13b.data_quality.get("contradiction_resolved"), r13b.data_quality
print(f"✅ momentum variant resolved → {[x.symbol for x in r13b.results]}")

# Non-contradictory case: RSI ≤ 35 WITHOUT any trend filter must NOT be
# mangled — both oversold rows match with no resolution notes.
q13c = InterpretedQuery(
    raw_input="just oversold",
    filters=[FilterCriterion("rsi_14", ComparisonOp.LTE, 35)],
)
r13c = s13.screen(q13c, limit=10)
assert {x.symbol for x in r13c.results} == {"OVERSOLD", "REBOUND"}, \
    [x.symbol for x in r13c.results]
assert not r13c.data_quality.get("contradiction_resolved"), r13c.data_quality
print(f"✅ no contradiction → unchanged → {[x.symbol for x in r13c.results]}")

# Overbought mirror: RSI ≥ 70 + below_sma_50 / bearish momentum — the trend
# must be dropped, keeping the explicit numeric RSI, with the REAL threshold
# (not a hardcoded 35) in the note.
db13d = FakeQualityDB([
    # Truly overbought: RSI 75, price ABOVE sma50
    _record("OVERBOUGHT", 90.0, 70.0, 60.0, 100.0, 50.0, 30.0, 75.0, 4_000_000_000),
    # RSI 75 but price BELOW sma50 — the contradictory pullback shape
    _record("PULLBACK", 55.0, 65.0, 62.0, 80.0, 40.0, 28.0, 75.0, 2_000_000_000),
    # Not overbought
    _record("CALM", 50.0, 52.0, 55.0, 70.0, 30.0, 18.0, 55.0, 1_000_000_000),
])
s13d = StockScreener(db=db13d)
q13d = InterpretedQuery(
    raw_input="overbought pullback",
    filters=[FilterCriterion("rsi_14", ComparisonOp.GTE, 70)],
    below_sma_50=True,
)
r13d = s13d.screen(q13d, limit=10)
assert {x.symbol for x in r13d.results} == {"OVERBOUGHT", "PULLBACK"}, \
    [x.symbol for x in r13d.results]
assert r13d.data_quality.get("contradiction_resolved"), r13d.data_quality
assert "70" in (r13d.data_quality.get("message") or ""), r13d.data_quality.get("message")
print(f"✅ overbought variant resolved → {[x.symbol for x in r13d.results]}")
print(f"✅ threshold from real filter → {r13d.data_quality.get('message')!r}")

# Message edge case: relax rescue that STILL finds 0 must say "but still
# found 0" — never claim matches. The sector filter is the query's subject
# and is NEVER relaxed, so even after both dead-column filters are dropped
# (cap = 2) the screen stays empty → the honest message fires.
q13e = InterpretedQuery(
    raw_input="impossible",
    filters=[
        FilterCriterion("avg_volume", ComparisonOp.GTE, 1_000_000),
        FilterCriterion("rsi_14", ComparisonOp.LTE, 1),  # RSI ≤ 1: no rows
    ],
    sector="Biotech",  # no Biotech rows exist → stays 0 after relaxing
)
r13e = s13d.screen(q13e, limit=10)
assert r13e.total_matches == 0, r13e.total_matches
assert r13e.data_quality.get("relaxed_from"), r13e.data_quality
assert "still found 0" in (r13e.data_quality.get("message") or ""), \
    r13e.data_quality.get("message")
print(f"✅ still-0 message honest → {r13e.data_quality.get('message')!r}")

print()
print("🎯 All screener quality tests pass!")
