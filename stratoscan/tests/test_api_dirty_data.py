"""Hermetic HTTP-level regression test for the dirty-data /backtest lifecycle.

Run: python -m tests.test_api_dirty_data

This locks the bug class found by the end-to-end lifecycle probe: with
inf/NaN-corrupted price rows, /backtest used to 500 (Starlette
JSONResponse allow_nan=False on an inf-poisoned equity curve) — and on a
fresh SQLite file it silently returned HTTP 200 with ZERO results
("no such table: historical_prices" on every symbol).

Unlike the engine-level sections 20-23 in test_backtester.py, this drives
the REAL FastAPI HTTP surface end to end:

  register (free) → /backtest with dirty frames + corrupt SPY benchmark
  → real engine →
  real sim → real JSON serialization → all numerics finite.

Fully hermetic: FakeUserDB, FakeLLMRouter, mocked fetch, temp SQLite,
fake screener. Zero network, zero live API calls.
"""

import atexit
import math
import os
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Hermetic: keep the API rate limiter OFF (in-memory AND Upstash) so this
# suite never touches the network or real rate-limit counters.
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["REDIS_RATE_LIMIT_ENABLED"] = "false"

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from tests.fakes import FakeUserDB, FakeLLMRouter

# ── Hermetic wiring (mirrors tests/test_auth.py) ───────────────────────────
fake_db = FakeUserDB()
mock.patch("src.database.PostgresDB", return_value=fake_db).start()
# RSI-based canned strategy (max_rsi → buy rsi<30) so the real engine
# actually generates trades on the sawtooth fixture. The default canned
# JSON (max_pe_ratio) translates to a `pe < 15` rule that can never fire
# on OHLCV-only data.
mock.patch(
    "src.llm_router.get_router",
    return_value=FakeLLMRouter(json_str='{"sector": "Technology", "max_rsi": 30}'),
).start()

from src.config import settings
from src.api import app

_tmp = tempfile.TemporaryDirectory()
# Clean up even if an assertion aborts the module (not just on success).
atexit.register(_tmp.cleanup)
settings.backtest_db_path = os.path.join(_tmp.name, "test_dirty.db")
# Keep this suite hermetic: with BACKTEST_DATABASE_URL set in .env, the
# BacktestEngine default would flip to the real Neon BacktestDB and this
# test would write its DIRTYA/DIRTYB/SPY probe data into the production
# backtest database. Force the SQLite dev fallback so the whole lifecycle
# stays on the temp file (mirrors how the test already redirects
# backtest_db_path).
settings.backtest_database_url = ""

client = TestClient(app)


# ── Dirty fixtures: real OHLCV frames, corrupted like real Yahoo data ──────
def _frame(symbol: str, n: int = 2600, dirty_at: int = 100) -> pd.DataFrame:
    """10 years of business days; one inf row + one NaN Close row."""
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    base = 50 + 30 * np.sin(np.arange(n) / 40 * 2 * np.pi) + np.linspace(0, 20, n)
    df = pd.DataFrame(
        {
            "Open": base - 1,
            "High": base + 2,
            "Low": base - 2,
            "Close": base,
            "Volume": np.full(n, 1_000_000, dtype=float),
            "Adj Close": base,  # auto_adjust=False style download
        },
        index=idx,
    )
    df.iloc[dirty_at, df.columns.get_loc("Close")] = np.inf
    df.iloc[dirty_at, df.columns.get_loc("Open")] = np.inf
    df.iloc[dirty_at, df.columns.get_loc("High")] = np.inf
    df.iloc[dirty_at, df.columns.get_loc("Low")] = np.inf
    df.iloc[dirty_at, df.columns.get_loc("Adj Close")] = np.inf
    df.iloc[dirty_at, df.columns.get_loc("Volume")] = np.inf
    df.iloc[dirty_at + 5, df.columns.get_loc("Close")] = np.nan
    df.iloc[dirty_at + 5, df.columns.get_loc("Adj Close")] = np.nan
    return df


DIRTY_A = _frame("DIRTYA")
DIRTY_B = _frame("DIRTYB")
SPY = _frame("SPY", dirty_at=500)


def _fake_fetch(symbol: str, start_date: Optional[str] = None):
    # Signature matches fetch_historical_data's new start_date kwarg (the
    # engine passes it for cache-coverage refetches). The fake ignores it —
    # its frames already span the whole test window.
    return {"DIRTYA": DIRTY_A, "DIRTYB": DIRTY_B, "SPY": SPY}.get(symbol)


class _FakeScreener:
    def screen(self, interpreted, limit=5):
        return SimpleNamespace(
            results=[SimpleNamespace(symbol="DIRTYA"), SimpleNamespace(symbol="DIRTYB")]
        )


