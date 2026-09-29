"""Database layer for Stratoscan.

Three PostgreSQL clients + a dev-only SQLite fallback:
  1. PostgresDB   — real-time screener stock data (psycopg2-binary).
  2. NewsDB       — news articles + sentiment (dedicated Neon project).
  3. BacktestDB   — historical OHLCV + backtest results (dedicated Neon
                    project; production storage for the backtesting
                    engine).
  4. SQLiteDB     — LOCAL DEV / TESTS ONLY fallback for the backtester
                    (single-writer embedded file is not production-safe).

The PostgreSQL layer connects DIRECTLY to the database (bypassing the
Supabase REST API), so any PostgreSQL provider works in production.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple

import psycopg2
import psycopg2.extras
import psycopg2.pool

from src.config import settings

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# Shared connection pools
# ═══════════════════════════════════════════════════════════════════════════════
#
# The API creates a FRESH client instance per request (every route handler
# does ``PostgresDB()`` / ``NewsDB()`` / ``BacktestDB()``). Originally each
# instance lazily opened its OWN ThreadedConnectionPool (minconn=1, maxconn=5)
# on first access — under concurrency that multiplied connections against the
# serverless Neon DB (N requests → up to 5N connections, each cold-starting),
# which the live throughput test exposed as /stocks requests hanging while
# single requests were fast. All instances of each client now share ONE
# module-level pool per database, created under a lock on first access.
# ``close()`` closes the shared pool and resets it to None so the next access
# re-creates it lazily — preserving the scheduler's "release connections so
# Neon can auto-suspend" design without a per-request pool.
_pool_lock = threading.Lock()
_pg_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
_news_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
_backtest_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None


@contextmanager
def _get_shared_conn(
    get_pool: Callable[[], Any],
) -> Generator[Any, None, None]:
    """Check a connection out of a lazily-created shared pool, close() safe.

    The scheduler deliberately calls ``close()`` after each refresh cycle so
    Neon can auto-suspend. With shared pools that closes the SAME pool API
    requests are using, and psycopg2's pool raises ``PoolError`` on a closed
    pool (``getconn``/``putconn`` both check ``self.closed``). A request that
    read the pool reference just before a concurrent ``close()`` would 500.

    Handle the race at the single choke point every query flows through:
    - ``getconn`` on a closed pool → the pool property recreates a fresh one
      (``close()`` reset the global to None under the lock) → retry once.
    - ``putconn`` on a pool closed mid-query → the connection is already
      dead (closeall closed it); discard instead of raising.

    Args:
        get_pool: Callable returning the current shared pool (typically a
            bound ``pool`` property — re-reads the global each call).

    Yields:
        A pooled psycopg2 connection, returned (or discarded) on exit.
    """
    # Neon auto-suspend can leave a pooled connection dead while it sits
    # idle (its TCP socket is gone). psycopg2 then raises InterfaceError
    # ("connection already closed") on the NEXT execute — which surfaced
    # as intermittent 502s on the first request after an idle window.
    # Probe each connection at checkout so a stale conn is discarded
    # BEFORE a request runs a query on it. Bound the retries so a
    # genuinely down database fails fast instead of hanging the request.
    attempts = 0
    max_attempts = 3
    conn = None
    while True:
        attempts += 1
        try:
            pool = get_pool()
            conn = pool.getconn()
            probe = conn.cursor()
            probe.execute("SELECT 1")
            probe.close()
        except (psycopg2.pool.PoolError, psycopg2.InterfaceError,
                psycopg2.OperationalError):
            if attempts >= max_attempts:
                raise
            # Discard the dead conn (closeall already closed it — ignore
            # putconn failures) and retry with a fresh one.
            if conn is not None:
                try:
                    pool.putconn(conn)
                except Exception:
                    pass
                conn = None
            continue
        break
    try:
        yield conn
    finally:
        try:
            pool.putconn(conn)
        except psycopg2.pool.PoolError:
            # Pool closed mid-flight: closeall already closed this conn.
            pass

# ═══════════════════════════════════════════════════════════════════════════════
# Schema Definitions
# ═══════════════════════════════════════════════════════════════════════════════

# ── PostgreSQL: stock_master table ──────────────────────────────────────
# Stores the universe of US stocks with their fundamental and pricing data.
#
# Columns beyond the original MVP set (Tier 2 screener realism): valuation
# ratios (ps_ratio, pb_ratio, ev_ebitda), profitability (roe_ttm, roa_ttm,
# gross_margin_ttm, net_margin_ttm), growth (revenue_growth_ttm,
# eps_growth_ttm) and extra technicals (macd_12_26, atr_14,
# bollinger_upper/lower). Each is populated by the fundamental/daily
# refresh and optional; a NULL value simply means "no data for this name"
# — filters on those columns naturally exclude rows without the field.
STOCK_MASTER_SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_master (
    symbol          TEXT PRIMARY KEY,
    company_name    TEXT NOT NULL,
    sector          TEXT,
    industry        TEXT,
    market_cap      BIGINT,
    current_price   DOUBLE PRECISION,
    price_change_pct DOUBLE PRECISION,
    volume          BIGINT,
    avg_volume      BIGINT,
    pe_ratio        DOUBLE PRECISION,
    eps_ttm         DOUBLE PRECISION,
    dividend_yield  DOUBLE PRECISION,
    beta            DOUBLE PRECISION,
    rsi_14          DOUBLE PRECISION,
    sma_50          DOUBLE PRECISION,
    sma_200         DOUBLE PRECISION,
    high_52w        DOUBLE PRECISION,
    low_52w         DOUBLE PRECISION,
    ps_ratio        DOUBLE PRECISION,
    pb_ratio        DOUBLE PRECISION,
    ev_ebitda       DOUBLE PRECISION,
    roe_ttm         DOUBLE PRECISION,
    roa_ttm         DOUBLE PRECISION,
    gross_margin_ttm DOUBLE PRECISION,
    net_margin_ttm  DOUBLE PRECISION,
    revenue_growth_ttm DOUBLE PRECISION,
    eps_growth_ttm  DOUBLE PRECISION,
    macd_12_26      DOUBLE PRECISION,
    atr_14          DOUBLE PRECISION,
    bollinger_upper DOUBLE PRECISION,
    bollinger_lower DOUBLE PRECISION,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
"""

STOCK_MASTER_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_sector ON stock_master(sector);",
    "CREATE INDEX IF NOT EXISTS idx_market_cap ON stock_master(market_cap);",
    "CREATE INDEX IF NOT EXISTS idx_pe_ratio ON stock_master(pe_ratio);",
    "CREATE INDEX IF NOT EXISTS idx_rsi ON stock_master(rsi_14);",
    "CREATE INDEX IF NOT EXISTS idx_volume ON stock_master(volume);",
    "CREATE INDEX IF NOT EXISTS idx_industry ON stock_master(industry);",
    "CREATE INDEX IF NOT EXISTS idx_avg_volume ON stock_master(avg_volume);",
    "CREATE INDEX IF NOT EXISTS idx_sma_50 ON stock_master(sma_50);",
    "CREATE INDEX IF NOT EXISTS idx_sma_200 ON stock_master(sma_200);",
    "CREATE INDEX IF NOT EXISTS idx_eps_ttm ON stock_master(eps_ttm);",
]

# Idempotent column backfill for databases created BEFORE the Tier-2
# screener upgrade shipped: ``CREATE TABLE IF NOT EXISTS`` never alters an
# existing table, so a stock_master created by an earlier fill is missing
# the valuation / profitability / growth / technical columns below, and any
# screen that computes the composite score (or writes them on refresh)
# would fail with "column does not exist" and silently return 0 matches.
# Same pattern as USERS_EMAIL_VERIFICATION_BACKFILL below — ADD COLUMN IF
# NOT EXISTS is a no-op on current tables and heals old ones.
STOCK_MASTER_COLUMN_BACKFILL = [
    "ALTER TABLE stock_master ADD COLUMN IF NOT EXISTS ps_ratio DOUBLE PRECISION",
    "ALTER TABLE stock_master ADD COLUMN IF NOT EXISTS pb_ratio DOUBLE PRECISION",
    "ALTER TABLE stock_master ADD COLUMN IF NOT EXISTS ev_ebitda DOUBLE PRECISION",
    "ALTER TABLE stock_master ADD COLUMN IF NOT EXISTS roe_ttm DOUBLE PRECISION",
    "ALTER TABLE stock_master ADD COLUMN IF NOT EXISTS roa_ttm DOUBLE PRECISION",
    "ALTER TABLE stock_master ADD COLUMN IF NOT EXISTS gross_margin_ttm DOUBLE PRECISION",
    "ALTER TABLE stock_master ADD COLUMN IF NOT EXISTS net_margin_ttm DOUBLE PRECISION",
    "ALTER TABLE stock_master ADD COLUMN IF NOT EXISTS revenue_growth_ttm DOUBLE PRECISION",
    "ALTER TABLE stock_master ADD COLUMN IF NOT EXISTS eps_growth_ttm DOUBLE PRECISION",
    "ALTER TABLE stock_master ADD COLUMN IF NOT EXISTS macd_12_26 DOUBLE PRECISION",
    "ALTER TABLE stock_master ADD COLUMN IF NOT EXISTS atr_14 DOUBLE PRECISION",
    "ALTER TABLE stock_master ADD COLUMN IF NOT EXISTS bollinger_upper DOUBLE PRECISION",
    "ALTER TABLE stock_master ADD COLUMN IF NOT EXISTS bollinger_lower DOUBLE PRECISION",
]

# Market-cap sanity sweep — the final layer of the OTC/ADR unit-confusion
# guard. The fetch-time guards live in data_fetcher.build_stock_record, but
# the enrich pass (bootstrap --no-quotes) and the scheduler's daily
# fundamental job call bulk_upsert_stocks DIRECTLY with raw Finnhub
# profiles (no build_stock_record), so a corrupt market cap gets re-written
# into the DB on every refresh. This sweep re-applies the same three rules
# against the STORED price (which the enrich path has on disk even though
# the incoming record doesn't):
#   1. Implied shares = mcap / price > 50B  (no company has >50B shares;
#      NVDA at ~24B is the largest ever — a corrupt cap implies 92B-500B)
#   2. F-suffix (foreign OTC pink-sheet) AND mcap > $100B  (every F-suffix
#      value above that in this dataset is corrupt: TOYOF=Toyota $32.6T vs
#      real $255B, NTDOF=Nintendo $8.85T vs ~$60B, SSNLF=Samsung $1.3Q vs
#      ~$400B; the largest legit F-suffix value is Ford "F" ~$58.5B)
#   3. Absolute mcap > $6T (largest real company ever is NVDA ~$4.86T)
# It runs INSIDE the same transaction as the upsert so a refresh can never
# leave corrupt values behind. Only executes when the incoming batch
# actually carries market_cap fields — the 10-minute price refresh (quotes
# only) skips it entirely, keeping the hot path free of the full-table scan.
MARKET_CAP_SANITY_SWEEP = """
    UPDATE stock_master
    SET market_cap = NULL
    WHERE market_cap IS NOT NULL
      AND current_price IS NOT NULL
      AND (
        (current_price > 0 AND market_cap::numeric / current_price::numeric > 50000000000)
        OR (symbol LIKE '%%F' AND market_cap > 100000000000)
        OR market_cap > 6000000000000
      )
"""

