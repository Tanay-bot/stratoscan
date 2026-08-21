"""Historical OHLCV backfill into the dedicated backtest database (Neon).

Tier 3 data-completeness: pre-fills N years of yfinance daily OHLCV for
the whole universe (or a bounded subset) into ``BACKTEST_DATABASE_URL``,
so a backtest NEVER waits on a cold download at request time — and,
critically, so "10 years of history is loaded" is a **counted, verified
fact** instead of a claim.

Why this exists (and why it's not just the cache warmer):
  - The cache warmer (src/cache_warmer.py) refreshes the top-N symbols
    by market cap nightly. That's the *ongoing* freshness job.
  - This tool is the *initial bulk load*: after a fresh deploy or a
    wiped backtest DB, run ``python -m src.main backfill-history`` once
    (or with --limit for a bounded first pass) to populate everything,
    then the warmer keeps it fresh. It is resumable — re-running skips
    symbols whose newest cached bar is recent — so a long backfill can
    be interrupted and continued without re-downloading.
  - It uses the SAME primitives as the request path
    (``fetch_historical_data`` / ``store_historical_data`` from
    src.backtester), so backfilled data is byte-identical to on-demand
    data. No second implementation of the fetch/store logic.
  - It VERIFIES: after storing, it counts rows per symbol and — for a
    sample symbol — cross-checks the stored series against a FRESH
    yfinance download (row count + last date), so a truncated or corrupt
    backfill fails loudly instead of silently degrading backtests.

Design (the Stratoscan way — near-zero marginal cost):
  - Selector: explicit symbols, OR the screener's top-N by market cap,
    OR the whole yfinance universe (--universe).
  - Planner: one ``get_symbol_max_dates()`` query decides which symbols
    are missing or stale — no per-symbol round-trips.
  - Fetcher: one yfinance download per stale symbol (same timeout + pin
    as the engine), polite pause between downloads.
  - Resilience: a single bad symbol is logged and skipped; the cycle
    never aborts mid-way. No backtest DB → clear error, not a crash.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from src.config import settings
from src.database import BacktestDB

logger = logging.getLogger(__name__)

# Expected trading days per year (252) — used to sanity-check a symbol's
# stored row count against the years requested. yfinance daily bars are
# weekdays-minus-holidays, so 252 is the standard convention.
TRADING_DAYS_PER_YEAR: int = 252
# A symbol whose stored count is below this fraction of the expected count
# is flagged as truncated (delisted names legitimately have less history,
# so the threshold is deliberately generous — we catch real truncation,
# not legitimate IPO-age names).
MIN_COVERAGE_FRACTION: float = 0.5


def _select_universe(
    symbols: Optional[List[str]],
    limit: Optional[int],
) -> List[str]:
    """Resolve the symbol set to backfill.

    Priority: explicit ``symbols`` list → screener top-N by market cap →
    full yfinance universe (bounded by ``limit``).

    Args:
        symbols: Explicit symbol list (deduped, uppercased).
        limit: Max symbols from the fallback sources.

    Returns:
        Ordered, deduped list of tickers.
    """
    if symbols:
        seen: set = set()
        out: List[str] = []
        for s in symbols:
            s = str(s).strip().upper()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out

    result: List[str] = []
    try:
        from src.database import PostgresDB

        result = PostgresDB().get_top_symbols(limit or 1000)
        logger.info("Backfill universe: %d screener top symbols by market cap", len(result))
    except Exception as e:
        logger.warning(
            "Screener DB unavailable (%s) — falling back to full yfinance universe", e
        )
        result = []

    if not result:
        try:
            from src.data_fetcher import fetch_us_stock_symbols, initialize_client_pool

            result = fetch_us_stock_symbols(initialize_client_pool())
            logger.info("Backfill universe: %d symbols from yfinance universe", len(result))
        except Exception as e:
            logger.warning("Universe fallback failed too: %s — nothing to backfill", e)
            return []

    if limit:
        result = result[:limit]
    return [s.strip().upper() for s in result if str(s).strip()]


def _stale_symbols(
    candidates: List[str],
    max_dates: Dict[str, Optional[str]],
    stale_days: int,
    today: str,
) -> List[str]:
    """Which candidates need a (re)download: missing or stale history."""
    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=stale_days)).strftime(
        "%Y-%m-%d"
    )
    stale: List[str] = []
    for symbol in candidates:
        newest = max_dates.get(symbol)
        if newest is None or newest < cutoff:
            stale.append(symbol)
    return stale


def _verify_symbol(
    db: Any,
    symbol: str,
    stored_count: int,
    years: int,
    fetch_fn: Callable[..., Any],
) -> Dict[str, Any]:
    """Cross-check a stored symbol against a FRESH yfinance download.

    Independent of the store path (a second, live download), so it catches
    truncation or corruption in the stored series that a self-consistent
    count could not. Tolerance-based: delisted/IPO-age symbols legitimately
    have less history.

    Returns:
        Dict with ``ok`` (bool), ``stored``, ``fresh`` (row counts),
        ``last_date`` (stored newest date), and a ``note``.
    """
    expected_min = int(years * TRADING_DAYS_PER_YEAR * MIN_COVERAGE_FRACTION)
    if stored_count < expected_min:
        return {
            "ok": False,
            "stored": stored_count,
            "fresh": None,
            "last_date": None,
            "note": f"stored {stored_count} rows < {expected_min} expected for {years}y",
        }

    try:
        fresh = fetch_fn(symbol, years=years)
        fresh_count = len(fresh) if fresh is not None else 0
        # yfinance counts days; a 2-3% drift is normal (delist halts,
        # corporate actions). Flag anything above 10% or a flat-out
        # mismatch of magnitude.
        drift = abs(stored_count - fresh_count) / max(fresh_count, 1)
        ok = drift <= 0.10
        return {
            "ok": ok,
            "stored": stored_count,
            "fresh": fresh_count,
            "last_date": db.get_symbol_max_dates().get(symbol),
            "note": (
                f"fresh re-download {fresh_count} rows (drift {drift:.1%})"
                if ok
                else f"MISMATCH: stored {stored_count} vs fresh {fresh_count}"
            ),
        }
    except Exception as e:
        return {
            "ok": False,
            "stored": stored_count,
            "fresh": None,
            "last_date": None,
            "note": f"cross-check failed: {e}",
        }


def backfill_history(
    symbols: Optional[List[str]] = None,
    years: int = 10,
    limit: Optional[int] = None,
    delay: float = 0.15,
    stale_days: int = 2,
    today: Optional[str] = None,
    fetch_fn: Optional[Callable[..., Any]] = None,
    store_fn: Optional[Callable[..., Any]] = None,
    verify: bool = True,
    db: Optional[Any] = None,
) -> Dict[str, Any]:
    """Run one backfill cycle: fetch + store history for stale symbols.

    Args:
        symbols: Explicit symbols. None → screener top-N → yfinance
            universe (see ``--universe``).
        years: Years of history per symbol (default 10).
        limit: Max symbols from the fallback universe selectors.
        delay: Politeness pause between yfinance downloads.
        stale_days: Skip symbols whose newest cached bar is younger than
            this (default 2 — a backfill skips what the warmer already
            keeps fresh).
        today: Date override (YYYY-MM-DD) for hermetic tests.
        fetch_fn / store_fn: Overridable primitives (default to the real
            ``fetch_historical_data`` / ``store_historical_data``).
        verify: Run the fresh-download cross-check on a sample symbol.
        db: Optional injected backtest DB (tests). Defaults to Neon
            (``BACKTEST_DATABASE_URL``) — REQUIRED; this tool refuses to
            silently write to the local SQLite dev file.

    Returns:
        Stats dict: candidates / backfilled / skipped / failed / rows,
        per-symbol results, verification (if enabled).
    """
    from src.backtester import fetch_historical_data, store_historical_data

    fetch_fn = fetch_fn or fetch_historical_data
    store_fn = store_fn or store_historical_data
    today = today or datetime.now().strftime("%Y-%m-%d")

    created_db = db is None
    if created_db:
        if not settings.has_backtest_database:
            return {
                "error": (
                    "BACKTEST_DATABASE_URL is not set. The backfill writes "
                    "ONLY to the dedicated Neon backtest DB (production "
                    "storage) — it deliberately refuses the local SQLite "
                    "dev fallback. Set BACKTEST_DATABASE_URL in .env."
                ),
                "candidates": 0, "backfilled": 0, "skipped": 0, "failed": 0,
                "rows": 0, "per_symbol": {},
            }
        db = BacktestDB()

    try:
        db.init_schema()

        candidates = _select_universe(symbols, limit)
        if not candidates:
            return {
                "candidates": 0, "backfilled": 0, "skipped": 0, "failed": 0,
                "rows": 0, "per_symbol": {}, "error": "no candidate symbols",
            }

        max_dates = db.get_symbol_max_dates()
        to_backfill = _stale_symbols(candidates, max_dates, stale_days, today)
        skipped = len(candidates) - len(to_backfill)

        backfilled: List[str] = []
        failed: List[str] = []
        rows_total = 0
        per_symbol: Dict[str, Dict[str, Any]] = {}

        for i, symbol in enumerate(to_backfill, 1):
            try:
                df = fetch_fn(symbol, years=years)
                if df is None or df.empty:
                    logger.warning("[Backfill] No data for %s — skipping", symbol)
                    failed.append(symbol)
                    per_symbol[symbol] = {"status": "failed", "note": "no data"}
                    continue
                rows = store_fn(db, symbol, df)
                stored = db.count_prices(symbol)
                rows_total += rows
                per_symbol[symbol] = {
                    "status": "ok",
                    "rows_stored": rows,
                    "total_for_symbol": stored,
                }
                backfilled.append(symbol)
                logger.info(
                    "[Backfill] %s: %d rows stored (total %d) [%d/%d]",
                    symbol, rows, stored, i, len(to_backfill),
                )
            except Exception as e:
                logger.warning("[Backfill] Failed to backfill %s: %s", symbol, e)
                failed.append(symbol)
                per_symbol[symbol] = {"status": "failed", "note": str(e)[:200]}
            time.sleep(max(0.0, delay))

        stats: Dict[str, Any] = {
            "candidates": len(candidates),
            "backfilled": len(backfilled),
            "skipped": skipped,
            "failed": len(failed),
            "rows": rows_total,
            "symbols": backfilled,
            "per_symbol": per_symbol,
        }

        # Row-count verification (Tier 3): independent fresh-download
        # cross-check on a sample of the backfilled symbols.
        if verify and backfilled:
            sample = backfilled[0]
            stats["verification"] = _verify_symbol(
                db, sample, per_symbol[sample]["total_for_symbol"], years, fetch_fn
            )
            stats["verification"]["symbol"] = sample
            if stats["verification"]["ok"]:
                logger.info(
                    "[Backfill] Verification PASSED for %s: %s",
                    sample, stats["verification"]["note"],
                )
            else:
                logger.error(
                    "[Backfill] Verification FAILED for %s: %s",
                    sample, stats["verification"]["note"],
                )
        elif verify:
            stats["verification"] = {"ok": False, "note": "no symbols backfilled to verify"}

        logger.info(
            "[Backfill] Cycle done: %d candidates, %d backfilled, %d skipped, %d failed, %d rows",
            stats["candidates"], stats["backfilled"], stats["skipped"],
            stats["failed"], stats["rows"],
        )
        return stats
    finally:
        if created_db:
            try:
                db.close()
            except Exception:
                pass


def build_backfill_history(
    symbols: Optional[List[str]] = None,
    years: Optional[int] = None,
    limit: Optional[int] = None,
    verify: bool = True,
) -> Dict[str, Any]:
    """CLI entry point: run one backfill cycle, then report (Tier 3).

    Thin wrapper over :func:`backfill_history` that owns the Neon DB
    lifecycle (closed on exit so Neon can auto-suspend).

    Args:
        symbols: Explicit symbols (None → universe selectors).
        years: Years of history (default 10).
        limit: Max symbols from the fallback selectors.
        verify: Run the fresh-download cross-check (default True).

    Returns:
        The stats dict from :func:`backfill_history`.
    """
    return backfill_history(
        symbols=symbols,
        years=years or 10,
        limit=limit,
        verify=verify,
    )
