"""Headless probe: reported per-trade PnL must equal the actual cash P&L.

Systematic-debugging reproduction for a suspected fee-accounting bug in the
backtest simulator:

  - LONG  close: pnl = proceeds - shares*buy_price
                  = shares*exit*(1-fee) - shares*entry       (entry fee omitted)
  - SHORT cover:  pnl = shares*entry - shares*exit           (both fees omitted)

The simulator's cash flow applies the fee on BOTH legs of every round trip
(cost = shares*entry*(1+fee), proceeds = shares*exit*(1-fee), etc.), so the
reported trade PnL should equal the actual cash delta:

  - LONG : shares*exit*(1-fee)  - shares*entry*(1+fee)
  - SHORT: shares*entry*(1-fee) - shares*exit*(1+fee)

When they disagree, win rate and Monte Carlo (which resample trade
pnl_pct) are skewed relative to the true cash results.

Run: python verify_trade_pnl.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

import numpy as np
import pandas as pd

from src.backtester import run_backtest

FAILS: list = []


def check(cond: bool, label: str, detail: str = "") -> None:
    if cond:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ FAIL: {label} {detail}")
        FAILS.append(label)


def make_frame() -> pd.DataFrame:
    """Engineered tape: strong rise (RSI>70 -> short), steep fall (RSI<30
    -> cover), then drift. Produces at least one closed short round trip."""
    idx = pd.bdate_range("2020-01-01", periods=120)
    base = np.concatenate([
        np.linspace(100, 140, 40),   # rise -> overbought, short fires
        np.linspace(140, 70, 50),    # fall -> oversold, cover fires
        np.linspace(70, 75, 30),     # drift
    ])
    return pd.DataFrame({
        "Open": base - 0.5,
        "High": base + 1.5,
        "Low": base - 1.5,
        "Close": base,
        "Volume": np.full(len(base), 2_000_000, dtype=float),
        "Adj Close": base,
        "Dividends": 0.0,
        "Stock Splits": 1.0,
    }, index=idx)


RULES = {
    "initial_capital": 100_000.0,
    "fee_rate": 0.001,
    "slippage": 0.0,
    "slippage_cents": 0.0,
    "max_order_volume_pct": 0.0,
    "stop_loss": None,
    "take_profit": None,
    "trailing_stop": None,
    "position_sizing": "all_in",
    "position_pct": 1.0,
    "risk_per_trade": 0.01,
    "allow_short": True,
    "max_leverage": 1.0,
    "buy_rules": ["rsi < 30"],
    "sell_rules": ["rsi > 70"],
}

df = make_frame()
res = run_backtest(
    "TST", RULES, "2020-01-01", "2020-12-31",
    df=df, compute_robustness=False,
)

print(f"closed trades: {sum(1 for t in res.trades if not t.is_open)}")
for t in res.trades:
    pct = f"{t.pnl_pct * 100:.2f}%" if t.pnl_pct is not None else "n/a"
    sell = round(t.sell_price, 2) if t.sell_price is not None else "open"
    print(
        f"  {t.side:>5} {t.buy_date}->{t.sell_date or 'open'} "
        f"buy={t.buy_price:.2f} sell={sell} "
        f"pnl={t.pnl:.2f} pct={pct}"
    )

# Hard requirement: the probe MUST exercise the short path — the fee bug
# lives there. A silent all-long run would be a weak reproduction.
check(
    any(t.side == "short" and not t.is_open for t in res.trades),
    "probe produced at least one closed SHORT (bug lives in the short path)",
)

fee = RULES["fee_rate"]
checked = 0
for t in res.trades:
    if t.is_open or t.sell_price is None or t.pnl is None:
        continue
    sh = float(t.shares)
    if t.side == "long":
        actual = sh * t.sell_price * (1 - fee) - sh * t.buy_price * (1 + fee)
    else:
        actual = sh * t.buy_price * (1 - fee) - sh * t.sell_price * (1 + fee)
    check(
        abs(t.pnl - actual) < 0.01,
        f"{t.side} trade PnL == actual cash delta",
        f"(reported {t.pnl:.2f} vs cash {actual:.2f})",
    )
    checked += 1

# Sanity: sign of pnl must match sign of pnl_pct.
for t in res.trades:
    if t.is_open or t.pnl is None or t.pnl_pct is None:
        continue
    check(
        (t.pnl > 0) == (t.pnl_pct > 0),
        f"{t.side} PnL sign matches pnl_pct sign",
        f"(pnl={t.pnl:.2f}, pct={t.pnl_pct:.4f})",
    )

if checked == 0:
    print("  ⚠️ no closed trades produced — cannot verify")
    sys.exit(2)

print()
if FAILS:
    print(f"❌ PROBE FAILED — {len(FAILS)} mismatches (bug reproduced)")
    sys.exit(1)
print("✅ TRADE PNL PROBE PASSED — reported PnL matches actual cash P&L")
