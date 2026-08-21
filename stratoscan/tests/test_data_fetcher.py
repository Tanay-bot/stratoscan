"""Hermetic tests for the Finnhub data-fetcher layer — no network, no live DB.

Run: python -m tests.test_data_fetcher

Covers the highest-risk infra that had ZERO direct coverage:

1. FinnhubClientPool: round-robin rotation, thread safety, size.
2. initialize_client_pool: ValueError when no keys configured; timeout set.
3. _api_call_with_retry: 429 → exponential backoff retry, 5xx → retry,
   4xx (non-429) → hard RuntimeError, network errors → retry, success path.
4. fetch_stock_profile: the market-cap unit conversion (Finnhub millions →
   canonical dollars — a 3-way unit mismatch bug was fixed at this boundary),
   None on empty/failed profiles.
5. fetch_financial_metrics: _num() parsing (None / non-numeric → None).
6. fetch_stock_quote + build_stock_record merge semantics (None without
   symbol/profile+quote).
7. fetch_company_news: dedicated news key first, shared-pool fallback on
   failure, empty list on total failure.
8. fetch_technical_indicators: optional indicator calls never break the
   symbol's refresh (_safe_indicator_call never raises).
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest import mock  # noqa: E402

import finnhub  # noqa: E402
import requests  # noqa: E402

from src import data_fetcher as df  # noqa: E402


class _FakeFinnhubResponse:
    """Minimal object the Finnhub SDK exception extracts status/message from."""

    def __init__(self, status_code: int, text: str = "boom"):
        self.status_code = status_code
        self.text = text

    def json(self):
        raise ValueError("not json")


def _fh_error(status_code: int) -> finnhub.exceptions.FinnhubAPIException:
    """Build a real FinnhubAPIException carrying the given status code."""
    return finnhub.exceptions.FinnhubAPIException(
        _FakeFinnhubResponse(status_code)
    )


# ── 1. Client pool: round-robin ─────────────────────────────────────────────
print("1. FinnhubClientPool round-robin...")

c1, c2, c3 = object(), object(), object()
pool = df.FinnhubClientPool(clients=[c1, c2, c3])
seen = [pool.next(), pool.next(), pool.next(), pool.next(), pool.next()]
assert seen == [c1, c2, c3, c1, c2], "round-robin must wrap"
assert pool.size == 3
# Thread safety: the lock-protected counter must advance atomically — no
# lost updates under contention. 4 threads × 200 draws = 800 total, the
# counter ends exactly at 800, and round-robin fairness hands each of the 3
# clients a balanced share (~266/267 each). (Unique-object assertions are
# impossible here: next() returns c1/c2/c3 cyclically by design.)
import threading
pool2 = df.FinnhubClientPool(clients=[c1, c2, c3])
results = []
def _spin(_):
    for _ in range(200):
        results.append(pool2.next())
threads = [threading.Thread(target=_spin, args=(i,)) for i in range(4)]
[t.start() for t in threads]
[t.join() for t in threads]
assert len(results) == 800, len(results)
assert pool2._counter == 800, f"lost update: counter={pool2._counter}"
from collections import Counter
counts = Counter(id(r) for r in results)
assert len(counts) == 3, "all three clients must be served"
assert all(260 <= n <= 270 for n in counts.values()), dict(counts)
print("✅ round-robin wraps, size correct, thread-safe (no lost updates)")


# ── 2. Pool initialization ──────────────────────────────────────────────────
print("2. initialize_client_pool...")

# finnhub_keys is a read-only PROPERTY on the pydantic Settings class —
# instance-level mock.patch.object fails (pydantic's __delattr__ rejects
# unknown attrs on exit). Patch the property at the CLASS level instead.
with mock.patch.object(
    type(df.settings), "finnhub_keys", mock.PropertyMock(return_value=[])
):
    try:
        df.initialize_client_pool()
        raise AssertionError("must raise ValueError with no keys")
    except ValueError as e:
        assert "FINNHUB_KEY" in str(e)

with mock.patch.object(
    type(df.settings),
    "finnhub_keys",
    mock.PropertyMock(return_value=["key-a", "key-b"]),
), mock.patch.object(df.settings, "finnhub_request_timeout", 7.0):
    with mock.patch("finnhub.Client") as m_client:
        m_client.side_effect = lambda api_key: mock.Mock(api_key=api_key)
        built = df.initialize_client_pool()
        assert built.size == 2
        for cli in built.clients:
            assert cli.DEFAULT_TIMEOUT == 7.0, "timeout must be pinned on each client"
print("✅ ValueError on no keys; timeout pinned; pool built")


# ── 3. Retry / backoff ──────────────────────────────────────────────────────
print("3. _api_call_with_retry backoff...")

# 429 → retry → success.
with mock.patch("time.sleep") as m_sleep:
    calls = {"n": 0}
    def flaky_429():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _fh_error(429)
        return "ok"
    out = df._api_call_with_retry(flaky_429, max_retries=3, base_delay=1.0)
    assert out == "ok"
    # Two backoffs: 1.5s then 2.5s (base*2^attempt + 0.5).
    waits = [a[0][0] for a in m_sleep.call_args_list]
    assert any(1.4 < w < 1.6 for w in waits), waits
    assert any(2.4 < w < 2.6 for w in waits), waits

# 5xx → retry → success.
with mock.patch("time.sleep"):
    calls = {"n": 0}
    def flaky_500():
        calls["n"] += 1
        if calls["n"] < 2:
            raise _fh_error(503)
        return "ok"
    assert df._api_call_with_retry(flaky_500, max_retries=3) == "ok"

# 4xx non-429 → hard RuntimeError, NO retry.
with mock.patch("time.sleep") as m_sleep:
    try:
        df._api_call_with_retry(lambda: (_ for _ in ()).throw(_fh_error(403)), max_retries=3)
        raise AssertionError("403 must raise RuntimeError")
    except RuntimeError as e:
        assert "403" in str(e)
    assert not m_sleep.called, "hard errors must not sleep/retry"

# Network error → retry → success. MUST raise requests.exceptions.
# ConnectionError (what the retry loop actually catches) — the builtin
# ConnectionError is a sibling under OSError, so it would escape uncaught.
with mock.patch("time.sleep"):
    calls = {"n": 0}
    def flaky_net():
        calls["n"] += 1
        if calls["n"] < 2:
            raise requests.exceptions.ConnectionError("reset")
        return "ok"
    assert df._api_call_with_retry(flaky_net, max_retries=3) == "ok"

# Exhausted retries → RuntimeError with last error.
with mock.patch("time.sleep"):
    try:
        df._api_call_with_retry(lambda: (_ for _ in ()).throw(_fh_error(429)), max_retries=1)
        raise AssertionError("exhausted retries must raise")
    except RuntimeError as e:
        assert "exhausted" in str(e)
print("✅ 429/5xx/network retried; 403 hard-fails; exhaustion raises")


# ── 4. Profile fetch + market-cap unit conversion ───────────────────────────
print("4. fetch_stock_profile (unit conversion)...")

fake_client = mock.Mock()
fake_client.company_profile2.return_value = {
    "name": "Apple Inc",
    "sector": "Technology",
    "industry": "Consumer Electronics",
    "marketCapitalization": 2500.0,  # Finnhub = millions
    "peRatio": 28.5,
    "epsTTM": 6.2,
    "dividendYield": 0.005,
    "beta": 1.2,
    "high52": 200.0,
    "low52": 120.0,
}
fake_pool = mock.Mock()
fake_pool.next.return_value = fake_client

profile = df.fetch_stock_profile(fake_pool, "AAPL")
assert profile["symbol"] == "AAPL"
# THE canonical-unit contract: market cap must be DOLLARS (2500M → 2.5B).
assert profile["market_cap"] == 2_500_000_000, profile["market_cap"]
assert profile["company_name"] == "Apple Inc"
assert profile["pe_ratio"] == 28.5

# Empty profile → None.
fake_client.company_profile2.return_value = {}
assert df.fetch_stock_profile(fake_pool, "AAPL") is None

# Raised error → None (fetch must never crash the caller). Mock time.sleep
# so the 429 retry backoff doesn't burn real wall-clock.
with mock.patch("time.sleep"):
    fake_client.company_profile2.side_effect = _fh_error(429)
    assert df.fetch_stock_profile(fake_pool, "AAPL") is None
print("✅ millions→dollars conversion; None on empty/error")


# ── 5. Financial metrics parsing ────────────────────────────────────────────
print("5. fetch_financial_metrics _num parsing...")

fake_client2 = mock.Mock()
fake_client2.company_basic_financials.return_value = {
    "metric": {
        "psTTM": 7.1,
        "pb": "4.2",          # string numeric → coerced
        "roeTTM": None,        # None → None
        "grossMarginTTM": "not-a-number",  # junk → None
        "epsGrowthTTMYoy": 0.15,
        # The REAL Finnhub keys (verified live against /stock/metric):
        # evEbitdaTTM + netProfitMarginTTM. The old mapping read the
        # non-existent evToEBITDA / netMarginTTM keys, so these two
        # columns were ALWAYS None in production (0% populated → every
        # filter on them returned 0 candidates).
        "evEbitdaTTM": 27.49,
        "netProfitMarginTTM": 27.62,
    }
}
fake_pool2 = mock.Mock()
fake_pool2.next.return_value = fake_client2
m = df.fetch_financial_metrics(fake_pool2, "MSFT")
assert m["ps_ratio"] == 7.1
assert m["pb_ratio"] == 4.2
assert m["roe_ttm"] is None
assert m["gross_margin_ttm"] is None
assert m["eps_growth_ttm"] == 0.15
# Fixed key names must land in the DB columns.
assert m["ev_ebitda"] == 27.49, m
assert m["net_margin_ttm"] == 27.62, m
assert m["symbol"] == "MSFT"
print("✅ numeric coercion + junk/None → None; corrected Finnhub keys mapped")


# ── 6. Quote + record building ──────────────────────────────────────────────
print("6. quote + build_stock_record...")

fake_client3 = mock.Mock()
fake_client3.quote.return_value = {"c": 150.5, "dp": 1.2, "v": 5000000}
fake_pool3 = mock.Mock()
fake_pool3.next.return_value = fake_client3
q = df.fetch_stock_quote(fake_pool3, "AAPL")
assert q["current_price"] == 150.5 and q["volume"] == 5000000

# build_stock_record merges profile + quote + technicals.
rec = df.build_stock_record(
    {"symbol": "AAPL", "sector": "Technology"},
    {"current_price": 150.5},
    {"rsi_14": 45.0},
)
assert rec["symbol"] == "AAPL"
assert rec["sector"] == "Technology"
assert rec["current_price"] == 150.5
assert rec["rsi_14"] == 45.0
assert "updated_at" in rec

# No profile AND no quote → None.
assert df.build_stock_record(None, None, {"rsi_14": 1.0}) is None
# No symbol anywhere → None.
assert df.build_stock_record({"sector": "X"}, {"current_price": 1.0}, None) is None
print("✅ merge semantics + None guards")


# ── 7. News: dedicated key first, pool fallback ─────────────────────────────
print("7. fetch_company_news dedicated→fallback...")

dedicated = mock.Mock()
dedicated.company_news.return_value = [{"headline": "a"}]
with mock.patch.object(df, "_get_news_client", return_value=dedicated):
    news = df.fetch_company_news(fake_pool3, "AAPL", "2026-01-01", "2026-01-02")
    assert news == [{"headline": "a"}]
    dedicated.company_news.assert_called_once()

# Dedicated key fails → shared pool fallback. Mock time.sleep so the 429
# retry backoff doesn't burn real wall-clock.
with mock.patch("time.sleep"):
    dedicated.company_news.side_effect = _fh_error(429)
    pool_fallback = mock.Mock()
    pool_fallback.next.return_value.company_news.return_value = [{"headline": "b"}]
    with mock.patch.object(df, "_get_news_client", return_value=dedicated):
        news = df.fetch_company_news(pool_fallback, "AAPL", "2026-01-01", "2026-01-02")
        assert news == [{"headline": "b"}]

# Both fail → [] (never raises).
with mock.patch("time.sleep"):
    pool_fallback.next.return_value.company_news.side_effect = _fh_error(500)
    with mock.patch.object(df, "_get_news_client", return_value=dedicated):
        news = df.fetch_company_news(pool_fallback, "AAPL", "2026-01-01", "2026-01-02")
        assert news == []
print("✅ dedicated first, pool fallback, [] on total failure")


# ── 8. Technical indicators: optional calls are isolated ───────────────────
print("8. fetch_technical_indicators isolation...")

tc = mock.Mock()
# RSI/SMA succeed; the optional MACD/ATR/BBANDS calls are patched to None
# (via _safe_indicator_call) so they must not kill the fetch.
tc.technical_indicator.side_effect = [
    {"rsi": [None, 30.0, 40.0]},
    {"sma": [50.0]},
    {"sma": [100.0]},
]
fake_pool4 = mock.Mock()
fake_pool4.next.return_value = tc
with mock.patch.object(df, "_safe_indicator_call", return_value=None):
    out = df.fetch_technical_indicators(fake_pool4, "TSLA")
assert out["rsi_14"] == 40.0
assert out["sma_50"] == 50.0
assert out["sma_200"] == 100.0
assert out["macd_12_26"] is None

# _safe_indicator_call swallows errors → None.
tc2 = mock.Mock()
tc2.technical_indicator.side_effect = _fh_error(429)
assert df._safe_indicator_call(tc2, "TSLA", "macd", None) is None
print("✅ RSI/SMA extracted; optional indicators degrade to None, never raise")


# ── 8b. Technical indicators: REAL SDK signature (the production bug) ───────
# The installed finnhub SDK signature is:
#   technical_indicator(self, symbol, resolution, _from, to, indicator,
#                       indicator_fields=None)
# Production was calling it with a bare ``timeperiod=`` kwarg (and no
# _from/to) — which the real SDK rejects with TypeError. That TypeError was
# swallowed by the except in fetch_technical_indicators, so EVERY technicals
# fetch returned None in production. A mock.Mock() client accepts any kwargs
# and masked the bug; this test uses the REAL signature so a wrong call shape
# fails the same way the live SDK would.
print("8b. technical_indicator call shape matches the REAL SDK signature...")


class _RealSignatureClient:
    """Mirror of the installed finnhub SDK's technical_indicator signature:
    bare timeperiod= raises TypeError (the production bug), while
    _from/to/indicator_fields are accepted and recorded."""

    def __init__(self):
        self.calls = []

    def technical_indicator(self, symbol, resolution, _from, to, indicator,
                            indicator_fields=None):
        self.calls.append({
            "symbol": symbol, "resolution": resolution, "_from": _from,
            "to": to, "indicator": indicator,
            "indicator_fields": dict(indicator_fields or {}),
        })
        if indicator == "rsi":
            return {"rsi": [None, 30.0, 40.0]}
        if indicator == "sma":
            return {"sma": [50.0]}
        return None  # macd/atr/bbands optional calls → no data


rsc = _RealSignatureClient()
fake_pool4b = mock.Mock()
fake_pool4b.next.return_value = rsc
out = df.fetch_technical_indicators(fake_pool4b, "TSLA")
assert out is not None, "fetch_technical_indicators returned None — the production bug!"
assert out["rsi_14"] == 40.0
assert out["sma_50"] == 50.0
# Every SDK call must carry the date range (Finnhub requires _from/to).
for call in rsc.calls:
    assert call["_from"] and call["to"], f"missing date range: {call}"
    assert call["resolution"] == "D"
# The period must ride in indicator_fields (NOT a bare timeperiod kwarg).
rsi_call = next(c for c in rsc.calls if c["indicator"] == "rsi")
assert rsi_call["indicator_fields"] == {"timeperiod": 14}, rsi_call
sma_call = next(c for c in rsc.calls if c["indicator"] == "sma")
assert sma_call["indicator_fields"] == {"timeperiod": 50}, sma_call
print("✅ call shape matches the real SDK: _from/to + indicator_fields, values extracted")


# ── 8c. Finnhub paid-tier 403 → LOCAL yfinance fallback (the real fix) ─────
# The /indicator endpoint is paid-tier on this account (live 403). The
# production pipeline must NOT return None — it computes locally from
# yfinance history instead. Hermetic: patch _fetch_technicals_yfinance so
# no network is touched; assert the fallback is wired into both the
# exception path and the empty-result path.
print("8c. Finnhub 403/empty → local yfinance fallback...")

import pandas as pd  # noqa: E402

fake_hist = pd.DataFrame({
    "Close": [100.0 + i for i in range(260)],
    "High": [101.0 + i for i in range(260)],
    "Low": [99.0 + i for i in range(260)],
    # Constant volume → avg_volume must be exactly 1_000_000 (trailing 63
    # sessions, matching Finnhub's 3-month avgVolume definition).
    "Volume": [1_000_000] * 260,
}, index=pd.date_range("2025-01-01", periods=260, freq="B"))

# 8c-1: Finnhub raises 403 (paid tier) → local compute used.
tc403 = mock.Mock()
tc403.technical_indicator.side_effect = _fh_error(403)
fake_pool403 = mock.Mock()
fake_pool403.next.return_value = tc403
with mock.patch.object(df, "_fetch_technicals_yfinance",
                       return_value={"symbol": "TSLA", "sma_50": 111.0,
                                     "rsi_14": 55.0}) as m_fb:
    out403 = df.fetch_technical_indicators(fake_pool403, "TSLA")
assert m_fb.called, "403 must trigger the local fallback"
assert out403["sma_50"] == 111.0

# 8c-2: compute_indicators math over a real frame (the shared implementation).
ind = df.compute_indicators(fake_hist)
assert ind["sma_50"] == 334.5, ind  # mean of last 50 of 100.0..359.0
assert ind["sma_200"] == 259.5, ind  # mean of last 200 of 100.0..359.0
assert ind["high_52w"] == 360.0 and ind["low_52w"] == 99.0, ind
assert ind["current_price"] == 359.0
# Strictly-rising series: zero losses → RSI is None (avg_loss=0 → NA), not a
# crash — the indicator set degrades per-column exactly like production data.
assert ind["rsi_14"] is None
assert ind["macd_12_26"] == 7.0, ind  # (12-26 EMA diff) ≈ 7.0 on a linear ramp
assert ind["atr_14"] == 2.0, ind  # High-Low = 2 every day
assert ind["bollinger_upper"] > ind["bollinger_lower"]
# avg_volume: mean of the trailing 63 sessions (1M constant → 1M).
assert ind["avg_volume"] == 1_000_000, ind

# A frame WITHOUT a Volume column degrades to avg_volume None, not a crash
# (bare price frames must never break the shared indicator math).
no_vol = fake_hist.drop(columns=["Volume"])
assert df.compute_indicators(no_vol)["avg_volume"] is None

# A frame WITH losses produces a real RSI in (0, 100).
import math  # noqa: E402

wavy_close = [100.0 + 5 * math.sin(i / 4) + i * 0.2 for i in range(260)]
wavy = pd.DataFrame({
    "Close": wavy_close,
    "High": [c + 1.0 for c in wavy_close],
    "Low": [c - 1.0 for c in wavy_close],
}, index=pd.date_range("2025-01-01", periods=260, freq="B"))
ind_w = df.compute_indicators(wavy)
assert ind_w["rsi_14"] is not None and 0.0 <= ind_w["rsi_14"] <= 100.0, ind_w
assert ind_w["sma_50"] is not None and ind_w["sma_200"] is not None

# 8c-3: Finnhub returns data but all-empty → also falls back (never persists
#       a row of None technicals).
tc_empty = mock.Mock()
tc_empty.technical_indicator.return_value = {"rsi": None, "sma": None}
fake_pool_empty = mock.Mock()
fake_pool_empty.next.return_value = tc_empty
with mock.patch.object(df, "_safe_indicator_call", return_value=None), \
     mock.patch.object(df, "_fetch_technicals_yfinance",
                       return_value={"symbol": "TSLA", "rsi_14": 45.0}) as m_fb2:
    out_empty = df.fetch_technical_indicators(fake_pool_empty, "TSLA")
assert m_fb2.called, "all-empty Finnhub result must also fall back"
assert out_empty["rsi_14"] == 45.0
print("✅ 403 and empty-result both fall back to local yfinance computation")


# ── 8d. Dotted class-share tickers → dashed form on yfinance ───────────────
# Finnhub stores BRK.A; yfinance rejects the dotted form (verified live:
# "possibly delisted") but serves BRK-A. The fallback must translate.
print("8d. dotted tickers fall back to dashed form...")

_frames = {}
_frames["BRK-A"] = pd.DataFrame({  # dashed form of BRK.A
    "Close": [200.0 + i * 0.5 for i in range(260)],
    "High": [201.0 + i * 0.5 for i in range(260)],
    "Low": [199.0 + i * 0.5 for i in range(260)],
}, index=pd.date_range("2025-01-01", periods=260, freq="B"))


def _fake_download(symbols, **kwargs):
    sym = symbols[0]
    if sym in _frames:
        return _frames[sym]
    return pd.DataFrame()  # dotted form fails → empty


with mock.patch("yfinance.download", side_effect=_fake_download) as m_yd:
    res = df._fetch_technicals_yfinance("BRK.A")
assert res is not None, "dotted BRK.A must still get technicals via the dashed form"
# mean of last 50 of 200+0.5i for i in 210..259 = (305.0+329.5)/2
assert res["sma_50"] == 317.25, res
# The dashed form must have been attempted after the dotted form failed.
called_syms = [c.args[0][0] for c in m_yd.call_args_list]
assert called_syms == ["BRK.A", "BRK-A"], called_syms
print("✅ dotted ticker translated to dashed form; technicals recovered")


# ── 9. Symbol universe: major US exchanges only (no OTC pink sheets) ────────
print("9. fetch_us_stock_symbols MIC filtering...")

# The aggregated exchange=US endpoint returns ~18.4k Common Stocks, but
# ~13.5k are OOTC pink sheets (mic=OOTC). The scanner must only screen the
# three major exchanges (XNYS/XNAS/XASE) — the real ~5k-symbol universe.
big_listing = [
    {"symbol": "AAPL", "type": "Common Stock", "mic": "XNAS"},
    {"symbol": "BRK.B", "type": "Common Stock", "mic": "XNYS"},
    {"symbol": "VOO", "type": "ETF", "mic": "XASE"},       # not Common Stock
    {"symbol": "SRTSF", "type": "Common Stock", "mic": "OOTC"},  # pink sheet
    {"symbol": "TOYOF", "type": "Common Stock", "mic": "OOTC"},  # pink sheet
    {"symbol": "F", "type": "Common Stock", "mic": "XNYS"},  # 1-letter valid
    {"symbol": "ZZZZ", "type": "Common Stock"},  # no mic field at all
]

def _fake_us_fetch(resp):
    pool = mock.Mock()
    pool.next.return_value = mock.Mock()
    with mock.patch.object(df, "_api_call_with_retry", return_value=resp):
        return df.fetch_us_stock_symbols(pool)

# mic present → pink sheets dropped, majors kept, ETFs excluded.
syms = _fake_us_fetch(big_listing)
assert "AAPL" in syms and "BRK.B" in syms and "F" in syms, syms
assert "SRTSF" not in syms and "TOYOF" not in syms, syms
assert "VOO" not in syms, "ETF must be excluded"
assert "ZZZZ" not in syms, "item without mic must be excluded when mic data exists"
print(f"✅ OOTC pink sheets filtered; majors kept ({len(syms)} symbols)")

# No mic metadata at all → falls back to type-only filtering (old behavior).
no_mic = [
    {"symbol": "AAPL", "type": "Common Stock"},
    {"symbol": "BRK.B", "type": "Common Stock"},
    {"symbol": "VOO", "type": "ETF"},
]
syms2 = _fake_us_fetch(no_mic)
assert syms2 == ["AAPL", "BRK.B"], syms2
print("✅ No-mic responses fall back to type-only filtering")


# ── 10. Parallel quote fetch: rate-limited fan-out ───────────────────────────
print("10. fetch_bulk_quotes parallel fan-out...")

# The pool is thread-safe (round-robin under a lock), so N workers fetch
# concurrently at the aggregate rate budget. Verify all symbols are covered,
# price-guards hold, and no call is dropped.
from concurrent.futures import ThreadPoolExecutor as _TPE  # noqa
import threading as _thr

call_log = []
call_lock = _thr.Lock()

def _fake_quote(pool, symbol):
    with call_lock:
        call_log.append(symbol)
    if symbol.startswith("BAD"):
        return {"symbol": symbol, "current_price": None}  # bogus price → dropped
    return {"symbol": symbol, "current_price": 100.0, "volume": 1000}

pool3 = df.FinnhubClientPool(clients=[object(), object(), object()])  # 3 keys
with mock.patch.object(df, "fetch_stock_quote", side_effect=_fake_quote):
    out = df.fetch_bulk_quotes(pool3, ["AAPL", "MSFT", "BAD1", "GOOG", "BAD2"])

assert {q["symbol"] for q in out} == {"AAPL", "MSFT", "GOOG"}, out
assert len(call_log) == 5, f"all 5 symbols must be fetched, got {call_log}"
assert set(call_log) == {"AAPL", "MSFT", "GOOG", "BAD1", "BAD2"}
print(f"✅ 5 symbols fanned out across 3-key pool, price guards hold ({len(out)} kept)")

# Empty input → empty output, no crash.
assert df.fetch_bulk_quotes(pool3, []) == []
print("✅ empty symbol list handled")

# Rate limiter paces aggregate throughput: 3 calls at 3/sec ≈ 0.66s+.
import time as _t
lim = df._AggregateRateLimiter(3.0)
t0 = _t.monotonic()
for _ in range(3):
    lim.wait()
elapsed = _t.monotonic() - t0
assert elapsed >= 0.6, f"limiter too fast: {elapsed:.2f}s"
print(f"✅ aggregate limiter paces ({elapsed:.2f}s for 3 @ 3/s)")

# ── 11. refresh_price_data technicals-universe contract ────────────────────
# The P1-era fix: technicals must go to the caller's top-by-cap universe
# (tech_symbols), NOT the alphabetical symbols[:500] slice. Asserts the
# fetch side: exactly the filtered universe is fetched, and None falls
# back to symbols[:500].
print("11. refresh_price_data technicals universe...")
pool4 = mock.Mock()
quotes_for = ["AAPL", "MSFT", "NVDA", "TSLA", "GOOG"]
q_records = [{"symbol": s, "current_price": 100.0, "volume": None} for s in quotes_for]
tech_calls = []


def _fake_tech(pool, sym):
    tech_calls.append(sym)
    return {"symbol": sym, "sma_50": 99.0, "rsi_14": 50.0}


with mock.patch.object(df, "fetch_bulk_quotes", return_value=q_records), \
     mock.patch.object(df, "fetch_technical_indicators", side_effect=_fake_tech) as m_tech:
    # 11a. Explicit universe: only in-refresh-list members are fetched.
    records = df.refresh_price_data(
        pool4, quotes_for,
        include_technicals=True,
        tech_symbols=["AAPL", "NVDA", "ZZZ-NOT-REFRESHED"],
    )
    assert m_tech.call_count == 2, f"expected 2 (filtered), got {m_tech.call_count}"
    assert tech_calls == ["AAPL", "NVDA"], tech_calls
    assert any(r["symbol"] == "AAPL" and r.get("sma_50") == 99.0 for r in records)
    print("✅ explicit tech_symbols filtered to refresh universe")

    # 11b. None -> falls back to symbols[:500] (alphabetical here since the
    #      refresh list is what it is).
    tech_calls.clear()
    m_tech.reset_mock()
    with mock.patch.object(df, "fetch_technical_indicators", side_effect=_fake_tech) as m_tech2:
        df.refresh_price_data(pool4, quotes_for, include_technicals=True, tech_symbols=None)
        assert m_tech2.call_count == len(quotes_for), \
            f"None must fall back to symbols[:500], got {m_tech2.call_count}"
        assert set(tech_calls) == set(quotes_for), tech_calls
    print("✅ tech_symbols=None falls back to symbols[:500]")

    # 11c. Explicit EMPTY universe is authoritative (never falls back) —
    #      no technical indicator calls happen.
    tech_calls.clear()
    m_tech.reset_mock()
    with mock.patch.object(df, "fetch_technical_indicators", side_effect=_fake_tech) as m_tech3:
        df.refresh_price_data(pool4, quotes_for, include_technicals=True, tech_symbols=[])
        assert m_tech3.call_count == 0, f"empty universe must fetch nothing, got {m_tech3.call_count}"
    print("✅ empty tech_symbols is authoritative (no fallback, no calls)")


mock.patch.stopall()
print()
print("🎯 All data-fetcher tests pass!")
