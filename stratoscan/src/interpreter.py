"""Natural Language Interpreter for Stratoscan.

Translates a user's plain-English investment idea into structured
screener filter criteria via the multi-provider LLM router
(src/llm_router.py) — providers, models and API keys all come from
``.env`` (Z.ai by default; GLM flash models are free).

Key design constraint: one LLM call per user query. Never call for
individual stocks.
"""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from src.cache import TTLCache
from src.config import settings

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

# (No module-level LLM_MODEL constant: the router picks the model per provider
#  from .env — see src/llm_router.py and the ZAI_MODEL / GEMINI_MODEL vars.)

# System prompt that instructs the LLM how to interpret queries
SYSTEM_PROMPT = """You are Stratoscan, an AI that converts plain-English stock investment ideas into structured JSON filter criteria.

Your task is to analyze the user's query and output a JSON object with filter fields. Only output valid JSON — no markdown, no explanation.

Available filter fields and their valid formats:
- "sector": string (e.g., "Technology", "Healthcare", "Finance", "Energy", "Consumer Cyclical", "Consumer Defensive", "Industrials", "Basic Materials", "Real Estate", "Utilities", "Communication Services")
- "industry": string (e.g., "Software", "Biotechnology", "Banks")
- "max_market_cap_billions": number (maximum market cap in billions of USD)
- "min_market_cap_billions": number (minimum market cap in billions)
- "max_pe_ratio": number
- "min_pe_ratio": number
- "max_rsi": number (0-100)
- "min_rsi": number (0-100)
- "min_price": number (minimum stock price in USD)
- "max_price": number (maximum stock price in USD)
- "min_volume": number (minimum daily trading volume)
- "min_avg_volume": number (minimum average daily volume)
- "max_avg_volume": number
- "min_eps_ttm": number (minimum trailing-twelve-month earnings per share)
- "min_dividend_yield": number (minimum dividend yield as percentage, e.g., 2.5 for 2.5%)
- "max_dividend_yield": number
- "max_beta": number
- "min_beta": number
- "min_price_change_pct": number (minimum daily change in percent)
- "max_price_change_pct": number
- "momentum": string ("bullish", "bearish", "neutral") — use this for trend/momentum descriptions
- "above_sma_50": boolean (true = price above its 50-day moving average — a real uptrend, not just a daily move)
- "below_sma_50": boolean (true = price below its 50-day moving average)
- "above_sma_200": boolean (true = price above its 200-day moving average — long-term uptrend)
- "below_sma_200": boolean (true = price below its 200-day moving average)
- "golden_cross": boolean (true = 50-day MA above 200-day MA — classic bull signal)
- "death_cross": boolean (true = 50-day MA below 200-day MA — classic bear signal)
- "near_52w_high": boolean (true = price within 5% of its 52-week high)
- "near_52w_low": boolean (true = price within 5% of its 52-week low)
- "min_ps_ratio": number (minimum price-to-sales ratio)
- "max_ps_ratio": number
- "min_pb_ratio": number (minimum price-to-book ratio)
- "max_pb_ratio": number
- "max_ev_ebitda": number (maximum enterprise-value/EBITDA)
- "min_roe_ttm": number (minimum return on equity, percentage)
- "min_roa_ttm": number (minimum return on assets, percentage)
- "min_gross_margin_ttm": number (minimum gross margin, percentage)
- "min_net_margin_ttm": number (minimum net margin, percentage)
- "min_revenue_growth_ttm": number (minimum year-over-year revenue growth, percentage)
- "min_eps_growth_ttm": number (minimum year-over-year EPS growth, percentage)
- "macd_bullish": boolean (true = MACD line above its signal line — positive momentum)
- "bollinger_breakout": boolean (true = price above the upper Bollinger band — strong momentum)
- "bollinger_oversold": boolean (true = price below the lower Bollinger band — oversold)
- "max_atr_14": number (maximum 14-day Average True Range as a percentage of price — low volatility)

Rules:
1. Only output a JSON object. No other text.
2. Infer as many fields as you reasonably can from the query.
3. If the user mentions "growth" or "growth stocks", set sector to "Technology" unless otherwise specified.
4. For "undervalued", set max_pe_ratio to 15 and include any relevant sector.
5. For "dividend" or "income", set min_dividend_yield to 3.0.
6. "Small cap" = min_market_cap_billions: 0.3, max_market_cap_billions: 2.0.
7. "Mid cap" = min_market_cap_billions: 2.0, max_market_cap_billions: 10.0.
8. "Large cap" = min_market_cap_billions: 10.0.
9. For "momentum" or "rising", set momentum to "bullish" AND above_sma_50 to true (real trend, not just a daily pop).
10. For "oversold", set max_rsi to 35.
11. For "overbought", set min_rsi to 70.
12. For "tech" or "technology", set sector to "Technology".
13. "Strong uptrend" = golden_cross: true. "Weakening/downtrend" = death_cross: true.
14. "Breakout" or "52-week highs" = near_52w_high: true.
15. "Bargain/cheap/value" (beyond P/E) = min_ps_ratio: 0 (or max_ps_ratio: 2) and max_ev_ebitda: 15 when plausible.
16. "Profitable" = min_roe_ttm: 10 and min_net_margin_ttm: 5.
17. If no clear criteria can be extracted, output an empty object {}.
18. Use common sense to fill in multiple fields — a "small-cap tech stock with momentum" should have sector, market cap bounds, and momentum all filled.

Examples:
Input: "find undervalued small-cap tech stocks with rising momentum"
Output: {"sector":"Technology","min_market_cap_billions":0.3,"max_market_cap_billions":2.0,"max_pe_ratio":15,"min_rsi":50,"momentum":"bullish","above_sma_50":true}

Input: "large cap dividend stocks"
Output: {"min_market_cap_billions":10.0,"min_dividend_yield":3.0}

Input: "stocks near their 52-week highs with strong momentum"
Output: {"near_52w_high":true,"golden_cross":true,"momentum":"bullish"}
"""