# Whitelist of every column the screener may reference in SQL expressions
# (column-to-column comparisons and sort keys). Column names from the LLM
# interpreter are NEVER interpolated into SQL directly — they must resolve
# against this set first, which makes the expression builder injection-safe.
STOCK_MASTER_COLUMNS: frozenset = frozenset({
    "symbol", "company_name", "sector", "industry", "market_cap",
    "current_price", "price_change_pct", "volume", "avg_volume",
    "pe_ratio", "eps_ttm", "dividend_yield", "beta", "rsi_14",
    "sma_50", "sma_200", "high_52w", "low_52w",
    "ps_ratio", "pb_ratio", "ev_ebitda", "roe_ttm", "roa_ttm",
    "gross_margin_ttm", "net_margin_ttm", "revenue_growth_ttm",
    "eps_growth_ttm", "macd_12_26", "atr_14", "bollinger_upper",
    "bollinger_lower", "updated_at",
})

def coverage_gap_columns(
    counts: Dict[str, int], min_pct: float = 50.0
) -> List[str]:
    """Columns whose non-null coverage is below ``min_pct``.

    Pure function over a ``{column: non_null_count}`` dict so the gate
    logic is unit-testable without a database. ``total`` is taken as the
    max count across the given columns (any column's count approximates
    total rows); a column at 0 count with everything else present is a
    guaranteed 0-candidate filter.

    Args:
        counts: Mapping of column name → non-null row count.
        min_pct: Minimum required non-null percentage (0-100).

    Returns:
        Sorted list of columns with coverage < min_pct.
    """
    if not counts:
        return []
    total = max(counts.values())
    # total == 0 means the table is empty — every column is a gap (a
    # zero-row universe can't satisfy any screen).
    if total <= 0:
        return sorted(counts.keys())
    return sorted(
        c for c, n in counts.items()
        if 100.0 * n / total < min_pct
    )


# ── SQLite: historical_prices table ────────────────────────────────────
# Stores 10 years of daily OHLCV data for backtesting.
#
# ``close`` is the split/dividend-ADJUSTED close (what the simulation trades
# on — adjusted prices already reflect splits/dividends, so a 2:1 split never
# looks like a -50% crash). ``raw_close`` is the unadjusted close, kept so the
# result can report price-only return vs total return (dividend contribution).
# ``dividends`` / ``stock_splits`` are stored for the same price-only-vs-total
# comparison (Tier 2 realism).
HISTORICAL_PRICES_SCHEMA = """
CREATE TABLE IF NOT EXISTS historical_prices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    date        TEXT NOT NULL,
    open        REAL NOT NULL,
    high        REAL NOT NULL,
    low         REAL NOT NULL,
    close       REAL NOT NULL,
    raw_close   REAL,
    volume      INTEGER NOT NULL,
    dividends   REAL DEFAULT 0.0,
    stock_splits REAL DEFAULT 1.0,
    UNIQUE(symbol, date)
);
"""

HISTORICAL_PRICES_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_hp_symbol ON historical_prices(symbol);",
    "CREATE INDEX IF NOT EXISTS idx_hp_date ON historical_prices(date);",
    "CREATE INDEX IF NOT EXISTS idx_hp_symbol_date ON historical_prices(symbol, date);",
]

# ── SQLite: backtest_results table ──────────────────────────────────────
# Caches backtest performance metrics.
BACKTEST_RESULTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS backtest_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_query_hash TEXT NOT NULL,
    strategy_params TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    start_date      TEXT NOT NULL,
    end_date        TEXT NOT NULL,
    initial_capital REAL NOT NULL,
    final_value     REAL NOT NULL,
    total_return    REAL NOT NULL,
    sharpe_ratio    REAL,
    max_drawdown    REAL,
    win_rate        REAL,
    total_trades    INTEGER,
    created_at      TEXT DEFAULT (datetime('now'))
);
"""

BACKTEST_RESULTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_br_query ON backtest_results(user_query_hash);",
    "CREATE INDEX IF NOT EXISTS idx_br_symbol ON backtest_results(symbol);",
]

# ── PostgreSQL: historical_prices table (dedicated backtest DB) ───────
# Mirrors the SQLite schema so the BacktestEngine sees the SAME storage
# contract whether it runs against the local dev file or production Neon.
# ``close`` is the split/dividend-ADJUSTED close (what the sim trades on);
# ``raw_close`` the unadjusted close for the price-only-vs-total-return
# report; ``dividends`` / ``stock_splits`` for the same Tier 2 comparison.
BACKTEST_HISTORICAL_PRICES_SCHEMA = """
CREATE TABLE IF NOT EXISTS historical_prices (
    id          BIGSERIAL PRIMARY KEY,
    symbol      TEXT NOT NULL,
    date        TEXT NOT NULL,
    open        DOUBLE PRECISION NOT NULL,
    high        DOUBLE PRECISION NOT NULL,
    low         DOUBLE PRECISION NOT NULL,
    close       DOUBLE PRECISION NOT NULL,
    raw_close   DOUBLE PRECISION,
    volume      BIGINT NOT NULL,
    dividends   DOUBLE PRECISION DEFAULT 0.0,
    stock_splits DOUBLE PRECISION DEFAULT 1.0,
    UNIQUE(symbol, date)
);
"""

BACKTEST_HISTORICAL_PRICES_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_hp_symbol ON historical_prices(symbol);",
    "CREATE INDEX IF NOT EXISTS idx_hp_date ON historical_prices(date);",
]

# ── PostgreSQL: backtest_results table (dedicated backtest DB) ─────────
BACKTEST_RESULTS_SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS backtest_results (
    id              BIGSERIAL PRIMARY KEY,
    user_query_hash TEXT NOT NULL,
    strategy_params TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    start_date      TEXT NOT NULL,
    end_date        TEXT NOT NULL,
    initial_capital DOUBLE PRECISION NOT NULL,
    final_value     DOUBLE PRECISION NOT NULL,
    total_return    DOUBLE PRECISION NOT NULL,
    sharpe_ratio    DOUBLE PRECISION,
    max_drawdown    DOUBLE PRECISION,
    win_rate        DOUBLE PRECISION,
    total_trades    INTEGER,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
"""

BACKTEST_RESULTS_INDEXES_PG = [
    "CREATE INDEX IF NOT EXISTS idx_br_query ON backtest_results(user_query_hash);",
    "CREATE INDEX IF NOT EXISTS idx_br_symbol ON backtest_results(symbol);",
]

# ── PostgreSQL (news DB): stock_sentiment table ───────────────────────
# Per-stock aggregate sentiment, scored by the free LLM pool (Finnhub's
# /news-sentiment is a paid-tier resource on free keys — verified live).
STOCK_SENTIMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_sentiment (
    symbol            TEXT PRIMARY KEY,
    buzz              DOUBLE PRECISION,
    company_news_score DOUBLE PRECISION,
    sector_news_score DOUBLE PRECISION,
    article_count     INTEGER DEFAULT 0,
    updated_at        TIMESTAMPTZ DEFAULT NOW()
);
"""

# ── PostgreSQL (news DB): news_articles table ─────────────────────────
# Individual articles with per-article LLM sentiment. UNIQUE(symbol, url)
# dedupes so hourly refreshes don't pile up duplicates.
NEWS_ARTICLES_SCHEMA = """
CREATE TABLE IF NOT EXISTS news_articles (
    id               BIGSERIAL PRIMARY KEY,
    symbol           TEXT NOT NULL,
    headline         TEXT NOT NULL,
    summary          TEXT,
    source           TEXT,
    url              TEXT,
    published_at     TIMESTAMPTZ,
    sentiment        TEXT,             -- "positive" / "negative" / "neutral"
    sentiment_score  DOUBLE PRECISION, -- -1.0 to 1.0 (per article)
    fetched_at       TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, url)
);
"""

NEWS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_news_symbol_time ON news_articles(symbol, published_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_news_fetched ON news_articles(fetched_at DESC);",
]

# ── PostgreSQL (main DB): users table ────────────────────────────────
# App-level accounts. Passwords are PBKDF2 hashes (never plaintext).
# ``plan`` defaults to 'free' and is kept for forward-compat, but there
# is NO paywall — Stratoscan is fully free (the Stripe billing layer was
# removed). See src/auth.py.
#
# Email verification (src/emails.py): every new account starts
# ``email_verified = False`` with a one-time random ``verification_token``
# and its ``verification_token_expires`` deadline. GET /auth/verify-email
# flips the flag on a valid token. Login may be blocked for unverified
# accounts when settings.email_verification_required is True.
#
# ``token_version`` is the session-revocation counter (src/auth.py):
# ``create_access_token`` stamps it as the ``ver`` claim and
# ``get_current_user`` rejects any token whose claim doesn't match the
# row, so incrementing it (POST /auth/logout-all) instantly invalidates
# every token the account ever issued.
USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                  BIGSERIAL PRIMARY KEY,
    email               TEXT NOT NULL UNIQUE,
    password_hash       TEXT NOT NULL,
    plan                TEXT NOT NULL DEFAULT 'free',
    email_verified      BOOLEAN NOT NULL DEFAULT FALSE,
    verification_token  TEXT,
    verification_token_expires TIMESTAMPTZ,
    is_admin            BOOLEAN NOT NULL DEFAULT FALSE,
    token_version       INTEGER NOT NULL DEFAULT 0,
    reset_token_hash    TEXT,
    reset_token_expires TIMESTAMPTZ,
    google_sub          TEXT UNIQUE,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
"""

# Idempotent column backfills for pre-verification databases: CREATE TABLE
# IF NOT EXISTS never alters an existing table, so the new columns are
# added here (ADD COLUMN IF NOT EXISTS) the same way the raw_close column
# is backfilled on SQLite. A column may be absent only in a DB created
# before this feature shipped; the migration is a no-op otherwise.
USERS_EMAIL_VERIFICATION_BACKFILL = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_token TEXT",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_token_expires TIMESTAMPTZ",
    # Grandfather existing accounts: rows created BEFORE email verification
    # shipped have no verification_token — mark them verified so a future
    # EMAIL_VERIFICATION_REQUIRED=true never locks out pre-existing users.
    # New signups always carry a token (unverified), so this UPDATE never
    # touches them; it only flips the legacy no-token rows.
    "UPDATE users SET email_verified = TRUE "
    "WHERE verification_token IS NULL AND email_verified = FALSE",
    # Admin role column (gates the /admin dashboard). Idempotent — no-op
    # on databases that already have it.
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE",
]

# Session-revocation counter (src/auth.py / POST /auth/logout-all). Same
# idempotent-backfill contract as above: this is what heals a database
# created before the column existed (the Alembic revision covers managed
# ones, this covers a bare deploy — see CLAUDE.md §18).
USERS_TOKEN_VERSION_BACKFILL = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0",
]

# Password reset (POST /auth/password-reset/*). Only the SHA-256 of the
# emailed token is stored, never the token itself, so a read of this table
# (a leaked backup, a SQL-injection elsewhere) yields nothing usable. One
# hash per user: requesting a second reset overwrites the first, which is
# what makes "only the most recent link works" true without any bookkeeping.
USERS_PASSWORD_RESET_BACKFILL = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token_hash TEXT",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token_expires TIMESTAMPTZ",
    "CREATE INDEX IF NOT EXISTS idx_users_reset_token_hash ON users (reset_token_hash)",
]

