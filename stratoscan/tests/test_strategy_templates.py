"""Day 8 — every strategy template parses for free, simulates what its title
says, and does not churn. Hermetic: no DB, no network, no LLM.

Collectively the templates are also a regression suite for the interpreter:
if a parser change moves any of them off the free path, or changes the
rules they trade, this suite says which one.

Run: python -m tests.test_strategy_templates
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["DATABASE_URL"] = ""
os.environ.setdefault("JWT_SECRET", "templates-dummy-secret")
os.environ["ANALYTICS_ENABLED"] = "false"

from unittest import mock  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.backtester import _translate_to_rules, run_backtest  # noqa: E402
from src.database import STOCK_MASTER_COLUMNS  # noqa: E402
from src.deterministic_interpreter import (  # noqa: E402
    CONFIDENCE_THRESHOLD,
    DeterministicInterpreter,
)
from src.screener import StockScreener  # noqa: E402
from src.strategy_templates import TEMPLATES, simulates, templates_payload  # noqa: E402
from tests.fakes import FakeLLMRouter  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PARSER = DeterministicInterpreter()
SCREENER = StockScreener.__new__(StockScreener)  # filter building only — no DB
TECHNICAL = {"rsi_14", "sma_50", "sma_200", "high_52w", "low_52w",
             "bollinger_upper", "bollinger_lower", "macd_12_26", "atr_14"}


def frame(drift: float, vol: float, seed: int, n: int = 2520) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(drift / 252, vol / np.sqrt(252), n)))
    idx = pd.bdate_range("2016-01-04", periods=n)
    op = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"Open": op, "High": np.maximum(op, close) * 1.005,
                         "Low": np.minimum(op, close) * 0.995, "Close": close,
                         "Volume": 5_000_000}, index=idx)


MARKETS = {"up": frame(0.25, 0.20, 1), "choppy": frame(0.0, 0.30, 2), "down": frame(-0.15, 0.25, 3)}


def holds(rules) -> dict:
    out = {}
    for name, df in MARKETS.items():
        res = run_backtest("SYN", rules, "2016-01-04", str(df.index[-1].date()), df=df,
                           compute_robustness=False)
        days = [int(np.busday_count(t.buy_date, t.sell_date)) for t in res.trades if t.sell_date]
        out[name] = (res.total_trades, days)
    return out


# ═══════════════════════════════════════════════════════════════════════════
# 0. Shape
# ═══════════════════════════════════════════════════════════════════════════
assert 12 <= len(TEMPLATES) <= 15, len(TEMPLATES)
ids = [t.id for t in TEMPLATES]
assert len(set(ids)) == len(ids) and all(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", i) for i in ids)
assert len({t.query for t in TEMPLATES}) == len(TEMPLATES)
assert len({t.title for t in TEMPLATES}) == len(TEMPLATES)
for t in TEMPLATES:
    assert t.title and t.idea and t.category and len(t.query) <= 200, t.id
    assert not re.search(r"\b(best|good|top|recommend|guaranteed)\b", t.query, re.I), (
        f"{t.id}: fuzzy words drop parser confidence (and 'guaranteed' is a promise)"
    )
print(f"✅ {len(TEMPLATES)} templates: unique slug ids, titles and queries; no fuzzy words")


# ═══════════════════════════════════════════════════════════════════════════
# 1. Free: deterministic at >= 0.6, and zero LLM calls through the real path
# ═══════════════════════════════════════════════════════════════════════════
for t in TEMPLATES:
    iq, conf = PARSER.parse(t.query)
    assert conf >= CONFIDENCE_THRESHOLD, f"{t.id}: confidence {conf:.2f} → an LLM call per use"
    assert not iq.error, (t.id, iq.error)

fake = FakeLLMRouter()
with mock.patch("src.llm_router.get_router", return_value=fake):
    from src import interpreter

    interpreter._interpret_cache.clear()
    for t in TEMPLATES:
        got = interpreter.interpret_query(t.query)
        assert got.source == "deterministic", (t.id, got.source)
    assert fake.call_count == 0, f"templates made {fake.call_count} LLM call(s)"
print(f"✅ every template parses deterministically (≥ {CONFIDENCE_THRESHOLD}); 0 LLM calls for all "
      f"{len(TEMPLATES)}")


# ═══════════════════════════════════════════════════════════════════════════
# 2. Honest: the engine trades exactly the pinned rules; the screen keeps
#    everything the query asked for, on columns that have data
# ═══════════════════════════════════════════════════════════════════════════
for t in TEMPLATES:
    iq, _ = PARSER.parse(t.query)
    rules = _translate_to_rules(iq)
    assert tuple(rules["buy_rules"]) == t.buy_rules, (t.id, rules["buy_rules"])
    assert tuple(rules["sell_rules"]) == t.sell_rules, (t.id, rules["sell_rules"])
    skip, notes = SCREENER._resolve_contradictions(iq)
    assert not skip and not notes, f"{t.id}: the screener would drop part of it: {notes}"
    db_filters, applied, _ = SCREENER._build_filter_set(iq, set())
    assert applied, t.id
    cols = set(db_filters)
    for spec in db_filters.values():
        for clause in (spec if isinstance(spec, list) else [spec]):
            if isinstance(clause, tuple) and len(clause) >= 2 and isinstance(clause[1], str):
                cols.add(clause[1])
    assert cols <= set(STOCK_MASTER_COLUMNS), (t.id, cols - set(STOCK_MASTER_COLUMNS))
    if cols & TECHNICAL:
        # Technicals are refreshed for the 500 largest names only; a
        # technical screen over small caps would match nothing and relax.
        large = db_filters.get("market_cap")
        large_ok = any(
            isinstance(c, tuple) and c[0] == "gte" and c[1] >= 10e9
            for c in (large if isinstance(large, list) else [large])
        )
        assert large_ok or "sector" in db_filters, (
            f"{t.id}: technical screen without a large-cap or sector universe"
        )
    assert simulates(t).startswith("Buys when ") and "sells when" in simulates(t)
print("✅ each template trades exactly its pinned rules; nothing is dropped as contradictory; "
      "every column exists and has data")


# ═══════════════════════════════════════════════════════════════════════════
# 3. Runs green, and does not churn
# ═══════════════════════════════════════════════════════════════════════════
for t in TEMPLATES:
    iq, _ = PARSER.parse(t.query)
    res = holds(_translate_to_rules(iq))
    assert sum(n for n, _ in res.values()) > 0, f"{t.id}: never trades on any market"
    for market, (n, days) in res.items():
        if days:
            assert min(days) >= 1, f"{t.id}/{market}: exit on the entry bar"
            assert np.median(days) >= 10, f"{t.id}/{market}: median hold {np.median(days)} days"
print("✅ every template backtests on up/choppy/down synthetic markets; median hold ≥ 10 "
      "trading days, never a same-bar exit")

# Why momentum templates are not in the library (pinned so that when the
# engine's momentum exit changes, this fails and says the door is open).
iq, _ = PARSER.parse("large cap stocks with bullish momentum above their 50-day moving average")
momentum = _translate_to_rules(iq)
assert momentum["buy_rules"] == ["price > sma_50"] and momentum["sell_rules"] == ["rsi > 70"]
_, up_days = holds(momentum)["up"]
assert np.median(up_days) < 3, (
    "momentum no longer churns — revisit the excluded momentum templates in "
    "src/strategy_templates.py"
)
print(f"✅ known limit pinned: momentum entry + RSI-70 exit churns (median hold "
      f"{int(np.median(up_days))} day on an uptrend) — excluded from the library")


# ═══════════════════════════════════════════════════════════════════════════
# 4. The parser reads "their" (plural phrasing) like "its"
# ═══════════════════════════════════════════════════════════════════════════
for phrase, flag in [
    ("stocks trading above their 200-day moving average", "above_sma_200"),
    ("stocks below their 50-day moving average", "below_sma_50"),
    ("stocks above their 50-day moving average", "above_sma_50"),
    ("stocks near their 52-week low", "near_52w_low"),
    ("stocks close to their 52-week high", "near_52w_high"),
]:
    iq, _ = PARSER.parse(phrase)
    assert getattr(iq, flag) is True, (phrase, flag)
print("✅ 'above/below/near THEIR …' sets the same trend flags as 'its'")


# ═══════════════════════════════════════════════════════════════════════════
# 5. Served in /meta, and mirrored by the frontend
# ═══════════════════════════════════════════════════════════════════════════
with mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()):
    from fastapi.testclient import TestClient

    import src.api as api

    meta = TestClient(api.app).get("/meta").json()
assert meta["templates"] == templates_payload()
print("✅ GET /meta serves the library verbatim")

ts = (ROOT.parent / "frontend" / "src" / "lib" / "templates.ts").read_text(encoding="utf-8")
fe_ids = re.findall(r"^\s*id: '([^']+)'", ts, re.M)
fe_queries = re.findall(r"^\s*query:\s*'([^']+)'", ts, re.M)
assert fe_ids == ids, f"frontend ids drifted: {fe_ids}"
assert fe_queries == [t.query for t in TEMPLATES], "frontend queries drifted from the backend"
print("✅ frontend/src/lib/templates.ts mirrors the backend list (ids and queries, in order)")

print("\nALL STRATEGY-TEMPLATE CHECKS PASSED")
