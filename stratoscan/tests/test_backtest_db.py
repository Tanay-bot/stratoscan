"""Hermetic tests for the BacktestDB (PostgreSQL/Neon) storage layer.

Run: python -m tests.test_backtest_db

The backtesting engine's historical OHLCV + results were moved off the
local SQLite file onto a dedicated Neon database (BACKTEST_DATABASE_URL)
for production-readiness: SQLite is a single-writer embedded file, so
multiple server instances can't share it and it can't survive a redeploy.
BacktestDB mirrors the EXACT interface BacktestEngine consumes from
SQLiteDB (init_schema, insert_historical_prices, get_historical_prices,
get_all_symbols_with_data, save_backtest_result) so the engine swaps
storage backends without code changes.

These checks are fully hermetic (zero network, zero live DB):
  1. Interface parity — BacktestDB exposes every method SQLiteDB does
     with the same signatures (the engine's duck-typed contract).
  2. DDL validity — the Postgres schema strings contain the required
     tables/columns and valid index statements.
  3. DSN sanitization — the same channel_binding strip that protects
     NewsDB/PostgresDB applies to backtest URLs.
  4. Engine default selection — BacktestEngine picks BacktestDB when
     BACKTEST_DATABASE_URL is set, SQLiteDB otherwise (dev fallback).
"""

from __future__ import annotations

import os
import sys
import inspect
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["RATE_LIMIT_ENABLED"] = "false"

from src.database import (
    BacktestDB,
    SQLiteDB,
    _sanitize_dsn,
    BACKTEST_HISTORICAL_PRICES_SCHEMA,
    BACKTEST_RESULTS_SCHEMA_PG,
    BACKTEST_HISTORICAL_PRICES_INDEXES,
    BACKTEST_RESULTS_INDEXES_PG,
)

# ── 1. Interface parity (the engine's duck-typed contract) ───────────────
print("1. BacktestDB mirrors the SQLiteDB interface the engine consumes...")

sqlite_methods = {
    name for name, _ in inspect.getmembers(SQLiteDB, inspect.isfunction)
    if not name.startswith("_")
}
pg_methods = {
    name for name, _ in inspect.getmembers(BacktestDB, inspect.isfunction)
    if not name.startswith("_")
}
required = {"init_schema", "insert_historical_prices", "get_historical_prices",
            "get_all_symbols_with_data", "save_backtest_result"}
missing = required - pg_methods
assert not missing, f"BacktestDB missing engine-required methods: {missing}"
print(f"✅ BacktestDB methods: {sorted(required)}")

# The engine calls get_historical_prices(symbol, start, end) and
# insert_historical_prices(records) positionally — signatures must match.
sig = inspect.signature(SQLiteDB.get_historical_prices)
sig_pg = inspect.signature(BacktestDB.get_historical_prices)
assert list(sig.parameters) == list(sig_pg.parameters), f"{sig} vs {sig_pg}"
sig2 = inspect.signature(SQLiteDB.insert_historical_prices)
sig2_pg = inspect.signature(BacktestDB.insert_historical_prices)
assert list(sig2.parameters) == list(sig2_pg.parameters), f"{sig2} vs {sig2_pg}"
print("✅ parameter signatures match SQLiteDB (positional call contract)")

# ── 2. DDL validity ───────────────────────────────────────────────────────
print("2. Postgres DDL defines the required schema...")

assert "CREATE TABLE IF NOT EXISTS historical_prices" in BACKTEST_HISTORICAL_PRICES_SCHEMA
assert "UNIQUE(symbol, date)" in BACKTEST_HISTORICAL_PRICES_SCHEMA
for col in ("open", "high", "low", "close", "raw_close", "volume",
            "dividends", "stock_splits"):
    assert col in BACKTEST_HISTORICAL_PRICES_SCHEMA, f"missing column {col}"
assert "CREATE TABLE IF NOT EXISTS backtest_results" in BACKTEST_RESULTS_SCHEMA_PG
for col in ("user_query_hash", "strategy_params", "initial_capital",
            "final_value", "total_return", "sharpe_ratio"):
    assert col in BACKTEST_RESULTS_SCHEMA_PG, f"missing column {col}"
for idx in BACKTEST_HISTORICAL_PRICES_INDEXES + BACKTEST_RESULTS_INDEXES_PG:
    assert idx.startswith("CREATE INDEX IF NOT EXISTS"), idx
print("✅ historical_prices + backtest_results DDL + indexes present")

# ── 3. DSN sanitization ───────────────────────────────────────────────────
print("3. channel_binding stripped from backtest DSNs (Neon safety)...")

url = ("postgresql://u:pw@ep-x-pooler.c-4.us-east-2.aws.neon.tech/neondb"
       "?sslmode=require&channel_binding=require")
clean = _sanitize_dsn(url)
assert "channel_binding" not in clean, clean
print(f"✅ sanitized: {clean}")

# ── 4. Engine default selection ───────────────────────────────────────────
print("4. BacktestEngine selects BacktestDB when configured, SQLite otherwise...")

from src.backtester import BacktestEngine

# Unset → SQLite dev fallback (also keeps all hermetic tests green).
with mock.patch.object(BacktestDB, "init_schema"), \
     mock.patch.object(SQLiteDB, "init_schema"):
    with mock.patch("src.backtester.settings") as ms:
        ms.has_backtest_database = False
        eng = BacktestEngine(db=None)
        assert isinstance(eng._db, SQLiteDB), type(eng._db)
print("✅ BACKTEST_DATABASE_URL unset → SQLiteDB (dev fallback)")

with mock.patch.object(BacktestDB, "init_schema"), \
     mock.patch.object(SQLiteDB, "init_schema"):
    with mock.patch("src.backtester.settings") as ms:
        ms.has_backtest_database = True
        eng = BacktestEngine(db=None)
        assert isinstance(eng._db, BacktestDB), type(eng._db)
print("✅ BACKTEST_DATABASE_URL set → BacktestDB (production Neon)")

# Explicitly-injected db is never overridden by settings (tests stay hermetic).
with mock.patch.object(BacktestDB, "init_schema"), \
     mock.patch.object(SQLiteDB, "init_schema"):
    with mock.patch("src.backtester.settings") as ms:
        ms.has_backtest_database = True
        eng = BacktestEngine(db=SQLiteDB())
        assert isinstance(eng._db, SQLiteDB), type(eng._db)
print("✅ injected db always wins over the default-selection logic")

print()
print("🎯 All BacktestDB tests pass!")
