"""Deterministic NL interpreter — regex/dictionary parser for Stratoscan.

Translates plain-English stock screening queries into structured
``InterpretedQuery`` objects without any LLM call.  Sub-millisecond
latency, zero cost, zero rate-limit risk.

**Design principles**
  1. **Output compatibility** — produces the exact same ``InterpretedQuery``
     dataclass the LLM interpreter returns.  The screener, backtester, and
     API consume it identically.
  2. **Confidence scoring** — each parse returns a 0.0-1.0 confidence
     score.  The caller (``interpret_query``) decides whether to trust
     the deterministic result or fall back to the LLM.
  3. **Graceful degradation** — unknown queries score low confidence and
     route to the LLM.  A parser bug never crashes the screen (wrapped
     in try/except at the caller).
  4. **Extensibility** — adding a new field means adding a regex pattern,
     a synonym entry, or a dictionary lookup.  No architectural changes.

Used by: ``src.interpreter.interpret_query`` (hybrid path).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from src.interpreter import (
    DERIVED_CRITERIA_KEYS,
    ComparisonOp,
    FilterCriterion,
    InterpretedQuery,
)

logger = logging.getLogger(__name__)

# ── Confidence threshold ────────────────────────────────────────────────────
# Queries scoring >= this value are served deterministically.
# Queries below this fall back to the LLM.
CONFIDENCE_THRESHOLD = 0.6

# ── Sector aliases ──────────────────────────────────────────────────────────
# Maps common abbreviations / synonyms to the canonical sector names
# stored in ``stock_master.sector``.
SECTOR_ALIASES: Dict[str, str] = {
    # Technology
    "tech": "Technology",
    "technology": "Technology",
    "tech stocks": "Technology",
    "software": "Technology",
    "semiconductor": "Technology",
    "semiconductors": "Technology",
    "chips": "Technology",
    "ai": "Technology",
    "artificial intelligence": "Technology",
    # Healthcare
    "health": "Healthcare",
    "healthcare": "Healthcare",
    "pharma": "Healthcare",
    "pharmaceutical": "Healthcare",
    "biotech": "Healthcare",
    "biotechnology": "Healthcare",
    "medical": "Healthcare",
    "drugs": "Healthcare",
    # Financials
    "finance": "Financial Services",
    "financials": "Financial Services",
    "financial": "Financial Services",
    "banking": "Financial Services",
    "banks": "Financial Services",
    "insurance": "Financial Services",
    # Energy
    "energy": "Energy",
    "oil": "Energy",
    "gas": "Energy",
    "petroleum": "Energy",
    "renewable": "Energy",
    "solar": "Energy",
    # Consumer
    "consumer": "Consumer Cyclical",
    "consumer cyclical": "Consumer Cyclical",
    "retail": "Consumer Cyclical",
    "ecommerce": "Consumer Cyclical",
    "e-commerce": "Consumer Cyclical",
    "defensive": "Consumer Defensive",
    "consumer defensive": "Consumer Defensive",
    "staples": "Consumer Defensive",
    "consumer staples": "Consumer Defensive",
    "food": "Consumer Defensive",
    "beverages": "Consumer Defensive",
    # Industrials
    "industrials": "Industrials",
    "industrial": "Industrials",
    "manufacturing": "Industrials",
    "aerospace": "Industrials",
    "defense": "Industrials",
    "defense stocks": "Industrials",
    # Materials
    "materials": "Basic Materials",
    "basic materials": "Basic Materials",
    "mining": "Basic Materials",
    "metals": "Basic Materials",
    # Real Estate
    "real estate": "Real Estate",
    "reits": "Real Estate",
    "reit": "Real Estate",
    "property": "Real Estate",
    # Utilities
    "utilities": "Utilities",
    "utility": "Utilities",
    "electric": "Utilities",
    "power": "Utilities",
    # Communication Services
    "communication": "Communication Services",
    "comms": "Communication Services",
    "media": "Communication Services",
    "entertainment": "Communication Services",
    "telecom": "Communication Services",
    "telecommunications": "Communication Services",
    "social media": "Communication Services",
}

# ── Industry aliases ────────────────────────────────────────────────────────
INDUSTRY_ALIASES: Dict[str, str] = {
    "software": "Software",
    "saas": "Software",
    "cloud": "Software",
    "biotech": "Biotechnology",
    "biotechnology": "Biotechnology",
    "banks": "Banks",
    "banking": "Banks",
    "pharma": "Drug Manufacturers",
    "pharmaceutical": "Drug Manufacturers",
    "semiconductors": "Semiconductors",
    "semiconductor": "Semiconductors",
    "internet": "Internet Content & Information",
    "ecommerce": "Internet Retail",
    "e-commerce": "Internet Retail",
    "social": "Internet Content & Information",
    "insurance": "Insurance",
    "autos": "Auto Manufacturers",
    "electric vehicles": "Auto Manufacturers",
    "ev": "Auto Manufacturers",
}

# ── Market-cap presets ──────────────────────────────────────────────────────
# Human-friendly shorthands → (min_B, max_B) in billions of USD.
MARKET_CAP_PRESETS: Dict[str, Tuple[float, Optional[float]]] = {
    "micro cap": (0.05, 0.3),
    "micro-cap": (0.05, 0.3),
    "microcap": (0.05, 0.3),
    "small cap": (0.3, 2.0),
    "small-cap": (0.3, 2.0),
    "smallcap": (0.3, 2.0),
    "mid cap": (2.0, 10.0),
    "mid-cap": (2.0, 10.0),
    "midcap": (2.0, 10.0),
    "midsize": (2.0, 10.0),
    "large cap": (10.0, None),
    "large-cap": (10.0, None),
    "largecap": (10.0, None),
    "big cap": (10.0, None),
    "mega cap": (200.0, None),
    "mega-cap": (200.0, None),
    "megacap": (200.0, None),
}

# ── Synonym → filter criteria ──────────────────────────────────────────────
# Each key maps to a list of (raw_key, value) pairs matching the
# LLM's output format so ``_convert_to_filter_criteria`` can handle them.
SYNONYM_MAP: Dict[str, List[Tuple[str, Any]]] = {
    # Valuation
    "undervalued": [("max_pe_ratio", 15), ("max_ps_ratio", 2)],
    "cheap": [("max_pe_ratio", 15), ("max_ps_ratio", 2)],
    "value": [("max_pe_ratio", 15), ("max_ev_ebitda", 15)],
    "bargain": [("max_pe_ratio", 15)],
    "bargains": [("max_pe_ratio", 15)],
    "expensive": [("min_pe_ratio", 30)],
    "overvalued": [("min_pe_ratio", 30)],
    # Income / dividends
    "dividend": [("min_dividend_yield", 3.0)],
    "dividends": [("min_dividend_yield", 3.0)],
    "income": [("min_dividend_yield", 3.0)],
    "income stocks": [("min_dividend_yield", 3.0)],
    "yield": [("min_dividend_yield", 2.5)],
    "high yield": [("min_dividend_yield", 4.0)],
    # Growth
    "growth": [("min_revenue_growth_ttm", 15)],
    "growth stocks": [("min_revenue_growth_ttm", 15)],
    "high growth": [("min_revenue_growth_ttm", 25)],
    "fast growing": [("min_revenue_growth_ttm", 20)],
    "rapid growth": [("min_revenue_growth_ttm", 25)],
    "accelerating": [("min_eps_growth_ttm", 20)],
    # Technical / momentum
    "oversold": [("max_rsi", 35)],
    "overbought": [("min_rsi", 70)],
    "beaten down": [("max_rsi", 35)],
    # Quality
    "profitable": [("min_roe_ttm", 10), ("min_net_margin_ttm", 5)],
    "high quality": [("min_roe_ttm", 15), ("min_net_margin_ttm", 8)],
    "quality": [("min_roe_ttm", 10)],
    "safe": [("max_beta", 0.8)],
    "safety": [("max_beta", 0.8)],
    "low risk": [("max_beta", 0.7)],
    "low volatility": [("max_beta", 0.7)],
    "stable": [("max_beta", 0.8)],
    "defensive": [("max_beta", 0.8)],
    # Volume
    "high volume": [("min_avg_volume", 1_000_000)],
    "heavily traded": [("min_avg_volume", 1_000_000)],
    "liquid": [("min_avg_volume", 500_000)],
    # Beta shorthand
    "low beta": [("max_beta", 1.0)],
    # Price levels
    "penny stocks": [("max_price", 5)],
    "penny": [("max_price", 5)],
    "blue chip": [("min_market_cap_billions", 100)],
    "blue-chip": [("min_market_cap_billions", 100)],
}

# ── Numeric regex patterns ──────────────────────────────────────────────────
# Each tuple: (compiled_regex, raw_key, comparison_op, optional_fixed_value)
# raw_key matches the LLM's output keys so ``_convert_to_filter_criteria``
# can translate them identically.

def _build_numeric_patterns() -> List[Tuple[re.Pattern, str, str, Optional[float]]]:
    """Build compiled regex patterns for explicit numeric criteria."""
    P: List[Tuple[re.Pattern, str, str, Optional[float]]] = []

    def add(pattern: str, key: str, op: str, fixed: Optional[float] = None) -> None:
        P.append((re.compile(pattern, re.IGNORECASE), key, op, fixed))

    # ── RSI ──
    add(r'rsi\s*(?:below|under|less\s*than|<|<=|lte)\s*(\d+(?:\.\d+)?)',
        'max_rsi', 'lte')
    add(r'rsi\s*(?:above|over|greater\s*than|>|>=|gte)\s*(\d+(?:\.\d+)?)',
        'min_rsi', 'gte')

    # ── P/E ──
    add(r'(?:p/?e|price[\s-]*to[\s-]*earnings?)\s*(?:below|under|<|<=)\s*(\d+(?:\.\d+)?)',
        'max_pe_ratio', 'lte')
    add(r'(?:p/?e|price[\s-]*to[\s-]*earnings?)\s*(?:above|over|>|>=)\s*(\d+(?:\.\d+)?)',
        'min_pe_ratio', 'gte')

    # ── Market cap (explicit numeric) ──
    add(r'market[\s-]*cap\s*(?:below|under|<|<=)\s*(\d+(?:\.\d+)?)\s*(B|M|bn|mn)?',
        'max_market_cap_billions', 'lte')
    add(r'market[\s-]*cap\s*(?:above|over|>|>=)\s*(\d+(?:\.\d+)?)\s*(B|M|bn|mn)?',
        'min_market_cap_billions', 'gte')
    add(r'(?:mcap|mc)\s*(?:below|under|<|<=)\s*(\d+(?:\.\d+)?)\s*(B|M|bn|mn)?',
        'max_market_cap_billions', 'lte')
    add(r'(?:mcap|mc)\s*(?:above|over|>|>=)\s*(\d+(?:\.\d+)?)\s*(B|M|bn|mn)?',
        'min_market_cap_billions', 'gte')

    # ── Price ──
    add(r'price\s*(?:above|over|>|>=)\s*\$?\s*(\d+(?:\.\d+)?)',
        'min_price', 'gte')
    add(r'price\s*(?:below|under|<|<=)\s*\$?\s*(\d+(?:\.\d+)?)',
        'max_price', 'lte')

    # ── Dividend yield ──
    add(r'dividend\s*yield\s*(?:above|over|>|>=)\s*(\d+(?:\.\d+)?)\s*%?',
        'min_dividend_yield', 'gte')
    add(r'yield\s*(?:above|over|>|>=)\s*(\d+(?:\.\d+)?)\s*%?',
        'min_dividend_yield', 'gte')
    add(r'dividend\s*yield\s*(?:below|under|<|<=)\s*(\d+(?:\.\d+)?)\s*%?',
        'max_dividend_yield', 'lte')

    # ── Beta ──
    add(r'beta\s*(?:below|under|<|<=)\s*(\d+(?:\.\d+)?)',
        'max_beta', 'lte')
    add(r'beta\s*(?:above|over|>|>=)\s*(\d+(?:\.\d+)?)',
        'min_beta', 'gte')
    add(r'(?:low|minimum)\s+beta\s*(?:below|under|<|<=)?\s*(\d+(?:\.\d+)?)',
        'max_beta', 'lte')

    # ── EPS ──
    add(r'(?:eps|earnings?\s*per\s*share)\s*(?:above|over|>|>=)\s*\$?\s*(\d+(?:\.\d+)?)',
        'min_eps_ttm', 'gte')
    add(r'(?:eps|earnings?\s*per\s*share)\s*(?:below|under|<|<=)\s*\$?\s*(\d+(?:\.\d+)?)',
        'max_eps_ttm', 'lte')

    # ── Volume ──
    add(r'volume\s*(?:above|over|>|>=)\s*(\d+(?:\.\d+)?)\s*(M|K|B|million|thousand|billion)?',
        'min_volume', 'gte')

    # ── P/S ratio ──
    add(r'(?:p/?s|price[\s-]*to[\s-]*sales?)\s*(?:ratio)?\s*(?:below|under|<|<=)\s*(\d+(?:\.\d+)?)',
        'max_ps_ratio', 'lte')
    add(r'(?:p/?s|price[\s-]*to[\s-]*sales?)\s*(?:ratio)?\s*(?:above|over|>|>=)\s*(\d+(?:\.\d+)?)',
        'min_ps_ratio', 'gte')

    # ── P/B ratio ──
    add(r'(?:p/?b|price[\s-]*to[\s-]*book)\s*(?:ratio)?\s*(?:below|under|<|<=)\s*(\d+(?:\.\d+)?)',
        'max_pb_ratio', 'lte')

    # ── EV/EBITDA ──
    add(r'(?:ev/ebitda|ev\s*to\s*ebitda)\s*(?:below|under|<|<=)\s*(\d+(?:\.\d+)?)',
        'max_ev_ebitda', 'lte')

    # ── ROE ──
    add(r'(?:roe|return\s*on\s*equity)\s*(?:above|over|>|>=)\s*(\d+(?:\.\d+)?)\s*%?',
        'min_roe_ttm', 'gte')

    # ── ROA ──
    add(r'(?:roa|return\s*on\s*assets)\s*(?:above|over|>|>=)\s*(\d+(?:\.\d+)?)\s*%?',
        'min_roa_ttm', 'gte')

    # ── Net margin ──
    add(r'net\s*margin\s*(?:above|over|>|>=)\s*(\d+(?:\.\d+)?)\s*%?',
        'min_net_margin_ttm', 'gte')

    # ── Revenue growth ──
    add(r'revenue\s*growth\s*(?:above|over|>|>=)\s*(\d+(?:\.\d+)?)\s*%?',
        'min_revenue_growth_ttm', 'gte')

    # ── EPS growth ──
    add(r'eps\s*growth\s*(?:above|over|>|>=)\s*(\d+(?:\.\d+)?)\s*%?',
        'min_eps_growth_ttm', 'gte')

    return P

NUMERIC_PATTERNS = _build_numeric_patterns()

# ── Derived / boolean criteria patterns ─────────────────────────────────────
# Each tuple: (compiled_regex, derived_field, bool_value)
DERIVED_BOOLEAN_PATTERNS: List[Tuple[re.Pattern, str, bool]] = [
    (re.compile(r'golden\s*cross', re.IGNORECASE), 'golden_cross', True),
    (re.compile(r'death\s*cross', re.IGNORECASE), 'death_cross', True),
    (re.compile(r'(?:above|over)\s*(?:its\s*)?(?:the\s*)?(?:50|fifty)[\s-]*day\s*(?:moving\s*)?average', re.IGNORECASE), 'above_sma_50', True),
    (re.compile(r'(?:below|under)\s*(?:its\s*)?(?:the\s*)?(?:50|fifty)[\s-]*day\s*(?:moving\s*)?average', re.IGNORECASE), 'below_sma_50', True),
    (re.compile(r'(?:above|over)\s*(?:its\s*)?(?:the\s*)?(?:200|two[\s-]*hundred)[\s-]*day', re.IGNORECASE), 'above_sma_200', True),
    (re.compile(r'(?:below|under)\s*(?:its\s*)?(?:the\s*)?(?:200|two[\s-]*hundred)[\s-]*day', re.IGNORECASE), 'below_sma_200', True),
    (re.compile(r'(?:near|close\s*to|approaching|at)\s*(?:its\s*)?(?:the\s*)?52[\s-]*week\s*high', re.IGNORECASE), 'near_52w_high', True),
    (re.compile(r'(?:near|close\s*to|approaching|at)\s*(?:its\s*)?(?:the\s*)?52[\s-]*week\s*low', re.IGNORECASE), 'near_52w_low', True),
    (re.compile(r'52[\s-]*week\s*highs?', re.IGNORECASE), 'near_52w_high', True),
    (re.compile(r'52[\s-]*week\s*lows?', re.IGNORECASE), 'near_52w_low', True),
    (re.compile(r'strong\s*uptrend', re.IGNORECASE), 'golden_cross', True),
    (re.compile(r'(?:weakening|downtrend|declining\s*trend)', re.IGNORECASE), 'death_cross', True),
    (re.compile(r'macd\s*(?:bullish|positive|above\s*zero|above\s*signal)', re.IGNORECASE), 'macd_bullish', True),
    (re.compile(r'bollinger\s*breakout', re.IGNORECASE), 'bollinger_breakout', True),
    (re.compile(r'above\s*(?:the\s*)?upper\s*bollinger', re.IGNORECASE), 'bollinger_breakout', True),
    (re.compile(r'bollinger\s*oversold', re.IGNORECASE), 'bollinger_oversold', True),
    (re.compile(r'below\s*(?:the\s*)?lower\s*bollinger', re.IGNORECASE), 'bollinger_oversold', True),
]

# ── Momentum keywords ───────────────────────────────────────────────────────
MOMENTUM_BULLISH = re.compile(
    r'(?:bullish|rising\s*momentum|uptrend|gaining|surging|rallying|'
    r'momentum\s*up|positive\s*momentum|upward|up\s*trend|'
    r'price\s*(?:is\s*)?(?:rising|going\s*up|climbing))',
    re.IGNORECASE,
)
MOMENTUM_BEARISH = re.compile(
    r'(?:bearish|falling|downtrend|declining|dropping|weak|weakening|'
    r'momentum\s*down|negative\s*momentum|downward|down\s*trend|'
    r'price\s*(?:is\s*)?(?:falling|going\s*down|dropping))',
    re.IGNORECASE,
)

# ── Fuzzy indicators ────────────────────────────────────────────────────────
# Words that suggest the query needs LLM-level understanding (opinion,
# recommendation, vague descriptors).
FUZZY_INDICATORS = [
    "some", "interesting", "good", "best", "top", "great",
    "suggest", "recommend", "find me", "show me", "what about",
    "how about", "think", "feel", "opinion", "like",
    "any", "cool", "nice", "awesome", "worth", "should i",
    "compare", "versus", "vs",
]

# ── Unit suffix multiplier ─────────────────────────────────────────────────
UNIT_MULTIPLIERS = {
    "k": 1_000, "thousand": 1_000,
    "m": 1_000_000, "million": 1_000_000,
    "b": 1_000_000_000, "billion": 1_000_000_000,
    "bn": 1_000_000_000, "mn": 1_000_000,
}


def _parse_unit(value_str: str, unit: Optional[str]) -> float:
    """Convert a numeric string + optional unit suffix to a raw number."""
    try:
        base = float(value_str)
    except (TypeError, ValueError):
        return 0.0
    if unit:
        multiplier = UNIT_MULTIPLIERS.get(unit.lower(), 1.0)
        return base * multiplier
    return base


class DeterministicInterpreter:
    """Regex/dictionary NL parser.  Zero LLM calls, sub-millisecond latency.

    Usage::

        parser = DeterministicInterpreter()
        result, confidence = parser.parse("small cap tech stocks with RSI below 30")
        if confidence >= 0.6:
            return result  # fast path
        else:
            ...  # fall back to LLM
    """

    def parse(self, query: str) -> Tuple[InterpretedQuery, float]:
        """Parse a query deterministically.

        Returns:
            ``(InterpretedQuery, confidence)`` where confidence is 0.0–1.0.
            Confidence < ``CONFIDENCE_THRESHOLD`` means the caller should
            fall back to the LLM.
        """
        if not query or not query.strip():
            return InterpretedQuery(raw_input=query, filters=[]), 0.0

        query_lower = query.lower().strip()
        filters: List[FilterCriterion] = []
        confidence_parts: List[float] = []

        # ── 1. Sector ──
        sector, sector_conf = self._extract_sector(query_lower)
        confidence_parts.append(sector_conf)

        # ── 2. Industry ──
        industry, industry_conf = self._extract_industry(query_lower)
        confidence_parts.append(industry_conf)

        # ── 3. Market-cap presets ──
        mc_filters, mc_conf = self._extract_market_cap(query_lower)
        filters.extend(mc_filters)
        confidence_parts.append(mc_conf)

        # ── 4. Synonym-based criteria ──
        syn_filters, syn_conf = self._extract_synonyms(query_lower)
        filters.extend(syn_filters)
        confidence_parts.append(syn_conf)

        # ── 5. Explicit numeric criteria via regex ──
        num_filters, num_conf = self._extract_numeric(query_lower)
        filters.extend(num_filters)
        confidence_parts.append(num_conf)

        # ── 6. Derived / boolean criteria ──
        derived, derived_conf = self._extract_derived(query_lower)
        confidence_parts.append(derived_conf)

        # ── 7. Momentum ──
        momentum, mom_conf = self._extract_momentum(query_lower)
        confidence_parts.append(mom_conf)

        # ── Deduplicate across extraction paths ──
        filters = self._dedup_filters(filters)

        # ── Confidence ──
        confidence = self._calculate_confidence(confidence_parts, query_lower)

        result = InterpretedQuery(
            raw_input=query,
            filters=filters,
            sector=sector,
            industry=industry,
            momentum=momentum,
            **derived,
        )
        return result, confidence

    @staticmethod
    def _dedup_filters(filters: List[FilterCriterion]) -> List[FilterCriterion]:
        """Merge same-field + same-op filters, keeping the stricter value.

        For LTE: lower value is stricter.  For GTE: higher is stricter.
        Different operators on the same field are kept (e.g. min_pe + max_pe).
        """
        merged: List[FilterCriterion] = []
        for f in filters:
            existing = next(
                (m for m in merged if m.field == f.field and m.operator == f.operator),
                None,
            )
            if existing is None:
                merged.append(FilterCriterion(field=f.field, operator=f.operator, value=f.value))
            else:
                # Keep the stricter value
                if f.operator == ComparisonOp.LTE and f.value < existing.value:
                    existing.value = f.value
                elif f.operator == ComparisonOp.GTE and f.value > existing.value:
                    existing.value = f.value
        return merged

    # ── Sector extraction ───────────────────────────────────────────────

    def _extract_sector(self, query: str) -> Tuple[Optional[str], float]:
        """Extract canonical sector from query.  Returns (sector, confidence)."""
        # Check longest matches first (multi-word before single-word)
        for alias in sorted(SECTOR_ALIASES, key=len, reverse=True):
            if alias in query:
                return SECTOR_ALIASES[alias], 1.0
        return None, 0.0

    # ── Industry extraction ─────────────────────────────────────────────

    def _extract_industry(self, query: str) -> Tuple[Optional[str], float]:
        """Extract canonical industry from query."""
        for alias in sorted(INDUSTRY_ALIASES, key=len, reverse=True):
            if alias in query:
                return INDUSTRY_ALIASES[alias], 1.0
        return None, 0.0

    # ── Market-cap presets ──────────────────────────────────────────────

    def _extract_market_cap(
        self, query: str
    ) -> Tuple[List[FilterCriterion], float]:
        """Extract market-cap preset (small cap, large cap, etc.)."""
        filters: List[FilterCriterion] = []
        for preset in sorted(MARKET_CAP_PRESETS, key=len, reverse=True):
            if preset in query:
                min_b, max_b = MARKET_CAP_PRESETS[preset]
                if min_b is not None:
                    filters.append(FilterCriterion(
                        field="market_cap",
                        operator=ComparisonOp.GTE,
                        value=min_b,
                    ))
                if max_b is not None:
                    filters.append(FilterCriterion(
                        field="market_cap",
                        operator=ComparisonOp.LTE,
                        value=max_b,
                    ))
                return filters, 1.0
        return filters, 0.0

    # ── Synonym-based criteria ──────────────────────────────────────────

    def _extract_synonyms(
        self, query: str
    ) -> Tuple[List[FilterCriterion], float]:
        """Extract criteria from synonym mappings."""
        filters: List[FilterCriterion] = []
        matched = 0
        # Check longest matches first
        for synonym in sorted(SYNONYM_MAP, key=len, reverse=True):
            if synonym in query:
                matched += 1
                for raw_key, value in SYNONYM_MAP[synonym]:
                    # Convert to FilterCriterion using the same mapping
                    # the LLM path uses
                    db_field = self._raw_key_to_field(raw_key)
                    if db_field is None:
                        continue
                    op = ComparisonOp.LTE if raw_key.startswith("max_") else ComparisonOp.GTE
                    # Avoid duplicating criteria already extracted
                    if not any(f.field == db_field and f.operator == op for f in filters):
                        filters.append(FilterCriterion(
                            field=db_field,
                            operator=op,
                            value=value,
                        ))
        conf = min(1.0, matched * 0.8) if matched else 0.0
        return filters, conf

    @staticmethod
    def _raw_key_to_field(raw_key: str) -> Optional[str]:
        """Map LLM-style raw_key to DB field name."""
        mapping = {
            "max_market_cap_billions": "market_cap",
            "min_market_cap_billions": "market_cap",
            "max_pe_ratio": "pe_ratio",
            "min_pe_ratio": "pe_ratio",
            "max_rsi": "rsi_14",
            "min_rsi": "rsi_14",
            "min_price": "current_price",
            "max_price": "current_price",
            "min_volume": "volume",
            "min_avg_volume": "avg_volume",
            "max_avg_volume": "avg_volume",
            "min_eps_ttm": "eps_ttm",
            "max_eps_ttm": "eps_ttm",
            "min_dividend_yield": "dividend_yield",
            "max_dividend_yield": "dividend_yield",
            "max_beta": "beta",
            "min_beta": "beta",
            "min_price_change_pct": "price_change_pct",
            "max_price_change_pct": "price_change_pct",
            "min_ps_ratio": "ps_ratio",
            "max_ps_ratio": "ps_ratio",
            "min_pb_ratio": "pb_ratio",
            "max_pb_ratio": "pb_ratio",
            "max_ev_ebitda": "ev_ebitda",
            "min_roe_ttm": "roe_ttm",
            "min_roa_ttm": "roa_ttm",
            "min_gross_margin_ttm": "gross_margin_ttm",
            "min_net_margin_ttm": "net_margin_ttm",
            "min_revenue_growth_ttm": "revenue_growth_ttm",
            "min_eps_growth_ttm": "eps_growth_ttm",
            "max_atr_14": "atr_14",
        }
        return mapping.get(raw_key)

    # ── Explicit numeric criteria ───────────────────────────────────────

    def _extract_numeric(
        self, query: str
    ) -> Tuple[List[FilterCriterion], float]:
        """Extract numeric criteria via regex patterns."""
        filters: List[FilterCriterion] = []
        matched = 0

        for pattern, raw_key, op_str, fixed_value in NUMERIC_PATTERNS:
            m = pattern.search(query)
            if m:
                matched += 1
                if fixed_value is not None:
                    value = fixed_value
                else:
                    value_str = m.group(1)
                    unit = m.group(2) if m.lastindex and m.lastindex >= 2 else None
                    value = _parse_unit(value_str, unit)

                db_field = self._raw_key_to_field(raw_key)
                if db_field is None:
                    continue

                # Market cap: LLM speaks in billions, DB in dollars
                if db_field == "market_cap" and not unit:
                    value = value  # already in billions from raw_key name

                op = ComparisonOp(op_str)

                # Deduplicate same-field + same-op: keep the STRICTER value.
                # For LTE: lower value is stricter. For GTE: higher is stricter.
                existing_idx = next(
                    (i for i, f in enumerate(filters)
                     if f.field == db_field and f.operator == op),
                    None,
                )
                if existing_idx is not None:
                    existing_val = filters[existing_idx].value
                    if op == ComparisonOp.LTE:
                        # Lower value = stricter
                        if value < existing_val:
                            filters[existing_idx].value = value
                    elif op == ComparisonOp.GTE:
                        # Higher value = stricter
                        if value > existing_val:
                            filters[existing_idx].value = value
                else:
                    filters.append(FilterCriterion(
                        field=db_field,
                        operator=op,
                        value=value,
                    ))

        conf = min(1.0, matched * 0.6) if matched else 0.0
        return filters, conf

    # ── Derived / boolean criteria ──────────────────────────────────────

    def _extract_derived(
        self, query: str
    ) -> Tuple[Dict[str, Optional[bool]], float]:
        """Extract derived/boolean criteria (trend, crossover, proximity)."""
        derived: Dict[str, Optional[bool]] = {k: None for k in DERIVED_CRITERIA_KEYS}
        matched = 0

        for pattern, field, value in DERIVED_BOOLEAN_PATTERNS:
            if pattern.search(query):
                derived[field] = value
                matched += 1

        conf = min(1.0, matched * 0.5) if matched else 0.0
        return derived, conf

    # ── Momentum ────────────────────────────────────────────────────────

    def _extract_momentum(self, query: str) -> Tuple[Optional[str], float]:
        """Extract momentum direction from query."""
        bullish = bool(MOMENTUM_BULLISH.search(query))
        bearish = bool(MOMENTUM_BEARISH.search(query))

        if bullish and not bearish:
            return "bullish", 1.0
        if bearish and not bullish:
            return "bearish", 1.0
        if bullish and bearish:
            # Contradictory — neither
            return None, 0.5
        return None, 0.0

    # ── Confidence scoring ──────────────────────────────────────────────

    def _calculate_confidence(
        self, parts: List[float], query: str
    ) -> float:
        """Calculate overall confidence from per-component scores.

        Scoring logic:
          - No criteria at all → 0.0 (probably fuzzy / opinion query)
          - Strongest single component → high confidence if it's real
          - Multiple components → boost (compound query = more certain)
          - Fuzzy indicators → penalize ONLY when few criteria extracted
            (opinion-only queries like "show me some good stocks" need
             the LLM; but "find me some small cap tech stocks with RSI
             below 30" has enough concrete criteria to serve deterministically)
        """
        if not parts or all(p == 0 for p in parts):
            return 0.0

        non_zero = [p for p in parts if p > 0]
        if not non_zero:
            return 0.0

        max_conf = max(non_zero)
        n_components = len(non_zero)

        # Base: the strongest extraction determines confidence.
        if n_components >= 3:
            boosted = max(0.8, max_conf)
        elif n_components >= 2:
            boosted = max(0.7, max_conf)
        else:
            boosted = max(0.6, max_conf)

        # Penalize fuzzy indicators — but ONLY when we extracted few
        # concrete criteria.  Many criteria = the user's intent is clear
        # even if the framing is fuzzy.
        has_fuzzy = any(ind in query for ind in FUZZY_INDICATORS)
        if has_fuzzy and n_components <= 2:
            boosted *= 0.4

        # Clamp to [0, 1]
        return max(0.0, min(1.0, boosted))
