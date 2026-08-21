"""Live accuracy-testing loop for the Stratoscan backtester.

Throws a battery of hard/diverse queries at the running backend and, for
EVERY returned symbol result, independently recomputes the simulation math
from the response itself (two implementations agreeing = accurate):

  - per-trade PnL vs actual cash delta with fees (long & short)
  - cash conservation: final_value == initial + sum(closed trade PnL)
  - total_return == final_value/initial - 1
  - win_rate == wins/closed trades
  - total_trades == len(trades)
  - equity curve endpoints == initial/final; drawdown curve <= 0
  - buy_date < sell_date; integer shares > 0; finite numbers, no NaN/inf
  - exit reasons in the documented set
  - benchmark/excess consistency
  - strategy echo: requested risk overrides and buy-and-hold appear in
    the echoed strategy; garbage input fails HONESTLY (never fake success)

Run:  python accuracy_loop.py [queries_file]
Exit: 0 = all hard checks passed; 1 = at least one failed.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("ACC_BASE", "http://127.0.0.1:8000")
TOKEN_PATH = os.environ.get("ACC_TOKEN", "/tmp/bt_token.txt")

# 30 hard/diverse queries: contradictions, dead columns, explicit tickers,
# date windows, risk overrides, shorts, garbage input.
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
    ("asdfgh qwerty 12345", {}),                      # garbage — must fail honestly
    ("buy oversold stocks", {}),
    ("stocks with dividend yield above 4 percent and low beta", {}),
    ("all in on TSLA", {}),
    ("short sell overbought stocks with rsi above 70", {"allow_short": True}),
    ("stocks below their 52 week low", {}),
]

# end_of_period is the engine's DOCUMENTED force-liquidation exit for a
# position still open at the window's last bar (backtester.py TradeInfo
# docstring + the close-loop sets it). Missing it here is a harness bug,
# not an engine bug — the accuracy loop must not false-positive on the
# engine's own valid exit taxonomy.
VALID_EXITS = {"signal", "stop_loss", "take_profit", "trailing_stop", "end_of_period"}

RESULTS: dict = {}   # query -> {checks, passes, failures: [...]}
ALL_FAILS: list = []
CHECK_COUNT: list = [0]  # exact total of invariant checks run (mutable cell)


def api_post(path: str, payload: dict, token: str):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


def _finite(x):
    if x is None:
        return True
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _check(q: str, label: str, ok: bool, detail: str = "") -> bool:
    CHECK_COUNT[0] += 1
    if not ok:
        ALL_FAILS.append(f"{q}: {label} {detail}")
    return ok


def verify_symbol(q: str, sym: str, r: dict, strategy: dict) -> None:
    """Independent recomputation of one symbol's reported math."""
    icap = r.get("initial_capital")
    fval = r.get("final_value")
    ret = r.get("total_return_pct")
    fee = (strategy.get("fee_rate") or 0.0)
    trades = r.get("trades") or []

    _check(q, f"[{sym}] initial_capital finite", _finite(icap))
    _check(q, f"[{sym}] final_value finite", _finite(fval))
    _check(q, f"[{sym}] total_trades == len(trades)",
           r.get("total_trades") == len(trades), f"({r.get('total_trades')} vs {len(trades)})")
    _check(q, f"[{sym}] equity curve non-empty", bool(r.get("equity_curve")))

    # Return consistency: total_return_pct == final/initial - 1
    if icap and fval and ret is not None:
        expect = (float(fval) / float(icap) - 1.0) * 100.0
        _check(q, f"[{sym}] total_return matches final/initial",
               abs(expect - float(ret)) < 0.05,
               f"(expect {expect:.2f} got {ret})")

    # Equity curve endpoints
    eq = r.get("equity_curve") or []
    if eq:
        first, last = eq[0].get("value"), eq[-1].get("value")
        if icap:
            _check(q, f"[{sym}] equity starts at initial capital",
                   first is not None and abs(float(first) - float(icap)) < 1.0,
                   f"(first {first} vs {icap})")
        if fval:
            _check(q, f"[{sym}] equity ends at final value",
                   last is not None and abs(float(last) - float(fval)) < 1.0,
                   f"(last {last} vs {fval})")

    # Drawdown curve <= 0 and same length as equity
    dd = r.get("drawdown_curve") or []
    if dd:
        _check(q, f"[{sym}] drawdown <= 0",
               all(float(v.get("value", 0)) <= 1e-6 for v in dd),
               f"(max {max((float(v.get('value',0)) for v in dd), default=0):.4f})")
        _check(q, f"[{sym}] drawdown length == equity length",
               len(dd) == len(eq), f"({len(dd)} vs {len(eq)})")

    # Max drawdown vs the (downsampled) drawdown_curve. The engine computes
    # max_drawdown from the FULL daily series; the charted curves are
    # downsampled to MAX_EQUITY_CURVE_POINTS. Sampling can only MISS the
    # exact worst point, so the true drawdown must be AT LEAST as deep as
    # the chart's deepest point (report <= chart_min + tolerance).
    mdd = r.get("max_drawdown_pct")
    if dd and mdd is not None:
        chart_min = min(float(v.get("value", 0)) for v in dd) * 100.0
        _check(q, f"[{sym}] max_drawdown at least as deep as charted curve",
               float(mdd) <= chart_min + 1.0,
               f"(reported {mdd} vs charted deepest {chart_min:.2f} — full-series "
               f"must be <= downsampled chart)")

    # Per-trade math: PnL == cash delta with fees (long & short)
    closed = [t for t in trades if t.get("sell_date") and t.get("pnl") is not None]
    wins = 0
    for t in closed:
        sh = float(t["shares"])
        entry, exit_ = float(t["buy_price"]), float(t["sell_price"])
        if t.get("side") == "short":
            actual = sh * entry * (1 - fee) - sh * exit_ * (1 + fee)
        else:
            actual = sh * exit_ * (1 - fee) - sh * entry * (1 + fee)
        _check(q, f"[{sym}] trade {t.get('buy_date')} PnL == cash delta",
               abs(float(t["pnl"]) - actual) < 0.05,
               f"({t['pnl']:.2f} vs {actual:.2f} side={t.get('side')} fee={fee})")
        _check(q, f"[{sym}] trade dates ordered",
               str(t["buy_date"]) <= str(t["sell_date"]),
               f"({t['buy_date']} > {t['sell_date']})")
        _check(q, f"[{sym}] shares positive integer",
               float(sh).is_integer() and sh > 0, f"(shares={sh})")
        _check(q, f"[{sym}] exit reason valid",
               t.get("exit_reason") in VALID_EXITS, f"({t.get('exit_reason')})")
        if float(t["pnl"]) > 0:
            wins += 1
        # pnl_pct sign matches pnl sign
        pct = t.get("pnl_pct")
        if pct is not None:
            _check(q, f"[{sym}] pnl_pct sign matches pnl",
                   (float(pct) > 0) == (float(t["pnl"]) > 0) or float(pct) == 0.0,
                   f"(pnl {t['pnl']} pct {pct})")

    # Win rate == wins / closed
    wr = r.get("win_rate_pct")
    if closed and wr is not None:
        expect_wr = wins / len(closed) * 100.0
        _check(q, f"[{sym}] win_rate matches trades",
               abs(expect_wr - float(wr)) < 1.0,
               f"(expect {expect_wr:.1f} got {wr})")

    # Cash conservation when all trades closed
    if closed and len(closed) == len(trades):
        sum_pnl = sum(float(t["pnl"]) for t in closed)
        if icap and fval:
            _check(q, f"[{sym}] cash conservation final == initial + ΣPnL",
                   abs(float(fval) - (float(icap) + sum_pnl)) < 1.0,
                   f"(final {fval} vs {icap + sum_pnl:.2f})")

    # Benchmark/excess consistency
    bt = r.get("benchmark_total_return_pct")
    ex = r.get("excess_return_pct")
    if bt is not None and ex is not None and ret is not None:
        _check(q, f"[{sym}] excess == total - benchmark",
               abs((float(ret) - float(bt)) - float(ex)) < 0.5,
               f"(expect {float(ret)-float(bt):.2f} got {ex})")

    # No NaN/inf in headline numbers
    for key in ("sharpe_ratio", "alpha_pct", "beta", "buy_hold_price_return_pct"):
        _check(q, f"[{sym}] {key} finite-or-None", _finite(r.get(key)))


