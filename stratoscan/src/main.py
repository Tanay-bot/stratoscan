"""Stratoscan — AI-Powered Stock Screener & Backtesting Platform.

Main entry point. Run this module to start the interactive CLI.

Usage:
    python -m src.main

Commands:
    screen <query>    Screen stocks using natural language
    backtest <query>  Backtest a strategy (free for everyone)
    enrich <query>    Screen + enrich with news/sentiment
    refresh           Run a one-time price data refresh
    scheduler         Start the background data refresh scheduler
    setup             Initialize database schemas
    serve             Start the FastAPI REST API server
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import List, Optional

from src.config import settings, validate_config
from src.database import BacktestDB, PostgresDB, SQLiteDB
from src.data_fetcher import initialize_client_pool, fetch_us_stock_symbols
from src.logging_config import configure_logging
from src.scheduler import DataRefreshScheduler

logger = logging.getLogger(__name__)

# ── CLI Commands ─────────────────────────────────────────────────────────────

def cmd_screen(args: argparse.Namespace) -> None:
    """Screen stocks using a natural language query.

    Usage: python -m src.main screen "find undervalued small-cap tech stocks"
    """
    from src.interpreter import interpret_query, format_filters_for_display
    from src.screener import StockScreener

    query = " ".join(args.query)
    if not query:
        print("❌ Please provide a query. Usage: screen <natural language query>")
        return

    print(f"🔍 Screening: \"{query}\"")
    print("─" * 60)

    interpreted = interpret_query(query)
    print(f"📋 Filters: {format_filters_for_display(interpreted)}\n")

    screener = StockScreener()
    report = screener.screen(interpreted, limit=args.limit or 50)

    print(report.summary)

    if getattr(args, "enrich", False) and report.results:
        print("\n" + "─" * 60)
        from src.enrichment import EnrichmentEngine
        engine = EnrichmentEngine()
        enrichment = engine.enrich_results(query, report.results)
        print(enrichment.summary)


def cmd_backtest(args: argparse.Namespace) -> None:
    """Backtest a strategy from a natural language query.

    Free for everyone (the old premium paywall was removed — Stratoscan
    is fully free).

    Usage: python -m src.main backtest "buy oversold tech stocks"
    """
    from src.interpreter import interpret_query
    from src.backtester import BacktestEngine

    query = " ".join(args.query)
    if not query:
        print("❌ Please provide a query. Usage: backtest <natural language query>")
        return

    print(f"\n📊 Backtesting: \"{query}\"")
    print("─" * 60)

    # Tier 4: light date validation — the API rejects malformed dates with a
    # 422; the CLI must not silently pass a bad string to the engine (which
    # would degrade to a confusing zero-result backtest).
    from datetime import datetime as _dt
    for label, value in (("--start", args.start), ("--end", args.end)):
        if value is not None:
            try:
                _dt.strptime(value, "%Y-%m-%d")
            except ValueError:
                print(f"❌ {label} must be YYYY-MM-DD, got '{value}'")
                return
    if args.start and args.end and args.start > args.end:
        print("❌ --start must not be after --end")
        return

    interpreted = interpret_query(query)
    engine = BacktestEngine()

    # Screen for top stocks first, then backtest (Tier 4: optional date range)
    results = engine.backtest_top_results(
        interpreted,
        top_n=args.top_n or 5,
        start_date=args.start,
        end_date=args.end,
    )

    if not results:
        print("❌ No backtest results. Check that symbols have historical data.")
        return

    for symbol, result in results.items():
        print(f"\n{result.summary}")
        print("─" * 40)


def cmd_refresh(args: argparse.Namespace) -> None:
    """Run a one-time data refresh (price data).

    Usage: python -m src.main refresh
    """
    print("🔄 Running price data refresh...")
    pool = initialize_client_pool()

    symbols = fetch_us_stock_symbols(pool)
    print(f"📡 Found {len(symbols)} US stock symbols")

    from src.data_fetcher import refresh_price_data
    records = refresh_price_data(pool, symbols)

    db = PostgresDB()
    db.bulk_upsert_stocks(records)

    print(f"✅ Updated {len(records)} stock records")


def cmd_setup(args: argparse.Namespace) -> None:
    """Initialize database schemas.

    Usage: python -m src.main setup
    """
    from src.config import _secure_env_permissions

    print("🔒 Securing .env file permissions...")
    _secure_env_permissions()

    # PostgreSQL via Alembic migrations
    print("\n📦 PostgreSQL — running Alembic migrations...")
    _run_alembic_migrations()

    # Backtesting database (dedicated Neon project when configured, else
    # the local SQLite dev file — never both as production storage)
    if settings.has_backtest_database:
        print("\n📊 Backtest database (Neon/PostgreSQL) — creating tables...")
        backtest_db = BacktestDB()
        backtest_db.init_schema()
        backtest_db.close()
        print("   ✅ historical_prices + backtest_results created in PostgreSQL")
    else:
        print("\n💾 Backtest database: BACKTEST_DATABASE_URL not set — using local SQLite for dev")
        sqlite = SQLiteDB()
        sqlite.init_schema()
        print(f"   Database path: {settings.backtest_db_path}")

    # News database (separate Neon project)
    if settings.has_news_database:
        print("\n📰 News database — creating tables...")
        from src.database import NewsDB

        news_db = NewsDB()
        news_db.init_schema()
        news_db.close()
        print("   ✅ stock_sentiment + news_articles created")
    else:
        print("\n📰 News database: skipped (NEWS_DATABASE_URL not set)")

    print("\n✅ Setup complete!")


def cmd_refresh_news(args: argparse.Namespace) -> None:
    """Run a one-time news + sentiment refresh.

    Usage: python -m src.main refresh-news [--limit N]
    """
    if not settings.has_news_credentials:
        print("❌ News pipeline not configured. Set NEWS_FINNHUB_KEY and NEWS_DATABASE_URL in .env")
        return

    print("📰 Running news + sentiment refresh...")
    from src.news import refresh_news_batch
    from src.database import NewsDB

    db = NewsDB()
    db.init_schema()

    # Refresh the top-N stocks by market cap (news matters most for liquid names)
    limit = settings.news_symbols_limit if getattr(args, "limit", None) is None else args.limit
    symbols: List[str] = []
    try:
        from src.database import PostgresDB
        symbols = PostgresDB().get_top_symbols(limit)
    except Exception as e:
        print(f"⚠️  Could not fetch top symbols from screener DB ({e}) — using cached list")
    if not symbols:
        from src.data_fetcher import initialize_client_pool, fetch_us_stock_symbols
        symbols = fetch_us_stock_symbols(initialize_client_pool())[:limit]
    print(f"📡 Refreshing news for top {len(symbols)} stocks by market cap")

    stored = refresh_news_batch(symbols, db)
    db.close()

    print(f"✅ News refresh complete: {stored}/{len(symbols)} symbols stored")


def _run_alembic_migrations() -> None:
    """Run Alembic migrations to bring the PostgreSQL schema up to date.

    Equivalent to: alembic upgrade head
    """
    from alembic.config import Config
    from alembic import command

    try:
        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, "head")
        print("   ✅ Migrations up to date")
    except Exception as e:
        logger.error("Migration failed: %s", e)
        print(f"   ❌ Migration failed: {e}")


def cmd_scheduler(args: argparse.Namespace) -> None:
    """Start the background data refresh scheduler.

    Usage: python -m src.main scheduler
    """
    scheduler = DataRefreshScheduler()
    print("🔄 Starting Stratoscan data refresh scheduler...")
    print("   Press Ctrl+C to stop.\n")
    scheduler.start()


def cmd_digest(args: argparse.Namespace) -> None:
    """Run the weekly strategy digest once (Day 10).

    Usage:
        python -m src.main digest --dry-run          # print, send nothing
        python -m src.main digest --user you@x.com   # just one person
        python -m src.main digest                    # the real pass

    The same pass the scheduler runs daily: at most one email per user per
    ISO week (a second run this week skips anyone already sent).
    """
    from src.digest import run_weekly_digest

    report = run_weekly_digest(dry_run=args.dry_run, only_email=args.user)
    for p in report.previews:
        print(f"\n── To: {p['to']}\nSubject: {p['subject']}\n\n{p['text']}\n")
    print(f"📬 Digest: {report.summary()}")


def cmd_backfill_history(args: argparse.Namespace) -> None:
    """Bulk-load historical OHLCV into the Neon backtest DB (Tier 3).

    Usage: python -m src.main backfill-history [--symbols AAPL,MSFT] [--years 10]

    Pre-fills ``BACKTEST_DATABASE_URL`` with years of yfinance daily OHLCV
    so a backtest never waits on a cold download. Resumable + verified:
    re-runs skip fresh symbols, and a sample symbol is cross-checked
    against a fresh yfinance download (row count + coverage).
    """
    from src.backfill import build_backfill_history

    symbols = None
    if getattr(args, "symbols", None):
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    years = max(1, min(getattr(args, "years", 10), 30))
    limit = getattr(args, "limit", None)
    verify = not getattr(args, "no_verify", False)

    if symbols:
        print(f"📥 Backfilling {len(symbols)} explicit symbols ({years}y each)...")
    else:
        print(f"📥 Backfilling universe ({years}y each, limit={limit or 'all'})...")

    stats = build_backfill_history(symbols=symbols, years=years, limit=limit, verify=verify)

    if stats.get("error"):
        print(f"❌ {stats['error']}")
        return

    print(
        f"✅ Backfill complete: {stats.get('backfilled', 0)} backfilled, "
        f"{stats.get('skipped', 0)} skipped, "
        f"{stats.get('failed', 0)} failed, "
        f"{stats.get('rows', 0):,} rows stored "
        f"({stats.get('candidates', 0)} candidates)"
    )
    if stats.get("per_symbol"):
        print("\nPer-symbol:")
        for sym, info in stats["per_symbol"].items():
            if info.get("status") == "ok":
                print(f"  ✅ {sym}: {info.get('total_for_symbol', 0):,} rows")
            else:
                print(f"  ❌ {sym}: {info.get('note', 'failed')}")
    if stats.get("verification"):
        v = stats["verification"]
        icon = "✅" if v.get("ok") else "❌"
        print(
            f"\n{icon} Verification ({v.get('symbol', '?')}): "
            f"{v.get('note', 'n/a')}"
        )


def cmd_warm_cache(args: argparse.Namespace) -> None:
    """Warm the backtest history cache for the top screener symbols.

    Usage: python -m src.main warm-cache [--limit N]

    Pre-fetches 10 years of yfinance OHLCV for the top-N stocks by market
    cap into the backtest DB (see src/cache_warmer.py). The nightly
    scheduler job does this automatically; this command runs it once,
    on demand — useful right after a fresh deploy or a wiped backtest DB.
    """
    print("🔥 Warming backtest history cache...")
    from src.cache_warmer import build_backtest_history_cache

    stats = build_backtest_history_cache(limit=getattr(args, "limit", None))
    print(
        f"✅ Cache warmer complete: {stats.get('warmed', 0)} warmed, "
        f"{stats.get('skipped', 0)} skipped, "
        f"{stats.get('failed', 0)} failed "
        f"({stats.get('candidates', 0)} candidates)"
    )


def cmd_enrich(args: argparse.Namespace) -> None:
    """Screen stocks and enrich with news/sentiment.

    Usage: python -m src.main enrich "find undervalued small-cap tech stocks"

    Note: the ``enrich`` subparser has no ``--enrich`` flag (enriching IS
    its purpose), so we force it on before delegating to ``cmd_screen`` —
    otherwise ``cmd_screen``'s ``args.enrich`` lookup would raise
    AttributeError after burning an LLM call.
    """
    args.enrich = True
    cmd_screen(args)


def cmd_serve(args: argparse.Namespace) -> None:
    """Start the Stratoscan FastAPI REST API server.

    Usage: python -m src.main serve
           python -m src.main serve --port 8080
           python -m src.main serve --no-reload
    """
    try:
        import uvicorn
    except ImportError:
        print("❌ uvicorn is not installed. Run: pip install 'uvicorn[standard]'")
        return

    host = getattr(args, "host", "0.0.0.0")
    port = getattr(args, "port", 8000)
    reload_enabled = not getattr(args, "no_reload", False)

    print(f"🚀 Stratoscan API starting on http://{host}:{port}")
    # Docs are disabled in production (attack-surface reduction) — only
    # advertise the links when they actually exist.
    if not settings.is_production:
        print(f"   Docs: http://localhost:{port}/docs")
        print(f"   Redoc: http://localhost:{port}/redoc")
    if reload_enabled:
        print("   Hot reload enabled — edit code to restart automatically")

    uvicorn.run(
        "src.api:app",
        host=host,
        port=port,
        reload=reload_enabled,
        log_level=getattr(args, "log_level", "info"),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# CLI Parser
# ═══════════════════════════════════════════════════════════════════════════════

def create_parser() -> argparse.ArgumentParser:
    """Create the CLI argument parser.

    Returns:
        Configured ArgumentParser.
    """
    parser = argparse.ArgumentParser(
        prog="stratoscan",
        description="🚀 Stratoscan — AI-Powered Stock Screener & Backtesting Platform",
        epilog="Not a stock-picking service. A bridge from idea to candidate list.",
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose (debug) logging",
    )

    subparsers = parser.add_subparsers(
        title="commands",
        dest="command",
        help="Available commands",
    )

    # screen
    screen_parser = subparsers.add_parser(
        "screen",
        help="Screen stocks using natural language",
        description="Screen stocks using a natural language query.",
    )
    screen_parser.add_argument(
        "query",
        nargs="+",
        help="Natural language query (e.g., 'undervalued small-cap tech stocks')",
    )
    screen_parser.add_argument(
        "--limit", "-l",
        type=int,
        default=50,
        help="Maximum number of results (default: 50)",
    )
    screen_parser.add_argument(
        "--enrich", "-e",
        action="store_true",
        help="Also fetch news and sentiment for top results",
    )

    # backtest
    bt_parser = subparsers.add_parser(
        "backtest",
        help="Backtest a strategy (free for everyone)",
        description="Backtest a strategy using natural language.",
    )
    bt_parser.add_argument(
        "query",
        nargs="+",
        help="Natural language strategy description",
    )
    bt_parser.add_argument(
        "--top-n", "-n",
        type=int,
        default=5,
        help="Number of top stocks to backtest (default: 5)",
    )
    bt_parser.add_argument(
        "--start",
        default=None,
        help="Backtest start date YYYY-MM-DD (default: 10 years ago)",
    )
    bt_parser.add_argument(
        "--end",
        default=None,
        help="Backtest end date YYYY-MM-DD (default: today)",
    )

    # refresh
    subparsers.add_parser(
        "refresh",
        help="Run a one-time price data refresh",
    )

    # setup
    subparsers.add_parser(
        "setup",
        help="Initialize database schemas",
    )

    # scheduler
    subparsers.add_parser(
        "scheduler",
        help="Start the background data refresh scheduler",
    )

    # refresh-news
    refresh_news_parser = subparsers.add_parser(
        "refresh-news",
        help="Run a one-time news + sentiment refresh",
        description="Fetch news + LLM sentiment for top stocks and store in the news DB.",
    )
    refresh_news_parser.add_argument(
        "--limit", "-n",
        type=int,
        default=None,
        help="Max stocks to refresh (default: NEWS_SYMBOLS_LIMIT from .env)",
    )

    # warm-cache
    warm_cache_parser = subparsers.add_parser(
        "warm-cache",
        help="Warm the backtest history cache once",
        description="Pre-fetch yfinance history for the top screener symbols into the backtest DB.",
    )
    warm_cache_parser.add_argument(
        "--limit", "-n",
        type=int,
        default=None,
        help="Max symbols to warm (default: BACKTEST_CACHE_WARMER_LIMIT from .env)",
    )

    # digest (Day 10: weekly strategy email)
    digest_parser = subparsers.add_parser(
        "digest",
        help="Run the weekly saved-strategy digest once",
        description="Email each opted-in user what their saved strategies did since saving (once per ISO week).",
    )
    digest_parser.add_argument("--dry-run", action="store_true",
                               help="Compose and print the emails; send nothing, record nothing")
    digest_parser.add_argument("--user", default=None, help="Only this account's email")

    # backfill-history (Tier 3: Neon bulk load)
    backfill_parser = subparsers.add_parser(
        "backfill-history",
        help="Bulk-load N years of yfinance OHLCV into the Neon backtest DB",
        description=(
            "Pre-fill the dedicated backtest DB (BACKTEST_DATABASE_URL) with "
            "years of daily OHLCV for the whole universe (or a bounded subset) "
            "so backtests never wait on a cold download. Resumable: re-running "
            "skips symbols whose cached history is already fresh."
        ),
    )
    backfill_parser.add_argument(
        "--symbols",
        default=None,
        help="Comma-separated explicit symbols (e.g. AAPL,MSFT,NVDA). Default: screener top-N by market cap, then the yfinance universe.",
    )
    backfill_parser.add_argument(
        "--years",
        type=int,
        default=10,
        help="Years of history per symbol (default: 10)",
    )
    backfill_parser.add_argument(
        "--limit", "-n",
        type=int,
        default=None,
        help="Max symbols from the fallback universe selectors (default: all / screener top 1000)",
    )
    backfill_parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip the fresh-download row-count cross-check on a sample symbol",
    )

    # enrich (alias for screen --enrich)
    enrich_parser = subparsers.add_parser(
        "enrich",
        help="Screen stocks and enrich with news/sentiment",
        description="Screen stocks and attach news/sentiment data.",
    )
    enrich_parser.add_argument(
        "query",
        nargs="+",
        help="Natural language query",
    )
    enrich_parser.add_argument(
        "--limit", "-l",
        type=int,
        default=10,
        help="Maximum number of results (default: 10)",
    )

    # serve
    serve_parser = subparsers.add_parser(
        "serve",
        help="Start the FastAPI REST API server",
        description="Start the Stratoscan FastAPI REST API server.",
    )
    serve_parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind to (default: 8000)",
    )
    serve_parser.add_argument(
        "--no-reload",
        action="store_true",
        help="Disable auto-reload (use in production)",
    )
    serve_parser.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error", "critical"],
        help="Logging level (default: info)",
    )

    return parser


# ═══════════════════════════════════════════════════════════════════════════════
# Main Entry Point
# ═══════════════════════════════════════════════════════════════════════════════

def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point for Stratoscan.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code (0 for success, 1 for error).
    """
    parser = create_parser()
    args = parser.parse_args(argv)

    # Configure logging
    level = "DEBUG" if getattr(args, "verbose", False) else None
    configure_logging(level=level, force=True)

    # Validate configuration
    warnings = validate_config()
    for warning in warnings:
        logger.warning("⚠️  %s", warning)

    # Route commands
    command_dispatch = {
        "screen": cmd_screen,
        "backtest": cmd_backtest,
        "refresh": cmd_refresh,
        "refresh-news": cmd_refresh_news,
        "setup": cmd_setup,
        "scheduler": cmd_scheduler,
        "warm-cache": cmd_warm_cache,
        "digest": cmd_digest,
        "backfill-history": cmd_backfill_history,
        "enrich": cmd_enrich,
        "serve": cmd_serve,
    }

    if args.command in command_dispatch:
        command_dispatch[args.command](args)
        return 0
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