# ── Data Types ──────────────────────────────────────────────────────────────

# Boolean/derived criteria that map to column-to-column SQL expressions
# (trend, 52-week proximity, crossovers, volatility regimes) rather than a
# simple col-op-value filter row. SINGLE SOURCE OF TRUTH, shared by:
#   - _convert_to_filter_criteria: skip silently (they're SUPPORTED — the
#     "Unknown filter field" warning is reserved for fields the interpreter
#     genuinely doesn't know, and this tuple prevents a misleading WARNING
#     on every momentum/trend screen),
#   - interpret_query: extract the strict booleans,
#   - InterpretedQuery.has_derived_criteria: which criteria are "derived".
# Keeping one constant guarantees the three never drift out of sync.
DERIVED_CRITERIA_KEYS: tuple = (
    "above_sma_50", "below_sma_50", "above_sma_200", "below_sma_200",
    "golden_cross", "death_cross", "near_52w_high", "near_52w_low",
    "macd_bullish", "bollinger_breakout", "bollinger_oversold",
)


class ComparisonOp(str, Enum):
    """Comparison operators for filter criteria."""
    LT = "lt"
    LTE = "lte"
    GT = "gt"
    GTE = "gte"
    EQ = "eq"
    NEQ = "neq"
    IN = "in"


@dataclass
class FilterCriterion:
    """A single filter criterion parsed from the user's query."""

    field: str
    operator: ComparisonOp
    value: Any

    def to_db_filter(self) -> tuple:
        """Convert to (field, (operator, value)) tuple for the database."""
        return (self.field, (self.operator.value, self.value))


@dataclass
class InterpretedQuery:
    """The complete structured interpretation of a user query."""

    raw_input: str
    filters: List[FilterCriterion]
    sector: Optional[str] = None
    industry: Optional[str] = None
    momentum: Optional[str] = None
    # Derived/boolean criteria the LLM can express that map to
    # column-to-column comparisons (trend, 52-week proximity, crossovers,
    # volatility regimes). Each defaults to None ("not specified"); the
    # screener translates them into SQL expressions. Kept separate from
    # ``filters`` because they can't be expressed as a simple column op value.
    above_sma_50: Optional[bool] = None
    below_sma_50: Optional[bool] = None
    above_sma_200: Optional[bool] = None
    below_sma_200: Optional[bool] = None
    golden_cross: Optional[bool] = None
    death_cross: Optional[bool] = None
    near_52w_high: Optional[bool] = None
    near_52w_low: Optional[bool] = None
    macd_bullish: Optional[bool] = None
    bollinger_breakout: Optional[bool] = None
    bollinger_oversold: Optional[bool] = None
    # Set when the LLM call failed or returned unparseable JSON. When present,
    # ``filters`` is empty NOT because the query had no criteria, but because
    # the interpreter could not produce criteria — callers should surface this
    # to the user instead of silently returning the whole stock universe.
    error: Optional[str] = None

    @property
    def has_derived_criteria(self) -> bool:
        """True when any boolean/derived criterion is set."""
        return any(
            getattr(self, name) is not None
            for name in DERIVED_CRITERIA_KEYS
        )


