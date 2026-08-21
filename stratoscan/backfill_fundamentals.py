"""One-time financial-metrics backfill for the corrected Finnhub keys.

Populates ev_ebitda / net_margin_ttm (and re-freshes the other Tier 2
ratios) for symbols currently missing them. The old mapping read
non-existent Finnhub keys (``evToEBITDA`` / ``netMarginTTM``), so those
two columns were 0% populated and every filter on them returned 0
candidates. This script re-fetches /stock/metric with the CORRECTED keys
(``evEbitdaTTM`` / ``netProfitMarginTTM``) and COALESCE-updates so an
existing value is never clobbered.

Usage:
    python backfill_fundamentals.py [--limit N] [--sleep S]

Defaults: limit 3000 (top-N by market cap), sleep 0.1s between calls
(politeness pace — /stock/metric is one call per symbol).
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Any, Dict

sys.path.insert(0, ".")

from src.data_fetcher import (  # noqa: E402
    fetch_financial_metrics,
    initialize_client_pool,
)
from src.database import PostgresDB  # noqa: E402

# The metric columns fetch_financial_metrics returns (subset of
# stock_master) — only these are updated, never profile/quote columns.
METRIC_COLUMNS: tuple = (
    "pe_ratio", "eps_ttm", "dividend_yield", "beta", "ps_ratio",
    "pb_ratio", "ev_ebitda", "roe_ttm", "roa_ttm", "gross_margin_ttm",
    "net_margin_ttm", "revenue_growth_ttm", "eps_growth_ttm",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=3000)
    parser.add_argument("--sleep", type=float, default=0.1)
    args = parser.parse_args()

    db = PostgresDB()
    with db._get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT symbol FROM stock_master "
            "WHERE (ev_ebitda IS NULL OR net_margin_ttm IS NULL) "
            "AND market_cap IS NOT NULL "
            "ORDER BY market_cap DESC NULLS LAST LIMIT %s",
            (args.limit,),
        )
        symbols = [r[0] for r in cur.fetchall()]
        cur.close()
    print(f"Refreshing financial metrics for {len(symbols)} symbols...", flush=True)

    pool = initialize_client_pool()
    start = time.time()
    updated = 0
    for i, sym in enumerate(symbols, 1):
        m = fetch_financial_metrics(pool, sym)
        if m:
            # Only the metric columns we own — never touch profile fields.
            row: Dict[str, Any] = {
                c: m.get(c) for c in METRIC_COLUMNS if c in m
            }
            if row:
                with db._get_conn() as conn:
                    cur = conn.cursor()
                    set_clause = ", ".join(
                        f"{c} = COALESCE(%s, {c})" for c in row
                    )
                    cur.execute(
                        f"UPDATE stock_master SET {set_clause} "
                        "WHERE symbol = %s",
                        list(row.values()) + [sym],
                    )
                    conn.commit()
                    cur.close()
                updated += 1
        if args.sleep:
            time.sleep(args.sleep)
        if i % 200 == 0:
            print(
                f"  progress: {i}/{len(symbols)} ({time.time() - start:.0f}s)",
                flush=True,
            )

    # Coverage report.
    with db._get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT "
            "COUNT(*) FILTER (WHERE ev_ebitda IS NOT NULL), "
            "COUNT(*) FILTER (WHERE net_margin_ttm IS NOT NULL), "
            "COUNT(*) FROM stock_master"
        )
        ev, nm, total = cur.fetchone()
        cur.close()
    db.close()

    print(
        f"\nDone in {time.time() - start:.0f}s. rows updated: {updated}. "
        f"Coverage now: ev_ebitda={ev} net_margin_ttm={nm} of {total}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
