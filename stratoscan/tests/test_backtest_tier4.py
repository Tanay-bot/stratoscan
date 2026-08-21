"""Hermetic tests for the Tier 4 backtester upgrades — no network, no live DB.

Run: python -m tests.test_backtest_tier4

Covers the three Tier 4 product additions:

1. drawdown_curve: run_backtest now exposes the full drawdown series (same
   dates as the equity curve, negative decimals, downsampled) — the second
   chart every backtester UI shows. Also: no trades → empty curve.
2. API date range: /backtest + /backtest/export accept start_date/end_date;
   start > end → 422, malformed format → 422 (pydantic pattern); valid
   dates thread through engine.backtest_top_results → run_backtest
   (observable in result.start_date/end_date).
3. CSV export: /backtest/export returns text/csv with the header + one row
   per CLOSED trade, requires auth (401 without a token), and never emits
   open trades.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Hermetic: keep the API rate limiter OFF (in-memory AND Upstash) so this
# suite never touches the network or real rate-limit counters.
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["REDIS_RATE_LIMIT_ENABLED"] = "false"

from unittest import mock  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.backtester import BacktestResult, Trade, run_backtest  # noqa: E402
from src.auth import hash_password  # noqa: E402
from tests.fakes import FakeLLMRouter, FakeUserDB  # noqa: E402

# ── Sawtooth fixture (same regime as test_backtester) ───────────────────────
_N_DAYS = 6 * 252
_IDX = pd.date_range("2020-01-01", periods=_N_DAYS, freq="B")
_CLOSE = 50 + 30 * np.sin(np.arange(_N_DAYS) / 40 * 2 * np.pi) + np.linspace(0, 20, _N_DAYS)
_DF = pd.DataFrame(
    {
        "Open": _CLOSE,
        "High": _CLOSE * 1.01,
        "Low": _CLOSE * 0.99,
        "Close": _CLOSE,
        "Volume": np.full(_N_DAYS, 1_000_000),
    },
    index=_IDX,
)
_RULES = {
    "initial_capital": 10_000.0,
    "fee_rate": 0.001,
    "slippage": 0.0005,
    "buy_rules": ["rsi < 30"],
    "sell_rules": ["rsi > 70"],
}


# ── 1. drawdown_curve ────────────────────────────────────────────────────────
print("1. drawdown_curve...")

res = run_backtest("TEST", _RULES, "2020-01-01", "2025-12-31", df=_DF)
assert res.total_trades >= 3, "sawtooth must trade (fixture sanity)"
assert res.drawdown_curve, "drawdown_curve must be populated on a traded backtest"
# Same date basis as the equity curve — the two charts must line up.
assert [p["date"] for p in res.drawdown_curve] == [
    p["date"] for p in res.equity_curve
], "drawdown and equity curves must share the same date axis"
# Values are negative (or zero) drawdowns, never positive.
assert all(v["value"] <= 0.0 for v in res.drawdown_curve), (
    "drawdown values must be <= 0"
)
# The deepest point of the curve equals max_drawdown (within rounding).
deepest = min(v["value"] for v in res.drawdown_curve)
assert res.max_drawdown is not None
assert abs(deepest - res.max_drawdown) <= 0.0051, (
    f"deepest curve point {deepest:.4f} != max_drawdown {res.max_drawdown:.4f}"
)
# Downsampled to the same cap as the equity curve.
from src.backtester import MAX_EQUITY_CURVE_POINTS
assert len(res.drawdown_curve) <= MAX_EQUITY_CURVE_POINTS + 1

# No trades → empty curve (consistent with max_drawdown=None).
flat_df = pd.DataFrame(
    {
        "Open": np.full(100, 50.0),
        "High": np.full(100, 50.0),
        "Low": np.full(100, 50.0),
        "Close": np.full(100, 50.0),
        "Volume": np.full(100, 1_000_000),
    },
    index=pd.date_range("2024-01-01", periods=100, freq="B"),
)
flat = run_backtest("FLAT", _RULES, "2024-01-01", "2024-05-31", df=flat_df)
assert flat.total_trades == 0
assert flat.drawdown_curve == [], "no-trade backtest must have an empty drawdown curve"
assert flat.max_drawdown is None
print("✅ curve populated, date-aligned, deepest point == max_drawdown, empty on no-trade")


# ── 2. API: date-range validation + threading ───────────────────────────────
print("2. API date range...")

# Canned engine: captures the args /backtest threads through and returns a
# single result with one closed trade — zero network, zero DB.
class _FakeEngine:
    def __init__(self):
        self.calls = []

    def backtest_top_results(
        self, interpreted, top_n=5, strategy_rules=None,
        start_date=None, end_date=None, max_sector_exposure=None,
        symbols=None, progress_cb=None,
    ):
        self.calls.append({
            "top_n": top_n,
            "start_date": start_date,
            "end_date": end_date,
        })
        trade = Trade(
            symbol="TEST", buy_date="2020-03-01", buy_price=50.0,
            sell_date="2020-03-15", sell_price=55.0, shares=100,
            pnl=450.0, pnl_pct=0.09, is_open=False, side="long",
            exit_reason="take_profit",
        )
        result = BacktestResult(
            symbol="TEST",
            strategy_params=strategy_rules or {},
            start_date=start_date or "2020-01-01",
            end_date=end_date or "2025-12-31",
            initial_capital=10_000.0,
            final_value=11_000.0,
            total_return=0.10,
            sharpe_ratio=1.2,
            max_drawdown=-0.08,
            win_rate=0.5,
            total_trades=1,
            trades=[trade],
            equity_curve=[{"date": "2020-03-01", "value": 10000.0}],
            drawdown_curve=[{"date": "2020-03-01", "value": -0.08}],
        )
        return {"TEST": result}


fake_db = FakeUserDB()
fake_db.create_user("t4@stratoscan.app", hash_password("tier4pass123"), email_verified=True)
mock.patch("src.database.PostgresDB", return_value=fake_db).start()
mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()
fake_engine = _FakeEngine()
mock.patch("src.backtester.BacktestEngine", return_value=fake_engine).start()

from src.api import app  # noqa: E402

client = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(app)

r = client.post("/auth/login", json={
    "email": "t4@stratoscan.app", "password": "tier4pass123",
})
assert r.status_code == 200, r.text
token = r.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}


def _mk(overrides: dict) -> dict:
    body = {"query": "buy oversold stocks", "top_n": 3}
    body.update(overrides)
    return body


# start > end → 422.
r = client.post("/backtest", json=_mk({
    "start_date": "2025-01-01", "end_date": "2020-01-01",
}), headers=headers)
assert r.status_code == 422, f"start>end must 422: {r.status_code} {r.text[:120]}"
assert "start_date" in r.json()["detail"].lower(), r.text
# Malformed date → 422 (pydantic pattern ^\\d{4}-\\d{2}-\\d{2}$).
r = client.post("/backtest", json=_mk({"start_date": "2025/01/01"}), headers=headers)
assert r.status_code == 422, f"bad date format must 422: {r.status_code}"
# Valid dates thread through to the engine.
r = client.post("/backtest", json=_mk({
    "start_date": "2021-01-01", "end_date": "2023-12-31",
}), headers=headers)
assert r.status_code == 200, r.text
assert fake_engine.calls, "engine was never invoked"
last = fake_engine.calls[-1]
assert last["start_date"] == "2021-01-01", last
assert last["end_date"] == "2023-12-31", last
body = r.json()
assert body["results"]["TEST"]["drawdown_curve"] == [
    {"date": "2020-03-01", "value": -0.08}
], body["results"]["TEST"]["drawdown_curve"]
# Defaults: no dates → engine receives None (engine applies its own 10y/today).
fake_engine.calls.clear()
r = client.post("/backtest", json=_mk({}), headers=headers)
assert r.status_code == 200, r.text
assert fake_engine.calls[-1]["start_date"] is None
assert fake_engine.calls[-1]["end_date"] is None
print("✅ 422 on start>end + bad format; valid dates threaded; defaults pass None")


# ── 3. CSV export ────────────────────────────────────────────────────────────
print("3. CSV export...")

# Auth gate: no token → 401.
r = client.post("/backtest/export", json=_mk({}))
assert r.status_code == 401, f"export without auth must 401: {r.status_code}"

r = client.post("/backtest/export", json=_mk({
    "start_date": "2020-01-01", "end_date": "2024-01-01",
}), headers=headers)
assert r.status_code == 200, r.text
assert r.headers["content-type"].startswith("text/csv"), r.headers
csv_text = r.text
lines = csv_text.strip().splitlines()
assert lines[0] == ",".join([
    "symbol", "side", "buy_date", "buy_price", "sell_date",
    "sell_price", "shares", "pnl", "pnl_pct", "exit_reason",
]), lines[0]
# One closed trade → exactly one data row, and it matches the canned trade.
assert len(lines) == 2, lines
assert "TEST" in lines[1] and "take_profit" in lines[1], lines[1]
assert fake_engine.calls[-1]["start_date"] == "2020-01-01"
# The request sent end_date="2024-01-01" — assert it EXACTLY, so a threading
# regression can't hide behind an OR'd fallback value.
assert fake_engine.calls[-1]["end_date"] == "2024-01-01", fake_engine.calls[-1]

# Open trades never appear in the CSV.
fake_engine2 = _FakeEngine()
open_trade = Trade(
    symbol="OPEN", buy_date="2020-03-01", buy_price=50.0,
    shares=100, is_open=True, side="long",
)
fake_engine2.backtest_top_results = lambda *a, **k: {
    "OPEN": BacktestResult(
        symbol="OPEN", strategy_params={}, start_date="2020-01-01",
        end_date="2024-01-01", initial_capital=10_000.0, final_value=10_500.0,
        total_return=0.05, total_trades=1, trades=[open_trade],
        equity_curve=[{"date": "2020-03-01", "value": 10000.0}],
    )
}
with mock.patch("src.backtester.BacktestEngine", return_value=fake_engine2):
    r = client.post("/backtest/export", json=_mk({}), headers=headers)
assert r.status_code == 200, r.text
assert "OPEN" not in r.text, "open trades must be excluded from the CSV"
print("✅ CSV: 401 unauth / text/csv / header + closed-trade rows / open trades excluded")


# ── 4. Zero-match backtest → explicit message (no fake success) ─────────────
print("4. Zero-match backtest message...")

# When the engine returns NO results (0 candidates matched the filters, or
# explicit tickers had no usable price data), /backtest must tell the user
# WHY instead of returning a 200 that looks like a successful empty run.
class _EmptyEngine:
    def backtest_top_results(self, *a, **k):
        return {}


with mock.patch("src.backtester.BacktestEngine", return_value=_EmptyEngine()):
    r = client.post("/backtest", json=_mk({}), headers=headers)
assert r.status_code == 200, f"zero-match must still 200: {r.status_code} {r.text[:120]}"
body = r.json()
assert body["total_symbols_tested"] == 0, body
assert body["results"] == {}, body
assert body.get("message"), f"zero-match must carry an explanatory message: {body}"
assert "0 candidates" in body["message"] or "unavailable" in body["message"], body["message"]

# A normal run must NOT carry the message (None → absent/null in JSON).
r = client.post("/backtest", json=_mk({}), headers=headers)
assert r.status_code == 200, r.text
assert r.json().get("message") is None, r.json()
print("✅ zero-match → 200 + message field; normal run → message null")


mock.patch.stopall()
print()
print("🎯 All Tier 4 backtester tests pass!")
