"""Data fetcher with round-robin Finnhub key rotation.

Core design:
  - Initializes N Finnhub clients (one per API key).
  - Cycles through them in round-robin fashion to stay within free-tier limits
    (60 calls/min/key → 600 calls/min aggregate with 10 keys).
  - Two refresh strategies:
      * Price/Volume: every 10 minutes for all ~5,000 US stocks.
      * Fundamentals: daily for sector, market cap, PE ratio, etc.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

import finnhub
import requests

from src.config import settings

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

# US stock exchanges to screen (NYSE, NASDAQ, AMEX)
US_EXCHANGES = ["XNYS", "XNAS", "XASE"]

# How many stocks to fetch per API call in the profile batch
FINNHUB_BATCH_SIZE = 50

# Delay between individual API calls (seconds) to smooth out rate limiting
CALL_DELAY = 0.05  # 50ms

# ── Type Definitions ─────────────────────────────────────────────────────────

StockProfile = Dict[str, Any]       # A single stock's fundamental profile
QuoteData = Dict[str, Any]          # A single stock's real-time quote
MetricData = Dict[str, Any]         # A single stock's metric data (RSI, etc.)


@dataclass
class FinnhubClientPool:
    """Manages a pool of Finnhub clients with round-robin access."""

    clients: List[finnhub.Client]
    _counter: int = 0
    _lock: threading.Lock = threading.Lock()

    def next(self) -> finnhub.Client:
        """Get the next client in round-robin order (thread-safe)."""
        with self._lock:
            client = self.clients[self._counter % len(self.clients)]
            self._counter += 1
        return client

    @property
    def size(self) -> int:
        return len(self.clients)


def initialize_client_pool() -> FinnhubClientPool:
    """Create the pool of Finnhub clients from configured API keys.

    Raises:
        ValueError: If no Finnhub API keys are configured.

    Returns:
        A FinnhubClientPool ready for round-robin access.
    """
    keys = settings.finnhub_keys
    if not keys:
        raise ValueError(
            "No Finnhub API keys found. Set FINNHUB_KEY_01 through FINNHUB_KEY_10 "
            "in your .env file."
        )

    # The python finnhub SDK defaults every request to a 10s timeout — too
    # tight for bulk refresh loops on slow networks (it surfaces as spurious
    # retries/exhaustion). DEFAULT_TIMEOUT is read per request from the
    # instance (self.DEFAULT_TIMEOUT), so setting it on each client overrides
    # the class default. See settings.finnhub_request_timeout.
    clients = [
        finnhub.Client(api_key=key) for key in keys
    ]
    for client in clients:
        client.DEFAULT_TIMEOUT = settings.finnhub_request_timeout

    pool = FinnhubClientPool(clients=clients)

    logger.info(
        "Initialized Finnhub client pool with %d keys (600 req/min aggregate, "
        "%ss request timeout)",
        pool.size,
        settings.finnhub_request_timeout,
    )
    return pool


def _api_call_with_retry(
    call_fn: Callable[[], Any],
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> Any:
    """Execute a Finnhub API call with exponential backoff retry.

    Args:
        call_fn: A zero-argument callable that performs the API call.
        max_retries: Maximum retry attempts.
        base_delay: Initial delay in seconds (doubles each retry).

    Returns:
        The API response data.

    Raises:
        RuntimeError: If all retries are exhausted.
    """
    last_error: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            result = call_fn()
            time.sleep(CALL_DELAY)
            return result
        except finnhub.exceptions.FinnhubAPIException as e:
            last_error = e
            status_code = e.status_code if hasattr(e, "status_code") else 0

            if status_code == 429:
                # Rate limited — wait longer and retry with next key
                wait = base_delay * (2 ** attempt) + 0.5
                logger.warning(
                    "Rate limited (429). Retrying in %.1fs (attempt %d/%d)",
                    wait,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(wait)
            elif 500 <= status_code < 600:
                # Server error — transient, worth retrying
                wait = base_delay * (2 ** attempt)
                logger.warning(
                    "Finnhub server error %d. Retrying in %.1fs (attempt %d/%d)",
                    status_code,
                    wait,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(wait)
            else:
                # Client error (4xx) other than 429 — not retryable
                raise RuntimeError(
                    f"Finnhub API error (status {status_code}): {e}"
                ) from e
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            last_error = e
            wait = base_delay * (2 ** attempt)
            logger.warning(
                "Network error. Retrying in %.1fs (attempt %d/%d): %s",
                wait,
                attempt + 1,
                max_retries,
                e,
            )
            time.sleep(wait)

    raise RuntimeError(
        f"All {max_retries} retries exhausted. Last error: {last_error}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Stock Universe Discovery
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_us_stock_symbols(pool: FinnhubClientPool) -> List[str]:
    """Fetch all US stock symbols listed on NYSE, NASDAQ, and AMEX.

    Uses Finnhub's stock-symbol endpoint (cached server-side, low cost).

    Prefers the aggregated ``exchange=US`` endpoint (one call covers all
    three US listings) and falls back to per-exchange codes
    (``XNYS``/``XNAS``/``XASE``) only if the aggregated call is
    unavailable on the account's plan — some Finnhub plans 401 the
    per-exchange endpoints while the aggregated one works.

    Args:
        pool: The Finnhub client pool.

    Returns:
        A list of ticker symbols (e.g., ['AAPL', 'MSFT', ...]).
    """
    def _extract_common_stocks(response: Any) -> List[str]:
        """Pull Common Stock tickers listed on the three major US exchanges.

        Filters by the ``mic`` market-identifier field when the response
        carries it: Finnhub's aggregated ``exchange=US`` endpoint also
        returns ~13k OTC pink sheets (``mic=OOTC``) that the scanner should
        not screen — the real exchange-listed universe is ~5k symbols on
        NYSE/NASDAQ/AMEX. If a response carries no mic data at all (some
        per-exchange fallbacks), falls back to type-only filtering.
        """
        if not response or not isinstance(response, list):
            return []
        common = [
            item
            for item in response
            if item.get("type") == "Common Stock" and item.get("symbol")
        ]
        if not common:
            return []
        if any(item.get("mic") for item in common):
            common = [
                item for item in common if item.get("mic") in US_EXCHANGES
            ]
        return [item["symbol"] for item in common]

    # Primary: the aggregated US endpoint. Single call, and it works on
    # plans where the per-exchange codes return 401.
    try:
        client = pool.next()
        response = _api_call_with_retry(
            lambda c=client: c.stock_symbols("US")
        )
        aggregated = _extract_common_stocks(response)
        if aggregated:
            logger.info(
                "Found %d US symbols via aggregated exchange=US", len(aggregated)
            )
            return sorted(set(aggregated))
        logger.warning(
            "Aggregated exchange=US returned no symbols — falling back to per-exchange"
        )
    except Exception as e:
        logger.warning(
            "Aggregated exchange=US fetch failed (%s) — falling back to per-exchange", e
        )

    # Fallback: per-exchange codes (NYSE / NASDAQ / AMEX).
    symbols: List[str] = []
    for exchange in US_EXCHANGES:
        try:
            client = pool.next()
            response = _api_call_with_retry(
                lambda c=client: c.stock_symbols(exchange)
            )
            exchange_symbols = _extract_common_stocks(response)
            symbols.extend(exchange_symbols)
            logger.info("Found %d symbols on %s", len(exchange_symbols), exchange)
        except Exception as e:
            logger.error("Failed to fetch symbols for %s: %s", exchange, e)

    # Deduplicate (some stocks are listed on multiple exchanges)
    unique_symbols = list(sorted(set(symbols)))
    logger.info("Total unique US stock symbols: %d", len(unique_symbols))
    return unique_symbols


# ═══════════════════════════════════════════════════════════════════════════════
# Fundamental Profile Fetching (Daily Refresh)
# ═══════════════════════════════════════════════════════════════════════════════

# Best-effort mapping from Finnhub industry names (``finnhubIndustry`` /
# ``industry``) to GICS-style sectors. Finnhub's free-tier profile2 omits
# the GICS ``sector`` field, so we derive a sector from the industry name
# by keyword — this keeps the screener's sector filters (the most-used
# dimension: "tech stocks", "energy") functional without a paid plan.
# Only used when the API returns no explicit ``sector``.
_SECTOR_KEYWORDS: List[Tuple[str, str]] = [
    ("technology", "Technology"), ("software", "Technology"),
    ("semiconductor", "Technology"), ("internet", "Technology"),
    ("electronics", "Technology"), ("computer", "Technology"),
    ("data processing", "Technology"), ("it services", "Technology"),
    ("health", "Healthcare"), ("medical", "Healthcare"),
    ("pharmaceutical", "Healthcare"), ("biotech", "Healthcare"),
    ("biotechnology", "Healthcare"),
    ("bank", "Finance"), ("financial", "Finance"),
    ("insurance", "Finance"), ("asset management", "Finance"),
    ("capital markets", "Finance"), ("mortgage", "Finance"),
    ("credit", "Finance"), ("investment", "Finance"),
    ("oil", "Energy"), ("gas", "Energy"), ("energy", "Energy"),
    ("coal", "Energy"), ("uranium", "Energy"), ("petroleum", "Energy"),
    ("auto", "Consumer Cyclical"), ("automotive", "Consumer Cyclical"),
    ("retail", "Consumer Cyclical"), ("apparel", "Consumer Cyclical"),
    ("textile", "Consumer Cyclical"), ("luxury", "Consumer Cyclical"),
    ("hotel", "Consumer Cyclical"), ("restaurant", "Consumer Cyclical"),
    ("leisure", "Consumer Cyclical"), ("entertainment", "Communication Services"),
    ("media", "Communication Services"), ("telecom", "Communication Services"),
    ("communication", "Communication Services"),
    ("food", "Consumer Defensive"), ("beverage", "Consumer Defensive"),
    ("tobacco", "Consumer Defensive"), ("household", "Consumer Defensive"),
    ("personal care", "Consumer Defensive"), ("staples", "Consumer Defensive"),
    ("industrial", "Industrials"), ("machinery", "Industrials"),
    ("aerospace", "Industrials"), ("defense", "Industrials"),
    ("transportation", "Industrials"), ("logistics", "Industrials"),
    ("shipping", "Industrials"), ("rail", "Industrials"),
    ("construction", "Industrials"), ("engineering", "Industrials"),
    ("metals", "Basic Materials"), ("mining", "Basic Materials"),
    ("chemical", "Basic Materials"), ("materials", "Basic Materials"),
    ("steel", "Basic Materials"), ("paper", "Basic Materials"),
    ("forest", "Basic Materials"),
    ("real estate", "Real Estate"), ("reit", "Real Estate"),
    ("property", "Real Estate"),
    ("utility", "Utilities"), ("electric", "Utilities"),
    ("water", "Utilities"), ("power", "Utilities"),
]


def _derive_sector(industry: Optional[str]) -> Optional[str]:
    """Map a Finnhub industry name to a GICS-style sector (best-effort).

    Args:
        industry: Industry name from the API (e.g. "Semiconductors").

    Returns:
        A sector string like "Technology", or None if no keyword matches.
    """
    if not industry:
        return None
    lower = industry.lower()
    for keyword, sector in _SECTOR_KEYWORDS:
        if keyword in lower:
            return sector
    return None


def fetch_stock_profile(
    pool: FinnhubClientPool, symbol: str
) -> Optional[StockProfile]:
    """Fetch fundamental company profile for a single stock.

    Args:
        pool: The Finnhub client pool.
        symbol: Ticker symbol (e.g., 'AAPL').

    Returns:
        Dict with company profile data, or None on failure.
    """
    try:
        client = pool.next()
        profile = _api_call_with_retry(
            lambda c=client: c.company_profile2(symbol=symbol)
        )

        if not profile or not isinstance(profile, dict):
            return None

        # Finnhub's marketCapitalization is in MILLIONS of USD (e.g. 2500 =
        # $2.5B). The canonical DB unit for market_cap is DOLLARS — every
        # other layer assumes dollars (interpreter emits billions and the
        # screener converts to dollars; display divides by 1e9). Storing the
        # raw millions value here caused a 3-way unit mismatch where cap
        # filters compared billions against millions. Fix at the source:
        # convert once, at the boundary where the foreign unit enters.
        market_cap_millions = profile.get("marketCapitalization")
        market_cap_dollars = None
        if isinstance(market_cap_millions, (int, float)) and market_cap_millions > 0:
            candidate = int(market_cap_millions * 1_000_000)
            # Sanity guard: Finnhub returns broken market caps for some
            # OTC/ADR tickers (e.g. Samsung's SSNLF came back at $1.3
            # QUADRILLION — more than the entire US market, real mcap
            # ~$400B). A corrupt cap would rank such a name #1 in every
            # "largest companies" screen, so reject anything physically
            # impossible (> $50T; the largest company ever is ~$4T) and
            # store NULL — the screener treats NULL as "no data".
            # $6T: the largest real company ever is NVIDIA at ~$4.86T
            # (this dataset, 2026), so anything above $6T is unit
            # confusion. The first fill caught OTC/ADR names at $6.8-39T
            # (Samsung's SSNLF at $1.3Q, Nintendo's NTDOF at $9.4T,
            # Indonesian pink sheets) — tight enough to catch all of
            # those, loose enough that no legitimate market cap (even a
            # future megacap) is ever rejected.
            _MAX_SANE_MARKET_CAP = 6_000_000_000_000  # $6T
            if 0 < candidate <= _MAX_SANE_MARKET_CAP:
                market_cap_dollars = candidate
            else:
                logger.debug(
                    "Rejecting implausible market cap for %s: %.3g (unit confusion)",
                    symbol, candidate,
                )

        # Some Finnhub plans return the industry only under
        # ``finnhubIndustry`` (the free-tier profile2 payload omits the
        # GICS ``sector``/``industry`` fields entirely). Use whatever is
        # available; derive a GICS-style sector from the industry name as
        # a best-effort fallback so sector filters keep working.
        industry = profile.get("industry") or profile.get("finnhubIndustry")
        sector = profile.get("sector") or _derive_sector(industry)

        return {
            "symbol": symbol,
            "company_name": profile.get("name", ""),
            "sector": sector,
            "industry": industry,
            "market_cap": market_cap_dollars,  # canonical unit: dollars
            "pe_ratio": profile.get("peRatio"),
            "eps_ttm": profile.get("epsTTM"),
            "dividend_yield": profile.get("dividendYield"),
            "beta": profile.get("beta"),
            "high_52w": profile.get("high52"),
            "low_52w": profile.get("low52"),
        }
    except Exception as e:
        logger.debug("Failed to fetch profile for %s: %s", symbol, e)
        return None


def fetch_financial_metrics(
    pool: FinnhubClientPool, symbol: str
) -> Optional[Dict[str, Any]]:
    """Fetch valuation / profitability / growth ratios (Tier 2 screener).

    Uses Finnhub's ``/stock/metric`` basic-financials endpoint (free tier),
    which returns a ``metric`` dict keyed by names like ``psTTM``,
    ``roeTTM``, ``grossMarginTTM``, etc. All values are returned as-is;
    NULL/missing keys stay None so the screener treats them as "no data".

    Args:
        pool: The Finnhub client pool.
        symbol: Ticker symbol.

    Returns:
        Dict with the extra stock_master columns, or None on failure.
    """
    try:
        client = pool.next()
        data = _api_call_with_retry(
            lambda c=client: c.company_basic_financials(symbol=symbol, metric="all")
        )

        if not data or not isinstance(data, dict):
            return None
        m = data.get("metric") or {}
        if not isinstance(m, dict):
            return None

        def _num(key: str) -> Optional[float]:
            v = m.get(key)
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        return {
            "symbol": symbol,
            "pe_ratio": _num("peTTM"),
            "eps_ttm": _num("epsTTM"),
            "dividend_yield": _num("dividendYieldIndicatedAnnual"),
            "beta": _num("beta"),
            "ps_ratio": _num("psTTM"),
            "pb_ratio": _num("pb"),
            # Finnhub key names VERIFIED live against /stock/metric (metric=all):
            # the response carries ``evEbitdaTTM`` and ``netProfitMarginTTM``;
            # the previously-used ``evToEBITDA`` / ``netMarginTTM`` do not
            # exist in the payload, so every refresh silently wrote NULL into
            # these two columns (0% populated → every filter on them returned
            # 0 candidates).
            "ev_ebitda": _num("evEbitdaTTM"),
            "roe_ttm": _num("roeTTM"),
            "roa_ttm": _num("roaTTM"),
            "gross_margin_ttm": _num("grossMarginTTM"),
            "net_margin_ttm": _num("netProfitMarginTTM"),
            "revenue_growth_ttm": _num("revenueGrowthTTMYoy"),
            "eps_growth_ttm": _num("epsGrowthTTMYoy"),
        }
    except Exception as e:
        logger.debug("Failed to fetch financial metrics for %s: %s", symbol, e)
        return None


def fetch_bulk_profiles(
    pool: FinnhubClientPool, symbols: List[str]
) -> List[StockProfile]:
    """Fetch fundamental profiles for a list of stock symbols.

    Uses individual API calls per symbol since Finnhub's free tier
    doesn't offer a bulk profile endpoint.

    Args:
        pool: The Finnhub client pool.
        symbols: List of ticker symbols.

    Returns:
        List of stock profile dicts (only successfully fetched ones).
    """
    profiles: List[StockProfile] = []
    total = len(symbols)

    for i, symbol in enumerate(symbols):
        if i % 100 == 0 and i > 0:
            logger.info("Fetched profiles: %d/%d", i, total)

        profile = fetch_stock_profile(pool, symbol)
        if profile:
            profiles.append(profile)

    logger.info("Fetched %d/%d profiles successfully", len(profiles), total)
    return profiles


# ═══════════════════════════════════════════════════════════════════════════════
# Real-Time Quote Data (10-Minute Refresh)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_stock_quote(pool: FinnhubClientPool, symbol: str) -> Optional[QuoteData]:
    """Fetch real-time quote data for a single stock.

    Args:
        pool: The Finnhub client pool.
        symbol: Ticker symbol.

    Returns:
        Dict with current price, change, volume data, or None on failure.
    """
    try:
        client = pool.next()
        quote = _api_call_with_retry(
            lambda c=client: c.quote(symbol)
        )

        if not quote or not isinstance(quote, dict):
            return None

        price = quote.get("c")
        # A zero/negative "current price" is not a real quote — it's Finnhub
        # returning 0.0 for halted/delisted/OTC names. Storing 0.0 pollutes
        # the completeness guard (0.0 passes IS NOT NULL) and every
        # price-sorted screen. Treat it as "no data" (None) instead.
        try:
            price = float(price) if price is not None else None
            if price is not None and price <= 0:
                price = None
        except (TypeError, ValueError):
            price = None

        return {
            "symbol": symbol,
            "current_price": price,          # Current price (None if bogus)
            "price_change_pct": quote.get("dp"),    # Percent change
            # No volume here: Finnhub's /quote does not return one (the old
            # ``quote.get("v")`` was always None). It comes from daily bars —
            # compute_indicators and cache_warmer.backfill_volume_from_history.
        }
    except Exception as e:
        logger.debug("Failed to fetch quote for %s: %s", symbol, e)
        return None


class _AggregateRateLimiter:
    """Thread-safe pace limiter: at most ``rate`` calls per second overall.

    Workers call :meth:`wait` before each API call; the first caller wins
    the next slot, later callers sleep until their slot. This keeps the
    parallel fetch inside the account's aggregate budget (N keys × 60
    req/min = N req/sec) instead of tripping 429s.
    """

    def __init__(self, rate: float) -> None:
        self._min_interval = 1.0 / rate
        self._lock = threading.Lock()
        self._next_ok = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next_ok:
                delay = self._next_ok - now
            else:
                delay = 0.0
            self._next_ok = max(now, self._next_ok) + self._min_interval
        if delay > 0:
            time.sleep(delay)


def fetch_bulk_quotes(
    pool: FinnhubClientPool, symbols: List[str]
) -> List[QuoteData]:
    """Fetch real-time quotes for a list of stock symbols, in parallel.

    Fans out across a thread pool sized to the number of Finnhub keys so
    requests go out concurrently (the round-robin pool is thread-safe), paced
    by an aggregate rate limiter at the account budget (N keys × 60 req/min
    = N req/sec). A full ~5k-symbol exchange-listed pass drops from ~40
    minutes serial to ~9 minutes at the 600 req/min budget.

    Args:
        pool: The Finnhub client pool.
        symbols: List of ticker symbols.

    Returns:
        List of quote dicts.
    """
    quotes: List[QuoteData] = []
    total = len(symbols)
    if not symbols:
        logger.info("Fetched 0/0 quotes successfully")
        return quotes

    rate_per_sec = max(1, pool.size)  # N keys → N req/sec aggregate
    limiter = _AggregateRateLimiter(rate_per_sec)
    lock = threading.Lock()
    progress = {"done": 0}

    def _fetch(symbol: str) -> None:
        limiter.wait()
        quote = fetch_stock_quote(pool, symbol)
        with lock:
            progress["done"] += 1
            done = progress["done"]
            if done % 200 == 0:
                logger.info("Fetched quotes: %d/%d", done, total)
            if quote and quote.get("current_price") is not None:
                quotes.append(quote)

    workers = min(pool.size, 10)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(_fetch, symbols))

    logger.info("Fetched %d/%d quotes successfully", len(quotes), total)
    return quotes


# ═══════════════════════════════════════════════════════════════════════════════
# Technical Metrics (for screener filters)
# ═══════════════════════════════════════════════════════════════════════════════

# Finnhub's /indicator endpoint is a PAID-tier resource on most free keys
# (verified live: 403 "You don't have access to this resource"). The proven
# free path — used by backfill_technicals.py and the backtester — computes
# the full indicator set LOCALLY from yfinance daily history. The fetch
# below tries Finnhub first (for plans that DO have access), then falls back
# to the local yfinance computation so technicals never silently go missing.

# Columns compute_indicators produces (mirrors backfill_technicals.py).
TECH_COLUMNS = [
    "current_price",
    "sma_50",
    "sma_200",
    "rsi_14",
    "high_52w",
    "low_52w",
    "macd_12_26",
    "atr_14",
    "bollinger_upper",
    "bollinger_lower",
    "avg_volume",
    # Day 14: the latest session's volume. Finnhub's /quote has no volume
    # field (``quote.get("v")`` was always None), so `volume` was NULL for
    # every stock while /meta advertised a volume filter and sort. The daily
    # bars this module already downloads carry it.
    "volume",
]

# How many trailing sessions the avg_volume computation averages. Finnhub's
# own avgVolume metric is a 3-month (≈63 trading days) mean, so the local
# computation matches that window — the screener's min_avg_volume filter
# then compares apples to apples.
AVG_VOLUME_WINDOW = 63


def compute_indicators(df) -> Dict[str, Optional[float]]:
    """Compute the screener's indicator set from a daily OHLCV frame.

    Same math as backfill_technicals.compute_indicators (the proven fill
    that populated 494/500 sma_50 on the live DB): SMA-50/200, 52w
    high/low, Wilder RSI-14, MACD(12,26) line, ATR-14, Bollinger(20,2).

    Args:
        df: Daily OHLCV DataFrame (columns Close/High/Low/...).

    Returns:
        Dict of TECH_COLUMNS → value (None when the window is too short).
    """
    import pandas as pd

    out: Dict[str, Optional[float]] = {c: None for c in TECH_COLUMNS}
    if df is None or df.empty:
        return out

    close = df["Close"].astype(float).dropna()
    if close.empty:
        return out
    high = df["High"].astype(float).dropna()
    low = df["Low"].astype(float).dropna()

    out["current_price"] = round(float(close.iloc[-1]), 2)

    # Average daily volume over the trailing 3-month window (matches
    # Finnhub's avgVolume definition). Volumes are integers; the average
    # is rounded to an integer too. When the frame lacks a Volume column
    # (e.g. a bare price frame) the column stays None rather than crashing.
    if "Volume" in df.columns:
        vol = df["Volume"].astype(float).dropna()
        if not vol.empty:
            window = vol.tail(AVG_VOLUME_WINDOW)
            if len(window) >= 5:
                out["avg_volume"] = int(round(float(window.mean())))
            out["volume"] = int(round(float(vol.iloc[-1])))

    if len(close) >= 50:
        out["sma_50"] = round(float(close.rolling(50).mean().iloc[-1]), 2)
    if len(close) >= 200:
        out["sma_200"] = round(float(close.rolling(200).mean().iloc[-1]), 2)

    if not high.empty:
        out["high_52w"] = round(float(high.max()), 2)
    if not low.empty:
        out["low_52w"] = round(float(low.min()), 2)

    if len(close) >= 14:
        delta = close.diff()
        gain = delta.clip(lower=0.0)
        loss = (-delta).clip(lower=0.0)
        avg_gain = gain.ewm(alpha=1 / 14, min_periods=14).mean()
        avg_loss = loss.ewm(alpha=1 / 14, min_periods=14).mean()
        rs = avg_gain / avg_loss.replace(0.0, pd.NA)
        rsi = 100 - (100 / (1 + rs))
        last = rsi.dropna()
        if not last.empty:
            out["rsi_14"] = round(float(last.iloc[-1]), 2)

    if len(close) >= 26:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = (ema12 - ema26).dropna()
        if not macd.empty:
            out["macd_12_26"] = round(float(macd.iloc[-1]), 4)

    if len(close) >= 14 and not high.empty and not low.empty:
        prev_close = close.shift(1)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        atr = tr.ewm(alpha=1 / 14, min_periods=14).mean().dropna()
        if not atr.empty:
            out["atr_14"] = round(float(atr.iloc[-1]), 4)

    if len(close) >= 20:
        mid = close.rolling(20).mean()
        std = close.rolling(20).std(ddof=0)
        upper = (mid + 2 * std).dropna()
        lower = (mid - 2 * std).dropna()
        if not upper.empty:
            out["bollinger_upper"] = round(float(upper.iloc[-1]), 2)
        if not lower.empty:
            out["bollinger_lower"] = round(float(lower.iloc[-1]), 2)

    return out


def _fetch_technicals_yfinance(symbol: str) -> Optional[MetricData]:
    """Compute the full technical set locally from yfinance daily history.

    Free fallback when Finnhub /indicator is paid-tier (403) on this plan.
    Same source + math as the backtester and backfill_technicals.py. Uses
    a 1-year window (52w high/low needs it) — the same window the original
    backfill used successfully.

    Args:
        symbol: Ticker symbol.

    Returns:
        MetricData dict with symbol, or None on any failure.
    """
    try:
        import yfinance as yf
        import pandas as pd
    except ImportError as e:
        logger.debug("yfinance unavailable (%s) — no technicals for %s", e, symbol)
        return None

    # yfinance rejects DOTTED class-share tickers that Finnhub stores
    # (verified live: BRK.A/BRK.B/HEI.A → "possibly delisted", while
    # BRK-A/BRK-B/HEI-A download 251 rows). Try the stored symbol first,
    # then the dashed form — never let a ticker-form mismatch silently
    # drop a top-500 name's technicals.
    candidates = [symbol]
    if "." in symbol:
        candidates.append(symbol.replace(".", "-"))

    df = None
    for cand in candidates:
        try:
            data = yf.download(
                [cand], period="1y", interval="1d", progress=False,
                auto_adjust=True, threads=False, group_by="ticker",
            )
        except Exception as e:
            logger.debug("yfinance download failed for %s: %s", cand, e)
            continue
        if data is None or getattr(data, "empty", True):
            continue
        # Normalize the frame (single symbol with group_by may yield a
        # MultiIndex; a bare frame is already per-symbol).
        try:
            if isinstance(data.columns, pd.MultiIndex) and cand in data.columns.levels[1]:
                df = data[cand]
            elif cand in data.columns.get_level_values(0):
                df = data.xs(cand, axis=1, level=0)
            else:
                df = data
        except Exception:
            df = data
        df = df.dropna(subset=["Close"])
        if df is not None and not df.empty:
            break
        df = None

    if df is None or df.empty:
        logger.debug("yfinance returned no history for %s", symbol)
        return None
    ind = compute_indicators(df)
    ind["symbol"] = symbol
    return ind


def fetch_technical_indicators(
    pool: FinnhubClientPool, symbol: str
) -> Optional[MetricData]:
    """Fetch technical indicators (RSI, SMA) for a single stock.

    Uses Finnhub's technical-indicators endpoint when the plan has access;
    falls back to a LOCAL yfinance computation when Finnhub 403s (the /indicator
    endpoint is paid-tier on most free keys — verified live) so the technicals
    pipeline never silently produces nothing.

    Args:
        pool: The Finnhub client pool.
        symbol: Ticker symbol.

    Returns:
        Dict with technical indicator values, or None on failure.
    """
    try:
        client = pool.next()

        # Finnhub's /indicator endpoint requires a date range (from/to) and
        # the period must ride in ``indicator_fields`` — the installed SDK
        # signature is technical_indicator(symbol, resolution, _from, to,
        # indicator, indicator_fields=None). Passing a bare ``timeperiod=``
        # kwarg (as this code once did) raises TypeError instantly — which
        # the except below swallowed, so EVERY technicals fetch returned
        # None in production. Use a 2-year window so even SMA-200 has
        # enough history to compute a value.
        to_date = datetime.utcnow().strftime("%Y-%m-%d")
        from_date = (datetime.utcnow() - timedelta(days=730)).strftime("%Y-%m-%d")

        # Fetch RSI (14-day)
        rsi_data = _api_call_with_retry(
            lambda c=client: c.technical_indicator(
                symbol=symbol,
                resolution="D",
                _from=from_date,
                to=to_date,
                indicator="rsi",
                indicator_fields={"timeperiod": 14},
            )
        )

        # Fetch 50-day SMA
        sma_50_data = _api_call_with_retry(
            lambda c=client: c.technical_indicator(
                symbol=symbol,
                resolution="D",
                _from=from_date,
                to=to_date,
                indicator="sma",
                indicator_fields={"timeperiod": 50},
            )
        )

        # Fetch 200-day SMA
        sma_200_data = _api_call_with_retry(
            lambda c=client: c.technical_indicator(
                symbol=symbol,
                resolution="D",
                _from=from_date,
                to=to_date,
                indicator="sma",
                indicator_fields={"timeperiod": 200},
            )
        )

        # Tier 2: MACD (12,26,9) — we store the MACD line; a positive value
        # means bullish momentum. Also ATR-14 (volatility) and Bollinger
        # Bands (20,2) for breakout/oversold screens. Each is optional — a
        # failure here must NOT fail the whole refresh for that symbol.
        macd_data = _safe_indicator_call(
            client, symbol, indicator="macd", timeperiod=None,
            from_date=from_date, to_date=to_date,
        )
        atr_data = _safe_indicator_call(
            client, symbol, indicator="atr", timeperiod=14,
            from_date=from_date, to_date=to_date,
        )
        bbands_data = _safe_indicator_call(
            client, symbol, indicator="bbands", timeperiod=20,
            from_date=from_date, to_date=to_date,
        )

        # Extract the most recent values (Finnhub returns arrays)
        rsi = None
        if rsi_data and isinstance(rsi_data, dict) and rsi_data.get("rsi"):
            values = [v for v in rsi_data["rsi"] if v is not None]
            rsi = values[-1] if values else None

        sma_50 = None
        if sma_50_data and isinstance(sma_50_data, dict) and sma_50_data.get("sma"):
            values = [v for v in sma_50_data["sma"] if v is not None]
            sma_50 = values[-1] if values else None

        sma_200 = None
        if sma_200_data and isinstance(sma_200_data, dict) and sma_200_data.get("sma"):
            values = [v for v in sma_200_data["sma"] if v is not None]
            sma_200 = values[-1] if values else None

        macd_12_26 = None
        if macd_data and isinstance(macd_data, dict) and macd_data.get("macd"):
            values = [v for v in macd_data["macd"] if v is not None]
            macd_12_26 = values[-1] if values else None

        atr_14 = None
        if atr_data and isinstance(atr_data, dict) and atr_data.get("atr"):
            values = [v for v in atr_data["atr"] if v is not None]
            atr_14 = values[-1] if values else None

        bollinger_upper = bollinger_lower = None
        if bbands_data and isinstance(bbands_data, dict):
            upper = [v for v in bbands_data.get("upperband") or [] if v is not None]
            lower = [v for v in bbands_data.get("lowerband") or [] if v is not None]
            bollinger_upper = upper[-1] if upper else None
            bollinger_lower = lower[-1] if lower else None

        # If Finnhub returned data but every indicator is empty (e.g. the
        # symbol has no history on the plan), also fall back to the local
        # computation rather than persisting a row of None technicals.
        result = {
            "symbol": symbol,
            "rsi_14": rsi,
            "sma_50": sma_50,
            "sma_200": sma_200,
            "macd_12_26": macd_12_26,
            "atr_14": atr_14,
            "bollinger_upper": bollinger_upper,
            "bollinger_lower": bollinger_lower,
        }
        if any(result[k] is not None for k in ("rsi_14", "sma_50", "sma_200")):
            return result
        logger.debug("Finnhub returned no technical values for %s — local fallback", symbol)
        return _fetch_technicals_yfinance(symbol)
    except Exception as e:
        # Finnhub /indicator is paid-tier on most free keys (403) — never
        # silently return None: compute locally from yfinance instead.
        logger.debug(
            "Finnhub /indicator failed for %s (%s) — computing locally", symbol, e
        )
        return _fetch_technicals_yfinance(symbol)


def _safe_indicator_call(client, symbol: str, indicator: str, timeperiod,
                         from_date: Optional[str] = None,
                         to_date: Optional[str] = None):
    """Best-effort Finnhub technical-indicator call that never raises.

    The extra Tier 2 indicators (MACD/ATR/Bollinger) are optional enrich-
    ment — a rate limit or missing data for one must not break the whole
    symbol's refresh.

    Args:
        client: A Finnhub client from the pool.
        symbol: Ticker symbol.
        indicator: Indicator name ('macd', 'atr', 'bbands').
        timeperiod: Period for the indicator, or None if not applicable.
        from_date: Range start (YYYY-MM-DD). Required by the SDK — the
            /indicator endpoint takes from/to, and the period rides in
            ``indicator_fields``.
        to_date: Range end (YYYY-MM-DD).

    Returns:
        Raw response dict, or None on any failure.
    """
    try:
        kwargs = {
            "symbol": symbol,
            "resolution": "D",
            "indicator": indicator,
        }
        if from_date and to_date:
            kwargs["_from"] = from_date
            kwargs["to"] = to_date
        if timeperiod is not None:
            kwargs["indicator_fields"] = {"timeperiod": timeperiod}
        return _api_call_with_retry(lambda c=client: c.technical_indicator(**kwargs))
    except Exception as e:
        logger.debug("Indicator %s for %s unavailable: %s", indicator, symbol, e)
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# News Fetching (dedicated key + fallback)
# ═══════════════════════════════════════════════════════════════════════════════
#
# The news pipeline uses its OWN Finnhub key (NEWS_FINNHUB_KEY) so news
# fetching never competes with the screener's quote/profile refresh on the
# main 10 keys. If the dedicated key ever rate-limits (429) or errors, the
# fetch falls back to the shared pool — news never hard-fails.
#

_NEWS_DEDICATED_CLIENT = None  # lazy singleton for the dedicated key


def _get_news_client() -> Optional[finnhub.Client]:
    """Return the dedicated news Finnhub client (built once from .env)."""
    global _NEWS_DEDICATED_CLIENT
    if _NEWS_DEDICATED_CLIENT is None:
        key = settings.news_finnhub_key
        if not key:
            return None
        _NEWS_DEDICATED_CLIENT = finnhub.Client(api_key=key)
        # Same timeout override as the shared pool (SDK default is 10s).
        _NEWS_DEDICATED_CLIENT.DEFAULT_TIMEOUT = settings.finnhub_request_timeout
        logger.info("Dedicated news Finnhub client initialized")
    return _NEWS_DEDICATED_CLIENT


def fetch_company_news(
    pool: FinnhubClientPool,
    symbol: str,
    from_date: str,
    to_date: str,
) -> List[Dict[str, Any]]:
    """Fetch company news for a symbol using the dedicated news key.

    Uses ``NEWS_FINNHUB_KEY`` exclusively for /company-news so the news
    refresh never consumes the screener keys' budget. Falls back to the
    shared pool on rate limit or error, so a throttled news key degrades
    gracefully instead of failing the whole news cycle.

    Args:
        pool: The shared Finnhub client pool (fallback source).
        symbol: Ticker symbol (e.g. 'AAPL').
        from_date: Start date (YYYY-MM-DD).
        to_date: End date (YYYY-MM-DD).

    Returns:
        List of news article dicts (empty on failure).
    """
    dedicated = _get_news_client()

    # 1) Try the dedicated news key first
    if dedicated is not None:
        try:
            news = _api_call_with_retry(
                lambda c=dedicated: c.company_news(symbol, _from=from_date, to=to_date)
            )
            return news if isinstance(news, list) else []
        except Exception as e:
            logger.warning(
                "Dedicated news key failed for %s (%s) — falling back to shared pool",
                symbol,
                e,
            )

    # 2) Fallback: round-robin the shared pool
    try:
        client = pool.next()
        news = _api_call_with_retry(
            lambda c=client: c.company_news(symbol, _from=from_date, to=to_date)
        )
        return news if isinstance(news, list) else []
    except Exception as e:
        logger.debug("News fetch fallback failed for %s: %s", symbol, e)
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# Orchestration: Full Data Refresh
# ═══════════════════════════════════════════════════════════════════════════════

def build_stock_record(
    profile: Optional[StockProfile],
    quote: Optional[QuoteData],
    technicals: Optional[MetricData],
) -> Optional[Dict[str, Any]]:
    """Merge profile, quote, and technical data into a single stock DB record.

    Args:
        profile: Fundamental profile data (may be None if fetch failed).
        quote: Real-time quote data (may be None).
        technicals: Technical indicator data (may be None).

    Returns:
        A merged dict suitable for DB upsert, or None if no useful data.
    """
    if not profile and not quote:
        return None

    record: Dict[str, Any] = {
        "updated_at": datetime.utcnow().isoformat(),
    }

    if profile:
        record.update(profile)
    if quote:
        record.update(quote)
    if technicals:
        record.update(technicals)

    # Ensure symbol is present
    if "symbol" not in record:
        return None

    # Market-cap sanity (unit-confusion guard): Finnhub returns badly
    # inflated marketCapitalization for foreign OTC/ADR pink-sheet tickers
    # (Nintendo's NTDOF came back at $9.4T implying 195 BILLION shares when
    # Nintendo has ~1.2B; the real NVDA/AAPL values imply sane ~15-24B
    # share counts). When we have a real price, cross-check the implied
    # share count: no company on Earth has >50B shares (NVDA at ~24B is
    # the largest ever), so an implied count above that proves the mcap is
    # corrupted — store NULL and let the completeness guard treat the row
    # as missing market cap instead of letting it top "largest" screens.
    price = record.get("current_price")
    cap = record.get("market_cap")
    if cap and price:
        try:
            implied_shares = float(cap) / float(price)
            if implied_shares > 50_000_000_000:  # 50B shares
                logger.debug(
                    "Rejecting market cap for %s: implies %.3g shares (corrupt OTC/ADR unit)",
                    record.get("symbol"), implied_shares,
                )
                record["market_cap"] = None
                cap = None
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    # Foreign OTC pink sheets: US tickers ending in ``F`` are the OTC
    # convention for unsponsored foreign ADR listings (NTDOF=Nintendo,
    # DISPF=Disco, DAIUF=Daimler, BNPEF=BNP Paribas...). Finnhub reports
    # their marketCapitalization with a broken share-count unit (~8-330x
    # inflated): the live fill caught SSNLF=Samsung at $1.3Q (real ~$400B),
    # NTDOF=Nintendo at $9.4T (real ~$60B), and mid-size names like
    # Nagase & Co at $497B (real ~$1.5B), Towa Corp at $199B (real ~$2B),
    # CLP Holdings at $197B (real ~$25B) and Novozymes at $196B (real
    # ~$25B) — all of which would top "largest companies" screens above
    # real US megacaps. No genuine US-listed company ends in ``F`` (real
    # megacaps: NVDA/MSFT/AAPL/GOOGL/AMZN/META — even Ford's ticker is
    # the bare letter ``F``, whose ~$58B cap is far below the threshold),
    # and the largest legit foreign name trading as an F-suffix OTC pink
    # sheet is on the order of ~$60B (Nintendo). Anything above $100B for
    # that population is unit corruption in practice — the threshold is set
    # just above the largest legitimate value so no real cap is rejected.
    symbol = record.get("symbol", "")
    if cap and isinstance(symbol, str) and symbol.endswith("F") and cap > 100_000_000_000:
        logger.debug(
            "Rejecting foreign OTC/ADR market cap for %s: %.3g (broken ADR unit)",
            symbol, cap,
        )
        record["market_cap"] = None

    return record


def refresh_price_data(
    pool: FinnhubClientPool,
    symbols: List[str],
    include_technicals: bool = True,
    tech_symbols: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Fetch fresh quotes and technicals for a list of stocks.

    This is the "every N minutes" job. Updates price, volume, and
    optionally RSI/SMA.

    Technical indicators (RSI, SMA) change slowly and don't need
    refreshing every cycle — pass ``include_technicals=False`` for
    most cycles and ``True`` every 4th-6th cycle to save API calls
    and database compute time.

    Args:
        pool: The Finnhub client pool.
        symbols: List of ticker symbols to refresh.
        include_technicals: Whether to also fetch RSI-14, SMA-50, SMA-200
            for the technicals universe. Default True.
        tech_symbols: WHICH symbols to fetch technicals for. The doc
            intent is the top-N by market cap — the scheduler passes
            ``PostgresDB.get_top_symbols(500)`` (the same universe the
            news job uses). When None (API /refresh, one-off scripts),
            falls back to ``symbols[:500]`` — note that the symbol list
            from ``fetch_us_stock_symbols`` is ALPHABETICAL, so the
            fallback is the first 500 alphabetically, NOT top-by-cap.
            Callers with DB access should always pass ``tech_symbols``
            so technicals reach the names users actually screen.

    Returns:
        List of stock records ready for database upsert.
    """
    quotes = fetch_bulk_quotes(pool, symbols)
    quote_map = {q["symbol"]: q for q in quotes}

    # Only fetch technicals when requested (they change slowly —
    # RSI/SMA refresh once per hour is plenty)
    tech_map: Dict[str, MetricData] = {}
    if include_technicals:
        # Prefer the caller's explicit universe (top-N by market cap from
        # the scheduler); never fetch technicals for symbols outside the
        # refreshed list (wasted API calls that would never be merged).
        symbol_set = set(symbols)
        if tech_symbols is not None:
            # An explicitly-passed universe (even empty) is authoritative —
            # never silently fall back to the alphabetical slice, which would
            # refresh the wrong 500. Filter to symbols actually in the
            # refresh list to avoid wasted API calls that never merge.
            tech_symbols = [s for s in tech_symbols if s in symbol_set]
        else:
            tech_symbols = symbols[:500]
        technicals_list = [
            fetch_technical_indicators(pool, sym) for sym in tech_symbols
        ]
        tech_map = {
            t["symbol"]: t for t in technicals_list if t
        }
        logger.debug(
            "Fetched technical indicators for %d symbols", len(tech_map)
        )

    records: List[Dict[str, Any]] = []
    for symbol in symbols:
        quote = quote_map.get(symbol)
        technicals = tech_map.get(symbol)

        record = build_stock_record(None, quote, technicals)
        if record:
            records.append(record)

    what = "quotes+technicals" if include_technicals else "quotes only"
    logger.info(
        "Price refresh complete (%s): %d records with data (of %d symbols)",
        what,
        len(records),
        len(symbols),
    )
    return records


