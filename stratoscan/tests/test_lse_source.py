"""Hermetic tests for the LSE history source — no network, no key.

The load-bearing claim is that ``build_adjusted_frame`` rebuilds a
split/dividend-adjusted series from LSE's RAW candles plus its separate
corporate-action datasets, on the same basis yfinance's ``Adj Close`` uses.
If that math is wrong, every backtest built on LSE data is silently and
plausibly wrong — so it is pinned here against a HAND-COMPUTED example
rather than against the implementation's own output.

Worked example (5 daily bars):

    bar   close   event on this bar
    0     100.00  —
    1     110.00  dividend $1.00 (ex-date)
    2     120.00  —
    3      60.00  2-for-1 split
    4      62.00  —

Each event scales every bar STRICTLY BEFORE it:
    ef[1] = 1 - 1.00/100.00 = 0.99      (prior close is the basis)
    ef[3] = 1/2             = 0.50
    ef[0] = ef[2] = ef[4]   = 1.00

adj_factor[i] = product of ef[j] for every j > i, so the newest bar is 1.0:
    adj_factor = [0.99*0.5, 0.5, 0.5, 1.0, 1.0] = [0.495, 0.5, 0.5, 1.0, 1.0]

    Adj Close  = [49.50, 55.00, 60.00, 60.00, 62.00]

Note bar 2 (raw 120) adjusts to 60.00 and bar 3 is 60.00 — the split leaves
the series continuous, which is the whole point.

Run: python -m tests.test_lse_source
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ.setdefault("JWT_SECRET", "lse-source-dummy-secret")

import numpy as np  # noqa: E402

from src.lse_source import build_adjusted_frame, is_configured  # noqa: E402
from src.backtester import _normalize_ohlcv  # noqa: E402

TOL = 1e-9


def _candles():
    closes = [100.0, 110.0, 120.0, 60.0, 62.0]
    return [
        {
            "timestamp": f"2026-01-0{i + 1}",
            "open": c, "high": c, "low": c, "close": c, "volume": 1000 + i,
        }
        for i, c in enumerate(closes)
    ]


# ═══════════════════════════════════════════════════════════════════════════
# 1. The hand-computed adjustment
# ═══════════════════════════════════════════════════════════════════════════

df = build_adjusted_frame(
    _candles(),
    dividends=[{"effective_date": "2026-01-02", "amount": 1.00}],
    splits=[{"effective_date": "2026-01-04", "ratio": 2}],
)

expected_adj = [49.50, 55.00, 60.00, 60.00, 62.00]
got_adj = [round(float(v), 10) for v in df["Adj Close"]]
assert np.allclose(got_adj, expected_adj, atol=1e-8), (
    f"Adj Close mismatch.\n  expected {expected_adj}\n  got      {got_adj}"
)
print(f"✅ adjusted closes match the hand-computed values — {got_adj}")

# Raw closes must be untouched — the sim needs both bases.
assert np.allclose(df["Close"].to_numpy(), [100.0, 110.0, 120.0, 60.0, 62.0])
print("✅ raw Close preserved alongside Adj Close")

# The newest bar is the anchor: no future events, so factor is exactly 1.
assert abs(float(df["Adj Close"].iloc[-1]) - float(df["Close"].iloc[-1])) < TOL, (
    "the most recent bar must have an adjustment factor of exactly 1.0"
)
print("✅ newest bar anchors at factor 1.0 (Adj Close == Close)")

# The split must leave the series continuous, not show a -50% crash.
pre_split, post_split = float(df["Adj Close"].iloc[2]), float(df["Adj Close"].iloc[3])
assert abs(pre_split - post_split) < TOL, (
    f"2-for-1 split left a discontinuity: {pre_split} -> {post_split}"
)
print("✅ 2-for-1 split leaves the adjusted series continuous")

# Events are echoed in the columns _normalize_ohlcv reads.
assert float(df["Dividends"].iloc[1]) == 1.00
assert float(df["Stock Splits"].iloc[3]) == 2.0
assert float(df["Stock Splits"].iloc[0]) == 1.0, "non-split bars default to 1.0"
print("✅ Dividends / Stock Splits columns carry the events")

# ═══════════════════════════════════════════════════════════════════════════
# 2. No corporate actions → adjusted IS raw
# ═══════════════════════════════════════════════════════════════════════════

flat = build_adjusted_frame(_candles(), dividends=[], splits=[])
assert np.allclose(flat["Adj Close"].to_numpy(), flat["Close"].to_numpy()), (
    "with no events the adjusted series must equal the raw series"
)
print("✅ no events → Adj Close == Close on every bar")

# ═══════════════════════════════════════════════════════════════════════════
# 3. The frame satisfies the downstream contract
# ═══════════════════════════════════════════════════════════════════════════
# This is the real compatibility proof: feed our frame to the REAL
# _normalize_ohlcv the backtester uses and confirm it produces the simulator's
# price basis. If the shape were wrong, normalize would silently fall through
# to its else-branch and the sim would trade raw prices.

required = {"Open", "High", "Low", "Close", "Adj Close", "Volume",
            "Dividends", "Stock Splits"}
assert required.issubset(set(df.columns)), f"missing columns: {required - set(df.columns)}"
print(f"✅ frame carries every column yfinance provides — {sorted(required)}")

norm = _normalize_ohlcv(df)
# normalize sets Close to the adjusted price and keeps raw_close.
assert np.allclose(norm["Close"].to_numpy(), expected_adj, atol=1e-8), (
    "normalize did not adopt the adjusted close — the frame shape is wrong"
)
assert np.allclose(norm["raw_close"].to_numpy(), [100.0, 110.0, 120.0, 60.0, 62.0]), (
    "normalize lost the raw close"
)
print("✅ backtester._normalize_ohlcv adopts it correctly (adjusted Close + raw_close)")

# OHLC must be scaled by the same per-day ratio, not left raw.
assert abs(float(norm["Open"].iloc[0]) - 49.50) < 1e-8, (
    f"Open was not adjusted with Close: {float(norm['Open'].iloc[0])}"
)
print("✅ Open/High/Low ride the same adjustment ratio as Close")

# ═══════════════════════════════════════════════════════════════════════════
# 4. Degenerate inputs degrade, never raise
# ═══════════════════════════════════════════════════════════════════════════

empty = build_adjusted_frame([], [], [])
assert empty.empty and required.issubset(set(empty.columns)), (
    "an empty fetch must still return a correctly-shaped empty frame"
)
print("✅ no candles → empty frame with the right columns (no raise)")

junk = build_adjusted_frame(
    [{"timestamp": "nonsense", "close": "abc"},
     {"timestamp": "2026-01-01", "close": None},
     {"timestamp": "2026-01-02", "close": 50.0}],
    dividends=[{"effective_date": None, "amount": 1}],
    splits=[{"effective_date": "2026-01-02", "ratio": 0}],  # 0 must not divide
)
assert len(junk) == 1 and float(junk["Close"].iloc[0]) == 50.0, (
    "unparseable rows must be dropped, not crash or poison the frame"
)
assert np.isfinite(float(junk["Adj Close"].iloc[0])), "a zero ratio produced a non-finite price"
print("✅ unparseable rows / zero split ratio are dropped safely")

# A dividend on the very first bar has no prior close to use as a basis.
first_bar_div = build_adjusted_frame(
    _candles(), dividends=[{"effective_date": "2026-01-01", "amount": 5.0}], splits=[]
)
assert np.allclose(first_bar_div["Adj Close"].to_numpy(), first_bar_div["Close"].to_numpy()), (
    "a dividend on bar 0 has nothing before it to scale"
)
print("✅ dividend on the first bar scales nothing (no prior close)")

# ═══════════════════════════════════════════════════════════════════════════
# 5. Off by default
# ═══════════════════════════════════════════════════════════════════════════

assert is_configured() is False, (
    "LSE must be inert without LSE_API_KEY — it is opt-in and nothing "
    "changes for anyone who has not set a key"
)
print("✅ inert without LSE_API_KEY (opt-in only)")

print("\n✅ ALL LSE SOURCE TESTS PASSED")
