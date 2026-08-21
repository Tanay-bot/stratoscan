"""Hermetic pagination tests for the /screen pipeline — no network, no DB.

Run: python -m tests.test_pagination

Verifies:
1. StockScreener.screen() paginates correctly: true total_matches, has_more
   flipping at the page boundary, offset threaded to the DB.
2. The /screen API endpoint exposes limit/offset/total_matches/has_more/
   next_offset, and pages 1+2 return disjoint results.
3. /enrich's nested screening block also carries the pagination fields.

The live PostgresDB is swapped for an in-memory fake; the LLM router is
swapped for FakeLLMRouter, so nothing ever leaves the box.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest import mock

# Rate limits OFF for this suite (see tests/test_auth.py for why) — the
# numbers themselves are locked in tests/test_rate_limits.py.
os.environ["RATE_LIMIT_ENABLED"] = "false"

from src.interpreter import InterpretedQuery
from src.screener import StockScreener
from tests.fakes import FakeLLMRouter

# ── In-memory stand-in for PostgresDB.query_stocks/count_stocks ─────────────
class FakeStockDB:
    """Mirrors the PostgresDB methods the screener uses (query_stocks,
    count_stocks, aggregate_stats, data_freshness) over an in-memory
    list. Pagination ordering mirrors the real DB (market cap desc).
    Filters are IGNORED for pagination purposes (the tests exercise
    ordering/paging, not filtering — see test_screener_quality for that)."""

    def __init__(self, records):
        self._records = list(records)

    def query_stocks(self, filters=None, limit=50, offset=0,
                     sort_by="market_cap", sort_desc=True):
        ordered = sorted(
            self._records,
            key=lambda r: r.get("market_cap") or 0,
            reverse=True,
        )
        return ordered[offset : offset + limit]

    def count_stocks(self, filters=None):
        return len(self._records)

    def aggregate_stats(self, filters=None, columns=None):
        return {c: {"mean": 0.0, "std": 0.0, "valid": 0} for c in (columns or [])}

    def data_freshness(self, max_age_minutes=15):
        return {
            "oldest_age_minutes": 0.0,
            "max_updated_at": None,
            "stale_count": 0,
            "total_rows": len(self._records),
        }


def _make_records(n: int):
    """n fake stock records with distinct market caps."""
    return [
        {
            "symbol": f"SYM{i:03d}",
            "company_name": f"Fake Company {i}",
            "sector": "Technology",
            "market_cap": (n - i) * 1_000_000_000,  # SYM000 largest
            "current_price": 100.0 + i,
        }
        for i in range(n)
    ]


def _make_query() -> InterpretedQuery:
    """A filter-less query (screener passes filters through to the fake)."""
    return InterpretedQuery(raw_input="test query", filters=[])


# ── 1. Screener-level pagination ────────────────────────────────────────────
print("1. StockScreener.screen() pagination...")

db = FakeStockDB(_make_records(5))  # 5 stocks total
screener = StockScreener(db=db)

page1 = screener.screen(_make_query(), limit=2, offset=0)
assert page1.total_matches == 5, f"total_matches: {page1.total_matches} (want 5)"
assert len(page1.results) == 2, f"page1 len: {len(page1.results)}"
assert page1.has_more is True, "page1 should have more"
assert page1.offset == 0
page1_symbols = [r.symbol for r in page1.results]
assert page1_symbols == ["SYM000", "SYM001"], f"page1 order: {page1_symbols}"
print(f"✅ page 1: {len(page1.results)} rows, total={page1.total_matches}, has_more={page1.has_more}")

page2 = screener.screen(_make_query(), limit=2, offset=2)
assert page2.total_matches == 5
assert len(page2.results) == 2
assert page2.has_more is True
page2_symbols = [r.symbol for r in page2.results]
assert page2_symbols == ["SYM002", "SYM003"], f"page2 order: {page2_symbols}"
assert not (set(page1_symbols) & set(page2_symbols)), "pages overlap!"
print(f"✅ page 2: {len(page2.results)} rows, no overlap with page 1")

page3 = screener.screen(_make_query(), limit=2, offset=4)
assert page3.total_matches == 5
assert len(page3.results) == 1
assert page3.has_more is False, "last page should not have more"
assert page3.results[0].symbol == "SYM004"
print("✅ page 3: 1 row, has_more=False")

# Offset past the end → empty page, no more
empty = screener.screen(_make_query(), limit=2, offset=10)
assert empty.results == []
assert empty.has_more is False
print("✅ offset past end → empty page, has_more=False")

# ── 2. API-level: POST /screen pagination ───────────────────────────────────
print("2. POST /screen pagination via TestClient...")

# Hermetic: fake DB + fake LLM router + no live news lookup.
# NOTE: patch src.screener.PostgresDB (NOT src.database.PostgresDB) —
# screener.py binds that name at module import, so StockScreener.__init__
# resolves it from the screener namespace at call time.
fake_db = FakeStockDB(_make_records(5))
mock.patch("src.screener.PostgresDB", return_value=fake_db).start()
mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()
mock.patch("src.news.get_sentiment_for_screen", return_value={}).start()

# /enrich hits EnrichmentEngine (real Finnhub calls) — fake it so the
# test stays hermetic. enrich_results returns an object with .sentiments.
class _FakeEnrichment:
    sentiments = {}


class _FakeEnrichmentEngine:
    def enrich_results(self, query, results, max_stocks=10):
        return _FakeEnrichment()


mock.patch("src.enrichment.EnrichmentEngine", _FakeEnrichmentEngine).start()

from src.api import app
from fastapi.testclient import TestClient

client = TestClient(app)

r = client.post("/screen", json={"query": "tech stocks", "limit": 2, "offset": 0})
assert r.status_code == 200, f"/screen page1: {r.status_code} {r.text[:200]}"
body = r.json()
assert body["total_matches"] == 5, f"total_matches: {body['total_matches']}"
assert body["limit"] == 2
assert body["offset"] == 0
assert body["has_more"] is True
assert body["next_offset"] == 2, f"next_offset: {body['next_offset']}"
assert len(body["results"]) == 2
# Tier 1 echo: applied_filters + data_quality are present on every response
assert isinstance(body.get("applied_filters"), list), "applied_filters missing"
assert isinstance(body.get("data_quality"), dict), "data_quality missing"
assert body.get("sort_by") == "market_cap", f"sort_by: {body.get('sort_by')}"
api_page1 = {s["symbol"] for s in body["results"]}
print(f"✅ /screen page 1 → offset={body['offset']}, has_more={body['has_more']}, next_offset={body['next_offset']}")

r = client.post("/screen", json={"query": "tech stocks", "limit": 2, "offset": 2})
assert r.status_code == 200
body = r.json()
assert body["offset"] == 2
assert body["has_more"] is True
assert body["next_offset"] == 4
api_page2 = {s["symbol"] for s in body["results"]}
assert not (api_page1 & api_page2), "API pages overlap!"
print(f"✅ /screen page 2 → offset={body['offset']}, no overlap, next_offset={body['next_offset']}")

r = client.post("/screen", json={"query": "tech stocks", "limit": 2, "offset": 4})
body = r.json()
assert body["has_more"] is False
assert body["next_offset"] is None
print(f"✅ /screen page 3 → offset=4, has_more={body['has_more']}, next_offset=None")

# Validation: negative offset rejected
r = client.post("/screen", json={"query": "tech", "limit": 2, "offset": -1})
assert r.status_code == 422, f"negative offset: {r.status_code} (want 422)"
print("✅ negative offset → 422")

# ── 3. /enrich nested screening block carries pagination fields ─────────────
print("3. /enrich nested screening pagination fields...")

r = client.post("/enrich", json={"query": "tech stocks", "max_stocks": 2, "limit": 2})
assert r.status_code == 200, f"/enrich: {r.status_code} {r.text[:200]}"
screening = r.json()["screening"]
assert screening["total_matches"] == 5, f"enrich total_matches: {screening['total_matches']}"
assert screening["offset"] == 0
assert screening["has_more"] is True
assert "next_offset" in screening
print(f"✅ /enrich screening block → total={screening['total_matches']}, has_more={screening['has_more']}")

print()
print("🎯 All pagination tests pass!")
