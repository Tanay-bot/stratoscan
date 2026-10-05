"""Backtest cache warmer for Stratoscan.

Pre-fetches 10 years of yfinance OHLCV for the screener's top-N stocks
(by market cap) into the backtest database, so a premium backtest never
waits on a cold yfinance download at request time — the #1 latency
complaint against backtesting engines.

Why it matters (Tier 4.2 "pre-fetch cache warmer")
---------------------------------------------------
A user backtesting "AAPL" with a freshly-deployed backend used to trigger
a ~10s yfinance download inside the request. With the warmer running as a
background job (nightly, before the US open), the top ~100 names the
screener actually returns already have full history cached; a request-time
backtest reads Neon and runs in milliseconds.

Design (the Stratoscan way — near-zero marginal cost):
  - Selector: the screener's top-N symbols by market cap (the names users
    actually screen and backtest). One cheap query.
  - Planner: one ``get_symbol_max_dates()`` query against the backtest DB
    decides which symbols are stale (newest bar older than
    ``backtest_cache_warmer_stale_days``) or missing entirely — no
    per-symbol DB round-trips.
  - Fetcher: ``fetch_historical_data`` + ``store_historical_data`` from
    ``src.backtester`` (the SAME code the request path uses, so warmed
    data is byte-identical to on-demand data). One yfinance download per
    stale symbol, polite pause between downloads.
  - Resilience: any single failure is logged and skipped; the cycle never
    aborts mid-way. Missing backtest DB / empty screener DB degrades to a
    no-op, not a crash.

The job is registered in :data:`src.job_monitor.JOB_DEFINITIONS` as
``backtest_cache_warm`` and scheduled by the scheduler.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src.config import settings
from src.database import BacktestDB, SQLiteDB

logger = logging.getLogger(__name__)


def _backtest_db() -> Any:
    """Return the backtest storage the warmer writes into.

    Mirrors ``BacktestEngine.__init__``: production uses the dedicated
    Neon backtest DB (``BACKTEST_DATABASE_URL``); local dev / hermetic
    tests fall back to the SQLite file. Both expose the duck-typed
    contract (``get_symbol_max_dates``, ``insert_historical_prices``).
    """
    return BacktestDB() if settings.has_backtest_database else SQLiteDB()


def _select_target_symbols(
    screener_db: Any,
    limit: int,
) -> List[str]:
    """Pick the symbols to warm: the screener's top-N by market cap.

    Falls back to a broad symbol list if the screener DB is unreachable
    or empty (fresh install) so the warm cycle still does useful work.

    Args:
        screener_db: PostgresDB-compatible client (``get_top_symbols``).
        limit: Max symbols to consider.

    Returns:
        List of ticker symbols (deduplicated, order preserved).
    """
    symbols: List[str] = []
    try:
        symbols = screener_db.get_top_symbols(limit)
    except Exception as e:
        logger.warning(
            "[Cache Warmer] Screener DB top-symbols query failed: %s", e
        )
        symbols = []
    if not symbols:
        try:
            from src.data_fetcher import fetch_us_stock_symbols
            from src.data_fetcher import initialize_client_pool

            all_symbols = fetch_us_stock_symbols(initialize_client_pool())
            symbols = all_symbols[:limit]
            logger.info(
                "[Cache Warmer] Screener DB empty — fell back to %d universe symbols",
                len(symbols),
            )
        except Exception as e:
            logger.warning(
                "[Cache Warmer] Universe fallback failed too: %s — nothing to warm", e
            )
            symbols = []
    # Dedupe while preserving order (defensive: the DB could return dupes).
    seen: set = set()
    out: List[str] = []
    for s in symbols:
        s = str(s).strip().upper()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _stale_symbols(
    candidates: List[str],
    max_dates: Dict[str, Optional[str]],
    stale_days: int,
    today: str,
) -> List[str]:
    """Which candidate symbols need a refresh (missing or stale history).

    Args:
        candidates: Symbols to consider warming.
        max_dates: ``symbol -> newest cached date`` map from
            ``get_symbol_max_dates()``.
        stale_days: Refresh when the newest bar is older than this.
        today: Today's date (YYYY-MM-DD) for the staleness comparison.

    Returns:
        The subset of ``candidates`` whose history is missing or stale.
    """
    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=stale_days)).strftime(
        "%Y-%m-%d"
    )
    stale: List[str] = []
    for symbol in candidates:
        newest = max_dates.get(symbol)
        if newest is None or newest < cutoff:
            stale.append(symbol)
    return stale


def warm_backtest_cache(
    backtest_db: Optional[Any] = None,
    screener_db: Optional[Any] = None,
    limit: Optional[int] = None,
    stale_days: Optional[int] = None,
    delay: Optional[float] = None,
    today: Optional[str] = None,
    fetch_fn=None,
    store_fn=None,
) -> Dict[str, Any]:
    """Run one warm cycle: pre-fetch history for the screener's top symbols.

    Pure orchestration over the existing backtester primitives — the same
    ``fetch_historical_data`` / ``store_historical_data`` the request path
    uses, so warmed data is identical to on-demand data.

    Args:
        backtest_db: Backtest storage (BacktestDB | SQLiteDB). Defaults to
            the production/dev pick (``BACKTEST_DATABASE_URL`` → Neon).
        screener_db: PostgresDB-compatible client for the top-symbols
            selector. Defaults to a fresh ``PostgresDB``.
        limit: Max symbols to warm per cycle (defaults to
            ``settings.backtest_cache_warmer_limit``).
        stale_days: Refresh threshold (defaults to
            ``settings.backtest_cache_warmer_stale_days``).
        delay: Pause between yfinance downloads (defaults to
            ``settings.backtest_cache_warmer_delay``).
        today: Date override for hermetic tests (YYYY-MM-DD).
        fetch_fn / store_fn: Overridable primitives for hermetic tests
            (default to ``fetch_historical_data`` / ``store_historical_data``).

    Returns:
        Dict with cycle stats:
          - ``candidates`` (int): symbols considered.
          - ``warmed`` (int): symbols whose history was fetched + stored.
          - ``skipped`` (int): candidates already fresh (no download).
          - ``failed`` (int): candidates whose download/store failed.
          - ``symbols`` (list): the warmed symbol list (for logging/monitor).
    """
    from src.backtester import fetch_historical_data, store_historical_data
    from src.database import PostgresDB

    fetch_fn = fetch_fn or fetch_historical_data
    store_fn = store_fn or store_historical_data
    limit = settings.backtest_cache_warmer_limit if limit is None else limit
    stale_days = (
        settings.backtest_cache_warmer_stale_days if stale_days is None else stale_days
    )
    delay = settings.backtest_cache_warmer_delay if delay is None else delay
    today = today or datetime.now().strftime("%Y-%m-%d")

    # Ownership: DBs the warmer creates ITSELF are closed before returning —
    # a lingering pool in the long-running scheduler would keep the Neon
    # compute endpoint awake 24/7 and defeat the auto-suspend design (the
    # same rule job_monitor's PostgresJobMonitorStore enforces). Callers
    # that pass their own DB (tests, CLI wrapper) keep ownership.
    created_backtest = backtest_db is None
    created_screener = screener_db is None

    def _close_owned() -> None:
        """Close the backtest + screener DBs this call created (if any)."""
        if created_backtest and backtest_db is not None:
            try:
                backtest_db.close()
            except Exception:
                pass
        if created_screener and screener_db is not None:
            try:
                screener_db.close()
            except Exception:
                pass

    try:
        if backtest_db is None:
            try:
                backtest_db = _backtest_db()
                backtest_db.init_schema()
            except Exception as e:
                logger.warning(
                    "[Cache Warmer] Backtest DB unavailable (%s) — cycle skipped", e
                )
                return {
                    "candidates": 0, "warmed": 0, "skipped": 0, "failed": 0,
                    "symbols": [], "error": f"backtest db unavailable: {e}",
                }
        if screener_db is None:
            try:
                screener_db = PostgresDB()
            except Exception as e:
                logger.warning(
                    "[Cache Warmer] Screener DB unavailable (%s) — cycle skipped", e
                )
                return {
                    "candidates": 0, "warmed": 0, "skipped": 0, "failed": 0,
                    "symbols": [], "error": f"screener db unavailable: {e}",
                }

        candidates = _select_target_symbols(screener_db, limit)
        if not candidates:
            logger.warning("[Cache Warmer] No candidate symbols — cycle skipped")
            return {
                "candidates": 0, "warmed": 0, "skipped": 0, "failed": 0,
                "symbols": [], "error": "no candidate symbols",
            }

        # One staleness query plans the whole cycle (no per-symbol round-trips).
        try:
            max_dates = backtest_db.get_symbol_max_dates()
        except Exception as e:
            logger.warning(
                "[Cache Warmer] Staleness query failed (%s) — warming everything", e
            )
            max_dates = {}
        to_warm = _stale_symbols(candidates, max_dates, stale_days, today)

        warmed: List[str] = []
        failed: List[str] = []
        for symbol in to_warm:
            try:
                df = fetch_fn(symbol)
                if df is None or df.empty:
                    logger.warning(
                        "[Cache Warmer] No data for %s — skipping", symbol
                    )
                    failed.append(symbol)
                    continue
                store_fn(backtest_db, symbol, df)
                warmed.append(symbol)
                logger.info(
                    "[Cache Warmer] Warmed %s (%d bars)", symbol, len(df)
                )
            except Exception as e:
                logger.warning(
                    "[Cache Warmer] Failed to warm %s: %s", symbol, e
                )
                failed.append(symbol)
            # Politeness pause so a full cycle never hammers Yahoo.
            time.sleep(max(0.0, delay))

        skipped = len(candidates) - len(to_warm)
        stats = {
            "candidates": len(candidates),
            "warmed": len(warmed),
            "skipped": skipped,
            "failed": len(failed),
            "symbols": warmed,
        }
        logger.info(
            "[Cache Warmer] Cycle done: %d candidates, %d warmed, %d skipped, %d failed",
            stats["candidates"], stats["warmed"], stats["skipped"], stats["failed"],
        )
        return stats
    finally:
        _close_owned()


def build_backtest_history_cache(
    limit: Optional[int] = None,
    today: Optional[str] = None,
) -> Dict[str, Any]:
    """CLI entry point: warm the backtest history cache once, then report.

    Thin wrapper over :func:`warm_backtest_cache` that closes the backtest
    DB pool afterwards (Neon auto-suspend) and returns the same stats dict.

    Args:
        limit: Max symbols to warm (defaults to the configured limit).
        today: Date override for hermetic tests.

    Returns:
        The stats dict from :func:`warm_backtest_cache`.
    """
    db = _backtest_db()
    try:
        # Self-heal the schema BEFORE warming: unlike the scheduler path
        # (warm_backtest_cache with no db constructs + init_schema itself),
        # this wrapper passes the db explicitly, so the idempotent
        # CREATE TABLE IF NOT EXISTS must be issued here — otherwise a
        # fresh deploy / wiped backtest DB (the exact scenario this CLI
        # exists for) would crash on "no such table: historical_prices".
        # Graceful degradation matches the rest of the codebase: a dead
        # Neon backtest DB returns an error stats dict (CLI prints it)
        # instead of a raw traceback.
        db.init_schema()
        return warm_backtest_cache(backtest_db=db, limit=limit, today=today)
    except Exception as e:
        logger.warning("[Cache Warmer] Backtest DB unavailable (%s)", e)
        return {
            "candidates": 0, "warmed": 0, "skipped": 0, "failed": 0,
            "symbols": [], "error": f"backtest db unavailable: {e}",
        }
    finally:
        try:
            db.close()
        except Exception:
            pass


def backfill_volume_from_history(
    backtest_db: Optional[Any] = None,
    screener_db: Optional[Any] = None,
    today: Optional[str] = None,
    max_age_days: int = 10,
) -> Dict[str, Any]:
    """Day 14: fill ``stock_master.volume`` / ``avg_volume`` from stored bars.

    Finnhub's quote carries no volume, so the screener's volume filter and
    sort matched nothing. The daily bars the backtester already stores carry
    it: the latest session's volume and the trailing 63-session average
    (Finnhub's own avgVolume window). Only symbols with a bar in the last
    ``max_age_days`` are written, and only onto rows that already exist.
    Pure database work — no API calls. DBs it creates are closed after.
    """
    from datetime import timedelta

    from src.database import PostgresDB

    created_bt, created_main = backtest_db is None, screener_db is None
    backtest_db = backtest_db or _backtest_db()
    screener_db = screener_db or PostgresDB()
    try:
        now = datetime.strptime(today, "%Y-%m-%d") if today else datetime.now()
        fresh_cutoff = (now - timedelta(days=max_age_days)).strftime("%Y-%m-%d")
        # 63 sessions ≈ 92 calendar days; 120 leaves room for holidays.
        window_start = (now - timedelta(days=120)).strftime("%Y-%m-%d")
        rows = backtest_db.latest_volumes(window_start, fresh_cutoff)
        updated = screener_db.update_volumes(rows)
        logger.info("Volume backfill: %d symbols with recent bars, %d rows updated", len(rows), updated)
        return {"symbols": len(rows), "updated": updated}
    finally:
        for owned, db in ((created_bt, backtest_db), (created_main, screener_db)):
            if owned and hasattr(db, "close"):
                try:
                    db.close()
                except Exception:  # noqa: BLE001
                    pass

