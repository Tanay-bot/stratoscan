"""Day 9 — the Tune panel's RSI thresholds reach the engine. Hermetic.

Before Day 9 the Backtest page appended "momentum: RSI buy below 30, RSI sell
above 70, SMA cross 20/50" to the query text. The deterministic parser never
read it, so moving those fields changed nothing. They are now structured
request fields applied to the translated rules, and the applied rules are
what the response echoes as `strategy`.

Run: python -m tests.test_tune_overrides
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["DATABASE_URL"] = ""
os.environ.setdefault("JWT_SECRET", "tune-overrides-dummy-secret")
os.environ["ANALYTICS_ENABLED"] = "false"

from unittest import mock  # noqa: E402

from fastapi import HTTPException  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from tests.fakes import FakeLLMRouter  # noqa: E402

mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()

from src.api import BacktestRequest, _run_backtest_pipeline  # noqa: E402


class _Engine:
    _last_screen_message = None
    _last_fill_note = None

    def backtest_top_results(self, interpreted, top_n=5, **kwargs):
        return {}

    def backtest(self, *a, **k):
        return {}


USER = {"email": "t@t.test", "plan": "free"}


def rules_for(**fields):
    with mock.patch("src.backtester.BacktestEngine", return_value=_Engine()):
        rules, *_ = _run_backtest_pipeline(BacktestRequest(**fields), USER)
    return rules["buy_rules"], rules["sell_rules"]


# Nothing set → exactly the translated rules.
assert rules_for(query="oversold large cap stocks with RSI below 30") == (
    ["rsi < 30.0"], ["rsi > 70"])
assert rules_for(query="large cap stocks with bullish momentum") == (
    ["price > sma_50", "sma_50 > sma_200"], ["price < sma_200"])
print("✅ no overrides → the strategy's own rules (momentum: trend entry, trend-break exit)")

# Entry threshold replaces the RSI entry, never stacks a second one.
buy, sell = rules_for(query="oversold large cap stocks with RSI below 30", rsi_buy_below=35)
assert buy == ["rsi < 35"] and sell == ["rsi > 70"], (buy, sell)
# On an entry that had no RSI rule it is ADDED (the user asked for it).
buy, _ = rules_for(query="large cap stocks with bullish momentum", rsi_buy_below=45)
assert buy == ["price > sma_50", "sma_50 > sma_200", "rsi < 45"], buy
print("✅ rsi_buy_below replaces the RSI entry threshold, or adds one")

# Exit threshold replaces the exit outright.
_, sell = rules_for(query="large cap stocks with bullish momentum", rsi_sell_above=75)
assert sell == ["rsi > 75"], sell
_, sell = rules_for(query="oversold large cap stocks with RSI below 30", rsi_sell_above=60.5)
assert sell == ["rsi > 60.5"], sell
print("✅ rsi_sell_above becomes the exit")

# Nonsense is refused, not silently simulated.
try:
    rules_for(query="oversold stocks", rsi_buy_below=60, rsi_sell_above=55)
    raise AssertionError("buy-below >= sell-above must be refused")
except HTTPException as e:
    assert e.status_code == 422
for bad in (0, 100, -5, 150):
    try:
        BacktestRequest(query="x", rsi_buy_below=bad)
        raise AssertionError(f"rsi_buy_below={bad} accepted")
    except ValidationError:
        pass
print("✅ out-of-range or crossed thresholds → 422")

# A re-run from the panel must give the same numbers as typing the same
# strategy from scratch (Day 9 "done when").
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.backtester import run_backtest  # noqa: E402

rng = np.random.default_rng(11)
close = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.018, 1500)))
idx = pd.bdate_range("2019-01-02", periods=1500)
df = pd.DataFrame({"Open": np.r_[close[0], close[:-1]], "High": close * 1.01,
                   "Low": close * 0.99, "Close": close, "Volume": 5_000_000}, index=idx)


def sim(**fields):
    with mock.patch("src.backtester.BacktestEngine", return_value=_Engine()):
        rules, *_ = _run_backtest_pipeline(BacktestRequest(**fields), USER)
    r = run_backtest("EQ", rules, "2019-01-02", str(idx[-1].date()), df=df,
                     compute_robustness=False)
    return r.total_return, r.final_value, r.total_trades, [t.buy_date for t in r.trades]


tuned = sim(query="oversold large cap stocks with RSI below 30", rsi_buy_below=35, rsi_sell_above=65)
assert tuned[2] > 0, "the comparison needs trades to mean anything"
with mock.patch("src.backtester.BacktestEngine", return_value=_Engine()):
    typed_rules, *_ = _run_backtest_pipeline(
        BacktestRequest(query="oversold large cap stocks with RSI below 35", rsi_sell_above=65), USER)
retyped = run_backtest("EQ", typed_rules, "2019-01-02", str(idx[-1].date()), df=df,
                       compute_robustness=False)
assert tuned == (retyped.total_return, retyped.final_value, retyped.total_trades,
                 [t.buy_date for t in retyped.trades]), (tuned, retyped.total_return)
print(f"✅ a panel re-run (RSI 35/65) matches the same thresholds typed from scratch: "
      f"{tuned[2]} trades, {tuned[0] * 100:+.2f}% both ways")

mock.patch.stopall()
print("\nALL TUNE-OVERRIDE CHECKS PASSED")
