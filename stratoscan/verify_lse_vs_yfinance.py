#!/usr/bin/env python3
"""Prove the LSE history source agrees with yfinance before trusting it.

``src/lse_source.py`` rebuilds a split/dividend-adjusted series from LSE's
RAW candles plus its separate corporate-action datasets. The math is pinned
against a hand-computed example in ``tests/test_lse_source.py``, but that
proves the arithmetic, not that LSE's real payloads carry the fields and
conventions the code assumes. Only a live run can prove that.

This probe fetches the same symbols from both sources and compares the
adjusted closes they produce. It also PRINTS THE RAW FIELD NAMES LSE
returns — those were never published, so the parser accepts several
plausible spellings; this is how you find out which ones are real.

    export LSE_API_KEY=...
    python verify_lse_vs_yfinance.py
    python verify_lse_vs_yfinance.py --symbols AAPL,MSFT --years 3

Exit codes: 0 = sources agree · 1 = they disagree · 2 = usage/env error.

Note: needs network to BOTH LSE and Yahoo, so it cannot run in a sandbox and
is not part of the hermetic suite. Run it on a machine with real egress.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Adjusted closes from two independent vendors will never match to the cent:
# they differ in dividend rounding, ex-date handling and split precision.
# 0.5% is tight enough to catch a wrong adjustment (a missed 2-for-1 split is
# a 50% error; a missed dividend is typically 0.5-3%) and loose enough not to
# fail on honest vendor disagreement.
TOLERANCE_PCT = 0.5
# A source that only agrees on a handful of bars is not agreeing.
MIN_OVERLAP_BARS = 100
# Fraction of overlapping bars that must be inside tolerance.
MIN_PASS_RATE = 0.98

DEFAULT_SYMBOLS = ["AAPL", "MSFT", "NVDA", "KO", "JNJ"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS),
                    help="comma-separated tickers (default: 5 liquid large caps, "
                         "two of them dividend payers)")
    ap.add_argument("--years", type=int, default=5)
    args = ap.parse_args()

    from src.config import settings
    if not getattr(settings, "lse_api_key", ""):
        print("❌ LSE_API_KEY is not set — nothing to verify.", file=sys.stderr)
        print("   Get a free key at https://londonstrategicedge.com/data", file=sys.stderr)
        return 2

    import numpy as np
    from src import lse_source
    from src.backtester import fetch_historical_data

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    print(f"Comparing {len(symbols)} symbols over {args.years}y "
          f"(tolerance {TOLERANCE_PCT}%, min pass rate {MIN_PASS_RATE:.0%})\n")

    # ── report the real payload shape once, since it was never published ──
    try:
        probe = symbols[0]
        raw_candles = lse_source._get("/candles", [
            ("symbol", probe), ("timeframe", "1d"), ("order", "desc"), ("limit", 1)])
        raw_divs = lse_source._get("/dividends", [
            ("symbol", probe), ("order", "desc"), ("limit", 1)])
        raw_splits = lse_source._get("/stock_splits", [
            ("symbol", probe), ("order", "desc"), ("limit", 1)])
        print("LSE payload shapes (parser guesses at these — confirm them):")
        print(f"  candles[0]   keys: {sorted(raw_candles[0]) if raw_candles else 'EMPTY'}")
        print(f"  dividends[0] keys: {sorted(raw_divs[0]) if raw_divs else 'EMPTY'}")
        print(f"  splits[0]    keys: {sorted(raw_splits[0]) if raw_splits else 'EMPTY'}")
        if raw_divs:
            print(f"  dividends[0] row : {raw_divs[0]}")
        if raw_splits:
            print(f"  splits[0]    row : {raw_splits[0]}")
        print()
    except Exception as exc:
        print(f"⚠️  could not probe payload shape: {exc}\n")

    failures: list[str] = []
    for sym in symbols:
        lse_df = lse_source.fetch_historical_data_lse(sym, years=args.years)
        yf_df = fetch_historical_data(sym, years=args.years)

        if lse_df is None or lse_df.empty:
            failures.append(f"{sym}: LSE returned no data")
            print(f"❌ {sym:6s} LSE returned no data")
            continue
        if yf_df is None or yf_df.empty:
            print(f"⚠️  {sym:6s} yfinance returned no data — skipped (not an LSE failure)")
            continue

        a = lse_df["Adj Close"].copy()
        b = yf_df["Adj Close"].copy()
        a.index = [str(i)[:10] for i in a.index]
        b.index = [str(i)[:10] for i in b.index]
        common = sorted(set(a.index) & set(b.index))

        if len(common) < MIN_OVERLAP_BARS:
            failures.append(f"{sym}: only {len(common)} overlapping bars")
            print(f"❌ {sym:6s} only {len(common)} overlapping bars "
                  f"(need {MIN_OVERLAP_BARS})")
            continue

        av = np.array([float(a.loc[d]) for d in common])
        bv = np.array([float(b.loc[d]) for d in common])
        with np.errstate(divide="ignore", invalid="ignore"):
            diff_pct = np.abs(av - bv) / np.where(bv != 0, np.abs(bv), np.nan) * 100.0
        diff_pct = diff_pct[np.isfinite(diff_pct)]

        within = float((diff_pct <= TOLERANCE_PCT).mean())
        worst = float(diff_pct.max()) if diff_pct.size else float("inf")
        median = float(np.median(diff_pct)) if diff_pct.size else float("inf")

        ok = within >= MIN_PASS_RATE
        mark = "✅" if ok else "❌"
        print(f"{mark} {sym:6s} {len(common):5d} bars · "
              f"{within:6.2%} within {TOLERANCE_PCT}% · "
              f"median {median:.3f}% · worst {worst:.2f}%")
        if not ok:
            failures.append(
                f"{sym}: only {within:.1%} of bars within tolerance (worst {worst:.2f}%)")
            # A worst-case near 50% is the signature of a missed split.
            if worst > 40:
                print("   ↳ worst error near 50% — likely a MISSED SPLIT. "
                      "Check the split ratio field name against the shapes above.")

    print()
    if failures:
        print(f"❌ FAILED — {len(failures)} symbol(s) disagree:")
        for f in failures:
            print(f"   · {f}")
        print("\nDo NOT backfill from LSE until this passes: a wrong adjustment "
              "makes every backtest silently and plausibly wrong.")
        return 1

    print("✅ PASSED — LSE's rebuilt adjusted series agrees with yfinance.")
    print("   Safe to use as a backfill source for the backtest database.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