# Google identity ("Continue with Google", src/google_oauth.py).
# ``google_sub`` is Google's subject id: stable forever for an account, and
# therefore the thing that identifies a returning sign-in — a Google email
# can change, the sub cannot. UNIQUE so two rows can never claim one Google
# identity (Postgres permits any number of NULLs in a unique index, so every
# email/password account is unaffected). NULL means "no Google identity".
USERS_GOOGLE_SUB_BACKFILL = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS google_sub TEXT",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_sub ON users (google_sub)",
]

# ── PostgreSQL (main DB): job_runs table ─────────────────────────────
# Background-job monitoring (src/job_monitor.py). One row per job
# execution: status (running/success/failed/skipped), timestamps,
# duration, record count and error. Queried by GET /ops/jobs.
JOB_RUNS_SCHEMA = """
CREATE TABLE IF NOT EXISTS job_runs (
    id          BIGSERIAL PRIMARY KEY,
    job_name    TEXT NOT NULL,
    status      TEXT NOT NULL,            -- running | success | failed | skipped
    started_at  TIMESTAMPTZ DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    duration_ms DOUBLE PRECISION,
    records     INTEGER,
    details     TEXT,
    error       TEXT
);
"""

JOB_RUNS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_job_runs_name_time "
    "ON job_runs(job_name, started_at DESC);",
]

# ── PostgreSQL (main DB): scheduler_heartbeat table ──────────────────
# Single-row liveness beacon (id=1). The scheduler refreshes it on a
# throttled cadence (settings.job_heartbeat_persist_minutes) so Neon can
# still auto-suspend between refresh cycles; a stale row means the
# scheduler process itself is dead or stuck.
SCHEDULER_HEARTBEAT_SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduler_heartbeat (
    id           SMALLINT PRIMARY KEY DEFAULT 1,
    last_beat_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    pid          INTEGER
);
"""

# ── PostgreSQL (main DB): backtest_jobs table ─────────────────────
# Per-user async backtest job persistence. The in-memory _BACKTEST_JOBS
# dict is the fast path; this table is the durability layer: on restart,
# any job with status='running' is marked 'interrupted' so the UI shows
# "Server restarted" instead of "Job not found". The result JSONB is
# stored so completed jobs can be retrieved from DB even after the
# in-memory entry is evicted.
BACKTEST_JOBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS backtest_jobs (
    job_id      TEXT PRIMARY KEY,
    user_email  TEXT NOT NULL,
    query       TEXT,
    status      TEXT NOT NULL DEFAULT 'queued',   -- queued | running | done | error | interrupted
    phase       TEXT,
    done_symbols JSONB DEFAULT '[]'::jsonb,
    total_symbols INTEGER DEFAULT 0,
    result      JSONB,
    error       TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
"""

BACKTEST_JOBS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_backtest_jobs_user "
    "ON backtest_jobs(user_email, created_at DESC);",
]

# ── PostgreSQL (main DB): user_data table ──────────────────────────
# Per-user saved state synced from the frontend: watchlist, saved
# screeners and recent backtests. This was localStorage-only for a long
# time — per-device, lost on another browser, invisible to the operator.
# Now the frontend hydrates from and pushes to GET/PUT /me/personal, with
# localStorage kept as an offline cache. JSONB blobs (frontend already
# stores them as plain JSON) keyed by user with ON DELETE CASCADE.
USER_DATA_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_data (
    user_id          BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    watchlist        JSONB NOT NULL DEFAULT '[]'::jsonb,
    saved_screeners  JSONB NOT NULL DEFAULT '[]'::jsonb,
    recent_backtests JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);
