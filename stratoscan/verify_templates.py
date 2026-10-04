"""Live probe: every strategy template against real data (Day 8).

Run: python verify_templates.py            (needs DATABASE_URL + network)
     python verify_templates.py --symbols 2 (backtest the top 2 per template)

The hermetic suite (tests/test_strategy_templates.py) proves each template
parses for free, trades its pinned rules and does not churn on synthetic
markets. What it cannot prove without real data, this does:

  1. The screen is satisfiable TODAY on the live universe without the
     zero-match rescue relaxing anything (a template that only works after
     relaxation is advertising a screen it does not run), and its columns
     are populated for the names it matches.
  2. Its top matches backtest on real 10-year history: at least one trade
     across them, a median hold of at least a week, no same-bar exits.
  3. The doubt from the build notes, closed with actual prices: the
     dip-in-an-uptrend pairs (RSI < 35 with price > SMA-50, and RSI < 40
     with price > SMA-200) are flagged unsatisfiable on a pure sinusoid —
     here they are counted on real large caps, where they must occur.

Not in run_probes.PROBES: the seeded probes DB is a small fixture universe,
so (1) would fail there by construction. Run it against a full database.

Exit 0 = all checks passed.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.backtester import (  # noqa: E402
    _calculate_indicators,
    _translate_to_rules,
    fetch_historical_data,
    run_backtest,
)
from src.interpreter import interpret_query  # noqa: E402
from src.screener import StockScreener  # noqa: E402
from src.strategy_templates import TEMPLATES  # noqa: E402

LARGE_CAPS = ["AAPL", "MSFT", "JNJ", "JPM", "XOM", "PG", "KO", "WMT", "UNH", "HD"]

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {label}{(' — ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=int, default=3, help="top matches to backtest per template")
    args = ap.parse_args()

    screener = StockScreener()
    for t in TEMPLATES:
        print(f"\n▶ {t.title}  ({t.id})")
        iq = interpret_query(t.query)
        check("parsed on the free path", iq.source == "deterministic", iq.source or "")
        report = screener.screen(iq, limit=max(args.symbols, 5))
        relaxed = report.data_quality.get("relaxed_from") or []
        check(f"screen matches today ({report.total_matches} stocks)", report.total_matches > 0)
        check("no filter relaxed to get there", not relaxed, ", ".join(relaxed))

        symbols = [r.symbol for r in report.results[: args.symbols]]
        rules = _translate_to_rules(iq)
        trades = 0
        holds: list[int] = []
        for sym in symbols:
            df = fetch_historical_data(sym)
            if df is None or df.empty:
                print(f"    · {sym}: no history")
                continue
            res = run_backtest(sym, rules, str(df.index[0].date()), str(df.index[-1].date()),
                               df=df, compute_robustness=False)
            trades += res.total_trades
            holds += [int(np.busday_count(tr.buy_date, tr.sell_date))
                      for tr in res.trades if tr.sell_date]
            print(f"    · {sym}: {res.total_trades} trades, {res.total_return * 100:+.1f}%")
        check(f"real history produces trades ({trades} across {len(symbols)})", trades > 0)
        if holds:
            med = statistics.median(holds)
            check(f"median hold {med:.0f} trading days (≥ 5)", med >= 5)
            check("no exit on the entry bar", min(holds) >= 1)

    print("\n▶ Dip-in-an-uptrend pairs on real prices")
    total_a = total_b = 0
    for sym in LARGE_CAPS:
        df = fetch_historical_data(sym)
        if df is None or df.empty:
            print(f"    · {sym}: no history")
            continue
        ind = _calculate_indicators(df)
        a = int(((ind["RSI"] < 35) & (ind["Close"] > ind["SMA_50"])).sum())
        b = int(((ind["RSI"] < 40) & (ind["Close"] > ind["SMA_200"])).sum())
        total_a, total_b = total_a + a, total_b + b
        print(f"    · {sym}: RSI<35 & >SMA50 on {a} days; RSI<40 & >SMA200 on {b} days")
    check("RSI<35 with price>SMA50 occurs on real large caps", total_a > 0, f"{total_a} days")
    check("RSI<40 with price>SMA200 occurs on real large caps", total_b > 0, f"{total_b} days")

    print(f"\n{'ALL TEMPLATE PROBES PASSED' if not failures else f'{len(failures)} FAILED'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
