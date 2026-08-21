"""Edge-path accuracy verification for the Stratoscan backtester.

Run: python verify_backtest_edge_paths.py

The accuracy probe (verify_backtest_accuracy.py) verified long-only all-in
round trips. THIS probe systematically drives the engine through every path
that probe never touched, watching for the strongest root-cause signal a
simulation can show: violation of cash conservation. Every scenario asserts

    final_value == initial_capital + sum(trade PnL)         (invariant 1)
    trade.pnl == that trade's actual cash-flow delta        (invariant 2)

independently recomputed from the documented formulas, plus hand-computed
ground-truth literals. Any violation = a real engine bug, not a symptom.

Scenarios:
  A. SHORT round trip (sell-rules entry, buy-rules cover) — shares negative,
     fee-inclusive both legs, pnl == proceeds - cover_cost.
  B. risk_based sizing with a stop-loss: budget = equity*risk/stop.
  C. fixed_pct sizing (50%).
  D. liquidity cap binds: shares == floor(volume * max_order_volume_pct).
  E. slippage_cents: buy fill = price*(1+slip)+cents, sell = price*(1-slip)-cents.
  F. take_profit: fill = max(open, entry*(1+tp)), exit_reason='take_profit'.
  G. trailing_stop: trail rides the high, fill = min(open, trail*(1-trail%)).
  H. end_of_period: open long force-liquidated at final close.
  I. no trades: warning fires, final == initial, max_drawdown is None.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

import math  # noqa: E402

import pandas as pd  # noqa: E402

from src.backtester import run_backtest  # noqa: E402

print("=" * 72)
print("BACKTEST EDGE PATHS — shorts / sizing / caps / stops / liquidation")
print("=" * 72)

CAP = 100_000.0
FEE = 0.001
SLIP = 0.0005


def buy_fill(price: float, cents: float = 0.0) -> float:
    return price * (1 + SLIP) + cents


def sell_fill(price: float, cents: float = 0.0) -> float:
    return price * (1 - SLIP) - cents


def bdf(ohlc: list) -> pd.DataFrame:
    return pd.DataFrame(
        ohlc,
        columns=["Open", "High", "Low", "Close", "Volume"],
        index=pd.bdate_range("2000-03-01", periods=len(ohlc)),
    )


def base_rules(**over) -> dict:
    rules = {
        "initial_capital": CAP,
        "fee_rate": FEE,
        "slippage": SLIP,
        "slippage_cents": 0.0,
        "max_order_volume_pct": 0.0,
        "buy_rules": ["price > 90"],
        "sell_rules": ["price < 80"],
        "position_sizing": "all_in",
        "position_pct": 1.0,
        "risk_per_trade": 0.01,
        "allow_short": False,
        "max_leverage": 1.0,
    }
    rules.update(over)
    return rules


def assert_invariants(res, label: str, expect_trades: int) -> None:
    """Both invariants + trade count, recomputed independently."""
    assert len(res.trades) == expect_trades, (label, len(res.trades), expect_trades)
    # Invariant 2 per trade: pnl == cash delta for that trade.
    for t in res.trades:
        assert t.pnl is not None and math.isfinite(t.pnl), (label, t)
        # Long: pnl = proceeds - cost. Short: pnl = entry_proceeds - cover_cost.
        if t.side == "long":
            delta = t.shares * t.sell_price * (1 - FEE) - t.shares * t.buy_price * (1 + FEE)
        else:
            delta = (t.shares * t.buy_price * (1 - FEE)
                     - t.shares * t.sell_price * (1 + FEE))
        assert math.isclose(t.pnl, delta, rel_tol=1e-9), (label, t, t.pnl, delta)
    # Invariant 1: final == initial + sum(pnl).
    sum_pnl = sum(t.pnl for t in res.trades)
    assert math.isclose(res.final_value, CAP + sum_pnl, abs_tol=1e-6), (
        label, res.final_value, CAP + sum_pnl,
    )


# ── A. SHORT round trip ────────────────────────────────────────────────────────
print("\nA. Short selling round trip (allow_short)...")
# sell_rules fire while flat (price < 101), buy_rules cover (price < 92).
short_ohlc = [
    (100.0, 101.0, 99.0, 100.0, 1_000_000.0),  # 1 short signal (close 100 < 101)
    (99.0, 100.0, 96.0, 96.0, 1_000_000.0),    # 2 short fill @ 98.9505
    (96.0, 97.0, 94.0, 94.0, 1_000_000.0),     # 3
    (94.0, 95.0, 92.0, 91.0, 1_000_000.0),     # 4 cover signal (close 91 < 92)
    (90.0, 92.0, 89.0, 92.0, 1_000_000.0),     # 5 cover fill @ 90.045
]
res = run_backtest(
    "SHORT", base_rules(allow_short=True, buy_rules=["price < 92"], sell_rules=["price < 101"]),
    start_date="2000-03-01", end_date="2000-12-31", df=bdf(short_ohlc),
    benchmark_series=None,
)
assert_invariants(res, "short", 1)
t = res.trades[0]
f_entry = sell_fill(99.0)                      # 98.9505
sh = int(CAP / f_entry)                        # engine: int(budget/fill), budget=CAP
assert t.side == "short" and t.shares == sh, (t.side, t.shares, sh)
assert math.isclose(t.buy_price, f_entry, rel_tol=1e-9), (t.buy_price, f_entry)
proceeds = sh * f_entry * (1 - FEE)
f_cover = buy_fill(90.0)                       # 90.045
cover_cost = sh * f_cover * (1 + FEE)
pnl_ref = proceeds - cover_cost
assert math.isclose(t.pnl, pnl_ref, rel_tol=1e-9), (t.pnl, pnl_ref)
assert math.isclose(t.sell_price, f_cover, rel_tol=1e-9), (t.sell_price, f_cover)
assert t.exit_reason == "signal", t.exit_reason
# Hardcoded literals: 1010 shares, +$8,803.67, final $108,803.67.
assert t.shares == 1010, t.shares
assert math.isclose(t.pnl, 8803.669545, abs_tol=1e-3), t.pnl
assert math.isclose(res.final_value, 108803.669545, abs_tol=1e-3), res.final_value
print(f"   short {t.shares} sh @ {t.buy_price:.4f} -> cover {t.sell_price:.4f}, "
      f"pnl={t.pnl:+,.2f} final=${res.final_value:,.2f}  ✅")

# ── B. risk_based sizing with stop ─────────────────────────────────────────────
print("\nB. risk_based sizing (risk 1% / 10% stop -> 10% of equity)...")
rb_ohlc = [
    (100.0, 101.0, 99.0, 100.0, 1_000_000.0),  # 1 buy signal
    (99.0, 100.0, 98.0, 95.0, 1_000_000.0),    # 2 buy fill @ 99.0495
    (95.0, 96.0, 93.0, 90.0, 1_000_000.0),     # 3
    (90.0, 91.0, 84.0, 85.0, 1_000_000.0),     # 4 low 84 <= stop -> stop fill
]
res = run_backtest(
    "RISK", base_rules(position_sizing="risk_based", risk_per_trade=0.01, stop_loss=0.10),
    start_date="2000-03-01", end_date="2000-12-31", df=bdf(rb_ohlc),
)
assert_invariants(res, "risk", 1)
t = res.trades[0]
f_entry = buy_fill(99.0)
budget = CAP * 0.01 / 0.10                     # 10,000
sh = int(budget / (f_entry * (1 + FEE)))
assert t.shares == sh == 100, (t.shares, sh)
stop_level = f_entry * (1 - 0.10)
assert t.exit_reason == "stop_loss", t.exit_reason
assert math.isclose(t.sell_price, min(90.0, stop_level), rel_tol=1e-9), (
    t.sell_price, min(90.0, stop_level),
)
assert math.isclose(t.pnl, t.shares * t.sell_price * (1 - FEE) - t.shares * f_entry * (1 + FEE), rel_tol=1e-9)
assert math.isclose(t.pnl, -1009.314405, abs_tol=1e-3), t.pnl
print(f"   {t.shares} sh (10% budget), stop fill {t.sell_price:.4f}, pnl={t.pnl:+,.2f}  ✅")

# ── C. fixed_pct sizing (50%) ──────────────────────────────────────────────────
print("\nC. fixed_pct sizing (50% of equity)...")
fp_ohlc = [
    (100.0, 101.0, 99.0, 100.0, 1_000_000.0),  # 1 buy signal
    (99.0, 100.0, 98.0, 100.0, 1_000_000.0),   # 2 buy fill @ 99.0495
    (100.0, 101.0, 99.0, 101.0, 1_000_000.0),  # 3
    (101.0, 102.0, 100.0, 105.0, 1_000_000.0), # 4
]
res = run_backtest(
    "FIXEDPCT", base_rules(position_sizing="fixed_pct", position_pct=0.5),
    start_date="2000-03-01", end_date="2000-12-31", df=bdf(fp_ohlc),
)
assert_invariants(res, "fixed_pct", 1)
t = res.trades[0]
f_entry = buy_fill(99.0)
sh = int((CAP * 0.5) / (f_entry * (1 + FEE)))
assert t.shares == sh == 504, (t.shares, sh)
print(f"   {t.shares} sh (50% budget = ${CAP*0.5:,.0f})  ✅")

# ── D. liquidity cap binds ─────────────────────────────────────────────────────
print("\nD. liquidity cap (0.001% of 1M vol -> 10 shares max)...")
liq_ohlc = [
    (100.0, 101.0, 99.0, 100.0, 1_000_000.0),  # 1 buy signal
    (99.0, 100.0, 98.0, 100.0, 1_000_000.0),   # 2 buy fill; cap = 10 sh
    (100.0, 101.0, 99.0, 101.0, 1_000_000.0),  # 3
]
res = run_backtest(
    "LIQCAP", base_rules(max_order_volume_pct=0.00001),
    start_date="2000-03-01", end_date="2000-12-31", df=bdf(liq_ohlc),
)
assert_invariants(res, "liq_cap", 1)
t = res.trades[0]
cap = int(1_000_000.0 * 0.00001)
assert t.shares == cap == 10, (t.shares, cap)
print(f"   {t.shares} sh (cap {cap} binds; un-capped would be 1008)  ✅")

# ── E. slippage_cents ──────────────────────────────────────────────────────────
print("\nE. slippage_cents (2c per share)...")
sc_ohlc = [
    (100.0, 101.0, 99.0, 100.0, 1_000_000.0),  # 1 buy signal (close 100 > 90)
    (99.0, 100.0, 98.0, 90.0, 1_000_000.0),    # 2 buy fill @ 99*1.0005+0.02
    (90.0, 91.0, 84.0, 79.0, 1_000_000.0),     # 3 close 79 < 81 -> SELL signal
    (80.0, 82.0, 79.0, 81.0, 1_000_000.0),     # 4 sell fill @ 80*0.9995-0.02
]
res = run_backtest(
    "SLIPC", base_rules(slippage_cents=0.02, sell_rules=["price < 81"]),
    start_date="2000-03-01", end_date="2000-12-31", df=bdf(sc_ohlc),
)
assert_invariants(res, "slippage_cents", 1)
t = res.trades[0]
assert math.isclose(t.buy_price, buy_fill(99.0, cents=0.02), rel_tol=1e-9), t.buy_price
assert math.isclose(t.sell_price, sell_fill(80.0, cents=0.02), rel_tol=1e-9), t.sell_price
print(f"   buy fill {t.buy_price:.4f} (99*1.0005+0.02={buy_fill(99.0, 0.02):.4f}) "
      f"sell fill {t.sell_price:.4f}  ✅")

# ── F. take_profit ─────────────────────────────────────────────────────────────
print("\nF. take_profit (+10%, fill at max(open, tp))...")
tp_ohlc = [
    (100.0, 101.0, 99.0, 100.0, 1_000_000.0),  # 1 buy signal
    (99.0, 100.0, 98.0, 99.0, 1_000_000.0),    # 2 buy fill @ 99.0495
    (105.0, 110.0, 104.0, 108.0, 1_000_000.0), # 3 high 110 >= tp -> fill
]
res = run_backtest(
    "TP", base_rules(take_profit=0.10),
    start_date="2000-03-01", end_date="2000-12-31", df=bdf(tp_ohlc),
)
assert_invariants(res, "take_profit", 1)
t = res.trades[0]
f_entry = buy_fill(99.0)
tp_level = f_entry * (1 + 0.10)
assert t.exit_reason == "take_profit", t.exit_reason
assert math.isclose(t.sell_price, max(105.0, tp_level), rel_tol=1e-9), (
    t.sell_price, max(105.0, tp_level),
)
print(f"   entry {t.buy_price:.4f} tp {tp_level:.4f} fill {t.sell_price:.4f}  ✅")

# ── G. trailing_stop ───────────────────────────────────────────────────────────
print("\nG. trailing_stop (5%, trail rides the high)...")
tr_ohlc = [
    (100.0, 101.0, 99.0, 100.0, 1_000_000.0),  # 1 buy signal
    (99.0, 100.0, 98.0, 99.0, 1_000_000.0),    # 2 buy fill @ 99.0495
    (99.0, 110.0, 105.5, 108.0, 1_000_000.0),  # 3 trail -> 110; low 105.5 > 104.5 (no same-bar trigger)
    (105.0, 106.0, 104.0, 107.0, 1_000_000.0), # 4 low 104 <= 104.5 -> fill 104.5
]
res = run_backtest(
    "TRAIL", base_rules(trailing_stop=0.05),
    start_date="2000-03-01", end_date="2000-12-31", df=bdf(tr_ohlc),
)
assert_invariants(res, "trailing", 1)
t = res.trades[0]
trail_level = 110.0 * (1 - 0.05)               # 104.5
assert t.exit_reason == "trailing_stop", t.exit_reason
assert math.isclose(t.sell_price, min(105.0, trail_level), rel_tol=1e-9), (
    t.sell_price, min(105.0, trail_level),
)
print(f"   trail 110 -> level {trail_level:.2f} fill {t.sell_price:.2f}  ✅")

# ── H. end_of_period liquidation ───────────────────────────────────────────────
print("\nH. end_of_period liquidation (open long force-closed at final close)...")
eop_ohlc = [
    (100.0, 101.0, 99.0, 100.0, 1_000_000.0),  # 1 buy signal
    (99.0, 100.0, 98.0, 100.0, 1_000_000.0),   # 2 buy fill @ 99.0495
    (100.0, 101.0, 99.0, 101.0, 1_000_000.0),  # 3
    (101.0, 102.0, 100.0, 102.0, 1_000_000.0), # 4
    (102.0, 103.0, 101.0, 103.0, 1_000_000.0), # 5 final close 103
]
res = run_backtest(
    "EOP", base_rules(sell_rules=["price < 1"]),
    start_date="2000-03-01", end_date="2000-12-31", df=bdf(eop_ohlc),
)
assert_invariants(res, "end_of_period", 1)
t = res.trades[0]
f_entry = buy_fill(99.0)
sh = int(CAP / (f_entry * (1 + FEE)))
assert t.shares == sh == 1008, (t.shares, sh)
assert t.exit_reason == "end_of_period", t.exit_reason
assert math.isclose(t.sell_price, sell_fill(103.0), rel_tol=1e-9), t.sell_price
assert math.isclose(t.pnl, t.shares * t.sell_price * (1 - FEE) - t.shares * f_entry * (1 + FEE), rel_tol=1e-9)
print(f"   {t.shares} sh, forced exit @ {t.sell_price:.4f}, pnl={t.pnl:+,.2f}, "
      f"final=${res.final_value:,.2f}  ✅")

# ── I. no trades ───────────────────────────────────────────────────────────────
print("\nI. no trades (rules never fire)...")
nt_ohlc = [
    (50.0, 51.0, 49.0, 50.0, 1_000_000.0),
    (50.0, 51.0, 49.0, 50.0, 1_000_000.0),
    (50.0, 51.0, 49.0, 50.0, 1_000_000.0),
]
res = run_backtest(
    "NOTRADES", base_rules(buy_rules=["price > 200"], sell_rules=["price < 10"]),
    start_date="2000-03-01", end_date="2000-12-31", df=bdf(nt_ohlc),
)
assert len(res.trades) == 0, res.trades
assert res.total_trades == 0, res.total_trades
assert math.isclose(res.final_value, CAP, abs_tol=1e-9), res.final_value
assert res.max_drawdown is None, res.max_drawdown
assert res.win_rate is None, res.win_rate
assert any("No trades were triggered" in w for w in res.warnings), res.warnings
assert res.monte_carlo is not None and not res.monte_carlo["available"], res.monte_carlo
assert res.monte_carlo["reason"] == "no closed trades", res.monte_carlo
print(f"   final=${res.final_value:,.0f} == initial, dd=None, warning fired, MC declined  ✅")

print()
print("🎯 BACKTEST EDGE-PATH PROBE PASSED — cash conservation + PnL invariants hold on every path.")