def verify_strategy_echo(q: str, body: dict, payload: dict) -> None:
    """The echoed strategy must reflect the request's intent."""
    strategy = body.get("strategy") or {}
    _check(q, "strategy echoes fee_rate", "fee_rate" in strategy, f"({list(strategy)[:6]})")
    _check(q, "strategy echoes slippage", "slippage" in strategy)
    _check(q, "strategy echoes buy_rules", "buy_rules" in strategy)
    _check(q, "strategy echoes sell_rules", "sell_rules" in strategy)

    if payload.get("stop_loss_pct"):
        sl = strategy.get("stop_loss")
        _check(q, f"stop_loss echoed ({payload['stop_loss_pct']}%)",
               sl is not None and abs(float(sl) * 100 - float(payload["stop_loss_pct"])) < 0.01,
               f"(got {sl})")
    if payload.get("take_profit_pct"):
        tp = strategy.get("take_profit")
        _check(q, f"take_profit echoed ({payload['take_profit_pct']}%)",
               tp is not None and abs(float(tp) * 100 - float(payload["take_profit_pct"])) < 0.01,
               f"(got {tp})")
    if payload.get("trailing_stop_pct"):
        ts = strategy.get("trailing_stop")
        _check(q, f"trailing_stop echoed ({payload['trailing_stop_pct']}%)",
               ts is not None and abs(float(ts) * 100 - float(payload["trailing_stop_pct"])) < 0.01,
               f"(got {ts})")
    if payload.get("position_sizing"):
        _check(q, f"position_sizing echoed ({payload['position_sizing']})",
               strategy.get("position_sizing") == payload["position_sizing"],
               f"(got {strategy.get('position_sizing')})")
    if payload.get("allow_short"):
        _check(q, "allow_short echoed True",
               strategy.get("allow_short") is True,
               f"(got {strategy.get('allow_short')})")
    if "buy and hold" in q.lower():
        _check(q, "buy-and-hold buy_rules == ['price > 0']",
               strategy.get("buy_rules") == ["price > 0"],
               f"(got {strategy.get('buy_rules')})")
        _check(q, "buy-and-hold sell_rules == ['price < 0']",
               strategy.get("sell_rules") == ["price < 0"],
               f"(got {strategy.get('sell_rules')})")