"""


# ═══════════════════════════════════════════════════════════════════════════════
# PostgreSQL Client (Screener)
# ═══════════════════════════════════════════════════════════════════════════════

class PostgresDB:
    """Direct PostgreSQL connection for the stock screener database.

    Connects using a standard DATABASE_URL connection string.
    Compatible with Supabase PostgreSQL, RDS, Neon, Railway, or any
    PostgreSQL provider — just swap the connection string.

    Uses a simple connection pool for thread safety.
    """

    def __init__(self) -> None:
        # No per-instance pool — ALL PostgresDB instances share one
        # module-level pool (see the "Shared connection pools" header), so
        # the per-request ``PostgresDB()`` in every API route does not open
        # a fresh pool (and fresh Neon connections) on each request.
        # Same libpq sanitization as NewsDB: Neon URLs sometimes carry
        # channel_binding=require which breaks psycopg2 pooled connections
        # with spurious SSL errors. Strip it defensively.
        self._database_url = _sanitize_dsn(settings.database_url)

    @property
    def pool(self) -> psycopg2.pool.ThreadedConnectionPool:
        """Return the shared PostgreSQL connection pool (lazily created)."""
        global _pg_pool
        if _pg_pool is None:
            with _pool_lock:
                if _pg_pool is None:
                    if not self._database_url:
                        raise RuntimeError(
                            "PostgreSQL is not configured. Set DATABASE_URL in your .env file."
                        )
                    try:
                        _pg_pool = psycopg2.pool.ThreadedConnectionPool(
                            minconn=1,
                            maxconn=5,
                            dsn=self._database_url,
                            # Don't let a pooled getconn() hang forever on a cold
                            # serverless DB (Neon suspend) — same budget as the
                            # /health probe. psycopg2 passes kwargs to psycopg2.connect.
                            connect_timeout=settings.db_connect_timeout,
                        )
                        logger.info(
                            "PostgreSQL connection pool initialized (min=1, max=5, "
                            "connect_timeout=%ss)",
                            settings.db_connect_timeout,
                        )
                    except Exception as e:
                        logger.error("Failed to connect to PostgreSQL: %s", e)
                        raise RuntimeError(f"PostgreSQL connection failed: {e!s}") from e
        return _pg_pool

    @contextmanager
    def _get_conn(self) -> Generator[psycopg2.extensions.connection, None, None]:
        """Get a connection from the shared pool and return it when done."""
        # ``lambda: self.pool`` re-reads the shared global on every retry so
        # a concurrent close() (scheduler auto-suspend) never 500s a query.
        with _get_shared_conn(lambda: self.pool) as conn:
            yield conn

    def init_schema(self) -> None:
        """Create the stock_master + users tables and indexes if they don't exist.

        This is safe to call multiple times — uses IF NOT EXISTS.
        """
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(STOCK_MASTER_SCHEMA)
                # Heal stock_master tables created before the Tier-2 upgrade
                # (ADD COLUMN IF NOT EXISTS — no-op on current tables).
                for stmt in STOCK_MASTER_COLUMN_BACKFILL:
                    try:
                        cur.execute(stmt)
                    except Exception as e:
                        logger.debug(
                            "stock_master backfill skipped (%s): %s",
                            stmt.split()[8], e,
                        )
                for idx in STOCK_MASTER_INDEXES:
                    cur.execute(idx)
                cur.execute(USERS_SCHEMA)
                # Backfill users columns added after the table shipped
                # (email verification, is_admin, token_version) — each
                # statement independently guarded so one failure can't
                # abort the rest; no-ops on fresh installs.
                for stmt in (
                    USERS_EMAIL_VERIFICATION_BACKFILL
                    + USERS_TOKEN_VERSION_BACKFILL
                    + USERS_PASSWORD_RESET_BACKFILL
                    + USERS_GOOGLE_SUB_BACKFILL
                ):
                    try:
                        cur.execute(stmt)
                    except Exception as e:
                        logger.debug(
                            "users backfill skipped (%s): %s", stmt.split()[2], e
                        )
                cur.execute(JOB_RUNS_SCHEMA)
                for idx in JOB_RUNS_INDEXES:
                    cur.execute(idx)
                cur.execute(SCHEDULER_HEARTBEAT_SCHEMA)
                cur.execute(USER_DATA_SCHEMA)
                cur.execute(BACKTEST_JOBS_SCHEMA)
                for idx in BACKTEST_JOBS_INDEXES:
                    cur.execute(idx)
            conn.commit()
        logger.info("PostgreSQL stock_master + users + job monitoring + backtest_jobs schema initialized")

    # ── User CRUD (auth) ──────────────────────────────────────────────

    def create_user(
        self,
        email: str,
        password_hash: str,
        plan: str = "free",
        email_verified: bool = False,
        verification_token: Optional[str] = None,
        verification_token_expires: Optional[str] = None,
    ) -> int:
        """Create a new user account.

        Args:
            email: Unique user email (lowercased).
            password_hash: PBKDF2 hash from src.auth.hash_password.
            plan: Subscription plan — 'free' or 'premium'.
            email_verified: Whether the email is already verified (default
                False — signup verification; the register endpoint sends a
                link via SendGrid, see src/emails.py).
            verification_token: One-time token embedded in the emailed link.
            verification_token_expires: Expiry (ISO timestamp) for the token.

        Returns:
            The new user's id.

        Raises:
            psycopg2.IntegrityError: if the email is already registered.
        """
        sql = """
            INSERT INTO users
                (email, password_hash, plan, email_verified,
                 verification_token, verification_token_expires)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        """
        with self._get_conn() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        sql,
                        (
                            email.lower().strip(),
                            password_hash,
                            plan,
                            email_verified,
                            verification_token,
                            verification_token_expires,
                        ),
                    )
                    user_id = cur.fetchone()[0]
                conn.commit()
            except Exception:
                # Critical: roll back BEFORE returning the connection to the
                # pool. A failed INSERT (e.g. duplicate email) leaves the
                # connection in an aborted transaction; putting it back
                # without rollback poisons the whole pool (every subsequent
                # query fails with "current transaction is aborted").
                conn.rollback()
                raise
        logger.debug("Created user %s (id=%d, plan=%s)", email, user_id, plan)
        return user_id

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Fetch a user by email (for login).

        Args:
            email: The user's email.

        Returns:
            Dict with id, email, password_hash, plan, email_verified,
            is_admin, token_version, created_at — or None.
        """
        sql = """
            SELECT id, email, password_hash, plan, email_verified, is_admin,
                   token_version, reset_token_expires, created_at
            FROM users WHERE email = %s
        """
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (email.lower().strip(),))
                row = cur.fetchone()
        return dict(row) if row else None

    def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Fetch a user by primary key (for token validation).

        Args:
            user_id: The user's database id.

        Returns:
            Dict with id, email, password_hash, plan, email_verified,
            is_admin, token_version, created_at — or None. ``token_version``
            is what ``get_current_user`` compares the token's ``ver`` claim
            against on every authenticated request.
        """
        sql = """
            SELECT id, email, password_hash, plan, email_verified, is_admin,
                   token_version, created_at
            FROM users WHERE id = %s
        """
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (user_id,))
                row = cur.fetchone()
        return dict(row) if row else None

    def set_user_plan(self, user_id: int, plan: str) -> None:
        """Upgrade/downgrade a user's subscription plan.

        Args:
            user_id: The user's database id.
            plan: 'free' or 'premium'.
        """
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET plan = %s WHERE id = %s",
                    (plan, user_id),
                )
            conn.commit()
        logger.info("User %d plan set to %s", user_id, plan)

    # ── Admin dashboard (user management) ────────────────────────────

    def list_users(
        self,
        limit: int = 50,
        offset: int = 0,
        search: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List users (newest first) with optional email search.

        Admin dashboard: never exposes password hashes — only the fields
        the management UI needs (plan, verification state, admin flag).

        Args:
            limit: Page size.
            offset: Page start.
            search: Optional substring filter on email (case-insensitive).

        Returns:
            List of user dicts (id, email, plan, email_verified, is_admin,
            created_at).
        """
        sql = (
            "SELECT id, email, plan, email_verified, is_admin, created_at "
            "FROM users"
        )
        params: List[Any] = []
        if search:
            sql += " WHERE email ILIKE %s"
            params.append(f"%{search.strip()}%")
        sql += " ORDER BY id DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
        return [dict(r) for r in rows]

    def count_users(self, search: Optional[str] = None) -> int:
        """Count users (with the same optional email search)."""
        sql = "SELECT COUNT(*) FROM users"
        params: List[Any] = []
        if search:
            sql += " WHERE email ILIKE %s"
            params.append(f"%{search.strip()}%")
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                return int(cur.fetchone()[0] or 0)

    def set_user_admin(self, user_id: int, is_admin: bool) -> None:
        """Grant/revoke the admin role for a user.

        Args:
            user_id: The user's database id.
            is_admin: True to grant admin, False to revoke.
        """
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET is_admin = %s WHERE id = %s",
                    (is_admin, user_id),
                )
            conn.commit()
        logger.info("User %d admin role set to %s", user_id, is_admin)

    def bump_token_version(self, user_id: int) -> int:
        """Invalidate every token this user currently holds.

        Backs POST /auth/logout-all. The increment happens in the UPDATE
        itself (``token_version + 1``) rather than a read-modify-write, so
        two concurrent "log out everywhere" calls cannot lose each other's
        bump.

        Args:
            user_id: The user's database id.

        Returns:
            The new version. Tokens minted with the previous version — and
            every token minted before this feature existed — now fail the
            check in ``get_current_user``.
        """
        sql = (
            "UPDATE users SET token_version = token_version + 1 "
            "WHERE id = %s RETURNING token_version"
        )
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id,))
                row = cur.fetchone()
            conn.commit()
        # No row = no such user. Unreachable from the API (the caller is an
        # authenticated user resolved from this same table), returned rather
        # than raised so the method is total.
        new_version = int(row[0]) if row else 0
        logger.info("User %d token_version bumped to %d", user_id, new_version)
        return new_version

    def user_stats(self) -> Dict[str, Any]:
        """Aggregate user counts for the admin overview.

        Returns:
            Dict with total, admins, verified, and plan breakdown.
        """
        sql = """
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE is_admin) AS admins,
                COUNT(*) FILTER (WHERE email_verified) AS verified
            FROM users
        """
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
        plans_sql = "SELECT plan, COUNT(*) FROM users GROUP BY plan"
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(plans_sql)
                plans = {r[0]: int(r[1]) for r in cur.fetchall()}
        return {
            "total": int(row[0] or 0),
            "admins": int(row[1] or 0),
            "verified": int(row[2] or 0),
            "plans": plans,
        }

    # ── Email verification ──────────────────────────────────────────

    def set_user_verification_token(
        self,
        user_id: int,
        token: str,
        expires_at: str,
    ) -> None:
        """Persist a one-time email-verification token + its expiry.

        Args:
            user_id: The user's database id.
            token: Random token embedded in the emailed verification link.
            expires_at: ISO timestamp after which the token is invalid.
        """
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET verification_token = %s, "
                    "verification_token_expires = %s WHERE id = %s",
                    (token, expires_at, user_id),
                )
            conn.commit()

    def mark_email_verified(self, user_id: int) -> None:
        """Flip a user's email_verified flag to True (verification link used).

        The token is deliberately LEFT on the row. It can only ever mark its
        own account verified, so a replay is harmless — and keeping it is what
        lets a second click be recognised and answered with success instead of
        "invalid link". That matters more than it sounds: corporate mail
        scanners pre-fetch links, so without this the user's first real click
        lands on an error page. Resend overwrites the token; a password reset
        clears it.
        """
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET email_verified = TRUE WHERE id = %s",
                    (user_id,),
                )
            conn.commit()

    def get_user_by_verification_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Fetch the user holding a verification token (for link clicks).

        Returns None when no user has the token, so a forged/expired link
        fails closed (the API checks the token's expiry against NOW()).

        Args:
            token: The token from the emailed verification link.

        Returns:
            Dict with id, email, verification_token_expires — or None.
        """
        sql = """
            SELECT id, email, email_verified, verification_token_expires
            FROM users WHERE verification_token = %s
        """
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (token,))
                row = cur.fetchone()
        return dict(row) if row else None

    # ── Password reset & change ─────────────────────────────────────

    def set_password_reset_token(
        self, user_id: int, token_hash: str, expires_at: str
    ) -> None:
        """Store the hash of a freshly issued reset token, replacing any older one.

        Overwriting is the point: a second request invalidates the first
        link, so only the most recently emailed link can ever work.

        Args:
            user_id: The user's database id.
            token_hash: SHA-256 hex of the emailed token (never the token).
            expires_at: ISO timestamp after which the token is invalid.
        """
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET reset_token_hash = %s, "
                    "reset_token_expires = %s WHERE id = %s",
                    (token_hash, expires_at, user_id),
                )
            conn.commit()

    def get_user_by_reset_token_hash(
        self, token_hash: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch the user holding a reset-token hash, or None.

        Args:
            token_hash: SHA-256 hex of the token from the emailed link.

        Returns:
            Dict with id, email, reset_token_expires — or None.
        """
        sql = """
            SELECT id, email, reset_token_expires
            FROM users WHERE reset_token_hash = %s
        """
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (token_hash,))
                row = cur.fetchone()
        return dict(row) if row else None

    def complete_password_reset(
        self, user_id: int, token_hash: str, password_hash: str
    ) -> Optional[int]:
        """Consume a reset token: set the password and revoke every session.

        Single-use is enforced by the ``WHERE reset_token_hash = %s`` clause
        in the same statement that clears it — not by a prior read. Two
        concurrent requests carrying the same token cannot both succeed: the
        second finds the hash already NULL, matches no row, and gets None.

        Also marks the email verified: following a link delivered to the
        address proves control of it, exactly as the verification link does.

        Args:
            user_id: The user's database id.
            token_hash: SHA-256 hex of the presented token.
            password_hash: The new PBKDF2 hash.

        Returns:
            The new token_version, or None when the token was already used.
        """
        sql = (
            "UPDATE users SET password_hash = %s, "
            "reset_token_hash = NULL, reset_token_expires = NULL, "
            "email_verified = TRUE, "
            "verification_token = NULL, verification_token_expires = NULL, "
            "token_version = token_version + 1 "
            "WHERE id = %s AND reset_token_hash = %s "
            "RETURNING token_version"
        )
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (password_hash, user_id, token_hash))
                row = cur.fetchone()
            conn.commit()
        return int(row[0]) if row else None

    def change_password(self, user_id: int, password_hash: str) -> int:
        """Set a new password and revoke every existing session.

        Also clears any outstanding reset token: a link emailed before the
        change must not be able to undo it.

        Args:
            user_id: The user's database id.
            password_hash: The new PBKDF2 hash.

        Returns:
            The new token_version (the caller re-issues a token with it).
        """
        sql = (
            "UPDATE users SET password_hash = %s, "
            "reset_token_hash = NULL, reset_token_expires = NULL, "
            "token_version = token_version + 1 "
            "WHERE id = %s RETURNING token_version"
        )
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (password_hash, user_id))
                row = cur.fetchone()
            conn.commit()
        return int(row[0]) if row else 0

    # ── Google OAuth ("Continue with Google") ─────────────────────────

    def get_user_by_google_sub(self, sub: str) -> Optional[Dict[str, Any]]:
        """Fetch the account linked to a Google subject id, or None.

        Args:
            sub: The ``sub`` claim of a validated Google ID token.

        Returns:
            Dict with id, email, is_admin, token_version — or None. The
            caller does NOT need the password hash: a Google sign-in never
            verifies one.
        """
        sql = """
            SELECT id, email, is_admin, token_version
            FROM users WHERE google_sub = %s
        """
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (sub,))
                row = cur.fetchone()
        return dict(row) if row else None

    def link_google_identity(self, user_id: int, sub: str) -> None:
        """Attach a Google subject id to an existing, already-verified account.

        Used when the Google email matches a verified password account: the
        account keeps its password AND gains a working Google sign-in.

        Args:
            user_id: The user's database id.
            sub: The validated Google subject id.
        """
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET google_sub = %s WHERE id = %s",
                    (sub, user_id),
                )
            conn.commit()
        logger.info("Google identity linked to user %d", user_id)

    def take_over_for_google(self, user_id: int, sub: str, unusable_hash: str) -> int:
        """Hand an UNVERIFIED account to the Google identity that owns its address.

        The account-takeover case, and the reason this method exists.
        Anyone can register an address they do not own — a fresh install has
        no way to tell. If such a row were merely linked, the stranger's
        password would keep opening an account that now belongs to the real
        owner, and the session they already hold would stay alive. So the
        row is taken over in ONE statement:

          - ``password_hash`` becomes a real PBKDF2 hash of a random value
            nobody knows — the stranger's password stops working, and the
            verification cost stays identical to a normal login (a marker
            string like ``"!"`` would fail faster and leak which accounts
            are Google-only);
          - ``google_sub`` is set and ``email_verified`` becomes TRUE (Google
            vouched for the address);
          - every pending verification/reset token is cleared;
          - ``token_version`` increments, killing every session the stranger
            held — the same mechanism as POST /auth/logout-all.

        Args:
            user_id: The user's database id.
            sub: The validated Google subject id.
            unusable_hash: A PBKDF2 hash of a random secret (see
                ``google_oauth.unusable_password_hash``).

        Returns:
            The new token_version, which the caller stamps into the token it
            mints for the real owner.
        """
        sql = (
            "UPDATE users SET password_hash = %s, google_sub = %s, "
            "email_verified = TRUE, "
            "verification_token = NULL, verification_token_expires = NULL, "
            "reset_token_hash = NULL, reset_token_expires = NULL, "
            "token_version = token_version + 1 "
            "WHERE id = %s RETURNING token_version"
        )
        # The squatter's saved state goes too, in the same transaction: the
        # real owner must not inherit a stranger's watchlist, saved screens or
        # backtest history. backtest_jobs has no FK to users, hence explicit.
        with self._get_conn() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(sql, (unusable_hash, sub, user_id))
                    row = cur.fetchone()
                    cur.execute("DELETE FROM user_data WHERE user_id = %s", (user_id,))
                    cur.execute(
                        "DELETE FROM backtest_jobs WHERE user_email = "
                        "(SELECT email FROM users WHERE id = %s)",
                        (user_id,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        new_version = int(row[0]) if row else 0
        logger.info(
            "Unverified account %d taken over by Google identity (token_version=%d)",
            user_id, new_version,
        )
        return new_version

    def create_google_user(self, email: str, sub: str, unusable_hash: str) -> int:
        """Create the account for a first-time Google sign-in.

        Starts verified (Google proved the address) and password-less: the
        stored hash is a real PBKDF2 hash of a random secret, so no password
        can ever match it and login stays timing-identical to any other
        account. A Google-only user who wants a password uses "Forgot
        password".

        Args:
            email: The verified Google email (lowercased here).
            sub: The validated Google subject id.
            unusable_hash: A PBKDF2 hash of a random secret.

        Returns:
            The new user's id.

        Raises:
            psycopg2.IntegrityError: if the email is already registered (a
            concurrent callback won the race).
        """
        sql = """
            INSERT INTO users (email, password_hash, plan, email_verified, google_sub)
            VALUES (%s, %s, 'free', TRUE, %s)
            RETURNING id
        """
        with self._get_conn() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(sql, (email.lower().strip(), unusable_hash, sub))
                    user_id = cur.fetchone()[0]
                conn.commit()
            except Exception:
                # Roll back before the connection goes back to the pool — an
                # aborted transaction would poison every later query on it
                # (same contract as create_user).
                conn.rollback()
                raise
        logger.info("Created Google account %s (id=%d)", email, user_id)
        return user_id

    # ── Account deletion ────────────────────────────────────────────

    def delete_user(self, user_id: int, email: str) -> Dict[str, int]:
        """Permanently delete an account and every row of personal data.

        Personal data lives in three places. ``user_data`` has an
        ``ON DELETE CASCADE`` foreign key and goes with the user row.
        ``backtest_jobs`` does NOT — it is keyed by ``user_email`` with no
        foreign key — so a bare ``DELETE FROM users`` would leave the
        account's queries and results behind. Both deletes run in one
        transaction: either the account and all of its data are gone, or
        nothing is.

        Args:
            user_id: The user's database id.
            email: The account email (``backtest_jobs`` is keyed by it).

        Returns:
            Row counts: ``{"users": n, "backtest_jobs": n}``.
        """
        with self._get_conn() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM backtest_jobs WHERE user_email = %s", (email,))
                    jobs = cur.rowcount
                    cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
                    users = cur.rowcount
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        logger.info("Deleted user %d (%d backtest jobs)", user_id, jobs)
        return {"users": users, "backtest_jobs": jobs}

    # ── Per-user saved data (watchlist / screeners / backtests) ────────

    def get_user_data(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Fetch a user's saved watchlist/screeners/backtests (JSON blobs).

        Args:
            user_id: The user's database id.

        Returns:
            Dict with watchlist, saved_screeners, recent_backtests — or
            None when the user has never saved anything (no row yet).
        """
        sql = (
            "SELECT watchlist, saved_screeners, recent_backtests "
            "FROM user_data WHERE user_id = %s"
        )
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (user_id,))
                row = cur.fetchone()
        return dict(row) if row else None

    def upsert_user_data(
        self,
        user_id: int,
        watchlist: Optional[List[str]] = None,
        saved_screeners: Optional[List[Dict[str, Any]]] = None,
        recent_backtests: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Insert or replace a user's saved data row (upsert by user_id).

        The frontend pushes its whole personal state (watchlist, saved
        screeners, recent backtests) on change, so this REPLACES the three
        JSONB blobs in one transaction. Arrays are serialized to JSON here
        (psycopg2 needs ``Json`` for JSONB columns; the frontend already
        sends plain JSON-compatible structures).

        Args:
            user_id: The user's database id.
            watchlist: List of stock symbols.
            saved_screeners: List of {id, query, savedAt} dicts.
            recent_backtests: List of backtest-response dicts + savedAt.
        """

        sql = """
            INSERT INTO user_data (user_id, watchlist, saved_screeners, recent_backtests)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                watchlist = EXCLUDED.watchlist,
                saved_screeners = EXCLUDED.saved_screeners,
                recent_backtests = EXCLUDED.recent_backtests,
                updated_at = NOW()
        """
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        user_id,
                        psycopg2.extras.Json(watchlist or []),
                        psycopg2.extras.Json(saved_screeners or []),
                        psycopg2.extras.Json(recent_backtests or []),
                    ),
                )
            conn.commit()
        logger.debug("Saved user_data for user %d", user_id)

    def upsert_stock(self, record: Dict[str, Any]) -> None:
        """Insert or update a single stock record.

        Uses PostgreSQL INSERT ... ON CONFLICT (symbol) DO UPDATE.

        Args:
            record: A dictionary of stock fields (must include 'symbol').

        Raises:
            ValueError: if 'symbol' is missing or any column name is not
                in ``STOCK_MASTER_COLUMNS`` (defense-in-depth: column names
                are interpolated into SQL, so unknown names are rejected
                instead of trusted).
        """
        if "symbol" not in record:
            raise ValueError("Record must contain 'symbol' key")
        unknown = set(record) - set(STOCK_MASTER_COLUMNS)
        if unknown:
            raise ValueError(
                f"Unknown stock_master column(s): {sorted(unknown)}"
            )

        columns = list(record.keys())
        placeholders = ", ".join([f"%({k})s" for k in columns])
        updates = ", ".join([f"{col} = EXCLUDED.{col}" for col in columns if col != "symbol"])

        sql = f"""
            INSERT INTO stock_master ({", ".join(columns)})
            VALUES ({placeholders})
            ON CONFLICT (symbol) DO UPDATE SET {updates}
        """

        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, record)
            if record.get("market_cap") is not None:
                self._sweep_corrupt_market_caps(conn)
            conn.commit()

    def bulk_upsert_stocks(self, records: List[Dict[str, Any]]) -> None:
        """Efficiently upsert many stock records in a single transaction.

        Uses execute_values for batch performance.

        Args:
            records: List of stock record dictionaries.
        """
        if not records:
            return

        # Quote-only refreshes (the scheduler's price job, API refresh) build
        # records with no company_name. PostgreSQL enforces NOT NULL on the
        # *proposed* INSERT row even when ON CONFLICT DO UPDATE would fire
        # (verified empirically: an INSERT omitting company_name fails for an
        # existing symbol too), so backfill existing names in one batched
        # query. Records that still lack a name (genuinely new symbols with
        # only a quote, no profile yet) are skipped — the fundamentals pass,
        # which carries the profile, stores them.
        missing_names = [r["symbol"] for r in records if not r.get("company_name")]
        if missing_names:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT symbol, company_name FROM stock_master "
                        "WHERE symbol = ANY(%s)",
                        (missing_names,),
                    )
                    existing_names = dict(cur.fetchall())
            for r in records:
                if not r.get("company_name"):
                    r["company_name"] = existing_names.get(r["symbol"])
        records = [r for r in records if r.get("company_name") is not None]
        if not records:
            logger.info("No records to upsert (all lacked company_name)")
            return

        # Collect all unique column names across all records
        columns = list({k for r in records for k in r.keys()})
        if "symbol" not in columns:
            raise ValueError("All records must contain 'symbol' key")
        # Defense-in-depth (mirrors upsert_stock): column names are
        # interpolated into the SQL directly, so reject any name that is
        # not a known stock_master column rather than trusting it.
        unknown = set(columns) - set(STOCK_MASTER_COLUMNS)
        if unknown:
            raise ValueError(
                f"Unknown stock_master column(s): {sorted(unknown)}"
            )

        # Build the VALUES tuples
        values: List[Tuple[Any, ...]] = []
        for record in records:
            values.append(tuple(record.get(col) for col in columns))

        col_list = ", ".join(columns)
        # A refresh must never clobber columns it didn't fetch: the price
        # refresh (quotes only) carries no company_name/profile fields, and
        # writing EXCLUDED.company_name=NULL into the existing row violates
        # the NOT NULL constraint (and would erase fundamentals between the
        # daily passes). COALESCE keeps the stored value when the incoming
        # record has no data for that column — quote refreshes only update
        # price/volume/technicals, fundamentals/names survive untouched.
        updates = ", ".join(
            [
                f"{col} = COALESCE(EXCLUDED.{col}, stock_master.{col})"
                for col in columns
                if col != "symbol"
            ]
        )

        sql = f"""
            INSERT INTO stock_master ({col_list})
            VALUES %s
            ON CONFLICT (symbol) DO UPDATE SET {updates}
        """

        with self._get_conn() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(cur, sql, values, template=None)
            # Post-upsert sweep (same transaction): the enrich pass and the
            # scheduler's daily fundamental job write raw Finnhub profiles
            # WITHOUT going through build_stock_record, so a corrupt OTC/ADR
            # market cap would otherwise be re-written on every refresh.
            # Only runs when the batch carries market_cap — the 10-minute
            # price refresh (quotes only) skips it.
            if any(r.get("market_cap") is not None for r in records):
                self._sweep_corrupt_market_caps(conn)
            conn.commit()

        logger.info("Bulk upserted %d stock records", len(records))

    def _sweep_corrupt_market_caps(self, conn) -> None:
        """NULL out unit-corrupt market caps in the same transaction as the upsert.

        Applies :data:`MARKET_CAP_SANITY_SWEEP` against the STORED price so
        every write path (enrich pass, scheduler fundamentals, API refresh,
        bootstrap) is protected even when the incoming record had no price.
        Logs how many rows were corrected (a sweep that actually fires is
        itself a signal Finnhub served corrupt data for that batch).

        Args:
            conn: An open connection inside the caller's transaction (the
                sweep shares the caller's transaction and commit).
        """
        with conn.cursor() as cur:
            cur.execute(MARKET_CAP_SANITY_SWEEP)
            corrected = cur.rowcount
        if corrected:
            logger.warning(
                "Market-cap sanity sweep nulled %d corrupt OTC/ADR values",
                corrected,
            )

    def _build_filter_where(self, filters: Dict[str, Any]) -> tuple:
        """Build a parameterized WHERE clause from a filter dict.

        Shared by :meth:`query_stocks` and :meth:`count_stocks` so the
        paginated page and its total count ALWAYS see the same match set
        (prevents SQL injection; psycopg2-standard ``%s`` placeholders).

        Filter value forms:
          - Range:       {'pe_ratio': ('lt', 15)}
          - Column-comp: {'current_price': ('gt_col', 'sma_50')}  → price > SMA50
                         {'current_price': ('gte_frac_col', 'high_52w', 0.95)}
                             → price >= high_52w * 0.95 ("near 52w high")
          - IN:          {'sector': ['Tech', 'Health']}
          - Exact:       {'sector': 'Tech'}

        Column-compare operands are validated against
        ``STOCK_MASTER_COLUMNS`` before being interpolated, so only known
        column names can ever appear on the RHS — never user/LLM strings.

        Args:
            filters: Column-value pairs (see forms above).

        Returns:
            (where_clause, params) tuple ready for ``WHERE {clause}``.
        """
        conditions: List[str] = []
        params: List[Any] = []

        for col, value in filters.items():
            conds, ps = self._column_conditions(col, value)
            conditions.extend(conds)
            params.extend(ps)

        where_clause = " AND ".join(conditions) if conditions else "TRUE"
        return where_clause, params

    def _column_conditions(
        self, col: str, value: Any
    ) -> Tuple[List[str], List[Any]]:
        """Compile the SQL conditions for ONE column into (sql, params).

        A single column may carry multiple ANDed conditions (passed as a
        list of tuples) — e.g. momentum + a 52-week proximity filter both
        constrain ``current_price`` and must coexist, which a single-value
        filter dict could not express.

        Args:
            col: The column name (left side of the condition).
            value: One of:
              - scalar: ``col = %s``
              - list of scalars: ``col IN (...)``
              - tuple (op, val): range/exact (``neq`` with None → IS NOT NULL)
              - tuple (col-op, other_col): column-to-column comparison
              - tuple (frac-col-op, other_col, factor): ``col >= other*factor``
              - list of the tuple forms above: ANDed together

        Returns:
            (conditions, params) ready to append to the WHERE builder.
        """
        # Multiple ANDed conditions on the same column.
        if (
            isinstance(value, list)
            and value
            and all(isinstance(v, tuple) for v in value)
        ):
            conds: List[str] = []
            ps: List[Any] = []
            for sub in value:
                c, p = self._column_conditions(col, sub)
                conds.extend(c)
                ps.extend(p)
            return conds, ps

        # Column-to-column comparison: ('gt_col' | 'lt_col' | ..., other_col)
        if (
            isinstance(value, tuple)
            and len(value) >= 2
            and isinstance(value[0], str)
            and value[0].endswith("_col")
        ):
            operator = value[0]
            other = value[1]
            if other not in STOCK_MASTER_COLUMNS:
                raise ValueError(f"unknown column in filter: {other}")
            op_sql = {
                "gt_col": ">", "lt_col": "<",
                "gte_col": ">=", "lte_col": "<=",
                "gte_frac_col": ">=", "lte_frac_col": "<=",
            }[operator]
            if len(value) == 3 and value[0].endswith("frac_col"):
                # ('gte_frac_col', 'high_52w', 0.95) → col >= other * 0.95
                factor = float(value[2])
                return [f"{col} {op_sql} ({other} * %s)"], [factor]
            return [f"{col} {op_sql} {other}"], []

        if isinstance(value, tuple) and len(value) == 2:
            operator, operand = value
            # NULL-safe completeness guards: ("neq", None) → IS NOT NULL,
            # ("eq", None) → IS NULL. The naive ``col != NULL`` is never
            # true in SQL, so this must be special-cased or every row
            # would be excluded.
            if operator == "neq" and operand is None:
                return [f"{col} IS NOT NULL"], []
            if operator == "eq" and operand is None:
                return [f"{col} IS NULL"], []
            operator_map = {
                "lt": "<", "lte": "<=", "gt": ">",
                "gte": ">=", "eq": "=", "neq": "!=",
            }
            op_sql = operator_map.get(operator, "=")
            return [f"{col} {op_sql} %s"], [operand]

        if isinstance(value, (list, tuple)):
            # IN clause
            placeholders = ", ".join(["%s"] * len(value))
            return [f"{col} IN ({placeholders})"], list(value)

        return [f"{col} = %s"], [value]

    def query_stocks(
        self,
        filters: Dict[str, Any],
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "market_cap",
        sort_desc: bool = True,
    ) -> List[Dict[str, Any]]:
        """Query the stock_master table with dynamic filters and sorting.

        Builds a parameterized WHERE clause from the filter dict to
        prevent SQL injection. Uses psycopg2-standard ``%s`` placeholders.

        ``sort_by`` is validated against ``STOCK_MASTER_COLUMNS`` before
        being interpolated into ORDER BY — the column list is a frozenset
        of known names, so a crafted value cannot inject SQL. ``sort_desc``
        chooses ASC/DESC. NULLs sort last in both directions so pages don't
        silently drop rows whose sort key is missing.

        Args:
            filters: Column-value pairs to filter on.
                     Range filters: {'pe_ratio': ('lt', 15), 'market_cap': ('gte', 1e9)}
                     Exact match:  {'sector': 'Technology'}
                     Column-comp:  {'current_price': ('gt_col', 'sma_50')}
            limit: Maximum number of results.
            offset: Pagination offset.
            sort_by: Column to order by (must be in STOCK_MASTER_COLUMNS).
            sort_desc: True = DESC (largest first), False = ASC.

        Returns:
            List of matching stock records as dicts.
        """
        where_clause, params = self._build_filter_where(filters)
        if sort_by not in STOCK_MASTER_COLUMNS:
            logger.warning(
                "Unknown sort_by %r — falling back to market_cap", sort_by
            )
            sort_by = "market_cap"
        direction = "DESC" if sort_desc else "ASC"
        sql = (
            f"SELECT * FROM stock_master WHERE {where_clause} "
            f"ORDER BY {sort_by} {direction} NULLS LAST LIMIT %s OFFSET %s"
        )
        params.append(limit)
        params.append(offset)

        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()

        return [dict(row) for row in rows]

    def count_stocks(self, filters: Dict[str, Any]) -> int:
        """Count rows matching the filters (no LIMIT/OFFSET).

        Used for a TRUE ``total_matches`` in paginated responses — the
        count is computed with the same WHERE clause as the page query,
        so it reflects the whole match set, not just the returned page.

        Args:
            filters: Same filter dict format as :meth:`query_stocks`.

        Returns:
            Number of matching rows.
        """
        where_clause, params = self._build_filter_where(filters)
        sql = f"SELECT COUNT(*) FROM stock_master WHERE {where_clause}"
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                return cur.fetchone()[0] or 0

    def aggregate_stats(
        self,
        filters: Dict[str, Any],
        columns: List[str],
    ) -> Dict[str, Dict[str, float]]:
        """Compute mean/stddev/valid-count per column over the match set.

        Used by the screener's multi-factor composite score (Tier 3):
        each candidate is z-scored against the *match set's own*
        distribution, so "top value" / "top momentum" are relative to
        the names that already passed the user's filters — not the whole
        universe. One aggregate query per column keeps it cheap.

        Args:
            filters: Same filter dict format as :meth:`query_stocks`.
            columns: Numeric columns to summarize.

        Returns:
            {col: {"mean": .., "std": .., "valid": n}} — std is 0 when
            fewer than 2 valid values exist (z-score degenerates to 0).
        """
        where_clause, params = self._build_filter_where(filters)
        out: Dict[str, Dict[str, float]] = {}
        for col in columns:
            if col not in STOCK_MASTER_COLUMNS:
                continue
            sql = (
                f"SELECT COUNT({col}), AVG({col}), STDDEV({col}) "
                f"FROM stock_master WHERE {where_clause}"
            )
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    valid, mean, std = cur.fetchone()
            out[col] = {
                "mean": float(mean) if mean is not None else 0.0,
                "std": float(std) if std is not None else 0.0,
                "valid": int(valid or 0),
            }
        return out

    def data_freshness(self, max_age_minutes: int = 15) -> Dict[str, Any]:
        """Report how fresh the stock_master data is (staleness guard).

        Args:
            max_age_minutes: Age threshold that marks a row "stale"
                (default 15 — the refresh cadence is 10 minutes, so any
                row older than 15 min is behind schedule).

        Returns:
            Dict with:
              oldest_age_minutes (float | None): age of the oldest row.
              max_updated_at (str | None): newest updated_at timestamp.
              stale_count (int): rows older than the threshold.
              total_rows (int): rows in the table.
        """
        sql = (
            "SELECT COUNT(*), "
            "EXTRACT(EPOCH FROM (NOW() - MAX(updated_at)))/60, "
            "MAX(updated_at)::text "
            "FROM stock_master"
        )
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                total, oldest_min, max_ts = cur.fetchone()
        total = int(total or 0)
        oldest = float(oldest_min) if oldest_min is not None else None
        stale = 0
        if oldest is not None:
            sql2 = (
                "SELECT COUNT(*) FROM stock_master "
                "WHERE updated_at < NOW() - INTERVAL '%s minutes'"
            )
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql2, (max_age_minutes,))
                    stale = int(cur.fetchone()[0] or 0)
        return {
            "oldest_age_minutes": oldest,
            "max_updated_at": max_ts,
            "stale_count": stale,
            "total_rows": total,
        }

    # Columns the screener can filter/sort on (a strict subset of
    # STOCK_MASTER_COLUMNS). Every numeric/optional column is a candidate;
    # text identity columns are expected to be ~always present and are
    # excluded from the gate so it flags real data gaps, not the schema.
    # Kept module-level so tests can assert the exact list.
    COVERAGE_GATE_COLUMNS: tuple = (
        "market_cap", "current_price", "pe_ratio", "rsi_14",
        "avg_volume", "dividend_yield", "beta", "eps_ttm", "sma_50",
        "sma_200", "high_52w", "low_52w", "ps_ratio", "pb_ratio",
        "ev_ebitda", "roe_ttm", "roa_ttm", "gross_margin_ttm",
        "net_margin_ttm", "revenue_growth_ttm", "eps_growth_ttm",
        "macd_12_26", "atr_14", "bollinger_upper", "bollinger_lower",
    )

    def column_coverage(self, columns=None) -> Dict[str, int]:
        """Return non-null row counts for the given columns (one query).

        The coverage gate's data source: how many rows actually carry each
        column, so a silently-empty column (0% populated → every filter on
        it returns 0 candidates) is visible instead of quietly zeroing
        screens.

        Args:
            columns: Iterable of column names. Defaults to
                COVERAGE_GATE_COLUMNS.

        Returns:
            Dict mapping column -> non-null row count.
        """
        cols = list(columns or self.COVERAGE_GATE_COLUMNS)
        if not cols:
            return {}
        for c in cols:
            if c not in STOCK_MASTER_COLUMNS:
                raise ValueError(f"unknown stock_master column: {c}")
        select = ", ".join(
            f"COUNT({c}) AS \"{c}\"" for c in cols
        )
        sql = f"SELECT {select} FROM stock_master"
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
        return {c: int(row[idx] or 0) for idx, c in enumerate(cols)}

    def coverage_gaps(self, min_pct: float = 50.0, columns=None) -> List[str]:
        """Columns whose non-null coverage is below ``min_pct``.

        The coverage gate itself: a filterable column under the threshold is
        a data problem, not a query problem — every filter on it silently
        returns zero candidates. Returns the offending column names so
        callers (verify script, tests, refresh jobs) can fail loudly.

        Args:
            min_pct: Minimum required non-null percentage (0-100).
            columns: Iterable of column names (defaults to the gate set).

        Returns:
            Sorted list of columns with coverage < min_pct.
        """
        counts = self.column_coverage(columns)
        return coverage_gap_columns(counts, min_pct=min_pct)

    def get_all_symbols(self) -> List[str]:
        """Fetch all stock symbols currently in the database."""
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT symbol FROM stock_master ORDER BY symbol")
                return [row[0] for row in cur.fetchall()]

    def get_top_symbols(self, limit: int = 300) -> List[str]:
        """Fetch the top-N symbols by market cap (news refresh universe).

        News matters most for liquid names users actually screen, so the
        news pipeline refreshes the largest names first.

        Args:
            limit: How many symbols to return.

        Returns:
            List of symbols ordered by market cap descending.
        """
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT symbol FROM stock_master "
                    "ORDER BY market_cap DESC NULLS LAST LIMIT %s",
                    (limit,),
                )
                return [row[0] for row in cur.fetchall()]

    def stock_count(self) -> int:
        """Return the total number of stocks in the database."""
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM stock_master")
                return cur.fetchone()[0] or 0

    def health_check(self) -> Dict[str, Any]:
        """Probe the database with ``SELECT 1`` and measure latency.

        Returns:
            Dict with keys:
              alive (bool): True if the query succeeded.
              latency_ms (float | None): Round-trip time in milliseconds.
              error (str | None): Error message if the probe failed.

        This never raises — errors are captured in the return dict.
        """
        import time

        if not settings.database_url:
            return {"alive": False, "latency_ms": None, "error": "DATABASE_URL not configured"}

        try:
            start = time.perf_counter()
            # Configurable connect timeout (settings.db_connect_timeout, default
            # 15s): Neon is serverless and a suspended-database cold start can
            # take >3s, so the old hardcoded 3s produced false unhealthy alarms.
            conn = psycopg2.connect(
                settings.database_url,
                connect_timeout=settings.db_connect_timeout,
            )
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
            conn.close()
            elapsed_ms = (time.perf_counter() - start) * 1000
            return {"alive": True, "latency_ms": round(elapsed_ms, 1), "error": None}
        except Exception as e:
            return {"alive": False, "latency_ms": None, "error": str(e)[:120]}

    def close(self) -> None:
        """Close the shared connection pool and reset it.

        Idempotent: closing an already-closed pool is a no-op. The next
        access to :attr:`pool` lazily re-creates it, so the scheduler's
        "release connections so Neon can auto-suspend" behavior is
        preserved — a later refresh or API request simply re-opens.
        """
        global _pg_pool
        with _pool_lock:
            if _pg_pool is not None:
                try:
                    _pg_pool.closeall()
                except Exception:
                    pass
                _pg_pool = None
                logger.info("PostgreSQL connection pool closed")


