#!/usr/bin/env python3
"""verify_data_accuracy.py — CROSS-VERIFY our stored market data vs yfinance (P1-5).

Run: python verify_data_accuracy.py [--report out.md] [--tolerance-price 5.0 ...]

The "are we doing BS" test: Stratoscan serves data that came from Finnhub
(price/RSI/SMA/52w) and backtests run off yfinance history. If the two
sources disagree beyond a reasonable tolerance, screens and backtests are
built on fiction. This probe samples 25 liquid symbols, reads the values
Stratoscan actually STORES (from the configured DATABASE_URL — staging/
probes DB under the release gate), downloads yfinance daily history, and
recomputes every metric INDEPENDENTLY from yfinance on the SAME trading day
as our snapshot (``updated_at``). Same as-of basis = a true accuracy
comparison, not a freshness one.

Metrics + tolerances (documented, tunable via CLI):
  - current_price  vs yfinance Close  : relative diff  <= 5%
  - sma_50         vs 50d mean(Close) : relative diff  <= 5%
  - high_52w       vs 252d max(High)  : relative diff  <= 5%
  - rsi_14         vs Wilder RSI-14   : ABSOLUTE diff  <= 10 points
    (RSI is a bounded 0-100 oscillator — a "percentage" diff is
    meaningless near 0/100, so tolerance is in points.)

Outlier policy: any metric outside tolerance is FLAGGED in the report and
counts toward the probe result. The probe FAILS if >2 symbols have any
out-of-tolerance metric (small methodology noise is expected: Finnhub
technicals vs adjusted closes differ slightly) OR if fewer than 20 of the
25 symbols could be genuinely compared (a symbol counts as compared only
when at least one metric had BOTH sides available).

Exit 0 = data is accurate within tolerance. 1 = outliers/coverage failure.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd  # noqa: E402

# ── The 25-symbol sample (task spec) ────────────────────────────────────────
SYMBOLS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "NVDA",
    "JPM", "V", "PG", "JNJ", "UNH", "HD", "BAC", "MA",
    "DIS", "NFLX", "COST", "PEP", "TMO", "AVGO", "WMT",
    "XOM", "CVX", "ABT",
]

# Tolerance defaults (relative % except RSI which is absolute points).
TOL = {
    "current_price": 5.0,
    "sma_50": 5.0,
    "high_52w": 5.0,
    "rsi_14": 10.0,  # points, not %
}

# A symbol whose metrics can't be computed from yfinance (bad ticker, delist,
# yahoo block) is recorded as unavailable and skipped, not a failure.
MAX_OUTLIER_SYMBOLS = 2  # >2 symbols with any out-of-tolerance metric -> fail
MIN_COMPARED = 20        # fewer genuinely-compared symbols -> coverage fail


# ── Independent metric math (documented formulas, written from the docs) ────
def wilder_rsi(series: pd.Series, period: int = 14) -> float:
    """Wilder's RSI from a Close series (the standard yfinance-equivalent).

    Returns NaN when the window is flat (0/0) — callers treat NaN as
    incomparable, never as an outlier.
    """
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi = 100.0 - 100.0 / (1.0 + rs)
    return float(rsi.iloc[-1])


def _as_of(hist: pd.DataFrame, date: _dt.date) -> pd.DataFrame:
    """Slice history through the snapshot date, inclusive (tz-safe)."""
    cutoff = pd.Timestamp(date)
    return hist[hist.index <= cutoff]


def compute_metrics(hist: pd.DataFrame, as_of: _dt.date) -> dict | None:
    """Recompute all four metrics from yfinance bars AS OF the snapshot date.

    Returns None if there aren't enough bars by that date (fresh ticker).
    """
    d = _as_of(hist, as_of)
    if len(d) < 60:  # need 252 for 52w, 50 for SMA, 15 for RSI — be generous
        return None
    close = d["Close"].astype(float)
    return {
        "current_price": float(close.iloc[-1]),
        "sma_50": float(close.tail(50).mean()),
        "high_52w": float(d["High"].tail(252).max()),
        "rsi_14": wilder_rsi(close),
    }


# ── DB read (our stored values) ─────────────────────────────────────────────
def read_our_data() -> dict:
    """Read stored current_price/rsi_14/sma_50/high_52w/updated_at per symbol.

    Uses the configured DATABASE_URL (run_probes forces it to the probes DB,
    so this never touches production).
    """
    from src.config import settings

    if not settings.has_database_url:
        print("❌ DATABASE_URL not configured — cannot read stored values.")
        sys.exit(2)

    import psycopg2

    conn = psycopg2.connect(settings.database_url, connect_timeout=15)
    out: dict[str, dict] = {}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT symbol, current_price, rsi_14, sma_50, high_52w, updated_at "
                "FROM stock_master WHERE symbol = ANY(%s)",
                (SYMBOLS,),
            )
            for sym, price, rsi, sma, hi52, upd in cur.fetchall():
                out[sym] = {
                    "current_price": price,
                    "rsi_14": rsi,
                    "sma_50": sma,
                    "high_52w": hi52,
                    "updated_at": upd,
                }
    finally:
        conn.close()
    return out


# ── Diff + report ───────────────────────────────────────────────────────────
def rel_diff(a: float, b: float) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return abs(a - b) / abs(b) * 100.0


def _compare(metric: str, ours, theirs) -> tuple[bool, float | None]:
    """One comparison, computed once. Returns (is_outlier, diff).

    NaN on either side = incomparable (not an outlier) — a flat RSI window
    or a missing value must never fail the probe.
    """
    if ours is None or theirs is None:
        return False, None
    try:
        if math.isnan(float(ours)) or math.isnan(float(theirs)):
            return False, None
    except (TypeError, ValueError):
        return False, None
    if metric == "rsi_14":
        return abs(float(ours) - float(theirs)) > TOL["rsi_14"], abs(float(ours) - float(theirs))
    d = rel_diff(float(ours), float(theirs))
    if d is None:
        return False, None
    return d > TOL[metric], d


def _fmt(v) -> str:
    return "N/A" if v is None else f"{v:.2f}"


METRICS = ("current_price", "rsi_14", "sma_50", "high_52w")


def main() -> int:
    ap = argparse.ArgumentParser(description="Cross-verify stored data vs yfinance (P1-5)")
    ap.add_argument("--report", metavar="PATH", help="Write a markdown diff report")
    ap.add_argument("--tolerance-price", type=float, default=TOL["current_price"])
    ap.add_argument("--tolerance-sma", type=float, default=TOL["sma_50"])
    ap.add_argument("--tolerance-high52w", type=float, default=TOL["high_52w"])
    ap.add_argument("--tolerance-rsi", type=float, default=TOL["rsi_14"])
    args = ap.parse_args()

    TOL["current_price"] = args.tolerance_price
    TOL["sma_50"] = args.tolerance_sma
    TOL["high_52w"] = args.tolerance_high52w
    TOL["rsi_14"] = args.tolerance_rsi

    print("=" * 72)
    print("DATA ACCURACY — 25 symbols cross-verified vs yfinance")
    print(f"tolerances: price <= {TOL['current_price']}%  sma50 <= {TOL['sma_50']}%  "
          f"high52w <= {TOL['high_52w']}%  rsi <= {TOL['rsi_14']} pts")
    print("=" * 72)

    print("\n📥 Reading stored values from DB...")
    ours = read_our_data()
    found = len(ours)
    missing = [s for s in SYMBOLS if s not in ours]
    print(f"   {found}/{len(SYMBOLS)} symbols in DB"
          + (f"  (missing: {missing})" if missing else ""))

    print("\n📥 Downloading yfinance history (1y daily)...")
    import yfinance as yf

    try:
        raw = yf.download(SYMBOLS, period="1y", interval="1d", group_by="ticker",
                          progress=False, threads=True, auto_adjust=False)
    except Exception as e:  # noqa: BLE001
        print(f"❌ yfinance download failed: {e}")
        return 1

    rows = []
    outliers: list[tuple[str, list]] = []
    unavailable = []
    for sym in SYMBOLS:
        if sym not in ours:
            unavailable.append(sym)
            continue
        o = ours[sym]
        try:
            h = raw[sym]
            h = h.dropna(subset=["Close"])
            if h.empty:
                raise KeyError(sym)
            # Normalize a (possibly tz-aware) yfinance index so the as-of
            # slice comparison is version-independent.
            h = h.copy()
            if getattr(h.index, "tz", None) is not None:
                h.index = h.index.tz_localize(None)
        except Exception:  # noqa: BLE001
            unavailable.append(sym)
            continue

        # Same as-of basis: our snapshot date (or the newest bar if unset).
        upd = o.get("updated_at")
        if isinstance(upd, _dt.datetime):
            as_of = upd.date()
        else:
            as_of = h.index[-1].date()
        as_of = min(as_of, h.index[-1].date())  # never in the future
        theirs = compute_metrics(h, as_of)
        if theirs is None:
            unavailable.append(sym)
            continue

        # One comparison per metric; reuse for the flag AND the row.
        flags = {}
        for m in METRICS:
            is_out, diff = _compare(m, o[m], theirs[m])
            flags[m] = (is_out, diff)

        sym_outliers = [(m, o[m], theirs[m], flags[m][1])
                        for m in METRICS if flags[m][0]]
        # Genuinely compared = at least one metric had both sides non-None.
        compared_any = any(
            o[m] is not None and theirs[m] is not None
            and not (isinstance(o[m], float) and math.isnan(o[m]))
            and not (isinstance(theirs[m], float) and math.isnan(theirs[m]))
            for m in METRICS
        )
        if not compared_any:
            unavailable.append(sym)
            continue

        rows.append({
            "symbol": sym,
            "as_of": str(as_of),
            **{f"our_{m}": o[m] for m in METRICS},
            **{f"yf_{m}": theirs[m] for m in METRICS},
            **{f"diff_{m}": flags[m][1] for m in METRICS},
            "outliers": [m for m, *_ in sym_outliers],
        })
        if sym_outliers:
            outliers.append((sym, sym_outliers))

    # ── Console report ─────────────────────────────────────────────────
    print("\n" + "-" * 72)
    hdr = (f"{'SYM':<6}{'as-of':<12}{'our_px':>10}{'yf_px':>10}{'d%':>7} | "
           f"{'our_rsi':>8}{'yf_rsi':>8}{'dpts':>6} | {'our_sma':>9}{'yf_sma':>9}{'d%':>6} | "
           f"{'our_hi':>9}{'yf_hi':>9}{'d%':>6}  flags")
    print(hdr)
    print("-" * 72)
    for r in rows:
        f = ",".join(r["outliers"]) if r["outliers"] else ""
        print(
            f"{r['symbol']:<6}{r['as_of']:<12}"
            f"{_fmt(r['our_current_price']):>10}{_fmt(r['yf_current_price']):>10}"
            f"{_fmt(r['diff_current_price']):>7} | "
            f"{_fmt(r['our_rsi_14']):>8}{_fmt(r['yf_rsi_14']):>8}{_fmt(r['diff_rsi_14']):>6} | "
            f"{_fmt(r['our_sma_50']):>9}{_fmt(r['yf_sma_50']):>9}{_fmt(r['diff_sma_50']):>6} | "
            f"{_fmt(r['our_high_52w']):>9}{_fmt(r['yf_high_52w']):>9}{_fmt(r['diff_high_52w']):>6}"
            f"  {'❌' + f if f else ''}"
        )
    print("-" * 72)

    compared = len(rows)
    if unavailable:
        print(f"\n⚠️  Unavailable (no yfinance / not in DB / no comparable metrics): {unavailable}")

    flagged = sum(1 for r in rows if r["outliers"])
    print(f"\n📊 Compared: {compared}/{len(SYMBOLS)} symbols | "
          f"symbols with out-of-tolerance metrics: {flagged}")

    # ── Markdown report ────────────────────────────────────────────────
    if args.report:
        lines = [
            "# Stratoscan — Data Accuracy Report (Finnhub-stored vs yfinance)",
            "",
            f"- **Generated:** {_dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            f"- **Sample:** {len(SYMBOLS)} liquid symbols, compared as-of each snapshot's `updated_at`",
            f"- **Tolerances:** price ≤ {TOL['current_price']}% | sma_50 ≤ {TOL['sma_50']}% | "
            f"high_52w ≤ {TOL['high_52w']}% | rsi_14 ≤ {TOL['rsi_14']} pts",
            f"- **Result:** {'✅ PASS' if flagged <= MAX_OUTLIER_SYMBOLS and compared >= MIN_COMPARED else '❌ FAIL'}",
            "",
            "| Symbol | as-of | our px | yf px | px d% | our rsi | yf rsi | rsi dpts | our sma | yf sma | sma d% | our 52w | yf 52w | 52w d% | flags |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for r in rows:
            flags_s = ", ".join(r["outliers"]) if r["outliers"] else ""
            lines.append(
                f"| {r['symbol']} | {r['as_of']} | {_fmt(r['our_current_price'])} | "
                f"{_fmt(r['yf_current_price'])} | {_fmt(r['diff_current_price'])} | "
                f"{_fmt(r['our_rsi_14'])} | {_fmt(r['yf_rsi_14'])} | {_fmt(r['diff_rsi_14'])} | "
                f"{_fmt(r['our_sma_50'])} | {_fmt(r['yf_sma_50'])} | {_fmt(r['diff_sma_50'])} | "
                f"{_fmt(r['our_high_52w'])} | {_fmt(r['yf_high_52w'])} | {_fmt(r['diff_high_52w'])} | "
                f"{flags_s} |"
            )
        if outliers:
            lines += ["", "## Out-of-tolerance details", ""]
            for sym, out in outliers:
                lines += [f"### {sym}", ""]
                for metric, o, t, d in out:
                    unit = "pts" if metric == "rsi_14" else "%"
                    lines.append(
                        f"- `{metric}`: ours={_fmt(o)}, yfinance={_fmt(t)}, diff={_fmt(d)}{unit} "
                        f"(tolerance {TOL[metric]}{unit})"
                    )
                lines.append("")
        with open(args.report, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"📄 Report written to {args.report}")

    # ── Verdict ────────────────────────────────────────────────────────
    fail_reasons = []
    if flagged > MAX_OUTLIER_SYMBOLS:
        fail_reasons.append(f"{flagged} symbols exceeded tolerance (> {MAX_OUTLIER_SYMBOLS})")
    if compared < MIN_COMPARED:
        fail_reasons.append(f"only {compared}/{len(SYMBOLS)} compared (< {MIN_COMPARED})")
    print("\n" + "=" * 72)
    if not fail_reasons:
        print(f"✅ DATA ACCURACY PROBE PASSED — {compared} symbols within tolerance "
              f"(yfinance is the backtest source; stored data matches).")
        return 0
    for r in fail_reasons:
        print(f"❌ {r}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
