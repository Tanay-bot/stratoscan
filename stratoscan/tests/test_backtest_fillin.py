"""Hermetic tests for the backtester debugging-loop fixes.

Run: python -m tests.test_backtest_fillin

Covers the two bugs the live debugging loop caught:
  Bug A (Tier 4 #9): top_n=3 → 1 symbol. backtest_top_results screened
      exactly top_n, so a delisted/unavailable symbol (BRK.A/BRK.B —
      "possibly delisted; no timezone found") silently shrank the batch.
      Fix: screen a superset, backtest in rank order, promote the
      next-ranked candidate when one has no data.
  Bug B: the screener's relax message (0-match rescue) was thrown away by
      backtest_top_results, so a relaxed backtest returned message=None.
      Fix: forward data_quality.message + a fill-in note to the API layer,
      which puts it on BacktestResponse.message.

All fakes are in-memory; zero network, zero LLM, zero DB.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.backtester import BacktestEngine, BacktestResult


# ── Minimal fake result ─────────────────────────────────────────────────────
def _fake_result(symbol: str) -> BacktestResult:
    return BacktestResult(
        symbol=symbol,
        strategy_params={"buy_rules": []},
        start_date="2020-01-01",
        end_date="2025-01-01",
        initial_capital=100000.0,
        final_value=110000.0,
        total_return=0.10,
        trades=[],
        equity_curve=[],
        drawdown_curve=[],
    )


# ── Fake screener whose results we control ─────────────────────────────────
class FakeScreenResult:
    def __init__(self, symbols: List[str], message: Optional[str] = None):
        self._symbols = symbols
        self.data_quality = ({"message": message} if message else {})

    @property
    def results(self):
        class _R:
            def __init__(self, s):
                self.symbol = s
        return [_R(s) for s in self._symbols]


# ── Bug A: candidate fill-in ────────────────────────────────────────────────
print("A. Candidate fill-in: delisted top symbols must be replaced, not dropped...")

engine = BacktestEngine.__new__(BacktestEngine)  # skip __init__ (no DB needed)
engine._db = mock.Mock()

# Top 4 candidates; the first two (BRK.A, BRK.B) have NO data — the old code
# would backtest exactly top_n=2 symbols and return only the ones that worked.
screen_symbols = ["BRK.A", "BRK.B", "JPM", "T", "VZ"]
no_data = {"BRK.A", "BRK.B"}

def fake_backtest(interpreted, symbols, **kwargs):
    return {s: _fake_result(s) for s in symbols if s not in no_data}

with mock.patch.object(
    engine, "backtest", side_effect=fake_backtest
), mock.patch(
    "src.screener.StockScreener",
    **{"return_value.screen.return_value": FakeScreenResult(screen_symbols)},
):
    from src.screener import StockScreener as _SS  # noqa: F401
    # Patch the class attribute the engine imports at call time
    with mock.patch("src.screener.StockScreener") as FakeScreenerCls:
        FakeScreenerCls.return_value.screen.return_value = FakeScreenResult(screen_symbols)
        results = engine.backtest_top_results(mock.Mock(), top_n=2)

assert set(results.keys()) == {"JPM", "T"}, (
    f"fill-in must promote next-ranked candidates: {list(results.keys())}"
)
assert len(results) == 2, f"must honor top_n=2: {len(results)}"
assert engine._last_fill_note and "no usable" in engine._last_fill_note, engine._last_fill_note
print(f"✅ BRK.A/BRK.B (no data) replaced → {sorted(results.keys())}")
print(f"✅ fill note: {engine._last_fill_note!r}")

# Cap: never backtest MORE than top_n even when everything has data.
with mock.patch("src.screener.StockScreener") as FakeScreenerCls:
    FakeScreenerCls.return_value.screen.return_value = FakeScreenResult(
        ["A1", "A2", "A3", "A4", "A5"]
    )
    with mock.patch.object(
        engine, "backtest",
        side_effect=lambda interpreted, symbols, **kw: {s: _fake_result(s) for s in symbols},
    ):
        results = engine.backtest_top_results(mock.Mock(), top_n=3)
assert len(results) == 3, f"cap at top_n: {len(results)}"
print(f"✅ top_n=3 capped → {sorted(results.keys())} (no over-testing)")

# No data at all → honest empty, no crash.
with mock.patch("src.screener.StockScreener") as FakeScreenerCls:
    FakeScreenerCls.return_value.screen.return_value = FakeScreenResult([])
    results = engine.backtest_top_results(mock.Mock(), top_n=3)
assert results == {}, f"no candidates → empty: {results}"
print("✅ no candidates → {} (honest empty)")

# ── Bug B: message forwarding ───────────────────────────────────────────────
print("\nB. Screener relax message + fill note forwarded to the API layer...")

# Relaxed screen (message set) + NO fill-in → only the relax note.
engine2 = BacktestEngine.__new__(BacktestEngine)
engine2._db = mock.Mock()
with mock.patch("src.screener.StockScreener") as FakeScreenerCls:
    FakeScreenerCls.return_value.screen.return_value = FakeScreenResult(
        ["ENB", "CVS"],
        message="Relaxed 1 filter(s) (volume ≥ 1000000) to show the nearest matches.",
    )
    with mock.patch.object(
        engine2, "backtest",
        side_effect=lambda interpreted, symbols, **kw: {s: _fake_result(s) for s in symbols},
    ):
        engine2.backtest_top_results(mock.Mock(), top_n=2)
assert engine2._last_screen_message and "Relaxed" in engine2._last_screen_message, \
    engine2._last_screen_message
assert engine2._last_fill_note is None, "no fill-in → no fill note"
print(f"✅ relax note forwarded: {engine2._last_screen_message!r}")

# ONE engine doing BOTH: relaxed screen AND a delisted top candidate → both
# notes set so the API layer can join them (the real pipeline joins via
# `' '.join(n for n in (screen, fill) if n)`).
engine3 = BacktestEngine.__new__(BacktestEngine)
engine3._db = mock.Mock()
with mock.patch("src.screener.StockScreener") as FakeScreenerCls:
    FakeScreenerCls.return_value.screen.return_value = FakeScreenResult(
        ["BRK.A", "ENB"],
        message="Relaxed 1 filter(s) (volume ≥ 1000000) to show the nearest matches.",
    )
    with mock.patch.object(
        engine3, "backtest",
        side_effect=lambda interpreted, symbols, **kw: {
            s: _fake_result(s) for s in symbols if s not in no_data
        },
    ):
        engine3.backtest_top_results(mock.Mock(), top_n=1)
assert engine3._last_screen_message and "Relaxed" in engine3._last_screen_message
assert engine3._last_fill_note and "no usable" in engine3._last_fill_note
joined = " ".join(n for n in (engine3._last_screen_message, engine3._last_fill_note) if n)
assert joined and "Relaxed" in joined and "no usable" in joined, joined
print(f"✅ both notes set on one engine: {joined!r}")

# ── API surface: _run_backtest_pipeline returns the note ───────────────────
print("\nC. _run_backtest_pipeline forwards the note to the endpoint...")

from src.api import _run_backtest_pipeline
from src.backtester import BacktestEngine as BE

class FakeEngineWithNotes:
    _last_screen_message = "Relaxed 1 filter(s) (volume ≥ 1000000) to show the nearest matches."
    _last_fill_note = None
    def backtest_top_results(self, interpreted, top_n=5, **kwargs):
        return {"ENB": _fake_result("ENB")}
    def backtest(self, *a, **k):
        return {"AAPL": _fake_result("AAPL")}

class _FakeInterpreted:
    error = None
    raw_input = "test"

class _FakePayload:
    query = "buy oversold stocks"
    top_n = 2
    stop_loss_pct = None
    take_profit_pct = None
    trailing_stop_pct = None
    position_sizing = None
    position_pct = None
    risk_per_trade_pct = None
    allow_short = None
    max_leverage = None
    start_date = None
    end_date = None
    # Tier 5 portfolio fields (added with the portfolio layer).
    portfolio_allocation = None
    rebalance_frequency = None
    dividend_reinvest = None
    borrow_rate_pct = None
    max_sector_exposure = None
    # Tier 3 compare mode: explicit symbol set (None = screen the universe).
    symbols = None

_FAKE_PARSED = {
    "symbols": [],
    "buy_and_hold": False,
    "start_date": None,
    "end_date": None,
}

class _FakeUser:
    def get(self, k, default=None):
        return {"email": "t@t.com", "plan": "free"}.get(k, default)

# _run_backtest_pipeline does `from src.backtester import BacktestEngine` at
# call time — patch the source module, not src.api.
with mock.patch("src.interpreter.interpret_query", return_value=_FakeInterpreted()), \
     mock.patch("src.backtester.translate_strategy", return_value={"buy_rules": []}), \
     mock.patch("src.backtester.parse_backtest_query", return_value=_FAKE_PARSED), \
     mock.patch("src.backtester.BacktestEngine", return_value=FakeEngineWithNotes()):
    rules, bt_results, screen_msg, portfolio = _run_backtest_pipeline(
        _FakePayload(), _FakeUser()
    )
assert screen_msg and "Relaxed" in screen_msg, screen_msg
assert "ENB" in bt_results, bt_results
# Tier 5: single-symbol run → portfolio is None (aggregation needs ≥2).
assert portfolio is None, portfolio
print(f"✅ pipeline returned note: {screen_msg!r} (portfolio=None for 1 symbol)")

# Endpoint assembly: non-empty results + note → message = the note.
message = None
if not bt_results:
    message = "No stocks could be backtested"
elif screen_msg:
    message = screen_msg
assert message and "Relaxed" in message, message
print(f"✅ BacktestResponse.message surfaced: {message!r}")

print()
print("🎯 All backtest fill-in tests pass!")
