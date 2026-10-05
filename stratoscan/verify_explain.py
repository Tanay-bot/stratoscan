"""Live probe: the compliance guard against the REAL model (Day 11).

Run: python verify_explain.py           (needs LLM provider keys; no DB)
     python verify_explain.py --n 10    (fewer calls)

The hermetic suite (tests/test_explain.py) proves the guards with a faked
model. What only a real model can show:

  1. Over N varied engine results (synthetic prices, real simulations), every
     narrative that is SHOWN passes the compliance guard and the number guard
     — re-checked here independently of explain_result's own check.
  2. The model mostly obeys the constraints: at most 30% of readings are
     withheld. A higher rate means the prompt has drifted and users are
     seeing the bare findings far too often.
  3. A losing result's narrative says it lost.
  4. The providers answer at all: if every reading is "unavailable", the
     probe fails rather than passing on silence.

Spends one LLM call per result (no cache). Exit 0 = all checks passed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JWT_SECRET", "verify-explain-probe")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src import explain as ex  # noqa: E402
from src.backtester import compute_portfolio, run_backtest, walk_forward_analysis  # noqa: E402

MAX_WITHHELD_SHARE = 0.30


def _prices(seed: int, drift: float, vol: float, n: int = 900) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    idx = pd.bdate_range("2019-01-02", periods=n)
    return pd.DataFrame({"Open": np.r_[close[0], close[:-1]], "High": close * 1.01,
                         "Low": close * 0.99, "Close": close, "Volume": 5_000_000}, index=idx)


def _stored(rules, frames) -> dict:
    from src.api import BacktestRequest, _build_backtest_response

    bt = {}
    for sym, df in frames.items():
        start, end = str(df.index[0].date()), str(df.index[-1].date())
        bt[sym] = run_backtest(sym, rules, start, end, df=df)
        bt[sym].walk_forward = walk_forward_analysis(df, rules, symbol=sym, start_date=start, end_date=end)
    portfolio = compute_portfolio(bt) if len(bt) > 1 else None
    resp = _build_backtest_response(BacktestRequest(query="probe"), rules, bt, None, portfolio)
    return json.loads(resp.model_dump_json())


def _rules(buy, sell, **extra):
    return {"buy_rules": buy, "sell_rules": sell, "initial_capital": 100000.0,
            "fee_rate": 0.001, "slippage": 0.0005, **extra}


RULESETS = [
    _rules(["rsi < 35"], ["rsi > 65"]),
    _rules(["price > sma_50", "sma_50 > sma_200"], ["price < sma_200"]),
    _rules(["rsi < 30"], ["rsi > 70"]),
    _rules(["rsi < 45"], ["rsi > 55"], stop_loss=0.05),
    _rules(["price > sma_50"], ["price < sma_50"], take_profit=0.15),
]
REGIMES = [(0.0007, 0.015), (-0.0006, 0.02), (0.0, 0.025), (0.0003, 0.01), (-0.0002, 0.03)]


def varied(n: int):
    k = 0
    for i, rs in enumerate(RULESETS):
        for j, (drift, vol) in enumerate(REGIMES):
            for frames in ({"S": _prices(100 + i * 10 + j, drift, vol)},
                           {"P": _prices(7 + i, drift, vol), "Q": _prices(70 + j, -drift, vol)}):
                if k >= n:
                    return
                k += 1
                yield _stored(rs, frames)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    args = ap.parse_args()

    counts = {"ok": 0, "withheld": 0, "unavailable": 0}
    failures = []
    for idx, result in enumerate(varied(args.n)):
        out = ex.explain_result(result)  # real router, no cache
        counts[out["narrative_status"]] += 1
        found = [ex.Finding(**f) for f in out["findings"]]
        for f in found:
            if ex.compliance_violations(f.text):
                failures.append(f"#{idx} finding {f.code} fails the guard: {f.text}")
        n = out["narrative"]
        if n:
            text = " ".join([n["summary"], *n["points"]])
            if ex.compliance_violations(text) or ex.unsupported_numbers(text, out["facts"], found):
                failures.append(f"#{idx} a SHOWN narrative fails a guard: {text[:200]}")
            if (out["facts"]["total_return_pct"] or 0) < 0 and not re.search(
                r"\b(lost|loss|losses|losing|down|declin|fell|negative)", text, re.I
            ):
                failures.append(f"#{idx} a losing result's narrative never says it lost: {text[:200]}")
        print(f"  #{idx:02d} {out['basis']:9s} {out['facts']['total_return_pct']!s:>8}%  {out['narrative_status']}")

    total = sum(counts.values())
    print(f"\n{counts}")
    if counts["unavailable"] == total:
        failures.append("every reading was unavailable — are the LLM provider keys set?")
    elif counts["withheld"] / max(1, total - counts["unavailable"]) > MAX_WITHHELD_SHARE:
        failures.append(f"{counts['withheld']} of {total} readings withheld (> {MAX_WITHHELD_SHARE:.0%}): "
                        "the model is not following the prompt's constraints")
    for f in failures:
        print(f"❌ {f}")
    if failures:
        return 1
    print(f"✅ explain compliance: {counts['ok']} narratives shown, all clean; "
          f"{counts['withheld']} withheld by the guard")
    return 0


if __name__ == "__main__":
    sys.exit(main())
