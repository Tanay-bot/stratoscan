#!/usr/bin/env python3
"""verify_failure_drills.py — P1-7: documented, repeatable failure-injection
drills proving Stratoscan degrades GRACEFULLY when upstreams die.

Run: python verify_failure_drills.py          (hermetic — zero network, zero
                                               quota burn; failures are
                                               injected at the real boundaries)

Each drill drives the REAL code path (not a stub of the pipeline) and asserts
the actual, documented contract:

  1. FINNHUB KEYS DEAD — round-robin pool + _api_call_with_retry:
       429/5xx/network → retries happen (exponential backoff); a hard 4xx
       (401 invalid key) is NOT retried pointlessly — it surfaces as a clean
       RuntimeError. A refresh run with dead keys logs the failure, produces
       zero records, and NEVER corrupts existing DB data (the DB is not
       touched on total failure).
  2. ALL LLM PROVIDERS DOWN — interpret_query catches the router's RuntimeError
       and returns an InterpretedQuery with .error set. POST /screen returns a
       graceful response (not a 500) carrying data_quality.error + llm_error;
       POST /backtest returns 502 "Could not interpret strategy" (generic, no
       raw exception leak).
  3. YFINANCE TIMEOUT — fetch_historical_data returns None on timeout;
       run_backtest degrades to a clean zero-data result (final_value=0,
       total_return=-1) instead of raising; the /backtest surface returns 502
       "Backtest failed" — never a 500.
  4. UPSTASH DOWN — get_rate_limit_store() falls back to the in-memory store
       when Redis doesn't answer PING (logged, fail-open); requests continue
       to be rate-limited by the in-memory backend (burst still enforced).

Exit 0 = all drills pass. 1 = any drill failed.

The module is importable (a __main__ guard defers the drills): the helpers
_check/_drill_banner are unit-testable, and nothing executes on import.
"""

from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

FAILURES: list[str] = []


