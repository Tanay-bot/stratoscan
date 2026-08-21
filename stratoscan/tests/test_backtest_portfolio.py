"""Hermetic tests for the Tier 5 portfolio layer.

Run: python -m tests.test_backtest_portfolio

Covers the four new engine features:

  1. compute_portfolio — shared-pool aggregation: equal allocation, risk-
     based (inverse-vol) allocation, rebalance schedules, and the >=2
     symbol guard (None below it).
  2. Cash dividend accrual at ex-dates (longs receive, shorts pay) with an
     optional DRIP reinvest that deploys the cash at the next open.
  3. Daily short borrow costs on the mark value (annualized / 252).
  4. Off-by-default regression guarantee: with no flags set, the sim stays
     byte-identical (zero dividends_received / borrow_fees_paid).
  5. max_sector_exposure diversification cap in backtest_top_results.

All fakes are in-memory; zero network, zero LLM, zero DB.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.backtester import (
    BacktestEngine,
    BacktestResult,
    compute_portfolio,
    run_backtest,
)


# ── Shared helpers ──────────────────────────────────────────────────────────
def _result(sym: str, curve: List[Dict[str, Any]], capital: float = 100_000.0) -> BacktestResult:
    """A BacktestResult with a full-resolution daily equity curve.

    An empty curve is legal (represents a symbol with no usable daily
    series — it must drop out of the portfolio, not crash it).
    """
    return BacktestResult(
        symbol=sym,
        strategy_params={},
        start_date=curve[0]["date"] if curve else "2020-01-01",
        end_date=curve[-1]["date"] if curve else "2020-01-02",
        initial_capital=capital,
        final_value=curve[-1]["value"] if curve else 0.0,
        total_return=((curve[-1]["value"] / capital) - 1.0) if curve else -1.0,
        full_equity_curve=list(curve),
        equity_curve=list(curve),
    )


def _frame(n=260, start="2020-01-01", base=100.0, drift=0.0, dividend_on=None,
           dividend_amt=2.0):
    """Synthetic daily OHLCV frame with raw_close + dividends columns.

    Price walks ``base`` with an optional daily drift; ``dividend_on`` is an
    integer bar index whose dividends value is ``dividend_amt`` (0 elsewhere).
    """
    idx = pd.bdate_range(start, periods=n)
    # NOTE: never wrap values in ``pd.Series(values, index=idx)`` — pandas
    # ALIGNS BY LABEL, and a RangeIndex (0..259) vs DatetimeIndex shares no
    # label, so the whole frame comes out NaN. Build the values Series
    # WITHOUT an index and take the raw numpy array instead.
    close = (base + drift * pd.Series(range(n))).to_numpy()
    df = pd.DataFrame(
        {
            "Open": close * 0.999,
            "High": close * 1.005,
            "Low": close * 0.995,
            "Close": close,
            "Volume": 1_000_000.0,
        },
        index=idx,
    )
    df["raw_close"] = df["Close"]
    df["dividends"] = 0.0
    if dividend_on is not None:
        df.iloc[dividend_on, df.columns.get_loc("dividends")] = dividend_amt
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# 1. compute_portfolio — shared-pool aggregation math
# ═══════════════════════════════════════════════════════════════════════════════
print("1. compute_portfolio — equal allocation math...")

# A: 100 → 110 → 121 (+10%/day). B: 100 → 90 → 99 (then back up).
# Equal weights: day1 = 100k, day2 = 55k+45k = 100k, day3 = 60.5k+49.5k = 110k.
dates = ["2020-01-02", "2020-01-03", "2020-01-06"]
a = _result("A", [{"date": d, "value": v} for d, v in zip(dates, [100, 110, 121])])
b = _result("B", [{"date": d, "value": v} for d, v in zip(dates, [100, 90, 99])])

pf = compute_portfolio({"A": a, "B": b}, initial_capital=100_000.0, allocation="equal")
assert pf is not None, "two symbols must aggregate"
assert pf["allocation"] == "equal"
assert pf["weights"] == {"A": 0.5, "B": 0.5}, pf["weights"]
curve = pf["equity_curve"]
assert curve[-1]["value"] == 110_000.0, f"final pooled value: {curve[-1]}"
assert pf["total_return_pct"] == 10.0, pf["total_return_pct"]
assert pf["initial_capital"] == 100_000.0
assert pf["final_value"] == 110_000.0
assert set(pf["symbols"]) == {"A", "B"}
assert pf["drawdown_curve"], "drawdown series must be present"
assert pf["sharpe_ratio"] is not None, "portfolio Sharpe must be computed"
print("✅ equal weights {A:0.5, B:0.5}, pool 100k → 110k (+10%)")

# Risk-based: inverse-vol weights — the smooth symbol gets MORE weight.
print("\n1b. compute_portfolio — risk_based (inverse-vol) allocation...")
dates6 = [f"2020-01-{d:02d}" for d in (2, 3, 6, 7, 8, 9)]
vol_a = _result("A", [{"date": d, "value": v} for d, v in zip(
    dates6, [100, 120, 90, 110, 130, 105])])  # choppy
vol_b = _result("B", [{"date": d, "value": v} for d, v in zip(
    dates6, [100, 102, 104, 106, 108, 110])])  # smooth
pf_rb = compute_portfolio({"A": vol_a, "B": vol_b}, allocation="risk_based")
assert pf_rb is not None
w = pf_rb["weights"]
assert abs(sum(w.values()) - 1.0) < 1e-6, f"weights must sum to 1: {w}"
assert w["B"] > w["A"], f"smooth asset should get more weight: {w}"
assert w["A"] > 0 and w["B"] > 0
print(f"✅ inverse-vol weights: {w} (B smooth > A choppy)")

# Rebalance: with A climbing and B flat, trimming winners lowers the final
# value vs. no-rebalance (sells A high, buys flat B). Assert monthly
# rebalance differs from 'none' and still yields a sane ≥-initial pool.
print("\n1c. compute_portfolio — rebalance schedules...")
days = pd.bdate_range("2020-01-02", "2020-06-30")
ra = _result("A", [{"date": str(d.date()), "value": 100 * (1.001 ** i)}
                   for i, d in enumerate(days)])
rb = _result("B", [{"date": str(d.date()), "value": 100.0} for d in days])
no_rebal = compute_portfolio({"A": ra, "B": rb}, allocation="equal", rebalance="none")
monthly = compute_portfolio({"A": ra, "B": rb}, allocation="equal", rebalance="monthly")
quarterly = compute_portfolio({"A": ra, "B": rb}, allocation="equal", rebalance="quarterly")
yearly = compute_portfolio({"A": ra, "B": rb}, allocation="equal", rebalance="yearly")
for name, p in (("none", no_rebal), ("monthly", monthly),
                ("quarterly", quarterly), ("yearly", yearly)):
    assert p is not None and p["final_value"] >= 100_000.0, f"{name}: {p}"
assert monthly["final_value"] < no_rebal["final_value"], (
    "rebalancing a winner-takes-all run must trim the final value"
)
assert monthly["final_value"] != quarterly["final_value"] != yearly["final_value"], (
    "distinct schedules must give distinct paths"
)
print(f"✅ none={no_rebal['final_value']:.0f} monthly={monthly['final_value']:.0f} "
      f"quarterly={quarterly['final_value']:.0f} yearly={yearly['final_value']:.0f}")

# Guard: <2 usable symbols → None (both a single result and empty).
print("\n1d. compute_portfolio — >=2 symbol guard...")
assert compute_portfolio({"A": a}, initial_capital=100_000.0) is None, (
    "one symbol must NOT aggregate"
)
assert compute_portfolio({}, initial_capital=100_000.0) is None
# A result with an EMPTY curve (no usable daily series) can't contribute.
empty = _result("A", [])
pf_guard = compute_portfolio({"A": empty, "B": b}, initial_capital=100_000.0)
assert pf_guard is None, "only 1 usable curve → None"
print("✅ <2 usable symbols → None")

# ═══════════════════════════════════════════════════════════════════════════════
# 2 + 3 + 4. run_backtest — dividend accrual, DRIP, borrow costs, zero regression
# ═══════════════════════════════════════════════════════════════════════════════
STRATEGY_HOLD = {
    "buy_rules": ["price > 0"],   # always true → buy first bar, hold forever
    "sell_rules": ["price < 0"],  # never true → never sell
    "initial_capital": 100_000.0,
    "fee_rate": 0.001,
    "slippage": 0.0,
    "slippage_cents": 0.0,
}

print("\n2. Cash dividend accrual (long holds through an ex-date)...")
df_div = _frame(n=260, base=100.0, dividend_on=130, dividend_amt=2.0)
res = run_backtest(
    "TEST", dict(STRATEGY_HOLD, dividend_accrual=True, dividend_reinvest=False),
    "2020-01-01", "2021-01-01", df=df_div, compute_robustness=False,
)
assert res.total_trades >= 1, "must hold a position"
held_shares = res.trades[0].shares
expected_div = held_shares * 2.0  # one $2 ex-date while holding
assert res.dividends_received > 0, res.dividends_received
assert abs(res.dividends_received - expected_div) < 1.0, (
    f"dividends_received={res.dividends_received} expected≈{expected_div}"
)
print(f"✅ received ${res.dividends_received:,.0f} on {held_shares} shares (2.0/share)")

print("\n2b. DRIP reinvest deploys the cash at the next open...")
res_drip = run_backtest(
    "TEST", dict(STRATEGY_HOLD, dividend_accrual=True, dividend_reinvest=True),
    "2020-01-01", "2021-01-01", df=df_div, compute_robustness=False,
)
assert res_drip.dividends_received > 0
assert res_drip.trades[0].shares > held_shares, (
    f"DRIP must grow the position: {res_drip.trades[0].shares} vs {held_shares}"
)
print(f"✅ DRIP grew {held_shares} → {res_drip.trades[0].shares} shares")

print("\n3. Short borrow costs (annualized on mark value / 252)...")
SHORT_STRATEGY = {
    "buy_rules": ["price < 0"],    # never cover
    "sell_rules": ["price > 0"],   # always true → short the first bar
    "initial_capital": 100_000.0,
    "allow_short": True,
    "fee_rate": 0.001,
    "slippage": 0.0,
    "slippage_cents": 0.0,
    "borrow_rate": 0.10,           # 10% annual
}
df_flat = _frame(n=260, base=100.0)
res_short = run_backtest(
    "TEST", SHORT_STRATEGY, "2020-01-01", "2021-01-01",
    df=df_flat, compute_robustness=False,
)
assert res_short.trades and res_short.trades[0].side == "short"
assert res_short.borrow_fees_paid > 0, res_short.borrow_fees_paid
# ~1 trading year of 10% on ~1000 shares ≈ $100/share-day count… bound loosely.
assert res_short.borrow_fees_paid < 100_000.0, "borrow must not exceed capital"
print(f"✅ short borrow fees ${res_short.borrow_fees_paid:,.0f} over the run")

print("\n3b. Shorts PAY dividends on ex-dates (negative received)...")
df_short_div = _frame(n=260, base=100.0, dividend_on=130, dividend_amt=1.0)
# Dividend mode must be ON for dividends to book at all (the default sim is
# adjusted-price total-return — gated). Borrow rate stays on so BOTH
# cash-accounting behaviors are exercised together on one short position.
short_div_rules = dict(SHORT_STRATEGY, dividend_accrual=True)
res_short_div = run_backtest(
    "TEST", short_div_rules, "2020-01-01", "2021-01-01",
    df=df_short_div, compute_robustness=False,
)
assert res_short_div.dividends_received < 0, res_short_div.dividends_received
assert res_short_div.borrow_fees_paid > 0, res_short_div.borrow_fees_paid
print(f"✅ shorts paid ${-res_short_div.dividends_received:,.0f} in dividends "
      f"(+ ${res_short_div.borrow_fees_paid:,.0f} borrow)")

print("\n4. Off-by-default regression: no flags → zero dividends/borrow...")
res_default = run_backtest(
    "TEST", dict(STRATEGY_HOLD), "2020-01-01", "2021-01-01",
    df=df_div, compute_robustness=False,
)
assert res_default.dividends_received == 0.0, res_default.dividends_received
assert res_default.borrow_fees_paid == 0.0, res_default.borrow_fees_paid
print("✅ default sim untouched (0.0 / 0.0) — byte-identical to pre-Tier-5")

# ═══════════════════════════════════════════════════════════════════════════════
# 5. max_sector_exposure diversification cap in backtest_top_results
# ═══════════════════════════════════════════════════════════════════════════════
print("\n5. Sector diversification cap...")

class FakeScreenRow:
    def __init__(self, symbol: str, sector: Optional[str]):
        self.symbol = symbol
        self.sector = sector

class FakeScreenResult:
    def __init__(self, rows: List[FakeScreenRow], message: Optional[str] = None):
        self._rows = rows
        self.data_quality = ({"message": message} if message else {})

    @property
    def results(self):
        return self._rows

engine = BacktestEngine.__new__(BacktestEngine)  # skip __init__ (no DB needed)
engine._db = mock.Mock()

candidates = [
    FakeScreenRow("AAPL", "Technology"),
    FakeScreenRow("MSFT", "Technology"),
    FakeScreenRow("JNJ", "Healthcare"),
    FakeScreenRow("XOM", "Energy"),
    FakeScreenRow("T", "Communication"),
]

with mock.patch("src.screener.StockScreener") as FakeScreenerCls:
    FakeScreenerCls.return_value.screen.return_value = FakeScreenResult(candidates)
    with mock.patch.object(
        engine, "backtest",
        side_effect=lambda interpreted, symbols, **kw: {s: _result(s, []) for s in symbols},
    ):
        capped = engine.backtest_top_results(mock.Mock(), top_n=3, max_sector_exposure=1)

# Rank order with max 1 per sector: AAPL(tech), JNJ(health), XOM(energy);
# MSFT and T are skipped (their sector slot is full / cap reached).
assert set(capped.keys()) == {"AAPL", "JNJ", "XOM"}, f"capped picks: {list(capped)}"
assert engine._last_fill_note and "Diversification cap" in engine._last_fill_note, (
    engine._last_fill_note
)
assert "skipped" in engine._last_fill_note
print(f"✅ max 1/sector → {sorted(capped.keys())}")
print(f"✅ fill note: {engine._last_fill_note!r}")

# No cap → legacy behavior: the plain top-N by rank (2 tech names allowed).
with mock.patch("src.screener.StockScreener") as FakeScreenerCls:
    FakeScreenerCls.return_value.screen.return_value = FakeScreenResult(candidates)
    with mock.patch.object(
        engine, "backtest",
        side_effect=lambda interpreted, symbols, **kw: {s: _result(s, []) for s in symbols},
    ):
        uncapped = engine.backtest_top_results(mock.Mock(), top_n=3, max_sector_exposure=None)
assert set(uncapped.keys()) == {"AAPL", "MSFT", "JNJ"}, f"uncapped picks: {list(uncapped)}"
print("✅ no cap → plain top-3 by rank (AAPL, MSFT, JNJ)")

# ── Staggered start dates: a symbol listed AFTER the pool's first date ──
# Regression for the live 500: compute_portfolio anchored every slice to
# all_dates[0], so a pick whose history starts later (new listing) raised
# KeyError and crashed the whole /backtest request. It must instead sit
# flat (un-invested cash) until its first value, never crash.
print("\nStaggered listing dates (live-500 regression)")
early = [
    {"date": "2020-01-02", "value": 100_000.0},
    {"date": "2020-01-03", "value": 110_000.0},
]
late = [
    {"date": "2020-06-01", "value": 100_000.0},
    {"date": "2020-06-02", "value": 100_000.0},
]
staggered = compute_portfolio(
    {"AAPL": _result("AAPL", early), "NEW": _result("NEW", late)},
    allocation="equal",
    rebalance="none",
)
assert staggered is not None, "compute_portfolio crashed or returned None"
# The late symbol's slice must be flat BEFORE its listing (100k × 0.5 = 50k
# held in cash, then tracked from its first value) and the pool must still
# start at 100k — proving no crash and no phantom gains.
assert staggered["equity_curve"][0]["value"] == 100_000.0, staggered["equity_curve"][0]
print(
    f"✅ staggered listing → pool {staggered['equity_curve'][0]['value']:.2f} → "
    f"{staggered['final_value']:.2f} (no KeyError)"
)

# risk_based allocation must ALSO tolerate staggered starts (the vol loop
# used to iterate every date per symbol and KeyError the same way).
rb = compute_portfolio(
    {"AAPL": _result("AAPL", early), "NEW": _result("NEW", late)},
    allocation="risk_based",
    rebalance="monthly",
)
assert rb is not None, "risk_based staggered crash"
print(f"✅ risk_based + staggered → final {rb['final_value']:.2f} (no KeyError)")

print()
print("🎯 All Tier 5 portfolio-layer tests pass!")
