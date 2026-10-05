"""Day 14 — the corporate-action worked example on the verification page.

A stock trades flat at $100. Halfway through, it splits 2-for-1: from that day
on there are twice as many shares, each at $50. Nobody lost a cent.

A naive backtester that reads raw closing prices sees $100 → $50 and reports a
50% crash. Stratoscan downloads raw AND adjusted closes in one yfinance call
(``auto_adjust=False``) and trades the adjusted series, in which every price
before the split is halved — so the same buy-and-hold is flat, minus the real
cost of trading it.

The numbers asserted here are the ones the verification page quotes
(``frontend/src/pages/Verification.tsx``); ``tests/test_public_claims.py``
checks that the page still says them.

Run: python -m tests.test_corporate_actions
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("JWT_SECRET", "corp-actions-dummy-secret")

import pandas as pd  # noqa: E402

from src.backtester import _normalize_ohlcv, run_backtest  # noqa: E402

DAYS = 40
SPLIT_AT = 20
idx = pd.bdate_range("2024-01-02", periods=DAYS)
raw = [100.0 if i < SPLIT_AT else 50.0 for i in range(DAYS)]

# What yfinance returns with auto_adjust=False: raw OHLC, the back-adjusted
# close, and the split recorded on its day.
yf_frame = pd.DataFrame(
    {
        "Open": raw, "High": raw, "Low": raw, "Close": raw,
        "Adj Close": [50.0] * DAYS,
        "Volume": [1_000_000] * DAYS,
        "Dividends": [0.0] * DAYS,
        "Stock Splits": [2.0 if i == SPLIT_AT else 0.0 for i in range(DAYS)],
    },
    index=idx,
)
# A naive backtester's view: the raw prices and nothing else.
naive_frame = yf_frame[["Open", "High", "Low", "Close", "Volume"]].copy()

# Exactly what POST /backtest runs for "buy and hold" (api.py): buy at once,
# never sell; the position is liquidated at the final close.
RULES = {"buy_rules": ["price > 0"], "sell_rules": ["price < 0"], "position_sizing": "all_in"}
start, end = idx[0].strftime("%Y-%m-%d"), idx[-1].strftime("%Y-%m-%d")

naive = run_backtest("SPLIT", dict(RULES), start, end, df=naive_frame, compute_robustness=False)
ours = run_backtest("SPLIT", dict(RULES), start, end, df=yf_frame, compute_robustness=False)

# 1. The adjusted series is flat: pre-split prices are halved, the raw close is kept.
norm = _normalize_ohlcv(yf_frame)
assert (norm["Close"] == 50.0).all() and (norm["Open"] == 50.0).all()
assert norm["raw_close"].iloc[0] == 100.0 and norm["raw_close"].iloc[-1] == 50.0
print("✅ adjusted series: every pre-split price halved; the raw close is kept for reference")

# 2. The naive reading: a 50% crash that never happened.
naive_pct, ours_pct = naive.total_return * 100, ours.total_return * 100
assert -50.5 < naive_pct < -49.8, naive_pct
print(f"✅ naive (raw prices): {naive_pct:.2f}% — a crash that never happened")

# 3. Stratoscan: flat, minus the real costs (0.1% fee + 0.05% slippage, both legs).
assert -0.35 < ours_pct < 0.0, ours_pct
assert ours.total_trades == naive.total_trades == 1
print(f"✅ Stratoscan (adjusted): {ours_pct:.2f}% — flat, minus fees and slippage")

# The figures the verification page shows (rounded to one decimal).
NAIVE_PCT = round(naive_pct, 1)
OURS_PCT = round(ours_pct, 1)
NAIVE_FINAL, OURS_FINAL = round(naive.final_value), round(ours.final_value)
assert (NAIVE_PCT, OURS_PCT, NAIVE_FINAL, OURS_FINAL) == (-50.1, -0.3, 49_875, 99_700), \
    (NAIVE_PCT, OURS_PCT, NAIVE_FINAL, OURS_FINAL)
print(f"   page figures: naive {NAIVE_PCT}% (${NAIVE_FINAL:,}), Stratoscan {OURS_PCT}% (${OURS_FINAL:,})")

print("\nALL CORPORATE-ACTION CHECKS PASSED")