def _parse_llm_response(response_json: str) -> Dict[str, Any]:
    """Parse the LLM's JSON response, handling common edge cases.

    Args:
        response_json: Raw JSON string from the LLM.

    Returns:
        Parsed dictionary of filter criteria.
    """
    # Strip markdown code fences if present
    cleaned = response_json.strip()
    if cleaned.startswith("```"):
        # Remove opening ```json or ``` and closing ```
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned[3:]
        cleaned = cleaned.rsplit("```", 1)[0].strip()

    return json.loads(cleaned)


def _convert_to_filter_criteria(parsed: Dict[str, Any]) -> List[FilterCriterion]:
    """Convert the LLM's parsed JSON into FilterCriterion objects.

    Args:
        parsed: Dictionary of filter criteria from the LLM.

    Returns:
        List of FilterCriterion objects.
    """
    filters: List[FilterCriterion] = []
    field_mapping: Dict[str, str] = {
        "sector": "sector",
        "industry": "industry",
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
        "min_dividend_yield": "dividend_yield",
        "max_dividend_yield": "dividend_yield",
        "max_beta": "beta",
        "min_beta": "beta",
        "min_price_change_pct": "price_change_pct",
        "max_price_change_pct": "price_change_pct",
        # Tier 2 valuation / profitability / growth (all optional data)
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

    for raw_key, value in parsed.items():
        # momentum and the derived criteria are handled separately (metadata
        # / column-to-column SQL expressions) — they are SUPPORTED fields, so
        # they must never trip the "Unknown filter field" warning, which is
        # for criteria the interpreter genuinely doesn't know.
        if raw_key == "momentum" or raw_key in DERIVED_CRITERIA_KEYS:
            continue  # Handled separately

        if raw_key not in field_mapping:
            logger.warning("Unknown filter field: %s", raw_key)
            continue

        db_field = field_mapping[raw_key]

        if raw_key.startswith("max_"):
            operator = ComparisonOp.LTE
        elif raw_key.startswith("min_"):
            operator = ComparisonOp.GTE
        else:
            operator = ComparisonOp.EQ

        filters.append(FilterCriterion(
            field=db_field,
            operator=operator,
            value=value,
        ))

    return filters


# Process-wide cache of LLM interpretations (query -> InterpretedQuery).
# The LLM call is the expensive part of every screen; identical queries
# never burn a free LLM call again within the TTL. Skipped entirely when
# settings.cache_enabled is False (tests / debug).
_interpret_cache = TTLCache(
    ttl_seconds=settings.interpret_cache_ttl,
    max_entries=settings.interpret_cache_max_entries,
    name="interpret",
)


def _interpret_cache_key(query: str) -> str:
    """Normalize a query for caching (case + whitespace insensitive)."""
    return " ".join(query.strip().lower().split())


def interpret_query(query: str) -> InterpretedQuery:
    """Translate a natural-language stock query into structured filter criteria.

    Cost: At most one LLM call per unique query — results are cached
    (see settings.interpret_cache_ttl, default 24h). Never called per-stock.

    Args:
        query: A plain-English stock screening request from the user.

    Returns:
        An InterpretedQuery object with parsed filters.

    Raises:
        RuntimeError: If the LLM call fails or returns unparseable JSON.
    """
    if not query or not query.strip():
        return InterpretedQuery(raw_input=query, filters=[])

    key = _interpret_cache_key(query)
    if settings.cache_enabled:
        cached = _interpret_cache.get(key)
        if cached is not None:
            logger.debug("Interpret cache hit for '%s'", query)
            # Return a COPY: InterpretedQuery.filters is a mutable list, and
            # callers (backtest, enrich, future features) must never be able
            # to mutate the shared cache entry.
            return copy.deepcopy(cached)

    # ── Hybrid path: try deterministic parser first ────────────────────
    # The deterministic parser handles ~80% of queries (exact criteria
    # like "RSI below 30", "small cap tech stocks") in <1ms with zero
    # cost.  Fuzzy queries ("show me some interesting stocks") score low
    # confidence and fall through to the LLM.
    if getattr(settings, 'use_deterministic_interpreter', True):
        try:
            from src.deterministic_interpreter import (
                DeterministicInterpreter,
                CONFIDENCE_THRESHOLD,
            )
            det = DeterministicInterpreter()
            det_result, det_conf = det.parse(query)
            if det_conf >= CONFIDENCE_THRESHOLD:
                logger.info(
                    "Deterministic parse (conf=%.2f) for '%s': %d filters",
                    det_conf, query, len(det_result.filters),
                )
                if settings.cache_enabled:
                    _interpret_cache.set(key, copy.deepcopy(det_result))
                return det_result
            logger.debug(
                "Deterministic confidence %.2f < %.2f, LLM fallback for '%s'",
                det_conf, CONFIDENCE_THRESHOLD, query,
            )
        except Exception as e:
            logger.debug("Deterministic parser error, LLM fallback: %s", e)
    # ── End hybrid path ───────────────────────────────────────────────

    try:
        # Route through the multi-provider router: tries providers in priority
        # order and fails over instantly on 429/5xx/dead keys, so a single
        # provider's rate limit or outage never blocks a screen.
        from src.llm_router import get_router

        response = get_router().chat_completion(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ],
            temperature=0.1,  # Low temperature for consistent JSON output
            # Budget must cover reasoning tokens: GLM flash models (the free
            # defaults) spend ~300-400 tokens thinking before emitting JSON.
            # With max_tokens=300 the model was truncated at finish_reason
            # "length" with empty content -> empty filters. 2000 gives ample
            # headroom while staying cheap (a screen costs <$0.001).
            max_tokens=2000,
        )

        raw_output = response.choices[0].message.content or "{}"
        parsed = _parse_llm_response(raw_output)

        logger.debug("LLM response for '%s': %s", query, parsed)

        filters = _convert_to_filter_criteria(parsed)

        # Extract metadata fields
        sector = parsed.get("sector")
        industry = parsed.get("industry")
        momentum = parsed.get("momentum")

        # Derived/boolean criteria (trend, proximity, crossovers). Only accept
        # real booleans — a malformed "truthy" string must not silently flip a
        # filter on. Keys come from the shared DERIVED_CRITERIA_KEYS constant.
        derived = {k: (parsed.get(k) if isinstance(parsed.get(k), bool) else None)
                   for k in DERIVED_CRITERIA_KEYS}

        result = InterpretedQuery(
            raw_input=query,
            filters=filters,
            sector=sector,
            industry=industry,
            momentum=momentum,
            **derived,
        )
        if settings.cache_enabled:
            # Store a copy so the instance handed to the FIRST caller can't
            # mutate what future cache hits receive.
            _interpret_cache.set(key, copy.deepcopy(result))
        return result

    except json.JSONDecodeError as e:
        logger.error("Failed to parse LLM response as JSON: %s", e)
        logger.debug("Raw LLM output: %s", raw_output if 'raw_output' in dir() else "N/A")
        return InterpretedQuery(
            raw_input=query,
            filters=[],
            error=f"LLM returned unparseable JSON: {e}",
        )
    except Exception as e:
        logger.error("LLM interpretation failed: %s", e)
        return InterpretedQuery(
            raw_input=query,
            filters=[],
            error=f"LLM call failed: {e}",
        )


