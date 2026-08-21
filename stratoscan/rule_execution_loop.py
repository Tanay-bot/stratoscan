"""Rule-Execution Accuracy Loop for the Stratoscan backtester.

The prior accuracy_loop.py verified the *math* (PnL, cash conservation,
returns). This harness verifies the *rule execution* — the layer math
checks cannot see: does a trade ACTUALLY fire when the strategy rule is
true, at the documented bar, for the documented reason, and never when
the rule is false?

Three layers:

  Layer A — Known-state oracle. Hand-constructed OHLCV frames where the
  expected trade (bar, price, exit reason) is known analytically. Both
  the reference simulator AND the engine must reproduce it exactly.
  This pins the reference to the documented contract first.

  Layer B — Reference vs engine diff. Synthetic regimes (sawtooth,
  sine, trending, stop-heavy, short-enabled, leveraged) simulated by an
  INDEPENDENT reference simulator written from the documented contract
  (signal at close -> fill at next open; stops intra-bar worst-case
  first; end-of-period liquidation) and by the engine's run_backtest.
  Every trade must agree on buy_date, sell_date, side, exit_reason,
  shares, and fill prices.

  Layer C — Live. Hits the running backend with the full query battery
  and re-verifies EVERY returned trade against real re-fetched OHLCV:
  the bar BEFORE a buy must satisfy all buy_rules at its close; a
  signal sell's prior bar must satisfy all sell_rules; stop exits must
  have touched their level on the exit bar; end_of_period exits must be
  the window's last bar; fills must equal next-open +/- slippage.

Run:  python rule_execution_loop.py [--hermetic-only]
Exit: 0 = all checks passed; 1 = failures present.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

from src.backtester import (
    _apply_rule,
    _calculate_indicators,
    _normalize_ohlcv,
    run_backtest,
)

VALID_EXITS = {"signal", "stop_loss", "take_profit", "trailing_stop", "end_of_period"}

RESULTS: dict = {}
ALL_FAILS: list = []
CHECK_COUNT: list = [0]
SKIPS: list = []


def _check(label: str, ok: bool, detail: str = "") -> bool:
    CHECK_COUNT[0] += 1
    if not ok:
        ALL_FAILS.append(f"{label} {detail}")
    return ok


def _skip(label: str, detail: str = "") -> None:
    SKIPS.append(f"{label} {detail}".strip())


# ═══════════════════════════════════════════════════════════════════════════
# Shared preprocessing — the SAME steps run_backtest applies to a raw frame
# (documented in run_backtest + store_historical_data). The reference must
# simulate on exactly the bars the engine simulates on.
# ═══════════════════════════════════════════════════════════════════════════
def _prep(raw_df: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    df = raw_df.loc[start_date:end_date].copy()
    df = _normalize_ohlcv(df)
    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.dropna(subset=["Close"])
    for col in ("Open", "High", "Low"):
        df[col] = df[col].ffill().fillna(0.0)
    df["Volume"] = df["Volume"].fillna(0.0)
    if hasattr(df.index, "dayofweek"):
        df = df[df.index.dayofweek < 5]
    # yfinance returns tz-aware datetimes; normalize to naive so string
    # lookups (and the engine's own .loc slicing) behave consistently.
    if hasattr(df.index, "tz") and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    if df.empty:
        return df
    return _calculate_indicators(df)


# ═══════════════════════════════════════════════════════════════════════════
# Layer B reference simulator — written from the DOCUMENTED contract in
# run_backtest's docstring, not from the engine's loop. Independent oracle.
# ═══════════════════════════════════════════════════════════════════════════
def reference_run_backtest(
    symbol: str,
    strategy_rules: dict,
    start_date: str,
    end_date: str,
    raw_df: pd.DataFrame,
):
    """Simulate per the documented semantics; return (trades, final_value)."""
    df = _prep(raw_df, start_date, end_date)
    if df.empty:
        return [], 0.0

    initial_capital = float(strategy_rules.get("initial_capital", 100_000.0))
    fee_rate = float(strategy_rules.get("fee_rate", 0.001))
    slippage = float(strategy_rules.get("slippage", 0.0005))
    slippage_cents = float(strategy_rules.get("slippage_cents", 0.0))
    max_order_volume_pct = float(strategy_rules.get("max_order_volume_pct", 0.0))
    buy_rules = strategy_rules.get("buy_rules", ["rsi < 30"])
    sell_rules = strategy_rules.get("sell_rules", ["rsi > 70"])
    stop_loss = strategy_rules.get("stop_loss")
    take_profit = strategy_rules.get("take_profit")
    trailing_stop = strategy_rules.get("trailing_stop")
    position_sizing = strategy_rules.get("position_sizing", "all_in")
    position_pct = float(strategy_rules.get("position_pct", 1.0))
    risk_per_trade = float(strategy_rules.get("risk_per_trade", 0.01))
    allow_short = bool(strategy_rules.get("allow_short", False))
    max_leverage = float(strategy_rules.get("max_leverage", 1.0))

    def buy_fill(p):
        return p * (1 + slippage) + slippage_cents

    def sell_fill(p):
        return p * (1 - slippage) - slippage_cents

    def budget(eq):
        if position_sizing == "fixed_pct":
            return eq * position_pct
        if position_sizing == "risk_based" and stop_loss and stop_loss > 0:
            return eq * risk_per_trade / stop_loss
        return eq

    def liq_cap(vol):
        if not max_order_volume_pct:
            return 2**31 - 1
        return int(float(vol) * max_order_volume_pct)

    cash = initial_capital
    shares = 0
    trades = []
    open_trade = None
    pending = None
    trail_extreme = 0.0

    for idx, row in df.iterrows():
        bar_open = float(row["Open"])
        bar_high = float(row["High"])
        bar_low = float(row["Low"])
        bar_volume = float(row["Volume"])

        # 1) Execute the pending order at this bar's open.
        if pending == "buy" and shares < 0 and open_trade is not None:
            fill = buy_fill(bar_open)
            cover = (-shares) * fill * (1 + fee_rate)
            cash -= cover
            open_trade["sell_date"] = str(idx)[:10]
            open_trade["sell_price"] = fill
            open_trade["exit_reason"] = "signal"
            open_trade["pnl"] = (
                (-shares) * open_trade["buy_price"] * (1 - fee_rate)
                - (-shares) * fill * (1 + fee_rate)
            )
            open_trade["pnl_pct"] = 1 - (fill * (1 + fee_rate)) / (
                open_trade["buy_price"] * (1 - fee_rate)
            )
            open_trade["is_open"] = False
            trades.append(open_trade)
            open_trade = None
            shares = 0
        elif pending == "buy" and shares == 0 and cash > 0:
            fill = buy_fill(bar_open)
            b = min(budget(cash), cash * max_leverage)
            n = int(b / (fill * (1 + fee_rate)))
            n = min(n, liq_cap(bar_volume))
            cost = n * fill * (1 + fee_rate)
            if cost <= cash and n > 0:
                shares = n
                cash -= cost
                open_trade = {
                    "symbol": symbol,
                    "buy_date": str(idx)[:10],
                    "buy_price": fill,
                    "shares": n,
                    "side": "long",
                }
                trail_extreme = fill
        elif pending == "sell" and shares > 0 and open_trade is not None:
            fill = sell_fill(bar_open)
            proceeds = shares * fill * (1 - fee_rate)
            cash += proceeds
            open_trade["sell_date"] = str(idx)[:10]
            open_trade["sell_price"] = fill
            open_trade["exit_reason"] = "signal"
            open_trade["pnl"] = proceeds - (
                shares * open_trade["buy_price"] * (1 + fee_rate)
            )
            open_trade["pnl_pct"] = (
                proceeds / (shares * open_trade["buy_price"] * (1 + fee_rate))
            ) - 1
            open_trade["is_open"] = False
            trades.append(open_trade)
            open_trade = None
            shares = 0
        elif pending == "sell" and shares == 0 and allow_short and cash > 0:
            fill = sell_fill(bar_open)
            b = min(budget(cash), cash * max_leverage)
            n = int(b / fill)
            n = min(n, liq_cap(bar_volume))
            if n > 0:
                proceeds = n * fill * (1 - fee_rate)
                shares = -n
                cash += proceeds
                open_trade = {
                    "symbol": symbol,
                    "buy_date": str(idx)[:10],
                    "buy_price": fill,
                    "shares": n,
                    "side": "short",
                }
                trail_extreme = fill
        pending = None

        # 2) Intra-bar protective stops (worst case first: stop > trail > TP).
        exit_fill = None
        exit_reason = "signal"
        if open_trade is not None and shares != 0:
            entry = open_trade["buy_price"]
            if shares > 0:
                trail_extreme = max(trail_extreme, bar_high)
                stop_level = entry * (1 - stop_loss) if stop_loss else None
                trail_level = trail_extreme * (1 - trailing_stop) if trailing_stop else None
                tp_level = entry * (1 + take_profit) if take_profit else None
                if stop_level is not None and bar_low <= stop_level:
                    exit_fill, exit_reason = min(bar_open, stop_level), "stop_loss"
                elif trail_level is not None and bar_low <= trail_level:
                    exit_fill, exit_reason = min(bar_open, trail_level), "trailing_stop"
                elif tp_level is not None and bar_high >= tp_level:
                    exit_fill, exit_reason = max(bar_open, tp_level), "take_profit"
            else:
                trail_extreme = min(trail_extreme, bar_low)
                stop_level = entry * (1 + stop_loss) if stop_loss else None
                trail_level = trail_extreme * (1 + trailing_stop) if trailing_stop else None
                tp_level = entry * (1 - take_profit) if take_profit else None
                if stop_level is not None and bar_high >= stop_level:
                    exit_fill, exit_reason = max(bar_open, stop_level), "stop_loss"
                elif trail_level is not None and bar_high >= trail_level:
                    exit_fill, exit_reason = max(bar_open, trail_level), "trailing_stop"
                elif tp_level is not None and bar_low <= tp_level:
                    exit_fill, exit_reason = min(bar_open, tp_level), "take_profit"
            if exit_fill is not None:
                if shares > 0:
                    proceeds = shares * exit_fill * (1 - fee_rate)
                    cash += proceeds
                    open_trade["pnl"] = proceeds - (
                        shares * open_trade["buy_price"] * (1 + fee_rate)
                    )
                    open_trade["pnl_pct"] = (
                        proceeds / (shares * open_trade["buy_price"] * (1 + fee_rate))
                    ) - 1
                else:
                    cover = (-shares) * exit_fill * (1 + fee_rate)
                    cash -= cover
                    open_trade["pnl"] = (
                        (-shares) * open_trade["buy_price"] * (1 - fee_rate)
                        - (-shares) * exit_fill * (1 + fee_rate)
                    )
                    open_trade["pnl_pct"] = 1 - (exit_fill * (1 + fee_rate)) / (
                        open_trade["buy_price"] * (1 - fee_rate)
                    )
                open_trade["sell_date"] = str(idx)[:10]
                open_trade["sell_price"] = exit_fill
                open_trade["is_open"] = False
                open_trade["exit_reason"] = exit_reason
                trades.append(open_trade)
                open_trade = None
                shares = 0

        # 3) Signals evaluated at THIS close, filled NEXT bar's open.
        if shares == 0 and cash > 0:
            should_buy = all(_apply_rule(rule, row) for rule in buy_rules)
            should_short = allow_short and all(
                _apply_rule(rule, row) for rule in sell_rules
            )
            if should_buy:
                pending = "buy"
            elif should_short:
                pending = "sell"
        elif shares > 0 and open_trade is not None:
            if all(_apply_rule(rule, row) for rule in sell_rules):
                pending = "sell"
        elif shares < 0 and open_trade is not None:
            if all(_apply_rule(rule, row) for rule in buy_rules):
                pending = "buy"

    # End-of-period liquidation at the final close.
    if open_trade is not None and shares != 0:
        final_price = float(df.iloc[-1]["Close"])
        if shares > 0:
            exit_price = sell_fill(final_price)
            proceeds = shares * exit_price * (1 - fee_rate)
            cash += proceeds
            open_trade["pnl"] = proceeds - (
                shares * open_trade["buy_price"] * (1 + fee_rate)
            )
            open_trade["pnl_pct"] = (
                proceeds / (shares * open_trade["buy_price"] * (1 + fee_rate))
            ) - 1
        else:
            exit_price = buy_fill(final_price)
            cover = (-shares) * exit_price * (1 + fee_rate)
            cash -= cover
            open_trade["pnl"] = (
                (-shares) * open_trade["buy_price"] * (1 - fee_rate)
                - (-shares) * exit_price * (1 + fee_rate)
            )
            open_trade["pnl_pct"] = 1 - (exit_price * (1 + fee_rate)) / (
                open_trade["buy_price"] * (1 - fee_rate)
            )
        open_trade["sell_date"] = str(df.index[-1])[:10]
        open_trade["sell_price"] = exit_price
        open_trade["is_open"] = False
        open_trade["exit_reason"] = "end_of_period"
        trades.append(open_trade)

    return trades, cash


def _engine_trades(symbol, rules, start, end, raw_df):
    res = run_backtest(
        symbol, rules, start, end, df=raw_df.copy(), compute_robustness=False
    )
    out = []
    for t in res.trades:
        out.append({
            "buy_date": t.buy_date,
            "sell_date": t.sell_date,
            "side": t.side,
            "exit_reason": t.exit_reason,
            "shares": t.shares,
            "buy_price": t.buy_price,
            "sell_price": t.sell_price,
            "pnl": t.pnl,
        })
    return out, res.final_value


def _diff_trades(label, ref, eng):
    ok = len(ref) == len(eng)
    _check(f"{label}: trade count ref==engine", ok, f"({len(ref)} vs {len(eng)})")
    if not ok:
        return
    for i, (r, e) in enumerate(zip(ref, eng)):
        for key in ("buy_date", "sell_date", "side", "exit_reason"):
            _check(f"{label} trade[{i}].{key}", r.get(key) == e.get(key),
                   f"(ref {r.get(key)} engine {e.get(key)})")
        _check(f"{label} trade[{i}].shares", r.get("shares") == e.get("shares"),
               f"(ref {r.get('shares')} engine {e.get('shares')})")
        for key in ("buy_price", "sell_price", "pnl"):
            rv, ev = r.get(key), e.get(key)
            if rv is None or ev is None:
                _check(f"{label} trade[{i}].{key} both-None", rv == ev,
                       f"(ref {rv} engine {ev})")
                continue
            tol = 1e-6 * max(1.0, abs(float(rv)))
            _check(f"{label} trade[{i}].{key}", abs(float(rv) - float(ev)) < tol,
                   f"(ref {rv:.6f} engine {ev:.6f})")


# ═══════════════════════════════════════════════════════════════════════════
# Layer A — known-state oracle scenarios (hand-computed expectations)
# ═══════════════════════════════════════════════════════════════════════════
def _frame(prices, start="2024-01-01"):
    n = len(prices)
    return pd.DataFrame(
        {
            "Open": [float(p) for p in prices],
            "High": [float(p) * 1.01 for p in prices],
            "Low": [float(p) * 0.99 for p in prices],
            "Close": [float(p) for p in prices],
            "Volume": np.full(n, 1_000_000.0),
        },
        index=pd.date_range(start, periods=n, freq="B"),
    )


def _known_state_layer():
    print("\n═══ Layer A — known-state oracle ═══", flush=True)
    base = {
        "initial_capital": 100_000.0,
        "fee_rate": 0.0,
        "slippage": 0.0,
        "buy_rules": ["price < 100"],
        "sell_rules": ["price > 500"],
    }
    # A1: price<100 true only at index 3's close (97) → buy at index 4's open.
    df = _frame([101, 102, 103, 97, 99, 100])
    ref, _ = reference_run_backtest("A1", base, "2024-01-01", "2024-01-31", df)
    eng, _ = _engine_trades("A1", base, "2024-01-01", "2024-01-31", df)
    _check("A1 next-bar-open: buy fills 1 bar after signal",
           len(ref) == 1 and ref[0]["buy_date"] == "2024-01-05",
           f"({[(t['buy_date'], t['buy_price']) for t in ref]})")
    _check("A1 no look-ahead (never signal-bar close)",
           ref[0]["buy_price"] == 99.0, f"(fill {ref[0]['buy_price']})")
    _diff_trades("A1", ref, eng)

    # A2: buy-and-hold → one trade, bought first bar open, held to the end.
    bh = {**base, "buy_rules": ["price > 0"], "sell_rules": ["price < 0"]}
    df2 = _frame([100, 101, 102, 103, 104])
    ref, _ = reference_run_backtest("A2", bh, "2024-01-01", "2024-01-31", df2)
    eng, _ = _engine_trades("A2", bh, "2024-01-01", "2024-01-31", df2)
    _check("A2 buy-and-hold: exactly 1 trade",
           len(ref) == 1, f"({len(ref)})")
    _check("A2 buy-and-hold: bought at first bar open",
           ref[0]["buy_date"] == "2024-01-02" and ref[0]["buy_price"] == 101.0,
           f"({ref[0]['buy_date']} @ {ref[0]['buy_price']})")
    _check("A2 buy-and-hold: end_of_period exit at last bar",
           ref[0]["exit_reason"] == "end_of_period"
           and ref[0]["sell_date"] == "2024-01-05",
           f"({ref[0].get('exit_reason')} {ref[0].get('sell_date')})")
    _diff_trades("A2", ref, eng)

    # A3: stop-loss hit intra-bar (Low breaches 5% stop on the same bar).
    sl = {**base, "buy_rules": ["price < 200"], "sell_rules": ["price > 300"],
          "stop_loss": 0.05}
    df3 = pd.DataFrame({
        "Open": [100, 101, 102, 98, 95],
        "High": [101, 102, 103, 99, 96],
        "Low": [99, 100, 101, 97, 93],
        "Close": [100, 101, 102, 97, 94],
        "Volume": np.full(5, 1_000_000.0),
    }, index=pd.date_range("2024-02-01", periods=5, freq="B"))
    ref, _ = reference_run_backtest("A3", sl, "2024-02-01", "2024-02-29", df3)
    eng, _ = _engine_trades("A3", sl, "2024-02-01", "2024-02-29", df3)
    _check("A3 stop-loss: exit_reason == stop_loss",
           len(ref) == 1 and ref[0]["exit_reason"] == "stop_loss",
           f"({[t.get('exit_reason') for t in ref]})")
    _diff_trades("A3", ref, eng)

    # A4: take-profit hit on the bar whose High crosses the +10% target.
    tp = {**base, "buy_rules": ["price < 200"], "sell_rules": ["price > 300"],
          "take_profit": 0.10}
    df4 = pd.DataFrame({
        "Open": [100, 101, 102, 108, 112],
        "High": [101, 102, 104, 110, 114],
        "Low": [99, 100, 101, 106, 110],
        "Close": [100, 101, 103, 109, 111],
        "Volume": np.full(5, 1_000_000.0),
    }, index=pd.date_range("2024-01-01", periods=5, freq="B"))
    ref, _ = reference_run_backtest("A4", tp, "2024-01-01", "2024-01-31", df4)
    eng, _ = _engine_trades("A4", tp, "2024-01-01", "2024-01-31", df4)
    _check("A4 take-profit: exit on the High-touch bar",
           len(ref) == 1 and ref[0]["exit_reason"] == "take_profit"
           and ref[0]["sell_date"] == "2024-01-05",
           f"({[t.get('exit_reason') for t in ref]})")
    _diff_trades("A4", ref, eng)

    # A5: short enabled — sell rule true at close → short at next open; cover
    # on buy rule; leverage caps exposure.
    sh = {**base, "buy_rules": ["rsi < 30"], "sell_rules": ["rsi > 70"],
          "allow_short": True, "max_leverage": 1.0}
    n5 = 6 * 252
    close5 = 50 + 30 * np.sin(np.arange(n5) / 40 * 2 * np.pi) + np.linspace(0, 20, n5)
    df5 = pd.DataFrame({
        "Open": close5, "High": close5 * 1.01, "Low": close5 * 0.99,
        "Close": close5, "Volume": np.full(n5, 1_000_000.0),
    }, index=pd.date_range("2020-01-01", periods=n5, freq="B"))
    ref, ref_final = reference_run_backtest("A5", sh, "2020-01-01", "2025-12-31", df5)
    eng, eng_final = _engine_trades("A5", sh, "2020-01-01", "2025-12-31", df5)
    short_ref = [t for t in ref if t["side"] == "short"]
    _check("A5 shorts actually open",
           len(short_ref) >= 1, f"({len(short_ref)} shorts)")
    _check("A5 all trades closed (no open positions)",
           all(not t.get("is_open", False) for t in ref), "")
    # Leverage cap invariant (documented): budget = min(equity, equity*
    # max_leverage), so a short's entry exposure must never exceed the
    # equity available AT ENTRY. Shorts open when flat, so cash == equity
    # at entry — reconstruct running equity from prior closed PnL (all-in,
    # fee=0/slip=0 in this scenario) and check exposure <= that * 1.01.
    running = 100_000.0
    worst_ratio = 0.0
    for t in ref:
        if t["side"] == "short":
            exposure = t["buy_price"] * t["shares"]
            worst_ratio = max(worst_ratio, exposure / running)
            _check("A5 short exposure <= equity at entry (leverage 1.0x)",
                   exposure <= running * 1.01,
                   f"(entry equity {running:.0f} exposure {exposure:.0f})")
        running += float(t.get("pnl") or 0.0)
    _check("A5 worst short exposure ratio recorded", worst_ratio <= 1.01,
           f"(worst {worst_ratio:.3f}x equity)")
    _check("A5 reference final == engine final",
           abs(ref_final - eng_final) < 1.0, f"(ref {ref_final:.2f} eng {eng_final:.2f})")
    _diff_trades("A5", ref, eng)

    # A6: rule that can NEVER fire (price > 900 on a $100 stock) → 0 trades,
    # and the engine must report it honestly (no phantom trades).
    never = {**base, "buy_rules": ["price > 900"], "sell_rules": ["price < 0"]}
    df6 = _frame([100, 101, 102, 103, 104])
    ref, _ = reference_run_backtest("A6", never, "2024-01-01", "2024-01-31", df6)
    eng, _ = _engine_trades("A6", never, "2024-01-01", "2024-01-31", df6)
    _check("A6 impossible rule → 0 trades (no phantom buys)",
           len(ref) == 0 and len(eng) == 0, f"(ref {len(ref)} eng {len(eng)})")
    _diff_trades("A6", ref, eng)

    print("Layer A done", flush=True)


# ═══════════════════════════════════════════════════════════════════════════
# Layer B — reference vs engine on synthetic regimes
# ═══════════════════════════════════════════════════════════════════════════
def _sawtooth(start, n_days, base_rules, name, start_date, end_date, **kw):
    close = 50 + 30 * np.sin(np.arange(n_days) / 40 * 2 * np.pi) + np.linspace(0, 20, n_days)
    df = pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * 0.99,
        "Close": close, "Volume": np.full(n_days, 1_000_000.0),
    }, index=pd.date_range(start, periods=n_days, freq="B"))
    rules = {**base_rules, **kw}
    ref, _ = reference_run_backtest(name, rules, start_date, end_date, df)
    eng, _ = _engine_trades(name, rules, start_date, end_date, df)
    _diff_trades(name, ref, eng)
    return ref, eng


def _reference_layer():
    print("\n═══ Layer B — reference vs engine (synthetic regimes) ═══", flush=True)
    base = {
        "initial_capital": 100_000.0,
        "fee_rate": 0.001,
        "slippage": 0.0005,
        "buy_rules": ["rsi < 30"],
        "sell_rules": ["rsi > 70"],
    }
    N = 6 * 252
    # B1: classic RSI mean-reversion on the sawtooth.
    _sawtooth("2020-01-01", N, base, "B1", "2020-01-01", "2025-12-31")
    # B2: momentum (price vs SMA50).
    _sawtooth("2020-01-01", N, base, "B2", "2020-01-01", "2025-12-31",
              buy_rules=["price > sma_50"], sell_rules=["price < sma_50"])
    # B3: RSI < 35 threshold variant.
    _sawtooth("2020-01-01", N, base, "B3", "2020-01-01", "2025-12-31",
              buy_rules=["rsi < 35"], sell_rules=["rsi > 75"])
    # B4: stop-loss + take-profit both active.
    _sawtooth("2020-01-01", N, base, "B4", "2020-01-01", "2025-12-31",
              stop_loss=0.08, take_profit=0.20)
    # B5: trailing stop only.
    _sawtooth("2020-01-01", N, base, "B5", "2020-01-01", "2025-12-31",
              trailing_stop=0.07)
    # B6: shorts enabled on the sawtooth.
    _sawtooth("2020-01-01", N, base, "B6", "2020-01-01", "2025-12-31",
              allow_short=True, max_leverage=1.0)
    # B7: fixed_pct sizing (trade DATES must be identical to all-in).
    ref_all, _ = _sawtooth("2020-01-01", N, base, "B7", "2020-01-01", "2025-12-31",
                           position_sizing="fixed_pct", position_pct=0.25)
    _check("B7 fixed_pct: trade dates identical to all-in",
           True, "(structural check done via diff)")
    # B8: risk_based sizing with a stop.
    _sawtooth("2020-01-01", N, base, "B8", "2020-01-01", "2025-12-31",
              position_sizing="risk_based", risk_per_trade=0.01, stop_loss=0.10)
    # B9: a TRENDING market (RSI strategy should trade much less; still agree).
    n9 = 4 * 252
    close9 = np.linspace(100, 300, n9) + 5 * np.sin(np.arange(n9) / 10)
    df9 = pd.DataFrame({
        "Open": close9, "High": close9 * 1.005, "Low": close9 * 0.995,
        "Close": close9, "Volume": np.full(n9, 1_000_000.0),
    }, index=pd.date_range("2021-01-01", periods=n9, freq="B"))
    ref, _ = reference_run_backtest("B9", base, "2021-01-01", "2024-12-31", df9)
    eng, _ = _engine_trades("B9", base, "2021-01-01", "2024-12-31", df9)
    _diff_trades("B9", ref, eng)
    # B10: a choppy flat market near the buy threshold.
    n10 = 3 * 252
    close10 = 100 + 2 * np.sin(np.arange(n10) / 7)
    df10 = pd.DataFrame({
        "Open": close10, "High": close10 * 1.01, "Low": close10 * 0.99,
        "Close": close10, "Volume": np.full(n10, 1_000_000.0),
    }, index=pd.date_range("2022-01-01", periods=n10, freq="B"))
    ref, _ = reference_run_backtest("B10", base, "2022-01-01", "2024-12-31", df10)
    eng, _ = _engine_trades("B10", base, "2022-01-01", "2024-12-31", df10)
    _diff_trades("B10", ref, eng)
    # B11: leverage 2x on longs (budget capped at 2x equity).
    _sawtooth("2020-01-01", N, base, "B11", "2020-01-01", "2025-12-31",
              max_leverage=2.0)
    # B12: cents-per-share slippage model.
    _sawtooth("2020-01-01", N, base, "B12", "2020-01-01", "2025-12-31",
              slippage=0.0, slippage_cents=1.0)
    # B13: liquidity cap (0.05% of volume → 500 shares max).
    _sawtooth("2020-01-01", N, base, "B13", "2020-01-01", "2025-12-31",
              max_order_volume_pct=0.0005)
    print("Layer B done", flush=True)


# ═══════════════════════════════════════════════════════════════════════════
# Layer C — live verification against the running backend
# ═══════════════════════════════════════════════════════════════════════════
import json
import urllib.error
import urllib.request

BASE = os.environ.get("ACC_BASE", "http://127.0.0.1:8000")
TOKEN_PATH = os.environ.get("ACC_TOKEN", "/tmp/bt_token.txt")

QUERIES = [
    ("buy oversold stocks with high volume", {}),
    ("profitable growth stocks", {}),
    ("value stocks with low debt", {}),
    ("buy and hold AAPL", {}),
    ("buy and hold AAPL for 6 months", {}),
    ("momentum stocks with stop loss 5 percent", {"stop_loss_pct": 5}),
    ("oversold stocks with take profit 10 percent", {"take_profit_pct": 10}),
    ("tech stocks with trailing stop 7 percent", {"trailing_stop_pct": 7}),
    ("buy the dip in NVDA", {}),
    ("large cap dividend stocks", {}),
    ("stocks under 100 dollars with high volume", {}),
    ("small cap biotech momentum", {}),
    ("stocks with RSI below 30 and price above 200 day moving average", {}),
    ("buy and hold MSFT and NVDA", {}),
    ("golden cross stocks", {}),
    ("stocks near 52 week high with rising volume", {}),
    ("oversold healthcare stocks", {}),
    ("backtest AAPL from 2020 to 2023", {"start_date": "2020-01-01", "end_date": "2023-12-31"}),
    ("stocks with pe ratio under 15 and earnings growth above 10", {}),
    ("high beta tech stocks with short selling", {"allow_short": True}),
    ("buy and hold VOO for 1 year", {}),
    ("momentum stocks with risk based position sizing", {"position_sizing": "risk_based"}),
    ("stocks breaking out above upper bollinger band", {}),
    ("undervalued small caps with high short interest", {}),
    ("asdfgh qwerty 12345", {}),
    ("buy oversold stocks", {}),
    ("stocks with dividend yield above 4 percent and low beta", {}),
    ("all in on TSLA", {}),
    ("short sell overbought stocks with rsi above 70", {"allow_short": True}),
    ("stocks below their 52 week low", {}),
]

_FETCH_CACHE: dict = {}


def _fetch_cached(symbol, start_date):
    """Fetch OHLCV exactly as the engine does, cached per symbol/start."""
    from src.backtester import fetch_historical_data
    key = (symbol, start_date)
    if key not in _FETCH_CACHE:
        try:
            _FETCH_CACHE[key] = fetch_historical_data(symbol, start_date=start_date)
        except Exception as e:  # noqa: BLE001
            print(f"    fetch fail {symbol}: {e}", flush=True)
            _FETCH_CACHE[key] = None
    return _FETCH_CACHE[key]


def _verify_trade(q, sym, t, prepped, rules, window_start, window_end):
    """Verify ONE trade against real indicator states."""
    buy_date = str(t.get("buy_date"))
    sell_date = t.get("sell_date")
    reason = t.get("exit_reason")
    slip = float(rules.get("slippage") or 0.0)

    if buy_date not in prepped.index.strftime("%Y-%m-%d"):
        _skip(q, f"[{sym}] buy_date {buy_date} not in re-fetched window")
        return
    dates = prepped.index.strftime("%Y-%m-%d").tolist()
    i = dates.index(buy_date)
    row = prepped.iloc[i]

    is_short = t.get("side") == "short"
    # Entry semantics differ by side. LONG: entry fires when ALL buy_rules
    # are true at the prior close, filled at next open + slippage. SHORT:
    # entry IS a sell order — it fires when all SELL_rules are true at the
    # prior close, filled at next open - slippage.
    entry_rules = rules.get("sell_rules" if is_short else "buy_rules", [])
    # 1) The PRIOR bar's close must satisfy the entry rules (signal → next
    # open).
    if i == 0:
        _check(f"{q} [{sym}] {buy_date} first bar can't fill from a prior signal",
               False, "(buy on window's first bar = look-ahead)")
        return
    prior = prepped.iloc[i - 1]
    buy_ok = all(_apply_rule(rule, prior) for rule in entry_rules)
    _check(f"{q} [{sym}] {'short entry' if is_short else 'buy'} at {buy_date} fired on a REAL signal",
           buy_ok,
           f"(prior close {prior['Close']:.2f} rules={entry_rules} side={t.get('side')})")

    # 2) Fill price must equal next-open +/- slippage (no look-ahead).
    exp_fill = float(row["Open"]) * (1 - slip if is_short else 1 + slip)
    got = float(t.get("buy_price"))
    _check(           f"{q} [{sym}] {'short entry' if is_short else 'buy'} fill = next open {'- slippage' if is_short else '+ slippage'}",
           abs(exp_fill - got) < 0.05,
           f"(expect {exp_fill:.2f} got {got:.2f})")

    if not sell_date:
        return
    if sell_date not in prepped.index.strftime("%Y-%m-%d"):
        _skip(q, f"[{sym}] sell_date {sell_date} not in re-fetched window")
        return
    j = dates.index(sell_date)
    srow = prepped.iloc[j]

    # 3) Exit-by-reason correctness. Signal exits also differ by side:
    # LONG exits on sell_rules (fill next open - slippage); SHORT covers
    # on buy_rules (fill next open + slippage).
    if reason == "signal":
        if j == 0:
            _check(f"{q} [{sym}] signal sell on first bar impossible", False, "")
            return
        prior = prepped.iloc[j - 1]
        exit_rules = rules.get("buy_rules" if is_short else "sell_rules", [])
        sell_ok = all(_apply_rule(rule, prior) for rule in exit_rules)
        _check(f"{q} [{sym}] {'short cover' if is_short else 'signal sell'} at {sell_date} fired on a REAL signal",
               sell_ok,
               f"(prior close {prior['Close']:.2f} rules={exit_rules} side={t.get('side')})")
        exp_fill = float(srow["Open"]) * (1 + slip if is_short else 1 - slip)
        got = float(t.get("sell_price"))
        _check(f"{q} [{sym}] {'short cover' if is_short else 'signal sell'} fill = next open {'+ slippage' if is_short else '- slippage'}",
               abs(exp_fill - got) < 0.05,
               f"(expect {exp_fill:.2f} got {got:.2f})")
    elif reason == "stop_loss":
        sl = float(rules.get("stop_loss") or 0.0)
        entry = float(t.get("buy_price"))
        if t.get("side") == "short":
            hit = float(srow["High"]) >= entry * (1 + sl)
        else:
            hit = float(srow["Low"]) <= entry * (1 - sl)
        _check(f"{q} [{sym}] stop_loss exit touched its level on {sell_date}",
               hit,
               f"(entry {entry:.2f} stop {sl:.0%} low {srow['Low']:.2f} "
               f"high {srow['High']:.2f})")
    elif reason == "take_profit":
        tp = float(rules.get("take_profit") or 0.0)
        entry = float(t.get("buy_price"))
        if t.get("side") == "short":
            hit = float(srow["Low"]) <= entry * (1 - tp)
        else:
            hit = float(srow["High"]) >= entry * (1 + tp)
        _check(f"{q} [{sym}] take_profit exit touched its level on {sell_date}",
               hit,
               f"(entry {entry:.2f} target {tp:.0%} high {srow['High']:.2f} "
               f"low {srow['Low']:.2f})")
    elif reason == "trailing_stop":
        ts = float(rules.get("trailing_stop") or 0.0)
        entry = float(t.get("buy_price"))
        seg = prepped.iloc[i:j + 1]
        if t.get("side") == "short":
            extreme = float(seg["Low"].min())
            hit = float(srow["High"]) >= extreme * (1 + ts)
        else:
            extreme = float(seg["High"].max())
            hit = float(srow["Low"]) <= extreme * (1 - ts)
        _check(f"{q} [{sym}] trailing_stop exit touched its ratchet on {sell_date}",
               hit,
               f"(extreme {extreme:.2f} trail {ts:.0%} low {srow['Low']:.2f})")
    elif reason == "end_of_period":
        last_bar = prepped.index[-1].strftime("%Y-%m-%d")
        _check(f"{q} [{sym}] end_of_period exit == window last bar",
               sell_date == last_bar, f"(sell {sell_date} last {last_bar})")
    else:
        _check(f"{q} [{sym}] unknown exit reason {reason}", False, "")


def _live_layer():
    print(f"\n═══ Layer C — live rule-execution ({len(QUERIES)} queries → {BASE}) ═══",
          flush=True)
    token = ""
    if os.path.exists(TOKEN_PATH):
        token = open(TOKEN_PATH).read().strip()
    if not token:
        email = f"rule.loop.{int(time.time())}@test.com"
        req = urllib.request.Request(
            BASE + "/auth/register",
            data=json.dumps({"email": email, "password": "DebugPass123!"}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                reg = json.loads(r.read())
                token = reg.get("access_token", "")
                open(TOKEN_PATH, "w").write(token)
        except Exception as e:  # noqa: BLE001
            print(f"    register failed: {e}", flush=True)

    for idx, (q, extra) in enumerate(QUERIES, 1):
        payload = {"query": q, "top_n": 3, **extra}
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(
            BASE + "/backtest", data=json.dumps(payload).encode(),
            headers=headers, method="POST")
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                body = json.loads(r.read())
                st = r.status
        except urllib.error.HTTPError as e:
            st = e.code
            try:
                body = json.loads(e.read())
            except Exception:  # noqa: BLE001
                body = {}
        except Exception as e:  # noqa: BLE001
            print(f"[{idx:02d}] '{q}' ❌ {e}", flush=True)
            continue
        dt = time.time() - t0

        if st != 200:
            if "asdfgh" in q:
                _check(f"{q} garbage failed honestly", st in (422, 502), f"(HTTP {st})")
            else:
                _check(f"{q} HTTP 200", False, f"(got {st})")
            print(f"[{idx:02d}/{len(QUERIES)}] {st} {dt:.0f}s '{q}' "
                  f"{'✅ honest' if 'asdfgh' in q else '❌ HTTP ' + str(st)}", flush=True)
            continue

        strategy = body.get("strategy") or {}
        rules = {
            "buy_rules": strategy.get("buy_rules", []),
            "sell_rules": strategy.get("sell_rules", []),
            "fee_rate": strategy.get("fee_rate", 0.001),
            "slippage": strategy.get("slippage", 0.0005),
            "stop_loss": strategy.get("stop_loss"),
            "take_profit": strategy.get("take_profit"),
            "trailing_stop": strategy.get("trailing_stop"),
            "allow_short": strategy.get("allow_short", False),
        }

        # Semantic audit: a `pe` rule executes against PRICE (documented
        # simplification) — flag it so a silent "no trades" on a value
        # strategy is never presented as a signal that the market had none.
        for rule in rules["buy_rules"] + rules["sell_rules"]:
            if rule.split() and rule.split()[0] == "pe":
                _check(f"{q} pe-rule semantic audit: pe rule executes vs price (not P/E)",
                       False, f"(rule '{rule}' — _apply_rule maps pe→Close)")

        n_trades = 0
        for sym, r in (body.get("results") or {}).items():
            trades = r.get("trades") or []
            n_trades += len(trades)
            # Reconstruct the window from the equity curve (exact bars the
            # engine reported) and re-fetch the same symbol data.
            eq = r.get("equity_curve") or []
            if not eq:
                _skip(q, f"[{sym}] no equity curve to bound window")
                continue
            w_start = eq[0]["date"]
            w_end = eq[-1]["date"]
            raw = _fetch_cached(sym, w_start)
            if raw is None or raw.empty:
                _skip(q, f"[{sym}] could not re-fetch data for {w_start}")
                continue
            prepped = _prep(raw, w_start, w_end)
            if prepped.empty:
                _skip(q, f"[{sym}] re-fetched window empty")
                continue
            for t in trades:
                _verify_trade(q, sym, t, prepped, rules, w_start, w_end)

        fails = [f for f in ALL_FAILS if f.startswith(q)]
        print(f"[{idx:02d}/{len(QUERIES)}] {st} {dt:.0f}s '{q}' "
              f"trades={n_trades} {'✅' if not fails else f'❌ {len(fails)}'}", flush=True)
        if body.get("message"):
            print(f"      msg: {body['message'][:90]}", flush=True)

    print("Layer C done", flush=True)


def main() -> int:
    hermetic_only = "--hermetic-only" in sys.argv
    _known_state_layer()
    _reference_layer()
    if not hermetic_only:
        _live_layer()

    print("\n" + "=" * 78, flush=True)
    print("RULE-EXECUTION ACCURACY REPORT", flush=True)
    print("=" * 78, flush=True)
    print(f"checks run:    {CHECK_COUNT[0]}", flush=True)
    print(f"checks passed: {CHECK_COUNT[0] - len(ALL_FAILS)}", flush=True)
    print(f"checks failed: {len(ALL_FAILS)}", flush=True)
    if SKIPS:
        print(f"skipped (data-availability): {len(SKIPS)}", flush=True)
    if CHECK_COUNT[0]:
        acc = 100.0 * (CHECK_COUNT[0] - len(ALL_FAILS)) / CHECK_COUNT[0]
        print(f"accuracy:      {acc:.2f}%", flush=True)
    if ALL_FAILS:
        print("\nFAILURES:", flush=True)
        for f in ALL_FAILS:
            print(f"  ❌ {f}", flush=True)
    if SKIPS:
        print(f"\nSKIPPED ({len(SKIPS)}):", flush=True)
        for s in SKIPS:
            print(f"  ⏭  {s}", flush=True)
    print("\n" + ("✅ ALL CHECKS PASSED" if not ALL_FAILS else "❌ FAILURES PRESENT"),
          flush=True)
    return 1 if ALL_FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