mock.patch("src.screener.StockScreener", return_value=_FakeScreener()).start()
mock.patch("src.backtester.fetch_historical_data", side_effect=_fake_fetch).start()


def _all_finite(obj) -> list:
    """Walk a JSON-decoded structure; return (path, value) for bad numerics."""
    bad = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            for b in _all_finite(v):
                bad.append((f"{k}.{b[0]}", b[1]))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            for b in _all_finite(v):
                bad.append((f"[{i}].{b[0]}", b[1]))
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        if not math.isfinite(obj):
            bad.append(("", obj))
    return bad


# ── 1. Auth (no paywall — everything is free) ─────────────────────────────
print("1. Auth: register → /backtest directly (no paywall, no premium flip)...")

r = client.post(
    "/auth/register",
    json={"email": "dirty@stratoscan.app", "password": "hunter2hunter2"},
)
assert r.status_code == 200 and "access_token" in r.json(), r.text[:200]
headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
assert r.json()["user"]["plan"] == "free", "new account must start free"
print("✅ registered free account (auth only, for per-user rate limits)")

# ── 2. THE original bug: /backtest with dirty data → real JSON ─────────────
print("2. /backtest with inf+NaN rows and corrupt SPY benchmark...")

r = client.post(
    "/backtest",
    json={"query": "oversold stocks with rising momentum", "top_n": 2},
    headers=headers,
)
assert r.status_code == 200, f"dirty-data /backtest: {r.status_code} {r.text[:300]}"
body = r.json()

assert set(body["results"].keys()) == {"DIRTYA", "DIRTYB"}, list(body["results"].keys())
for sym, res in body["results"].items():
    bad = _all_finite(res)
    assert not bad, f"{sym} JSON contains non-finite values: {bad[:3]}"
    assert res["equity_curve"] and not _all_finite(res["equity_curve"]), f"{sym} equity curve"
    assert len(res["trades"]) > 0 and res["total_trades"] > 0, f"{sym} no trades"
    t = res["trades"][0]
    assert t["buy_date"] and t["sell_date"] and t["pnl"] is not None, f"{sym} trade fields"
    assert res["beta"] is None or math.isfinite(res["beta"]), f"{sym} beta"
    assert res["monte_carlo"] is not None, f"{sym} monte_carlo missing"
    assert res["walk_forward"] is not None, f"{sym} walk_forward missing"
    assert res["parameter_sensitivity"] is not None, f"{sym} sensitivity missing"
print("✅ HTTP 200; both symbols survive; all JSON finite; trades + robustness present")

# ── 3. Storage: corrupted rows never persisted to SQLite ───────────────────
print("3. Storage: inf/NaN never persisted to SQLite...")

from src.database import SQLiteDB

db = SQLiteDB(db_path=settings.backtest_db_path)
rows = [dict(r) for r in db.get_historical_prices("DIRTYA", "2016-01-01", "2026-12-31")]
assert len(rows) > 2000, f"stored rows: {len(rows)}"
bad_stored = [
    (row["date"], k, row[k])
    for row in rows
    for k in ("open", "high", "low", "close", "raw_close")
    if row.get(k) is None or not math.isfinite(float(row[k]))
]
assert not bad_stored, f"corrupted rows persisted: {bad_stored[:3]}"
spy_rows = [dict(r) for r in db.get_historical_prices("SPY", "2016-01-01", "2026-12-31")]
assert len(spy_rows) > 2000 and not any(
    not math.isfinite(float(r[k])) or r[k] is None
    for r in spy_rows
    for k in ("open", "high", "low", "close")
), "SPY cached unclean"
db.close()
print("✅ inf/NaN rows cleaned before storage (DIRTYA + SPY)")

# ── 4. Robustness crash must not drop the headline result ──────────────────
print("4. Robustness-analysis crash isolation...")

import src.backtester as bt
from src.backtester import BacktestEngine
from src.interpreter import InterpretedQuery

iq = InterpretedQuery(raw_input="oversold stocks", filters=[], sector="Technology", momentum="rising")
with mock.patch.object(bt, "walk_forward_analysis", side_effect=RuntimeError("wf boom")), \
     mock.patch.object(bt, "parameter_sensitivity", side_effect=RuntimeError("sens boom")):
    eng = BacktestEngine(db=SQLiteDB(db_path=settings.backtest_db_path))
    res = eng.backtest(iq, ["DIRTYA"])
assert "DIRTYA" in res and res["DIRTYA"].total_trades > 0, "headline dropped"
assert res["DIRTYA"].walk_forward is None and res["DIRTYA"].parameter_sensitivity is None
assert res["DIRTYA"].final_value > 0 and res["DIRTYA"].total_return is not None
eng._db.close()
print("✅ robustness crash → headline preserved, analyses None")

print()
print("🎯 All API dirty-data tests pass!")
_tmp.cleanup()  # idempotent — atexit.register also covers the abort path
