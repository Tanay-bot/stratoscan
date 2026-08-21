"""One-time technical-indicator backfill for stock_master.

Populates the technical columns (sma_50, sma_200, rsi_14, high_52w,
low_52w, macd_12_26, atr_14, bollinger_upper/lower) plus current_price
where missing, computed locally from yfinance daily history. This does NOT
replace the scheduler — it is a one-time data backfill so the screener's
momentum/trend filters return matches immediately (the live DB currently
has 0/18449 rows with technicals because the scheduler was never run).

Why yfinance: it is the same source the backtester already uses, is
rate-limit friendly for bulk history, and needs no API budget. Finnhub's
per-symbol indicator calls are capped by the free tier.

Usage:
    /usr/local/bin/python3.14 backfill_technicals.py [--limit N] [--batch N]

Defaults: limit 3000 (top-N by market cap), batch 250.
Uses COALESCE on UPDATE so a missing value never clobbers an existing one.
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Dict, List, Optional

import pandas as pd

sys.path.insert(0, ".")
# Single source of truth: the same local yfinance computation the production
# scheduler now falls back to (data_fetcher._fetch_technicals_yfinance).
from src.data_fetcher import TECH_COLUMNS, compute_indicators  # noqa: E402
from src.database import PostgresDB  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=3000)
    parser.add_argument("--batch", type=int, default=250)
    parser.add_argument("--sleep", type=float, default=0.0,
                        help="seconds to sleep between batches (rate-limit throttle)")
    parser.add_argument("--missing", action="store_true",
                        help="only rows still missing technicals (2nd-pass mode)")
    args = parser.parse_args()

    import time as _time  # noqa: F401 (shadowed below intentionally)
    import yfinance as yf  # local import: keeps this script usable if absent

    db = PostgresDB()
    if args.missing:
        # avg_volume is part of the technical set (TECH_COLUMNS) — a
        # second-pass run must pick up rows that already have SMA/RSI but
        # predate the avg_volume computation.
        where = ("(sma_50 IS NULL OR rsi_14 IS NULL OR avg_volume IS NULL) "
                 "AND market_cap IS NOT NULL")
    else:
        where = "market_cap IS NOT NULL"
    with db._get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT symbol, current_price FROM stock_master "
            f"WHERE {where} ORDER BY market_cap DESC NULLS LAST LIMIT %s",
            (args.limit,),
        )
        rows = cur.fetchall()
        cur.close()

    symbols = [r[0] for r in rows]
    print(f"Backfilling technicals for {len(symbols)} symbols "
          f"(batch={args.batch}, sleep={args.sleep}s, missing={args.missing})...",
          flush=True)

    start = time.time()
    updates: List[tuple] = []
    fetched = 0
    for i in range(0, len(symbols), args.batch):
        chunk = symbols[i:i + args.batch]
        try:
            data = yf.download(
                chunk, period="1y", interval="1d", progress=False,
                auto_adjust=True, threads=True, group_by="ticker",
            )
        except Exception as e:
            print(f"  batch {i}-{i + len(chunk)} download failed: {e}", flush=True)
            if args.sleep:
                _time.sleep(args.sleep)
            continue
        if args.sleep:
            _time.sleep(args.sleep)
        for sym in chunk:
            try:
                if isinstance(data.columns, pd.MultiIndex) and sym in data.columns.levels[1]:
                    df = data[sym]
                elif sym in data.columns.get_level_values(0):
                    df = data.xs(sym, axis=1, level=0)
                else:
                    continue
            except Exception:
                continue
            df = df.dropna(subset=["Close"])
            if df.empty:
                continue
            fetched += 1
            ind = compute_indicators(df)
            # Only write symbols where we got something new; COALESCE on
            # UPDATE keeps any pre-existing value when the new one is None.
            if any(v is not None for v in ind.values()):
                updates.append(tuple(
                    [ind[c] for c in TECH_COLUMNS] + [sym]
                ))
        # Persist per batch so an interrupted run keeps its progress
        # (never lose a whole pass to a timeout / crash).
        if updates:
            with db._get_conn() as conn:
                cur = conn.cursor()
                set_clause = ", ".join(
                    f"{c} = COALESCE(%s, {c})" for c in TECH_COLUMNS
                )
                sql = f"UPDATE stock_master SET {set_clause} WHERE symbol = %s"
                for row in updates:
                    cur.execute(sql, row)
                conn.commit()
                cur.close()
            updates = []
        print(f"  progress: {min(i + args.batch, len(symbols))}/{len(symbols)} "
              f"symbols scanned ({time.time() - start:.0f}s)",
              flush=True)

    # Coverage report (includes the gate columns so a silently-empty
    # column is visible at the end of every backfill run).
    with db._get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT "
            "COUNT(*) FILTER (WHERE sma_50 IS NOT NULL), "
            "COUNT(*) FILTER (WHERE rsi_14 IS NOT NULL), "
            "COUNT(*) FILTER (WHERE high_52w IS NOT NULL), "
            "COUNT(*) FILTER (WHERE current_price IS NOT NULL), "
            "COUNT(*) FILTER (WHERE avg_volume IS NOT NULL), "
            "COUNT(*) FROM stock_master"
        )
        sma, rsi, h52, px, avgv, total = cur.fetchone()
        cur.close()

    print(f"\nDone in {time.time() - start:.0f}s. "
          f"symbols with computed data: {fetched}; rows updated: {len(updates)}")
    print(f"Coverage now: sma_50={sma} rsi_14={rsi} high_52w={h52} "
          f"current_price={px} avg_volume={avgv} of {total}")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
