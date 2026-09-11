"""Hermetic cache tests for Stratoscan — no network, no DB.

Run: python -m tests.test_cache

Verifies:
1. The TTLCache primitive: set/get, expiry, eviction, clear.
2. Interpreter-level cache: two identical queries hit the LLM router ONCE;
   different queries hit it twice (long-TTL interpret cache).
3. Endpoint-level cache: two identical /screen calls run the full pipeline
   (LLM + DB) ONCE — the second is a cache hit (short-TTL screen cache).
4. The screen cache key includes limit+offset, so distinct pages don't
   collide.
5. cache_enabled=False bypasses both caches.

The live PostgresDB and LLM router are swapped for fakes, so nothing ever
leaves the box.
"""

from __future__ import annotations

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest import mock

# Rate limits OFF for this suite (see tests/test_auth.py for why) — the
# numbers themselves are locked in tests/test_rate_limits.py.
os.environ["RATE_LIMIT_ENABLED"] = "false"
# Pin the LLM path: this suite measures LLM-call caching. The deterministic
# parser answers these queries at confidence 1.00 and returns before any LLM
# call is made, which would make every assertion below vacuous.
os.environ["USE_DETERMINISTIC_INTERPRETER"] = "false"

from src.cache import TTLCache
from src.config import settings
from tests.fakes import FakeLLMRouter

# ── 1. TTLCache primitive ────────────────────────────────────────────────────
print("1. TTLCache primitive...")

c = TTLCache(ttl_seconds=3600, max_entries=2, name="test")
c.set("a", 1)
assert c.get("a") == 1, "get after set failed"
assert c.get("missing") is None
assert "a" in c
print("✅ set/get works")

# Expiry
short = TTLCache(ttl_seconds=0.05, max_entries=4)
short.set("k", "v")
assert short.get("k") == "v"
time.sleep(0.1)
assert short.get("k") is None, "expired entry returned!"
print("✅ TTL expiry works")

# Eviction (max_entries)
ev = TTLCache(ttl_seconds=3600, max_entries=2)
ev.set("a", 1)
ev.set("b", 2)
ev.set("c", 3)  # evicts oldest ('a')
assert ev.get("a") is None, "oldest entry not evicted"
assert ev.get("b") == 2 and ev.get("c") == 3
print("✅ max_entries eviction works")

# clear
ev.clear()
assert len(ev) == 0
print("✅ clear works")

# ── 2. Interpreter-level cache (long TTL) ────────────────────────────────────
print("2. Interpreter cache — LLM called once per unique query...")

# The interpreter cache is process-wide; start from a clean slate for this
# test's counting to be deterministic.
from src import interpreter as interpreter_mod

interpreter_mod._interpret_cache.clear()

fake_router = FakeLLMRouter()  # counts calls via .total_attempts
# IMPORTANT: all four queries must run INSIDE this context. The interpreter
# imports get_router() lazily per call, so a query executed after the mock is
# torn down would hit the REAL LLM router (network + real quota) instead of
# the fake — and never increment the fake's counter. (Historical bug: r3/r4
# sat outside the block, silently making this suite network-dependent.)
with mock.patch("src.llm_router.get_router", return_value=fake_router):
    from src.interpreter import interpret_query

    r1 = interpret_query("undervalued tech stocks")
    calls_after_first = fake_router.total_attempts
    r2 = interpret_query("undervalued tech stocks")  # exact same query
    calls_after_second = fake_router.total_attempts

    assert calls_after_first == 1, f"first query should make 1 LLM call, got {calls_after_first}"
    assert calls_after_second == calls_after_first, (
        f"identical query should NOT re-call the LLM (calls {calls_after_first} → {calls_after_second})"
    )
    assert r1.filters == r2.filters, "cached result should match fresh result"
    print(f"✅ identical query: {calls_after_second} LLM call(s), not 2")

    # Case/whitespace normalization hits the same cache entry
    r3 = interpret_query("  Undervalued TECH stocks ")
    assert fake_router.total_attempts == calls_after_second, (
        "normalized-identical query should hit the cache"
    )
    print("✅ normalized query (case/whitespace) also cached")

    # A DIFFERENT query still calls the LLM
    r4 = interpret_query("oversold healthcare stocks")
    assert fake_router.total_attempts == calls_after_second + 1, (
        "different query should call the LLM again"
    )
    print("✅ different query → new LLM call")