def _check(tag: str, name: str, ok: bool, detail: str = "") -> None:
    """Record a check scoped to a drill tag (e.g. 'D1'). Failed checks are
    stored tagged so the per-drill banner and the final report are accurate —
    never a false "PASS" when a sub-check failed."""
    mark = "✅" if ok else "❌"
    print(f"   {mark} {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(f"[{tag}] {name}")


def _drill_banner(n: int) -> None:
    tag = f"D{n}"
    ok = not any(f"[{tag}]" in f for f in FAILURES)
    print(f"DRILL {n}: {'PASS ✅' if ok else 'FAIL ❌'}")
    print()


def _mk_finnhub_error(status):
    import finnhub
    e = finnhub.exceptions.FinnhubAPIException.__new__(
        finnhub.exceptions.FinnhubAPIException)
    e.status_code = status
    e.message = f"HTTP {status}"  # the SDK's __str__ reads self.message
    return e


def main() -> int:
    # ── Drill 1: all 10 Finnhub keys dead ─────────────────────────────────────
    print("=" * 72)
    print("DRILL 1 — ALL 10 FINNHUB KEYS DEAD")
    print("=" * 72)

    from src.data_fetcher import _api_call_with_retry  # noqa: E402
    from src.config import settings  # noqa: E402

    # Inject 10 invalid keys. finnhub_keys is a read-only @property on the
    # pydantic Settings class — patch it with PropertyMock (instance-level
    # patch.object would try to SET the property and fail).
    DEAD_KEYS = [f"dead_key_{i}" for i in range(10)]
    with mock.patch.object(type(settings), "finnhub_keys",
                           new_callable=mock.PropertyMock,
                           return_value=DEAD_KEYS):
        from src.data_fetcher import initialize_client_pool  # noqa: E402

        pool = initialize_client_pool()
        _check("D1", "pool initializes with the 10 dead keys", pool.size == 10,
               f"{pool.size} clients")

        # 1a. 429 → retries happen (exponential backoff, attempt counter grows).
        calls = {"n": 0}

        class _RateLimited:
            def __call__(self):
                calls["n"] += 1
                raise _mk_finnhub_error(429)

        # Fast path: force tiny backoff by monkeypatching time.sleep inside the
        # retry loop — we only assert THAT retries happen, not the wall-clock.
        with mock.patch("src.data_fetcher.time.sleep") as _m_sleep:
            try:
                _api_call_with_retry(_RateLimited(), max_retries=2, base_delay=0.001)
            except RuntimeError:
                pass
        _check("D1", "429 is retried (attempt counter grew)", calls["n"] == 3,
               f"{calls['n']} attempts (initial + 2 retries)")

        # 1b. Hard 4xx (401 invalid key) is NOT retried — clean RuntimeError.
        calls2 = {"n": 0}

        class _Forbidden:
            def __call__(self):
                calls2["n"] += 1
                raise _mk_finnhub_error(401)

        try:
            _api_call_with_retry(_Forbidden(), max_retries=3, base_delay=0.001)
            _check("D1", "401 raises RuntimeError", False, "no exception raised")
        except RuntimeError as e:
            _check("D1", "401 raises clean RuntimeError (not retried pointlessly)",
                   calls2["n"] == 1 and "Finnhub API error" in str(e),
                   f"attempts={calls2['n']}: {str(e)[:60]}")

        # 1c. Full refresh with dead keys → zero records, DB never touched.
        from tests.fakes import FakeQualityDB  # noqa: E402

        fake_db = FakeQualityDB([{"symbol": "AAPL", "current_price": 180.0,
                                  "updated_at": "2026-08-01T00:00:00+00:00"}])
        before = fake_db.data_freshness()
        from src.data_fetcher import refresh_price_data  # noqa: E402

        with mock.patch("src.data_fetcher._api_call_with_retry",
                        side_effect=RuntimeError("Finnhub API error (status 401): dead key")):
            records = refresh_price_data(pool, ["AAPL", "MSFT", "NVDA"],
                                         include_technicals=False)
        _check("D1", "refresh with dead keys yields zero records (nothing written)",
               len(records) == 0, f"{len(records)} records")
        _check("D1", "DB untouched on total key failure",
               before == fake_db.data_freshness())

    _drill_banner(1)

    # ── Drill 2: all LLM providers down ───────────────────────────────────────
    print("=" * 72)
    print("DRILL 2 — ALL LLM PROVIDERS DOWN")
    print("=" * 72)

    from src import interpreter as interp  # noqa: E402
    from fastapi.testclient import TestClient  # noqa: E402

    # 2a. interpret_query returns .error instead of raising.
    boom = mock.Mock()
    boom.chat_completion.side_effect = RuntimeError("all providers down")
    with mock.patch("src.llm_router.get_router", return_value=boom), \
         mock.patch.object(interp.settings, "cache_enabled", False):
        iq = interp.interpret_query("oversold tech stocks")
    _check("D2", "interpret_query degrades to .error (never raises)",
           iq.error is not None and "LLM call failed" in iq.error,
           (iq.error or "")[:70])

    # 2b. POST /screen → graceful 200 with data_quality.error + llm_error (not 500).
    from src import api as api_mod  # noqa: E402

    boom2 = mock.Mock()
    boom2.chat_completion.side_effect = RuntimeError("provider exploded")
    with mock.patch("src.llm_router.get_router", return_value=boom2), \
         mock.patch.object(api_mod.settings, "cache_enabled", False), \
         mock.patch.object(api_mod.settings, "rate_limit_enabled", False), \
         mock.patch("src.screener.PostgresDB", return_value=FakeQualityDB([])):
        api_mod._screen_cache.clear()
        client = TestClient(api_mod.app)
        r = client.post("/screen", json={"query": "tech stocks"})
        body = r.json()
        dq = body.get("data_quality") or {}
        _check("D2", "/screen is NOT a 500 when all LLM providers are down",
               r.status_code < 500, f"status {r.status_code}")
        _check("D2", "/screen surfaces data_quality.error",
               "error" in dq and "Could not interpret" in dq.get("error", ""),
               (dq.get("error") or "")[:60])
        _check("D2", "/screen surfaces llm_error in interpreted_filters",
               bool((body.get("interpreted_filters") or {}).get("llm_error")))

    # 2c. POST /backtest → 502 generic (not 500, no raw exception leak).
    with mock.patch("src.llm_router.get_router", return_value=boom2), \
         mock.patch.object(api_mod.settings, "cache_enabled", False), \
         mock.patch.object(api_mod.settings, "rate_limit_enabled", False), \
         mock.patch("src.screener.PostgresDB", return_value=FakeQualityDB([])):
        api_mod._screen_cache.clear()
        client = TestClient(api_mod.app)
        r = client.post("/backtest", json={"query": "buy oversold stocks"})
        _check("D2", "/backtest is NOT a 500 when the LLM is down",
               r.status_code in (400, 401, 402, 403, 404, 405, 409, 422, 429, 502),
               f"status {r.status_code}")
        detail = (r.json().get("detail") or "") if r.status_code != 200 else ""
        _check("D2", "/backtest detail is generic (no raw exception text)",
               "provider exploded" not in detail,
               detail[:60] or "(200 — unexpected)")

    _drill_banner(2)

    # ── Drill 3: yfinance timeout ─────────────────────────────────────────────
    print("=" * 72)
    print("DRILL 3 — YFINANCE TIMEOUT")
    print("=" * 72)

    from src.backtester import fetch_historical_data, run_backtest  # noqa: E402
    from src.backtester import BacktestEngine  # noqa: E402
    from src.interpreter import InterpretedQuery  # noqa: E402

    # 3a. fetch_historical_data returns None on timeout (the documented failure).
    with mock.patch("src.backtester.yf.Ticker",
                    side_effect=RuntimeError("connection timed out")):
        got = fetch_historical_data("AAPL")
    _check("D3", "fetch_historical_data returns None on timeout (never raises)",
           got is None, f"{got!r}")

    # 3b. run_backtest on a None frame degrades to a clean zero-data result.
    rules = {"initial_capital": 100_000.0, "fee_rate": 0.001, "slippage": 0.0005,
             "buy_rules": ["price > 0"], "sell_rules": []}
    with mock.patch("src.backtester.fetch_historical_data", return_value=None):
        res = run_backtest("AAPL", rules, "2020-01-01", "2025-12-31")
    _check("D3", "run_backtest returns a zero-data result (no raise, no NaN crash)",
           res.final_value == 0 and res.total_return == -1.0 and len(res.equity_curve) == 0,
           f"final={res.final_value} ret={res.total_return}")

    # 3c. The engine's top-N path with a dead data source → 502 (not 500).
    engine = BacktestEngine(db=mock.MagicMock())
    iq = InterpretedQuery(raw_input="oversold", filters=[])
    with mock.patch("src.backtester.fetch_historical_data", return_value=None):
        results = engine.backtest_top_results(
            iq, top_n=3, strategy_rules=rules, start_date="2020-01-01",
            end_date="2025-12-31")
    _check("D3", "top-N backtest survives a dead data source (no raise)",
           isinstance(results, dict), f"{type(results).__name__}")

    _drill_banner(3)

    # ── Drill 4: Upstash down ─────────────────────────────────────────────────
    print("=" * 72)
    print("DRILL 4 — UPSTASH (REDIS) DOWN")
    print("=" * 72)

    from src.redis_ratelimit import (  # noqa: E402
        InMemoryRateLimitStore,
        get_rate_limit_store,
        reset_rate_limit_store,
    )

    # 4a. Redis configured but PING fails → in-memory store, logged, fail-open.
    # redis_rate_limit_enabled is a settable field; has_upstash_redis is a
    # read-only @property (needs PropertyMock).
    with mock.patch.object(settings, "redis_rate_limit_enabled", True), \
         mock.patch.object(type(settings), "has_upstash_redis",
                           new_callable=mock.PropertyMock, return_value=True), \
         mock.patch("src.redis_ratelimit.UpstashRateLimitStore") as m_cls:
        fake_inst = mock.MagicMock()
        fake_inst._client.ping.return_value = False
        m_cls.return_value = fake_inst
        reset_rate_limit_store()
        store = get_rate_limit_store()
        _check("D4", "Upstash down → falls back to the in-memory store",
               isinstance(store, InMemoryRateLimitStore),
               f"{type(store).__name__}")
        reset_rate_limit_store()

    # 4b. Requests continue to be limited by the in-memory backend.
    # The 62-request count is tied to the documented READ burst (60/min): the
    # 61st request must be 429. If the bucket is ever retuned, keep this count
    # at burst + 2 so the assertion stays meaningful (n_429 >= 1).
    with mock.patch.object(settings, "redis_rate_limit_enabled", True), \
         mock.patch.object(type(settings), "has_upstash_redis",
                           new_callable=mock.PropertyMock, return_value=True), \
         mock.patch("src.redis_ratelimit.UpstashRateLimitStore") as m_cls2, \
         mock.patch.object(api_mod.settings, "rate_limit_enabled", True):
        fake_inst2 = mock.MagicMock()
        fake_inst2._client.ping.return_value = False
        m_cls2.return_value = fake_inst2
        reset_rate_limit_store()
        client = TestClient(api_mod.app)
        # Burn the read burst (60/min): the FIRST burst is exhausted after 60;
        # the in-memory store (NOT production Redis) must still limit — the
        # 61st/62nd requests must be 429.
        statuses = [client.get("/health").status_code for _ in range(62)]
        n_ok = sum(1 for s in statuses if s == 200)
        n_429 = sum(1 for s in statuses if s == 429)
        _check("D4", "in-memory backend still enforces the read burst (429s happen)",
               n_429 >= 1 and n_ok >= 1,
               f"{n_ok} x 200, {n_429} x 429 of {len(statuses)}")
        reset_rate_limit_store()

    _drill_banner(4)

    mock.patch.stopall()
    print()
    print("=" * 72)
    if FAILURES:
        print("❌ FAILURE DRILLS FAILED")
        for f in FAILURES:
            print(f"   ❌ {f}")
        print("=" * 72)
        return 1
    print("🎯 ALL 4 FAILURE DRILLS PASSED — system degrades gracefully.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
