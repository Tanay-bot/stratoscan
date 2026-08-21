"""News + Sentiment service for Stratoscan.

Background refresh pipeline that feeds the website's news section and
lets screening check a stock's sentiment score without any API calls.

Flow (per refresh cycle):
    1. Fetch recent /company-news articles via the DEDICATED news Finnhub
       key (``NEWS_FINNHUB_KEY``), falling back to the shared pool on
       rate limit — see ``src.data_fetcher.fetch_company_news``.
    2. Score per-article sentiment with the free multi-provider LLM pool
       (``src.llm_router``) — ONE LLM call per BATCH of stocks (headlines
       for several companies are scored in a single call), because a
       per-stock call was measured at 40-135s (GLM reasoning on the strict
       JSON prompt) and would blow past the refresh cadence. Z.ai GLM flash
       models are $0, so sentiment costs nothing. (Finnhub's own
       /news-sentiment endpoint is a paid-tier resource — 403 on free keys,
       verified live — which is why sentiment is LLM-scored.)
    3. Store articles + aggregate sentiment in the SEPARATE news database
       (``NEWS_DATABASE_URL``) via :class:`src.database.NewsDB`.

Reads (news feed, per-stock news, sentiment on screen results) hit the
news database only — zero API calls per user.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from src.config import settings
from src.data_fetcher import fetch_company_news, initialize_client_pool
from src.database import NewsDB

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

# How many days of news to fetch per stock per cycle
NEWS_LOOKBACK_DAYS: int = 7

# Max articles kept per stock per refresh (bounded to keep LLM batches small)
MAX_ARTICLES_PER_STOCK: int = 5

# How many stocks' headlines to score in ONE LLM call. Keeps each call's
# prompt small enough for fast, accurate JSON output.
SCORE_BATCH_SIZE: int = 5

# Max articles to show in the API news feed by default
DEFAULT_FEED_LIMIT: int = 50

# LLM system prompt: strict JSON sentiment scoring for MULTIPLE companies.
# Scores are -1.0 (very negative) to +1.0 (very positive).
SENTIMENT_SYSTEM_PROMPT = """You are Stratoscan, a financial news sentiment analyzer.

You receive news headlines for MULTIPLE companies. Each company has its own
numbered list of headlines. For each headline, classify the sentiment TOWARD
that company on a scale of -1.0 (very negative) to +1.0 (very positive),
with 0.0 = neutral.

Rules:
1. Output ONLY valid JSON — no markdown, no explanation.
2. The JSON must be:
   {"stocks": {"AAPL": {"articles": [{"index": 0, "sentiment": "positive", "score": 0.7}, ...]}, "MSFT": {"articles": [...]}}}
3. Include EVERY company and EVERY headline index exactly once.
4. sentiment label must match: score > 0.15 → "positive", < -0.15 → "negative", else "neutral".
5. A headline that is factual or mixed with no clear lean is "neutral" with score near 0.

Example:
Input:
AAPL:
0: Apple beats earnings estimates, stock jumps 4%
1: Apple faces new antitrust lawsuit in Europe
MSFT:
0: Microsoft cloud revenue grows 20%

