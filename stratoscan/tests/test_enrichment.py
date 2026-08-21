"""Hermetic tests for the enrichment engine (src/enrichment.py) — no network.

Run: python -m tests.test_enrichment

Covers the enrichment layer that had ZERO direct test coverage:

1. _classify_sentiment: None -> neutral, >0.2 -> positive, <-0.2 -> negative,
   boundaries at +/-0.2 stay neutral.
2. _fetch_company_news / _fetch_news_sentiment: pool client dispatch, list/dict
   guards, error -> [] / None (never raises).
3. enrich_stock: full happy path (sentiment + articles parsed, unix timestamp
   formatting, per-article classification), missing sentiment -> None, empty
   news -> empty articles.
4. enrich_results: max_stocks cap, failed enrichments skipped.
5. EnrichmentReport.summary: renders query, count, per-stock lines.
"""

from __future__ import annotations

import sys
import os
from datetime import datetime as _dt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest import mock  # noqa: E402

from src import enrichment as enr  # noqa: E402
from src.screener import ScreenerResult  # noqa: E402


def _fake_pool() -> mock.Mock:
    """Pool whose next() returns a bare client mock."""
    pool = mock.Mock()
    pool.next.return_value = mock.Mock()
    return pool


def _mk_stock(symbol: str) -> ScreenerResult:
    return ScreenerResult(symbol=symbol, company_name=symbol)


# ── 1. Sentiment classification ─────────────────────────────────────────────
print("1. _classify_sentiment...")

engine = enr.EnrichmentEngine(pool=_fake_pool())
assert engine._classify_sentiment(None) == "neutral"
assert engine._classify_sentiment(0.5) == "positive"
assert engine._classify_sentiment(-0.5) == "negative"
assert engine._classify_sentiment(0.1) == "neutral"
assert engine._classify_sentiment(0.2) == "neutral"    # boundary: not > 0.2
assert engine._classify_sentiment(-0.2) == "neutral"   # boundary: not < -0.2
assert engine._classify_sentiment(0.0) == "neutral"
print("✅ thresholds at +/-0.2 are inclusive-neutral")


# ── 2. Fetch methods: guards + never-raise ──────────────────────────────────
print("2. _fetch_company_news / _fetch_news_sentiment...")

pool = _fake_pool()
engine2 = enr.EnrichmentEngine(pool=pool)

with mock.patch.object(enr, "_api_call_with_retry", return_value=[{"headline": "h"}]):
    news = engine2._fetch_company_news("AAPL", "2026-01-01", "2026-01-08")
assert news == [{"headline": "h"}]

# non-list response -> []
with mock.patch.object(enr, "_api_call_with_retry", return_value={"not": "list"}):
    assert engine2._fetch_company_news("AAPL", "2026-01-01", "2026-01-08") == []

# exception -> [] (never raises)
with mock.patch.object(enr, "_api_call_with_retry",
                       side_effect=RuntimeError("boom")):
    assert engine2._fetch_company_news("AAPL", "2026-01-01", "2026-01-08") == []

with mock.patch.object(enr, "_api_call_with_retry",
                       return_value={"companyNewsScore": 0.5}):
    sent = engine2._fetch_news_sentiment("AAPL")
assert sent == {"companyNewsScore": 0.5}

with mock.patch.object(enr, "_api_call_with_retry", return_value=["nope"]):
    assert engine2._fetch_news_sentiment("AAPL") is None
with mock.patch.object(enr, "_api_call_with_retry",
                       side_effect=RuntimeError("boom")):
    assert engine2._fetch_news_sentiment("AAPL") is None
print("✅ list/dict guards + error -> [] / None")


# ── 3. enrich_stock ─────────────────────────────────────────────────────────
print("3. enrich_stock...")

pool3 = _fake_pool()
engine3 = enr.EnrichmentEngine(pool=pool3)
sentiment_payload = {
    "companyNewsScore": 0.5,
    "sectorAverageScore": -0.1,
    "buzz": 4.5,
}
news_payload = [
    {"headline": "Apple soars", "summary": "s" * 400, "source": "Reuters",
     "url": "http://x", "datetime": 1750000000},
    {"headline": "Mixed day", "datetime": 0},
]
with mock.patch.object(enr, "_api_call_with_retry",
                       side_effect=[sentiment_payload, news_payload]):
    data = engine3.enrich_stock("AAPL")

assert data is not None
assert data.symbol == "AAPL"
assert data.company_news_score == 0.5
assert data.sector_news_score == -0.1
assert data.buzz == 4.5
assert len(data.articles) == 2
a0, a1 = data.articles
assert a0.headline == "Apple soars"
assert len(a0.summary) == 200, "summary truncated to 200 chars"
assert a0.source == "Reuters"
# unix timestamp -> "%Y-%m-%d %H:%M"
assert a0.datetime == _dt.fromtimestamp(1750000000).strftime("%Y-%m-%d %H:%M")
assert a1.datetime == "0", "invalid ts kept as str"
# per-article sentiment inherits the aggregate classification
assert a0.sentiment == "positive" and a0.sentiment_score == 0.5
assert a1.sentiment == "positive"

# Missing sentiment -> None (no partial enrich)
with mock.patch.object(enr, "_api_call_with_retry", return_value=None):
    assert engine3.enrich_stock("AAPL") is None

# No news articles -> valid SentimentData with empty articles
with mock.patch.object(enr, "_api_call_with_retry",
                       side_effect=[sentiment_payload, []]):
    data2 = engine3.enrich_stock("AAPL")
assert data2 is not None and data2.articles == []
print("✅ happy path, truncation, ts format, per-article classification, None/empty")


# ── 4. enrich_results ───────────────────────────────────────────────────────
print("4. enrich_results...")

engine4 = enr.EnrichmentEngine(pool=_fake_pool())
results = [_mk_stock(f"S{i}") for i in range(5)]
with mock.patch.object(
    engine4, "enrich_stock",
    side_effect=lambda sym: enr.SentimentData(
        symbol=sym, buzz=1.0, company_news_score=0.3, sector_news_score=0.0
    ) if sym != "S1" else None,  # S1 fails -> skipped
):
    report = engine4.enrich_results("test query", results, max_stocks=3)

assert report.query == "test query"
assert report.stock_count == 2  # S0, S2 enriched; S1 failed; S3/S4 capped
assert set(report.sentiments.keys()) == {"S0", "S2"}
print("✅ max_stocks cap respected; failed enrichments skipped")


# ── 5. Report summary ───────────────────────────────────────────────────────
print("5. EnrichmentReport.summary...")

rep = enr.EnrichmentReport(
    query="undervalued tech",
    stock_count=1,
    sentiments={
        "AAPL": enr.SentimentData(
            symbol="AAPL", buzz=4.5, company_news_score=0.5,
            sector_news_score=-0.1,
            articles=[enr.NewsArticle(
                headline="Apple soars", summary="", source="Reuters",
                url="", datetime="2026-01-01 10:00",
                sentiment="positive", sentiment_score=0.5,
            )],
        )
    },
)
txt = rep.summary
assert "undervalued tech" in txt and "Stocks enriched: 1" in txt
assert "AAPL" in txt and "Buzz=4.5" in txt and "Company Sentiment=+0.500" in txt
assert "• [Reuters] Apple soars" in txt
print("✅ summary renders query, count, sentiment lines, headlines")

mock.patch.stopall()
print()
print("🎯 All enrichment tests pass!")
