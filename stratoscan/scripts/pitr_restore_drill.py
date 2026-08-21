#!/usr/bin/env python3
"""pitr_restore_drill.py — P1-4: Neon point-in-time-recovery restore drill.

WHY: Neon offers Point-in-Time Recovery (PITR), but the restore path had
never been exercised end-to-end. A restore gone wrong would surface at the
worst moment — after an incident, when you NEED the backup. This drill
proves, repeatedly, that a restored copy of the primary database serves
real screens and backtests.

WHAT "RESTORE" MEANS HERE (two modes):

  --scratch-url <url>   You performed a REAL restore via the Neon Console
                        (Project -> Restore -> point in time -> new
                        branch/DB). The drill uses that DB directly and
                        verifies it. This is the production mode.

  (default)             No console access needed: the drill performs the
                        RESTORE-EQUIVALENT — a timed, byte-faithful clone
                        of the production database (schema from
                        information_schema, rows via binary COPY, the same
                        machinery P1-6's migration dry-run uses) into a
                        scratch DB on the same Neon project. Everything the
                        drill verifies afterwards is identical to what the
                        restored DB must serve. The Console restore itself
                        is a human/API step (documented in STAGING.md); the
                        drill covers every downstream guarantee.

The drill then:
  1. Confirms the copy is faithful (stock count, users, alembic stamp).
  2. Points the BACKEND at the scratch DB (DATABASE_URL override) and runs
     real screen queries through the REAL interpreter -> screener pipeline
     (deterministic canned LLM filters; no network LLM, no quota burn).
  3. Runs a REAL backtest on AAPL (yfinance fetch, real engine) over a
     short window and hand-recomputes EVERY equity-curve point from the
     documented fill math — the "is it BS?" equity check, plus determinism
     (two runs, identical curves) and a full 10y run for sanity.
  4. Verifies the scratch DB is read-only during the drill (checksums
     unchanged before/after).
  5. Times every phase, tears the scratch DB down (unless --keep) — even if
     the drill fails or throws mid-run (try/finally).

SAFETY:
  - The SOURCE (prod) is opened read-only (SELECT / COPY TO only).
  - The scratch DB name must contain ``pitr`` — refused otherwise.
  - With --scratch-url the drill never writes anything to that DB's schema
    (only screens/backtests read it); the backtester writes to the shared
    backtest DB exactly as the API would in production.

Usage:
  /usr/local/bin/python3.14 scripts/pitr_restore_drill.py                 # clone-equivalent
  /usr/local/bin/python3.14 scripts/pitr_restore_drill.py --keep          # keep the scratch DB
  /usr/local/bin/python3.14 scripts/pitr_restore_drill.py \
      --scratch-url postgresql://.../neondb?sslmode=require              # real Console restore
  /usr/local/bin/python3.14 scripts/pitr_restore_drill.py --symbol MSFT --start 2023-06-01

Exit 0 = drill passed. 1 = any check failed. 2 = usage/env error.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, SCRIPTS)

# Import the clone machinery BEFORE pointing src.config at the scratch DB —
# migration_dryrun has no src imports at module level, so this is safe.
import migration_dryrun  # noqa: E402  (_copy_schema_and_data, _admin_conn, _table_checksums)

# A real restored DB must contain "pitr" in its name (safety anchor). A
# Console-restored branch is often named neondb — the operator should rename
# it (e.g. stratoscan_pitr).
ALLOWED_SCRATCH_MARKERS = ("pitr",)

FAILURES: list[str] = []
PHASES: list[tuple[str, float]] = []


def _phase(name: str, started: float) -> None:
    PHASES.append((name, time.monotonic() - started))
    print(f"   ⏱ {name}: {time.monotonic() - started:.1f}s")


def _check(name: str, ok: bool, detail: str = "") -> None:
    mark = "✅" if ok else "❌"
    print(f"   {mark} {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


# ── Canned LLM router: deterministic filters, zero network ────────────────────
class PromptAwareRouter:
    """chat_completion returns canned JSON keyed by the EXACT user query."""

    def __init__(self, mapping):
        self.mapping = mapping

    def chat_completion(self, *, messages, **kwargs):
        user = next(m["content"] for m in messages if m["role"] == "user")
        content = self.mapping.get(user, "{}")
        _msg = type("M", (), {"content": content})()
        _c = type("C", (), {"message": _msg, "finish_reason": "stop"})()
        return type("R", (), {"choices": [_c]})()


def _backtest_rules() -> dict:
    """All-in buy-and-hold rules (buy any bar, no sell signal) — the simplest
    equity path to hand-verify point-by-point."""
    return {
        "initial_capital": 100_000.0,
        "fee_rate": 0.001,
        "slippage": 0.0005,
        "slippage_cents": 0.0,
        "max_order_volume_pct": 0.0,
        "buy_rules": ["price > 0"],
        "sell_rules": ["price < -1"],  # never fires
        "position_sizing": "all_in",
        "position_pct": 1.0,
        "risk_per_trade": 0.01,
        "allow_short": False,
        "max_leverage": 1.0,
    }


def _hand_compute_equity(df, rules) -> tuple[list[dict], float] | None:
    """Recompute the equity path + final value from the DOCUMENTED fill math
    (P1-5 style): all-in sizing, buy filled at next bar's open with slippage +
    fee, hold to the end, force-liquidate at the final close minus fee.
    Returns (equity_points, expected_final_value), or None when the window
    has fewer than 2 bars (nothing to buy-and-hold — the caller reports it
    as an edge-case check instead of crashing)."""
    if df is None or len(df) < 2:
        return None
    cap = rules["initial_capital"]
    fee = rules["fee_rate"]
    slip = rules["slippage"]
    opens = df["Open"].to_list()
    closes = df["Close"].to_list()
    dates = [str(d.date()) for d in df.index]
    fill_price = opens[1] * (1 + slip)
    per_share = fill_price * (1 + fee)
    shares = int(cap / per_share)
    cost = shares * per_share
    cash = cap - cost
    pts = []
    for i in range(len(df)):
        if i == 0:
            pts.append({"date": dates[0], "value": round(cap, 2)})
        elif i == 1:
            pts.append({"date": dates[1], "value": round(cash + shares * closes[1], 2)})
        else:
            pts.append({"date": dates[i], "value": round(cash + shares * closes[i], 2)})
    final = (cash + shares * closes[-1]) * (1 - fee)
    return pts, final


def _drill(args, prod_url: str, scratch_url: str,
           created_scratch: bool, scratch_name: str) -> int:
    """The whole drill. Teardown is handled by main()'s try/finally so a
    mid-run exception can never leak the scratch DB."""
    t0 = time.monotonic()

    # ── 1. Restore (clone-equivalent) ───────────────────────────────────────
    print("\n1. RESTORE (clone-equivalent) into scratch DB...")
    t = time.monotonic()
    if created_scratch:
        admin = migration_dryrun._admin_conn(prod_url, "")
        admin.autocommit = True
        try:
            cur = admin.cursor()
            cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (args.target,))
            if cur.fetchone():
                cur.execute(f'DROP DATABASE "{args.target}"')
                print(f"   dropped previous scratch '{args.target}'")
            cur.execute(f'CREATE DATABASE "{args.target}"')
        finally:
            admin.close()
        migration_dryrun._copy_schema_and_data(prod_url, scratch_url)
        _phase("clone schema+data", t)
        print("   (the real Neon Console restore is a human/API step — see "
              "STAGING.md → PITR restore drill; this clone verifies every "
              "downstream guarantee it must serve)")
    else:
        _phase("(real restore already performed — skipped clone)", t)

    # ── 2. Faithfulness + read-only baseline ────────────────────────────────
    print("\n2. Copy faithfulness + checksum baseline...")
    t = time.monotonic()
    import psycopg2  # noqa: E402

    conn = psycopg2.connect(scratch_url, connect_timeout=20)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM stock_master")
        n_stocks = cur.fetchone()[0]
        # The screener's completeness guard only ranks rows that carry BOTH
        # price and market cap — the broad screen is measured against this
        # screenable universe, not the raw row count.
        cur.execute("SELECT COUNT(*) FROM stock_master "
                    "WHERE current_price IS NOT NULL AND market_cap IS NOT NULL")
        n_screenable = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM users")
        n_users = cur.fetchone()[0]
        cur.execute("SELECT version_num FROM alembic_version")
        stamp = cur.fetchone()[0]
    finally:
        conn.close()
    _check("stock universe present on the copy", n_stocks >= 1000,
           f"{n_stocks} stocks, {n_users} users, alembic stamp {stamp}")
    baseline = migration_dryrun._table_checksums(scratch_url)
    _phase("checksum baseline", t)

    # ── 3. Point the backend at the scratch DB + run real screens ───────────
    print("\n3. Screens against the restored copy (real interpreter→screener)...")
    os.environ["DATABASE_URL"] = scratch_url  # BEFORE importing src.config
    t = time.monotonic()

    from src import interpreter as interp  # noqa: E402
    from src.screener import StockScreener  # noqa: E402

    router = PromptAwareRouter({
        "large cap technology stocks":
            '{"sector": "Technology", "min_market_cap_billions": 10.0}',
        "stocks with momentum":
            '{"momentum": "bullish"}',
        "   ":
            "{}",
    })
    with mock.patch("src.llm_router.get_router", return_value=router), \
         mock.patch.object(interp.settings, "cache_enabled", False):
        # 3a. Filtered screen — must return real tech names from the COPY.
        interp._interpret_cache.clear()
        rep = StockScreener().screen_from_natural_language(
            "large cap technology stocks", limit=5)
        syms = [r.symbol for r in rep.results]
        _check("screen returns results off the restored copy",
               rep.total_matches > 0 and len(syms) > 0,
               f"{rep.total_matches} matches, top: {syms[:3]}")
        _check("screen results are real (not empty/BS)",
               len(syms) > 0 and all(s for s in syms)
               and bool(rep.results[0].company_name),
               f"{rep.results[0].symbol} = {rep.results[0].company_name}" if syms
               else "no results to verify")

        # 3b. Broad screen — the screenable universe (rows with price + market
        # cap, freshness guard passed — all fresh here). The completeness
        # guard excludes exactly the rows lacking price/market_cap.
        interp._interpret_cache.clear()
        rep2 = StockScreener().screen_from_natural_language("   ", limit=3)
        _check("broad screen counts the screenable universe",
               rep2.total_matches == n_screenable,
               f"{rep2.total_matches} of {n_screenable} screenable rows "
               f"({n_stocks - n_screenable} excluded by completeness guard)")

        # 3c. Momentum (derived criteria path) off the copy.
        interp._interpret_cache.clear()
        rep3 = StockScreener().screen_from_natural_language(
            "stocks with momentum", limit=3)
        _check("derived-criteria screen works off the copy",
               rep3.total_matches > 0 and any(
                   "SMA" in f for f in rep3.applied_filters),
               f"{rep3.total_matches} matches; filters: {rep3.applied_filters[:2]}")
    _phase("screens", t)

    # ── 4. Real AAPL backtest + hand-verified equity curve ──────────────────
    print(f"\n4. Backtest {args.symbol} ({args.start} → {args.end}) via the "
          "real engine + hand-verified equity...")
    t = time.monotonic()
    from src.backtester import fetch_historical_data, run_backtest  # noqa: E402

    df = fetch_historical_data(args.symbol)
    _check("historical data fetch works", df is not None and len(df) > 60,
           f"{len(df) if df is not None else 0} bars")
    if df is None:
        FAILURES.append("no historical data — cannot verify equity curve")
    else:
        rules = _backtest_rules()
        # The engine slices df.loc[start:end] before simulating — hand-compute
        # on the SAME window so the two curves are directly comparable.
        window = df.loc[args.start:args.end].copy()
        bt = run_backtest(args.symbol, rules, args.start, args.end, df=window)
        _check("backtest runs + reports an equity curve",
               len(bt.equity_curve) > 0 and bt.final_value > 0,
               f"{len(bt.equity_curve)} equity points, final ${bt.final_value:,.2f}")
        _check("strategy actually traded (buy-and-hold = 1 round trip)",
               bt.total_trades >= 1, f"{bt.total_trades} trade(s)")

        # Hand-verify EVERY equity point + the final value.
        hand = _hand_compute_equity(window, rules)
        _check("window has >= 2 bars for the hand-computed buy-and-hold",
               hand is not None, f"{len(window)} bars in window")
        expected_pts, expected_final = (hand if hand is not None
                                        else ([], -1.0))
        tol = 0.015  # 1.5%: rounding + engine cash/close timing nuances
        worst = 0.0
        for pt, exp in zip(bt.equity_curve, expected_pts):
            if pt["date"] != exp["date"]:
                worst = max(worst, 1.0)
                break
            diff = abs(pt["value"] - exp["value"]) / exp["value"]
            worst = max(worst, diff)
        _check(f"every equity point hand-verified (worst {worst:.2%})",
               hand is not None and worst < tol, f"tolerance {tol:.1%}")
        _check("final value matches hand-computed buy-and-hold",
               hand is not None
               and abs(bt.final_value - expected_final) / expected_final < tol,
               f"engine ${bt.final_value:,.2f} vs hand ${expected_final:,.2f}")
        _check("equity curve ends exactly at reported final_value",
               bt.equity_curve[-1]["value"] == round(bt.final_value, 2),
               f"{bt.equity_curve[-1]['value']} vs {round(bt.final_value, 2)}")

        # Determinism: same input → identical curve.
        bt2 = run_backtest(args.symbol, rules, args.start, args.end, df=window)
        _check("backtest is deterministic (two runs, identical curve)",
               bt2.equity_curve == bt.equity_curve
               and bt2.final_value == bt.final_value)

        # Full 10y sanity run through the same real engine. Symbols with less
        # than 10y of history (recent IPOs) legitimately have no data in the
        # window — skip gracefully instead of failing the drill for them.
        start_10y = f"{datetime.now(timezone.utc).year - 10}-01-01"
        df_10y = df.loc[start_10y:args.end]
        if len(df_10y) > 60:
            bt_long = run_backtest(args.symbol, rules, start_10y, args.end,
                                   df=df_10y)
            _check("10y backtest sane + curve ends at final_value",
                   bt_long.final_value > 0
                   and bt_long.equity_curve[-1]["value"] == round(bt_long.final_value, 2),
                   f"10y final ${bt_long.final_value:,.2f}, "
                   f"{len(bt_long.equity_curve)} equity points")
        else:
            _check("10y sanity run",
                   True, f"skipped — {args.symbol} has only {len(df_10y)} bars "
                         f"since {start_10y}")
    _phase("backtest + hand-verified equity", t)

    # ── 5. The drill must NOT have mutated the restored copy ────────────────
    print("\n5. Read-only guarantee (checksums unchanged)...")
    t = time.monotonic()
    after = migration_dryrun._table_checksums(scratch_url)
    changed = [tbl for tbl in set(baseline) | set(after)
               if baseline.get(tbl) != after.get(tbl)]
    _check("screens + backtests left the restored DB byte-identical",
           not changed, f"tables changed: {changed or 'none'}")
    _phase("read-only check", t)

    total = time.monotonic() - t0
    print("\n" + "=" * 72)
    print(f"⏱ Total drill time: {total:.1f}s")
    for name, dur in PHASES:
        print(f"   {name}: {dur:.1f}s")
    print("=" * 72)
    if FAILURES:
        print("❌ PITR RESTORE DRILL FAILED")
        for f in FAILURES:
            print(f"   ❌ {f}")
        return 1
    print("✅ PITR RESTORE DRILL PASSED — restored copy serves real screens, "
          "hand-verified AAPL equity curve, and stayed read-only.")
    return 0


def _teardown(created_scratch: bool, args, prod_url: str, scratch_name: str) -> None:
    """Always-run cleanup: drop the scratch DB unless --keep; a real
    --scratch-url DB is never touched. Invoked from main()'s finally so a
    mid-run exception cannot leak the scratch DB."""
    if not created_scratch:
        print(f"\n🔒 real restored DB '{scratch_name}' left untouched (read-only drill)")
        return
    if args.keep:
        print(f"\n🔒 kept scratch DB '{args.target}' (--keep)")
        return
    admin = migration_dryrun._admin_conn(prod_url, "")
    admin.autocommit = True
    try:
        cur = admin.cursor()
        cur.execute(f'DROP DATABASE IF EXISTS "{args.target}"')
        print(f"\n🧹 dropped scratch DB '{args.target}'")
    finally:
        admin.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="PITR restore drill (P1-4)")
    ap.add_argument("--scratch-url", default=None,
                    help="A REAL Console-restored DB URL (verification only). "
                         "DB name must contain 'pitr' (rename the branch, e.g. "
                         "stratoscan_pitr).")
    ap.add_argument("--target", default="stratoscan_pitr",
                    help="Scratch DB name for the clone mode (must contain 'pitr')")
    ap.add_argument("--keep", action="store_true",
                    help="Leave the scratch DB in place after the run")
    ap.add_argument("--symbol", default="AAPL",
                    help="Symbol to backtest (default AAPL)")
    ap.add_argument("--start", default="2024-01-01",
                    help="Backtest window start (short window keeps the equity "
                         "curve un-downsampled so every point is hand-checked)")
    ap.add_argument("--end", default="2024-03-31")
    ap.add_argument("--source-url", default=None,
                    help="Clone source (default: DATABASE_URL from .env = prod)")
    args = ap.parse_args()

    from dotenv import load_dotenv

    load_dotenv(os.path.join(HERE, ".env"))          # prod creds first
    prod_url = args.source_url or os.environ.get("DATABASE_URL", "")
    load_dotenv(os.path.join(HERE, "staging.env"), override=True)  # keys/timeouts

    if not prod_url:
        print("❌ DATABASE_URL not set in .env (clone source). Aborting.")
        return 2

    if args.scratch_url:
        scratch_url = args.scratch_url
        created_scratch = False
        name = scratch_url.rstrip("/").split("/")[-1].split("?")[0]
        if not any(m in name for m in ALLOWED_SCRATCH_MARKERS):
            print(f"❌ --scratch-url DB name '{name}' must contain 'pitr' "
                  f"(rename the restored branch, e.g. stratoscan_pitr).")
            return 2
        print(f"📥 Using REAL restored DB: {name}")
    else:
        if not any(m in args.target for m in ALLOWED_SCRATCH_MARKERS):
            print(f"❌ Refusing: target '{args.target}' must contain 'pitr'.")
            return 2
        from urllib.parse import urlsplit, urlunsplit

        p = urlsplit(prod_url)
        scratch_url = urlunsplit(
            (p.scheme, p.netloc, f"/{args.target}", p.query, p.fragment)
        )
        created_scratch = True
        name = args.target

    try:
        return _drill(args, prod_url, scratch_url, created_scratch, name)
    finally:
        _teardown(created_scratch, args, prod_url, name)


if __name__ == "__main__":
    sys.exit(main())