# ═══════════════════════════════════════════════════════════════════════════════
# PostgreSQL Client (News) — separate database
# ═══════════════════════════════════════════════════════════════════════════════

def _sanitize_dsn(url: str) -> str:
    """Strip libpq params that break psycopg2 connections.

    Neon connection strings sometimes carry ``channel_binding=require``,
    which psycopg2/libpq mishandles (spurious "SSL connection has been
    closed unexpectedly" on pooled/reused connections). Strip it and any
    other non-essential libpq knobs we don't use.
    """
    if not url:
        return url
    params_to_strip = ["channel_binding=require", "channel_binding=prefer", "channel_binding=allow"]
    for param in params_to_strip:
        url = url.replace(f"&{param}", "").replace(f"?{param}", "")
    return url


class NewsDB:
    """PostgreSQL client for the dedicated news database.

    Lives in a SEPARATE Neon project (``NEWS_DATABASE_URL``) so article
    bloat and LLM-sentiment writes never touch the screener database.
    Stores two tables:
      - ``news_articles``   : individual articles + per-article sentiment
      - ``stock_sentiment`` : aggregate sentiment per stock

    Uses the same threaded connection-pool pattern as :class:`PostgresDB`.
    """

    def __init__(self, database_url: Optional[str] = None) -> None:
        self._database_url = _sanitize_dsn(database_url or settings.news_database_url)
        # No per-instance pool — shares the module-level news pool (see the
        # "Shared connection pools" header) so per-request NewsDB() in the
        # API never opens a fresh pool per request.

    @property
    def pool(self) -> psycopg2.pool.ThreadedConnectionPool:
        """Return the shared news-database connection pool (lazily created)."""
        global _news_pool
        if _news_pool is None:
            with _pool_lock:
                if _news_pool is None:
                    if not self._database_url:
                        raise RuntimeError(
                            "News database is not configured. Set NEWS_DATABASE_URL in your .env file."
                        )
                    try:
                        _news_pool = psycopg2.pool.ThreadedConnectionPool(
                            minconn=1,
                            maxconn=3,
                            dsn=self._database_url,
                            # Same cold-start guard as PostgresDB (see above).
                            connect_timeout=settings.db_connect_timeout,
                        )
                        logger.info(
                            "News database connection pool initialized (min=1, max=3, "
                            "connect_timeout=%ss)",
                            settings.db_connect_timeout,
                        )
                    except Exception as e:
                        logger.error("Failed to connect to news database: %s", e)
                        raise RuntimeError(f"News database connection failed: {e!s}") from e
        return _news_pool

    @contextmanager
    def _get_conn(self) -> Generator[psycopg2.extensions.connection, None, None]:
        """Get a connection from the shared pool and return it when done."""
        with _get_shared_conn(lambda: self.pool) as conn:
            yield conn

    def init_schema(self) -> None:
        """Create the news tables and indexes if they don't exist."""
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(STOCK_SENTIMENT_SCHEMA)
                cur.execute(NEWS_ARTICLES_SCHEMA)
                for idx in NEWS_INDEXES:
                    cur.execute(idx)
            conn.commit()
        logger.info("News database schema initialized")

    def upsert_articles(self, articles: List[Dict[str, Any]]) -> int:
        """Insert news articles, skipping ones already stored (by symbol+url).

        Args:
            articles: List of article dicts (symbol, headline, summary,
                source, url, published_at, sentiment, sentiment_score).

        Returns:
            Number of new articles inserted.
        """
        if not articles:
            return 0

        rows: List[Tuple[Any, ...]] = []
        for a in articles:
            rows.append((
                a.get("symbol", ""),
                a.get("headline", "")[:500],
                (a.get("summary") or "")[:1000],
                a.get("source", "Unknown"),
                a.get("url", ""),
                a.get("published_at"),
                a.get("sentiment"),
                a.get("sentiment_score"),
            ))

        sql = """
            INSERT INTO news_articles
                (symbol, headline, summary, source, url, published_at,
                 sentiment, sentiment_score)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (symbol, url) DO NOTHING
        """
        inserted = 0
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, rows)
                inserted = cur.rowcount if cur.rowcount is not None else 0
            conn.commit()
        logger.debug("Inserted %d new news articles", inserted)
        return inserted

    def upsert_sentiment(self, record: Dict[str, Any]) -> None:
        """Insert or update aggregate sentiment for a stock.

        Args:
            record: Dict with symbol, buzz, company_news_score,
                sector_news_score, article_count.
        """
        if "symbol" not in record:
            raise ValueError("Sentiment record must contain 'symbol' key")

        sql = """
            INSERT INTO stock_sentiment
                (symbol, buzz, company_news_score, sector_news_score, article_count)
            VALUES (%(symbol)s, %(buzz)s, %(company_news_score)s, %(sector_news_score)s, %(article_count)s)
            ON CONFLICT (symbol) DO UPDATE SET
                buzz = EXCLUDED.buzz,
                company_news_score = EXCLUDED.company_news_score,
                sector_news_score = EXCLUDED.sector_news_score,
                article_count = EXCLUDED.article_count,
                updated_at = NOW()
        """
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, {
                    "symbol": record["symbol"],
                    "buzz": record.get("buzz", 0.0),
                    "company_news_score": record.get("company_news_score", 0.0),
                    "sector_news_score": record.get("sector_news_score", 0.0),
                    "article_count": record.get("article_count", 0),
                })
            conn.commit()

    def get_articles_for_symbol(
        self, symbol: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Fetch the most recent articles for a symbol (newest first)."""
        sql = """
            SELECT symbol, headline, summary, source, url,
                   published_at, sentiment, sentiment_score
            FROM news_articles
            WHERE symbol = %s
            ORDER BY published_at DESC NULLS LAST
            LIMIT %s
        """
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (symbol, limit))
                rows = cur.fetchall()
        return [dict(r) for r in rows]

    def get_recent_articles(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Fetch the most recent articles across all stocks (news feed)."""
        sql = """
            SELECT symbol, headline, summary, source, url,
                   published_at, sentiment, sentiment_score
            FROM news_articles
            ORDER BY published_at DESC NULLS LAST
            LIMIT %s
        """
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (limit,))
                rows = cur.fetchall()
        return [dict(r) for r in rows]

    def get_sentiment(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch aggregate sentiment for a single symbol."""
        sql = """
            SELECT symbol, buzz, company_news_score, sector_news_score,
                   article_count, updated_at
            FROM stock_sentiment
            WHERE symbol = %s
        """
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (symbol,))
                row = cur.fetchone()
        return dict(row) if row else None

    def get_sentiments_for_symbols(
        self, symbols: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """Batch-fetch aggregate sentiment for a list of symbols.

        Used to attach sentiment to screener results in one DB round-trip.

        Args:
            symbols: List of ticker symbols.

        Returns:
            Dict mapping symbol -> sentiment record (empty if none).
        """
        if not symbols:
            return {}
        placeholders = ", ".join(["%s"] * len(symbols))
        sql = f"""
            SELECT symbol, buzz, company_news_score, sector_news_score,
                   article_count, updated_at
            FROM stock_sentiment
            WHERE symbol IN ({placeholders})
        """
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, tuple(symbols))
                rows = cur.fetchall()
        return {r["symbol"]: dict(r) for r in rows}

    def sentiment_count(self) -> int:
        """Number of stocks with stored sentiment."""
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM stock_sentiment")
                return cur.fetchone()[0] or 0

    def article_count(self) -> int:
        """Total number of stored news articles."""
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM news_articles")
                return cur.fetchone()[0] or 0

    def close(self) -> None:
        """Close the shared news connection pool and reset it (idempotent)."""
        global _news_pool
        with _pool_lock:
            if _news_pool is not None:
                try:
                    _news_pool.closeall()
                except Exception:
                    pass
                _news_pool = None
                logger.info("News database connection pool closed")


# ═══════════════════════════════════════════════════════════════════════════════
# PostgreSQL Client (Backtesting) — production storage
# ═══════════════════════════════════════════════════════════════════════════════

class BacktestDB:
    """PostgreSQL client for the dedicated backtesting database.

    Production-ready replacement for the local SQLite file: 10 years of
    yfinance OHLCV + cached backtest results live in their own Neon project
    (``BACKTEST_DATABASE_URL``) so multiple server instances share one
    source of truth and the bulky time-series never touches the screener DB.

    Mirrors the EXACT interface the ``BacktestEngine`` consumes from
    ``SQLiteDB`` (``init_schema``, ``insert_historical_prices``,
    ``get_historical_prices``, ``get_all_symbols_with_data``,
    ``save_backtest_result``) so the engine swaps storage backends without
    code changes. Rows come back as dicts (RealDictCursor), which the
    engine already consumes via ``dict(row)``.
    """

    def __init__(self, database_url: Optional[str] = None) -> None:
        self._database_url = _sanitize_dsn(database_url or settings.backtest_database_url)
        # No per-instance pool — shares the module-level backtest pool (see
        # the "Shared connection pools" header) so per-request BacktestDB()
        # never opens a fresh pool per request.

    @property
    def pool(self) -> psycopg2.pool.ThreadedConnectionPool:
        """Return the shared backtest-database pool (lazily created)."""
        global _backtest_pool
        if _backtest_pool is None:
            with _pool_lock:
                if _backtest_pool is None:
                    if not self._database_url:
                        raise RuntimeError(
                            "Backtest database is not configured. Set BACKTEST_DATABASE_URL "
                            "in your .env file (or fall back to SQLite for local dev)."
                        )
                    try:
                        _backtest_pool = psycopg2.pool.ThreadedConnectionPool(
                            minconn=1,
                            maxconn=3,
                            dsn=self._database_url,
                            # Same cold-start guard as the other Neon DBs.
                            connect_timeout=settings.db_connect_timeout,
                        )
                        logger.info(
                            "Backtest database connection pool initialized (min=1, max=3, "
                            "connect_timeout=%ss)",
                            settings.db_connect_timeout,
                        )
                    except Exception as e:
                        logger.error("Failed to connect to backtest database: %s", e)
                        raise RuntimeError(f"Backtest database connection failed: {e!s}") from e
        return _backtest_pool

    @contextmanager
    def _get_conn(self) -> Generator[psycopg2.extensions.connection, None, None]:
        """Get a connection from the shared pool and return it when done."""
        with _get_shared_conn(lambda: self.pool) as conn:
            yield conn

    def init_schema(self) -> None:
        """Create the historical_prices + backtest_results tables (if missing).

        Idempotent (CREATE TABLE IF NOT EXISTS) — safe to call on every
        engine construction, mirroring the SQLite self-heal behavior.
        """
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(BACKTEST_HISTORICAL_PRICES_SCHEMA)
                for idx in BACKTEST_HISTORICAL_PRICES_INDEXES:
                    cur.execute(idx)
                cur.execute(BACKTEST_RESULTS_SCHEMA_PG)
                for idx in BACKTEST_RESULTS_INDEXES_PG:
                    cur.execute(idx)
            conn.commit()
        logger.info("Backtest database schema initialized (historical_prices + backtest_results)")

    def insert_historical_prices(
        self,
        records: List[Tuple[str, str, float, float, float, float, float, int, float, float]],
    ) -> int:
        """Insert historical OHLCV records, upserting on (symbol, date).

        Args:
            records: Tuples of (symbol, date, open, high, low, close,
                raw_close, volume, dividends, stock_splits) — same shape
                as the SQLite contract.

        Returns:
            Number of rows affected.
        """
        if not records:
            return 0
        sql = """
            INSERT INTO historical_prices
                (symbol, date, open, high, low, close, raw_close, volume,
                 dividends, stock_splits)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (symbol, date) DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                raw_close = EXCLUDED.raw_close,
                volume = EXCLUDED.volume,
                dividends = EXCLUDED.dividends,
                stock_splits = EXCLUDED.stock_splits
        """
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, records)
                affected = cur.rowcount if cur.rowcount is not None else 0
            conn.commit()
        return affected

    def get_historical_prices(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> List[Dict[str, Any]]:
        """Fetch historical OHLCV for a symbol within a date range.

        Args:
            symbol: Ticker symbol.
            start_date: Inclusive start (YYYY-MM-DD).
            end_date: Inclusive end (YYYY-MM-DD).

        Returns:
            List of row dicts (dict-able, like the engine's ``dict(row)``).
        """
        sql = """
            SELECT symbol, date, open, high, low, close, raw_close, volume,
                   dividends, stock_splits
            FROM historical_prices
            WHERE symbol = %s AND date >= %s AND date <= %s
            ORDER BY date ASC
        """
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (symbol, start_date, end_date))
                rows = cur.fetchall()
        return [dict(r) for r in rows]

    def get_all_symbols_with_data(self) -> List[str]:
        """Get all symbols that have historical price data loaded."""
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT symbol FROM historical_prices ORDER BY symbol")
                return [row[0] for row in cur.fetchall()]

    def get_symbol_max_dates(self) -> Dict[str, Optional[str]]:
        """Newest cached bar date per symbol (one query — cache-warmer staleness).

        Used by the backtest cache warmer to decide which symbols are
        stale enough to refresh: a symbol whose latest stored ``date`` is
        older than ``backtest_cache_warmer_stale_days`` (or missing
        entirely) gets its history re-downloaded. This returns the whole
        map in a single query so a 100-symbol warm cycle needs exactly one
        DB round-trip to plan, not one query per symbol.

        Returns:
            Dict mapping symbol -> its max ``date`` (YYYY-MM-DD) or None
            when the symbol has no stored history.
        """
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT symbol, MAX(date) FROM historical_prices "
                    "GROUP BY symbol"
                )
                return {row[0]: row[1] for row in cur.fetchall()}

    def count_prices(self, symbol: Optional[str] = None) -> int:
        """Count stored historical price rows (whole table or one symbol).

        Used by the backfill tool + row-count verification (Tier 3):
        proving "10 years of history is loaded" requires an actual count,
        not a vibe.

        Args:
            symbol: Restrict to one symbol when given (None = whole table).

        Returns:
            Number of rows in ``historical_prices``.
        """
        if symbol:
            sql = "SELECT COUNT(*) FROM historical_prices WHERE symbol = %s"
            params: tuple = (symbol,)
        else:
            sql = "SELECT COUNT(*) FROM historical_prices"
            params = ()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return int(cur.fetchone()[0] or 0)

    def save_backtest_result(self, record: Dict[str, Any]) -> int:
        """Save a backtest result to the cache."""
        sql = """
            INSERT INTO backtest_results
                (user_query_hash, strategy_params, symbol, start_date, end_date,
                 initial_capital, final_value, total_return, sharpe_ratio,
                 max_drawdown, win_rate, total_trades)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    record["user_query_hash"],
                    record["strategy_params"],
                    record["symbol"],
                    record["start_date"],
                    record["end_date"],
                    record["initial_capital"],
                    record["final_value"],
                    record["total_return"],
                    record.get("sharpe_ratio"),
                    record.get("max_drawdown"),
                    record.get("win_rate"),
                    record.get("total_trades"),
                ))
                result_id = cur.fetchone()[0]
            conn.commit()
        return result_id

    def close(self) -> None:
        """Close the shared backtest connection pool and reset it (idempotent)."""
        global _backtest_pool
        with _pool_lock:
            if _backtest_pool is not None:
                try:
                    _backtest_pool.closeall()
                except Exception:
                    pass
                _backtest_pool = None
                logger.info("Backtest database connection pool closed")


