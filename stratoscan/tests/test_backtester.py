"""Hermetic backtester tests for Stratoscan — no network, no live DB.

Run: python -m tests.test_backtester

The backtesting engine is free for every authenticated user (the old
premium paywall was removed), yet the simulation math itself had zero
coverage until this suite was added. It locks the engine down:

1. Indicator math: RSI stays in [0,100] and is DEFINED at price peaks
   (regression for the NaN-RSI bug that inverted trades into buy-high/sell-low).
2. The simulation on a mean-reversion-friendly sawtooth must be profitable,
   with every trade closed and all metrics computed.
3. The rule parser: constants, field-vs-field, malformed rules.
4. Graceful failure: out-of-range dates → zero result, no crash.
5. BacktestEngine orchestration: SQLite-backed fetch path + result caching,
   all with a temp DB and mocked yfinance.

The live yfinance download is mocked — nothing ever leaves the box.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd

from src.backtester import (
    MAX_EQUITY_CURVE_POINTS,
    BacktestEngine,
    _apply_rule,
    _calculate_indicators,
    _translate_to_rules,
    bootstrap_confidence,
    compute_benchmark_metrics,
    fetch_historical_data,
    monte_carlo_trades,
    parameter_sensitivity,
    run_backtest,
    store_historical_data,
    translate_strategy,
    walk_forward_analysis,
)
from src.database import SQLiteDB
from src.interpreter import ComparisonOp, FilterCriterion, InterpretedQuery

# ── Helpers ─────────────────────────────────────────────────────────────────

# A multi-year daily series that oscillates FAST (40-day period) with a mild
# upward drift. RSI < 30 happens near local troughs, RSI > 70 near local
# peaks — the perfect sandbox for a mean-reversion "buy oversold, sell
# overbought" strategy.
#
# Why not a slow sine? RSI is a 14-day lagging indicator; on any series whose
# oscillation period is long relative to that window (e.g. a ~188-day sine),
# the crossover fires mid-decline / mid-recovery and the strategy legitimately
# loses — that's real indicator behavior, not an engine bug. The fast series
# below is the regime where the strategy SHOULD profit, so a losing result
# there genuinely means the engine is broken.
_N_DAYS = 6 * 252
_IDX = pd.date_range("2020-01-01", periods=_N_DAYS, freq="B")
_CLOSE = 50 + 30 * np.sin(np.arange(_N_DAYS) / 40 * 2 * np.pi) + np.linspace(0, 20, _N_DAYS)

_DF = pd.DataFrame(
    {
        "Open": _CLOSE,
        "High": _CLOSE * 1.01,
        "Low": _CLOSE * 0.99,
        "Close": _CLOSE,
        "Volume": np.full(_N_DAYS, 1_000_000),
    },
    index=_IDX,
)

_RULES = {
    "initial_capital": 10_000.0,
    "fee_rate": 0.001,
    "slippage": 0.0005,
    "buy_rules": ["rsi < 30"],
    "sell_rules": ["rsi > 70"],
}

# ── 1. Indicator math ───────────────────────────────────────────────────────
print("1. Indicators (RSI/SMA)...")

ind = _calculate_indicators(_DF)
rsi = ind["RSI"].dropna()
assert rsi.between(0, 100).all(), "RSI outside [0,100]"
assert "SMA_50" in ind.columns and "SMA_200" in ind.columns

# Regression: RSI must be DEFINED at price peaks. The old SMA formula turned
# NaN on 14+ day up-streaks, which silently suppressed sell signals and
# inverted the strategy. Peak-day RSI should read strongly overbought.
peak_pos = int(np.argmax(_CLOSE))
peak_rsi = ind["RSI"].iloc[peak_pos]
assert not np.isnan(peak_rsi), f"RSI is NaN at the price peak (old bug): {peak_rsi}"
assert peak_rsi >= 60, f"RSI at a price peak should be overbought, got {peak_rsi:.1f}"
print(f"✅ RSI in [0,100], defined & overbought at price peak (peak RSI={peak_rsi:.1f})")

# ── 2. Simulation must be PROFITABLE on the sawtooth ────────────────────────
print("2. Simulation on mean-reversion-friendly sawtooth...")

res = run_backtest("TEST", _RULES, "2020-01-01", "2025-12-31", df=_DF)
assert res.total_trades >= 3, f"expected multiple round trips, got {res.total_trades}"
assert res.final_value > res.initial_capital, (
    f"buy-oversold/sell-overbought on a sawtooth should profit; "
    f"final={res.final_value:.2f} start={res.initial_capital:.2f} "
    f"(total_return={res.total_return:.2%})"
)
assert res.total_return > 0, f"total return should be positive, got {res.total_return:.2%}"
assert res.max_drawdown is not None and res.max_drawdown < 0, "max drawdown should be negative"
assert res.sharpe_ratio is not None, "sharpe ratio not computed"
assert res.win_rate is not None, "win rate not computed"
assert all(not t.is_open for t in res.trades), "all trades must be closed by the end"
# Aggregate quality: the strategy should win a majority of trades (a
# mean-reversion system on a fast oscillator will have a few individual
# losers — that's normal — but it must win more than it loses and profit
# overall). The original NaN-RSI bug made it LOSE ~99% on every trade;
# both assertions below fail under that regression.
assert res.win_rate >= 0.5, f"strategy should win a majority, got {res.win_rate:.0%}"
assert res.final_value >= res.initial_capital * 1.5, (
    "strategy should at least clear fees/slippage by a wide margin"
)
print(
    f"✅ {res.total_trades} round trips, return {res.total_return:.1%}, "
    f"win rate {res.win_rate:.0%}, maxDD {res.max_drawdown:.1%}"
)

# ── 3. Rule parser ──────────────────────────────────────────────────────────
print("3. Rule parser...")

row = ind.iloc[300]
assert _apply_rule("price > sma_50", row) in (True, False)
assert _apply_rule("rsi < 30", row) in (True, False)
assert _apply_rule("volume > 1000", row) is True
assert _apply_rule("rsi < 30 extra", row) is False  # malformed (4 tokens)
assert _apply_rule("bogus < 5", row) is False  # unknown field
print("✅ constants, field-vs-field, and malformed rules handled")

# ── 4. Graceful failure ─────────────────────────────────────────────────────
print("4. Graceful failure on out-of-range dates / missing columns...")

empty = run_backtest("TEST", _RULES, "2099-01-01", "2099-12-31", df=_DF)
assert empty.final_value == 0 and empty.total_return == -1.0, (
    "out-of-range window should return a zero result, not crash"
)
print("✅ out-of-range window → zero-result (no crash)")

# A caller-supplied df missing the Close column previously raised an
# unhandled KeyError; it must degrade to the same zero-result.
broken = _DF.iloc[:10].drop(columns=["Close"])
no_close = run_backtest("TEST", _RULES, "2020-01-01", "2020-03-31", df=broken)
assert no_close.final_value == 0 and no_close.total_return == -1.0, (
    "missing-Close df should return a zero result, not KeyError"
)
print("✅ missing-Close df → zero-result (no KeyError crash)")

# ── 4b. Strategy translation (rule generation) ───────────────────────────────
print("4b. Strategy translation (_translate_to_rules)...")


def _rules_for(raw, filters=None, momentum=None):
    return _translate_to_rules(
        InterpretedQuery(raw_input=raw, filters=filters or [], momentum=momentum)
    )

# Oversold (max_rsi lte) → mean-reversion buy below the threshold
r = _rules_for("oversold stocks", [FilterCriterion("rsi_14", ComparisonOp.LTE, 35)])
assert "rsi < 35" in r["buy_rules"], f"oversold should buy under 35: {r}"
assert "rsi > 70" in r["sell_rules"]  # default sell preserved

# Overbought (min_rsi gte 70) → sell above the threshold
r = _rules_for("overbought stocks", [FilterCriterion("rsi_14", ComparisonOp.GTE, 70)])
assert "rsi > 70" in r["sell_rules"], f"overbought should sell above 70: {r}"

# REGRESSION: "rising momentum" sets min_rsi=50 (a SCREENING filter). It must
# NOT become a sell rule — the old code produced "sell when RSI > 50", which
# closes a momentum entry almost immediately. Momentum is its own buy rule.
r = _rules_for(
    "rising momentum stocks",
    [FilterCriterion("rsi_14", ComparisonOp.GTE, 50)],
    momentum="bullish",
)
assert "price > sma_50" in r["buy_rules"], f"bullish momentum should buy above SMA50: {r}"
assert not any(rule.startswith("rsi > 50") for rule in r["sell_rules"]), (
    f"min_rsi=50 must not become a sell rule: {r}"
)
# Day 9 (approved signal change): momentum = price above the 50-day INSIDE
# an uptrend, held until the trend breaks. Entry and exit are mutually
# exclusive, so the rules can never open and close on consecutive bars —
# the old "rsi > 70" exit did exactly that in a strong trend.
assert r["buy_rules"] == ["price > sma_50", "sma_50 > sma_200"], r["buy_rules"]
assert r["sell_rules"] == ["price < sma_200"], r["sell_rules"]
# ...but a dip-buying entry with momentum keeps the overbought exit.
r = _rules_for(
    "oversold stocks with rising momentum",
    [FilterCriterion("rsi_14", ComparisonOp.LTE, 35)],
    momentum="bullish",
)
assert r["sell_rules"] == ["rsi > 70"], r

# Undervalued (pe lte): the backtest frame has NO per-bar P/E column, so a
# `pe < 15` rule would silently compare PRICE to 15 (_apply_rule maps
# pe→Close) — "buy undervalued" would fire on price, not P/E (verified by
# the rule-execution loop). Honest contract: P/E is a SCREENING filter
# applied when the candidate set is selected; it never becomes a tradeable
# price-proxy rule. Default technical rules take over.
r = _rules_for("undervalued stocks", [FilterCriterion("pe_ratio", ComparisonOp.LTE, 15)])
assert not any(x.split()[0] == "pe" for x in r["buy_rules"] + r["sell_rules"]), (
    f"pe must not leak into tradeable rules (engine has no per-bar P/E): {r}"
)
assert r["buy_rules"] == ["rsi < 30"] and r["sell_rules"] == ["rsi > 70"], (
    f"pe-only query should fall back to default rules: {r}"
)

# Bare query → classic defaults
r = _rules_for("stocks")
assert r["buy_rules"] == ["rsi < 30"] and r["sell_rules"] == ["rsi > 70"]
print("✅ oversold/overbought/momentum/pe-not-tradeable/default rule mapping correct")

# ── 4c. Dirty-data handling (yfinance NaN cells) ─────────────────────────────
print("4c. Dirty-data handling (NaN OHLCV cells)...")

# yfinance occasionally returns NaN cells. store_historical_data must not
# crash on int(NaN) volume, must drop NaN-Close rows, and must forward-fill
# other price NaNs; run_backtest must also survive a SQLite-rebuilt frame
# that contains a NULL close.
dirty = _DF.copy()
dirty.iloc[100, dirty.columns.get_loc("Close")] = np.nan
dirty.iloc[500, dirty.columns.get_loc("Volume")] = np.nan
dirty.iloc[900, dirty.columns.get_loc("High")] = np.nan

with tempfile.TemporaryDirectory() as tmp:
    db = SQLiteDB(db_path=os.path.join(tmp, "backtest_dirty.db"))
    db.init_schema()
    stored = store_historical_data(db, "DIRTY", dirty)
    rows = db.get_historical_prices("DIRTY", "2020-01-01", "2025-12-31")
    # The NaN-Close day (row 100) must have been dropped from storage.
    stored_dates = [dict(r)["date"] for r in rows]
    dropped_date = str(_DF.index[100])[:10]
    assert dropped_date not in stored_dates, "NaN-Close day should be dropped from storage"
    # No NULL prices may survive the round-trip.
    for r in rows:
        d = dict(r)
        assert d["close"] is not None and d["open"] is not None, (
            f"NULL price survived round-trip on {d['date']}"
        )
    db.close()
print(f"✅ NaN volume/close handled: {len(rows)} clean rows stored, no crash")

# Full engine chain with dirty data: store (cleans) → engine.backtest
# (rebuilds from SQLite) → run_backtest (defensive dropna). The whole
# path must complete with sensible results — no crash, no NULL prices.
with tempfile.TemporaryDirectory() as tmp:
    db = SQLiteDB(db_path=os.path.join(tmp, "backtest_dirty_engine.db"))
    db.init_schema()
    store_historical_data(db, "DIRTYE", dirty)
    # The engine now fetches a SPY benchmark too — pre-store it in the
    # temp DB so the benchmark also comes from cache (no network).
    store_historical_data(db, "SPY", dirty)
    iq = InterpretedQuery(
        raw_input="oversold stocks",
        filters=[],
        sector=None,
        industry=None,
        momentum=None,
    )
    engine = BacktestEngine(db=db)
    # NOTE: name must not shadow the section-2 ``res`` BacktestResult — a
    # `with` block has no scope in Python, so this dict would leak out and
    # break later sections that read ``res.trades``.
    engine_results = engine.backtest(iq, ["DIRTYE"], "2020-01-01", "2025-12-31")
    assert "DIRTYE" in engine_results, (
        f"dirty-data engine path lost the symbol: {list(engine_results)}"
    )
    r = engine_results["DIRTYE"]
    assert r.total_trades >= 3 and r.total_return > 0, (
        f"dirty-data engine path should still trade profitably: "
        f"trades={r.total_trades} ret={r.total_return:.2%}"
    )
    db.close()
print("✅ full engine chain survives dirty data (store → SQLite rebuild → sim)")

# Direct raw-fetch path: engine.backtest passes the RAW fetched df to
# run_backtest (it stores a cleaned copy but sims the uncleaned one), so a
# mid-series NaN Close must be tolerated by run_backtest's own dropna.
mid_nan = _DF.copy()
mid_nan.iloc[300, mid_nan.columns.get_loc("Close")] = np.nan
raw = run_backtest("RAWNAN", _RULES, "2020-01-01", "2025-12-31", df=mid_nan)
assert raw.total_trades >= 3 and raw.total_return > 0, (
    f"raw df with mid-series NaN Close should still trade profitably: "
    f"trades={raw.total_trades} ret={raw.total_return:.2%}"
)
print("✅ raw-fetch path (mid-series NaN Close → run_backtest) survives")

# run_backtest on a flat, never-traded series: max_drawdown must be None
# (the old formula reported 0.0, misleadingly reading as "zero risk").
flat_df = pd.DataFrame(
    {
        "Open": np.full(100, 50.0),
        "High": np.full(100, 50.0),
        "Low": np.full(100, 50.0),
        "Close": np.full(100, 50.0),
        "Volume": np.full(100, 1_000_000),
    },
    index=pd.date_range("2024-01-01", periods=100, freq="B"),
)
flat = run_backtest("FLAT", _RULES, "2024-01-01", "2024-05-31", df=flat_df)
assert flat.total_trades == 0
assert flat.max_drawdown is None, f"no-trade max_drawdown should be None, got {flat.max_drawdown}"
assert flat.sharpe_ratio is None and flat.win_rate is None
print("✅ no-trade flat series → max_drawdown None (not 0.0)")

# ── 5. BacktestEngine orchestration (temp SQLite, mocked yfinance) ──────────
print("5. BacktestEngine with temp SQLite DB...")

with tempfile.TemporaryDirectory() as tmp:
    db = SQLiteDB(db_path=os.path.join(tmp, "backtest_test.db"))
    db.init_schema()

    # Pre-store the sawtooth so the engine's SQLite cache path is exercised.
    store_historical_data(db, "TEST", _DF)
    # ...and the SPY benchmark, so the engine's benchmark fetch also comes
    # from cache (fetch_historical_data must never be called in this test).
    store_historical_data(db, "SPY", _DF)
    assert len(db.get_historical_prices("TEST", "2020-01-01", "2025-12-31")) > 0, (
        "historical prices not stored"
    )

    engine = BacktestEngine(db=db)
    iq = InterpretedQuery(
        raw_input="oversold stocks",
        filters=[],
        sector=None,
        industry=None,
        momentum=None,
    )

    # fetch_historical_data must NOT be called — data already in SQLite.
    with mock.patch("src.backtester.fetch_historical_data") as mock_fetch:
        results = engine.backtest(iq, ["TEST"], "2020-01-01", "2025-12-31")
        mock_fetch.assert_not_called()

    assert "TEST" in results, f"missing TEST result: {list(results)}"
    r = results["TEST"]
    assert r.total_trades >= 3 and r.total_return > 0
    # Tier 1: benchmark comparison computed from the cached SPY series, and
    # the equity curve populated.
    assert r.benchmark_total_return is not None, "benchmark return not computed"
    assert r.beta is not None and r.alpha is not None, "alpha/beta not computed"
    assert r.equity_curve, "equity curve missing"
    # Result should also be persisted to the backtest_results cache table.
    assert len(db.connect().execute(
        "SELECT * FROM backtest_results WHERE symbol='TEST'"
    ).fetchall()) >= 1, "backtest result not cached in SQLite"

    db.close()
print("✅ engine uses SQLite cache path, returns results, persists them")

# ── 6. Next-bar-open execution (Tier 1 credibility) ──────────────────────
print("6. Next-bar-open execution (no look-ahead)...")

# A frame where "price < 100" is true ONLY at index 3's close (97). With
# next-bar-open execution the buy must fill at index 4's OPEN (99), one day
# later — never at the signal bar's own close (which would be look-ahead).
_next_df = pd.DataFrame(
    {
        "Open": [101.0, 102.0, 103.0, 98.0, 99.0, 100.0],
        "High": [102.0, 103.0, 104.0, 99.0, 100.0, 101.0],
        "Low": [100.0, 101.0, 102.0, 96.0, 98.0, 99.0],
        "Close": [101.0, 102.0, 103.0, 97.0, 99.0, 100.0],
        "Volume": np.full(6, 1_000_000),
    },
    index=pd.date_range("2024-01-01", periods=6, freq="B"),
)
next_rules = {
    "initial_capital": 10_000.0,
    "fee_rate": 0.001,
    "slippage": 0.0005,
    "buy_rules": ["price < 100"],
    "sell_rules": ["price > 105"],
}
nb = run_backtest("NEXT", next_rules, "2024-01-01", "2024-01-10", df=_next_df)
assert nb.total_trades == 1, f"expected 1 trade, got {nb.total_trades}"
t = nb.trades[0]
# Signal close (97) is at 2024-01-04; fill must be the NEXT open (2024-01-05).
assert t.buy_date == "2024-01-05", f"buy must fill at next bar open, got {t.buy_date}"
assert abs(t.buy_price - 99.0 * 1.0005) < 1e-9, (
    f"buy must fill at next open+slippage (99*1.0005), got {t.buy_price}"
)
# Forced liquidation at the final close (no later open exists).
assert t.sell_date == "2024-01-08", f"forced close at final bar, got {t.sell_date}"
# Low trade count (1 < 30) must produce a statistical-confidence warning.
assert any("confidence" in w for w in nb.warnings), f"expected warning: {nb.warnings}"
print(
    f"✅ signal close 97 → fill at next open 99 on {t.buy_date}, "
    f"forced close {t.sell_date}, low-trade warning present"
)

# ── 7. Benchmark comparison math (Tier 1 credibility) ────────────────────
print("7. Benchmark comparison (alpha/beta/excess)...")

# Strategy with daily returns EXACTLY 2x the benchmark's → beta == 2,
# alpha == 0, excess == strat_total_return - benchmark_total_return.
_bench = np.array([100.0, 101.0, 103.0, 104.0])
_bench_rets = _bench[1:] / _bench[:-1] - 1.0
_strat = np.concatenate(([100.0], 100.0 * np.cumprod(1 + 2 * _bench_rets)))
_strat_idx = pd.date_range("2024-01-01", periods=4, freq="B")
strat_series = pd.Series(_strat, index=_strat_idx)
bench_series = pd.Series(_bench, index=_strat_idx)
strat_total = float(_strat[-1] / _strat[0] - 1.0)

m = compute_benchmark_metrics(strat_series, bench_series, strat_total)
assert m["benchmark_total_return"] is not None
assert abs(m["benchmark_total_return"] - 0.04) < 1e-9, m
assert abs(m["excess_return"] - (strat_total - 0.04)) < 1e-9, m
assert m["beta"] is not None and abs(m["beta"] - 2.0) < 1e-9, f"beta={m['beta']}"
assert m["alpha"] is not None and abs(m["alpha"]) < 1e-9, f"alpha={m['alpha']}"

# Degenerate: no benchmark series → every metric is None (never crashes).
empty_m = compute_benchmark_metrics(strat_series, None, strat_total)
assert empty_m["benchmark_total_return"] is None
assert empty_m["beta"] is None and empty_m["alpha"] is None

# Too-short benchmark (fewer than 2 points) → None metrics.
tiny_m = compute_benchmark_metrics(strat_series, pd.Series([100.0]), 0.1)
assert tiny_m["benchmark_total_return"] is None
print(
    f"✅ beta={m['beta']:.2f}, alpha={m['alpha']:.6f}, "
    f"excess={m['excess_return']:.4f}; degenerate inputs → None, no crash"
)

# ── 8. Equity curve, warnings, auto_adjust pin, rule echo (Tier 1) ───────
print("8. Equity curve / auto_adjust pin / rule echo...")

# Equity curve: downsampled, ALWAYS includes the final point (== final_value).
curve_res = run_backtest("CURVE", _RULES, "2020-01-01", "2025-12-31", df=_DF)
assert curve_res.equity_curve, "equity curve must be populated"
assert len(curve_res.equity_curve) <= MAX_EQUITY_CURVE_POINTS + 1, (
    f"equity curve should be downsampled to ≤{MAX_EQUITY_CURVE_POINTS}+1, "
    f"got {len(curve_res.equity_curve)}"
)
assert curve_res.equity_curve[0]["value"] == round(curve_res.initial_capital, 2)
assert curve_res.equity_curve[-1]["value"] == round(curve_res.final_value, 2), (
    "last equity point must equal final_value"
)
assert curve_res.equity_curve[-1]["date"] >= curve_res.equity_curve[0]["date"]

# auto_adjust must be pinned to False in fetch_historical_data so the raw
# Close AND Adj Close coexist in one download (reproducible data, Tier 1/2).
with mock.patch("src.backtester.yf.Ticker") as mock_ticker:
    raw_mock = _DF.head(5).copy()
    raw_mock["Adj Close"] = raw_mock["Close"] * 0.9
    mock_ticker.return_value.history.return_value = raw_mock
    fetched = fetch_historical_data("PINTEST")
    assert fetched is not None
    kwargs = mock_ticker.return_value.history.call_args.kwargs
    assert kwargs.get("auto_adjust") is False, f"auto_adjust not pinned: {kwargs}"
    assert "Adj Close" in fetched.columns, "raw fetch must carry Adj Close"

# Rule echo: translate_strategy must match the engine's rules exactly.
_echo_iq = InterpretedQuery(
    raw_input="oversold stocks",
    filters=[FilterCriterion("rsi_14", ComparisonOp.LTE, 35)],
)
assert translate_strategy(_echo_iq) == _translate_to_rules(_echo_iq)
assert translate_strategy(_echo_iq)["buy_rules"] == ["rsi < 35"]
print("✅ equity curve (downsampled, final point == final_value), auto_adjust pinned, rule echo")

# ── 9. Stop-loss / take-profit / trailing stops (Tier 2) ─────────────────
print("9. Protective stops (stop-loss / take-profit / trailing)...")

# Take-profit: entry ~101, +10% target ≈ 111.2 → hit on the day High hits 114.
# 5 bars so the take-profit lands on the FINAL bar — a frame that kept
# trading after the exit would legitimately re-enter (close < 200) and
# force-liquidate at the end, muddying the assertion.
_tp_df = pd.DataFrame(
    {
        "Open": [100, 101, 102, 108, 112],
        "High": [101, 102, 104, 110, 114],
        "Low": [99, 100, 101, 106, 110],
        "Close": [100, 101, 103, 109, 111],
        "Volume": np.full(5, 1_000_000),
    },
    index=pd.date_range("2024-01-01", periods=5, freq="B"),
)
tp_rules = {
    "initial_capital": 10_000.0,
    "fee_rate": 0.001,
    "slippage": 0.0005,
    "buy_rules": ["price < 200"],
    "sell_rules": ["price > 300"],
    "take_profit": 0.10,
}
tp = run_backtest("TP", tp_rules, "2024-01-01", "2024-01-31", df=_tp_df)
assert tp.total_trades == 1, f"expected 1 trade, got {tp.total_trades}"
assert tp.trades[0].exit_reason == "take_profit", tp.trades[0].exit_reason
assert tp.trades[0].sell_date == "2024-01-05", tp.trades[0].sell_date

# Stop-loss: entry ~101, -5% stop ≈ 96 → hit on the day Low reaches 93.
_sl_df = pd.DataFrame(
    {
        "Open": [100, 101, 102, 98, 95],
        "High": [101, 102, 103, 99, 96],
        "Low": [99, 100, 101, 97, 93],
        "Close": [100, 101, 102, 97, 94],
        "Volume": np.full(5, 1_000_000),
    },
    index=pd.date_range("2024-02-01", periods=5, freq="B"),
)
sl_rules = {
    "initial_capital": 10_000.0,
    "fee_rate": 0.001,
    "slippage": 0.0005,
    "buy_rules": ["price < 200"],
    "sell_rules": ["price > 300"],
    "stop_loss": 0.05,
}
sl = run_backtest("SL", sl_rules, "2024-02-01", "2024-02-29", df=_sl_df)
assert sl.total_trades == 1
assert sl.trades[0].exit_reason == "stop_loss", sl.trades[0].exit_reason

# Trailing stop: peak 109 then a pullback below the 5% trail ratchets the exit.
_tr_df = pd.DataFrame(
    {
        "Open": [100, 102, 104, 106, 108, 106, 90],
        "High": [101, 103, 105, 107, 109, 107, 91],
        "Low": [99, 101, 103, 105, 107, 105, 89],
        "Close": [100, 102, 104, 106, 108, 105, 90],
        "Volume": np.full(7, 1_000_000),
    },
    index=pd.date_range("2024-03-01", periods=7, freq="B"),
)
tr_rules = {
    "initial_capital": 10_000.0,
    "fee_rate": 0.001,
    "slippage": 0.0005,
    "buy_rules": ["price < 200"],
    "sell_rules": ["price > 300"],
    "trailing_stop": 0.05,
}
tr = run_backtest("TR", tr_rules, "2024-03-01", "2024-03-31", df=_tr_df)
assert tr.total_trades == 1
assert tr.trades[0].exit_reason == "trailing_stop", tr.trades[0].exit_reason
print("✅ take-profit, stop-loss, and trailing stops trigger intra-bar with correct reason")

# ── 10. Position sizing (fixed_pct / risk_based) (Tier 2) ────────────────
print("10. Position sizing (fixed_pct / risk_based)...")

all_res = run_backtest("ALL", _RULES, "2020-01-01", "2025-12-31", df=_DF)
pct_rules = {**_RULES, "position_sizing": "fixed_pct", "position_pct": 0.2}
pct_res = run_backtest("PCT", pct_rules, "2020-01-01", "2025-12-31", df=_DF)
assert pct_res.total_trades == all_res.total_trades, "sizing must not change signal bars"
pct_ratio = pct_res.trades[0].shares / all_res.trades[0].shares
assert 0.1 < pct_ratio < 0.4, f"fixed_pct=20% should size ~1/5 of all-in, got {pct_ratio:.2f}"

rb_rules = {
    **_RULES,
    "position_sizing": "risk_based",
    "risk_per_trade": 0.01,
    "stop_loss": 0.10,
}
rb_res = run_backtest("RB", rb_rules, "2020-01-01", "2025-12-31", df=_DF)
rb_ratio = rb_res.trades[0].shares / all_res.trades[0].shares
# risk 1% ÷ stop 10% → 10% of equity per position.
assert 0.05 < rb_ratio < 0.3, f"risk-based sizing ratio wrong: {rb_ratio:.2f}"

# risk_based without a stop-loss is undefined → all-in fallback + audit note.
rb_nostop = run_backtest(
    "RBNS", {**_RULES, "position_sizing": "risk_based"},
    "2020-01-01", "2025-12-31", df=_DF,
)
assert any("risk_based" in q for q in rb_nostop.data_quality), rb_nostop.data_quality
assert abs(rb_nostop.trades[0].shares - all_res.trades[0].shares) <= 1, (
    "risk_based without a stop should fall back to all-in sizing"
)
print(f"✅ fixed_pct≈{pct_ratio:.2f}x, risk_based≈{rb_ratio:.2f}x of all-in; fallback audited")

# ── 11. Short selling + leverage cap (Tier 2) ───────────────────────────
print("11. Short selling + leverage cap...")

short_rules = {
    "initial_capital": 10_000.0,
    "fee_rate": 0.001,
    "slippage": 0.0005,
    "buy_rules": ["rsi < 30"],
    "sell_rules": ["rsi > 70"],
    "allow_short": True,
    "max_leverage": 1.0,
}
short_res = run_backtest("SHORT", short_rules, "2020-01-01", "2025-12-31", df=_DF)
short_trades = [t for t in short_res.trades if t.side == "short"]
assert short_trades, "allow_short should produce short trades"
assert short_res.total_trades >= 3, f"shorts+longs should trade often: {short_res.total_trades}"
assert all(not t.is_open for t in short_res.trades), "all trades must be closed"
assert all(t.pnl is not None for t in short_res.trades)
assert any(t.pnl > 0 for t in short_trades), "shorts at peaks should profit on the sawtooth"
# Leverage cap: with max_leverage=1, a short's exposure ≤ equity.
first_short = short_trades[0]
assert first_short.buy_price * first_short.shares <= 10_000 * 1.01, (
    f"short exposure must respect the leverage cap: "
    f"{first_short.buy_price * first_short.shares:.0f}"
)
print(f"✅ {len(short_trades)} shorts, profitable, leverage-capped")

# ── 12. Trading calendar (weekends skipped) (Tier 2) ────────────────────
print("12. Trading calendar (weekend rows skipped)...")

_cal_idx = pd.DatetimeIndex(["2024-01-05", "2024-01-06", "2024-01-07", "2024-01-08"])
_cal_df = pd.DataFrame(
    {
        "Open": [101, 101, 100, 100],
        "High": [102, 102, 101, 101],
        "Low": [100, 100, 99, 99],
        "Close": [99, 101, 100, 100],
        "Volume": np.full(4, 1_000_000),
    },
    index=_cal_idx,
)
cal_rules = {
    "initial_capital": 10_000.0,
    "fee_rate": 0.001,
    "slippage": 0.0005,
    "buy_rules": ["price < 100"],
    "sell_rules": ["price > 500"],
}
cal = run_backtest("CAL", cal_rules, "2024-01-01", "2024-01-31", df=_cal_df)
assert cal.total_trades == 1
# Signal close 99 is Friday 01-05; the next OPEN must be Monday 01-08 —
# the Saturday/Sunday rows must not be simulated as tradable.
assert cal.trades[0].buy_date == "2024-01-08", cal.trades[0].buy_date
print("✅ Saturday/Sunday bars skipped — fill landed on Monday")

# ── 13. Slippage model (cents + liquidity cap) (Tier 2) ─────────────────
print("13. Slippage model (cents/share + liquidity cap)...")

cents_rules = {**_RULES, "slippage": 0.0, "slippage_cents": 1.0}
cents = run_backtest("SLIPC", cents_rules, "2020-01-01", "2025-12-31", df=_DF)
assert cents.trades[0].buy_price > all_res.trades[0].buy_price, (
    "fixed cents per share should raise the fill cost"
)
# Liquidity cap: 0.01% of 1M volume = 100 shares max per order.
cap_rules = {**_RULES, "max_order_volume_pct": 0.0001}
cap = run_backtest("SLIPC", cap_rules, "2020-01-01", "2025-12-31", df=_DF)
assert cap.trades[0].shares == 100, f"cap should limit to 100 shares, got {cap.trades[0].shares}"
print("✅ cents-per-share slippage and %-of-volume order cap enforced")

# ── 14. Buy-and-hold breakdown + data quality (Tier 2) ──────────────────
print("14. Price-only vs total return (dividend contribution)...")

_hold = pd.DataFrame(
    {
        "Open": [100, 100, 100, 100],
        "High": [100, 100, 100, 100],
        "Low": [100, 100, 100, 100],
        "Close": [100, 100, 100, 100],       # raw close flat → price return 0%
        "Adj Close": [100, 105, 105, 105],   # adjusted rises 5% → total return 5%
        "Volume": np.full(4, 1_000_000),
    },
    index=pd.date_range("2024-01-01", periods=4, freq="B"),
)
hold = run_backtest("HOLD", _RULES, "2024-01-01", "2024-01-31", df=_hold)
assert hold.buy_hold_price_return is not None
assert abs(hold.buy_hold_price_return) < 1e-9, hold.buy_hold_price_return
assert abs(hold.buy_hold_total_return - 0.05) < 1e-9, hold.buy_hold_total_return
assert abs(hold.dividend_contribution - 0.05) < 1e-9, hold.dividend_contribution
assert any("adjusted" in q for q in hold.data_quality), hold.data_quality
# Minimal frame (no raw data) → breakdown not reported, and the audit trail
# says so explicitly.
min_res = run_backtest("MIN", _RULES, "2020-01-01", "2025-12-31", df=_DF)
assert min_res.buy_hold_price_return is None
assert any("Raw price" in q for q in min_res.data_quality), min_res.data_quality
print("✅ price return 0% vs total return 5% → dividend contribution 5%; audit trail")

# ── 15. NL risk-parameter extraction (Tier 2) ───────────────────────────
print("15. NL risk-parameter extraction...")

r = _rules_for("buy oversold stocks with a stop loss of 8% and take profit 20%")
assert r["stop_loss"] == 0.08, r["stop_loss"]
assert r["take_profit"] == 0.20, r["take_profit"]
r = _rules_for("short overvalued stocks with 2x leverage")
assert r["allow_short"] is True and r["max_leverage"] == 2.0, r
r = _rules_for("risk 1% per trade with a stop loss 10%")
assert r["position_sizing"] == "risk_based", r
assert abs(r["risk_per_trade"] - 0.01) < 1e-9, r["risk_per_trade"]
assert abs(r["stop_loss"] - 0.10) < 1e-9, r["stop_loss"]
r = _rules_for("oversold stocks")
assert r["stop_loss"] is None and r["take_profit"] is None
assert r["allow_short"] is False and r["position_sizing"] == "all_in"
print("✅ stop/target/trail/short/leverage/risk parsed from plain English")

# ── 16. Monte Carlo simulation (Tier 3) ─────────────────────────────────
print("16. Monte Carlo outcome distribution...")

mc = monte_carlo_trades(res.trades)
assert mc["available"] is True
assert mc["simulations"] == 1000 and mc["trade_count"] == res.total_trades
assert mc["p5"] <= mc["median"] <= mc["p95"], mc
assert 0.0 <= mc["prob_positive"] <= 1.0
# Fixed seed → identical results across runs (reproducible product output).
mc2 = monte_carlo_trades(res.trades)
assert mc["median"] == mc2["median"] and mc["p5"] == mc2["p5"]
# No closed trades → graceful unavailable, no crash.
assert monte_carlo_trades([])["available"] is False
# The sawtooth edge is real: even the 5th percentile resample is positive.
assert mc["p5"] > 0, f"sawtooth strategy should survive resampling: {mc}"
# The result object carries the analysis (computed inside run_backtest).
assert res.monte_carlo is not None and res.monte_carlo["available"] is True
print(
    f"✅ p5={mc['p5']:.1%}, median={mc['median']:.1%}, p95={mc['p95']:.1%}, "
    f"prob_positive={mc['prob_positive']:.0%}"
)

# ── 17. Bootstrap confidence intervals (Tier 3) ─────────────────────────
print("17. Bootstrap CIs (Sharpe / max drawdown / t-stat)...")

# A steady 100→200 rise: Sharpe clearly positive, drawdown ~0.
rising_vals = list(np.linspace(100.0, 200.0, 252))
ci = bootstrap_confidence(rising_vals, sharpe_ratio=2.0)
assert ci["available"] is True
assert len(ci["sharpe_ci"]) == 2 and ci["sharpe_ci"][0] <= ci["sharpe_ci"][1]
assert len(ci["max_drawdown_ci"]) == 2
assert ci["sharpe_t_stat"] is not None and ci["sharpe_t_stat"] > 0
assert "observations" in ci["sharpe_sample_note"]
# Flat/no-variation series → unavailable (no fake confidence).
assert bootstrap_confidence([100.0] * 50, 1.0)["available"] is False
assert bootstrap_confidence([100.0] * 10, 1.0)["available"] is False
# The sawtooth result carries the analysis (computed inside run_backtest).
assert res.confidence_intervals is not None and res.confidence_intervals["available"] is True
print(
    f"✅ Sharpe CI [{ci['sharpe_ci'][0]:.2f}, {ci['sharpe_ci'][1]:.2f}], "
    f"t-stat {ci['sharpe_t_stat']:.2f}"
)

# ── 18. Walk-forward analysis (Tier 3) ──────────────────────────────────
print("18. Walk-forward (IS/OOS + rolling windows)...")

wf = walk_forward_analysis(
    _DF, _RULES, symbol="WF", start_date="2020-01-01", end_date="2025-12-31"
)
assert wf["available"] is True
assert "in_sample" in wf and "out_of_sample" in wf
assert wf["in_sample"]["total_trades"] >= 1
assert wf["out_of_sample"]["total_trades"] >= 1
assert len(wf["rolling"]["windows"]) == 4
assert wf["rolling"]["median_return"] is not None
# The mean-reversion edge must hold OUT of sample, not just in-sample.
assert wf["out_of_sample"]["total_return"] > 0, wf["out_of_sample"]
assert wf["rolling"]["positive_windows"] >= 1

# Warm-up correctness: bars before signal_start_date feed indicators but
# must NEVER trade. Prices crash to 30 (buy rule fires) long before the
# signal start; the first fill must still land at/after 2024-02-05.
_warm_idx = pd.date_range("2024-01-01", periods=40, freq="B")
_warm_df = pd.DataFrame(
    {
        "Open": np.full(40, 100.0),
        "High": np.full(40, 100.0),
        "Low": np.full(40, 100.0),
        "Close": np.concatenate([np.full(15, 100.0), np.full(25, 30.0)]),
        "Volume": np.full(40, 1_000_000),
    },
    index=_warm_idx,
)
warm_rules = {
    "initial_capital": 10_000.0,
    "fee_rate": 0.001,
    "slippage": 0.0005,
    "buy_rules": ["price < 40"],
    "sell_rules": ["price > 200"],
}
warm_res = run_backtest(
    "WARM", warm_rules, "2024-01-01", "2024-02-29",
    df=_warm_df, signal_start_date="2024-02-05",
)
assert warm_res.total_trades >= 1, "warm-up run should still trade"
assert all(t.buy_date >= "2024-02-05" for t in warm_res.trades), [
    t.buy_date for t in warm_res.trades
]
print(
    f"✅ OOS return {wf['out_of_sample']['total_return']:.1%}, "
    f"rolling {wf['rolling']['positive_windows']}/{wf['rolling']['total_windows']} positive; "
    "warm-up bars never trade"
)

# ── 19. Parameter sensitivity heatmap (Tier 3) ──────────────────────────
print("19. Parameter sensitivity grid (RSI × SMA)...")

sens = parameter_sensitivity(
    _DF, _RULES, symbol="SENS", start_date="2020-01-01", end_date="2025-12-31"
)
assert sens["available"] is True
assert len(sens["grid"]) == 4 * 3  # RSI 20/25/30/35 × SMA 30/50/70
for row in sens["grid"]:
    assert row["rsi"] in (20.0, 25.0, 30.0, 35.0)
    assert row["sma"] in (30, 50, 70)
    assert isinstance(row["total_return"], float)
assert sens["total_combos"] == 12
assert sens["best"]["total_return"] == max(r["total_return"] for r in sens["grid"])
assert sens["worst"]["total_return"] == min(r["total_return"] for r in sens["grid"])
assert isinstance(sens["overfit_signal"], bool)
assert sens["losing_combos"] <= sens["total_combos"]
# Too-little data → graceful unavailable.
assert parameter_sensitivity(_DF.head(20), _RULES)["available"] is False
print(
    f"✅ {len(sens['grid'])} combos, best rsi={sens['best']['rsi']} "
    f"sma={sens['best']['sma']} ({sens['best']['total_return']:.0%}), "
    f"overfit_signal={sens['overfit_signal']}"
)

# ── 20. Engine resilience: a Tier 3 crash must not drop the symbol ────────
print("20. Robustness failure keeps the headline result...")

# Regression: robustness analyses used to share the per-symbol try/except,
# so a crash in walk_forward or sensitivity LOST the whole symbol result.
_eng_df = pd.DataFrame(
    {
        "Open": 50 + 10 * np.sin(np.arange(300) / 20),
        "High": 50 + 10 * np.sin(np.arange(300) / 20) + 1,
        "Low": 50 + 10 * np.sin(np.arange(300) / 20) - 1,
        "Close": 50 + 10 * np.sin(np.arange(300) / 20),
        "Volume": np.full(300, 1_000_000),
    },
    index=pd.date_range("2021-01-01", periods=300, freq="B"),
)
with tempfile.TemporaryDirectory() as tmp:
    db = SQLiteDB(db_path=os.path.join(tmp, "robust_resilience.db"))
    db.init_schema()
    store_historical_data(db, "RESIL", _eng_df)
    store_historical_data(db, "SPY", _eng_df)
    engine = BacktestEngine(db=db)
    iq = InterpretedQuery(raw_input="oversold stocks", filters=[])
    with mock.patch(
        "src.backtester.walk_forward_analysis", side_effect=RuntimeError("wf boom")
    ), mock.patch(
        "src.backtester.parameter_sensitivity", side_effect=RuntimeError("sens boom")
    ):
        results = engine.backtest(iq, ["RESIL"], "2021-01-01", "2025-12-31")
    assert "RESIL" in results, f"symbol must survive a robustness crash: {list(results)}"
    r = results["RESIL"]
    assert r.total_return is not None and r.total_trades >= 0
    assert r.walk_forward is None and r.parameter_sensitivity is None
    db.close()
print("✅ robustness crash → headline result preserved, analyses None")

# ── 21. inf price rows dropped like NaN (defense-in-depth) ────────────────
print("21. inf price rows...")

# Regression: an inf price used to survive dropna (which only removes NaN),
# poisoning the equity curve with cash + shares*inf = inf — un-serializable
# in the API (Starlette JSONResponse uses allow_nan=False → 500).
_inf_df = _DF.copy()
_inf_df.iloc[300, _inf_df.columns.get_loc("Close")] = np.inf
_inf_df.iloc[600, _inf_df.columns.get_loc("High")] = np.inf
inf_res = run_backtest("INFP", _RULES, "2020-01-01", "2025-12-31", df=_inf_df)
assert np.isfinite(inf_res.total_return), f"inf leaked into total_return: {inf_res.total_return}"
assert inf_res.final_value > 0
assert all(np.isfinite(p["value"]) for p in inf_res.equity_curve), (
    "inf leaked into the equity curve"
)

# The store path must never persist inf either.
with tempfile.TemporaryDirectory() as tmp:
    db = SQLiteDB(db_path=os.path.join(tmp, "inf_store.db"))
    db.init_schema()
    store_historical_data(db, "INFST", _inf_df)
    rows = db.get_historical_prices("INFST", "2020-01-01", "2025-12-31")
    assert rows, "inf-store probe stored nothing"
    for row in rows:
        d = dict(row)
        assert d["close"] is not None and abs(d["close"]) != float("inf"), d
    db.close()
print("✅ inf rows dropped from sim and storage; all outputs finite")

# ── 22. Benchmark inf rows are cleaned (defense-in-depth) ────────────────
print("22. Benchmark inf rows...")

# Regression: _get_benchmark_series used to return the RAW benchmark series
# (only store_historical_data cleaned inf), so an inf benchmark close would
# leak inf into alpha/beta/excess and 500 the API response. Exercise the
# LIVE path (no SPY cached) with a mocked fetch bearing an inf row.
raw_bench = _DF.copy()
raw_bench["Adj Close"] = raw_bench["Close"] * 0.9
raw_bench.iloc[100, raw_bench.columns.get_loc("Close")] = np.inf
raw_bench.iloc[100, raw_bench.columns.get_loc("Adj Close")] = np.inf
with tempfile.TemporaryDirectory() as tmp:
    db = SQLiteDB(db_path=os.path.join(tmp, "inf_bench.db"))
    db.init_schema()
    store_historical_data(db, "TESTB", _DF)
    engine = BacktestEngine(db=db)
    iq = InterpretedQuery(raw_input="oversold stocks", filters=[])
    with mock.patch(
        "src.backtester.fetch_historical_data",
        side_effect=lambda symbol: raw_bench if symbol == "SPY" else None,
    ):
        results = engine.backtest(iq, ["TESTB"], "2020-01-01", "2025-12-31")
    assert "TESTB" in results
    r = results["TESTB"]
    assert r.benchmark_total_return is None or np.isfinite(r.benchmark_total_return)
    assert r.beta is None or np.isfinite(r.beta)
    assert r.alpha is None or np.isfinite(r.alpha)
    assert r.excess_return is None or np.isfinite(r.excess_return)
    db.close()
print("✅ benchmark inf row cleaned → alpha/beta/excess finite or None")

# ── 23. Fresh SQLite file self-heals (no manual `init` needed) ──────────────
print("23. Fresh SQLite self-heal...")

# Regression found by the end-to-end lifecycle probe: init_schema() was only
# called by the manual `init` CLI. On a fresh server or a wiped data/ dir,
# /backtest returned HTTP 200 with ZERO results — every symbol dropped with
# "no such table: historical_prices", a silent failure that looks like "no
# matches". BacktestEngine.__init__ now self-heals the schema.
with tempfile.TemporaryDirectory() as tmp:
    fresh = os.path.join(tmp, "brand_new.db")
    assert not os.path.exists(fresh), "test precondition: DB file must not exist"
    engine = BacktestEngine(db=SQLiteDB(db_path=fresh))
    # The engine must have created the tables on construction.
    conn = sqlite3.connect(fresh)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert {"historical_prices", "backtest_results"} <= tables, tables
    # And the schema is idempotent — constructing a second engine on the
    # same (now-existing) file must not raise.
    second = BacktestEngine(db=SQLiteDB(db_path=fresh))
    engine._db.close()
    second._db.close()
print("✅ fresh SQLite file self-initializes; re-construction is idempotent")

# ── 4c. REGRESSION: momentum rule appended exactly ONCE ───────────────
# The old code appended the momentum rule INSIDE the per-criterion loop, so
# a query with N filters produced N copies of "price > sma_50" (verified
# live: "buy oversold tech stocks with rising momentum" → buy=['price >
# sma_50', 'rsi < 35', 'price > sma_50']).
print("4c. Momentum rule emitted exactly once...")

r = _rules_for(
    "rising momentum stocks",
    [
        FilterCriterion("sector", ComparisonOp.EQ, "Technology"),
        FilterCriterion("rsi_14", ComparisonOp.GTE, 50),
    ],
    momentum="bullish",
)
assert r["buy_rules"].count("price > sma_50") == 1, (
    f"momentum rule must appear exactly once, got {r['buy_rules']}"
)
r2 = _rules_for(
    "bearish stocks",
    [
        FilterCriterion("sector", ComparisonOp.EQ, "Energy"),
        FilterCriterion("pe_ratio", ComparisonOp.GTE, 20),
    ],
    momentum="bearish",
)
assert r2["sell_rules"].count("price < sma_50") == 1, (
    f"bearish momentum rule must appear exactly once, got {r2['sell_rules']}"
)
print("✅ momentum/bearish rules appear exactly once regardless of filter count")

# ── 4d. parse_backtest_query: tickers, buy-and-hold, date windows ──────────
# The /backtest interpreter is the SCREENER interpreter (sector/PE/RSI...).
# Strategy-style directives (explicit tickers, buy-and-hold, "last N
# months") fall through it silently and the engine defaulted to an RSI
# mean-reversion strategy over 10 years on the top-5 by cap (verified
# live). parse_backtest_query recovers them deterministically (pure regex,
# no LLM).
print("4d. parse_backtest_query (tickers / buy-and-hold / date windows)...")

from src.backtester import parse_backtest_query

# Explicit tickers are extracted; English stopwords are NOT treated as symbols.
p = parse_backtest_query("buy and hold AAPL for the last 6 months")
assert p["symbols"] == ["AAPL"], p
assert p["buy_and_hold"] is True, p
assert p["start_date"] is not None and p["end_date"] is not None, p

p = parse_backtest_query("buy and hold NVDA and MSFT")
assert p["symbols"] == ["NVDA", "MSFT"], p
assert p["buy_and_hold"] is True, p

p = parse_backtest_query("backtest SPY over the last year")
assert p["symbols"] == ["SPY"], p
assert p["buy_and_hold"] is False, p

p = parse_backtest_query("buy oversold tech stocks with rising momentum")
assert p["symbols"] == [], p  # all-lowercase prose → no tickers
assert p["buy_and_hold"] is False, p
assert p["start_date"] is None, p

# Class-share tickers (BRK.A) survive the tokenizer.
p = parse_backtest_query("buy and hold BRK.A and BRK.B")
assert p["symbols"] == ["BRK.A", "BRK.B"], p

# Date windows: last 6 months → today minus 180 days (6 × 30).
p = parse_backtest_query("buy and hold AAPL for the last 6 months")
from datetime import datetime, timedelta
six_ago = (datetime.now() - timedelta(days=6 * 30)).strftime("%Y-%m-%d")
assert p["start_date"] == six_ago, p
assert p["end_date"] == datetime.now().strftime("%Y-%m-%d"), p

# Weeks and years parse too.
p = parse_backtest_query("backtest SPY over the past 2 years")
assert p["start_date"] == (datetime.now() - timedelta(days=2 * 365)).strftime("%Y-%m-%d"), p
p = parse_backtest_query("buy and hold NVDA for the last 8 weeks")
assert p["start_date"] == (datetime.now() - timedelta(days=8 * 7)).strftime("%Y-%m-%d"), p

# A query with no explicit directives yields empty extraction (normal path).
p = parse_backtest_query("strong uptrend in technology")
assert p == {"symbols": [], "buy_and_hold": False, "start_date": None, "end_date": None}, p

# Technical-indicator acronyms (RSI/SMA/MACD/...) are NOT tickers. The
# frontend folds its momentum tune clause into every query ("momentum:
# RSI buy below 30, RSI sell above 70, SMA cross 20/50"), so without
# stopwords the engine backtested phantom "RSI"/"SMA" — a full yfinance
# fetch each (1579/339 days of junk) that pushed requests past the
# client's 120s timeout (verified live in the UI round-trip).
p = parse_backtest_query(
    "AAPL RSI strategy — momentum: RSI buy below 30, RSI sell above 70, "
    "SMA cross 20/50"
)
assert p["symbols"] == ["AAPL"], p
p = parse_backtest_query("buy MACD cross with ATR filter on NVDA")
assert p["symbols"] == ["NVDA"], p
print("✅ indicator acronyms (RSI/SMA/MACD/ATR) not treated as tickers")
print("✅ explicit tickers, buy-and-hold, and date windows extracted deterministically")

# ── 24. Explicit start_date honored (10y-anchor truncation regression) ──
print("24. Explicit start_date honored in fetch + engine cache...")

# Regression (verified live on the backtest round-trip probe): requesting
# start_date=2016-01-01 (today in Aug 2026) produced an equity curve
# beginning 2016-08-03 — fetch_historical_data hardcoded the fetch start
# at today−10y, silently truncating any earlier requested window, and the
# engine trusted the truncated DB cache as complete while the API reported
# start_date=2016-01-01. The persisted row lied about the simulated window.

# 24a. fetch_historical_data must honor an explicit start_date earlier than
# the default 10y anchor (fetch from the earlier of the two).
with mock.patch("src.backtester.yf.Ticker") as mock_ticker:
    inst = mock_ticker.return_value
    inst.history.return_value = _DF
    df = fetch_historical_data("PINTEST", start_date="2016-01-01")
    assert df is not None
    called = inst.history.call_args
    fetched_start = pd.Timestamp(called.kwargs.get("start"))
    assert fetched_start <= pd.Timestamp("2016-01-01"), (
        f"fetch must start at/earlier than the requested window, got {fetched_start}"
    )

# 24b. No start_date → default 10y anchor unchanged.
with mock.patch("src.backtester.yf.Ticker") as mock_ticker:
    inst = mock_ticker.return_value
    inst.history.return_value = _DF
    fetch_historical_data("PINTEST")
    fetched_start = pd.Timestamp(inst.history.call_args.kwargs.get("start"))
    anchor = pd.Timestamp(datetime.now() - timedelta(days=10 * 365))
    assert abs((fetched_start - anchor).days) <= 2, (
        f"default fetch start should be the 10y anchor, got {fetched_start}"
    )

# 24c. The engine must NOT trust a cache whose first bar is far later than
# the requested start (truncated by the anchor fetch) — it must refetch so
# the simulated window matches the requested window.
_full_df = pd.DataFrame(
    {
        "Open": 50 + 10 * np.sin(np.arange(2500) / 20),
        "High": 50 + 10 * np.sin(np.arange(2500) / 20) + 1,
        "Low": 50 + 10 * np.sin(np.arange(2500) / 20) - 1,
        "Close": 50 + 10 * np.sin(np.arange(2500) / 20),
        "Volume": np.full(2500, 1_000_000),
    },
    index=pd.date_range("2016-01-04", periods=2500, freq="B"),
)
with tempfile.TemporaryDirectory() as tmp:
    db = SQLiteDB(db_path=os.path.join(tmp, "cov_trunc.db"))
    db.init_schema()
    # Cache starts 2020-01-01 (an anchor-era population) — truncated for a
    # 2016-01-01 request.
    store_historical_data(db, "COVTEST", _DF)
    store_historical_data(db, "SPY", _DF)
    engine = BacktestEngine(db=db)
    iq = InterpretedQuery(raw_input="oversold stocks", filters=[])
    with mock.patch(
        "src.backtester.fetch_historical_data", return_value=_full_df
    ) as mock_fetch:
        results = engine.backtest(iq, ["COVTEST"], "2016-01-01", "2026-01-01")
        assert mock_fetch.called, (
            "truncated cache must trigger a refetch (was trusted as complete)"
        )
    assert "COVTEST" in results
    r = results["COVTEST"]
    assert r.equity_curve and r.equity_curve[0]["date"] <= "2016-01-15", (
        f"equity curve must start at the requested window, got "
        f"{r.equity_curve[0]['date'] if r.equity_curve else 'EMPTY'}"
    )
    db.close()

# 24d. A cache that genuinely covers the window is still trusted (no
# refetch) — the coverage check must not break the normal cache hit.
with tempfile.TemporaryDirectory() as tmp:
    db = SQLiteDB(db_path=os.path.join(tmp, "cov_ok.db"))
    db.init_schema()
    store_historical_data(db, "COVTEST2", _DF)  # starts 2020-01-01
    store_historical_data(db, "SPY", _DF)
    engine = BacktestEngine(db=db)
    iq = InterpretedQuery(raw_input="oversold stocks", filters=[])
    with mock.patch("src.backtester.fetch_historical_data") as mock_fetch:
        results = engine.backtest(iq, ["COVTEST2"], "2020-01-01", "2025-12-31")
        mock_fetch.assert_not_called()
    assert "COVTEST2" in results
    db.close()
print("✅ explicit start_date honored; truncated cache refetched; covered cache trusted")

print()
print("🎯 All backtester tests pass!")
