"""Comprehensive end-to-end behavior probe for Stratoscan's core surfaces.

Drives the REAL pipelines (not mocks of the pipeline) with fake LLM prompts
and synthetic data, and ASSERTS the state transitions and outputs:

  1. SCREENER — plain-English prompts through the full
     interpret → screen_from_natural_language → filter → score path,
     with a FakeQualityDB (REAL filter semantics) + a prompt-aware fake
     LLM router. Verifies filter translation, derived criteria, composite
     scoring, pagination, staleness, and the generic-error contract.
  2. BACKTESTER — NL → strategy rules → run_backtest on a deterministic
     frame, hand-verified PnL (money actually changes, fees/slippage
     charged on both legs), plus the sawtooth profitability run and a
     synthetic-dividend buy-and-hold breakdown.
  3. NEWS — fake headlines through score_headlines_batch → _build_from_raw
     → refresh_news_batch orchestration; sentiment mapping, index
     alignment, truncation, aggregate scores, LLM-outage behavior.

Hermetic: zero network, zero live LLM, zero real DB.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from datetime import datetime, timedelta, timezone
from unittest import mock

import numpy as np
import pandas as pd

from src import interpreter as interp
from src import news as news_mod
from src.backtester import (
    BacktestEngine,
    run_backtest,
    store_historical_data,
    translate_strategy,
)
from src.database import SQLiteDB
from src.screener import StockScreener
from tests.fakes import FakeQualityDB


# ── Shared fake: prompt-aware LLM router ────────────────────────────────────
class PromptAwareRouter:
    """chat_completion returns canned JSON keyed by the EXACT user query."""

    def __init__(self, mapping):
        self.mapping = mapping
        self.calls: list = []

    def chat_completion(self, *, messages, **kwargs):
        self.calls.append(messages)
        user = next(m["content"] for m in messages if m["role"] == "user")
        content = self.mapping.get(user, "{}")
        _msg = type("M", (), {"content": content})()
        _choice = type("C", (), {"message": _msg, "finish_reason": "stop"})()
        return type("R", (), {"choices": [_choice]})()


def _patch_router(router):
    return mock.patch("src.llm_router.get_router", return_value=router)


def _fresh_ts(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


# ═══════════════════════════════════════════════════════════════════════════════
# 1. SCREENER — fake prompts through the real interpret→screen pipeline
# ═══════════════════════════════════════════════════════════════════════════════
print("=" * 72)
print("1. SCREENER: fake prompts → real interpret → screen → score pipeline")
print("=" * 72)

# Realistic universe across sectors, with a few trap records (small-cap
# below/above the small-cap band, loss-making, stale) to prove the filters
# actually exclude what they should.
_now = datetime.now(timezone.utc)
stale_ts = (_now - timedelta(days=40)).isoformat()
UNIVERSE = [
    {"symbol": "AAPL", "company_name": "Apple Inc", "sector": "Technology", "industry": "Hardware",
     "current_price": 180.0, "price_change_pct": 0.5, "market_cap": 2_800_000_000_000, "pe_ratio": 28.0,
     "rsi_14": 62.0, "volume": 50_000_000, "avg_volume": 60_000_000, "dividend_yield": 0.5, "beta": 1.2,
     "eps_ttm": 6.4, "sma_50": 175.0, "sma_200": 165.0, "high_52w": 195.0, "low_52w": 130.0,
     "ps_ratio": 7.5, "pb_ratio": 45.0, "ev_ebitda": 22.0, "roe_ttm": 150.0, "roa_ttm": 25.0,
     "gross_margin_ttm": 45.0, "net_margin_ttm": 25.0, "revenue_growth_ttm": 6.0, "eps_growth_ttm": 10.0,
     "atr_14": 2.0, "updated_at": _fresh_ts(5)},
    {"symbol": "MSFT", "company_name": "Microsoft Corp", "sector": "Technology", "industry": "Software",
     "current_price": 420.0, "price_change_pct": 1.0, "market_cap": 3_100_000_000_000, "pe_ratio": 35.0,
     "rsi_14": 65.0, "volume": 25_000_000, "avg_volume": 28_000_000, "dividend_yield": 0.7, "beta": 0.9,
     "eps_ttm": 12.0, "sma_50": 410.0, "sma_200": 380.0, "high_52w": 450.0, "low_52w": 300.0,
     "ps_ratio": 13.0, "pb_ratio": 12.0, "ev_ebitda": 28.0, "roe_ttm": 40.0, "roa_ttm": 18.0,
     "gross_margin_ttm": 68.0, "net_margin_ttm": 35.0, "revenue_growth_ttm": 15.0, "eps_growth_ttm": 18.0,
     "atr_14": 8.0, "updated_at": _fresh_ts(5)},
    {"symbol": "NVDA", "company_name": "NVIDIA Corp", "sector": "Technology", "industry": "Semiconductors",
     "current_price": 138.0, "price_change_pct": 2.0, "market_cap": 3_400_000_000_000, "pe_ratio": 55.0,
     "rsi_14": 70.0, "volume": 200_000_000, "avg_volume": 180_000_000, "dividend_yield": 0.03, "beta": 1.7,
     "eps_ttm": 2.5, "sma_50": 120.0, "sma_200": 95.0, "high_52w": 140.0, "low_52w": 55.0,
     "ps_ratio": 30.0, "pb_ratio": 55.0, "ev_ebitda": 40.0, "roe_ttm": 120.0, "roa_ttm": 55.0,
     "gross_margin_ttm": 75.0, "net_margin_ttm": 50.0, "revenue_growth_ttm": 55.0, "eps_growth_ttm": 60.0,
     "atr_14": 5.0, "updated_at": _fresh_ts(5)},
    {"symbol": "SMCI", "company_name": "Super Micro Computer", "sector": "Technology", "industry": "Hardware",
     "current_price": 15.0, "price_change_pct": 1.5, "market_cap": 800_000_000, "pe_ratio": 14.0,
     "rsi_14": 45.0, "volume": 4_000_000, "avg_volume": 5_000_000, "dividend_yield": 0.0, "beta": 1.5,
     "eps_ttm": 1.1, "sma_50": 14.0, "sma_200": 12.0, "high_52w": 18.0, "low_52w": 8.0,
     "ps_ratio": 2.0, "pb_ratio": 6.0, "ev_ebitda": 10.0, "roe_ttm": 8.0, "roa_ttm": 4.0,
     "gross_margin_ttm": 20.0, "net_margin_ttm": 5.0, "revenue_growth_ttm": 25.0, "eps_growth_ttm": 30.0,
     "atr_14": 1.0, "updated_at": _fresh_ts(5)},
    {"symbol": "PICO", "company_name": "Pico Tech", "sector": "Technology", "industry": "Software",
     "current_price": 8.0, "price_change_pct": 0.0, "market_cap": 200_000_000, "pe_ratio": 9.0,
     "rsi_14": 50.0, "volume": 500_000, "avg_volume": 600_000, "dividend_yield": 0.0, "beta": 1.8,
     "eps_ttm": 0.9, "sma_50": 8.0, "sma_200": 8.0, "high_52w": 10.0, "low_52w": 5.0,
     "ps_ratio": 1.0, "pb_ratio": 3.0, "ev_ebitda": 5.0, "roe_ttm": 6.0, "roa_ttm": 3.0,
     "gross_margin_ttm": 40.0, "net_margin_ttm": 8.0, "revenue_growth_ttm": 10.0, "eps_growth_ttm": 5.0,
     "atr_14": 0.5, "updated_at": _fresh_ts(5)},
    {"symbol": "LARG", "company_name": "Large Tech Co", "sector": "Technology", "industry": "Software",
     "current_price": 30.0, "price_change_pct": 0.2, "market_cap": 2_500_000_000, "pe_ratio": 20.0,
     "rsi_14": 55.0, "volume": 2_000_000, "avg_volume": 2_500_000, "dividend_yield": 0.5, "beta": 1.1,
     "eps_ttm": 1.5, "sma_50": 29.0, "sma_200": 28.0, "high_52w": 32.0, "low_52w": 20.0,
     "ps_ratio": 3.0, "pb_ratio": 4.0, "ev_ebitda": 12.0, "roe_ttm": 10.0, "roa_ttm": 5.0,
     "gross_margin_ttm": 30.0, "net_margin_ttm": 9.0, "revenue_growth_ttm": 12.0, "eps_growth_ttm": 8.0,
     "atr_14": 1.0, "updated_at": _fresh_ts(5)},
    {"symbol": "JNJ", "company_name": "Johnson & Johnson", "sector": "Healthcare", "industry": "Pharma",
     "current_price": 160.0, "price_change_pct": 0.1, "market_cap": 390_000_000_000, "pe_ratio": 15.0,
     "rsi_14": 55.0, "volume": 8_000_000, "avg_volume": 9_000_000, "dividend_yield": 3.1, "beta": 0.5,
     "eps_ttm": 10.7, "sma_50": 165.0, "sma_200": 168.0, "high_52w": 175.0, "low_52w": 145.0,
     "ps_ratio": 4.5, "pb_ratio": 5.0, "ev_ebitda": 12.0, "roe_ttm": 25.0, "roa_ttm": 10.0,
     "gross_margin_ttm": 68.0, "net_margin_ttm": 20.0, "revenue_growth_ttm": 3.0, "eps_growth_ttm": 4.0,
     "atr_14": 1.5, "updated_at": _fresh_ts(5)},
    {"symbol": "PFE", "company_name": "Pfizer Inc", "sector": "Healthcare", "industry": "Pharma",
     "current_price": 28.0, "price_change_pct": -0.5, "market_cap": 160_000_000_000, "pe_ratio": 12.0,
     "rsi_14": 32.0, "volume": 40_000_000, "avg_volume": 45_000_000, "dividend_yield": 5.8, "beta": 0.6,
     "eps_ttm": 2.3, "sma_50": 30.0, "sma_200": 32.0, "high_52w": 34.0, "low_52w": 24.0,
     "ps_ratio": 2.5, "pb_ratio": 1.8, "ev_ebitda": 9.0, "roe_ttm": 10.0, "roa_ttm": 6.0,
     "gross_margin_ttm": 55.0, "net_margin_ttm": 22.0, "revenue_growth_ttm": -2.0, "eps_growth_ttm": -5.0,
     "atr_14": 0.6, "updated_at": _fresh_ts(5)},
    {"symbol": "XOM", "company_name": "Exxon Mobil", "sector": "Energy", "industry": "Oil & Gas",
     "current_price": 110.0, "price_change_pct": 0.8, "market_cap": 440_000_000_000, "pe_ratio": 13.0,
     "rsi_14": 58.0, "volume": 20_000_000, "avg_volume": 22_000_000, "dividend_yield": 3.4, "beta": 0.9,
     "eps_ttm": 8.5, "sma_50": 108.0, "sma_200": 105.0, "high_52w": 118.0, "low_52w": 95.0,
     "ps_ratio": 1.2, "pb_ratio": 2.2, "ev_ebitda": 6.0, "roe_ttm": 18.0, "roa_ttm": 10.0,
     "gross_margin_ttm": 30.0, "net_margin_ttm": 10.0, "revenue_growth_ttm": 5.0, "eps_growth_ttm": 8.0,
     "atr_14": 1.8, "updated_at": _fresh_ts(5)},
    {"symbol": "GME", "company_name": "GameStop Corp", "sector": "Consumer Cyclical", "industry": "Retail",
     "current_price": 22.0, "price_change_pct": 3.0, "market_cap": 900_000_000, "pe_ratio": -5.0,
     "rsi_14": 60.0, "volume": 30_000_000, "avg_volume": 25_000_000, "dividend_yield": 0.0, "beta": 3.0,
     "eps_ttm": -0.4, "sma_50": 21.0, "sma_200": 19.0, "high_52w": 30.0, "low_52w": 12.0,
     "ps_ratio": 0.8, "pb_ratio": 2.0, "ev_ebitda": -20.0, "roe_ttm": -10.0, "roa_ttm": -5.0,
     "gross_margin_ttm": 20.0, "net_margin_ttm": -5.0, "revenue_growth_ttm": -5.0, "eps_growth_ttm": -10.0,
     "atr_14": 3.0, "updated_at": stale_ts},  # deliberately STALE
]

FAKE_DB = FakeQualityDB(UNIVERSE)
SCREENER = StockScreener(db=FAKE_DB)

PROMPTS = {
    "undervalued tech stocks with momentum":
        '{"sector":"Technology","max_pe_ratio":15,"momentum":"bullish","above_sma_50":true}',
    "oversold large cap dividend stocks":
        '{"min_market_cap_billions":10.0,"min_dividend_yield":3.0,"max_rsi":35}',
    "stocks near their 52-week highs with strong momentum":
        '{"near_52w_high":true,"golden_cross":true,"momentum":"bullish"}',
    "small cap growth stocks":
        '{"sector":"Technology","min_market_cap_billions":0.3,"max_market_cap_billions":2.0}',
    "   ":
        "{}",
}

ROUTER = PromptAwareRouter(PROMPTS)


def run_screen(query, **kw):
    """Run the REAL screen_from_natural_language with cache disabled."""
    interp._interpret_cache.clear()
    with _patch_router(ROUTER), mock.patch.object(
        interp.settings, "cache_enabled", False
    ):
        return SCREENER.screen_from_natural_language(query, **kw)


# 1a. Undervalued tech with momentum
rep = run_screen("undervalued tech stocks with momentum")
assert rep.total_matches == 1, f"expected only SMCI, got {rep.total_matches}"
assert rep.results[0].symbol == "SMCI"
assert rep.results[0].score is not None and 0 <= rep.results[0].score <= 100
assert "sector = Technology" in rep.applied_filters
assert "pe_ratio ≤ 15" in rep.applied_filters
# Momentum + above_sma_50 must both translate to price>SMA50 — and the
# echo must reflect the honest set (dedup, no double claim).
assert any("SMA50" in f for f in rep.applied_filters), rep.applied_filters
assert rep.results[0].why_matched, "why_matched must be populated"
# NVDA is tech but pe=55 → excluded; GME is cheap but not tech → excluded.
symbols = [r.symbol for r in rep.results]
assert "NVDA" not in symbols and "GME" not in symbols
print("  ✅ undervalued tech momentum → SMCI only; filters + score + why_matched")

# 1b. Oversold large-cap dividend
rep = run_screen("oversold large cap dividend stocks")
assert rep.total_matches == 1 and rep.results[0].symbol == "PFE", (
    f"expected PFE only, got {[r.symbol for r in rep.results]}"
)
# PFE: mc 160B ≥ 10B, div 5.8 ≥ 3, rsi 32 ≤ 35 — all three bounds enforced.
# XOM (div 3.4) fails rsi; JNJ (rsi 55) fails rsi; KO-like records absent.
print("  ✅ oversold large-cap dividend → PFE only (mc + yield + rsi ANDed)")

# 1c. Near 52w highs + momentum (derived criteria → SQL expressions)
rep = run_screen("stocks near their 52-week highs with strong momentum")
assert rep.total_matches == 1 and rep.results[0].symbol == "NVDA", (
    f"expected NVDA only, got {[r.symbol for r in rep.results]}"
)
assert any("52w high" in f for f in rep.applied_filters), rep.applied_filters
assert any("golden cross" in f for f in rep.applied_filters), rep.applied_filters
# NVDA price 138 ≥ 0.95*140 (52w high) AND sma_50 120 > sma_200 95 AND
# price 138 > sma_50 120 — the derived trio all enforced.
print("  ✅ near-52w-high + golden cross + bullish → NVDA only (derived SQL)")

# 1d. Small-cap growth: BOTH market-cap bounds enforced
rep = run_screen("small cap growth stocks")
assert rep.total_matches == 1 and rep.results[0].symbol == "SMCI", (
    f"expected SMCI only, got {[r.symbol for r in rep.results]}"
)
# PICO (0.2B, below min) and LARG (2.5B, above max) must BOTH be excluded —
# proving the min AND max bounds are ANDed, not last-wins.
symbols = [r.symbol for r in rep.results]
assert "PICO" not in symbols and "LARG" not in symbols
print("  ✅ small-cap band enforced both ends (0.2B and 2.5B excluded)")

# 1e. Empty query → completeness guard only, whole universe, no LLM call
interp._interpret_cache.clear()
router_never = mock.Mock()
with _patch_router(router_never), mock.patch.object(
    interp.settings, "cache_enabled", True
):
    rep = SCREENER.screen_from_natural_language("   ")
assert not router_never.chat_completion.called, "empty query must not call LLM"
# The freshness guard excludes the one deliberately-stale row (GME, 40 days).
assert rep.total_matches == len(UNIVERSE) - 1, f"expected {len(UNIVERSE)-1}, got {rep.total_matches}"
# Pagination: limit 3 → has_more True, exactly 3 rows.
rep_page = run_screen("   ", limit=3)
assert len(rep_page.results) == 3 and rep_page.has_more is True
assert rep_page.total_matches == len(UNIVERSE) - 1
print("  ✅ empty query → universe minus stale rows, pagination has_more works")

# 1f. Composite score sort: global ranking by score, not page re-order
rep = run_screen("   ", sort_by="score", limit=5)
scores = [r.score for r in rep.results]
assert scores == sorted(scores, reverse=True), f"score sort broken: {scores}"
assert all(s is not None for s in scores)
print("  ✅ score sort ranks globally desc")

# 1g. Freshness guard: GME updated 40 days ago must be EXCLUDED from
# results entirely (the guard filters stale rows out of SQL, not just flags
# them). A 5-min-old row still passes and is marked fresh.
rep = run_screen("   ")
stale_symbols = [r.symbol for r in rep.results]
assert "GME" not in stale_symbols, "40-day-old row must be excluded by the freshness guard"
fresh = [r for r in rep.results if r.symbol == "AAPL"]
assert fresh and fresh[0].is_stale is False, "5-min-old row must be present and fresh"
print("  ✅ freshness guard excludes the 40-day-old row; fresh rows pass")

# 1h. LLM failure → generic error, NO raw exception text leaked (VULN-002)
boom_router = mock.Mock()
boom_router.chat_completion.side_effect = RuntimeError("provider exploded")
interp._interpret_cache.clear()
with _patch_router(boom_router), mock.patch.object(
    interp.settings, "cache_enabled", False
):
    rep = SCREENER.screen_from_natural_language("any prompt")
assert rep.total_matches == 0
assert rep.query.error is not None and "provider exploded" in rep.query.error
assert "provider exploded" not in str(rep.data_quality), (
    "raw exception text must never leak into the response"
)
print("  ✅ LLM outage → generic data_quality error, no exception leak")

print("SCREENER: ALL CHECKS PASSED ✅")
print()


# ═══════════════════════════════════════════════════════════════════════════════
# 2. BACKTESTER — NL→rules→sim, hand-verified PnL (money actually changes)
# ═══════════════════════════════════════════════════════════════════════════════
print("=" * 72)
print("2. BACKTESTER: NL → strategy → simulation with hand-checked money math")
print("=" * 72)

# 2a. NL prompt → executable strategy rules (real translate_strategy path).
rules_router = PromptAwareRouter({
    "buy oversold stocks with a stop loss of 8% and take profit 20%":
        '{"max_rsi":35}',
    "short overvalued stocks with 2x leverage":
        '{"max_pe_ratio":25}',
})
interp._interpret_cache.clear()
with _patch_router(rules_router), mock.patch.object(
    interp.settings, "cache_enabled", False
):
    iq = interp.interpret_query(
        "buy oversold stocks with a stop loss of 8% and take profit 20%"
    )
rules = translate_strategy(iq)
assert rules["stop_loss"] == 0.08 and rules["take_profit"] == 0.20, rules
assert "rsi < 35" in rules["buy_rules"], rules
assert rules["fee_rate"] == 0.001 and rules["slippage"] == 0.0005
print("  ✅ NL 'stop loss 8%, take profit 20%' → enforceable sim params")

interp._interpret_cache.clear()
with _patch_router(rules_router), mock.patch.object(
    interp.settings, "cache_enabled", False
):
    iq2 = interp.interpret_query("short overvalued stocks with 2x leverage")
rules2 = translate_strategy(iq2)
assert rules2["allow_short"] is True and rules2["max_leverage"] == 2.0, rules2
# P/E is a screening filter, never a tradeable rule: the backtest frame has
# no per-bar P/E column, so `pe < 25` would silently compare PRICE to 25
# (_apply_rule maps pe→Close). The screener applies real P/E at selection.
assert not any(x.split()[0] == "pe" for x in rules2["buy_rules"]), (
    f"pe must not leak into tradeable rules: {rules2}"
)
print("  ✅ NL 'short with 2x leverage' → allow_short + leverage cap (pe stays a screening filter)")

# 2b. Deterministic PnL: ONE round trip, fees + slippage on BOTH legs.
# Bars (business days): signal at d3 close (49 < 50) → fill at d4 OPEN
# 50*(1+0.0005)=50.025; exit signal at d6 close (61 > 60) → fill at d7
# OPEN 61*(1-0.0005)=60.9695. Hand-computed:
#   shares   = int(10000 / (50.025 * 1.001)) = 199
#   buy cost = 199 * 50.025 * 1.001          = 9964.93  → cash 35.07
#   proceeds = 199 * 60.9695 * 0.999         = 12120.80
#   final    = 35.07 + 12120.80              = 12155.87
_PNL_IDX = pd.date_range("2024-01-01", periods=7, freq="B")
_PNL_DF = pd.DataFrame({
    "Open":  [100, 100, 100, 50, 51, 60, 61],
    "High":  [101, 101, 101, 52, 53, 62, 62],
    "Low":   [ 99,  99,  99, 49, 50, 59, 60],
    "Close": [ 99,  99,  49, 51, 52, 61, 61],
    "Volume": np.full(7, 1_000_000),
}, index=_PNL_IDX)
pnl_rules = {
    "initial_capital": 10_000.0, "fee_rate": 0.001, "slippage": 0.0005,
    "buy_rules": ["price < 50"], "sell_rules": ["price > 60"],
}
pnl = run_backtest("PNL", pnl_rules, "2024-01-01", "2024-01-31", df=_PNL_DF)
assert pnl.total_trades == 1, f"expected 1 trade, got {pnl.total_trades}"
t = pnl.trades[0]
assert t.buy_date == "2024-01-04", f"buy fill date {t.buy_date}"
assert abs(t.buy_price - 50.025) < 1e-9, f"buy fill {t.buy_price} != 50.025"
assert t.sell_date == "2024-01-09", f"sell fill date {t.sell_date}"
assert abs(t.sell_price - 60.9695) < 1e-9, f"sell fill {t.sell_price} != 60.9695"
assert abs(t.pnl - 2155.87) < 0.1, f"trade PnL {t.pnl:.2f} != ~2155.87"
assert abs(pnl.final_value - 12155.87) < 0.1, f"final {pnl.final_value:.2f} != ~12155.87"
assert abs(pnl.total_return - 0.215587) < 0.0005, pnl.total_return
assert pnl.final_value > pnl.initial_capital, "money must actually increase"
print(
    f"  ✅ hand-verified PnL: buy {t.buy_price:.3f} → sell {t.sell_price:.3f}, "
    f"pnl ${t.pnl:.2f}, final ${pnl.final_value:.2f} (fees+slippage both legs)"
)

# 2c. Sawtooth profitability (mean-reversion regime) via the real NL→rules path.
# NOTE: must use the PLAIN oversold prompt (no stop/take) — that is the
# regime test_backtester validates as profitable. Adding "stop loss 8% +
# take profit 20%" here is a LEGITIMATELY LOSING falling-knife config on
# this high-amplitude oscillation (probe 2f proves the stops fire); the
# engine is faithful, the strategy is bad.
_ND = 6 * 252
_IDX = pd.date_range("2020-01-01", periods=_ND, freq="B")
_CLOSE = 50 + 30 * np.sin(np.arange(_ND) / 40 * 2 * np.pi) + np.linspace(0, 20, _ND)
_DF = pd.DataFrame({
    "Open": _CLOSE, "High": _CLOSE * 1.01, "Low": _CLOSE * 0.99,
    "Close": _CLOSE, "Volume": np.full(_ND, 1_000_000),
}, index=_IDX)
plain_router = PromptAwareRouter({"oversold stocks": '{"max_rsi":35}'})
interp._interpret_cache.clear()
with _patch_router(plain_router), mock.patch.object(
    interp.settings, "cache_enabled", False
):
    iq_plain = interp.interpret_query("oversold stocks")
plain_rules = translate_strategy(iq_plain)
assert "rsi < 35" in plain_rules["buy_rules"]
assert plain_rules.get("stop_loss") is None, "plain prompt must carry no stops"
saw = run_backtest("SAW", plain_rules, "2020-01-01", "2025-12-31", df=_DF)
assert saw.total_trades >= 3 and saw.total_return > 0, (
    f"sawtooth should profit: trades={saw.total_trades} ret={saw.total_return:.2%}"
)
assert saw.win_rate is not None and saw.win_rate >= 0.5
assert all(not tr.is_open for tr in saw.trades)
assert saw.monte_carlo is not None and saw.monte_carlo["available"]
assert saw.confidence_intervals is not None and saw.confidence_intervals["available"]
print(
    f"  ✅ sawtooth: {saw.total_trades} trades, ret {saw.total_return:.1%}, "
    f"win {saw.win_rate:.0%}, MC+CI present"
)

# 2f. Protective stops genuinely FIRE on real data (the falling-knife config
# must produce stop_loss exits — proving intra-bar stops work end-to-end,
# not that this particular strategy is profitable).
stop_take = {**plain_rules, "stop_loss": 0.08, "take_profit": 0.20}
sk = run_backtest("SK", stop_take, "2020-01-01", "2025-12-31", df=_DF)
reasons = {}
for tr in sk.trades:
    reasons[tr.exit_reason] = reasons.get(tr.exit_reason, 0) + 1
assert reasons.get("stop_loss", 0) >= 1, f"stops must fire: {reasons}"
assert reasons.get("take_profit", 0) >= 1, f"targets must fire: {reasons}"
assert all(not tr.is_open for tr in sk.trades)
print(
    f"  ✅ stops + targets fire on real data (exit reasons: {reasons}); "
    "falling-knife config correctly loses — engine is faithful"
)

# 2d. Synthetic dividends: price-only vs total-return breakdown.
_hold = pd.DataFrame({
    "Open": [100, 100, 100, 100], "High": [100, 100, 100, 100],
    "Low": [100, 100, 100, 100], "Close": [100, 100, 100, 100],
    "Adj Close": [100, 105, 105, 105], "Volume": np.full(4, 1_000_000),
}, index=pd.date_range("2024-01-01", periods=4, freq="B"))
hold = run_backtest("HOLD", pnl_rules, "2024-01-01", "2024-01-31", df=_hold)
assert hold.buy_hold_price_return is not None
assert abs(hold.buy_hold_price_return) < 1e-9, hold.buy_hold_price_return
assert abs(hold.buy_hold_total_return - 0.05) < 1e-9
assert abs(hold.dividend_contribution - 0.05) < 1e-9
print("  ✅ dividend contribution isolated: price 0% vs total 5%")

# 2e. Full engine chain on SQLite (real store → rebuild → sim), no network.
import tempfile
with tempfile.TemporaryDirectory() as tmp:
    db = SQLiteDB(db_path=os.path.join(tmp, "probe.db"))
    db.init_schema()  # SQLiteDB requires explicit init (test convention)
    store_historical_data(db, "SAW", _DF)
    store_historical_data(db, "SPY", _DF)
    engine = BacktestEngine(db=db)
    from src.interpreter import InterpretedQuery
    with mock.patch("src.backtester.fetch_historical_data") as m_fetch:
        results = engine.backtest(
            InterpretedQuery(raw_input="oversold", filters=[]),
            ["SAW"], "2020-01-01", "2025-12-31",
        )
        m_fetch.assert_not_called()
    assert "SAW" in results
    r = results["SAW"]
    assert r.total_return > 0 and r.benchmark_total_return is not None
    assert r.equity_curve and r.equity_curve[-1]["value"] == round(r.final_value, 2)
    db.close()
print("  ✅ engine chain (SQLite cache → sim → benchmark → persisted) hermetic")

print("BACKTESTER: ALL CHECKS PASSED ✅")
print()


# ═══════════════════════════════════════════════════════════════════════════════
# 3. NEWS — fake headlines through scoring → assembly → batch orchestration
# ═══════════════════════════════════════════════════════════════════════════════
print("=" * 72)
print("3. NEWS: fake headlines → real scoring, assembly, batch refresh")
print("=" * 72)


def _mk_llm(content):
    router = mock.MagicMock()
    router.chat_completion.return_value.choices[0].message.content = content
    return router


# 3a. Batched sentiment scoring across two stocks, index-aligned.
router = _mk_llm(
    '{"stocks": {"AAPL": {"articles": ['
    '{"index": 0, "sentiment": "positive", "score": 0.7},'
    '{"index": 1, "sentiment": "negative", "score": -0.5}]}, '
    '"MSFT": {"articles": [{"index": 0, "sentiment": "positive", "score": 0.6}]}}}'
)
with mock.patch("src.llm_router.get_router", return_value=router):
    res, ok = news_mod.score_headlines_batch({
        "AAPL": ["Apple beats earnings", "Apple antitrust suit"],
        "MSFT": ["Azure cloud grows"],
    })
assert ok is True
assert res["AAPL"] == [
    {"index": 0, "sentiment": "positive", "score": 0.7},
    {"index": 1, "sentiment": "negative", "score": -0.5},
]
assert res["MSFT"] == [{"index": 0, "sentiment": "positive", "score": 0.6}]
print("  ✅ batched scoring: 2 stocks, index-aligned sentiment")

# 3b. Score clamping + bad-label recompute (LLM garbage → still sane output).
router = _mk_llm(
    '{"stocks": {"TSLA": {"articles": ['
    '{"index": 0, "sentiment": "great", "score": 5.0},'
    '{"index": 1, "sentiment": "nope", "score": -3.0}]}}}'
)
with mock.patch("src.llm_router.get_router", return_value=router):
    res, _ = news_mod.score_headlines_batch({"TSLA": ["h1", "h2"]})
assert res["TSLA"][0]["score"] == 1.0 and res["TSLA"][0]["sentiment"] == "positive"
assert res["TSLA"][1]["score"] == -1.0 and res["TSLA"][1]["sentiment"] == "negative"
print("  ✅ score clamped to [-1,1]; bad label recomputed from score")

# 3c. LLM outage → all-neutral + llm_ok=False (never overwrite good data).
router_fail = mock.Mock()
router_fail.chat_completion.side_effect = RuntimeError("provider down")
with mock.patch("src.llm_router.get_router", return_value=router_fail):
    res, ok = news_mod.score_headlines_batch({"AAPL": ["h"]})
assert ok is False and res["AAPL"][0] == {"index": 0, "sentiment": "neutral", "score": 0.0}
print("  ✅ LLM outage → neutral fallback, llm_ok=False")

# 3d. Raw-article assembly: truncation, unix-ts, buzz, aggregate.
raw = [
    {"headline": "h" * 600, "summary": "s" * 1100, "source": "Reuters",
     "url": "http://x", "datetime": 1750000000},
    {"headline": "Second", "datetime": 0},
]
scores = [{"index": 0, "sentiment": "positive", "score": 0.6},
          {"index": 1, "sentiment": "neutral", "score": 0.0}]
sn = news_mod._build_from_raw("AAPL", raw, scores)
assert len(sn.articles) == 2
assert len(sn.articles[0].headline) == 500 and len(sn.articles[0].summary) == 1000
assert sn.articles[0].source == "Reuters" and sn.articles[0].sentiment == "positive"
assert sn.articles[1].published_at is None
assert sn.buzz == 2.0 and abs(sn.company_news_score - 0.3) < 1e-9
print("  ✅ assembly: truncation, timestamp, buzz, aggregate mean")

# 3e. Batch refresh orchestration: 6 symbols → 2 LLM batches, all stored.
fake_db = mock.Mock()
with mock.patch.object(news_mod, "fetch_company_news",
                       return_value=[{"headline": "h"}] * 3), \
     mock.patch.object(
         news_mod, "score_headlines_batch",
         side_effect=lambda chunks: (
             {s: [{"index": i, "sentiment": "positive", "score": 0.5}
                  for i in range(3)] for s in chunks}, True,
         ),
     ) as m_score, \
     mock.patch.object(news_mod, "_store_news", return_value=True) as m_store, \
     mock.patch("time.sleep"):
    stored = news_mod.refresh_news_batch(
        [f"S{i}" for i in range(6)], db=fake_db, pool=mock.Mock()
    )
assert stored == 6, f"all 6 symbols must store, got {stored}"
assert m_score.call_count == 2, f"6 symbols → 5+1 batches, got {m_score.call_count}"
assert m_store.call_count == 6
# Every stored article carries a real sentiment, not a neutral default.
for call in m_store.call_args_list:
    art = call.args[1].articles[0]  # _store_news(symbol, news, db) — news is arg 1
    assert art.sentiment == "positive" and art.sentiment_score == 0.5
print("  ✅ batch refresh: 6 symbols, 2 LLM batches, 6 stored, sentiment intact")

# 3f. LLM outage during refresh → 0 stored, existing sentiment preserved.
with mock.patch.object(news_mod, "fetch_company_news",
                       return_value=[{"headline": "h"}]), \
     mock.patch.object(news_mod, "score_headlines_batch",
                       return_value=({}, False)), \
     mock.patch.object(news_mod, "_store_news") as m_store2, \
     mock.patch("time.sleep"):
    stored = news_mod.refresh_news_batch(["A"], db=fake_db, pool=mock.Mock())
assert stored == 0 and not m_store2.called
print("  ✅ LLM outage in refresh → skip store (keeps good sentiment)")

print("NEWS: ALL CHECKS PASSED ✅")
print()

# ═══════════════════════════════════════════════════════════════════════════════
# 4. API /screen CACHE INTEGRITY — an LLM error must never poison the cache
# ═══════════════════════════════════════════════════════════════════════════════
print("=" * 72)
print("4. API /screen cache: errors never cached, successes are")
print("=" * 72)

# Regression: /screen used to cache every response unconditionally. After the
# screener's query.error short-circuit landed, an LLM interpretation failure
# produced an ERROR response that was cached for the 60s TTL — a transient
# provider outage made EVERY user see "could not interpret" up to a minute
# after recovery. The fix: skip the cache write when report.data_quality
# carries an error (same rule the interpreter's own cache follows).
from fastapi.testclient import TestClient  # noqa: E402

from src import api as api_mod  # noqa: E402


# FakeQualityDB stands in for the PostgresDB the StockScreener constructs
# internally (the API layer builds its own screener). The universe is empty
# so any screen returns zero matches regardless of the filters — the cache
# assertions are about error-vs-success, not result counts.
class _FlakyRouter:
    def __init__(self):
        self.error_mode = True
        self.call_count = 0

    def chat_completion(self, *, messages, **kwargs):
        self.call_count += 1
        _msg = type("M", (), {"content": "not json at all" if self.error_mode
                                  else '{"sector":"Technology"}'})()
        _c = type("C", (), {"message": _msg, "finish_reason": "stop"})()
        return type("R", (), {"choices": [_c]})()


flaky = _FlakyRouter()
with mock.patch("src.llm_router.get_router", return_value=flaky), \
     mock.patch.object(api_mod.settings, "cache_enabled", True), \
     mock.patch.object(api_mod.settings, "rate_limit_enabled", False), \
     mock.patch("src.screener.PostgresDB",
                return_value=FakeQualityDB([])):
    api_mod._screen_cache.clear()
    client = TestClient(api_mod.app)

    # Call 1: LLM broken -> error response (200 with data_quality.error)
    r1 = client.post("/screen", json={"query": "tech stocks"})
    dq1 = r1.json().get("data_quality") or {}
    assert "error" in dq1, f"call 1 should be an error response: {dq1}"
    assert flaky.call_count == 1

    # LLM 'recovers' -> call 2 with the SAME query must hit the real pipeline
    # (no cache HIT on the error) and return a clean success.
    flaky.error_mode = False
    r2 = client.post("/screen", json={"query": "tech stocks"})
    dq2 = r2.json().get("data_quality") or {}
    assert "error" not in dq2, f"stale error served from cache: {dq2}"
    assert "llm_error" not in (r2.json().get("interpreted_filters") or {}), \
        "llm_error must not leak into a successful response"
    # Call 2 must have re-run the LLM (the error was NOT cached): 2nd call.
    assert flaky.call_count == 2, "error response must not be cached"

    # Call 3 (LLM still up, SAME query): must be served FROM CACHE — the
    # success path is cached, so the LLM must NOT be invoked again.
    r3 = client.post("/screen", json={"query": "tech stocks"})
    assert "error" not in (r3.json().get("data_quality") or {})
    assert flaky.call_count == 2, \
        f"success should be served from cache (no 3rd LLM call), got {flaky.call_count}"

print("  ✅ error NOT cached (LLM re-ran); success cached (no 3rd LLM call)")
print("API CACHE: ALL CHECKS PASSED ✅")
print()

# ═══════════════════════════════════════════════════════════════════════════════
# 5. API /enrich embedded screening honors the /screen contract
# ═══════════════════════════════════════════════════════════════════════════════
print("=" * 72)
print("5. API /enrich: embedded screening carries data_quality.error + filters")
print("=" * 72)

# Regression: /enrich used to build its embedded ScreenResponse WITHOUT
# data_quality / applied_filters / sort_by / sort_desc — so an LLM
# interpretation failure looked like a bare "0 matches" (no error note)
# and success responses lost the auditable filter echo. The embed must
# mirror /screen field-for-field.
err_router = mock.MagicMock()
err_router.chat_completion.return_value.choices[0].message.content = "not json at all"
with mock.patch("src.llm_router.get_router", return_value=err_router), \
     mock.patch.object(api_mod.settings, "cache_enabled", False), \
     mock.patch.object(api_mod.settings, "rate_limit_enabled", False), \
     mock.patch("src.screener.PostgresDB", return_value=FakeQualityDB([])):
    client = TestClient(api_mod.app)
    r = client.post("/enrich", json={"query": "tech stocks", "max_stocks": 5})
    scr = r.json()["screening"]
    dq_err = (scr.get("data_quality") or {}).get("error")
    assert dq_err == "Could not interpret your query. Please try rephrasing.", dq_err
    assert scr.get("interpreted_filters", {}).get("llm_error"), "llm_error missing"
    assert r.json()["stocks_enriched"] == 0
    assert "applied_filters" in scr and "sort_by" in scr and "sort_desc" in scr
print("  ✅ /enrich LLM failure -> screening.data_quality.error + llm_error + filters")

# Success path: with a working LLM the embedded screening must echo the
# exact enforced filters + ranking, mirroring /screen field-for-field
# (same builder). Applied filters must be non-empty and sort defaults must
# match what /enrich actually passed to _run_screen (market_cap desc).
class _OkRouter:
    def chat_completion(self, *, messages, **kwargs):
        _msg = type("M", (), {"content": '{"sector":"Technology"}'})()
        _c = type("C", (), {"message": _msg, "finish_reason": "stop"})()
        return type("R", (), {"choices": [_c]})()

with mock.patch("src.llm_router.get_router", return_value=_OkRouter()), \
     mock.patch.object(api_mod.settings, "cache_enabled", False), \
     mock.patch.object(api_mod.settings, "rate_limit_enabled", False), \
     mock.patch("src.screener.PostgresDB",
                return_value=FakeQualityDB([
                    {"symbol": "SMCI", "company_name": "Supermicro",
                     "sector": "Technology", "industry": "Hardware",
                     "current_price": 50.0, "price_change_pct": 1.2,
                     "market_cap": 40_000_000_000, "pe_ratio": 10.0,
                     "rsi_14": 62.0, "volume": 5_000_000, "dividend_yield": 0.0,
                     "beta": 1.8, "eps_ttm": 5.0, "sma_50": 48.0,
                     "sma_200": 45.0, "high_52w": 55.0, "low_52w": 30.0,
                     "atr_14": 1.0, "updated_at": _fresh_ts(5)},
                    {"symbol": "NVDA", "company_name": "Nvidia",
                     "sector": "Technology", "industry": "Semiconductors",
                     "current_price": 900.0, "price_change_pct": 2.1,
                     "market_cap": 2_200_000_000_000, "pe_ratio": 62.0,
                     "rsi_14": 58.0, "volume": 60_000_000, "dividend_yield": 0.0,
                     "beta": 1.7, "eps_ttm": 14.5, "sma_50": 880.0,
                     "sma_200": 700.0, "high_52w": 950.0, "low_52w": 480.0,
                     "atr_14": 25.0, "updated_at": _fresh_ts(5)},
                ])):
    client = TestClient(api_mod.app)
    r = client.post("/enrich", json={"query": "tech stocks", "max_stocks": 5})
    scr = r.json()["screening"]
    assert not (scr.get("data_quality") or {}).get("error"), \
        f"success path must not carry data_quality.error: {scr.get('data_quality')}"
    assert scr.get("applied_filters"), f"applied_filters empty on success: {scr}"
    assert any("Technology" in f for f in scr["applied_filters"]), \
        f"enforced sector filter not echoed: {scr['applied_filters']}"
    assert scr.get("sort_by") == "market_cap", scr.get("sort_by")
    assert scr.get("sort_desc") is True, scr.get("sort_desc")
    # Universe here is the 2-stock tech set (SMCI + NVDA), limit defaults
    # to 50, so the full result set fits on one page.
    assert scr.get("total_matches") == 2, scr.get("total_matches")
    # Pagination stride must come from the SAME helper as /screen: a
    # has_more page strides by limit (offset + limit), a last page is None.
    # Regression: /screen and /enrich used to each recompute next_offset,
    # and a copy-pasted block diverging is the bug class the shared
    # builder exists to prevent.
    assert scr.get("has_more") is False, scr.get("has_more")
    assert scr.get("next_offset") is None, scr.get("next_offset")
    # Page 1 of a multi-page set (limit=1 of 2 matches): next_offset must
    # stride by the page size (offset 0 + limit 1 = 1) exactly like /screen.
    r2 = client.post("/enrich", json={"query": "tech stocks", "max_stocks": 5, "limit": 1})
    scr2 = r2.json()["screening"]
    assert scr2.get("total_matches") == 2, scr2.get("total_matches")
    assert scr2.get("has_more") is True, scr2.get("has_more")
    assert scr2.get("next_offset") == 1, scr2.get("next_offset")

    # Strongest lock against THIS bug class: /screen and /enrich embed the
    # SAME screening object for the same query — field-for-field identical.
    # Both route through _build_screen_response, so a divergence now means
    # the helper itself regressed; this assertion catches that the instant
    # it happens.
    rs = client.post("/screen", json={"query": "tech stocks", "limit": 1})
    scr_s = rs.json()
    scr_e = r2.json()["screening"]
    for field in ("query", "total_matches", "limit", "offset", "has_more",
                  "next_offset", "sort_by", "sort_desc", "applied_filters",
                  "data_quality", "interpreted_filters"):
        assert scr_s.get(field) == scr_e.get(field), \
            f"/screen vs /enrich diverged on '{field}': " \
            f"{scr_s.get(field)!r} vs {scr_e.get(field)!r}"
    # Same result rows too (symbols in the same order).
    assert [x["symbol"] for x in scr_s["results"]] == \
           [x["symbol"] for x in scr_e["results"]], "result rows diverged"
print("  ✅ /enrich success -> applied_filters echoed + sort_by/sort_desc parity")
print("  ✅ /screen vs /enrich: screening block field-for-field identical")
print("API ENRICH CONTRACT: ALL CHECKS PASSED ✅")
print()

mock.patch.stopall()
print()
print("🎯 FULL BACKEND BEHAVIOR PROBE: ALL SECTIONS PASSED")
