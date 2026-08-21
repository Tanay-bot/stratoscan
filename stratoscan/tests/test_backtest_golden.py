"""Frozen golden-set backtests — the accuracy CI gate.

Run: python -m tests.test_backtest_golden

Every value below was captured ONCE from the validated engine (which
``verify_backtest_accuracy.py`` proves matches hand-computed ground truth)
on a DETERMINISTIC synthetic price series (fixed RNG seed). If any engine
change alters the math — a fill rule, a fee, an indicator, the equity
reconstruction — these numbers move and the suite fails loudly. That is
the point: a regression in simulation accuracy can never silently ship.

The suite is hermetic: zero network, zero LLM, zero DB — pure pandas +
numpy over the synthetic frames.

Scenarios:
  A. ``trend_up_rsi`` — drifting + wavy market, RSI mean-reversion rules.
     Positive edge, 3 closed trades, MC p5 > 0 (edge survives resampling).
  B. ``flat_buy_hold`` — flat market, no rules (all-in buy & hold at bar 0).
     Trades the noise; negative return, huge negative Sharpe, t-stat < 2.
  C. ``mean_revert`` — flat market, tight mean-reversion with a contradiction
     guard (rsi < 35 AND price > sma_50); losing edge, all windows negative.

Frozen fields: headline metrics (return / final value / Sharpe / drawdown /
win rate / trades), Monte Carlo percentiles + prob_positive, bootstrap CI
(Sharpe + drawdown + t-stat), walk-forward IS/OOS + rolling verdict, and
the parameter-sensitivity verdicts.
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.backtester import parameter_sensitivity, run_backtest, walk_forward_analysis  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ {label} {detail}")


def close(a: float, b: float, rel: float = 1e-4) -> bool:
    return math.isclose(a, b, rel_tol=rel, abs_tol=1e-6)


def frame(n=260, start="2020-01-01", base=100.0, drift=0.0, vol=0.02, wave=0.0) -> pd.DataFrame:
    """Deterministic synthetic daily frame (matches the golden-capture probe)."""
    idx = pd.bdate_range(start, periods=n)
    rng = np.random.default_rng(1234)
    t = np.arange(n, dtype=float)
    noise = rng.normal(0, vol, n)
    # NOTE: the GOLDEN values were captured with THIS exact construction.
    # Do not "simplify" it; the frozen expectations are keyed to it.
    close = base + drift * t + wave * np.sin(t / 12.0) + np.cumsum(noise) * base * 0.05
    close = np.maximum(close, 1.0)
    return pd.DataFrame(
        {
            "Open": close * 0.999,
            "High": close * 1.008,
            "Low": close * 0.992,
            "Close": close,
            "Volume": 1_000_000.0,
        },
        index=idx,
    )


def scenario(name: str, n=260, **kw) -> dict:
    rules = kw.pop("_rules", {})
    df = frame(n=n, **kw)
    res = run_backtest(
        symbol=name.upper(),
        strategy_rules=rules,
        start_date=str(df.index[0])[:10],
        end_date=str(df.index[-1])[:10],
        df=df,
        compute_robustness=True,
    )
    wf = walk_forward_analysis(df, rules, symbol=name.upper())
    ps = parameter_sensitivity(df, rules, symbol=name.upper())
    return {"df": df, "res": res, "wf": wf, "ps": ps}


# ═══════════════════════════════════════════════════════════════════════════
# Scenario A — trend up + RSI mean-reversion (positive edge)
# ═══════════════════════════════════════════════════════════════════════════
print("Scenario A: trend_up_rsi — RSI mean-reversion on a rising market")
A = scenario(
    "trend_up_rsi",
    drift=0.08,
    wave=4.0,
    _rules={"buy_rules": ["rsi < 30"], "sell_rules": ["rsi > 70"], "position_sizing": "all_in"},
)
r = A["res"]
check("total_return ≈ +0.024757", close(r.total_return, 0.024757), f"got {r.total_return}")
check("final_value ≈ $102,475.70", close(r.final_value, 102475.698958), f"got {r.final_value}")
check("sharpe ≈ 1.414951", close(r.sharpe_ratio or 0, 1.414951), f"got {r.sharpe_ratio}")
check("max_drawdown ≈ -0.02093", close(r.max_drawdown, -0.02093), f"got {r.max_drawdown}")
check("win_rate ≈ 1.0", close(r.win_rate, 1.0), f"got {r.win_rate}")
check("3 closed trades", r.total_trades == 3, f"got {r.total_trades}")
mc = r.monte_carlo
check("MC available", bool(mc and mc["available"]))
if mc and mc["available"]:
    check("MC p5 ≈ +0.019163", close(mc["p5"], 0.019163), f"got {mc['p5']}")
    check("MC median ≈ +0.024761", close(mc["median"], 0.024761), f"got {mc['median']}")
    check("MC p95 ≈ +0.03039", close(mc["p95"], 0.03039), f"got {mc['p95']}")
    check("MC prob_positive = 1.0", close(mc["prob_positive"], 1.0), f"got {mc['prob_positive']}")
ci = r.confidence_intervals
check("CI available", bool(ci and ci["available"]))
if ci and ci["available"]:
    check(
        "Sharpe CI [-0.128, 2.992]",
        close(ci["sharpe_ci"][0], -0.128384) and close(ci["sharpe_ci"][1], 2.991867),
        f"got {ci['sharpe_ci']}",
    )
    check("t-stat ≈ 1.434", close(ci["sharpe_t_stat"] or 0, 1.434468), f"got {ci['sharpe_t_stat']}")
wf = A["wf"]
check("walk-forward available", bool(wf and wf.get("available")))
if wf and wf.get("available"):
    check("IS ≈ +0.019464", close(wf["in_sample"]["total_return"], 0.019464), f"got {wf['in_sample']['total_return']}")
    check("OOS ≈ +0.00519", close(wf["out_of_sample"]["total_return"], 0.00519), f"got {wf['out_of_sample']['total_return']}")
    check("rolling verdict 'Mixed (2/4 windows positive)'", wf["rolling"]["verdict"] == "Mixed (2/4 windows positive)", f"got {wf['rolling']['verdict']}")
    check("4 rolling windows", wf["rolling"]["total_windows"] == 4, f"got {wf['rolling']['total_windows']}")
ps = A["ps"]
check("sensitivity available", bool(ps and ps.get("available")))
if ps and ps.get("available"):
    check("no overfit signal", ps["overfit_signal"] is False, f"got {ps['overfit_signal']}")
    check("0/12 losing combos", ps["losing_combos"] == 0, f"got {ps['losing_combos']}")

# ═══════════════════════════════════════════════════════════════════════════
# Scenario B — flat market, buy & hold (no rules)
# ═══════════════════════════════════════════════════════════════════════════
print("\nScenario B: flat_buy_hold — all-in buy & hold on noise")
B = scenario(
    "flat_buy_hold",
    drift=0.0,
    wave=0.0,
    _rules={"buy_rules": [], "sell_rules": [], "position_sizing": "all_in"},
)
r = B["res"]
check("total_return ≈ -0.317194", close(r.total_return, -0.317194), f"got {r.total_return}")
check("final_value ≈ $68,280.55", close(r.final_value, 68280.550427), f"got {r.final_value}")
check("sharpe ≈ -19.26", close(r.sharpe_ratio or 0, -19.260262), f"got {r.sharpe_ratio}")
check("max_drawdown ≈ -0.317194", close(r.max_drawdown, -0.317194), f"got {r.max_drawdown}")
check("130 trades", r.total_trades == 130, f"got {r.total_trades}")
mc = r.monte_carlo
if mc and mc["available"]:
    check("MC p95 < 0 (all outcomes lose)", mc["p95"] < 0, f"got {mc['p95']}")
    check("MC prob_positive = 0.0", close(mc["prob_positive"], 0.0), f"got {mc['prob_positive']}")
ci = r.confidence_intervals
if ci and ci["available"]:
    check("t-stat ≈ -19.53 (deeply negative)", close(ci["sharpe_t_stat"] or 0, -19.525933), f"got {ci['sharpe_t_stat']}")
wf = B["wf"]
if wf and wf.get("available"):
    check("verdict 'All windows negative'", wf["rolling"]["verdict"] == "All windows negative", f"got {wf['rolling']['verdict']}")
ps = B["ps"]
if ps and ps.get("available"):
    check("12/12 losing combos", ps["losing_combos"] == 12, f"got {ps['losing_combos']}")

# ═══════════════════════════════════════════════════════════════════════════
# Scenario C — tight mean-reversion with a contradiction guard
# ═══════════════════════════════════════════════════════════════════════════
print("\nScenario C: mean_revert — tight mean-reversion, losing edge")
C = scenario(
    "mean_revert",
    drift=0.0,
    wave=8.0,
    _rules={
        "buy_rules": ["rsi < 35", "price > sma_50"],
        "sell_rules": ["rsi > 65"],
        "position_sizing": "all_in",
    },
)
r = C["res"]
check("total_return ≈ -0.099182", close(r.total_return, -0.099182), f"got {r.total_return}")
check("final_value ≈ $90,081.78", close(r.final_value, 90081.783929), f"got {r.final_value}")
check("sharpe ≈ -2.5016", close(r.sharpe_ratio or 0, -2.501581), f"got {r.sharpe_ratio}")
check("max_drawdown ≈ -0.140771", close(r.max_drawdown, -0.140771), f"got {r.max_drawdown}")
check("3 trades", r.total_trades == 3, f"got {r.total_trades}")
mc = r.monte_carlo
if mc and mc["available"]:
    check("MC median ≈ -0.0992", close(mc["median"], -0.0992), f"got {mc['median']}")
    check("MC prob_positive = 0.0", close(mc["prob_positive"], 0.0), f"got {mc['prob_positive']}")
wf = C["wf"]
if wf and wf.get("available"):
    check("verdict 'All windows negative'", wf["rolling"]["verdict"] == "All windows negative", f"got {wf['rolling']['verdict']}")

print()
print(f"🎯 Golden-set: {PASS} passed, {FAIL} failed")
if FAIL:
    print("❌ GOLDEN VALUES MOVED — an engine change altered the simulation math.")
    sys.exit(1)
print("✅ Frozen golden set intact — simulation accuracy unchanged.")