def format_filters_for_display(query: InterpretedQuery) -> str:
    """Format the interpreted filters into a human-readable summary.

    Args:
        query: The interpreted query object.

    Returns:
        A readable string summarizing what the system will search for.
    """
    parts: List[str] = []

    if query.sector:
        parts.append(f"Sector: {query.sector}")
    if query.industry:
        parts.append(f"Industry: {query.industry}")
    if query.momentum:
        parts.append(f"Momentum: {query.momentum}")

    derived_labels = [
        ("above_sma_50", "price > SMA50"),
        ("below_sma_50", "price < SMA50"),
        ("above_sma_200", "price > SMA200"),
        ("below_sma_200", "price < SMA200"),
        ("golden_cross", "SMA50 > SMA200 (golden cross)"),
        ("death_cross", "SMA50 < SMA200 (death cross)"),
        ("near_52w_high", "within 5% of 52w high"),
        ("near_52w_low", "within 5% of 52w low"),
        ("macd_bullish", "MACD > signal"),
        ("bollinger_breakout", "price > upper Bollinger"),
        ("bollinger_oversold", "price < lower Bollinger"),
    ]
    for attr, label in derived_labels:
        if getattr(query, attr):
            parts.append(label)

    for f in query.filters:
        op_symbol = {
            "lt": "<",
            "lte": "≤",
            "gt": ">",
            "gte": "≥",
            "eq": "=",
            "neq": "≠",
        }.get(f.operator.value, f.operator.value)
        parts.append(f"{f.field} {op_symbol} {f.value}")

    if parts:
        return ", ".join(parts)
    if query.error:
        return f"⚠️  Could not interpret query: {query.error}"
    return "No specific filters — returning all stocks."
