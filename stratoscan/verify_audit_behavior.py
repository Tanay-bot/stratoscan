"""Behavioral probe — verifies the audit test suites against the REAL code paths.

Run: python verify_audit_behavior.py

This is NOT a unit test; it drives the actual production code end-to-end with
only the external boundaries mocked (LLM, Finnhub, Postgres):

  1. NL query -> interpret_query (real) -> StockScreener.screen (real)
     against a fake DB whose filter semantics MIRROR database._column_conditions
     exactly. Asserts: correct stocks selected, same-column bounds ANDed,
     derived trend filters enforced, pagination exact, applied-filter echo,
     composite score + why_matched populated, summary renders.
  2. News lifecycle: fetch (mocked) -> score (real batched LLM path, mocked
     router) -> build (real) -> store (fake NewsDB) -> retrieve via the real
     get_sentiment_for_screen. Asserts the stored score round-trips.
  3. Scheduler lifecycle: real DataRefreshScheduler + real JobMonitor +
     InMemory store: run price refresh (success -> records), a failing cycle
     (failed status + pool still closed), and the news job. Asserts the
     monitor store sees the exact state transitions.
  4. CLI lifecycle: main() -> cmd_backtest threads dates into the engine;
     malformed dates abort BEFORE the LLM call.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from unittest import mock  # noqa: E402

from src import news as news_mod  # noqa: E402
from src.screener import StockScreener  # noqa: E402
from src.job_monitor import InMemoryJobMonitorStore, JobMonitor  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════════
# Fake DB that mirrors database.PostgresDB._column_conditions semantics exactly
# ═══════════════════════════════════════════════════════════════════════════════

def _matches(row: dict, filters: dict) -> bool:
    """Evaluate a filter dict against a row using REAL _column_conditions rules."""
    for col, value in filters.items():
        # list of all-tuples -> ANDed conditions on the same column
        # (mirrors _column_conditions: each sub is a FULL condition on `col`.)
        if isinstance(value, list) and value and all(isinstance(v, tuple) for v in value):
            for sub in value:
                if not _matches(row, {col: sub}):
                    return False
            continue
        # column-to-column comparison
        if isinstance(value, tuple) and len(value) >= 2 and value[0].endswith("_col"):
            op, other = value[0], value[1]
            lhs = row.get(col)
            rhs = row.get(other)
            if lhs is None or rhs is None:
                return False
            if len(value) == 3 and value[0].endswith("frac_col"):
                factor = float(value[2])
                rhs = rhs * factor
            if op in ("gt_col", "gte_frac_col"):
                if not (lhs > rhs if op == "gt_col" else lhs >= rhs):
                    return False
            else:  # lt_col / lte_col / lte_frac_col
                if not (lhs < rhs if op == "lt_col" else lhs <= rhs):
                    return False
            continue
        # (op, operand) range / exact / null-guard
        if isinstance(value, tuple) and len(value) == 2:
            op, operand = value
            if op == "neq" and operand is None:
                if row.get(col) is None:
                    return False
                continue
            if op == "eq" and operand is None:
                if row.get(col) is not None:
                    return False
                continue
            cur = row.get(col)
            if cur is None:
                return False
            ops = {"lt": lambda a, b: a < b, "lte": lambda a, b: a <= b,
                   "gt": lambda a, b: a > b, "gte": lambda a, b: a >= b,
                   "eq": lambda a, b: a == b, "neq": lambda a, b: a != b}
            if not ops[op](cur, operand):
                return False
            continue
        # IN list / scalar equality
        if isinstance(value, (list, tuple)):
            if row.get(col) not in value:
                return False
        elif row.get(col) != value:
            return False
    return True


class FakeScreenDB:
    """In-memory DB with the SAME interface + filter semantics as PostgresDB."""

    def __init__(self, rows):
        self._rows = list(rows)

    def _matched(self, filters):
        return [r for r in self._rows if _matches(r, filters)]

    def count_stocks(self, filters):
        return len(self._matched(filters))

    def query_stocks(self, filters, limit=50, offset=0, sort_by="market_cap",
                     sort_desc=True):
        rows = self._matched(filters)
        rows.sort(
            key=lambda r: (r.get(sort_by) is not None, r.get(sort_by)),
            reverse=sort_desc,
        )
        return rows[offset: offset + limit]

    def aggregate_stats(self, filters, columns):
        rows = self._matched(filters)
        out = {}
        for col in columns:
            vals = [r[col] for r in rows if r.get(col) is not None]
            if len(vals) < 2:
                out[col] = {"mean": 0.0, "std": 0.0, "valid": len(vals)}
                continue
            mean = sum(vals) / len(vals)
            var = sum((v - mean) ** 2 for v in vals) / len(vals)
            out[col] = {"mean": mean, "std": var ** 0.5, "valid": len(vals)}
        return out

    def data_freshness(self, max_age_minutes=15):
        return {"oldest_age_minutes": 0.5, "max_updated_at": None,
                "stale_count": 0, "total_rows": len(self._rows)}

    def get_top_symbols(self, limit=300):
        return [r["symbol"] for r in self._matched({"market_cap": ("neq", None)})[:limit]]

    def close(self):
        pass


def _mk_llm(content: str):
    router = mock.MagicMock()
    router.chat_completion.return_value.choices[0].message.content = content
    return router


print("=" * 70)
print("STRATOSCAN BEHAVIORAL PROBE — real code paths, mocked boundaries")
print("=" * 70)

# ── 1. NL query → interpret (real) → screen (real) against fabricated universe ──
print("\n[1] Interpreter → Screener pipeline")

UNIVERSE = [
    # symbol, sector, mcap($), pe, rsi, price, sma50, sma200, high52w
    {"symbol": "TINYT", "company_name": "Tiny Tech", "sector": "Technology",
     "market_cap": 1_500_000_000, "pe_ratio": 12.0, "rsi_14": 60.0,
     "current_price": 55.0, "sma_50": 50.0, "sma_200": 45.0, "high_52w": 60.0},
    {"symbol": "BORDT", "company_name": "Border Tech", "sector": "Technology",
     "market_cap": 2_000_000_000, "pe_ratio": 15.0, "rsi_14": 50.0,
     "current_price": 70.0, "sma_50": 65.0, "sma_200": 60.0, "high_52w": 72.0},
    {"symbol": "BIGT", "company_name": "Big Tech", "sector": "Technology",
     "market_cap": 800_000_000_000, "pe_ratio": 28.0, "rsi_14": 65.0,
     "current_price": 180.0, "sma_50": 170.0, "sma_200": 160.0, "high_52w": 190.0},
    {"symbol": "PRCY", "company_name": "Pricey Tech", "sector": "Technology",
     "market_cap": 1_000_000_000, "pe_ratio": 18.0, "rsi_14": 58.0,
     "current_price": 40.0, "sma_50": 38.0, "sma_200": 36.0, "high_52w": 45.0},
    {"symbol": "SLOWT", "company_name": "Slow Tech", "sector": "Technology",
     "market_cap": 1_200_000_000, "pe_ratio": 10.0, "rsi_14": 42.0,
     "current_price": 30.0, "sma_50": 31.0, "sma_200": 32.0, "high_52w": 35.0},
    {"symbol": "HTH", "company_name": "Health Small", "sector": "Healthcare",
     "market_cap": 1_100_000_000, "pe_ratio": 9.0, "rsi_14": 55.0,
     "current_price": 20.0, "sma_50": 19.0, "sma_200": 18.0, "high_52w": 22.0},
    {"symbol": "MICRT", "company_name": "Micro Tech", "sector": "Technology",
     "market_cap": 100_000_000, "pe_ratio": 8.0, "rsi_14": 70.0,
     "current_price": 10.0, "sma_50": 9.0, "sma_200": 8.5, "high_52w": 12.0},
    {"symbol": "MIDT", "company_name": "Mid Tech", "sector": "Technology",
     "market_cap": 5_000_000_000, "pe_ratio": 14.0, "rsi_14": 62.0,
     "current_price": 90.0, "sma_50": 85.0, "sma_200": 80.0, "high_52w": 95.0},
]

# Freshness guard: the screener now excludes rows older than
# STALE_EXCLUSION_DAYS in SQL by default. Stamp the fabricated universe
# with a current updated_at so the guard lets them through.
_fresh_ts = datetime.now(timezone.utc).isoformat()
for _row in UNIVERSE:
    _row["updated_at"] = _fresh_ts

fake_db = FakeScreenDB(UNIVERSE)
screener = StockScreener(db=fake_db)

LLM_JSON = (
    '{"sector":"Technology","min_market_cap_billions":0.3,'
    '"max_market_cap_billions":2.0,"max_pe_ratio":15,"min_rsi":50,'
    '"momentum":"bullish","above_sma_50":true}'
)
with mock.patch("src.llm_router.get_router", return_value=_mk_llm(LLM_JSON)):
    report = screener.screen_from_natural_language(
        "find undervalued small-cap tech stocks with rising momentum"
    )

symbols = sorted(r.symbol for r in report.results)
assert symbols == ["BORDT", "TINYT"], f"expected exactly the 2 matching names, got {symbols}"
assert report.total_matches == 2, report.total_matches

# Same-column bounds must BOTH be enforced (0.3B ≤ mcap ≤ 2.0B): MICRT (0.1B)
# fails the min, MIDT (5B) fails the max, BIGT (800B) fails the max + PE.
# PRCY fails max_pe (18>15), SLOWT fails min_rsi (42<50) AND price>SMA50,
# HTH fails the sector.
for excluded in ("MICRT", "MIDT", "BIGT", "PRCY", "SLOWT", "HTH"):
    assert excluded not in symbols, f"{excluded} must NOT match"

# Derived trend filter: price > SMA50 enforced (SLOWT: 30 < 31 → excluded).
# applied echo must be honest (never claims a filter that didn't run).
echo = report.applied_filters
for expected in ("sector = Technology", "market_cap ≥ 0.3", "market_cap ≤ 2.0",
                 "pe_ratio ≤ 15", "rsi_14 ≥ 50", "price > SMA50",
                 "has price + market cap (completeness guard)"):
    assert expected in echo, f"applied echo missing: {expected}"

# Composite score + why_matched must be populated (Tier 3 path executed).
for r in report.results:
    assert r.score is not None, f"{r.symbol} missing composite score"
    assert r.why_matched, f"{r.symbol} missing why_matched"
    assert r.pct_off_52w_high is not None, f"{r.symbol} missing 52w % off"

# Summary renders.
summary = report.summary
assert "2 stocks" in summary and "TINYT" in summary and "BORDT" in summary

# Pagination: limit=1 → has_more True; offset=1 → has_more False, still 2 total.
rep1 = screener.screen(report.query, limit=1, offset=0)
assert len(rep1.results) == 1 and rep1.has_more is True and rep1.total_matches == 2
rep2 = screener.screen(report.query, limit=1, offset=1)
assert len(rep2.results) == 1 and rep2.has_more is False and rep2.total_matches == 2

print("✅ correct names selected; same-column bounds ANDed; trend filter enforced")
print("✅ echo honest; composite score + why_matched + 52w% populated; summary renders")
print("✅ pagination exact (total=2, page=1, has_more True→False)")


# ── 2. News lifecycle: fetch → score → build → store → retrieve ──────────────
print("\n[2] News pipeline store→retrieve round-trip")

class FakeNewsDB:
    """Minimal NewsDB fake: real upsert/retrieve shape, in-memory storage."""

    def __init__(self):
        self._articles = []
        self._sentiment = {}
        self.closed = False

    def upsert_articles(self, articles):
        n = 0
        for a in articles:
            if (a["symbol"], a["url"]) not in self._articles:
                self._articles.append((a["symbol"], a["url"]))
                n += 1
        return n

    def upsert_sentiment(self, record):
        self._sentiment[record["symbol"]] = record

    def get_sentiments_for_symbols(self, symbols):
        return {s: dict(self._sentiment[s]) for s in symbols if s in self._sentiment}

    def close(self):
        self.closed = True

fake_news = FakeNewsDB()
raw_articles = [
    {"headline": "Apple beats earnings, stock jumps 4%", "summary": "s1",
     "source": "Reuters", "url": "http://x/1", "datetime": 1750000000},
    {"headline": "Apple faces antitrust probe", "summary": "s2",
     "source": "Bloomberg", "url": "http://x/2", "datetime": 1750000100},
]
sentiment_llm = (
    '{"stocks": {"AAPL": {"articles": ['
    '{"index": 0, "sentiment": "positive", "score": 0.7},'
    '{"index": 1, "sentiment": "negative", "score": -0.5}]}}}'
)
with mock.patch.object(news_mod, "fetch_company_news", return_value=raw_articles), \
     mock.patch("src.llm_router.get_router", return_value=_mk_llm(sentiment_llm)), \
     mock.patch.object(type(news_mod.settings), "has_news_database",
                       mock.PropertyMock(return_value=True)):
    # pool=mock.Mock() keeps build_stock_news from initializing the REAL
    # Finnhub pool (fetch is mocked anyway).
    stored = news_mod.refresh_stock_news("AAPL", db=fake_news, pool=mock.Mock())
    lookup = news_mod.get_sentiment_for_screen(["AAPL"], db=fake_news)

assert stored is not None and stored.symbol == "AAPL"
assert len(stored.articles) == 2
assert stored.company_news_score == 0.1  # mean(0.7, -0.5) = 0.1
assert lookup["AAPL"]["company_news_score"] == 0.1, \
    f"stored sentiment must be retrievable, got {lookup}"
assert lookup["AAPL"]["article_count"] == 2
print("✅ fetch→score→build→store→retrieve round-trip; aggregate 0.1 survives the DB")


# ── 3. Scheduler lifecycle: run → monitor records → pool close ───────────────
print("\n[3] Scheduler run→monitor→pool-close lifecycle")

import src.scheduler as sched_mod  # noqa: E402

monitor = JobMonitor(InMemoryJobMonitorStore())
fake_pool = mock.Mock()
fake_db = mock.Mock()
fake_db.get_top_symbols.return_value = ["AAPL", "MSFT"]
sched = sched_mod.DataRefreshScheduler(
    pool=fake_pool, db=fake_db, news_db=mock.Mock(), monitor=monitor
)

# Success cycle: records upserted, pool closed, monitor records success.
records = [{"symbol": "AAPL"}, {"symbol": "MSFT"}]
with mock.patch.object(sched_mod, "fetch_us_stock_symbols", return_value=["AAPL", "MSFT"]), \
     mock.patch.object(sched_mod, "refresh_price_data", return_value=records):
    sched.job_refresh_prices()
price_runs = monitor._store.recent_runs("price_refresh")
assert price_runs[0]["status"] == "success" and price_runs[0]["records"] == 2
assert fake_db.bulk_upsert_stocks.called and fake_db.close.called
fake_db.reset_mock()

# Failing cycle: status failed, pool STILL closed (no leaked connection).
with mock.patch.object(sched_mod, "fetch_us_stock_symbols", return_value=["AAPL"]), \
     mock.patch.object(sched_mod, "refresh_price_data",
                       side_effect=RuntimeError("finnhub down")):
    sched.job_refresh_prices()
price_runs = monitor._store.recent_runs("price_refresh")
assert price_runs[0]["status"] == "failed" and price_runs[0]["error"]
assert fake_db.close.called, "pool must close even on failure"

# News job: top symbols from DB, stored count recorded, news pool closed.
with mock.patch("src.news.refresh_news_batch", return_value=2):
    sched.job_refresh_news()
news_runs = monitor._store.recent_runs("news_refresh")
assert news_runs[0]["status"] == "success" and news_runs[0]["records"] == 2
assert sched._news_db.close.called

# Monitor summary reflects all of it (what GET /ops/jobs would return).
summary = monitor.summary(now=datetime.now(timezone.utc).timestamp() + 10)
jobs = {j["job_name"]: j for j in summary["jobs"]}
assert jobs["price_refresh"]["last_status"] == "failed"
assert jobs["news_refresh"]["last_status"] == "success"
assert jobs["news_refresh"]["last_records"] == 2
print("✅ success→records, failure→failed+close, news→stored; monitor summary consistent")


# ── 4. CLI lifecycle: dates threaded / aborted ───────────────────────────────
print("\n[4] CLI dispatch → engine date threading")

import src.main as main_mod  # noqa: E402

engine_inst = mock.Mock()
engine_inst.backtest_top_results.return_value = {}
with mock.patch("src.interpreter.interpret_query") as m_iq, \
     mock.patch("src.backtester.BacktestEngine", return_value=engine_inst):
    rc = main_mod.main(["backtest", "buy", "oversold", "tech",
                        "--start", "2020-01-01", "--end", "2024-12-31", "-n", "4"])
assert rc == 0 and m_iq.called
_, kwargs = engine_inst.backtest_top_results.call_args
assert kwargs["start_date"] == "2020-01-01" and kwargs["end_date"] == "2024-12-31"
assert kwargs["top_n"] == 4

# Malformed date aborts BEFORE the LLM call (never a silent zero-result).
with mock.patch("src.interpreter.interpret_query") as m_iq2, \
     mock.patch("src.backtester.BacktestEngine") as m_be2:
    main_mod.main(["backtest", "q", "--start", "2020/01/01"])
assert not m_iq2.called and not m_be2.called
print("✅ valid dates threaded; malformed date aborts before LLM")

print("\n" + "=" * 70)
print("✅✅ ALL BEHAVIORAL CHECKS PASSED — real code paths verified end-to-end")
print("=" * 70)
