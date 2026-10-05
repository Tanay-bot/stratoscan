"""Day 14 — ETFs: a curated, clearly labelled fund set, and nothing more.

Pins:
  - the set itself: unique, plain-vanilla (no leveraged/inverse funds), sector
    funds use the interpreter's canonical sector names;
  - screens are stocks only unless the query asks for funds — both parsers;
  - an ETF screen returns funds only, echoes "asset type = ETF", and every
    result says asset_type "etf" (the label the UI renders);
  - the LLM path accepts only the exact value "etf".

Run: python -m tests.test_etfs
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("JWT_SECRET", "etf-dummy-secret")

from src import interpreter as interp  # noqa: E402
from src.deterministic_interpreter import SECTOR_ALIASES, DeterministicInterpreter  # noqa: E402
from src.etfs import ETF_SYMBOLS, ETFS, etf_seed_records, with_etfs  # noqa: E402
from src.screener import StockScreener  # noqa: E402
from tests.fakes import FakeLLMRouter, FakeQualityDB  # noqa: E402

# ── 1. The set ──────────────────────────────────────────────────────────────
syms = [e[0] for e in ETFS]
assert len(syms) == len(set(syms)) == len(ETF_SYMBOLS) >= 40, len(syms)
LEVERAGED = {"TQQQ", "SQQQ", "SPXL", "SPXS", "UPRO", "SPXU", "SSO", "SDS", "QLD", "SH", "PSQ",
             "SOXL", "SOXS", "TNA", "TZA", "UVXY", "VXX", "SVXY", "LABU", "LABD", "FAS", "FAZ"}
assert not ETF_SYMBOLS & LEVERAGED
for _, name, _, _ in ETFS:
    low = name.lower()
    assert not any(w in low for w in ("ultra", "2x", "3x", "inverse", "short", "bear", "bull")), name
canonical = set(SECTOR_ALIASES.values())
assert all(sector is None or sector in canonical for _, _, sector, _ in ETFS), \
    [s for *_, s, _ in ETFS if s and s not in canonical]
assert all(r["asset_type"] == "etf" and r["company_name"] for r in etf_seed_records())
assert with_etfs(["AAPL", "SPY"]) == sorted({"AAPL"} | ETF_SYMBOLS)
print(f"✅ {len(syms)} plain-vanilla funds, unique, canonical sectors, all labelled 'etf'")

# ── 2. Deterministic parser: funds only when asked ─────────────────────────
D = DeterministicInterpreter()
for q in ("ETFs with RSI below 30", "technology etf above its 200-day moving average",
          "exchange-traded funds that are oversold", "index funds near their 52-week high"):
    assert D.parse(q)[0].asset_type == "etf", q
for q in ("oversold large cap stocks", "technology stocks with RSI below 30",
          "undervalued small caps", "netflix-like growth names"):
    assert D.parse(q)[0].asset_type is None, q
tech_etf, conf = D.parse("technology ETFs with RSI below 40")
assert tech_etf.sector == "Technology" and conf >= 0.6
print("✅ deterministic parser: ETF words → 'etf'; everything else stays stocks")

# ── 3. Screener: stocks only by default; ETF screens are funds only ────────
now = datetime.now(timezone.utc).isoformat()


def rec(sym, asset_type=None, sector="Technology", rsi=25.0, cap=None):
    cap = cap if cap is not None else (5_000_000_000 if asset_type is None else None)
    r = {"symbol": sym, "company_name": f"{sym} Inc", "sector": sector, "current_price": 50.0,
         "market_cap": cap, "rsi_14": rsi, "updated_at": now}
    if asset_type:
        r["asset_type"] = asset_type
    return r


# VGT carries a market cap: the label, not missing data, must keep it out of
# a stock screen.
db = FakeQualityDB([rec("AAPL"), rec("MSFT"), rec("XLK", "etf"), rec("SPY", "etf", sector=None),
                    rec("XLE", "etf", sector="Energy"), rec("VGT", "etf", cap=9_000_000_000)])
scr = StockScreener(db=db)

stocks = scr.screen(D.parse("technology stocks with RSI below 30")[0])
assert {r.symbol for r in stocks.results} == {"AAPL", "MSFT"}, [r.symbol for r in stocks.results]
assert all(r.asset_type == "stock" for r in stocks.results)
assert not any("asset type" in a for a in stocks.applied_filters), stocks.applied_filters

funds = scr.screen(D.parse("ETFs with RSI below 30")[0])
assert {r.symbol for r in funds.results} == {"XLK", "SPY", "XLE", "VGT"}, [r.symbol for r in funds.results]
assert all(r.asset_type == "etf" for r in funds.results)
assert "asset type = ETF" in funds.applied_filters, funds.applied_filters

assert "has price (completeness guard)" in funds.applied_filters  # no market cap needed
assert "market cap" not in " ".join(funds.applied_filters)
assert funds.data_quality.get("sort_note"), funds.data_quality  # not an arbitrary order

tech_funds = scr.screen(D.parse("technology ETFs with RSI below 30")[0])
assert {r.symbol for r in tech_funds.results} == {"XLK", "VGT"}, [r.symbol for r in tech_funds.results]
print("✅ screener: stocks by default, funds only when asked, each result labelled")

# ── 4. LLM path: only the exact value "etf" switches to funds ──────────────
for payload, expected in (('{"asset_type": "ETF", "max_rsi": 30}', "etf"),
                          ('{"asset_type": "crypto", "max_rsi": 30}', None),
                          ('{"max_rsi": 30}', None)):
    interp._interpret_cache.clear()
    router = FakeLLMRouter(payload)
    with mock.patch("src.llm_router.get_router", return_value=router), \
         mock.patch.object(interp.settings, "use_deterministic_interpreter", False), \
         mock.patch.object(interp.logger, "warning") as warn:
        q = interp.interpret_query(f"fund query {payload}")
    assert q.asset_type == expected, (payload, q.asset_type)
    assert [f.field for f in q.filters] == ["rsi_14"], q.filters  # never a column filter
    assert not any("Unknown filter field" in str(c) for c in warn.call_args_list), warn.call_args_list
assert '"asset_type"' in interp.SYSTEM_PROMPT
print("✅ LLM path: 'etf' (any case) → funds; anything else → stocks; never a stray filter")

print("\nALL ETF CHECKS PASSED")