def refresh_fundamental_data(
    pool: FinnhubClientPool,
    symbols: List[str],
    include_metrics: bool = True,
) -> List[Dict[str, Any]]:
    """Fetch/refresh fundamental profiles for all stocks.

    This is the "daily" job. Updates sector, industry, market cap, etc.
    Optionally (``include_metrics=True``, the default) also pulls the
    Tier 2 valuation/profitability/growth ratios via /stock/metric and
    merges them into the record — a few extra calls per symbol on the
    daily cadence, well within the free-tier budget.

    Args:
        pool: The Finnhub client pool.
        symbols: List of ticker symbols.
        include_metrics: Whether to also fetch the extra financial ratios.

    Returns:
        List of stock profile records ready for database upsert.
    """
    profiles = fetch_bulk_profiles(pool, symbols)
    profile_map = {p["symbol"]: p for p in profiles}

    if include_metrics and profiles:
        # One /stock/metric call per symbol; merge into the profile record.
        metrics_list = [
            fetch_financial_metrics(pool, sym) for sym in profile_map.keys()
        ]
        for m in metrics_list:
            if m and m.get("symbol") in profile_map:
                profile_map[m["symbol"]].update(
                    {k: v for k, v in m.items() if k != "symbol"}
                )
        logger.info(
            "Fetched financial metrics for %d symbols",
            sum(1 for m in metrics_list if m),
        )

    logger.info(
        "Fundamental refresh complete: %d profiles fetched (of %d symbols)",
        len(profiles),
        len(symbols),
    )
    return list(profile_map.values())
