"""One-time cleanup: remove rows NOT in the exchange-listed universe.

The scheduler refreshes exactly the exchange-listed universe (NYSE/NASDAQ/
AMEX, ~4,964 symbols) every ~15 minutes. The 13,485 rows outside it are OTC
pink sheets (mic=OOTC) that will NEVER be refreshed again — they are dead
data that pollutes screens and inflates data_quality.stale_count. This
script deletes them so stock_master holds only the live refresh universe.

Safety:
  - The universe comes from the SAME code path the scheduler uses
    (fetch_us_stock_symbols → MIC filter). If the fetch returns an
    implausibly small set (< 3,000 — e.g. a network/API glitch), the
    script ABORTS without deleting anything.
  - Prints before/after row counts so the change is auditable.
  - Idempotent: re-running after a clean state deletes nothing.

Run:  /usr/local/bin/python3.14 prune_unlisted_stocks.py
"""

from __future__ import annotations

import logging
import sys
from typing import List

sys.path.insert(0, ".")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("prune")

from src.data_fetcher import fetch_us_stock_symbols, initialize_client_pool  # noqa: E402
from src.config import settings  # noqa: E402
import psycopg2  # noqa: E402

MIN_UNIVERSE = 3000  # below this, the fetch is broken — abort, don't nuke data


def main() -> int:
    if not settings.database_url:
        logger.error("DATABASE_URL not configured — aborting.")
        return 1

    logger.info("Fetching exchange-listed universe (scheduler code path)...")
    pool = initialize_client_pool()
    universe: List[str] = fetch_us_stock_symbols(pool)
    universe_set = set(universe)
    logger.info("Universe size: %d symbols", len(universe_set))
    if len(universe_set) < MIN_UNIVERSE:
        logger.error(
            "Universe fetch returned %d symbols (< %d) — refusing to delete. "
            "Check Finnhub keys / network, then re-run.",
            len(universe_set),
            MIN_UNIVERSE,
        )
        return 2

    conn = psycopg2.connect(settings.database_url, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM stock_master")
            before = int(cur.fetchone()[0] or 0)
            logger.info("stock_master rows BEFORE: %d", before)

            # Symbols in the DB that are NOT in the exchange-listed universe.
            cur.execute("SELECT symbol FROM stock_master")
            db_symbols = {row[0] for row in cur.fetchall()}
            to_delete = sorted(db_symbols - universe_set)
            logger.info(
                "Rows to delete (not in refresh universe): %d",
                len(to_delete),
            )

            if not to_delete:
                logger.info("Nothing to delete — table already matches universe.")
                return 0

            # Safety: never delete more than the stale excess would suggest.
            if len(to_delete) > before * 0.9:
                logger.error(
                    "Refusing: deleting %d of %d rows (>90%%) looks like a "
                    "bad universe fetch.",
                    len(to_delete),
                    before,
                )
                return 3

            for i in range(0, len(to_delete), 500):
                batch = to_delete[i : i + 500]
                cur.execute(
                    "DELETE FROM stock_master WHERE symbol = ANY(%s)",
                    (batch,),
                )
                logger.info("Deleted batch %d-%d (%d rows)", i, i + len(batch), cur.rowcount)
            conn.commit()

            cur.execute("SELECT COUNT(*) FROM stock_master")
            after = int(cur.fetchone()[0] or 0)
            logger.info("stock_master rows AFTER: %d (removed %d)", after, before - after)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
