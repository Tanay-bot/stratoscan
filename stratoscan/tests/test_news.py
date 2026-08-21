"""Hermetic tests for the news + sentiment service — no network, no live DB.

Run: python -m tests.test_news

Covers the news pipeline (src/news.py) that had ZERO direct test coverage:

1. _parse_sentiment_json: plain JSON, markdown-fenced, unparseable -> {}.
2. _aggregate_score: mean, empty -> 0.0.
3. score_headlines_batch: happy path (index-aligned entries), score clamping
   to [-1, 1], bad label recomputed from score, missing index -> neutral
   default, LLM failure -> neutral fallback with llm_ok=False, response
   without a "stocks" key -> llm_ok=False, symbol-key normalization.
4. score_headlines: single-stock wrapper.
5. _build_from_raw: article assembly, headline/summary truncation, unix
   timestamp formatting, buzz + aggregate score.
6. refresh_stock_news: pre-built news stored, build-failure -> None,
   symbol mismatch -> ValueError.
7. refresh_news_batch: max_symbols cap, LLM-outage keeps existing sentiment
   (0 stored), happy path with SCORE_BATCH_SIZE batching (2 LLM calls for
   6 symbols).
8. get_sentiment_for_screen: no news DB -> {}, lookup, lookup failure -> {}.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime
from unittest import mock  # noqa: E402

from src import news as news_mod  # noqa: E402


def _mk_llm(content: str):
    """Fake router whose chat_completion returns the given content.

    MagicMock (not Mock): the scorer reads ``response.choices[0].message``
    — ``[0]`` requires __getitem__, which only MagicMock implements.
    """
    router = mock.MagicMock()
    router.chat_completion.return_value.choices[0].message.content = content
    return router


# ── 1. Sentiment JSON parsing ───────────────────────────────────────────────
print("1. _parse_sentiment_json...")

assert news_mod._parse_sentiment_json('{"stocks": {}}') == {"stocks": {}}
assert news_mod._parse_sentiment_json('```json\n{"stocks": {}}\n```') == {"stocks": {}}
assert news_mod._parse_sentiment_json("not json") == {}
assert news_mod._parse_sentiment_json("") == {}
assert news_mod._parse_sentiment_json("[1,2,3]") == {}  # non-dict -> {}
print("✅ plain, fenced, garbage, empty, non-dict all handled")


# ── 2. Aggregate score ──────────────────────────────────────────────────────
print("2. _aggregate_score...")

assert news_mod._aggregate_score([0.5, 0.7, 0.3]) == 0.5  # mean, 4dp
assert news_mod._aggregate_score([]) == 0.0
assert news_mod._aggregate_score([-0.5, 0.5]) == 0.0
print("✅ mean with 4dp rounding; empty -> 0.0")


# ── 3. Batched sentiment scoring ────────────────────────────────────────────
print("3. score_headlines_batch...")

# Empty input -> ({}, True) without any LLM call
router_never = mock.Mock()
with mock.patch("src.llm_router.get_router", return_value=router_never):
    res, ok = news_mod.score_headlines_batch({})
assert res == {} and ok is True
assert not router_never.chat_completion.called

# Happy path: entries index-aligned to the input headlines
router = _mk_llm(
    '{"stocks": {"AAPL": {"articles": ['
    '{"index": 0, "sentiment": "positive", "score": 0.7},'
    '{"index": 1, "sentiment": "negative", "score": -0.5}]}}}'
)
with mock.patch("src.llm_router.get_router", return_value=router):
    res, ok = news_mod.score_headlines_batch(
        {"AAPL": ["Apple beats", "Apple faces"]}
    )
assert ok is True
assert res["AAPL"] == [
    {"index": 0, "sentiment": "positive", "score": 0.7},
    {"index": 1, "sentiment": "negative", "score": -0.5},
]

# Score clamped to [-1, 1]; bad label recomputed from score
router = _mk_llm(
    '{"stocks": {"TSLA": {"articles": ['
    '{"index": 0, "sentiment": "great", "score": 5.0},'
    '{"index": 1, "sentiment": "whatever", "score": -3.0}]}}}'
)
with mock.patch("src.llm_router.get_router", return_value=router):
    res, _ = news_mod.score_headlines_batch({"TSLA": ["h1", "h2"]})
assert res["TSLA"][0]["score"] == 1.0 and res["TSLA"][0]["sentiment"] == "positive"
assert res["TSLA"][1]["score"] == -1.0 and res["TSLA"][1]["sentiment"] == "negative"

# Missing index -> neutral default (never a short list)
router = _mk_llm('{"stocks": {"AAPL": {"articles": [{"index": 1, "sentiment": "positive", "score": 0.6}]}}}')
with mock.patch("src.llm_router.get_router", return_value=router):
    res, ok = news_mod.score_headlines_batch({"AAPL": ["a", "b"]})
assert ok is True
assert res["AAPL"][0] == {"index": 0, "sentiment": "neutral", "score": 0.0}
assert res["AAPL"][1]["score"] == 0.6
assert len(res["AAPL"]) == 2

# Symbol-key normalization: LLM returns lowercase with whitespace
router = _mk_llm('{"stocks": {"aapl ": {"articles": [{"index": 0, "sentiment": "positive", "score": 0.4}]}}}')
with mock.patch("src.llm_router.get_router", return_value=router):
    res, ok = news_mod.score_headlines_batch({"AAPL": ["h"]})
assert ok is True and res["AAPL"][0]["score"] == 0.4

# LLM raises -> all-neutral fallback, llm_ok=False
router_fail = mock.Mock()
router_fail.chat_completion.side_effect = RuntimeError("provider down")
with mock.patch("src.llm_router.get_router", return_value=router_fail):
    res, ok = news_mod.score_headlines_batch({"AAPL": ["h"]})
assert ok is False
assert res["AAPL"][0]["sentiment"] == "neutral" and res["AAPL"][0]["score"] == 0.0

# Response without a "stocks" key -> llm_ok=False (must not count as success)
with mock.patch(
    "src.llm_router.get_router", return_value=_mk_llm('{"summary": "x"}')
):
    res, ok = news_mod.score_headlines_batch({"AAPL": ["h"]})
assert ok is False and res["AAPL"][0]["sentiment"] == "neutral"
print("✅ happy path, clamping, label recompute, missing index, case, outages")


# ── 4. Single-stock wrapper ─────────────────────────────────────────────────
print("4. score_headlines...")

with mock.patch(
    "src.llm_router.get_router",
    return_value=_mk_llm('{"stocks": {"MSFT": {"articles": [{"index": 0, "sentiment": "positive", "score": 0.3}]}}}'),
):
    out = news_mod.score_headlines("MSFT", ["h"])
assert out == [{"index": 0, "sentiment": "positive", "score": 0.3}]
print("✅ single-stock wrapper delegates to batch")


# ── 5. Raw article assembly ─────────────────────────────────────────────────
print("5. _build_from_raw...")

raw = [
    {"headline": "h" * 600, "summary": "s" * 1100, "source": "Reuters",
     "url": "http://x", "datetime": 1750000000},
    {"headline": "Second", "datetime": 0},  # invalid ts -> None
]
scores = [
    {"index": 0, "sentiment": "positive", "score": 0.6},
    {"index": 1, "sentiment": "neutral", "score": 0.0},
]
sn = news_mod._build_from_raw("AAPL", raw, scores)
assert len(sn.articles) == 2
a0, a1 = sn.articles
assert len(a0.headline) == 500 and len(a0.summary) == 1000, "truncation caps"
assert a0.source == "Reuters" and a0.sentiment == "positive"
# unix ts formatted as "%Y-%m-%d %H:%M:%S"
assert a0.published_at == datetime.fromtimestamp(1750000000).strftime("%Y-%m-%d %H:%M:%S")
assert a1.published_at is None and a1.headline == "Second"
assert sn.symbol == "AAPL" and sn.buzz == 2.0
assert sn.company_news_score == 0.3  # mean of [0.6, 0.0]

# Missing score entry -> neutral defaults; no articles -> zero everything
sn2 = news_mod._build_from_raw("AAPL", [], [])
assert sn2.articles == [] and sn2.buzz == 0.0 and sn2.company_news_score == 0.0
print("✅ truncation, ts formatting, buzz, aggregate; empty edge")


# ── 6. refresh_stock_news ───────────────────────────────────────────────────
print("6. refresh_stock_news...")

fake_db = mock.Mock()
news_obj = news_mod.StockNews(
    symbol="AAPL",
    articles=[news_mod.ArticleWithSentiment(
        symbol="AAPL", headline="h", summary="", source="x", url="", published_at=None
    )],
)
# Pre-built news stored
out = news_mod.refresh_stock_news("AAPL", db=fake_db, news=news_obj)
assert out is news_obj
assert fake_db.upsert_articles.called and fake_db.upsert_sentiment.called

# Symbol mismatch -> ValueError (prevents mis-keyed writes)
try:
    news_mod.refresh_stock_news("AAPL", db=fake_db, news=news_mod.StockNews(symbol="MSFT"))
    raise AssertionError("symbol mismatch must raise ValueError")
except ValueError:
    pass

# Build failure (no articles) -> None, no DB write
fake_db.reset_mock()
with mock.patch.object(news_mod, "build_stock_news", return_value=None):
    assert news_mod.refresh_stock_news("AAPL", db=fake_db, pool=mock.Mock()) is None
assert not fake_db.upsert_articles.called

# Store failure -> None
with mock.patch.object(news_mod, "_store_news", return_value=False):
    assert news_mod.refresh_stock_news("AAPL", db=fake_db, news=news_obj) is None
print("✅ pre-built store, mismatch ValueError, build-fail None, store-fail None")


# ── 7. refresh_news_batch orchestration ─────────────────────────────────────
print("7. refresh_news_batch...")

# Empty -> 0
assert news_mod.refresh_news_batch([], db=fake_db, pool=mock.Mock()) == 0

# max_symbols cap respected
with mock.patch.object(news_mod, "fetch_company_news",
                       return_value=[{"headline": "h"}]) as m_fetch, \
     mock.patch.object(
         news_mod, "score_headlines_batch",
         side_effect=lambda chunks: (
             {s: [{"index": 0, "sentiment": "neutral", "score": 0.0} for _ in hs]
              for s, hs in chunks.items()}, True,
         )), \
     mock.patch.object(news_mod, "_store_news", return_value=True), \
     mock.patch("time.sleep"):
    stored = news_mod.refresh_news_batch(
        ["A", "B", "C"], db=fake_db, pool=mock.Mock(), max_symbols=2
    )
assert stored == 2
assert m_fetch.call_count == 2  # cap truncated the list (assert on the mock, not the restored function)

# LLM outage -> 0 stored, existing sentiment preserved (no store calls)
with mock.patch.object(news_mod, "fetch_company_news",
                       return_value=[{"headline": "h"}]), \
     mock.patch.object(news_mod, "score_headlines_batch", return_value=({}, False)), \
     mock.patch.object(news_mod, "_store_news") as m_store, \
     mock.patch("time.sleep"):
    stored = news_mod.refresh_news_batch(["A"], db=fake_db, pool=mock.Mock())
assert stored == 0
assert not m_store.called, "LLM outage must not overwrite good sentiment"

# Happy path: 6 symbols batched at SCORE_BATCH_SIZE=5 -> exactly 2 LLM calls
with mock.patch.object(news_mod, "fetch_company_news",
                       return_value=[{"headline": "h"}]), \
     mock.patch.object(
         news_mod, "score_headlines_batch",
         side_effect=lambda chunks: (
             {s: [{"index": 0, "sentiment": "neutral", "score": 0.0} for _ in hs]
              for s, hs in chunks.items()}, True,
         ),
     ) as m_score, \
     mock.patch.object(news_mod, "_store_news", return_value=True), \
     mock.patch("time.sleep"):
    stored = news_mod.refresh_news_batch(
        [f"S{i}" for i in range(6)], db=fake_db, pool=mock.Mock()
    )
assert stored == 6
assert m_score.call_count == 2, "6 symbols must split into 5+1 LLM batches"
print("✅ cap, LLM outage skip, batched scoring across 6 symbols")


# ── 8. Sentiment lookup (API path, 0 API calls) ─────────────────────────────
print("8. get_sentiment_for_screen...")

# No news DB -> {} immediately
with mock.patch.object(
    type(news_mod.settings), "has_news_database", mock.PropertyMock(return_value=False)
):
    assert news_mod.get_sentiment_for_screen(["AAPL"]) == {}

# With news DB -> returns the lookup result
fake_db2 = mock.Mock()
fake_db2.get_sentiments_for_symbols.return_value = {
    "AAPL": {"buzz": 5.0, "company_news_score": 0.4}
}
with mock.patch.object(
    type(news_mod.settings), "has_news_database", mock.PropertyMock(return_value=True)
):
    out = news_mod.get_sentiment_for_screen(["AAPL"], db=fake_db2)
assert out["AAPL"]["company_news_score"] == 0.4
fake_db2.get_sentiments_for_symbols.assert_called_once_with(["AAPL"])

# Lookup failure -> {} (never crashes the screener)
fake_db2.get_sentiments_for_symbols.side_effect = Exception("db down")
with mock.patch.object(
    type(news_mod.settings), "has_news_database", mock.PropertyMock(return_value=True)
):
    assert news_mod.get_sentiment_for_screen(["AAPL"], db=fake_db2) == {}
print("✅ no-DB {}, lookup passthrough, failure -> {}")

mock.patch.stopall()
print()
print("🎯 All news tests pass!")
