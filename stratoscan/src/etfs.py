"""The curated ETF set (Day 14) — the only asset-class expansion in the plan.

ETFs trade like stocks and their yfinance history is split- and
distribution-adjusted the same way, so the backtest math needs no change. The
screener universe, though, comes from Finnhub's "Common Stock" listing, which
leaves every fund out. This module is the hand-picked list that goes back in:
liquid, long-lived, plain-vanilla funds only.

Deliberately NOT included: leveraged and inverse funds (TQQQ, SQQQ, …). They
reset daily, so a multi-day backtest of one measures path decay, not the idea
being tested — a result that looks like a strategy and isn't.

Every row is stored with ``asset_type = 'etf'``. Screens return stocks only
unless the query asks for ETFs (``InterpretedQuery.asset_type``), and every
surface that shows a row labels a fund as one. ETFs carry no market cap, P/E
or fundamentals; price, volume and technicals refresh like any stock.

``sector`` is set for sector funds (so "technology ETFs" finds XLK) using the
interpreter's canonical sector names; ``industry`` carries the fund's category.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# (symbol, name, sector or None, category)
ETFS: Tuple[Tuple[str, str, Optional[str], str], ...] = (
    # Broad US market
    ("SPY", "SPDR S&P 500 ETF Trust", None, "Broad market"),
    ("IVV", "iShares Core S&P 500 ETF", None, "Broad market"),
    ("VOO", "Vanguard S&P 500 ETF", None, "Broad market"),
    ("VTI", "Vanguard Total Stock Market ETF", None, "Broad market"),
    ("QQQ", "Invesco QQQ Trust", None, "Broad market"),
    ("DIA", "SPDR Dow Jones Industrial Average ETF Trust", None, "Broad market"),
    ("RSP", "Invesco S&P 500 Equal Weight ETF", None, "Broad market"),
    ("MDY", "SPDR S&P MidCap 400 ETF Trust", None, "Broad market"),
    ("IWM", "iShares Russell 2000 ETF", None, "Broad market"),
    # Sectors (Select Sector SPDRs + three widely traded industry funds)
    ("XLK", "Technology Select Sector SPDR Fund", "Technology", "Sector"),
    ("XLF", "Financial Select Sector SPDR Fund", "Financial Services", "Sector"),
    ("XLV", "Health Care Select Sector SPDR Fund", "Healthcare", "Sector"),
    ("XLE", "Energy Select Sector SPDR Fund", "Energy", "Sector"),
    ("XLI", "Industrial Select Sector SPDR Fund", "Industrials", "Sector"),
    ("XLY", "Consumer Discretionary Select Sector SPDR Fund", "Consumer Cyclical", "Sector"),
    ("XLP", "Consumer Staples Select Sector SPDR Fund", "Consumer Defensive", "Sector"),
    ("XLU", "Utilities Select Sector SPDR Fund", "Utilities", "Sector"),
    ("XLB", "Materials Select Sector SPDR Fund", "Basic Materials", "Sector"),
    ("XLRE", "Real Estate Select Sector SPDR Fund", "Real Estate", "Sector"),
    ("XLC", "Communication Services Select Sector SPDR Fund", "Communication Services", "Sector"),
    ("SMH", "VanEck Semiconductor ETF", "Technology", "Industry"),
    ("XBI", "SPDR S&P Biotech ETF", "Healthcare", "Industry"),
    ("KRE", "SPDR S&P Regional Banking ETF", "Financial Services", "Industry"),
    # Style and factor
    ("VUG", "Vanguard Growth ETF", None, "Style & factor"),
    ("VTV", "Vanguard Value ETF", None, "Style & factor"),
    ("SCHD", "Schwab U.S. Dividend Equity ETF", None, "Style & factor"),
    ("VIG", "Vanguard Dividend Appreciation ETF", None, "Style & factor"),
    ("MTUM", "iShares MSCI USA Momentum Factor ETF", None, "Style & factor"),
    ("USMV", "iShares MSCI USA Min Vol Factor ETF", None, "Style & factor"),
    # International
    ("EFA", "iShares MSCI EAFE ETF", None, "International"),
    ("VEA", "Vanguard FTSE Developed Markets ETF", None, "International"),
    ("EEM", "iShares MSCI Emerging Markets ETF", None, "International"),
    ("VWO", "Vanguard FTSE Emerging Markets ETF", None, "International"),
    # Bonds
    ("AGG", "iShares Core U.S. Aggregate Bond ETF", None, "Bonds"),
    ("BND", "Vanguard Total Bond Market ETF", None, "Bonds"),
    ("SHY", "iShares 1-3 Year Treasury Bond ETF", None, "Bonds"),
    ("IEF", "iShares 7-10 Year Treasury Bond ETF", None, "Bonds"),
    ("TLT", "iShares 20+ Year Treasury Bond ETF", None, "Bonds"),
    ("TIP", "iShares TIPS Bond ETF", None, "Bonds"),
    ("LQD", "iShares iBoxx $ Investment Grade Corporate Bond ETF", None, "Bonds"),
    ("HYG", "iShares iBoxx $ High Yield Corporate Bond ETF", None, "Bonds"),
    # Real assets
    ("VNQ", "Vanguard Real Estate ETF", "Real Estate", "Real assets"),
    ("GLD", "SPDR Gold Shares", None, "Real assets"),
    ("SLV", "iShares Silver Trust", None, "Real assets"),
    ("DBC", "Invesco DB Commodity Index Tracking Fund", None, "Real assets"),
)

ETF_SYMBOLS: frozenset = frozenset(sym for sym, *_ in ETFS)


def etf_seed_records() -> List[Dict[str, Any]]:
    """One ``stock_master`` identity row per fund (no prices — the price and
    technical refreshes fill those exactly as they do for stocks)."""
    return [
        {"symbol": sym, "company_name": name, "sector": sector, "industry": category,
         "asset_type": "etf"}
        for sym, name, sector, category in ETFS
    ]


def with_etfs(symbols: List[str]) -> List[str]:
    """The stock universe plus the fund set, sorted and de-duplicated."""
    return sorted(set(symbols) | ETF_SYMBOLS)