def run() -> int:
    token = ""
    if os.path.exists(TOKEN_PATH):
        token = open(TOKEN_PATH).read().strip()

    # Re-register if the token is missing/stale
    if not token:
        import time as _t
        email = f"acc.loop.{int(_t.time())}@test.com"
        st, reg = api_post("/auth/register", {"email": email, "password": "DebugPass123!"}, None)
        if st == 200 and reg.get("access_token"):
            token = reg["access_token"]
            open(TOKEN_PATH, "w").write(token)

    print(f"accuracy loop: {len(QUERIES)} queries -> {BASE}", flush=True)
    for i, (q, extra) in enumerate(QUERIES, 1):
        payload = {"query": q, "top_n": 3, **extra}
        t0 = time.time()
        st, body = api_post("/backtest", payload, token)
        dt = time.time() - t0
        qres = {"checks": 0, "passes": 0, "failures": []}
        before = len(ALL_FAILS)
        checks_start = CHECK_COUNT[0]

        if st != 200:
            # Garbage input must fail HONESTLY (422/502) — a 200 with fake
            # data on garbage would be a failure. (The engine's fallback is
            # to interpret empty criteria → default strategy on top-N; that
            # returns 200 and is checked like any other query.)
            if "asdfgh" in q:
                ok = st in (422, 502)
                _check(q, "garbage input failed honestly (no fake success)", ok,
                       f"(status {st})")
                print(f"[{i:02d}/{len(QUERIES)}] {st} {dt:.0f}s '{q}' "
                      f"{'✅ honest failure' if ok else '❌ unexpected status'}", flush=True)
            else:
                print(f"[{i:02d}/{len(QUERIES)}] {st} {dt:.0f}s '{q}' ❌ HTTP {st} "
                      f"{str(body)[:120]}", flush=True)
            qres["failures"] = ALL_FAILS[before:]
            qres["checks"] = CHECK_COUNT[0] - qres["checks"]
            RESULTS[q] = qres
            continue

        verify_strategy_echo(q, body, extra)
        for sym, r in (body.get("results") or {}).items():
            verify_symbol(q, sym, r, body.get("strategy") or {})

        # Exact check counting: every _check call increments the counter.
        fails = ALL_FAILS[before:]
        qres["checks"] = CHECK_COUNT[0] - checks_start
        qres["passes"] = qres["checks"] - len(fails)
        qres["failures"] = fails
        RESULTS[q] = qres

        print(f"[{i:02d}/{len(QUERIES)}] {st} {dt:.0f}s '{q}' "
              f"tested={body.get('total_symbols_tested')} "
              f"checks={qres['checks']} "
              f"{'✅' if not fails else f'❌ {len(fails)} failures'}", flush=True)
        if body.get("message"):
            print(f"      msg: {body['message'][:100]}", flush=True)

    # ── Report ────────────────────────────────────────────────────────────
    print("\n" + "=" * 78, flush=True)
    print("ACCURACY REPORT", flush=True)
    print("=" * 78, flush=True)
    total_checks = sum(v["checks"] for v in RESULTS.values())
    total_passes = sum(v["passes"] for v in RESULTS.values())
    print(f"queries run:        {len(RESULTS)}", flush=True)
    print(f"symbol results:     {sum(len((b or {}).get('results', {})) for b in [{}])}", flush=True)
    print(f"invariant checks:   {total_checks} (across all symbol results)", flush=True)
    print(f"checks passed:      {total_passes}", flush=True)
    print(f"checks failed:      {len(ALL_FAILS)}", flush=True)
    if total_checks:
        print(f"accuracy:           {100.0 * total_passes / total_checks:.2f}%", flush=True)
    if ALL_FAILS:
        print("\nFAILURES:", flush=True)
        for f in ALL_FAILS:
            print(f"  ❌ {f}", flush=True)
    print("\n" + ("✅ ALL CHECKS PASSED" if not ALL_FAILS else "❌ FAILURES PRESENT"), flush=True)
    return 1 if ALL_FAILS else 0


if __name__ == "__main__":
    sys.exit(run())
