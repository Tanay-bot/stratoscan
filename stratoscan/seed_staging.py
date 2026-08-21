"""Seed the STAGING database from the PRODUCTION database.

The staging DB (``stratoscan_staging``, same Neon project as prod) is where
E2E tests, verify_*.py probes, and the release-gate rounds run. It must not
rely on production rows that tests mutate, and it must be small enough to
reset quickly.

By default copies the top 50 rows by market cap (real data, zero Finnhub
calls) plus creates the FULL schema (stock_master + users + job_runs +
heartbeat + user_data), so auth/screen/ops endpoints work out of the box.
``--all`` copies the whole exchange-listed universe (~4,953 rows).

Usage:
    /usr/local/bin/python3.14 seed_staging.py            # top 50 by market cap
    /usr/local/bin/python3.14 seed_staging.py --limit 200
    /usr/local/bin/python3.14 seed_staging.py --all      # full universe
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, ".")

from dotenv import load_dotenv  # noqa: E402
import psycopg2  # noqa: E402
from psycopg2.extras import RealDictCursor  # noqa: E402

from src.database import (  # noqa: E402
    JOB_RUNS_INDEXES,
    JOB_RUNS_SCHEMA,
    SCHEDULER_HEARTBEAT_SCHEMA,
    STOCK_MASTER_COLUMN_BACKFILL,
    STOCK_MASTER_COLUMNS,
    STOCK_MASTER_INDEXES,
    STOCK_MASTER_SCHEMA,
    USER_DATA_SCHEMA,
    USERS_EMAIL_VERIFICATION_BACKFILL,
    USERS_SCHEMA,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("seed_staging")

HERE = os.path.dirname(os.path.abspath(__file__))


def _connect(url: str):
    """Open a connection or exit with a friendly message (ops scripts should
    not dump raw psycopg2 tracebacks)."""
    try:
        return psycopg2.connect(url, connect_timeout=15)
    except Exception as e:  # noqa: BLE001
        logger.error("Could not connect to database: %s", str(e)[:200])
        sys.exit(1)


def _create_schema(cur) -> None:
    """Mirror PostgresDB.init_schema so the staging DB is fully functional."""
    cur.execute(STOCK_MASTER_SCHEMA)
    for stmt in STOCK_MASTER_COLUMN_BACKFILL:
        try:
            cur.execute(stmt)
        except Exception as e:  # noqa: BLE001
            logger.debug("column backfill skipped: %s", e)
    for idx in STOCK_MASTER_INDEXES:
        cur.execute(idx)
    cur.execute(USERS_SCHEMA)
    for stmt in USERS_EMAIL_VERIFICATION_BACKFILL:
        try:
            cur.execute(stmt)
        except Exception as e:  # noqa: BLE001
            logger.debug("users backfill skipped: %s", e)
    cur.execute(JOB_RUNS_SCHEMA)
    for idx in JOB_RUNS_INDEXES:
        cur.execute(idx)
    cur.execute(SCHEDULER_HEARTBEAT_SCHEMA)
    cur.execute(USER_DATA_SCHEMA)


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed staging DB from production.")
    ap.add_argument("--limit", type=int, default=50, help="Rows to copy (default 50)")
    ap.add_argument("--all", action="store_true", help="Copy the whole universe")
    args = ap.parse_args()

    # Load prod values first, then staging overrides (staging DATABASE_URL wins).
    load_dotenv(os.path.join(HERE, ".env"))
    prod_url = os.environ.get("DATABASE_URL", "")
    load_dotenv(os.path.join(HERE, "staging.env"), override=True)
    staging_url = os.environ.get("DATABASE_URL", "")

    if not prod_url or not staging_url:
        logger.error("Need DATABASE_URL in .env and staging.env — aborting.")
        return 1
    if "stratoscan_staging" not in staging_url:
        logger.error(
            "staging.env DATABASE_URL does not point at stratoscan_staging — refusing."
        )
        return 1

    limit = args.limit if not args.all else 10_000_000

    # 1) Pull rows from production.
    logger.info("Reading top-%d stocks from production...", limit)
    prod = _connect(prod_url)
    rows: List[Dict[str, Any]] = []
    try:
        with prod.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM stock_master "
                "ORDER BY market_cap DESC NULLS LAST LIMIT %s",
                (limit,),
            )
            rows = [dict(r) for r in cur.fetchall()]
    finally:
        prod.close()
    logger.info("Loaded %d production rows.", len(rows))
    if not rows:
        logger.error("Production stock_master is empty — nothing to seed.")
        return 1

    # 2) Create the full schema on staging and upsert the rows.
    staging = _connect(staging_url)
    try:
        with staging.cursor() as cur:
            logger.info("Creating schema on staging...")
            _create_schema(cur)
        staging.commit()

        # STOCK_MASTER_COLUMNS is a frozenset — sort for a deterministic
        # column order so the INSERT list and the value tuples always line up
        # regardless of set iteration order (also self-documenting).
        columns = sorted(c for c in STOCK_MASTER_COLUMNS if c != "symbol")
        col_list = ", ".join(["symbol"] + columns)
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns)
        sql = (
            f"INSERT INTO stock_master ({col_list}) VALUES %s "
            f"ON CONFLICT (symbol) DO UPDATE SET {updates}"
        )
        values: List[tuple] = []
        for r in rows:
            values.append((r.get("symbol"),) + tuple(r.get(c) for c in columns))
        from psycopg2.extras import execute_values

        with staging.cursor() as cur:
            execute_values(cur, sql, values, template=None)
        staging.commit()
        logger.info("Upserted %d rows into staging stock_master.", len(rows))
    finally:
        staging.close()

    # 3) Verify.
    check = _connect(staging_url)
    try:
        with check.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM stock_master")
            count = cur.fetchone()[0]
        logger.info("✅ Staging stock_master now has %d rows.", count)
    finally:
        check.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
