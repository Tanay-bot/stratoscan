"""Enrichment Layer for Stratoscan.

Fetches real-time news and sentiment data for the top matching stocks
from a screening query. Uses a single Finnhub API key's free-tier
endpoints:
  - /company-news (latest news articles)
  - /news-sentiment (aggregate sentiment score)

Cost discipline: fixed small number of API calls per user query
(e.g., 10 stocks × 2 calls = 20 calls).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src.data_fetcher import FinnhubClientPool, _api_call_with_retry, initialize_client_pool
from src.screener import ScreenerResult

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

# Number of days of news to fetch per stock
NEWS_LOOKBACK_DAYS: int = 7

# Maximum news articles to keep per stock
MAX_NEWS_PER_STOCK: int = 5


@dataclass
class NewsArticle:
    """A single news article with relevance to a stock."""

    headline: str
    summary: str
    source: str
    url: str
    datetime: str
    sentiment: Optional[str] = None         # "positive", "negative", "neutral"
    sentiment_score: Optional[float] = None  # -1.0 to 1.0


@dataclass
class SentimentData:
    """Aggregate sentiment data for a stock."""

    symbol: str
    buzz: float                    # News buzz volume (relative)
    company_news_score: float      # Company-specific sentiment
    sector_news_score: float       # Sector-level sentiment
    articles: List[NewsArticle] = field(default_factory=list)


@dataclass
class EnrichmentReport:
    """Complete enrichment data for a set of screened stocks."""

    query: str
    stock_count: int
    sentiments: Dict[str, SentimentData] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        """Generate a concise enrichment summary for display."""
        lines = [
            f"📰 News & Sentiment Enrichment for \"{self.query}\"",
            f"Stocks enriched: {self.stock_count}\n",
        ]

        for symbol, data in self.sentiments.items():
            sentiment_label = "🟢" if data.company_news_score > 0.2 else (
                "🔴" if data.company_news_score < -0.2 else "⚪"
            )
            lines.append(
                f"{sentiment_label} {symbol}: "
                f"Buzz={data.buzz:.1f}, "
                f"Company Sentiment={data.company_news_score:+.3f}, "
                f"Sector Sentiment={data.sector_news_score:+.3f}"
            )
            for art in data.articles[:3]:  # Show top 3 headlines
                lines.append(f"   • [{art.source}] {art.headline}")

        return "\n".join(lines)


class EnrichmentEngine:
    """Fetches news and sentiment for screened stocks.

    Uses a single Finnhub key for /company-news and /news-sentiment endpoints.
    These endpoints are relatively cheap on the free tier.
    """

    def __init__(self, pool: Optional[FinnhubClientPool] = None) -> None:
        self._pool = pool or initialize_client_pool()

    def _fetch_company_news(
        self, symbol: str, from_date: str, to_date: str
    ) -> List[Dict[str, Any]]:
        """Fetch company news articles from Finnhub.

        Args:
            symbol: Stock ticker symbol.
            from_date: Start date (YYYY-MM-DD).
            to_date: End date (YYYY-MM-DD).

        Returns:
            List of news article dicts.
        """
        try:
            client = self._pool.next()
            news = _api_call_with_retry(
                lambda c=client: c.company_news(symbol, _from=from_date, to=to_date)
            )
            return news if isinstance(news, list) else []
        except Exception as e:
            logger.debug("Failed to fetch news for %s: %s", symbol, e)
            return []

    def _fetch_news_sentiment(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch aggregate news sentiment for a stock from Finnhub.

        Args:
            symbol: Stock ticker symbol.

        Returns:
            Dict with sentiment data, or None on failure.
        """
        try:
            client = self._pool.next()
            sentiment = _api_call_with_retry(
                lambda c=client: client.news_sentiment(symbol)
            )
            return sentiment if isinstance(sentiment, dict) else None
        except Exception as e:
            logger.debug("Failed to fetch sentiment for %s: %s", symbol, e)
            return None

    def _classify_sentiment(self, score: Optional[float]) -> str:
        """Classify a numeric sentiment score into a label.

        Args:
            score: Numeric sentiment score (-1.0 to 1.0).

        Returns:
            "positive", "negative", or "neutral".
        """
        if score is None:
            return "neutral"
        if score > 0.2:
            return "positive"
        if score < -0.2:
            return "negative"
        return "neutral"

    def enrich_stock(self, symbol: str) -> Optional[SentimentData]:
        """Fetch news and sentiment for a single stock.

        Cost: 2 Finnhub API calls per stock (news + sentiment).

        Args:
            symbol: Stock ticker symbol.

        Returns:
            SentimentData with news articles and scores, or None on failure.
        """
        # Date range for news
        to_date = datetime.now().strftime("%Y-%m-%d")
        from_date = (
            datetime.now() - timedelta(days=NEWS_LOOKBACK_DAYS)
        ).strftime("%Y-%m-%d")

        # Fetch sentiment data
        sentiment_raw = self._fetch_news_sentiment(symbol)
        if not sentiment_raw:
            return None

        # Fetch news articles
        news_raw = self._fetch_company_news(symbol, from_date, to_date)

        # Parse articles
        articles: List[NewsArticle] = []
        for article in news_raw[:MAX_NEWS_PER_STOCK]:
            headline = article.get("headline", "")
            summary = article.get("summary", "")[:200]
            source = article.get("source", "Unknown")
            url = article.get("url", "")
            pub_time = article.get("datetime", 0)

            # Convert Unix timestamp to ISO date string
            if isinstance(pub_time, (int, float)) and pub_time > 0:
                pub_date = datetime.fromtimestamp(pub_time).strftime("%Y-%m-%d %H:%M")
            else:
                pub_date = str(pub_time)

            articles.append(NewsArticle(
                headline=headline,
                summary=summary,
                source=source,
                url=url,
                datetime=pub_date,
            ))

        # Parse sentiment scores
        company_score = sentiment_raw.get("companyNewsScore", 0.0) or 0.0
        sector_score = sentiment_raw.get("sectorAverageScore", 0.0) or 0.0
        buzz = sentiment_raw.get("buzz", 0.0) or 0.0

        # Classify each article's sentiment (Finnhub doesn't provide per-article
        # sentiment in the news endpoint, so we use the aggregate as context)
        for article in articles:
            article.sentiment = self._classify_sentiment(company_score)
            article.sentiment_score = company_score

        return SentimentData(
            symbol=symbol,
            buzz=float(buzz),
            company_news_score=float(company_score),
            sector_news_score=float(sector_score),
            articles=articles,
        )

    def enrich_results(
        self,
        user_query: str,
        results: List[ScreenerResult],
        max_stocks: int = 10,
    ) -> EnrichmentReport:
        """Enrich a list of screener results with news and sentiment.

        Args:
            user_query: The original user query (for reporting).
            results: The screener results to enrich.
            max_stocks: Maximum stocks to enrich (to limit API costs).

        Returns:
            EnrichmentReport with sentiment data for each stock.
        """
        stocks_to_enrich = results[:max_stocks]
        sentiment_map: Dict[str, SentimentData] = {}

        for stock in stocks_to_enrich:
            data = self.enrich_stock(stock.symbol)
            if data:
                sentiment_map[stock.symbol] = data

        logger.info(
            "Enriched %d/%d stocks with news and sentiment",
            len(sentiment_map),
            len(stocks_to_enrich),
        )

        return EnrichmentReport(
            query=user_query,
            stock_count=len(sentiment_map),
            sentiments=sentiment_map,
        )