Output: {"stocks": {"AAPL": {"articles": [{"index": 0, "sentiment": "positive", "score": 0.7}, {"index": 1, "sentiment": "negative", "score": -0.5}]}, "MSFT": {"articles": [{"index": 0, "sentiment": "positive", "score": 0.6}]}}}
"""


# ── Data Types ───────────────────────────────────────────────────────────────

@dataclass
class ArticleWithSentiment:
    """A news article plus its LLM-scored sentiment."""

    symbol: str
    headline: str
    summary: str
    source: str
    url: str
    published_at: Optional[str]
    sentiment: str = "neutral"
    sentiment_score: float = 0.0


@dataclass
class StockNews:
    """Fetched + scored news and aggregate sentiment for one stock."""

    symbol: str
    articles: List[ArticleWithSentiment] = field(default_factory=list)
    buzz: float = 0.0
    company_news_score: float = 0.0

    @property
    def summary(self) -> str:
        """One-line summary for logs."""
        return (
            f"{self.symbol}: {len(self.articles)} articles, "
            f"sentiment={self.company_news_score:+.3f}"
        )


# ── Sentiment Scoring (LLM, batched across stocks) ───────────────────────────

def score_headlines_batch(
    stocks: Dict[str, List[str]],
) -> tuple:
    """Score headlines for MULTIPLE stocks in ONE LLM call.

    Args:
        stocks: Mapping of symbol -> list of headlines.

    Returns:
        A ``(results, llm_ok)`` tuple:
          - results: Dict mapping symbol -> list of
            [{"index", "sentiment", "score"}] entries. Every entry matches
            its input length; missing/malformed entries default to
            neutral/0.0 so a bad LLM response degrades to neutral instead
            of failing the cycle.
          - llm_ok: True if the LLM call succeeded and returned parseable
            JSON. Callers can skip storing when False so a total provider
            outage doesn't overwrite good sentiment with zeros.
    """
    if not stocks:
        return {}, True

    # Build a numbered prompt covering every company
    blocks: List[str] = []
    for symbol, headlines in stocks.items():
        block = "\n".join(f"{i}: {h}" for i, h in enumerate(headlines))
        blocks.append(f"{symbol}:\n{block}")
    numbered = "\n".join(blocks)

    parsed: Dict[str, Any] = {}
    llm_ok = False
    try:
        from src.llm_router import get_router

        response = get_router().chat_completion(
            messages=[
                {"role": "system", "content": SENTIMENT_SYSTEM_PROMPT},
                {"role": "user", "content": numbered},
            ],
            temperature=0.1,
            max_tokens=2000,  # reasoning models need headroom
            # Background batch job: prefer the FAST provider (measured
            # <2s) over the primary. Falls back automatically if it's
            # cooling or fails — "never fail" is preserved.
            prefer=settings.news_llm_prefer or None,
        )
        raw = response.choices[0].message.content or "{}"
        parsed = _parse_sentiment_json(raw)
        # "OK" only if we actually got a usable stocks structure back — an
        # unparseable/garbage response (which _parse_sentiment_json converts
        # to {}) or a response without the stocks key must NOT count as a
        # success, so callers can skip storing rather than overwriting good
        # sentiment with all-neutral zeros.
        llm_ok = isinstance(parsed, dict) and bool(parsed.get("stocks"))
    except Exception as e:
        logger.warning("LLM sentiment scoring failed (%s) — neutral fallback", e)
        parsed = {}

    stocks_data = parsed.get("stocks", {}) if isinstance(parsed, dict) else {}
    if not isinstance(stocks_data, dict):
        stocks_data = {}
    # Normalize symbol keys (case/whitespace safety) so a slightly-off
    # symbol in the LLM output can't silently zero out a whole stock.
    stocks_data = {str(k).strip().upper(): v for k, v in stocks_data.items()}

    results: Dict[str, List[Dict[str, Any]]] = {}
    for symbol, headlines in stocks.items():
        company = stocks_data.get(symbol.upper(), {})
        company_articles = company.get("articles", []) if isinstance(company, dict) else []

        by_index: Dict[int, Dict[str, Any]] = {}
        for entry in company_articles:
            try:
                idx = int(entry.get("index"))
                score = float(entry.get("score", 0.0))
                score = max(-1.0, min(1.0, score))
                label = entry.get("sentiment", "neutral")
                if label not in ("positive", "negative", "neutral"):
                    label = "neutral" if -0.15 <= score <= 0.15 else (
                        "positive" if score > 0.15 else "negative"
                    )
                by_index[idx] = {"index": idx, "sentiment": label, "score": score}
            except (TypeError, ValueError):
                continue

        scores = [
            by_index.get(i, {"index": i, "sentiment": "neutral", "score": 0.0})
            for i in range(len(headlines))
        ]
        results[symbol] = scores

        # Observability: if every article came back neutral, it may be a
        # legitimately neutral news day OR a mis-keyed LLM response. Log
        # it at debug so a silent mis-mapping is detectable in scheduler logs.
        if headlines and all(s["sentiment"] == "neutral" for s in scores):
            logger.debug(
                "All-neutral sentiment for %s (%d articles) — verify LLM keying",
                symbol, len(headlines),
            )

    return results, llm_ok


def score_headlines(symbol: str, headlines: List[str]) -> List[Dict[str, Any]]:
    """Score one stock's headlines (thin wrapper over the batched scorer).

    Kept for the single-stock path (``build_stock_news``); batch refreshes
    should use :func:`score_headlines_batch` to amortize LLM latency.
    """
    return score_headlines_batch({symbol: headlines})[0].get(symbol, [])


def _parse_sentiment_json(raw: str) -> Dict[str, Any]:
    """Parse the LLM's sentiment JSON, stripping markdown fences if present."""
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned[3:]
        cleaned = cleaned.rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        logger.warning("LLM returned unparseable sentiment JSON")
        return {}


def _aggregate_score(scores: List[float]) -> float:
    """Simple mean of per-article scores (0.0 for empty)."""
    if not scores:
        return 0.0
    return round(sum(scores) / len(scores), 4)


