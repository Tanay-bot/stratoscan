"""Hermetic tests for the deterministic NL interpreter — no network, no LLM.

Run: python -m tests.test_deterministic_interpreter

Covers every extraction path in DeterministicInterpreter and verifies
that the deterministic parser produces equivalent InterpretedQuery output
to what the LLM would produce for the same query.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.deterministic_interpreter import (  # noqa: E402
    CONFIDENCE_THRESHOLD,
    DeterministicInterpreter,
    SECTOR_ALIASES,
    MARKET_CAP_PRESETS,
    SYNONYM_MAP,
)
from src.interpreter import (  # noqa: E402
    ComparisonOp,
    InterpretedQuery,
)

parser = DeterministicInterpreter()


def _filters_by_field(result: InterpretedQuery) -> dict:
    """Index filters by (field, operator) for easy assertions."""
    return {(f.field, f.operator.value): f.value for f in result.filters}


# ── 1. Empty / null queries ────────────────────────────────────────────────
print("1. Empty queries...")

result, conf = parser.parse("")
assert conf == 0.0
assert result.filters == []

result, conf = parser.parse("   ")
assert conf == 0.0
assert result.filters == []

result, conf = parser.parse(None)
assert conf == 0.0
assert result.filters == []

print("   ✅ Empty/null → conf=0.0, no filters")


# ── 2. Sector extraction ───────────────────────────────────────────────────
print("2. Sector extraction...")

result, conf = parser.parse("tech stocks")
assert result.sector == "Technology"
assert conf >= 0.6

result, conf = parser.parse("healthcare companies")
assert result.sector == "Healthcare"

result, conf = parser.parse("oil and gas stocks")
assert result.sector == "Energy"

result, conf = parser.parse("reits")
assert result.sector == "Real Estate"

result, conf = parser.parse("banking sector")
assert result.sector == "Financial Services"

result, conf = parser.parse("some random query about nothing")
assert result.sector is None

print("   ✅ Sectors extracted correctly")


# ── 3. Industry extraction ────────────────────────────────────────────────
print("3. Industry extraction...")

result, conf = parser.parse("software stocks")
assert result.industry == "Software"

result, conf = parser.parse("biotech companies")
assert result.industry == "Biotechnology"

result, conf = parser.parse("semiconductor stocks")
assert result.industry == "Semiconductors"

result, conf = parser.parse("SaaS companies")
assert result.industry == "Software"

print("   ✅ Industries extracted correctly")


# ── 4. Market cap presets ─────────────────────────────────────────────────
print("4. Market cap presets...")

result, conf = parser.parse("small cap stocks")
by_field = _filters_by_field(result)
assert ("market_cap", "gte") in by_field
assert by_field[("market_cap", "gte")] == 0.3
assert ("market_cap", "lte") in by_field
assert by_field[("market_cap", "lte")] == 2.0
assert conf >= 0.6

result, conf = parser.parse("large cap companies")
by_field = _filters_by_field(result)
assert ("market_cap", "gte") in by_field
assert by_field[("market_cap", "gte")] == 10.0

result, conf = parser.parse("mega cap stocks")
by_field = _filters_by_field(result)
assert ("market_cap", "gte") in by_field
assert by_field[("market_cap", "gte")] == 200.0

result, conf = parser.parse("mid cap")
by_field = _filters_by_field(result)
assert ("market_cap", "gte") in by_field
assert by_field[("market_cap", "gte")] == 2.0
assert ("market_cap", "lte") in by_field
assert by_field[("market_cap", "lte")] == 10.0

print("   ✅ Market cap presets work correctly")


# ── 5. Synonym mapping ───────────────────────────────────────────────────
print("5. Synonym mapping...")

result, conf = parser.parse("undervalued stocks")
by_field = _filters_by_field(result)
assert ("pe_ratio", "lte") in by_field
assert by_field[("pe_ratio", "lte")] == 15
assert conf >= 0.6

result, conf = parser.parse("dividend stocks")
by_field = _filters_by_field(result)
assert ("dividend_yield", "gte") in by_field
assert by_field[("dividend_yield", "gte")] == 3.0
assert conf >= 0.6

result, conf = parser.parse("growth stocks")
by_field = _filters_by_field(result)
assert ("revenue_growth_ttm", "gte") in by_field
assert by_field[("revenue_growth_ttm", "gte")] == 15

result, conf = parser.parse("profitable companies")
by_field = _filters_by_field(result)
assert ("roe_ttm", "gte") in by_field
assert by_field[("roe_ttm", "gte")] == 10

result, conf = parser.parse("low risk stocks")
by_field = _filters_by_field(result)
assert ("beta", "lte") in by_field
assert by_field[("beta", "lte")] == 0.7

result, conf = parser.parse("high volume stocks")
by_field = _filters_by_field(result)
assert ("avg_volume", "gte") in by_field
assert by_field[("avg_volume", "gte")] == 1_000_000

result, conf = parser.parse("blue chip companies")
by_field = _filters_by_field(result)
assert ("market_cap", "gte") in by_field
assert by_field[("market_cap", "gte")] == 100

print("   ✅ Synonyms map to correct filters")


# ── 6. Explicit numeric criteria (regex) ───────────────────────────────────
print("6. Explicit numeric criteria...")

result, conf = parser.parse("RSI below 30")
by_field = _filters_by_field(result)
assert ("rsi_14", "lte") in by_field
assert by_field[("rsi_14", "lte")] == 30.0
assert conf >= 0.6

result, conf = parser.parse("RSI above 70")
by_field = _filters_by_field(result)
assert ("rsi_14", "gte") in by_field
assert by_field[("rsi_14", "gte")] == 70.0

result, conf = parser.parse("P/E under 15")
by_field = _filters_by_field(result)
assert ("pe_ratio", "lte") in by_field
assert by_field[("pe_ratio", "lte")] == 15.0

result, conf = parser.parse("price to earnings above 25")
by_field = _filters_by_field(result)
assert ("pe_ratio", "gte") in by_field
assert by_field[("pe_ratio", "gte")] == 25.0

result, conf = parser.parse("market cap under 2B")
by_field = _filters_by_field(result)
assert ("market_cap", "lte") in by_field

result, conf = parser.parse("price above $100")
by_field = _filters_by_field(result)
assert ("current_price", "gte") in by_field
assert by_field[("current_price", "gte")] == 100.0

result, conf = parser.parse("price under $50")
by_field = _filters_by_field(result)
assert ("current_price", "lte") in by_field
assert by_field[("current_price", "lte")] == 50.0

result, conf = parser.parse("dividend yield above 3%")
by_field = _filters_by_field(result)
assert ("dividend_yield", "gte") in by_field
assert by_field[("dividend_yield", "gte")] == 3.0

result, conf = parser.parse("beta below 1")
by_field = _filters_by_field(result)
assert ("beta", "lte") in by_field
assert by_field[("beta", "lte")] == 1.0

result, conf = parser.parse("EPS above $5")
by_field = _filters_by_field(result)
assert ("eps_ttm", "gte") in by_field
assert by_field[("eps_ttm", "gte")] == 5.0

result, conf = parser.parse("volume above 1M")
by_field = _filters_by_field(result)
assert ("volume", "gte") in by_field
assert by_field[("volume", "gte")] == 1_000_000

result, conf = parser.parse("ROE above 15%")
by_field = _filters_by_field(result)
assert ("roe_ttm", "gte") in by_field
assert by_field[("roe_ttm", "gte")] == 15.0

result, conf = parser.parse("net margin above 10%")
by_field = _filters_by_field(result)
assert ("net_margin_ttm", "gte") in by_field
assert by_field[("net_margin_ttm", "gte")] == 10.0

result, conf = parser.parse("EV/EBITDA under 15")
by_field = _filters_by_field(result)
assert ("ev_ebitda", "lte") in by_field
assert by_field[("ev_ebitda", "lte")] == 15.0

print("   ✅ Regex numeric criteria work correctly")


# ── 7. Derived / boolean criteria ──────────────────────────────────────────
print("7. Derived / boolean criteria...")

result, conf = parser.parse("golden cross stocks")
assert result.golden_cross is True
assert conf >= 0.6

result, conf = parser.parse("death cross stocks")
assert result.death_cross is True

result, conf = parser.parse("above 50-day moving average")
assert result.above_sma_50 is True

result, conf = parser.parse("below 200-day moving average")
assert result.below_sma_200 is True

result, conf = parser.parse("near 52-week high")
assert result.near_52w_high is True

result, conf = parser.parse("52 week lows")
assert result.near_52w_low is True

result, conf = parser.parse("strong uptrend")
assert result.golden_cross is True

result, conf = parser.parse("MACD bullish")
assert result.macd_bullish is True

result, conf = parser.parse("bollinger breakout")
assert result.bollinger_breakout is True

result, conf = parser.parse("bollinger oversold")
assert result.bollinger_oversold is True

print("   ✅ Derived/boolean criteria extracted correctly")


# ── 8. Momentum extraction ────────────────────────────────────────────────
print("8. Momentum extraction...")

result, conf = parser.parse("rising momentum stocks")
assert result.momentum == "bullish"
assert conf >= 0.6

result, conf = parser.parse("bullish stocks")
assert result.momentum == "bullish"

result, conf = parser.parse("falling stocks")
assert result.momentum == "bearish"

result, conf = parser.parse("bearish stocks")
assert result.momentum == "bearish"

result, conf = parser.parse("price is rising")
assert result.momentum == "bullish"

result, conf = parser.parse("declining trend")
assert result.momentum == "bearish"

print("   ✅ Momentum extracted correctly")


# ── 9. Combined / compound queries ────────────────────────────────────────
print("9. Combined queries...")

result, conf = parser.parse("small cap tech stocks with RSI below 30 and golden cross")
assert result.sector == "Technology"
by_field = _filters_by_field(result)
assert ("market_cap", "gte") in by_field
assert ("market_cap", "lte") in by_field
assert ("rsi_14", "lte") in by_field
assert result.golden_cross is True
assert conf >= 0.6

result, conf = parser.parse("large cap dividend stocks with low beta")
by_field = _filters_by_field(result)
assert ("market_cap", "gte") in by_field
assert by_field[("market_cap", "gte")] == 10.0
assert ("dividend_yield", "gte") in by_field
assert ("beta", "lte") in by_field
assert by_field[("beta", "lte")] == 1.0
assert conf >= 0.6

result, conf = parser.parse("undervalued small-cap tech stocks with rising momentum")
assert result.sector == "Technology"
by_field = _filters_by_field(result)
assert ("pe_ratio", "lte") in by_field
assert ("market_cap", "gte") in by_field
assert ("market_cap", "lte") in by_field
assert result.momentum == "bullish"
assert conf >= 0.6

result, conf = parser.parse("oversold healthcare stocks near 52-week lows")
assert result.sector == "Healthcare"
by_field = _filters_by_field(result)
assert ("rsi_14", "lte") in by_field
assert result.near_52w_low is True
assert conf >= 0.6

print("   ✅ Compound queries extract all criteria")


# ── 10. Confidence scoring ────────────────────────────────────────────────
print("10. Confidence scoring...")

# Exact queries → high confidence
_, conf = parser.parse("small cap tech stocks with RSI below 30")
assert conf >= CONFIDENCE_THRESHOLD, f"Expected >= {CONFIDENCE_THRESHOLD}, got {conf}"

_, conf = parser.parse("large cap dividend stocks with P/E under 15")
assert conf >= CONFIDENCE_THRESHOLD

# Fuzzy / opinion queries → low confidence
_, conf = parser.parse("show me some interesting stocks")
assert conf < CONFIDENCE_THRESHOLD, f"Expected < {CONFIDENCE_THRESHOLD}, got {conf}"

_, conf = parser.parse("what are the best stocks to buy")
assert conf < CONFIDENCE_THRESHOLD

_, conf = parser.parse("find me some good stocks")
assert conf < CONFIDENCE_THRESHOLD

_, conf = parser.parse("think about AI stocks")
assert conf < CONFIDENCE_THRESHOLD

# Gibberish → zero confidence
_, conf = parser.parse("asdkjfhaskdjfh")
assert conf == 0.0

print("   ✅ Confidence scoring works correctly")


# ── 11. Edge cases ────────────────────────────────────────────────────────
print("11. Edge cases...")

# Very long query
long_query = "find me some really great small cap tech stocks that are undervalued with RSI below 30 and golden cross and above 50-day moving average near 52-week highs with high volume and strong uptrend and MACD bullish"
result, conf = parser.parse(long_query)
assert result.sector == "Technology"
assert conf >= 0.6

# Numbers only
result, conf = parser.parse("30")
assert conf == 0.0

# Special characters
result, conf = parser.parse("RSI < 30 & P/E < 15")
by_field = _filters_by_field(result)
assert ("rsi_14", "lte") in by_field
assert ("pe_ratio", "lte") in by_field

# Mixed case
result, conf = parser.parse("SMALL CAP TECH STOCKS WITH RSI BELOW 30")
assert result.sector == "Technology"
by_field = _filters_by_field(result)
assert ("market_cap", "gte") in by_field
assert ("rsi_14", "lte") in by_field

print("   ✅ Edge cases handled correctly")


# ── 12. Output equivalence with LLM-style output ──────────────────────────
print("12. Output equivalence check...")

# The deterministic parser should produce the same field names and operators
# that the LLM interpreter's _convert_to_filter_criteria would produce.
result, _ = parser.parse("small cap tech stocks with RSI below 30 and dividend yield above 3%")
by_field = _filters_by_field(result)

# Verify field names match what the screener expects
expected_fields = {
    ("market_cap", "gte"),    # small cap min
    ("market_cap", "lte"),    # small cap max
    ("rsi_14", "lte"),        # RSI below 30
    ("dividend_yield", "gte"), # dividend yield above 3%
}
actual_fields = set(by_field.keys())
assert expected_fields == actual_fields, f"Mismatch: {expected_fields ^ actual_fields}"

# Verify sector
assert result.sector == "Technology"

# Verify raw_input is preserved
assert result.raw_input == "small cap tech stocks with RSI below 30 and dividend yield above 3%"

print("   ✅ Output matches expected LLM-style format")


# ── 13. No duplicate filters + stricter value kept ────────────────
print("13. No duplicate filters...")

result, _ = parser.parse("RSI below 30 and oversold")
by_field = _filters_by_field(result)
rsi_filters = [f for f in result.filters if f.field == "rsi_14"]
assert len(rsi_filters) == 1, f"Expected 1 RSI filter, got {len(rsi_filters)}"
# Stricter value (30) should win over default oversold (35)
assert by_field[("rsi_14", "lte")] == 30.0

result, _ = parser.parse("RSI below 50 and oversold")
by_field = _filters_by_field(result)
# Oversold (35) is stricter than explicit 50
assert by_field[("rsi_14", "lte")] == 35.0

print("   ✅ No duplicate filters, stricter value wins")


# ── Summary ────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("ALL 13 TEST GROUPS PASSED ✅")
print("=" * 60)
