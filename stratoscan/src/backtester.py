"""Backtesting Engine for Stratoscan (Premium Feature).

Simulates trading a strategy on historical data to validate its performance.

This is Stratoscan's monetization layer — the backtesting engine is only
available to paid subscribers ($10/month).

Design:
  - Data Source: 10 years of daily OHLCV from Yahoo Finance (yfinance).
  - Storage: dedicated Neon/PostgreSQL database (BacktestDB,
    BACKTEST_DATABASE_URL) — production-ready shared storage. The local
    SQLite file is reserved for local dev / tests only.
  - Strategy: Rule-based signals derived from the LLM-interpreted criteria.
  - Metrics: Total return, Sharpe ratio, max drawdown, win rate.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

from src.config import settings
from src.database import BacktestDB, SQLiteDB
from src.interpreter import InterpretedQuery

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

# Default backtest parameters
DEFAULT_INITIAL_CAPITAL: float = 100_000.0
DEFAULT_FEE_RATE: float = 0.001  # 0.1% per trade (commission)
DEFAULT_SLIPPAGE: float = 0.0005  # 0.05% slippage per trade
DEFAULT_SLIPPAGE_CENTS: float = 0.0  # fixed cost per share (Tier 2 realism)
# Cap each order at this % of the bar's volume so a small-cap order can't
# silently move the market (Tier 2 liquidity model). 0 = unlimited.
DEFAULT_MAX_ORDER_VOLUME_PCT: float = 0.05
DEFAULT_POSITION_SIZING: str = "all_in"  # all_in | fixed_pct | risk_based

# How many years of historical data to fetch
HISTORICAL_YEARS: int = 10

# How many symbols to backtest concurrently. The dominant per-symbol cost is
# the yfinance download of 10 years of daily OHLCV (I/O-bound, verified live
# at 40s–2.5min on a cold cache) — a bounded pool makes a top_n=5 backtest
# cost ~1x a single fetch instead of 5x. Kept small so a broad query never
# hammers Yahoo or the DB pool (BacktestDB maxconn=3; SQLite serializes
# writes via its own lock).
BACKTEST_PARALLEL_WORKERS: int = 4

# Benchmark symbol for the "vs. the market" comparison (Tier 1 credibility).
# SPY is the default every investor can relate to; fetched and cached in the
# same backtest DB (BacktestDB in production, SQLite in dev) as any symbol.
BENCHMARK_SYMBOL: str = "SPY"

# Fewer than this many closed trades → Sharpe/drawdown/win-rate are noise.
# Surface a warning instead of presenting noise as signal.
MIN_TRADES_FOR_CONFIDENCE: int = 30

# Equity curve downsampling cap (payload size vs. fidelity tradeoff).
MAX_EQUITY_CURVE_POINTS: int = 300

# ── Tier 3 robustness defaults ───────────────────────────────────────────────
# All robustness analyses are pure compute over data the engine already has
# (the Stratoscan way — near-zero marginal cost). Seeds are FIXED so a
# backtest reproduces the exact same robustness numbers on every run.
ROBUSTNESS_SEED: int = 42
MONTE_CARLO_SIMULATIONS: int = 1000
BOOTSTRAP_SIMULATIONS: int = 1000
WALK_FORWARD_SPLITS: int = 4
SENSITIVITY_RSI_GRID: Tuple[float, ...] = (20.0, 25.0, 30.0, 35.0)
SENSITIVITY_SMA_GRID: Tuple[int, ...] = (30, 50, 70)


@dataclass
class Trade:
    """A single simulated trade."""

    symbol: str
    buy_date: str
    buy_price: float
    sell_date: Optional[str] = None
    sell_price: Optional[float] = None
    shares: int = 0
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    is_open: bool = True
    side: str = "long"  # "long" | "short" (Tier 2)
    exit_reason: str = "signal"  # signal | stop_loss | take_profit | trailing_stop | end_of_period


@dataclass
class BacktestResult:
    """Performance results from a backtest simulation."""

    symbol: str
    strategy_params: Dict[str, Any]
    start_date: str
    end_date: str
    initial_capital: float
    final_value: float
    total_return: float  # decimal
    sharpe_ratio: Optional[float] = None
    max_drawdown: Optional[float] = None  # decimal
    win_rate: Optional[float] = None  # decimal
    total_trades: int = 0
    trades: List[Trade] = field(default_factory=list)

    # Tier 1 credibility additions — equity curve for charting, confidence
    # warnings, and benchmark comparison (SPY) with alpha/beta/excess.
    equity_curve: List[Dict[str, Any]] = field(default_factory=list)
    # Tier 4 chart data: the drawdown series over the same dates (negative
    # decimals, downsampled) — the second chart every backtester UI shows,
    # next to the equity curve.
    drawdown_curve: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    benchmark_total_return: Optional[float] = None  # decimal
    alpha: Optional[float] = None  # annualized, decimal
    beta: Optional[float] = None
    excess_return: Optional[float] = None  # decimal

    # Tier 2 realism — buy-and-hold comparisons (price-only vs total return,
    # dividend contribution) and a data-quality audit trail.
    buy_hold_price_return: Optional[float] = None  # decimal
    buy_hold_total_return: Optional[float] = None  # decimal
    dividend_contribution: Optional[float] = None  # decimal
    data_quality: List[str] = field(default_factory=list)
    # Per-symbol data freshness from the screener (None = compare-mode or
    # unknown). Rendered by the frontend as a DataAgeBadge next to the
    # symbol tab, matching the screener's staleness indicator.
    data_age_minutes: Optional[float] = None
    is_stale: bool = False

    # Tier 5 portfolio + cash accounting. full_equity_curve is the FULL-
    # resolution (daily) equity series — the portfolio aggregator needs
    # daily resolution to combine symbols and apply a rebalance schedule;
    # the downsampled ``equity_curve`` alone would silently mis-weight the
    # combined curve. It is consumed server-side and NOT serialized by the
    # API response model (which picks fields explicitly).
    full_equity_curve: List[Dict[str, Any]] = field(default_factory=list)
    # Opt-in cash accounting (dividend accrual + short borrow costs):
    # cumulative $ received/paid, reported so the UI can show income
    # instead of only an embedded total-return number.
    dividends_received: float = 0.0
    borrow_fees_paid: float = 0.0

    # Tier 3 robustness — prove it's not luck. Filled by run_backtest for
    # Monte Carlo + bootstrap CI; by BacktestEngine for walk-forward and
    # parameter sensitivity (both need the full warmed history).
    monte_carlo: Optional[Dict[str, Any]] = None
    confidence_intervals: Optional[Dict[str, Any]] = None
    walk_forward: Optional[Dict[str, Any]] = None
    parameter_sensitivity: Optional[Dict[str, Any]] = None

    @property
    def return_pct(self) -> str:
        """Format total return as a percentage string."""
        return f"{self.total_return * 100:.2f}%"

    @property
    def summary(self) -> str:
        """Generate a human-readable backtest summary."""
        lines = [
            f"Backtest Results: {self.symbol}",
            f"Period: {self.start_date} → {self.end_date}",
            f"Initial Capital: ${self.initial_capital:,.2f}",
            f"Final Value: ${self.final_value:,.2f}",
            f"Total Return: {self.return_pct}",
        ]
        if self.sharpe_ratio is not None:
            lines.append(f"Sharpe Ratio: {self.sharpe_ratio:.3f}")
        if self.max_drawdown is not None:
            lines.append(f"Max Drawdown: {self.max_drawdown * 100:.2f}%")
        if self.win_rate is not None:
            lines.append(f"Win Rate: {self.win_rate * 100:.1f}%")
        lines.append(f"Total Trades: {self.total_trades}")
        if self.benchmark_total_return is not None:
            lines.append(
                f"Benchmark ({BENCHMARK_SYMBOL}): {self.benchmark_total_return * 100:.2f}%"
            )
        if self.excess_return is not None:
            lines.append(f"Excess Return: {self.excess_return * 100:.2f}%")
        if self.alpha is not None:
            beta_str = f"{self.beta:.2f}" if self.beta is not None else "n/a"
            lines.append(f"Alpha: {self.alpha * 100:.2f}% | Beta: {beta_str}")
        if self.buy_hold_total_return is not None:
            lines.append(
                f"Buy & Hold — price: {self.buy_hold_price_return * 100:.2f}% | "
                f"total: {self.buy_hold_total_return * 100:.2f}% | "
                f"dividends: {self.dividend_contribution * 100:.2f}%"
            )
        for w in self.warnings:
            lines.append(f"⚠️  {w}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# Strategy Translation
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_pct(raw_lower: str, keywords: Tuple[str, ...]) -> Optional[float]:
    """Extract a percentage following a keyword: ``'stop loss 8%'`` → 0.08.

    Also accepts the reversed form (``'8% stop loss'``). Returns None when
    no percentage appears near the keyword, so absent phrasing = no
    parameter (safe default), not a bogus 0%.

    Args:
        raw_lower: The lowercased user query.
        keywords: Phrases to look for (e.g. ("stop loss", "cut losses")).

    Returns:
        The percentage as a decimal fraction, or None.
    """
    kw = "|".join(keywords)
    fwd = re.search(rf"\b(?:{kw})\b[^%]{{0,15}}?(\d+(?:\.\d+)?)\s*%", raw_lower)
    if fwd:
        return float(fwd.group(1)) / 100.0
    rev = re.search(rf"(\d+(?:\.\d+)?)\s*%\s*\b(?:{kw})\b", raw_lower)
    if rev:
        return float(rev.group(1)) / 100.0
    return None


def _translate_to_rules(interpreted: InterpretedQuery) -> Dict[str, Any]:
    """Translate the LLM-interpreted query into a simple rule-based strategy.

    Args:
        interpreted: The interpreted user query.

    Returns:
        Dict with 'buy_rules' and 'sell_rules' as lists of conditions.
    """
    rules: Dict[str, list] = {"buy": [], "sell": []}

    # Defensive: interpret_query always returns a str raw_input, but a
    # mocked/corrupt one must not crash rule generation (re.search would
    # TypeError on a non-string). str() is a no-op on real strings.
    raw_lower = str(interpreted.raw_input).lower()

    for criterion in interpreted.filters:
        field = criterion.field
        op = criterion.operator.value
        val = criterion.value

        # RSI-based signals. A ``min_rsi`` (gte) filter is usually a SCREENING
        # criterion, not a sell signal — the interpreter sets min_rsi=50 for
        # "rising momentum", so treating every gte as "sell when RSI > X"
        # would close a momentum entry the moment RSI crosses 50 (almost
        # immediately). Only a genuine overbought intent (literal word, or
        # the conventional min_rsi >= 70 threshold) becomes a sell rule.
        # A ``max_rsi`` (lte) filter stays a mean-reversion buy signal.
        if field == "rsi_14":
            if op == "lte":
                rules["buy"].append(f"rsi < {val}")
            elif op == "gte" and ("overbought" in raw_lower or val >= 70):
                rules["sell"].append(f"rsi > {val}")

        # P/E is deliberately NOT translated into a tradeable rule. The
        # backtest frame is daily OHLCV + technical indicators — it has no
        # per-bar P/E column — and _apply_rule's "pe": "Close" fallback
        # silently turned a `pe < 15` rule into "price < 15" (verified by
        # the rule-execution loop: "buy undervalued stocks" fired on PRICE,
        # not P/E; a $200 stock could never qualify, a $10 stock always
        # did). The screener already applied the real P/E filter when it
        # selected the candidate set, so the backtest trades those already-
        # undervalued names on technical signals instead of lying about P/E.
        # (If a pe rule ever reaches the sim, _apply_rule still evaluates it
        # deterministically — but no translation path emits one.)

    # Momentum — emitted ONCE, AFTER the criterion loop. The old code placed
    # this inside the loop, so a query with N filters produced N copies of
    # the momentum rule (verified live: 'buy oversold tech stocks with
    # rising momentum' → buy=['price > sma_50', 'rsi < 35', 'price >
    # sma_50']). Redundant rules don't change AND-logic, but they bloat the
    # auditable strategy echo and look like a bug to users.
    if interpreted.momentum == "bullish":
        rules["buy"].append("price > sma_50")
    elif interpreted.momentum == "bearish":
        rules["sell"].append("price < sma_50")

    # Default rules if nothing specific was inferred
    if not rules["buy"]:
        rules["buy"] = ["rsi < 30"]  # Classic oversold buy
    if not rules["sell"]:
        rules["sell"] = ["rsi > 70"]  # Classic overbought sell

    raw = raw_lower

    # ── Tier 2: risk parameters from plain English ──────────────────────
    # Conservative, documented heuristics. Whatever the user states becomes
    # an enforceable sim parameter; nothing is invented when absent.
    stop_loss = _extract_pct(raw, ("stop loss", "stop-loss", "cut losses", "cut my losses"))
    take_profit = _extract_pct(raw, ("take profit", "take-profit", "profit target", "target"))
    trailing_stop = _extract_pct(raw, ("trailing stop", "trailing-stop", "trail"))
    risk_per_trade = _extract_pct(raw, ("risk", "risk per trade"))
    position_pct = _extract_pct(raw, ("position size", "size positions", "invest"))
    allow_short = bool(re.search(r"\bshort\b", raw))
    m_lev = re.search(
        r"(\d+(?:\.\d+)?)\s*[xX×]\s*(?:leverage|leveraged)"
        r"|(?:leverage|leveraged|margin)\s*(?:of\s+)?(\d+(?:\.\d+)?)\s*[xX×]"
        r"|(\d+(?:\.\d+)?)\s*:\s*1\b",
        raw,
    )
    max_leverage = (
        float(next((g for g in m_lev.groups() if g), "1.0")) if m_lev else None
    )

    return {
        "initial_capital": DEFAULT_INITIAL_CAPITAL,
        "fee_rate": DEFAULT_FEE_RATE,
        "slippage": DEFAULT_SLIPPAGE,
        "slippage_cents": DEFAULT_SLIPPAGE_CENTS,
        "max_order_volume_pct": DEFAULT_MAX_ORDER_VOLUME_PCT,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "trailing_stop": trailing_stop,
        "position_sizing": (
            "risk_based" if risk_per_trade is not None else DEFAULT_POSITION_SIZING
        ),
        "position_pct": position_pct if position_pct is not None else 1.0,
        "risk_per_trade": risk_per_trade if risk_per_trade is not None else 0.01,
        "allow_short": allow_short,
        "max_leverage": max_leverage if max_leverage is not None else 1.0,
        "buy_rules": rules["buy"],
        "sell_rules": rules["sell"],
    }


def translate_strategy(interpreted: InterpretedQuery) -> Dict[str, Any]:
    """Translate an interpreted query into executable strategy rules (public).

    Thin public wrapper over :func:`_translate_to_rules` — the API echoes
    these exact rules back to the user so a backtest is auditable ("what
    exactly was simulated?"), rather than a black box.

    Args:
        interpreted: The interpreted user query.

    Returns:
        Dict with initial_capital, fee_rate, slippage, buy_rules, sell_rules.
    """
    return _translate_to_rules(interpreted)


# Ticker-like upper-case tokens that are English words, not symbols. A false
# positive here silently narrows a backtest to a bogus ticker, so the list is
# deliberately tight and under-matches.
_TICKER_STOPWORDS = {
    "AND", "THE", "FOR", "WITH", "FROM", "LAST", "PAST", "OVER", "BUY",
    "HOLD", "SELL", "YEAR", "MONTH", "WEEK", "DAY", "STOCK", "STOCKS",
    "TECH", "BEST", "TOP", "ALL", "NEW", "HIGH", "LOW", "BIG", "SMALL",
    # Technical-indicator acronyms the frontend folds into the query text
    # (its momentum tune clause appends "momentum: RSI buy below 30, RSI
    # sell above 70, SMA cross 20/50"). These are NEVER the ticker a user
    # means in a strategy sentence — treating them as symbols backtested
    # phantom "RSI"/"SMA" (a full yfinance fetch each, ~1579/339 days of
    # junk) and pushed requests past the client's 120s timeout. Deliberately
    # NOT including "ROC" (Rockwell Automation — a real ticker).
    "RSI", "SMA", "EMA", "WMA", "MACD", "ATR", "ADX", "CCI", "OBV",
    "STOCH", "MOM", "MOMENTUM", "CROSS", "BELOW", "ABOVE", "SIGNAL",
}

# How many days each date-window unit spans (for "last N ..." parsing).
_WINDOW_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}


def _extract_tickers(raw: str) -> List[str]:
    """Pull explicit ticker symbols (AAPL, NVDA, BRK.A) from a query.

    Matches 1-5 upper-case letters with an optional ``.A``/``.B`` class
    suffix (BRK.A), then drops English stopwords. Lower-case prose never
    matches, so "buy oversold tech stocks" yields nothing.

    Args:
        raw: The raw user query.

    Returns:
        Deduplicated ticker candidates in order of appearance.
    """
    candidates = re.findall(r"\b[A-Z]{1,5}(?:\.[A-Z])?\b", str(raw or ""))
    seen: List[str] = []
    for c in candidates:
        if len(c) >= 2 and c not in _TICKER_STOPWORDS and c not in seen:
            seen.append(c)
    return seen


def _extract_date_window(raw_lower: str) -> Optional[tuple]:
    """Extract a "last N days/weeks/months/years" window.

    Args:
        raw_lower: Lowercased user query.

    Returns:
        ``(start_date, end_date)`` as YYYY-MM-DD (end = today), or None.
    """
    m = re.search(
        r"\b(?:last|past|previous)\s+(\d+)\s*(day|week|month|year)s?\b",
        raw_lower,
    )
    if not m:
        return None
    days = _WINDOW_DAYS[m.group(2)] * int(m.group(1))
    end = datetime.now()
    start = end - timedelta(days=days)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def parse_backtest_query(query: str) -> Dict[str, Any]:
    """Extract strategy directives the SCREENER interpreter cannot see.

    ``interpret_query`` is a *screener* interpreter (sector / P/E / RSI /
    market cap). Strategy-style phrasing falls straight through it — a query
    like "buy and hold AAPL for the last 6 months" yields ZERO criteria, so
    the engine silently ran the default RSI mean-reversion strategy over 10
    years on the top-5 by market cap (verified live). This helper recovers
    the three directives the screener misses, deterministically (pure regex
    — no LLM call, hermetic-testable):

      - ``symbols``: explicit tickers in the query (AAPL, NVDA, BRK.A).
        When present, the backtest should run ONLY those symbols instead of
        screening the universe.
      - ``buy_and_hold``: True when the user asked to buy and never sell —
        translated by the API into buy-on-first-bar / never-sell rules.
      - ``start_date`` / ``end_date``: a "last N days/weeks/months/years"
        window as YYYY-MM-DD; both None when no window is stated.

    Args:
        query: The raw backtest query.

    Returns:
        Dict with ``symbols``, ``buy_and_hold``, ``start_date``, ``end_date``.
    """
    raw = str(query or "")
    raw_lower = raw.lower()
    window = _extract_date_window(raw_lower)
    return {
        "symbols": _extract_tickers(raw),
        "buy_and_hold": bool(
            re.search(
                r"\b(buy\s*(?:and|&)\s*hold|buy\s+and\s+never\s+sell|"
                r"long\s*term\s*hold|hold\s*forever)\b",
                raw_lower,
            )
        ),
        "start_date": window[0] if window else None,
        "end_date": window[1] if window else None,
    }


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize a raw yfinance frame to the simulator's price basis.

    The simulator trades split/dividend-ADJUSTED prices (a 2:1 split must
    never look like a -50% crash). ``fetch_historical_data`` uses
    auto_adjust=False so raw Close AND Adj Close coexist in one download
    (one API call — cost discipline). This helper converts raw OHLC to
    adjusted via the per-day factor ``Adj Close / Close``, keeps the raw
    close for price-only-return reporting, and renames Dividends /
    Stock Splits to lowercase sim columns.

    Already-normalized frames (SQLite rebuilds carrying ``raw_close``) and
    minimal OHLCV test fixtures pass through unchanged with the optional
    columns defaulted, so callers can always read them.

    Args:
        df: Raw yfinance frame (auto_adjust=False) or a normalized frame.

    Returns:
        A copy with adjusted OHLC, ``raw_close``, ``dividends``,
        ``stock_splits`` columns.
    """
    df = df.copy()
    if "Adj Close" in df.columns and "Close" in df.columns:
        raw_close = df["Close"].astype(float)
        adj_close = df["Adj Close"].astype(float)
        # Guard divide-by-zero (a zero/NaN raw close → NaN ratio, ffill).
        ratio = adj_close / raw_close.replace(0.0, np.nan)
        ratio = ratio.replace([np.inf, -np.inf], np.nan).ffill().fillna(1.0)
        for col in ("Open", "High", "Low"):
            df[col] = df[col].astype(float) * ratio
        df["Close"] = adj_close
        df["raw_close"] = raw_close
        df["dividends"] = (
            df["Dividends"].astype(float) if "Dividends" in df.columns else 0.0
        )
        df["stock_splits"] = (
            df["Stock Splits"].astype(float) if "Stock Splits" in df.columns else 1.0
        )
    else:
        if "raw_close" not in df.columns:
            df["raw_close"] = df["Close"] if "Close" in df.columns else np.nan
        if "dividends" not in df.columns:
            df["dividends"] = 0.0
        if "stock_splits" not in df.columns:
            df["stock_splits"] = 1.0
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# Historical Data Fetcher
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_historical_data(
    symbol: str,
    years: int = HISTORICAL_YEARS,
    start_date: Optional[str] = None,
) -> Optional[pd.DataFrame]:
    """Download historical OHLCV data from Yahoo Finance.

    Args:
        symbol: Stock ticker symbol.
        years: Number of years of history to fetch (default 10).
        start_date: Optional explicit window start (YYYY-MM-DD). When set,
            fetch from the EARLIER of this date and the default anchor
            (today − years*365) — so a requested window that begins before
            the anchor is genuinely covered, while a later window still
            gets the full default history for indicator warm-up. The old
            code always fetched today−10y, so ``start_date=2016-01-01``
            with today in Aug 2026 silently truncated the simulated window
            to ~Aug 2016 (verified live: equity curve began 2016-08-03
            while the response reported 2016-01-01).

    Returns:
        DataFrame with columns: Open, High, Low, Close (raw), Adj Close
        (split/dividend-adjusted), Volume, plus Dividends / Stock Splits
        when present (auto_adjust=False — one download carries both the
        raw and the adjusted series, so the sim can trade adjusted while
        the result reports price-only vs total return). Index is Datetime.
        Returns None on failure.
    """
    try:
        end_date = datetime.now()
        anchor = end_date - timedelta(days=years * 365)
        if start_date:
            try:
                requested = datetime.strptime(start_date, "%Y-%m-%d")
            except ValueError:
                requested = anchor
            fetch_start = min(requested, anchor)
        else:
            fetch_start = anchor

        ticker = yf.Ticker(symbol)
        # Explicit timeout: 10 years of daily OHLCV is a large download, and
        # yfinance's implicit default is too short under load (surfaces as
        # None + "Failed to fetch historical data"). See settings.yfinance_timeout.
        # auto_adjust=False is PINNED (Tier 1/2): yfinance's auto_adjust
        # default has flipped across versions, so unpinned downloads are not
        # reproducible — Close could be raw OR adjusted depending on the
        # installed version, silently changing every backtest's prices. Pin
        # False and let _normalize_ohlcv derive the adjusted OHLCV from the
        # explicit Adj Close column — one download carries raw AND adjusted
        # prices, keeping the cost discipline (one API call per symbol).
        df = ticker.history(
            start=fetch_start,
            end=end_date,
            timeout=settings.yfinance_timeout,
            auto_adjust=False,
        )

        if df.empty:
            logger.warning("No historical data for %s", symbol)
            return None

        # Ensure required columns exist
        required = ["Open", "High", "Low", "Close", "Volume"]
        if not all(col in df.columns for col in required):
            logger.warning("Missing required columns for %s", symbol)
            return None

        logger.info(
            "Fetched %d days of historical data for %s",
            len(df),
            symbol,
        )
        return df

    except Exception as e:
        logger.error("Failed to fetch historical data for %s: %s", symbol, e)
        return None


def store_historical_data(
    db: Any,
    symbol: str,
    df: pd.DataFrame,
) -> int:
    """Store a DataFrame of historical prices into the backtest database.

    The ``db`` contract is duck-typed (SQLiteDB locally, BacktestDB in
    production): both expose ``insert_historical_prices(records)``.

    Args:
        db: Backtest storage (SQLiteDB | BacktestDB).
        symbol: Stock ticker symbol.
        df: DataFrame with OHLCV data from yfinance.

    Returns:
        Number of rows stored.
    """
    # yfinance occasionally returns NaN cells (halts, delistings, API
    # hiccups). A day without a Close is unusable for simulation — drop it.
    # Remaining NaN prices are forward-filled (last known value) and NaN
    # volume becomes 0, so one bad bar can't crash the store (``int(NaN)``
    # raises ValueError — verified) or poison later simulations with NULL
    # prices. Use .ffill() (fillna(method=...) is removed in pandas 3).
    # Normalize FIRST so close is the adjusted price and raw_close is kept
    # for the price-only-vs-total-return report (Tier 2).
    df = _normalize_ohlcv(df)
    # Same inf defense as run_backtest: an infinite price must never reach
    # SQLite (it would later poison an equity curve). Treat inf like NaN.
    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.dropna(subset=["Close"])
    for col in ("Open", "High", "Low"):
        df[col] = df[col].ffill().fillna(0.0)
    df["Volume"] = df["Volume"].fillna(0.0)
    # NaN raw closes fall back to the adjusted close; NaN dividends/splits
    # become 0.0/1.0 so SQLite never stores NULLs a later consumer trips on.
    df["raw_close"] = df["raw_close"].ffill().fillna(df["Close"])
    df["dividends"] = df["dividends"].fillna(0.0)
    df["stock_splits"] = df["stock_splits"].fillna(1.0)

    records: List[Tuple[str, str, float, float, float, float, float, int, float, float]] = []

    for idx, row in df.iterrows():
        date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
        records.append((
            symbol,
            date_str,
            float(row["Open"]),
            float(row["High"]),
            float(row["Low"]),
            float(row["Close"]),
            float(row["raw_close"]),
            int(row["Volume"]),
            float(row["dividends"]),
            float(row["stock_splits"]),
        ))

    return db.insert_historical_prices(records)


# ═══════════════════════════════════════════════════════════════════════════════
# Simulation Engine
# ═══════════════════════════════════════════════════════════════════════════════

def _apply_rule(rule: str, row: pd.Series) -> bool:
    """Evaluate a single rule string against a row of price data.

    Handles two forms:
      - "<field> <op> <constant>"    e.g. "rsi < 30"
      - "<field> <op> <field>"       e.g. "price > sma_50"

    Args:
        rule: A condition like "rsi < 30" or "price > sma_50".
        row: A single row of OHLCV data with calculated indicators.

    Returns:
        True if the rule condition is met.
    """
    try:
        # Parse the rule: "<field> <op> <value_or_field>"
        parts = rule.split()
        if len(parts) != 3:
            return False

        field, op, rhs = parts

        # Resolve field name to DataFrame column
        field_map: Dict[str, str] = {
            "price": "Close",
            "rsi": "RSI",
            "volume": "Volume",
            "sma_30": "SMA_30",
            "sma_50": "SMA_50",
            "sma_70": "SMA_70",
            "sma_200": "SMA_200",
            "pe": "Close",  # Simplified: using price as PE proxy
        }

        lhs_col = field_map.get(field, field)
        if lhs_col not in row.index:
            return False

        lhs_value = float(row[lhs_col])

        # Try to parse RHS as a constant; if it fails, treat it as a field name
        try:
            rhs_value = float(rhs)
        except ValueError:
            rhs_col = field_map.get(rhs, rhs)
            if rhs_col not in row.index:
                return False
            rhs_value = float(row[rhs_col])

        if op == "<":
            return lhs_value < rhs_value
        elif op == ">":
            return lhs_value > rhs_value
        elif op == "<=":
            return lhs_value <= rhs_value
        elif op == ">=":
            return lhs_value >= rhs_value
        elif op == "==":
            return abs(lhs_value - rhs_value) < 0.001

        return False
    except (ValueError, KeyError, IndexError) as e:
        logger.debug("Rule evaluation error: %s for rule '%s'", e, rule)
        return False


def _calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate technical indicators used by strategy rules.

    Adds columns: RSI, SMA_30, SMA_50, SMA_70, SMA_200.

    Args:
        df: DataFrame with at least a 'Close' column.

    Returns:
        DataFrame with additional indicator columns.
    """
    result = df.copy()

    # RSI (14-day, Wilder's smoothing). Uses an EMA (alpha=1/14) instead of a
    # simple SMA. The SMA version hit ``avg_loss == 0`` on any 14+ day up-streak,
    # turned rs into NaN (not inf), and left RSI undefined exactly at price
    # peaks — the sell-rule evaluation treated NaN as "no signal", so the
    # strategy bought high (after the peak, RSI still falling through 30) and
    # sold low (after the trough) → systematically inverted trades.
    delta = result["Close"].diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
    # avg_loss == 0 (pure up-streak) → rs = inf → RSI = 100 (correct
    # overbought reading), not NaN. 0/0 (dead-flat prices) stays NaN → no
    # signal, which is the right call for a flat tape.
    result["RSI"] = (100 - (100 / (1 + rs))).replace([np.inf, -np.inf], 100.0)

    # Simple Moving Averages. Multiple windows so the Tier 3 parameter
    # sweep (and richer rules later) can trade against any of them — each
    # window is one cheap vectorized rolling mean.
    for window in (30, 50, 70, 200):
        result[f"SMA_{window}"] = result["Close"].rolling(window=window).mean()

    return result


def _downsample_equity_curve(
    dates: List[str],
    values: List[float],
    max_points: int = MAX_EQUITY_CURVE_POINTS,
) -> List[Dict[str, Any]]:
    """Downsample a daily equity curve for a compact, chartable response.

    Keeps at most ``max_points`` evenly spaced points but ALWAYS includes
    the final point so the chart's last value equals ``final_value`` (a
    truncated curve would visually contradict the reported stats).

    Args:
        dates: Bar dates (YYYY-MM-DD), same length as values.
        values: Portfolio values at each bar's close.
        max_points: Target maximum number of points.

    Returns:
        List of {"date": ..., "value": ...} dicts (values rounded to 2dp).
    """
    n = len(values)
    if n <= max_points:
        return [
            {"date": d, "value": round(float(v), 2)}
            for d, v in zip(dates, values)
        ]
    step = n / max_points
    indices = [int(i * step) for i in range(max_points)]
    if indices[-1] != n - 1:
        indices.append(n - 1)
    return [
        {"date": dates[i], "value": round(float(values[i]), 2)}
        for i in indices
    ]


def compute_benchmark_metrics(
    strategy_series: pd.Series,
    benchmark_series: Optional[pd.Series],
    strategy_total_return: float,
) -> Dict[str, Optional[float]]:
    """Compare a strategy's equity curve against a benchmark (default SPY).

    Computes the trio every serious backtesting platform shows:

      - benchmark_total_return: buy-and-hold return of the benchmark over
        the same window (last/first close - 1).
      - excess_return: strategy total return minus the benchmark's.
      - beta: sensitivity of strategy daily returns to benchmark daily
        returns (cov / var of the aligned daily returns).
      - alpha: annualized Jensen's alpha — the strategy's risk-adjusted
        edge over the benchmark (mean(strategy daily ret) - beta *
        mean(benchmark daily ret), annualized by * 252).

    Args:
        strategy_series: Full-resolution portfolio value series
            (DatetimeIndex, one row per bar).
        benchmark_series: Benchmark Close series (DatetimeIndex). None
            disables the comparison entirely.
        strategy_total_return: The strategy's total return (decimal).

    Returns:
        Dict with benchmark_total_return, alpha, beta, excess_return —
        any that cannot be computed are None (never raises).
    """
    metrics: Dict[str, Optional[float]] = {
        "benchmark_total_return": None,
        "alpha": None,
        "beta": None,
        "excess_return": None,
    }
    if benchmark_series is None or len(benchmark_series) < 2:
        return metrics

    benchmark_total_return = float(
        benchmark_series.iloc[-1] / benchmark_series.iloc[0] - 1.0
    )
    metrics["benchmark_total_return"] = benchmark_total_return
    metrics["excess_return"] = strategy_total_return - benchmark_total_return

    strat_ret = strategy_series.pct_change().dropna()
    bench_ret = benchmark_series.pct_change().dropna()
    # Align on common trading days (inner join) so holidays that only one
    # series traded don't drag the regression.
    aligned = pd.concat([strat_ret, bench_ret], axis=1, join="inner").dropna()
    if len(aligned) < 2:
        return metrics

    bench_var = float(aligned.iloc[:, 1].var())
    if bench_var <= 0:
        return metrics
    beta = float(aligned.iloc[:, 0].cov(aligned.iloc[:, 1]) / bench_var)
    alpha_daily = float(
        aligned.iloc[:, 0].mean() - beta * aligned.iloc[:, 1].mean()
    )
    metrics["beta"] = beta
    metrics["alpha"] = alpha_daily * 252  # annualize

    return metrics


def run_backtest(
    symbol: str,
    strategy_rules: Dict[str, Any],
    start_date: str,
    end_date: str,
    df: Optional[pd.DataFrame] = None,
    benchmark_series: Optional[pd.Series] = None,
    signal_start_date: Optional[str] = None,
    compute_robustness: bool = True,
) -> BacktestResult:
    """Execute a complete backtest simulation for a single symbol.

    Signals are evaluated on a bar's CLOSE but filled at the NEXT bar's
    OPEN (no look-ahead: you can only act on a close after the market has
    closed). Open positions are force-liquidated at the final close.

    Args:
        symbol: Stock ticker symbol.
        strategy_rules: Dict with buy_rules, sell_rules, fee_rate, slippage,
                       initial_capital.
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD).
        df: Optional pre-fetched DataFrame. If None, fetches from yfinance.
        benchmark_series: Optional benchmark Close series (DatetimeIndex)
            used for the SPY comparison (alpha/beta/excess). None skips it.
        signal_start_date: Optional YYYY-MM-DD. Bars BEFORE this date are
            warm-up only — indicators are computed on them (the df slice
            must include them) but no orders fire and no portfolio value is
            recorded. Used by walk-forward analysis so out-of-sample runs
            are not polluted by indicator warm-up.
        compute_robustness: Run Monte Carlo + bootstrap CI (default True).
            Inner calls from walk-forward / parameter sensitivity pass
            False — they discard those numbers, so computing them would be
            pure waste (cost discipline).

    Returns:
        BacktestResult with all performance metrics, including the
        downsampled ``drawdown_curve`` (negative decimals over the same
        dates as ``equity_curve``; empty when no trades occurred) so a
        frontend can chart the drawdown alongside the equity curve.
    """
    # Fetch data if not provided
    if df is None:
        df = fetch_historical_data(symbol)
        if df is None:
            return BacktestResult(
                symbol=symbol,
                strategy_params=strategy_rules,
                start_date=start_date,
                end_date=end_date,
                initial_capital=strategy_rules.get("initial_capital", DEFAULT_INITIAL_CAPITAL),
                final_value=0,
                total_return=-1.0,
            )

    # Validate required OHLCV columns BEFORE touching the DataFrame: a
    # caller-supplied df missing 'Close' (corrupted cache, truncated
    # download) previously raised an unhandled KeyError instead of the
    # graceful zero-result every other bad input gets.
    required_cols = {"Open", "High", "Low", "Close", "Volume"}
    if not required_cols.issubset(df.columns):
        logger.warning(
            "Missing required OHLCV columns for %s (got %s)",
            symbol,
            list(df.columns),
        )
        return BacktestResult(
            symbol=symbol,
            strategy_params=strategy_rules,
            start_date=start_date,
            end_date=end_date,
            initial_capital=strategy_rules.get("initial_capital", DEFAULT_INITIAL_CAPITAL),
            final_value=0,
            total_return=-1.0,
        )

    # Slice to date range
    df = df.loc[start_date:end_date].copy()
    if df.empty:
        logger.warning("No data in range %s to %s for %s", start_date, end_date, symbol)
        return BacktestResult(
            symbol=symbol,
            strategy_params=strategy_rules,
            start_date=start_date,
            end_date=end_date,
            initial_capital=strategy_rules.get("initial_capital", DEFAULT_INITIAL_CAPITAL),
            final_value=0,
            total_return=-1.0,
        )

    # Defensive: drop rows whose Close is NaN. SQLite-rebuilt frames can
    # carry NULL closes if dirty data was stored, and a NaN Close would
    # corrupt portfolio values (a NaN buy signal is already filtered by
    # _apply_rule, but a NaN price must not reach the equity math).
    # Raw data availability (Tier 2): live yfinance frames carry Adj Close +
    # raw Close (auto_adjust=False); SQLite rebuilds carry raw_close. Capture
    # BEFORE normalization so the buy-and-hold breakdown is only reported
    # when genuine raw prices exist (a bare OHLCV test frame has none).
    _raw_available = ("Adj Close" in df.columns) or ("raw_close" in df.columns)
    # Normalize: sim trades split/dividend-ADJUSTED prices; keep the raw
    # close for price-only-return reporting.
    df = _normalize_ohlcv(df)
    # Dirty-data defense: dropna() removes NaN closes but NOT ±inf — a
    # corrupted row with an infinite price would poison the equity curve
    # (cash + shares*inf = inf) and make the API response un-serializable
    # (Starlette JSONResponse uses allow_nan=False → 500). Treat inf like
    # NaN so the row is dropped.
    df = df.replace([np.inf, -np.inf], np.nan)

    df = df.dropna(subset=["Close"])
    # Same OHLC cleaning as the store path: next-bar-open fills read
    # row["Open"], so a NaN Open here would poison the fill price.
    # Forward-fill prices and zero-fill volume before simulating.
    for col in ("Open", "High", "Low"):
        df[col] = df[col].ffill().fillna(0.0)
    df["Volume"] = df["Volume"].fillna(0.0)
    # Trading calendar (Tier 2): only Mon–Fri are tradeable. A Saturday or
    # holiday row silently simulated as tradable would inflate results.
    if hasattr(df.index, "dayofweek"):
        df = df[df.index.dayofweek < 5]
    if df.empty:
        logger.warning("No usable Close data in range %s to %s for %s", start_date, end_date, symbol)
        return BacktestResult(
            symbol=symbol,
            strategy_params=strategy_rules,
            start_date=start_date,
            end_date=end_date,
            initial_capital=strategy_rules.get("initial_capital", DEFAULT_INITIAL_CAPITAL),
            final_value=0,
            total_return=-1.0,
        )

    # Calculate indicators
    df = _calculate_indicators(df)

    # Extract parameters
    initial_capital = strategy_rules.get("initial_capital", DEFAULT_INITIAL_CAPITAL)
    fee_rate = strategy_rules.get("fee_rate", DEFAULT_FEE_RATE)
    slippage = strategy_rules.get("slippage", DEFAULT_SLIPPAGE)
    slippage_cents = strategy_rules.get("slippage_cents", DEFAULT_SLIPPAGE_CENTS)
    max_order_volume_pct = strategy_rules.get("max_order_volume_pct", 0.0)
    buy_rules = strategy_rules.get("buy_rules", ["rsi < 30"])
    sell_rules = strategy_rules.get("sell_rules", ["rsi > 70"])
    stop_loss = strategy_rules.get("stop_loss")
    take_profit = strategy_rules.get("take_profit")
    trailing_stop = strategy_rules.get("trailing_stop")
    position_sizing = strategy_rules.get("position_sizing", "all_in")
    position_pct = strategy_rules.get("position_pct", 1.0)
    risk_per_trade = strategy_rules.get("risk_per_trade", 0.01)
    allow_short = bool(strategy_rules.get("allow_short", False))
    max_leverage = float(strategy_rules.get("max_leverage", 1.0))
    # Tier 5 opt-in cash accounting. Default (all off) = the validated
    # adjusted-price total-return sim, byte-identical to before. When
    # dividend accrual/reinvest is enabled the sim switches to RAW prices
    # (dividends booked as cash, so marking adjusted would double-count
    # the embedded dividend) — a real cash-flow model. Borrow costs apply
    # on the active mark basis whenever a short is held.
    dividend_accrual = bool(strategy_rules.get("dividend_accrual", False))
    dividend_reinvest = bool(strategy_rules.get("dividend_reinvest", False))
    borrow_rate = float(strategy_rules.get("borrow_rate", 0.0) or 0.0)
    dividend_mode = dividend_accrual or dividend_reinvest

    def _buy_fill(price: float) -> float:
        """Long-entry / short-cover fill: price + slippage rate + cents."""
        return price * (1 + slippage) + slippage_cents

    def _sell_fill(price: float) -> float:
        """Long-exit / short-entry fill: price − slippage rate − cents."""
        return price * (1 - slippage) - slippage_cents

    def _position_budget(equity: float) -> float:
        """Dollar exposure budget for a new position, per the sizing model.

        - all_in: 100% of equity (fee-inclusive sizing never over-spends).
        - fixed_pct: ``position_pct`` of equity.
        - risk_based: risk ``risk_per_trade`` of equity per trade, divided by
          the stop distance — the classic fixed-fractional formula. Falls
          back to all-in when no stop-loss is set (undefined without it).
        """
        if position_sizing == "fixed_pct":
            return equity * float(position_pct)
        if position_sizing == "risk_based" and stop_loss and stop_loss > 0:
            return equity * float(risk_per_trade) / stop_loss
        return equity

    def _liquidity_cap(bar_volume: float) -> int:
        """Max shares that keep the order under the liquidity cap."""
        if not max_order_volume_pct:
            return 2**31 - 1  # unlimited
        return int(float(bar_volume) * max_order_volume_pct)

    # Tier 5: raw-price basis for dividend mode. The adjusted frame embeds
    # dividends in price; to book them as cash (income view / DRIP) the sim
    # must mark and fill on RAW prices. Recover raw OHLC from the adjusted
    # frame via the per-bar adjustment ratio (adj = raw × adj_close/raw_close
    # for the WHOLE bar — the factor is uniform across OHLC, so raw =
    # adjusted × raw_close/adj_close is exact). Degrades to adjusted prices
    # (no dividend accrual) when raw prices are unavailable, with a note.
    dividend_mode_active = False
    if dividend_mode and "raw_close" in df.columns:
        with np.errstate(divide="ignore", invalid="ignore"):
            _ratio = df["raw_close"] / df["Close"].replace(0.0, np.nan)
        _ratio = _ratio.replace([np.inf, -np.inf], np.nan).ffill().fillna(1.0)
        df["_raw_open"] = df["Open"] * _ratio
        df["_raw_high"] = df["High"] * _ratio
        df["_raw_low"] = df["Low"] * _ratio
        df["_raw_close"] = df["raw_close"]
        dividend_mode_active = True
    elif dividend_mode:
        logger.warning(
            "Dividend mode requested for %s but raw prices unavailable — "
            "falling back to adjusted prices (no cash dividend accrual)",
            symbol,
        )

    # ── Simulate (next-bar-open execution, Tier 1/2) ─────────────────────
    # Signals are decided at a bar's CLOSE and filled at the NEXT bar's
    # OPEN — the industry default, because a close is only knowable after
    # the market has closed. Intra-bar protective stops (stop-loss /
    # take-profit / trailing) execute on the bar where they are hit — that
    # is their entire purpose (Tier 2 realism).
    cash = initial_capital
    shares = 0  # negative = short position (Tier 2)
    trades: List[Trade] = []
    open_trade: Optional[Trade] = None
    portfolio_values: List[float] = []
    portfolio_dates: List[str] = []
    pending_order: Optional[str] = None  # "buy" / "sell" decided last close
    trail_extreme: float = 0.0  # running extreme since entry (high=long, low=short)
    # Tier 5 cash accounting accumulators (reported on the result).
    dividends_received: float = 0.0
    borrow_fees_paid: float = 0.0
    # DRIP: dividend cash earned on an ex-date, deployed into the open
    # position at the NEXT bar's open (merged via weighted-average cost).
    pending_reinvest_cash: float = 0.0

    for idx, row in df.iterrows():
        # Walk-forward warm-up (Tier 3): bars before signal_start_date feed
        # the indicators but never trade or record portfolio value.
        if signal_start_date is not None and str(idx)[:10] < signal_start_date:
            continue
        bar_open = float(row["_raw_open"] if dividend_mode_active else row["Open"])
        bar_high = float(row["_raw_high"] if dividend_mode_active else row["High"])
        bar_low = float(row["_raw_low"] if dividend_mode_active else row["Low"])
        bar_volume = float(row["Volume"])

        # Tier 5 DRIP: deploy dividend cash earned yesterday into the open
        # position at today's open (weighted-average cost, so the trade's
        # P&L stays exact). No position open → the cash simply rolls.
        if pending_reinvest_cash > 0 and open_trade is not None and shares > 0:
            _fill = _buy_fill(bar_open)
            _n = int(pending_reinvest_cash / (_fill * (1 + fee_rate)))
            if _n > 0:
                _cost = _n * _fill * (1 + fee_rate)
                _old_cost = shares * open_trade.buy_price
                cash -= _cost
                pending_reinvest_cash -= _cost
                shares += _n
                open_trade.shares = shares
                open_trade.buy_price = (_old_cost + _cost) / shares
                trail_extreme = max(trail_extreme, _fill)

        # 1) Execute the order decided at the PREVIOUS close, at THIS open.
        if pending_order == "buy" and shares < 0 and open_trade is not None:
            # Cover a short (buy to close).
            fill = _buy_fill(bar_open)
            cover_cost = (-shares) * fill * (1 + fee_rate)
            cash -= cover_cost
            open_trade.sell_date = str(idx)[:10]
            open_trade.sell_price = fill
            # Fee-inclusive net P&L (BOTH legs), matching the cash flow:
            # entry proceeds = shares*entry*(1-fee), exit cost = shares*exit*(1+fee).
            open_trade.pnl = (-shares) * open_trade.buy_price * (1 - fee_rate) - (-shares) * fill * (1 + fee_rate)
            open_trade.pnl_pct = 1 - (fill * (1 + fee_rate)) / (open_trade.buy_price * (1 - fee_rate))
            open_trade.is_open = False
            trades.append(open_trade)
            open_trade = None
            shares = 0
        elif pending_order == "buy" and shares == 0 and cash > 0:
            # Open a LONG. Fee-inclusive sizing: cost = shares * price *
            # (1 + fee) can never exceed cash because the fee sits in the
            # denominator; the leverage cap never lends for longs (budget
            # is capped at equity).
            fill = _buy_fill(bar_open)
            budget = min(_position_budget(cash), cash * max_leverage)
            shares_to_buy = int(budget / (fill * (1 + fee_rate)))
            shares_to_buy = min(shares_to_buy, _liquidity_cap(bar_volume))
            cost = shares_to_buy * fill * (1 + fee_rate)
            if cost <= cash and shares_to_buy > 0:
                shares = shares_to_buy
                cash -= cost
                open_trade = Trade(
                    symbol=symbol,
                    buy_date=str(idx)[:10],
                    buy_price=fill,
                    shares=shares_to_buy,
                    side="long",
                )
                trail_extreme = fill
        elif pending_order == "sell" and shares > 0 and open_trade is not None:
            # Close a LONG.
            fill = _sell_fill(bar_open)
            proceeds = shares * fill * (1 - fee_rate)
            cash += proceeds
            open_trade.sell_date = str(idx)[:10]
            open_trade.sell_price = fill
            # Fee-inclusive net P&L: entry cost = shares*entry*(1+fee), exit
            # proceeds = shares*exit*(1-fee) — matches the cash flow exactly.
            open_trade.pnl = proceeds - (shares * open_trade.buy_price * (1 + fee_rate))
            open_trade.pnl_pct = (proceeds / (shares * open_trade.buy_price * (1 + fee_rate))) - 1
            open_trade.is_open = False
            trades.append(open_trade)
            open_trade = None
            shares = 0
        elif pending_order == "sell" and shares == 0 and allow_short and cash > 0:
            # Open a SHORT (sell first, cover later) — Tier 2 realism. The
            # proceeds sit in cash; exposure is capped by the sizing model
            # AND the leverage cap so a short can never exceed it.
            fill = _sell_fill(bar_open)
            budget = min(_position_budget(cash), cash * max_leverage)
            shares_to_short = int(budget / fill)
            shares_to_short = min(shares_to_short, _liquidity_cap(bar_volume))
            if shares_to_short > 0:
                proceeds = shares_to_short * fill * (1 - fee_rate)
                shares = -shares_to_short
                cash += proceeds
                open_trade = Trade(
                    symbol=symbol,
                    buy_date=str(idx)[:10],
                    buy_price=fill,
                    shares=shares_to_short,
                    side="short",
                )
                trail_extreme = fill
        pending_order = None

        # 2) Intra-bar protective stops (only while a position is open).
        # Stops trigger on the bar where the level is touched; when several
        # trigger in the same bar, the WORST case (stop-loss first) wins —
        # the conservative convention shared by professional engines.
        exit_fill: Optional[float] = None
        exit_reason: str = "signal"
        if open_trade is not None and shares != 0:
            entry = open_trade.buy_price
            if shares > 0:
                # Long: stops trigger on the LOW, targets on the HIGH.
                trail_extreme = max(trail_extreme, bar_high)
                stop_level = entry * (1 - stop_loss) if stop_loss else None
                trail_level = trail_extreme * (1 - trailing_stop) if trailing_stop else None
                tp_level = entry * (1 + take_profit) if take_profit else None
                if stop_level is not None and bar_low <= stop_level:
                    # Gap through the stop → fill at the (worse) open.
                    exit_fill, exit_reason = min(bar_open, stop_level), "stop_loss"
                elif trail_level is not None and bar_low <= trail_level:
                    exit_fill, exit_reason = min(bar_open, trail_level), "trailing_stop"
                elif tp_level is not None and bar_high >= tp_level:
                    exit_fill, exit_reason = max(bar_open, tp_level), "take_profit"
            else:
                # Short: stops trigger on the HIGH, targets on the LOW.
                trail_extreme = min(trail_extreme, bar_low)
                stop_level = entry * (1 + stop_loss) if stop_loss else None
                trail_level = trail_extreme * (1 + trailing_stop) if trailing_stop else None
                tp_level = entry * (1 - take_profit) if take_profit else None
                if stop_level is not None and bar_high >= stop_level:
                    exit_fill, exit_reason = max(bar_open, stop_level), "stop_loss"
                elif trail_level is not None and bar_high >= trail_level:
                    exit_fill, exit_reason = max(bar_open, trail_level), "trailing_stop"
                elif tp_level is not None and bar_low <= tp_level:
                    exit_fill, exit_reason = min(bar_open, tp_level), "take_profit"
            if exit_fill is not None:
                if shares > 0:
                    proceeds = shares * exit_fill * (1 - fee_rate)
                    cash += proceeds
                else:
                    cover_cost = (-shares) * exit_fill * (1 + fee_rate)
                    cash -= cover_cost
                open_trade.sell_date = str(idx)[:10]
                open_trade.sell_price = exit_fill
                if shares > 0:
                    open_trade.pnl = proceeds - (shares * open_trade.buy_price * (1 + fee_rate))
                    open_trade.pnl_pct = (proceeds / (shares * open_trade.buy_price * (1 + fee_rate))) - 1
                else:
                    open_trade.pnl = (-shares) * open_trade.buy_price * (1 - fee_rate) - (-shares) * exit_fill * (1 + fee_rate)
                    open_trade.pnl_pct = 1 - (exit_fill * (1 + fee_rate)) / (open_trade.buy_price * (1 - fee_rate))
                open_trade.is_open = False
                open_trade.exit_reason = exit_reason
                trades.append(open_trade)
                open_trade = None
                shares = 0

        current_price = float(row["_raw_close"] if dividend_mode_active else row["Close"])

        # Tier 5: cash dividends on ex-dates (paid to the holder). Longs
        # receive cash; shorts PAY the dividend (they owe it to the lender).
        # Accumulated so the DRIP at the next open can deploy it. Gated on
        # dividend_mode_active: with all flags OFF the sim must stay
        # byte-identical to the pre-Tier-5 adjusted-price model — a frame
        # that merely CARRIES a dividends column (live yfinance downloads
        # always do) must not silently switch the default run to cash
        # accounting (double-counts the embedded dividend).
        _div = float(row.get("dividends", 0.0) or 0.0)
        if dividend_mode_active and _div != 0.0 and shares != 0:
            _div_cash = abs(shares) * _div
            if shares > 0:
                cash += _div_cash
                dividends_received += _div_cash
                if dividend_reinvest:
                    pending_reinvest_cash += _div_cash
            else:
                cash -= _div_cash
                dividends_received -= _div_cash

        # Tier 5: daily short borrow cost (annualized rate on mark value).
        if shares < 0 and borrow_rate > 0:
            _daily = abs(shares) * current_price * borrow_rate / 252.0
            cash -= _daily
            borrow_fees_paid += _daily

        # 3) Evaluate signals at THIS close — filled at the next bar's open.
        if shares == 0 and cash > 0:
            should_buy = all(_apply_rule(rule, row) for rule in buy_rules)
            should_short = allow_short and all(
                _apply_rule(rule, row) for rule in sell_rules
            )
            if should_buy:
                pending_order = "buy"
            elif should_short:
                pending_order = "sell"
        elif shares > 0 and open_trade is not None:
            should_sell = all(_apply_rule(rule, row) for rule in sell_rules)
            if should_sell:
                pending_order = "sell"
        elif shares < 0 and open_trade is not None:
            should_cover = all(_apply_rule(rule, row) for rule in buy_rules)
            if should_cover:
                pending_order = "buy"

        # 4) Track portfolio value at this close (equity curve / metrics).
        portfolio_values.append(cash + shares * current_price)
        portfolio_dates.append(str(idx)[:10])

    # Close any open position at the end (long or short), at the final close.
    if open_trade is not None and shares != 0:
        final_price = float(
            df.iloc[-1]["_raw_close"] if dividend_mode_active else df.iloc[-1]["Close"]
        )
        if shares > 0:
            exit_price = _sell_fill(final_price)
            proceeds = shares * exit_price * (1 - fee_rate)
            cash += proceeds
            open_trade.pnl = proceeds - (shares * open_trade.buy_price * (1 + fee_rate))
            open_trade.pnl_pct = (proceeds / (shares * open_trade.buy_price * (1 + fee_rate))) - 1
        else:
            exit_price = _buy_fill(final_price)
            cover_cost = (-shares) * exit_price * (1 + fee_rate)
            cash -= cover_cost
            open_trade.pnl = (-shares) * open_trade.buy_price * (1 - fee_rate) - (-shares) * exit_price * (1 + fee_rate)
            open_trade.pnl_pct = 1 - (exit_price * (1 + fee_rate)) / (open_trade.buy_price * (1 - fee_rate))
        open_trade.sell_date = str(df.index[-1])[:10]
        open_trade.sell_price = exit_price
        open_trade.is_open = False
        open_trade.exit_reason = "end_of_period"
        trades.append(open_trade)

    final_value = cash

    # The last recorded portfolio value is the mark-to-market at the final
    # bar's CLOSE (cash + shares * close, before any end-of-period
    # liquidation). If a position was still open, the forced liquidation
    # above charged slippage + fee, so the curve's last point would differ
    # from final_value — a chart that ends anywhere but the reported final
    # value looks like a bug. Replace it with the true final value.
    if portfolio_values:
        portfolio_values[-1] = final_value

    # Tier 5: keep the FULL-resolution (daily) equity series on the result
    # for the portfolio aggregator. The downsampled equity_curve below is
    # for charting; the portfolio needs daily granularity to combine
    # symbols and apply rebalance schedules without losing fidelity.
    full_equity_curve = [
        {"date": d, "value": v}
        for d, v in zip(portfolio_dates, portfolio_values)
    ]

    # ── Calculate Metrics ───────────────────────────────────────────────
    portfolio_series = pd.Series(portfolio_values)

    total_return = (final_value - initial_capital) / initial_capital

    # ── Tier 2: buy-and-hold breakdown (price-only vs total return) ─────
    # Adjusted prices embed dividends, so the sim's return is a TOTAL
    # return. Compare against the raw close to isolate how much of the
    # move was dividend yield vs capital appreciation. Only reported when
    # genuine raw prices were available.
    buy_hold_price_return: Optional[float] = None
    buy_hold_total_return: Optional[float] = None
    dividend_contribution: Optional[float] = None
    data_quality: List[str] = [
        (
            "Cash-dividend accounting: raw prices used for fills and marks; "
            "dividends booked as cash on ex-dates."
            if dividend_mode_active
            else "Split/dividend-adjusted prices used for fills and marks."
        )
    ]
    if borrow_rate > 0:
        data_quality.append(
            f"Short borrow cost charged at {borrow_rate * 100:.2f}% annualized "
            "on the mark value of open short positions."
        )
    if _raw_available and len(df) >= 2:
        raw_first_raw = df["raw_close"].iloc[0]
        raw_last_raw = df["raw_close"].iloc[-1]
        adj_first = float(df["Close"].iloc[0])
        adj_last = float(df["Close"].iloc[-1])
        # Guard migrated DBs: pre-Tier-2 rows get raw_close=NULL after the
        # ALTER TABLE migration, so the column exists but holds None — skip
        # the breakdown rather than crash on float(None).
        if (
            pd.notna(raw_first_raw)
            and pd.notna(raw_last_raw)
            and raw_first_raw > 0
            and adj_first > 0
        ):
            buy_hold_price_return = float(raw_last_raw) / float(raw_first_raw) - 1.0
            buy_hold_total_return = adj_last / adj_first - 1.0
            dividend_contribution = buy_hold_total_return - buy_hold_price_return
        else:
            data_quality.append(
                "Raw price data unavailable — price-only vs total-return "
                "breakdown not reported."
            )
    else:
        data_quality.append(
            "Raw price data unavailable — price-only vs total-return "
            "breakdown not reported."
        )
    if position_sizing == "risk_based" and not stop_loss:
        data_quality.append(
            "risk_based sizing requested without a stop-loss — fell back to "
            "all-in sizing."
        )

    # Sharpe Ratio (annualized, assuming 252 trading days)
    if len(portfolio_series) > 1:
        daily_returns = portfolio_series.pct_change().dropna()
        if not daily_returns.empty and daily_returns.std() > 0:
            sharpe_ratio = float(
                (daily_returns.mean() / daily_returns.std()) * np.sqrt(252)
            )
        else:
            sharpe_ratio = None
    else:
        sharpe_ratio = None

    # Max Drawdown. Only meaningful when trades actually happened: a flat,
    # never-traded tape reports max_drawdown=0.0 from the formula (no peak
    # to fall from), which misleadingly reads as "zero risk taken". Report
    # None instead so callers can distinguish 'untested' from 'no loss'.
    # Also downsample the FULL drawdown series for charting (Tier 4) — the
    # same date basis as the equity curve, so the two charts line up.
    drawdown_curve: List[Dict[str, Any]] = []
    if len(portfolio_series) > 1 and trades:
        peak = portfolio_series.expanding().max()
        drawdown = (portfolio_series - peak) / peak
        max_drawdown = float(drawdown.min()) if not drawdown.empty else None
        drawdown_curve = _downsample_equity_curve(
            portfolio_dates,
            [float(v) for v in drawdown],
            MAX_EQUITY_CURVE_POINTS,
        )
    else:
        max_drawdown = None

    # Win Rate
    closed_trades = [t for t in trades if not t.is_open and t.pnl is not None]
    if closed_trades:
        winning_trades = [t for t in closed_trades if t.pnl > 0]
        win_rate = len(winning_trades) / len(closed_trades)
    else:
        win_rate = None

    # ── Tier 1: confidence warning + benchmark comparison ────────────────
    # Low trade counts make Sharpe/drawdown/win-rate statistical noise —
    # flag it instead of presenting noise as signal.
    total_trades = len(trades)
    warnings: List[str] = []
    if total_trades == 0:
        warnings.append(
            "No trades were triggered over the backtest period — the rules "
            "never produced a valid entry signal."
        )
    elif total_trades < MIN_TRADES_FOR_CONFIDENCE:
        warnings.append(
            f"Only {total_trades} trades over the backtest period — Sharpe "
            "ratio, max drawdown, and win rate have low statistical "
            f"confidence (recommend at least {MIN_TRADES_FOR_CONFIDENCE})."
        )

    # Benchmark comparison against SPY (alpha/beta/excess return).
    benchmark_metrics = compute_benchmark_metrics(
        pd.Series(portfolio_values, index=pd.DatetimeIndex(portfolio_dates)),
        benchmark_series,
        total_return,
    )

    # Equity curve (downsampled) so the API can chart the strategy.
    equity_curve = _downsample_equity_curve(
        portfolio_dates, portfolio_values, MAX_EQUITY_CURVE_POINTS
    )

    # ── Tier 3 robustness (Monte Carlo + bootstrap CI) ───────────────────
    # Cheap, deterministic compute over the trades and equity curve we
    # already have: resampling shows whether the headline return is a real
    # edge or one lucky sequence of trades. Skipped when the caller is an
    # inner walk-forward/sensitivity sweep that discards these numbers.
    monte_carlo = monte_carlo_trades(trades) if compute_robustness else None
    confidence_intervals = (
        bootstrap_confidence(portfolio_values, sharpe_ratio)
        if compute_robustness
        else None
    )

    return BacktestResult(
        symbol=symbol,
        strategy_params=strategy_rules,
        start_date=start_date,
        end_date=end_date,
        initial_capital=initial_capital,
        final_value=final_value,
        total_return=total_return,
        sharpe_ratio=sharpe_ratio,
        max_drawdown=max_drawdown,
        win_rate=win_rate,
        total_trades=total_trades,
        trades=trades,
        equity_curve=equity_curve,
        drawdown_curve=drawdown_curve,
        warnings=warnings,
        benchmark_total_return=benchmark_metrics["benchmark_total_return"],
        alpha=benchmark_metrics["alpha"],
        beta=benchmark_metrics["beta"],
        excess_return=benchmark_metrics["excess_return"],
        buy_hold_price_return=buy_hold_price_return,
        buy_hold_total_return=buy_hold_total_return,
        dividend_contribution=dividend_contribution,
        data_quality=data_quality,
        monte_carlo=monte_carlo,
        confidence_intervals=confidence_intervals,
        full_equity_curve=full_equity_curve,
        dividends_received=dividends_received,
        borrow_fees_paid=borrow_fees_paid,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Robustness Analysis (Tier 3) — prove it's not luck
# ═══════════════════════════════════════════════════════════════════════════════
# Everything here is pure compute over data the engine already holds (the
# Stratoscan way — near-zero marginal cost). Fixed seeds keep results
# reproducible: the same backtest shows the same robustness numbers every
# run, which is what an auditable product needs.

def monte_carlo_trades(
    trades: List[Trade],
    n_simulations: int = MONTE_CARLO_SIMULATIONS,
    seed: int = ROBUSTNESS_SEED,
) -> Dict[str, Any]:
    """Resample the closed trades' returns to estimate the outcome spread.

    Draws the SAME number of trades as the actual backtest (with
    replacement) and compounds each draw N times. The 5th–95th percentile
    shows how fragile the headline return is: a "+240%" that only survives
    5% of resamples is luck; one whose 5th percentile is still positive is
    a real edge.

    Args:
        trades: Closed trades from a backtest result.
        n_simulations: Number of bootstrap resamples.
        seed: RNG seed (reproducible results).

    Returns:
        Dict with p5/p25/median/p75/p95, mean, prob_positive, and the
        sample size. ``available: False`` when there are no closed trades.
    """
    closed = [t for t in trades if not t.is_open and t.pnl_pct is not None]
    if not closed:
        return {"available": False, "reason": "no closed trades"}
    returns = np.array([float(t.pnl_pct) for t in closed], dtype=float)
    rng = np.random.default_rng(seed)
    outcomes = np.empty(n_simulations, dtype=float)
    for i in range(n_simulations):
        sample = rng.choice(returns, size=len(returns), replace=True)
        outcomes[i] = float(np.prod(1.0 + sample) - 1.0)
    pcts = np.percentile(outcomes, [5, 25, 50, 75, 95])
    return {
        "available": True,
        "simulations": n_simulations,
        "trade_count": len(closed),
        "p5": float(pcts[0]),
        "p25": float(pcts[1]),
        "median": float(pcts[2]),
        "p75": float(pcts[3]),
        "p95": float(pcts[4]),
        "mean": float(outcomes.mean()),
        "prob_positive": float((outcomes > 0).mean()),
    }


def bootstrap_confidence(
    portfolio_values: List[float],
    sharpe_ratio: Optional[float],
    n_bootstraps: int = BOOTSTRAP_SIMULATIONS,
    seed: int = ROBUSTNESS_SEED,
) -> Dict[str, Any]:
    """Bootstrap confidence intervals for Sharpe ratio and max drawdown.

    Resamples the strategy's DAILY returns with replacement, reconstructs a
    synthetic equity curve per draw, and recomputes both metrics — giving
    the 5th–95th percentile range that bounds how much the headline Sharpe
    and max drawdown could move under sampling noise. Also reports the
    Lo (2002) Sharpe t-statistic (annualized Sharpe × √years), the
    significance test professional engines (e.g. PyBroker) ship.

    Args:
        portfolio_values: Daily portfolio values (full resolution).
        sharpe_ratio: The computed annualized Sharpe (for the t-stat).
        n_bootstraps: Number of resamples.
        seed: RNG seed.

    Returns:
        Dict with sharpe_ci, max_drawdown_ci, sharpe_t_stat, and a sample
        note. ``available: False`` when there's not enough return history.
    """
    daily = pd.Series(portfolio_values).pct_change().dropna()
    if len(daily) < 30 or daily.std() <= 0:
        return {"available": False, "reason": "insufficient return history"}
    rng = np.random.default_rng(seed)
    sharpe_boot: List[float] = []
    mdd_boot: List[float] = []
    vals = daily.to_numpy(dtype=float)
    for _ in range(n_bootstraps):
        sample = rng.choice(vals, size=len(vals), replace=True)
        if sample.std() <= 0:
            continue
        sharpe_boot.append(float((sample.mean() / sample.std()) * np.sqrt(252)))
        curve = np.cumprod(1.0 + sample)
        peak = np.maximum.accumulate(curve)
        mdd_boot.append(float(np.min(curve / peak - 1.0)))
    if not sharpe_boot:
        return {"available": False, "reason": "bootstrap produced no variation"}
    return {
        "available": True,
        "samples": len(sharpe_boot),
        "sharpe_ci": [
            float(np.percentile(sharpe_boot, 5)),
            float(np.percentile(sharpe_boot, 95)),
        ],
        "max_drawdown_ci": [
            float(np.percentile(mdd_boot, 5)),
            float(np.percentile(mdd_boot, 95)),
        ],
        "sharpe_t_stat": (
            float(sharpe_ratio * np.sqrt(len(daily) / 252))
            if sharpe_ratio is not None
            else None
        ),
        "sharpe_sample_note": (
            f"{len(daily)} daily observations — a Sharpe t-stat below ~2 "
            "means the edge is not statistically distinguishable from noise."
        ),
    }


def _sub_rule_threshold(rule: str, field: str, value: Any) -> str:
    """Rewrite ``'rsi < 30'`` to ``'rsi < {value}'`` (rule sweep helper).

    Only touches ``<field> <op> <number>`` rules; anything else is returned
    unchanged.
    """
    parts = rule.split()
    if len(parts) == 3 and parts[0] == field:
        return f"{parts[0]} {parts[1]} {value}"
    return rule


def _sub_sma_reference(rule: str, sma_window: int) -> str:
    """Rewrite ``'price > sma_50'`` to another SMA window (rule sweep)."""
    parts = rule.split()
    if len(parts) == 3 and parts[2].startswith("sma_"):
        return f"{parts[0]} {parts[1]} sma_{sma_window}"
    return rule


def walk_forward_analysis(
    df: pd.DataFrame,
    strategy_rules: Dict[str, Any],
    symbol: str = "WALK",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    splits: int = WALK_FORWARD_SPLITS,
) -> Dict[str, Any]:
    """In-sample vs out-of-sample performance (walk-forward robustness).

    Runs the SAME rules on (a) a 70/30 in-sample/out-of-sample split and
    (b) ``splits`` consecutive rolling validation windows across the whole
    period. Every out-of-sample run warms its indicators on data BEFORE the
    window (via ``run_backtest``'s ``signal_start_date``), so out-of-sample
    numbers are never polluted by indicator warm-up — the number that
    matters when judging a strategy.

    Args:
        df: Historical OHLCV (raw or normalized).
        strategy_rules: The rules to test (unchanged across windows).
        symbol: Symbol label for the runs.
        start_date / end_date: Optional window (defaults to full history).
        splits: Number of rolling validation windows.

    Returns:
        Dict with in_sample / out_of_sample summaries, rolling window
        results, and a consistency verdict. ``available: False`` when too
        little data exists.
    """
    norm = _normalize_ohlcv(df).dropna(subset=["Close"])
    if start_date:
        norm = norm.loc[start_date:]
    if end_date:
        norm = norm.loc[:end_date]
    if len(norm) < 60:
        return {"available": False, "reason": "not enough bars for walk-forward"}

    dates = list(norm.index)
    total = len(dates)
    # Defensive: never index past the data (pathological splits input).
    splits = max(1, min(splits, total))

    def _run(full: pd.DataFrame, start: str, end: str, signal_start: Optional[str]) -> Dict[str, Any]:
        res = run_backtest(
            symbol, strategy_rules, start, end,
            df=full, signal_start_date=signal_start,
            compute_robustness=False,
        )
        return {
            "total_return": res.total_return,
            "sharpe_ratio": res.sharpe_ratio,
            "max_drawdown": res.max_drawdown,
            "total_trades": res.total_trades,
        }

    # 70/30 split: in-sample = first 70%, out-of-sample = last 30% (warmed).
    split_idx = int(total * 0.7)
    is_end = str(dates[split_idx - 1])[:10]
    oos_start = str(dates[split_idx])[:10]
    warm_start = (pd.Timestamp(oos_start) - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
    in_sample = _run(norm, str(dates[0])[:10], is_end, None)
    out_of_sample = _run(norm, warm_start, str(dates[-1])[:10], oos_start)

    # Rolling windows: splits consecutive equal-length windows, each warmed.
    window = max(1, total // splits)
    rolling: List[Dict[str, Any]] = []
    for i in range(splits):
        w_start_idx = i * window
        w_end_idx = total - 1 if i == splits - 1 else (i + 1) * window - 1
        w_start = str(dates[w_start_idx])[:10]
        w_end = str(dates[w_end_idx])[:10]
        w_warm = (pd.Timestamp(w_start) - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
        r = _run(norm, w_warm, w_end, w_start)
        r.update({"window_start": w_start, "window_end": w_end})
        rolling.append(r)

    returns = [r["total_return"] for r in rolling]
    positive = sum(1 for r in returns if r > 0)
    if positive == len(rolling):
        verdict = "All windows positive"
    elif positive == 0:
        verdict = "All windows negative"
    else:
        verdict = f"Mixed ({positive}/{len(rolling)} windows positive)"

    return {
        "available": True,
        "splits": splits,
        "in_sample": in_sample,
        "out_of_sample": out_of_sample,
        "rolling": {
            "windows": rolling,
            "median_return": float(np.median(returns)),
            "min_return": float(np.min(returns)),
            "max_return": float(np.max(returns)),
            "positive_windows": positive,
            "total_windows": len(rolling),
            "verdict": verdict,
        },
    }


def parameter_sensitivity(
    df: pd.DataFrame,
    strategy_rules: Dict[str, Any],
    symbol: str = "SENS",
    rsi_values: Tuple[float, ...] = SENSITIVITY_RSI_GRID,
    sma_values: Tuple[int, ...] = SENSITIVITY_SMA_GRID,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Sweep strategy thresholds to expose overfitting.

    Runs the backtest for every combination of RSI entry threshold × SMA
    window and returns the return grid. A strategy whose edge vanishes when
    the threshold moves ±5 is overfit; a flat grid means the edge is
    structural. The return spread (best − worst) and the count of losing
    parameter combos make that visible at a glance.

    Args:
        df: Historical OHLCV (raw or normalized).
        strategy_rules: Base rules — their ``rsi < X`` thresholds and
            ``sma_*`` references are substituted per grid cell.
        symbol: Symbol label for the runs.
        rsi_values: RSI entry thresholds to sweep.
        sma_values: SMA windows to sweep.
        start_date / end_date: Optional window.

    Returns:
        Dict with the grid rows (rsi/sma → metrics), best/worst cells, and
        an overfit_signal boolean + verdict. ``available: False`` when too
        little data exists.
    """
    norm = _normalize_ohlcv(df).dropna(subset=["Close"])
    if start_date:
        norm = norm.loc[start_date:]
    if end_date:
        norm = norm.loc[:end_date]
    if len(norm) < 60:
        return {"available": False, "reason": "not enough bars"}

    base_buy = strategy_rules.get("buy_rules", [])
    rows: List[Dict[str, Any]] = []
    for rsi in rsi_values:
        for sma in sma_values:
            rules = dict(strategy_rules)
            rules["buy_rules"] = [
                _sub_sma_reference(_sub_rule_threshold(rule, "rsi", rsi), sma)
                for rule in base_buy
            ]
            res = run_backtest(
                symbol, rules,
                str(norm.index[0])[:10], str(norm.index[-1])[:10],
                df=norm, compute_robustness=False,
            )
            rows.append({
                "rsi": float(rsi),
                "sma": int(sma),
                "total_return": res.total_return,
                "sharpe_ratio": res.sharpe_ratio,
                "max_drawdown": res.max_drawdown,
                "total_trades": res.total_trades,
            })

    returns = [r["total_return"] for r in rows]
    best = max(rows, key=lambda r: r["total_return"])
    worst = min(rows, key=lambda r: r["total_return"])
    spread = best["total_return"] - worst["total_return"]
    losing = sum(1 for r in returns if r <= 0)
    # An edge that evaporates when the threshold moves, or that needs a
    # single magic setting to work at all, is a classic overfit fingerprint.
    overfit_signal = spread > 0.5 or (losing >= 1 and best["total_return"] > 0)

    return {
        "available": True,
        "grid": rows,
        "best": {
            "rsi": best["rsi"], "sma": best["sma"],
            "total_return": best["total_return"],
        },
        "worst": {
            "rsi": worst["rsi"], "sma": worst["sma"],
            "total_return": worst["total_return"],
        },
        "return_spread": float(spread),
        "losing_combos": losing,
        "total_combos": len(rows),
        "overfit_signal": bool(overfit_signal),
        "verdict": (
            "Parameter-sensitive: the edge depends heavily on exact "
            "thresholds — likely overfit."
            if overfit_signal
            else "Robust: return is stable across the parameter grid."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# High-Level API
# ═══════════════════════════════════════════════════════════════════════════════

def _hash_query(query: str) -> str:
    """Create a deterministic hash from a user query string."""
    return hashlib.sha256(query.encode()).hexdigest()[:16]


def compute_portfolio(
    results: Dict[str, BacktestResult],
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
    allocation: str = "equal",  # "equal" | "risk_based"
    rebalance: str = "none",  # "none" | "monthly" | "quarterly" | "yearly"
    max_points: int = MAX_EQUITY_CURVE_POINTS,
) -> Optional[Dict[str, Any]]:
    """Aggregate per-symbol backtests into ONE shared-pool portfolio.

    The old engine backtested each symbol with its own ``initial_capital``
    (N independent $100k accounts). This combines the per-symbol daily
    equity curves into a single portfolio over a SHARED pool:

      - ``allocation='equal'``      → w_i = 1/N
      - ``allocation='risk_based'`` → w_i ∝ 1/vol_i (inverse-vol weights,
        vol = std of the symbol's daily equity returns)
      - ``rebalance``: none | monthly | quarterly | yearly — at each
        boundary the slices are reset to the target weights (trim
        winners, top up losers); between boundaries each slice grows at
        its own symbol's rate.

    Combined metrics (total return, Sharpe, max drawdown, equity +
    drawdown curves) are computed from the aggregated daily series — one
    number that describes the whole book, not N parallel accounts.

    Returns None when fewer than 2 symbols have usable equity curves.
    """
    usable: List[str] = []
    for sym, r in results.items():
        curve = getattr(r, "full_equity_curve", None) or getattr(
            r, "equity_curve", None
        )
        if curve:
            usable.append(sym)
    if len(usable) < 2:
        return None

    # Per-symbol daily series (full resolution when present, else the
    # downsampled chart curve — still correct, just coarser).
    series: Dict[str, Dict[str, float]] = {}
    for sym in usable:
        r = results[sym]
        curve = getattr(r, "full_equity_curve", None) or r.equity_curve
        series[sym] = {str(p["date"]): float(p["value"]) for p in curve}

    all_dates = sorted({d for s in series.values() for d in s})
    if len(all_dates) < 2:
        return None

    # Forward-fill so every symbol has a value on every date (holidays,
    # differing listing calendars). Symbols with <2 usable points drop out.
    filled: Dict[str, Dict[str, float]] = {}
    for sym in usable:
        d = series[sym]
        prev: Optional[float] = None
        row: Dict[str, float] = {}
        for dt in all_dates:
            if dt in d:
                prev = d[dt]
            if prev is not None:
                row[dt] = prev
        if len(row) >= 2:
            filled[sym] = row
    if len(filled) < 2:
        return None

    # A symbol's series may start AFTER all_dates[0] (newer listing) — the
    # forward-fill only backfills to its first real value. Anchor it at its
    # OWN first value; before that date its slice sits flat (un-invested
    # cash) instead of crashing on a missing key.
    first_val: Dict[str, float] = {s: filled[s][min(filled[s])] for s in filled}

    # Allocation weights.
    if allocation == "risk_based":
        vols: Dict[str, float] = {}
        for sym, d in filled.items():
            vals = [d[dt] for dt in all_dates if dt in d]
            daily = pd.Series(vals).pct_change().dropna()
            vols[sym] = (
                float(daily.std()) if len(daily) > 1 and daily.std() > 0 else 0.0
            )
        total_inv = sum(1.0 / v for v in vols.values() if v > 0)
        if total_inv > 0:
            weights = {
                s: (1.0 / vols[s] / total_inv) if vols[s] > 0 else 0.0
                for s in filled
            }
        else:
            weights = {s: 1.0 / len(filled) for s in filled}
    else:
        weights = {s: 1.0 / len(filled) for s in filled}

    def _period(dt: str) -> str:
        y, m = dt[:4], int(dt[5:7])
        if rebalance == "yearly":
            return y
        if rebalance == "quarterly":
            return f"{y}-{(m - 1) // 3}"
        if rebalance == "monthly":
            return f"{y}-{m:02d}"
        return "all"  # no rebalance → one period

    pool = float(initial_capital)
    anchor_slice: Dict[str, float] = {s: weights[s] * pool for s in filled}
    anchor_value: Dict[str, float] = dict(first_val)
    combined: List[Dict[str, Any]] = []
    prev_period = _period(all_dates[0])

    def _slice_value(s: str, dt: str) -> float:
        """Current dollar value of symbol ``s``'s slice on date ``dt``.

        Falls back to the symbol's own first value for dates before its
        listing (slice sits flat = un-invested cash), and skips zero/None
        anchors so a symbol can never divide by zero.
        """
        anchor = anchor_value[s]
        if not anchor or anchor <= 0:
            return 0.0
        return anchor_slice[s] * (filled[s].get(dt, anchor) / anchor)

    for dt in all_dates:
        period = _period(dt)
        if period != prev_period:
            # Rebalance: reset each slice to its target weight of the
            # CURRENT total (trim winners, top up losers).
            total_now = sum(_slice_value(s, dt) for s in filled)
            anchor_slice = {s: weights[s] * total_now for s in filled}
            anchor_value = {s: filled[s].get(dt, first_val[s]) for s in filled}
            prev_period = period
        total = sum(_slice_value(s, dt) for s in filled)
        combined.append({"date": dt, "value": round(total, 2)})

    values = [p["value"] for p in combined]
    final_value = values[-1]
    total_return = (final_value - pool) / pool if pool else 0.0
    daily = pd.Series(values).pct_change().dropna()
    sharpe = (
        float((daily.mean() / daily.std()) * np.sqrt(252))
        if len(daily) > 1 and daily.std() > 0
        else None
    )
    peak = pd.Series(values).expanding().max()
    dd = (pd.Series(values) - peak) / peak
    max_dd = float(dd.min()) if len(values) > 1 and not dd.empty else None
    drawdown_curve = _downsample_equity_curve(
        [p["date"] for p in combined], [float(v) for v in dd], max_points
    )
    equity_curve = _downsample_equity_curve(
        [p["date"] for p in combined], values, max_points
    )
    return {
        "allocation": allocation,
        "rebalance": rebalance,
        "initial_capital": round(pool, 2),
        "final_value": round(final_value, 2),
        "total_return_pct": round(total_return * 100, 2),
        "sharpe_ratio": round(sharpe, 3) if sharpe is not None else None,
        "max_drawdown_pct": round(max_dd * 100, 2) if max_dd is not None else None,
        "weights": {s: round(w, 4) for s, w in weights.items()},
        "equity_curve": equity_curve,
        "drawdown_curve": drawdown_curve,
        "symbols": sorted(filled.keys()),
    }


class BacktestEngine:
    """Premium backtesting engine for Stratoscan paid subscribers.

    Orchestrates: data fetching, strategy translation, simulation,
    result caching, and reporting.
    """

    def __init__(self, db: Optional[Any] = None) -> None:
        # Honesty notes surfaced by backtest_top_results to the API layer:
        # the screener's data_quality.message (relaxed filters / resolved
        # contradictions) and a candidate-fill-in note (some top-N symbols
        # had no data and were replaced by next-ranked candidates). Both are
        # None until a screening backtest sets them.
        self._last_screen_message: Optional[str] = None
        self._last_fill_note: Optional[str] = None
        # Production: dedicated Neon backtest database (BACKTEST_DATABASE_URL)
        # so 10 years of OHLCV is shared across instances, not a single-writer
        # SQLite file. Local dev / tests without that URL fall back to SQLite.
        self._db = db or (BacktestDB() if settings.has_backtest_database else SQLiteDB())
        # Log the backend so a silent production SQLite fallback is visible at
        # runtime (validate_config also warns, but this catches lazy starts).
        logger.info(
            "BacktestEngine storage: %s",
            type(self._db).__name__,
        )
        # Self-heal the schema. The backtest tables are normally created by
        # the manual `setup` CLI — but a fresh server or a wiped data/
        # directory starts with nothing, and nothing else initializes it.
        # Without this, /backtest silently returned HTTP 200 with ZERO
        # results ("no such table: historical_prices" on every symbol) — a
        # silent failure that looks like "no matches". init_schema is
        # idempotent (CREATE TABLE IF NOT EXISTS), so the per-request engine
        # construction cost is negligible.
        self._db.init_schema()

    def _get_benchmark_series(
        self, start_date: str, end_date: str
    ) -> Optional[pd.Series]:
        """Fetch the benchmark's Close series over the test window.

        Cache first (the benchmark is just another symbol in
        historical_prices), yfinance on a miss — stored for next time.
        Returns None on any failure so a benchmark outage degrades to
        no-comparison rather than failing the whole backtest.

        Args:
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).

        Returns:
            Close series (DatetimeIndex) or None.
        """
        try:
            existing = self._db.get_historical_prices(
                BENCHMARK_SYMBOL, start_date, end_date
            )
            # Coverage check (same class as the symbol cache): a benchmark
            # cache populated by a default-window run starts today−10y, so
            # a request with an earlier start_date would silently compare
            # the strategy against a SHORTER benchmark window than the
            # strategy itself ran. Refetch when the cache's first bar is
            # more than a few days after the requested start.
            benchmark_covered = False
            if existing:
                rows = [dict(r) for r in existing]
                if rows:
                    first_bar = pd.to_datetime(rows[0]["date"])
                    requested_start = pd.Timestamp(start_date)
                    benchmark_covered = (
                        pd.notna(first_bar)
                        and (first_bar - requested_start).days <= 7
                    )
            if benchmark_covered:
                series = pd.Series(
                    [float(r["close"]) for r in rows],
                    index=pd.to_datetime([r["date"] for r in rows]),
                )
                # Same inf defense as the sim (a pre-fix DB could hold inf).
                # None on an empty cleaned series keeps the "benchmark
                # unavailable" warning contract honest.
                series = series.replace([np.inf, -np.inf], np.nan).ffill().dropna()
                return None if series.empty else series

            df = fetch_historical_data(BENCHMARK_SYMBOL, start_date=start_date)
            if df is None:
                logger.warning(
                    "Benchmark %s unavailable — skipping comparison",
                    BENCHMARK_SYMBOL,
                )
                return None
            store_historical_data(self._db, BENCHMARK_SYMBOL, df)
            sliced = df.loc[start_date:end_date]
            if sliced.empty:
                return None
            # auto_adjust=False now: prefer the explicit adjusted series so
            # the benchmark is on the same basis as the strategy's equity.
            # Same inf defense as the sim: an infinite benchmark price would
            # leak inf into alpha/beta/excess (un-serializable → 500).
            col = "Adj Close" if "Adj Close" in sliced.columns else "Close"
            series = sliced[col].astype(float)
            series = series.replace([np.inf, -np.inf], np.nan).ffill().dropna()
            return None if series.empty else series
        except Exception as e:
            logger.warning(
                "Benchmark fetch failed for %s: %s", BENCHMARK_SYMBOL, e
            )
            return None

    def backtest(
        self,
        interpreted: InterpretedQuery,
        symbols: List[str],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        strategy_rules: Optional[Dict[str, Any]] = None,
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, BacktestResult]:
        """Run a backtest for multiple symbols using the interpreted strategy.

        Args:
            interpreted: The LLM-interpreted user query.
            symbols: Stock symbols to backtest.
            start_date: Start date (default: 10 years ago).
            end_date: End date (default: today).
            strategy_rules: Optional pre-built rules (e.g. translated rules
                with user risk overrides applied). Defaults to translating
                ``interpreted`` — the API passes rules so the echoed
                strategy and the executed strategy can never diverge.
            progress_cb: Optional callback invoked with each symbol as its
                backtest COMPLETES (used by the async job to stream live
                per-symbol progress to the UI). Never raises.

        Returns:
            Dict mapping symbol → BacktestResult.
        """
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")
        if start_date is None:
            start_date = (
                datetime.now() - timedelta(days=HISTORICAL_YEARS * 365)
            ).strftime("%Y-%m-%d")

        if strategy_rules is None:
            strategy_rules = _translate_to_rules(interpreted)
        strategy_json = json.dumps(strategy_rules)

        # Fetch the benchmark ONCE per backtest so every symbol is compared
        # against the same SPY series (cached after the first run).
        benchmark_series = self._get_benchmark_series(start_date, end_date)

        results: Dict[str, BacktestResult] = {}

        # Symbols are backtested CONCURRENTLY. The dominant per-symbol cost
        # is the yfinance download of 10 years of daily OHLCV (I/O-bound;
        # verified live at 40s–2m36s per symbol on a cold cache), so a
        # bounded thread pool turns a top_n=5 backtest from ~5x a single
        # fetch into ~1x — the fix for backtests that blew 20-minute client
        # timeouts. Results keep input order, so output stays deterministic.
        if len(symbols) == 1:
            # Fast path — no pool overhead for the common single-symbol case.
            one = self._backtest_one(
                interpreted, symbols[0], start_date, end_date,
                strategy_rules, strategy_json, benchmark_series,
            )
            if one is not None:
                results[symbols[0]] = one
            if progress_cb is not None:
                try:
                    progress_cb(symbols[0])
                except Exception:
                    pass  # progress is advisory — never break the backtest
            return results

        workers = min(BACKTEST_PARALLEL_WORKERS, len(symbols))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                (
                    symbol,
                    pool.submit(
                        self._backtest_one,
                        interpreted, symbol, start_date, end_date,
                        strategy_rules, strategy_json, benchmark_series,
                    ),
                )
                for symbol in symbols
            ]
            for symbol, future in futures:
                try:
                    one = future.result()
                except Exception as e:
                    logger.error("Backtest failed for %s: %s", symbol, e)
                    continue
                if one is not None:
                    results[symbol] = one
                if progress_cb is not None:
                    try:
                        progress_cb(symbol)
                    except Exception:
                        pass  # progress is advisory — never break the backtest
        return results

    def _backtest_one(
        self,
        interpreted: InterpretedQuery,
        symbol: str,
        start_date: str,
        end_date: str,
        strategy_rules: Dict[str, Any],
        strategy_json: str,
        benchmark_series: Optional[pd.Series],
    ) -> Optional[BacktestResult]:
        """Backtest a single symbol end-to-end: fetch → simulate → robustness → cache.

        Returns None when the symbol has no usable data or any step fails —
        the caller skips it and ``backtest_top_results`` promotes the
        next-ranked candidate. Extracted from :meth:`backtest` so per-symbol
        work runs in parallel threads (the yfinance download is I/O-bound
        and dominates wall-clock time). DB access is safe from worker
        threads: BacktestDB uses a pooled connection, SQLiteDB a per-thread
        connection (both in src/database.py).
        """
        try:
            # Fetch data if not already cached in the backtest DB.
            # Coverage check: the cache may have been populated by a
            # DEFAULT-window run (fetch starts today−10y), so a later
            # request whose start_date is EARLIER than the cache's first
            # bar is silently truncated — the sim ran a shorter window
            # than the API reported (verified live: start 2016-01-01
            # produced a curve beginning 2016-08-03). Treat the cache
            # as a miss and refetch with the explicit start when its
            # first bar is more than a few days after the requested
            # start (tolerance = first trading day after a holiday).
            existing = self._db.get_historical_prices(
                symbol, start_date, end_date
            )

            df = None
            if existing:
                # Convert stored rows back to a DataFrame (dict-able rows
                # from SQLite or the BacktestDB RealDictCursor)
                rows = [dict(row) for row in existing]
                df = pd.DataFrame(rows)
                df.set_index("date", inplace=True)
                df.index = pd.to_datetime(df.index)
                df.rename(columns={
                    "open": "Open",
                    "high": "High",
                    "low": "Low",
                    "close": "Close",
                    "volume": "Volume",
                }, inplace=True)
                first_bar = df.index.min()
                if pd.notna(first_bar):
                    requested_start = pd.Timestamp(start_date)
                    if (first_bar - requested_start).days > 7:
                        logger.info(
                            "Cache for %s starts %s — requested window "
                            "starts %s; refetching for full coverage",
                            symbol, first_bar.date(), start_date,
                        )
                        df = None

            if df is None:
                # Fetch from Yahoo Finance (honors the requested start).
                df = fetch_historical_data(symbol, start_date=start_date)
                if df is not None:
                    store_historical_data(self._db, symbol, df)

            if df is None:
                logger.warning("No data available for %s, skipping", symbol)
                return None

            result = run_backtest(
                symbol=symbol,
                strategy_rules=strategy_rules,
                start_date=start_date,
                end_date=end_date,
                df=df,
                benchmark_series=benchmark_series,
            )
            # If the benchmark fetch failed, say so — silent nulls for
            # alpha/beta/excess would undercut the credibility goal.
            if benchmark_series is None:
                result.warnings.append(
                    f"{BENCHMARK_SYMBOL} benchmark unavailable — "
                    "alpha/beta/excess comparison not computed."
                )
            # Tier 2 honesty: yfinance only serves currently-listed
            # tickers — dead/delisted companies are absent, which
            # flatters results. Say so on every result.
            result.data_quality.append(
                "Survivorship bias: Yahoo Finance serves currently-listed "
                "tickers; delisted companies are absent, which can "
                "flatter returns."
            )
            # Tier 3 robustness: walk-forward + parameter sensitivity are
            # pure compute over the data already in hand (the Stratoscan
            # way). The df is the full history, so out-of-sample windows
            # can warm their indicators correctly. Each is isolated in its
            # own try/except: robustness is secondary to the headline
            # result, so a crash must NEVER drop the symbol's backtest —
            # or its sibling analysis. On failure the field stays None
            # (the dataclass default), keeping the response valid.
            try:
                result.walk_forward = walk_forward_analysis(
                    df, strategy_rules, symbol=symbol,
                    start_date=start_date, end_date=end_date,
                )
            except Exception as e:
                logger.warning(
                    "Walk-forward failed for %s: %s — headline preserved",
                    symbol, e,
                )
            try:
                result.parameter_sensitivity = parameter_sensitivity(
                    df, strategy_rules, symbol=symbol,
                    start_date=start_date, end_date=end_date,
                )
            except Exception as e:
                logger.warning(
                    "Parameter sensitivity failed for %s: %s — headline preserved",
                    symbol, e,
                )

            # Cache the result in the backtest DB
            query_hash = _hash_query(interpreted.raw_input)
            self._db.save_backtest_result({
                "user_query_hash": query_hash,
                "strategy_params": strategy_json,
                "symbol": symbol,
                "start_date": start_date,
                "end_date": end_date,
                "initial_capital": result.initial_capital,
                "final_value": result.final_value,
                "total_return": result.total_return,
                "sharpe_ratio": result.sharpe_ratio,
                "max_drawdown": result.max_drawdown,
                "win_rate": result.win_rate,
                "total_trades": result.total_trades,
            })
            return result
        except Exception as e:
            logger.error("Backtest failed for %s: %s", symbol, e)
            return None

    def backtest_top_results(
        self,
        interpreted: InterpretedQuery,
        top_n: int = 5,
        strategy_rules: Optional[Dict[str, Any]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        max_sector_exposure: Optional[int] = None,
        symbols: Optional[List[str]] = None,
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, BacktestResult]:
        """Screen for top stocks, then backtest the top N (with fill-in).

        Tier 3 compare mode: pass ``symbols`` to pin the EXACT symbol set
        (skips screening entirely — strategy B re-runs on strategy A's
        picks, so the A/B is genuine). ``progress_cb`` streams each
        completed symbol to the async job so the UI can show live progress.

        Args:
            interpreted: The LLM-interpreted user query.
            top_n: Number of top stocks to backtest.
            strategy_rules: Optional pre-built rules (API passes them so the
                echoed strategy and the executed strategy never diverge).
            start_date: Optional backtest start (YYYY-MM-DD). Defaults to
                10 years ago when None.
            end_date: Optional backtest end (YYYY-MM-DD). Defaults to today
                when None.
            max_sector_exposure: Optional cap on how many stocks from the
                SAME sector may be picked (Tier 5 diversification). The
                screener ranks by composite score, so top-10 can be 10 tech
                names; this walks candidates in rank order and skips a name
                whose sector already hit the cap, promoting the next-ranked
                candidate instead. Skips are counted and surfaced on
                ``self._last_fill_note`` so a diversified backtest never
                looks like the plain top-N. None = no cap (legacy behavior).
            symbols: Optional explicit symbol set to backtest (Tier 3
                compare). When provided the screener is skipped entirely
                and these exact symbols are tested in order.
            progress_cb: Optional callback invoked with each symbol as its
                backtest completes (async job progress streaming). Never
                raises.

        Returns:
            Dict mapping symbol → BacktestResult (never more than top_n).

        Candidate fill-in (Tier 4 #9): the screener may rank a symbol whose
        price history is unavailable (delisted / never-tracked tickers —
        verified live: BRK.A/BRK.B in a top-3 silently shrank the backtest
        to 1 symbol). To honor top_n we screen a SUPERSET and, when a
        candidate's data fails, promote the next-ranked candidate instead
        of silently dropping it. The superset also feeds the screener's
        0-match rescue (auto-relax), which returns nearest matches with an
        honest ``data_quality.message`` — that note is forwarded to the
        caller via ``self._last_screen_message`` so the API can surface it
        (a relaxed backtest must never look like an exact one).
        """
        from src.screener import StockScreener

        self._last_screen_message = None
        self._last_fill_note = None
        self._screen_data_age: Dict[str, Optional[float]] = {}
        self._screen_is_stale: Dict[str, bool] = {}

        # Tier 3 compare mode: an explicit symbol set skips the screener
        # entirely — the caller (API) already picked the symbols from a
        # prior run, so screening again would re-rank and break the A/B.
        if symbols:
            results: Dict[str, BacktestResult] = {}
            got = self.backtest(
                interpreted,
                symbols,
                strategy_rules=strategy_rules,
                start_date=start_date,
                end_date=end_date,
                progress_cb=progress_cb,
            )
            for sym in symbols:  # preserve requested order
                if sym in got:
                    results[sym] = got[sym]
            return results

        screener = StockScreener()
        # Screen a superset so fill-in has next-ranked candidates to promote
        # (cap it so a broad query doesn't screen thousands of rows for one
        # backtest — 4x top_n covers realistic delisting gaps). A sector
        # diversification cap needs a larger superset to still fill top_n
        # after same-sector skips (e.g. max 2 per sector, top_n=10 → need
        # ~5 sectors represented).
        if max_sector_exposure:
            screen_limit = min(max(top_n * 4, top_n * max_sector_exposure * 3), 300)
        else:
            screen_limit = min(max(top_n * 4, top_n), 200)
        screening = screener.screen(interpreted, limit=screen_limit)

        # Forward the screener's honesty note (relaxed filters / resolved
        # contradictions / "still found 0") so the API can put it on the
        # BacktestResponse.message field. Defensive access: hermetic tests
        # inject a bare fake screener result with no data_quality/results.
        dq = getattr(screening, "data_quality", None)
        self._last_screen_message = getattr(dq, "get", lambda k, d=None: d)("message")
        symbols = [r.symbol for r in getattr(screening, "results", [])]

        # Capture per-symbol data-age from screener so the API can surface
        # staleness badges on the backtest results tab (same signal the
        # screener renders via DataAgeBadge).
        self._screen_data_age: Dict[str, Optional[float]] = {
            r.symbol: getattr(r, "data_age_minutes", None)
            for r in getattr(screening, "results", [])
        }
        self._screen_is_stale: Dict[str, bool] = {
            r.symbol: getattr(r, "is_stale", False)
            for r in getattr(screening, "results", [])
        }

        # Tier 5: sector diversification. Walk candidates in rank order,
        # accept at most ``max_sector_exposure`` per sector; a symbol whose
        # sector bucket is full is SKIPPED (its slot goes to the next-ranked
        # candidate from another sector). Unknown sectors (None) form their
        # own bucket so a data gap can't concentrate picks either.
        if max_sector_exposure and symbols:
            sector_counts: Dict[str, int] = {}
            diversified: List[str] = []
            sector_skips = 0
            for r in getattr(screening, "results", []):
                sec = getattr(r, "sector", None) or "unknown"
                if sector_counts.get(sec, 0) >= max_sector_exposure:
                    sector_skips += 1
                    continue
                sector_counts[sec] = sector_counts.get(sec, 0) + 1
                diversified.append(getattr(r, "symbol", ""))
            symbols = [s for s in diversified if s]
            if sector_skips:
                self._last_fill_note = (
                    f"Diversification cap: limited to {max_sector_exposure} "
                    f"stock(s) per sector; skipped {sector_skips} same-sector "
                    "candidate(s) to keep the picks diversified."
                )

        if not symbols:
            logger.warning("No matching stocks found for backtest")
            return {}

        results: Dict[str, BacktestResult] = {}
        filled_from = 0  # candidates consumed by fill-in (not in top_n)

        # Probe candidates in parallel waves: backtest() parallelizes the
        # batch internally (I/O-bound yfinance downloads), so a wave of top_n
        # candidates costs ~1x a single fetch instead of top_n x, and a
        # delisted candidate in the wave is replaced by the next-ranked one
        # via the next wave. Wave size = top_n (not padded to the worker
        # count) so a small top_n never fetches MORE symbols than needed —
        # top_n=1 should fetch exactly 1, not 4. Results are taken in rank
        # order and capped at top_n, so the returned set is identical to the
        # old sequential probing — just much faster.
        wave_size = max(1, top_n)
        for start in range(0, len(symbols), wave_size):
            if len(results) >= top_n:
                break
            batch = symbols[start:start + wave_size]
            got = self.backtest(
                interpreted,
                batch,
                strategy_rules=strategy_rules,
                start_date=start_date,
                end_date=end_date,
                progress_cb=progress_cb,
            )
            for symbol in batch:  # preserve rank order
                if len(results) >= top_n:
                    break
                if symbol in got:
                    results[symbol] = got[symbol]
                else:
                    filled_from += 1

        if filled_from:
            if len(results) < top_n:
                detail = f"but only {len(results)} of top {top_n} had usable data"
            else:
                detail = f"keeping top {top_n} intact"
            self._last_fill_note = (
                f"{filled_from} top-ranked candidate(s) had no usable "
                f"price history — promoted the next-ranked matches, {detail}."
            )
            logger.warning(
                "Candidate fill-in: %d top candidates lacked data for '%s'",
                filled_from, interpreted.raw_input,
            )

        # Inject per-symbol data-age from the screener into each result
        # so the API can surface DataAgeBadge on the backtest tab.
        for sym, bt in results.items():
            bt.data_age_minutes = self._screen_data_age.get(sym)
            bt.is_stale = self._screen_is_stale.get(sym, False)

        return results