# ── Orchestration ────────────────────────────────────────────────────────────

def _build_from_raw(
    symbol: str,
    raw_articles: List[Dict[str, Any]],
    scores: List[Dict[str, Any]],
) -> StockNews:
    """Assemble a StockNews from raw Finnhub articles + LLM scores."""
    score_map = {s["index"]: s for s in scores}

    articles: List[ArticleWithSentiment] = []
    for i, raw in enumerate(raw_articles):
        scored = score_map.get(i, {"sentiment": "neutral", "score": 0.0})
        pub_ts = raw.get("datetime", 0)
        published = None
        if isinstance(pub_ts, (int, float)) and pub_ts > 0:
            try:
                published = datetime.fromtimestamp(pub_ts).strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, OverflowError, OSError):
                published = None

        # Security (defense-in-depth): only store http(s) article URLs. The
        # URL arrives from Finnhub via the fetch pipeline, but an article
        # poisoned with a javascript:/data:/vbscript: scheme would render as
        # a clickable XSS vector in the frontend. Parse the scheme (same
        # semantic as the frontend's safeExternalUrl) and drop anything that
        # isn't http(s); the frontend re-checks on render as the second layer.
        raw_url = (raw.get("url") or "").strip()
        if urlparse(raw_url).scheme.lower() not in ("http", "https"):
            raw_url = ""

        articles.append(ArticleWithSentiment(
            symbol=symbol,
            headline=(raw.get("headline") or "")[:500],
            summary=(raw.get("summary") or "")[:1000],
            source=raw.get("source") or "Unknown",
            url=raw_url,
            published_at=published,
            sentiment=scored["sentiment"],
            sentiment_score=scored["score"],
        ))

    aggregate = _aggregate_score([s["score"] for s in scores])
    return StockNews(
        symbol=symbol,
        articles=articles,
        buzz=float(len(raw_articles)),
        company_news_score=aggregate,
    )