# ═══════════════════════════════════════════════════════════════════════════════
# SQLite Client (Backtesting) — local dev / tests fallback
# ═══════════════════════════════════════════════════════════════════════════════

class SQLiteDB:
    """Local SQLite database for historical backtesting data.

    Separate from the PostgreSQL screener database.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = Path(db_path or settings.backtest_db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # Per-THREAD connections: BacktestEngine now backtests symbols in
        # parallel (ThreadPoolExecutor), and sqlite3 connections are not
        # safe to share across threads (check_same_thread). WAL mode allows
        # concurrent readers + a single writer, so each worker gets its own
        # connection to the same file. ``threading.local`` stores one
        # connection per thread. ``_conns`` is a registry of every connection
        # ever created so ``close()`` can tear them all down regardless of
        # which thread created them (thread-local storage only exposes the
        # calling thread's connection — worker-thread connections would
        # otherwise leak). Guarded by a lock since workers create connections
        # concurrently.
        self._local = threading.local()
        self._conns: List[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()
        self._closed: bool = False

    def connect(self) -> sqlite3.Connection:
        """Get (or create) this thread's SQLite connection."""
        conn: Optional[sqlite3.Connection] = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self._db_path))
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            self._local.conn = conn
            with self._conns_lock:
                self._conns.append(conn)
        return conn

    def init_schema(self) -> None:
        """Create all SQLite tables and indexes if they don't exist."""
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute(HISTORICAL_PRICES_SCHEMA)
        for idx in HISTORICAL_PRICES_INDEXES:
            cursor.execute(idx)
        cursor.execute(BACKTEST_RESULTS_SCHEMA)
        for idx in BACKTEST_RESULTS_INDEXES:
            cursor.execute(idx)
        # Idempotent migration: older databases (pre-Tier 2) lack the
        # raw_close column added for the price-only-vs-total-return report.
        # CREATE TABLE IF NOT EXISTS won't alter an existing table, so add
        # the column when it's missing.
        cols = [r[1] for r in cursor.execute(
            "PRAGMA table_info(historical_prices)").fetchall()]
        if "raw_close" not in cols:
            cursor.execute(
                "ALTER TABLE historical_prices ADD COLUMN raw_close REAL")
        conn.commit()

    def insert_historical_prices(
        self,
        records: List[Tuple[str, str, float, float, float, float, int, float, float, float]],
    ) -> int:
        """Insert historical OHLCV price records (incl. raw close)."""
        conn = self.connect()
        cursor = conn.cursor()
        cursor.executemany(
            """INSERT OR REPLACE INTO historical_prices
               (symbol, date, open, high, low, close, raw_close, volume,
                dividends, stock_splits)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            records,
        )
        conn.commit()
        return cursor.rowcount

    def get_historical_prices(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> List[sqlite3.Row]:
        """Fetch historical OHLCV data for a symbol within a date range."""
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT * FROM historical_prices
               WHERE symbol = ? AND date >= ? AND date <= ?
               ORDER BY date ASC""",
            (symbol, start_date, end_date),
        )
        return cursor.fetchall()

    def get_all_symbols_with_data(self) -> List[str]:
        """Get all symbols that have historical price data loaded."""
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT symbol FROM historical_prices ORDER BY symbol")
        return [row["symbol"] for row in cursor.fetchall()]

    def get_symbol_max_dates(self) -> Dict[str, Optional[str]]:
        """Newest cached bar date per symbol (one query — cache-warmer staleness).

        Mirrors :meth:`BacktestDB.get_symbol_max_dates` so the backtest
        cache warmer's duck-typed ``db`` contract works on both backends
        (SQLite in dev/tests, PostgreSQL/Neon in production).

        Returns:
            Dict mapping symbol -> its max ``date`` (YYYY-MM-DD) or None
            when the symbol has no stored history.
        """
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT symbol, MAX(date) FROM historical_prices GROUP BY symbol"
        )
        return {row[0]: row[1] for row in cursor.fetchall()}

    def count_prices(self, symbol: Optional[str] = None) -> int:
        """Count stored historical price rows (whole table or one symbol).

        Mirrors :meth:`BacktestDB.count_prices` so the backfill tool + row-
        count verification work against the same duck-typed ``db`` contract
        on both backends (SQLite in dev/tests, PostgreSQL/Neon in prod).

        Args:
            symbol: Restrict to one symbol when given (None = whole table).

        Returns:
            Number of rows in ``historical_prices``.
        """
        conn = self.connect()
        cursor = conn.cursor()
        if symbol:
            cursor.execute(
                "SELECT COUNT(*) FROM historical_prices WHERE symbol = ?", (symbol,)
            )
        else:
            cursor.execute("SELECT COUNT(*) FROM historical_prices")
        return int(cursor.fetchone()[0] or 0)

    def save_backtest_result(self, record: Dict[str, Any]) -> int:
        """Save a backtest result to the cache."""
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO backtest_results
               (user_query_hash, strategy_params, symbol, start_date, end_date,
                initial_capital, final_value, total_return, sharpe_ratio,
                max_drawdown, win_rate, total_trades)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record["user_query_hash"],
                record["strategy_params"],
                record["symbol"],
                record["start_date"],
                record["end_date"],
                record["initial_capital"],
                record["final_value"],
                record["total_return"],
                record.get("sharpe_ratio"),
                record.get("max_drawdown"),
                record.get("win_rate"),
                record.get("total_trades"),
            ),
        )
        conn.commit()
        return cursor.lastrowid

    def close(self) -> None:
        """Close every connection created by this instance (any thread)."""
        if self._closed:
            return
        self._closed = True
        with self._conns_lock:
            conns, self._conns = self._conns, []
        for conn in conns:
            try:
                conn.close()
            except Exception:
                pass
        for attr in list(getattr(self._local, "__dict__", {})):
            try:
                delattr(self._local, attr)
            except Exception:
                pass
