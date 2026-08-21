"""Seed the DEDICATED probes database (``stratoscan_probes``) for the 15
``verify_*.py`` behavior probes.

Why a separate DB: the probes are allowed to mutate/abuse rows (they delete,
oversize, corrupt-format, and hammer data as part of their job). That must
never touch production — or even the E2E staging DB (``stratoscan_staging``),
which the release-gate E2E rounds rely on. Probes get their OWN database in
the same Neon project so isolation is structural, not just behavioral.

Data is REAL, not synthetic:
  - the top ``--top`` symbols by market cap are copied from production
    (guarantees AAPL + the mega-caps the probes reference),
  - SPY is fetched live from yfinance (it is an ETF, so it is NOT in the
    stock universe — real OHLCV -> current_price, sma_50/200, rsi_14,
    52w high/low, market_cap from shares outstanding),
  - ``--random`` symbols are pulled randomly from the production universe.

Usage:
    /usr/local/bin/python3.14 seed_probes_db.py                    # 100 top + 20 random + SPY
    /usr/local/bin/python3.14 seed_probes_db.py --top 50 --random 25
    /usr/local/bin/python3.14 seed_probes_db.py --no-spy           # skip the yfinance fetch

Safety: refuses to run unless the target URL's database path is exactly
``stratoscan_probes`` — production is untouchable from here.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, ".")

from dotenv import load_dotenv  # noqa: E402
from psycopg2.extras import RealDictCursor, execute_values  # noqa: E402

from seed_staging import _connect, _create_schema  # noqa: E402
from src.database import STOCK_MASTER_COLUMNS  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("seed_probes_db")

HERE = os.path.dirname(os.path.abspath(__file__))

TARGET_DB = "stratoscan_probes"


def _staging_url() -> str:
    """The probes DB URL: STAGING_DATABASE_URL if set, else staging.env's
    DATABASE_URL with the database path swapped to stratoscan_probes."""
    load_dotenv(os.path.join(HERE, "staging.env"), override=True)
    url = os.environ.get("STAGING_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
    if not url:
        raise SystemExit("Need STAGING_DATABASE_URL or DATABASE_URL in staging.env")
    from urllib.parse import parse_qsl, urlsplit, urlunsplit, urlencode

    parts = urlsplit(url)
    q = dict(parse_qsl(parts.query))
    fixed = urlunsplit(
        (parts.scheme, parts.netloc, f"/{TARGET_DB}", urlencode(q), parts.fragment)
    )
    return fixed


def _prod_rows(limit: int, exclude: set[str], prod_url: str) -> List[Dict[str, Any]]:
    if not prod_url:
        raise SystemExit("DATABASE_URL missing from .env")
    conn = _connect(prod_url)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM stock_master "
                "WHERE symbol <> ALL(%s) "
                "ORDER BY market_cap DESC NULLS LAST LIMIT %s",
                (list(exclude), limit),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _random_rows(n: int, exclude: set[str], prod_url: str) -> List[Dict[str, Any]]:
    conn = _connect(prod_url)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM stock_master "
                "WHERE symbol <> ALL(%s) "
                "ORDER BY random() LIMIT %s",
                (list(exclude), n),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _spy_row() -> Dict[str, Any]:
    """Real SPY OHLCV from yfinance -> a stock_master-shaped row."""
    import numpy as np
    import yfinance as yf

    t = yf.Ticker("SPY")
    df = t.history(period="1y")
    if df is None or df.empty or len(df) < 210:
        raise RuntimeError(f"yfinance returned only {0 if df is None else len(df)} SPY rows")
    closes = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    vol = df["Volume"].astype(float)
    last = closes.iloc[-1]
    prev = closes.iloc[-2]
    deltas = closes.diff()
    gain = deltas.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-deltas.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = (100 - 100 / (1 + rs)).iloc[-1]
    shares = (t.info or {}).get("sharesOutstanding") or 917_782_016
    row = {
        "symbol": "SPY",
        "company_name": "SPDR S&P 500 ETF Trust",
        "sector": "ETF",
        "industry": "Exchange Traded Fund",
        "current_price": round(float(last), 2),
        "price_change_pct": round(float((last / prev - 1) * 100), 2),
        "volume": int(vol.iloc[-1]),
        "avg_volume": int(vol.tail(30).mean()),
        "sma_50": round(float(closes.tail(50).mean()), 2),
        "sma_200": round(float(closes.tail(200).mean()), 2),
        "rsi_14": round(float(rsi), 2),
        "high_52w": round(float(high.max()), 2),
        "low_52w": round(float(low.min()), 2),
        "market_cap": round(float(last) * shares),
        "beta": None,
        "pe_ratio": None,
        "pb_ratio": None,
        "eps_ttm": None,
        "eps_growth_ttm": None,
        "revenue_growth_ttm": None,
        "gross_margin_ttm": None,
        "net_margin_ttm": None,
        "roa_ttm": None,
        "roe_ttm": None,
        "dividend_yield": None,
        "ps_ratio": None,
        "ev_ebitda": None,
        "atr_14": None,
        "bollinger_upper": None,
        "bollinger_lower": None,
        "macd_12_26": None,
    }
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed the probes DB from production + yfinance.")
    ap.add_argument("--top", type=int, default=100, help="Top-N by market cap from prod (default 100)")
    ap.add_argument("--random", type=int, default=20, help="Random symbols from prod (default 20)")
    ap.add_argument("--no-spy", action="store_true", help="Skip the yfinance SPY fetch")
    args = ap.parse_args()

    # Capture the PRODUCTION URL FIRST — before staging.env overrides
    # DATABASE_URL in os.environ. (seed_staging.py does the same; reading it
    # after the override would silently point "prod" at the staging DB.)
    load_dotenv(os.path.join(HERE, ".env"))
    prod_url = os.environ.get("DATABASE_URL", "")

    target = _staging_url()
    from urllib.parse import urlsplit

    db_path = urlsplit(target).path.lstrip("/").split("?")[0]
    if db_path != TARGET_DB:
        logger.error("Target database path is '%s' — refusing (must be %s).", db_path, TARGET_DB)
        return 1

    # 1) Pull rows: top-N (includes AAPL) + random M + SPY.
    logger.info("Pulling top-%d by market cap from production...", args.top)
    top = _prod_rows(args.top, set(), prod_url)
    if not top:
        logger.error("Production stock_master is empty — nothing to seed.")
        return 1
    have = {r["symbol"] for r in top}
    logger.info("  top: %d symbols (AAPL present: %s)", len(top), "AAPL" in have)

    random_rows: List[Dict[str, Any]] = []
    if args.random > 0:
        logger.info("Pulling %d random symbols from production...", args.random)
        random_rows = _random_rows(args.random, have, prod_url)
        have.update(r["symbol"] for r in random_rows)
        logger.info("  random: %d symbols", len(random_rows))

    rows = top + random_rows

    spy: Dict[str, Any] | None = None
    if not args.no_spy:
        logger.info("Fetching real SPY data from yfinance...")
        try:
            spy = _spy_row()
            logger.info(
                "  SPY: $%.2f, mcap=$%.0fB, sma50=%.2f, rsi14=%.2f, 52wH=%.2f 52wL=%.2f",
                spy["current_price"], spy["market_cap"] / 1e9,
                spy["sma_50"], spy["rsi_14"], spy["high_52w"], spy["low_52w"],
            )
            rows.append(spy)
        except Exception as e:  # noqa: BLE001
            logger.warning("  SPY fetch failed (%s) — continuing without it.", str(e)[:120])
    else:
        logger.info("SPY fetch skipped (--no-spy).")

    if len(rows) < 100:
        logger.error("Only %d rows — seed must be >= 100.", len(rows))
        return 1

    # 2) Create full schema + upsert into the probes DB.
    conn = _connect(target)
    try:
        with conn.cursor() as cur:
            logger.info("Creating schema on %s...", TARGET_DB)
            _create_schema(cur)
        conn.commit()

        columns = sorted(c for c in STOCK_MASTER_COLUMNS if c != "symbol")
        col_list = ", ".join(["symbol"] + columns)
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns)
        sql = (
            f"INSERT INTO stock_master ({col_list}) VALUES %s "
            f"ON CONFLICT (symbol) DO UPDATE SET {updates}"
        )
        values = []
        for r in rows:
            values.append((r.get("symbol"),) + tuple(r.get(c) for c in columns))
        with conn.cursor() as cur:
            execute_values(cur, sql, values, template=None)
        conn.commit()
        logger.info("Upserted %d rows into %s.stock_master.", len(rows), TARGET_DB)
    finally:
        conn.close()

    # 3) Verify.
    check = _connect(target)
    try:
        with check.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM stock_master")
            count = cur.fetchone()[0]
            cur.execute("SELECT symbol FROM stock_master WHERE symbol IN ('AAPL','SPY') ORDER BY symbol")
            present = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT COUNT(*) FROM stock_master WHERE current_price IS NOT NULL")
            with_price = cur.fetchone()[0]
    finally:
        check.close()
    logger.info("✅ %s now has %d rows (%d with price). AAPL+SPY: %s",
                TARGET_DB, count, with_price, present)
    if count < 100:
        logger.error("Verification failed: count=%d < 100", count)
        return 1
    # SPY is only required when it wasn't skipped via --no-spy (or a failed
    # yfinance fetch — which also skips the requirement, since the task's
    # "AAPL + SPY" guarantee is only meaningful when SPY was actually seeded).
    if not args.no_spy and spy is None:
        logger.error("SPY fetch failed and --no-spy not set — seed incomplete.")
        return 1
    if not args.no_spy and "SPY" not in present:
        logger.error("SPY missing from seeded rows: %s", present)
        return 1
    if "AAPL" not in present:
        logger.error("AAPL missing from seeded rows: %s", present)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