def build_stock_news(symbol: str, pool=None) -> Optional[StockNews]:
    """Fetch + score news for one stock (no DB writes).

    Args:
        symbol: Ticker symbol.
        pool: Optional shared Finnhub pool. Pass the SAME pool when building
            many stocks in a loop — creating one pool per stock wastes
            clients and defeats round-robin across the batch.

    Returns:
        A StockNews object, or None if no articles were fetched.
    """
    if pool is None:
        pool = initialize_client_pool()
    to_date = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - timedelta(days=NEWS_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    raw_articles = fetch_company_news(pool, symbol, from_date, to_date)
    raw_articles = raw_articles[:MAX_ARTICLES_PER_STOCK]
    if not raw_articles:
        return None

    headlines = [a.get("headline", "")[:300] for a in raw_articles]
    scores = score_headlines(symbol, headlines)
    return _build_from_raw(symbol, raw_articles, scores)


def _store_news(symbol: str, news: StockNews, db: NewsDB) -> bool:
    """Write one StockNews into the news DB. Returns True on success."""
    try:
        db.upsert_articles([
            {
                "symbol": a.symbol,
                "headline": a.headline,
                "summary": a.summary,
                "source": a.source,
                "url": a.url,
                "published_at": a.published_at,
                "sentiment": a.sentiment,
                "sentiment_score": a.sentiment_score,
            }
            for a in news.articles
        ])
        db.upsert_sentiment({
            "symbol": news.symbol,
            "buzz": news.buzz,
            "company_news_score": news.company_news_score,
            "sector_news_score": 0.0,  # sector-level scoring is out of scope for MVP
            "article_count": len(news.articles),
        })
        return True
    except Exception as e:
        logger.warning("Failed to store news for %s: %s", symbol, e)
        return False


def refresh_stock_news(
    symbol: str,
    db: Optional[NewsDB] = None,
    pool=None,
    news: Optional[StockNews] = None,
) -> Optional[StockNews]:
    """Store news+sentiment for one stock (builds it if not provided).

    Args:
        symbol: Ticker symbol (used for the DB lookup keys).
        db: Optional NewsDB (lazy-initialized if omitted).
        pool: Optional shared Finnhub pool (threaded through the batch).
        news: A pre-built StockNews to store — avoids re-fetching and
            re-scoring when the caller already built it.

    Returns:
        The StockNews if stored, else None.
    """
    if news is None:
        try:
            news = build_stock_news(symbol, pool=pool)
        except Exception as e:
            logger.debug("Failed to build news for %s: %s", symbol, e)
            return None
    if news is None:
        return None
    if news.symbol != symbol:
        raise ValueError(
            f"Pre-built news symbol {news.symbol!r} does not match {symbol!r}"
        )

    db = db or NewsDB()
    return news if _store_news(symbol, news, db) else None


def refresh_news_batch(
    symbols: List[str],
    db: Optional[NewsDB] = None,
    pool=None,
    max_symbols: Optional[int] = None,
) -> int:
    """Refresh news for a batch of symbols (top-by-market-cap recommended).

    Fetch phase uses ONE shared Finnhub pool for the whole batch. Scoring
    phase sends headlines for ``SCORE_BATCH_SIZE`` stocks per LLM call so
    the LLM latency is amortized (~5 stocks per call instead of one), which
    keeps a full cycle inside the refresh cadence.

    Args:
        symbols: List of ticker symbols to refresh.
        db: Optional NewsDB.
        pool: Optional shared Finnhub pool (created once if omitted).
        max_symbols: Cap on how many symbols to process (defaults to
            ``settings.news_symbols_limit``).

    Returns:
        Number of symbols successfully stored.
    """
    cap = settings.news_symbols_limit if max_symbols is None else max_symbols
    symbols = symbols[:cap]
    if not symbols:
        return 0

    db = db or NewsDB()
    if pool is None:
        pool = initialize_client_pool()

    to_date = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - timedelta(days=NEWS_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    # Phase 1: fetch raw articles for every symbol (fast — dedicated news key)
    t_fetch = time.time()
    raw_by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    for symbol in symbols:
        raw = fetch_company_news(pool, symbol, from_date, to_date)
        raw = raw[:MAX_ARTICLES_PER_STOCK]
        if raw:
            raw_by_symbol[symbol] = raw
    logger.info(
        "News fetch phase: %d/%d symbols have articles (%.1fs)",
        len(raw_by_symbol), len(symbols), time.time() - t_fetch,
    )

    # Phase 2: score in LLM batches, then store
    all_symbols = list(raw_by_symbol.keys())
    stored = 0
    for start in range(0, len(all_symbols), SCORE_BATCH_SIZE):
        chunk = all_symbols[start:start + SCORE_BATCH_SIZE]
        t_chunk = time.time()

        chunk_headlines = {
            sym: [a.get("headline", "")[:300] for a in raw_by_symbol[sym]]
            for sym in chunk
        }
        chunk_scores, llm_ok = score_headlines_batch(chunk_headlines)

        if not llm_ok:
            # Total LLM outage: skip the store so a dead provider can't
            # overwrite previously good sentiment with all-neutral zeros.
            logger.warning(
                "News batch %d-%d skipped (LLM unavailable) — keeping existing sentiment",
                start + 1, start + len(chunk),
            )
            # Small pause so a recovery mid-cycle gets picked up next batch
            time.sleep(1.0)
            continue

        for sym in chunk:
            news = _build_from_raw(sym, raw_by_symbol[sym], chunk_scores.get(sym, []))
            if _store_news(sym, news, db):
                stored += 1

        # Inter-batch pause: keeps the cycle under provider RPM caps (Groq
        # free tier is ~30 RPM per ACCOUNT across all keys; 30 batches x 2s
        # = 60s/cycle, comfortably under the cap) so the whole cycle stays
        # on the fast provider instead of failing over mid-cycle.
        time.sleep(2.0)

        if len(chunk) == SCORE_BATCH_SIZE:  # log full batches + final partial
            logger.info(
                "News score/store batch %d-%d/%d: %d stored (%.1fs)",
                start + 1, start + len(chunk), len(all_symbols),
                stored, time.time() - t_chunk,
            )

    logger.info("News refresh complete: %d/%d symbols stored", stored, len(symbols))
    return stored


# ── Sentiment lookup helpers (used by the API) ───────────────────────────────

def get_sentiment_for_screen(
    symbols: List[str],
    db: Optional[NewsDB] = None,
) -> Dict[str, Dict[str, Any]]:
    """Fetch aggregate sentiment for screener result symbols (0 API calls).

    Args:
        symbols: List of ticker symbols from the screener results.
        db: Optional NewsDB.

    Returns:
        Dict mapping symbol -> sentiment record (buzz, company_news_score,
        sector_news_score, article_count). Empty dict if news DB is unset
        or the lookup fails.
    """
    if not settings.has_news_database:
        return {}
    try:
        db = db or NewsDB()
        return db.get_sentiments_for_symbols(symbols)
    except Exception as e:
        logger.debug("Sentiment lookup failed (%s) — returning empty", e)
        return {}
