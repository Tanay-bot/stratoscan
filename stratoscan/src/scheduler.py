"""Scheduler for background data refresh jobs.

Manages three recurring tasks:
  1. Price/Volume refresh: every 15 minutes for ~5,000 US stocks.
  2. Technical indicator refresh: every 60 minutes (RSI, SMA — slow-moving).
  3. Fundamental profile refresh: daily at 2:00 AM (sector, market cap, PE).

Neon auto-suspend optimization
-------------------------------
The DB connection pool is closed after every refresh cycle so Neon can
suspend the compute endpoint between cycles. This keeps monthly compute
time well within the free tier (100 hours/month).

Estimated compute usage with pool closure:
  - Each 15-min cycle: ~1 min active (wake + upsert + suspend delay)
  - Active ratio: 1/15 = ~6.7% → ~48 hours/month
  - User queries add ~1-2 hours/month
  - **Total: ~50 hours/month** — fits free tier with 50h headroom
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import List, Optional

import schedule

from src.config import settings
from src.data_fetcher import (
    FinnhubClientPool,
    initialize_client_pool,
    fetch_us_stock_symbols,
    refresh_price_data,
    refresh_fundamental_data,
)
from src.database import PostgresDB, NewsDB
from src.job_monitor import JobMonitor, get_job_monitor_store
from src.cache_warmer import backfill_volume_from_history, warm_backtest_cache

logger = logging.getLogger(__name__)

# ── Refresh Intervals ──────────────────────────────────────────────────────────

# How often to run the price refresh (minutes).
# Every 15 min keeps prices reasonably fresh while letting Neon suspend
# between cycles (5-min suspend timer + ~10 min suspended).
PRICE_REFRESH_MINUTES: int = 15

# Every Nth price refresh also includes technical indicators (RSI, SMA).
# RSI/SMA are slow-moving (calculated from 14/50/200 days of data) —
# refreshing them hourly instead of every 15 min has zero visible impact
# on screening accuracy.
TECHNICAL_REFRESH_INTERVAL: int = 4  # Every 4th cycle = every 60 min

# How often to run the news + sentiment refresh (minutes).
# Overridable via NEWS_REFRESH_MINUTES in .env. Runs on the DEDICATED
# news Finnhub key so it never competes with the price refresh.
NEWS_REFRESH_MINUTES: int = settings.news_refresh_minutes


class DataRefreshScheduler:
    """Manages background data refresh jobs using an in-process scheduler.

    Runs three jobs:
      - Price refresh (every ``PRICE_REFRESH_MINUTES`` minutes):
        updates prices and volumes from Finnhub.
      - Technical indicator refresh (every 4th price cycle):
        updates RSI-14, SMA-50, SMA-200 for the top 500 stocks.
      - Fundamental refresh (daily at 2:00 AM):
        updates profiles, sectors, market caps.

    The DB connection pool is closed after each job so Neon can auto-suspend
    the compute endpoint — saving on compute hours.
    """

    def __init__(
        self,
        pool: Optional[FinnhubClientPool] = None,
        db: Optional[PostgresDB] = None,
        news_db: Optional[NewsDB] = None,
        monitor: Optional[JobMonitor] = None,
    ) -> None:
        self._pool = pool or initialize_client_pool()
        self._db = db or PostgresDB()
        self._news_db = news_db or (NewsDB() if settings.has_news_database else None)
        self._monitor = monitor or JobMonitor(get_job_monitor_store())
        self._symbols: List[str] = []
        self._running = False
        self._refresh_count: int = 0

    def _get_symbols(self) -> List[str]:
        """Get the list of stock symbols, fetching them if not cached."""
        if not self._symbols:
            try:
                self._symbols = fetch_us_stock_symbols(self._pool)
                logger.info("Loaded %d stock symbols", len(self._symbols))
            except Exception as e:
                logger.error("Failed to fetch stock symbols: %s", e)
                # Fall back to symbols already in the database
                try:
                    self._symbols = self._db.get_all_symbols()
                    logger.info(
                        "Fell back to %d symbols from database", len(self._symbols)
                    )
                except Exception as e2:
                    logger.error("Fallback to DB symbols also failed: %s", e2)
                    self._symbols = []
        return self._symbols

    def job_refresh_prices(self) -> None:
        """Refresh price/volume data for all stocks.

        Runs every ``PRICE_REFRESH_MINUTES`` minutes.
        Fetches quotes from Finnhub (no DB needed) then opens the
        DB pool, upserts, and closes the pool immediately so Neon
        can suspend.
        """
        self._refresh_count += 1
        include_technicals = (
            self._refresh_count % TECHNICAL_REFRESH_INTERVAL == 0
        )

        label = "+technicals" if include_technicals else "quotes only"
        logger.info(
            "[Price Refresh #%d] Starting at %s (%s)",
            self._refresh_count,
            datetime.now(),
            label,
        )

        with self._monitor.run("price_refresh") as run:
            run.details = label
            symbols = self._get_symbols()

            if not symbols:
                run.status = "skipped"
                run.details = "no symbols to refresh"
                logger.warning("[Price Refresh] No symbols to refresh")
                return

            try:
                # Technical indicators must reach the names users actually
                # screen: the top-N by market cap (same universe as the news
                # job). The raw symbol list from fetch_us_stock_symbols is
                # ALPHABETICAL — slicing it would refresh the wrong 500
                # (measured: 243/500 vs 494/500 coverage on the live DB).
                # Best-effort: if the DB query fails, refresh_price_data
                # falls back to symbols[:500].
                tech_symbols = None
                if include_technicals:
                    try:
                        tech_symbols = self._db.get_top_symbols(500)
                    except Exception as e:
                        logger.debug(
                            "get_top_symbols failed (%s) — technicals fall back to symbols[:500]",
                            e,
                        )

                # Step 1: Fetch Finnhub data (no DB connection needed here)
                records = refresh_price_data(
                    self._pool,
                    symbols,
                    include_technicals=include_technicals,
                    tech_symbols=tech_symbols,
                )

                # Step 2: Open DB pool → upsert → close immediately
                # (This lets Neon suspend the compute endpoint between cycles)
                self._db.bulk_upsert_stocks(records)
                self._db.close()

                run.records = len(records)
                logger.info(
                    "[Price Refresh #%d] Complete: %d stocks updated (%s)",
                    self._refresh_count,
                    len(records),
                    label,
                )

                # Technical indicators ride on every 4th price cycle — record
                # them as their own monitored job so the cadence is visible.
                if include_technicals:
                    with self._monitor.run("technical_refresh") as trun:
                        trun.records = len(records)
                        trun.details = f"cycle #{self._refresh_count}"
            except Exception as e:
                run.status = "failed"
                run.error = str(e)
                logger.error(
                    "[Price Refresh #%d] Failed: %s", self._refresh_count, e
                )
                # Ensure pool is closed even on failure
                try:
                    self._db.close()
                except Exception as e2:
                    logger.debug("[Price Refresh] pool close failed: %s", e2)

    def job_refresh_fundamentals(self) -> None:
        """Refresh fundamental profile data for all stocks.

        Runs every 24 hours at 2:00 AM.
        Updates sector, industry, market cap, PE, beta, etc.
        """
        logger.info("[Fundamental Refresh] Starting at %s", datetime.now())

        with self._monitor.run("fundamental_refresh") as run:
            symbols = self._get_symbols()

            if not symbols:
                run.status = "skipped"
                run.details = "no symbols to refresh"
                logger.warning("[Fundamental Refresh] No symbols to refresh")
                return

            try:
                records = refresh_fundamental_data(self._pool, symbols)

                # Same pool-close pattern for Neon suspend
                self._db.bulk_upsert_stocks(records)
                self._db.close()

                run.records = len(records)
                logger.info(
                    "[Fundamental Refresh] Complete: %d profiles updated",
                    len(records),
                )
            except Exception as e:
                run.status = "failed"
                run.error = str(e)
                logger.error("[Fundamental Refresh] Failed: %s", e)
                try:
                    self._db.close()
                except Exception as e2:
                    logger.debug("[Fundamental Refresh] pool close failed: %s", e2)

    def job_refresh_news(self) -> None:
        """Refresh news + LLM sentiment for the top stocks.

        Runs every ``NEWS_REFRESH_MINUTES`` minutes.
        Uses the dedicated news Finnhub key (falls back to the shared pool)
        and scores per-article sentiment with the free LLM router. Stores
        results in the separate news database, then closes its pool so
        Neon can suspend between cycles.

        News DB pool is kept separate from the screener DB pool.
        """
        if self._news_db is None:
            logger.warning(
                "[News Refresh] Skipped — NEWS_DATABASE_URL not configured"
            )
            return

        logger.info("[News Refresh] Starting at %s", datetime.now())

        with self._monitor.run("news_refresh") as run:
            try:
                from src.news import refresh_news_batch

                # Refresh the top-N stocks by market cap (news matters most
                # for liquid names users actually screen). Falls back to the
                # cached symbol list if the screener DB is unreachable OR has
                # no rows (fresh install) — an empty list would silently skip
                # the cycle.
                symbols: List[str] = []
                try:
                    symbols = self._db.get_top_symbols(settings.news_symbols_limit)
                except Exception as e:
                    logger.debug("[News Refresh] top-symbols query failed: %s", e)
                    symbols = []
                if not symbols:
                    symbols = self._get_symbols()[: settings.news_symbols_limit]

                if not symbols:
                    run.status = "skipped"
                    run.details = "no symbols to refresh"
                    logger.warning("[News Refresh] No symbols to refresh")
                    return

                stored = refresh_news_batch(symbols, self._news_db)

                # Close the news pool so Neon can suspend between cycles
                self._news_db.close()

                run.records = stored
                run.details = f"{stored}/{len(symbols)} symbols stored"
                logger.info(
                    "[News Refresh] Complete: %d/%d symbols stored",
                    stored,
                    len(symbols),
                )
            except Exception as e:
                run.status = "failed"
                run.error = str(e)
                logger.error("[News Refresh] Failed: %s", e)
                try:
                    self._news_db.close()
                except Exception as e2:
                    logger.debug("[News Refresh] news pool close failed: %s", e2)

    def job_send_digest(self) -> None:
        """Daily pass of the weekly strategy digest (Day 10).

        Each user gets at most one email per ISO week (``digest_sends`` is
        the ledger), so running daily only means a provider outage today is
        retried tomorrow. All guarantees (lock, cap, circuit breaker,
        opt-out honoured mid-batch) live in ``src.digest.run_digest``; the
        pass closes every pool it opened so Neon can suspend.
        """
        if not settings.digest_enabled:
            logger.info("[Digest] Skipped — DIGEST_ENABLED=false")
            return
        if not settings.has_database_url:
            logger.info("[Digest] Skipped — DATABASE_URL not set")
            return
        from src.digest import run_weekly_digest

        with self._monitor.run("weekly_digest") as run:
            try:
                report = run_weekly_digest()
                run.records = report.sent
                run.details = report.summary()
                if report.aborted:
                    run.status = "failed"
                    run.error = "stopped: email provider failing"
                elif report.misconfigured:
                    run.status = "failed"  # alerts the operator; never silent
                    run.error = "not sent: SHARE_BASE_URL unset"
                logger.info("[Digest] %s", report.summary())
            except Exception as e:
                run.status = "failed"
                run.error = str(e)
                logger.error("[Digest] Failed: %s", e)

    def job_warm_backtest_cache(self) -> None:
        """Warm the backtest history cache for the screener's top symbols.

        Runs daily (``BACKTEST_CACHE_WARMER_TIME``, default 03:30) BEFORE
        the US market opens, so premium backtests read fully-warmed Neon
        history at request time instead of blocking on a cold yfinance
        download. Pre-fetches 10 years of OHLCV for the top-N symbols by
        market cap (``BACKTEST_CACHE_WARMER_LIMIT``, default 100), then
        closes the backtest DB pool so Neon can suspend.

        Degrades gracefully: no backtest DB → cycle skipped (logged); the
        request-time fetch fallback in BacktestEngine stays the safety net.
        """
        if not settings.backtest_cache_warmer_enabled:
            logger.info(
                "[Cache Warmer] Skipped — BACKTEST_CACHE_WARMER_ENABLED=false"
            )
            return
        if not settings.has_backtest_database:
            logger.info(
                "[Cache Warmer] Skipped — BACKTEST_DATABASE_URL not set "
                "(SQLite dev fallback stays on-demand)"
            )
            return

        logger.info("[Cache Warmer] Starting at %s", datetime.now())

        with self._monitor.run("backtest_cache_warm") as run:
            try:
                stats = warm_backtest_cache()
                run.records = stats.get("warmed", 0)
                run.details = (
                    f"{stats.get('warmed', 0)} warmed, "
                    f"{stats.get('skipped', 0)} skipped, "
                    f"{stats.get('failed', 0)} failed "
                    f"({stats.get('candidates', 0)} candidates)"
                )
                if stats.get("failed"):
                    run.status = "failed"
                    run.error = f"{stats['failed']} symbols failed to warm"
                # Day 14: the freshly warmed bars carry the volume the quote
                # API does not — copy it onto the screener rows (DB-only). Its
                # failure is noted, never allowed to fail the warm itself.
                try:
                    vol = backfill_volume_from_history()
                    run.details += f"; volume backfilled for {vol.get('updated', 0)} symbols"
                except Exception as exc:  # noqa: BLE001
                    logger.error("[Cache Warmer] Volume backfill failed: %s", exc)
                    run.details += "; volume backfill failed"
                logger.info(
                    "[Cache Warmer] Complete: %s", run.details
                )
            except Exception as e:
                run.status = "failed"
                run.error = str(e)
                logger.error("[Cache Warmer] Failed: %s", e)

    def start(self) -> None:
        """Start the scheduler. Runs indefinitely until interrupted."""
        if self._running:
            logger.warning("Scheduler is already running")
            return

        self._running = True

        # Schedule jobs
        schedule.every(PRICE_REFRESH_MINUTES).minutes.do(self.job_refresh_prices)
        schedule.every().day.at("02:00").do(self.job_refresh_fundamentals)
        if self._news_db is not None:
            schedule.every(NEWS_REFRESH_MINUTES).minutes.do(self.job_refresh_news)
        if settings.backtest_cache_warmer_enabled and settings.has_backtest_database:
            schedule.every().day.at(settings.backtest_cache_warmer_time).do(
                self.job_warm_backtest_cache
            )
            logger.info(
                "Backtest cache warmer scheduled daily at %s",
                settings.backtest_cache_warmer_time,
            )

        if settings.digest_enabled:
            schedule.every().day.at(settings.digest_time).do(self.job_send_digest)
            logger.info("Weekly digest pass scheduled daily at %s", settings.digest_time)

        # Run initial refresh immediately
        logger.info("Running initial price refresh...")
        self.job_refresh_prices()
        self._monitor.heartbeat()

        logger.info(
            "Scheduler started. "
            "Price refresh every %d min, "
            "technicals every %d cycles (%d min), "
            "fundamental refresh daily at 2:00 AM, "
            "news+sentiment refresh every %d min. "
            "DB pools close between cycles for Neon auto-suspend. "
            "Job runs + heartbeat recorded to the job monitor.",
            PRICE_REFRESH_MINUTES,
            TECHNICAL_REFRESH_INTERVAL,
            PRICE_REFRESH_MINUTES * TECHNICAL_REFRESH_INTERVAL,
            NEWS_REFRESH_MINUTES,
        )
        try:
            last_health_alert = 0.0
            while self._running:
                schedule.run_pending()
                # Heartbeat persists at most every N minutes (store-throttled)
                # so Neon can still suspend between refresh cycles.
                self._monitor.heartbeat()
                # Alert on a stale job / dead scheduler, but check at most
                # every 10 min — each check queries the job-monitor store,
                # and the alert itself is throttled per key in src/alerts.py.
                now = time.time()
                if now - last_health_alert >= 600:
                    last_health_alert = now
                    self._alert_if_stale()
                time.sleep(30)  # Check every 30 seconds
        except KeyboardInterrupt:
            logger.info("Scheduler stopped by user")
            self._running = False
        except Exception as e:
            logger.error("Scheduler error: %s", e)
            self._running = False

    def _alert_if_stale(self) -> None:
        """Alert the operator if any job is stale or the scheduler is dead.

        Tier 2 ops: a silently-dead refresh is the failure that otherwise
        goes unnoticed until a user screens stale data. Fires a throttled
        webhook alert (src/alerts.py) — never blocks the scheduler loop.
        """
        if not settings.alerts_enabled:
            return
        try:
            summary = self._monitor.summary()
            stale_jobs = [
                j["job_name"] for j in summary.get("jobs", []) if j.get("stale")
            ]
            scheduler_alive = summary.get("scheduler", {}).get("alive")
            if stale_jobs or not scheduler_alive:
                from src.alerts import send_alert

                detail = f"stale jobs: {stale_jobs or 'none'} | scheduler alive: {scheduler_alive}"
                send_alert(
                    "Scheduler health alert",
                    message=detail,
                    level="error",
                    throttle_key="scheduler-health",
                )
                logger.warning("Scheduler health alert sent: %s", detail)
        except Exception as e:
            # Alerting must never crash the scheduler loop.
            logger.warning("Scheduler health alert check failed: %s", e)

    def stop(self) -> None:
        """Gracefully stop the scheduler."""
        self._running = False
        logger.info("Scheduler stopping...")
