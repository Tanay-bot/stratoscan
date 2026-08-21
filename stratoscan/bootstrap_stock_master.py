"""One-time bootstrap: fill stock_master with REAL Finnhub data.

Fetches the full US universe (via the aggregated ``exchange=US`` symbol
endpoint), then pulls each symbol's company profile (fundamentals) and
quote (price/volume) in parallel across the thread-safe client pool,
and bulk-upserts into the screener's PostgreSQL ``stock_master`` table.

This is the "first-run" equivalent of what the scheduler's price +
fundamental jobs do on an ongoing cadence. Use it when stock_master is
empty (e.g. a fresh database) so the screener serves real data instead
of zero matches.

Usage:
    /usr/local/bin/python3.14 bootstrap_stock_master.py [--workers N]
"""

from __future__ import annotations

import argparse
import concurrent.futures
import logging
import sys
import threading
import time
from typing import Any, Dict, List, Optional

from src.data_fetcher import (
    build_stock_record,
    fetch_financial_metrics,
    fetch_stock_profile,
    fetch_stock_quote,
    fetch_us_stock_symbols,
    initialize_client_pool,
)
from src.database import PostgresDB

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
)
logger = logging.getLogger("bootstrap")


class ThrottledPool:
    """Wrap a FinnhubClientPool with a global token-bucket rate gate.

    The underlying pool round-robins across N keys; each free key allows
    ~60 req/min, so the AGGREGATE budget is 60*N req/min (10 keys =
    10 req/s). Firing 10 workers at ~3 req/s each (30 req/s) without a
    gate triggers a 429 storm that burns the budget on retries. Gating
    ``next()`` with a token bucket keeps aggregate request rate under
    the cap, so retries (and wasted quota) drop to ~zero.
    """

    def __init__(self, pool: Any, rate_per_sec: float) -> None:
        self._pool = pool
        self._interval = 1.0 / rate_per_sec
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def next(self) -> Any:
        with self._lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                time.sleep(wait)
            self._next_allowed = max(now, self._next_allowed) + self._interval
            return self._pool.next()

    @property
    def size(self) -> int:
        return self._pool.size


def fetch_one(
    pool: Any,
    symbol: str,
    with_metrics: bool = False,
    no_quotes: bool = False,
) -> Optional[Dict[str, Any]]:
    """Fetch profile + quote (+ optional Tier-2 metrics) for one symbol.

    With ``no_quotes=True`` the quote call is skipped — used by the
    enrich pass, which already has prices on disk and only needs to
    fill the profile/metrics columns (sector, PE, beta, ROE, ...).
    """
    profile = fetch_stock_profile(pool, symbol)
    quote = None if no_quotes else fetch_stock_quote(pool, symbol)
    if profile and with_metrics:
        m = fetch_financial_metrics(pool, symbol)
        if m and m.get("symbol") == symbol:
            profile = {
                **profile,
                **{k: v for k, v in m.items() if k != "symbol"},
            }
    rec = build_stock_record(profile, quote, None)
    if rec:
        # company_name is NOT NULL in the schema — never let a symbol that
        # has a quote but no profile row violate the constraint.
        rec.setdefault("company_name", "")
    return rec


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--rate", type=float, default=9.0,
                        help="max aggregate API req/s (10 free keys ~ 10/s; keep under the cap)")
    parser.add_argument("--limit", type=int, default=0, help="0 = whole universe")
    parser.add_argument("--metrics", action="store_true", help="also fetch Tier-2 ratios")
    parser.add_argument("--no-quotes", action="store_true",
                        help="skip quote fetches (enrich pass: profile+metrics only)")
    args = parser.parse_args()

    t0 = time.time()
    raw_pool = initialize_client_pool()
    pool = ThrottledPool(raw_pool, args.rate)
    symbols = fetch_us_stock_symbols(pool)
    if args.limit:
        symbols = symbols[: args.limit]
    print(f"symbols: {len(symbols)} | throttle: {args.rate} req/s", flush=True)

    records: List[Dict[str, Any]] = []
    done = 0
    failed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [
            ex.submit(fetch_one, pool, s, args.metrics, args.no_quotes)
            for s in symbols
        ]
        for fut in concurrent.futures.as_completed(futs):
            try:
                r = fut.result()
            except Exception as e:  # never let one symbol kill the run
                failed += 1
                r = None
            if r:
                records.append(r)
            done += 1
            if done % 1000 == 0 or done == len(symbols):
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed > 0 else 0
                print(
                    f"fetched {done}/{len(symbols)} "
                    f"({len(records)} records, {rate:.1f}/s, "
                    f"{elapsed:.0f}s elapsed)",
                    flush=True,
                )

    print(f"total records with data: {len(records)} (failed: {failed})", flush=True)
    db = PostgresDB()
    for i in range(0, len(records), 1000):
        chunk = records[i : i + 1000]
        db.bulk_upsert_stocks(chunk)
        print(f"upserted {min(i + 1000, len(records))}/{len(records)}", flush=True)
    print(f"DONE in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    sys.exit(main())