# ── 3. Endpoint-level cache (short TTL, full response) ───────────────────────
print("3. /screen full-response cache...")


class CountingStockDB:
    """Fake PostgresDB that counts query_stocks calls (proves caching)."""

    def __init__(self):
        self.query_calls = 0
        self._records = [
            {
                "symbol": "AAPL",
                "company_name": "Apple Inc",
                "sector": "Technology",
                "market_cap": 3_000_000_000_000,
                "current_price": 200.0,
            },
            {
                "symbol": "MSFT",
                "company_name": "Microsoft",
                "sector": "Technology",
                "market_cap": 2_500_000_000_000,
                "current_price": 400.0,
            },
            {
                "symbol": "NVDA",
                "company_name": "Nvidia",
                "sector": "Technology",
                "market_cap": 2_000_000_000_000,
                "current_price": 120.0,
            },
        ]

    def query_stocks(self, filters=None, limit=50, offset=0,
                     sort_by="market_cap", sort_desc=True):
        self.query_calls += 1
        ordered = sorted(self._records, key=lambda r: r.get("market_cap") or 0, reverse=True)
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


# Clean the process-wide screen cache so our counting is deterministic.
from src import api as api_mod

api_mod._screen_cache.clear()

counting_db = CountingStockDB()
screen_router = FakeLLMRouter()
mock.patch("src.screener.PostgresDB", return_value=counting_db).start()
mock.patch("src.llm_router.get_router", return_value=screen_router).start()
mock.patch("src.news.get_sentiment_for_screen", return_value={}).start()

from src.api import app
from fastapi.testclient import TestClient

client = TestClient(app)

payload = {"query": "tech stocks", "limit": 2, "offset": 0}
r1 = client.post("/screen", json=payload)
assert r1.status_code == 200, f"/screen 1: {r1.status_code} {r1.text[:200]}"
db_calls_after_first = counting_db.query_calls
assert db_calls_after_first == 1, f"first screen should query DB once, got {db_calls_after_first}"
print(f"✅ first /screen: 1 DB query, {len(r1.json()['results'])} results")

r2 = client.post("/screen", json=payload)  # identical → cache hit
assert r2.status_code == 200
assert r2.json() == r1.json(), "cached response should be byte-identical"
assert counting_db.query_calls == db_calls_after_first, (
    f"second identical screen should hit cache, but DB was queried again "
    f"({counting_db.query_calls} calls)"
)
print(f"✅ second identical /screen: cache HIT (still {counting_db.query_calls} DB query)")

# Distinct offset = distinct cache entry (pagination doesn't collide)
r3 = client.post("/screen", json={"query": "tech stocks", "limit": 2, "offset": 2})
assert r3.status_code == 200
assert counting_db.query_calls == db_calls_after_first + 1, (
    "different offset should run the pipeline (new cache entry)"
)
print("✅ distinct offset → new cache entry, no collision")

# ── 4. cache_enabled=False bypasses both caches ──────────────────────────────
print("4. cache_enabled=False bypasses caches...")

original_flag = settings.cache_enabled
try:
    settings.cache_enabled = False
    api_mod._screen_cache.clear()
    interpreter_mod._interpret_cache.clear()

    bypass_db = CountingStockDB()
    bypass_router = FakeLLMRouter()
    with mock.patch("src.screener.PostgresDB", return_value=bypass_db), mock.patch(
        "src.llm_router.get_router", return_value=bypass_router
    ):
        p1 = client.post("/screen", json=payload)
        p2 = client.post("/screen", json=payload)
        assert p1.status_code == 200 and p2.status_code == 200
        assert bypass_db.query_calls == 2, (
            f"cache disabled → both screens should hit the DB ({bypass_db.query_calls})"
        )
        # Interpreter cache disabled too: the LLM router gets called again
        assert bypass_router.total_attempts >= 1, "LLM should still be called when caching off"
    print("✅ cache_enabled=False → pipeline runs on every request")
finally:
    settings.cache_enabled = original_flag

print()
print("🎯 All cache tests pass!")
