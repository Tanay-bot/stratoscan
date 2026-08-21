"""Hermetic tests for the natural-language interpreter — no network, no live LLM.

Run: python -m tests.test_interpreter

Covers the Layer 1 interpreter that had ZERO direct test coverage:

1. _parse_llm_response: plain JSON, markdown-fenced JSON, unparseable -> raises.
2. _convert_to_filter_criteria: max_ -> LTE, min_ -> GTE, plain -> EQ, unknown
   fields skipped, momentum skipped (metadata, not a filter).
3. interpret_query happy path: filters + sector/momentum metadata + derived
   boolean criteria (above_sma_50 etc.), non-boolean truthy values rejected.
4. interpret_query error paths: LLM exception -> error field, unparseable JSON
   -> error field, empty query -> no LLM call at all.
5. Interpret cache: cache hit returns a DEEP COPY (mutating a caller's filters
   must not corrupt the shared cache entry), cache disabled bypasses the cache.
6. format_filters_for_display: filters, derived criteria, error, empty.
"""

from __future__ import annotations

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest import mock  # noqa: E402

from src import interpreter as interp  # noqa: E402


def _mk_llm(content: str):
    """Build a fake router whose chat_completion returns the given content.

    MagicMock (not Mock): the interpreter reads ``response.choices[0].message``
    — ``[0]`` requires __getitem__, which only MagicMock implements.
    """
    router = mock.MagicMock()
    router.chat_completion.return_value.choices[0].message.content = content
    return router


# ── 1. LLM response parsing ────────────────────────────────────────────────
print("1. _parse_llm_response...")

assert interp._parse_llm_response('{"a": 1}') == {"a": 1}
# Markdown fences (both ```json and plain ```)
assert interp._parse_llm_response('```json\n{"a": 2}\n```') == {"a": 2}
assert interp._parse_llm_response('```\n{"a": 3}\n```') == {"a": 3}
# Trailing text after a fence is dropped, not parsed
assert interp._parse_llm_response('```json\n{"a": 4}\n``` here is extra') == {"a": 4}
# Garbage raises JSONDecodeError (callers handle it)
try:
    interp._parse_llm_response("not json at all")
    raise AssertionError("garbage must raise JSONDecodeError")
except json.JSONDecodeError:
    pass
print("✅ plain JSON, fences stripped, garbage raises JSONDecodeError")


# ── 2. Filter conversion ────────────────────────────────────────────────────
print("2. _convert_to_filter_criteria...")

filters = interp._convert_to_filter_criteria(
    {"max_pe_ratio": 15, "min_market_cap_billions": 0.3, "sector": "Technology"}
)
by_field = {f.field: f for f in filters}
assert by_field["pe_ratio"].operator == interp.ComparisonOp.LTE
assert by_field["pe_ratio"].value == 15
assert by_field["market_cap"].operator == interp.ComparisonOp.GTE
assert by_field["market_cap"].value == 0.3
assert by_field["sector"].operator == interp.ComparisonOp.EQ
assert by_field["sector"].value == "Technology"

# to_db_filter contract used by the screener
assert by_field["pe_ratio"].to_db_filter() == ("pe_ratio", ("lte", 15))

# momentum is metadata — never a filter row
assert interp._convert_to_filter_criteria({"momentum": "bullish"}) == []
# unknown fields are skipped with a warning, never crash
assert interp._convert_to_filter_criteria({"bogus_field": 1}) == []
print("✅ max->LTE, min->GTE, eq, to_db_filter, momentum/unknown skipped")


# ── 2b. Derived criteria must NOT warn as "unknown" (root-cause regression) ─
print("2b. derived-key silence...")

# Derived criteria (above_sma_50, golden_cross, ...) are SUPPORTED — they
# are extracted separately by interpret_query via DERIVED_CRITERIA_KEYS.
# They must never be logged as "Unknown filter field" (that warning is
# reserved for fields the interpreter genuinely doesn't know).
DERIVED_SAMPLE = {
    "above_sma_50": True, "below_sma_200": True, "golden_cross": True,
    "death_cross": False, "near_52w_high": True, "macd_bullish": True,
    "bollinger_breakout": True, "bollinger_oversold": False,
}
with mock.patch("src.interpreter.logger") as m_log:
    out = interp._convert_to_filter_criteria(DERIVED_SAMPLE)
assert out == [], "derived criteria are not simple col-op-value filters"
unknown_warns = [
    c for c in m_log.warning.call_args_list
    if c.args and c.args[0] == "Unknown filter field: %s"
]
assert unknown_warns == [], \
    f"derived keys must NOT warn as unknown: {unknown_warns}"

# A truly unknown field MUST still warn (that is the monitoring signal).
with mock.patch("src.interpreter.logger") as m_log2:
    interp._convert_to_filter_criteria({"flying_pig_score": 42})
unknown_warns2 = [
    c for c in m_log2.warning.call_args_list
    if c.args and c.args[0] == "Unknown filter field: %s"
]
assert len(unknown_warns2) == 1 and unknown_warns2[0].args[1] == "flying_pig_score", \
    "a genuinely unknown field must still be flagged"
print("✅ derived keys silent; truly-unknown fields still warn")


# ── 3. interpret_query happy path ───────────────────────────────────────────
print("3. interpret_query happy path...")

router = _mk_llm(
    '{"sector":"Technology","max_pe_ratio":15,"momentum":"bullish",'
    '"above_sma_50":true,"golden_cross":false}'
)
interp._interpret_cache.clear()
with mock.patch("src.llm_router.get_router", return_value=router), mock.patch.object(
    interp.settings, "cache_enabled", False
):
    q = interp.interpret_query("undervalued tech momentum")
assert q.raw_input == "undervalued tech momentum"
assert q.sector == "Technology"
assert q.momentum == "bullish"
assert q.above_sma_50 is True
assert q.golden_cross is False  # explicitly false is preserved
assert q.error is None
pe = [f for f in q.filters if f.field == "pe_ratio"]
assert len(pe) == 1 and pe[0].value == 15

# Non-boolean truthy strings must NOT flip a derived criterion on
router2 = _mk_llm('{"above_sma_50":"true","bollinger_breakout":1}')
with mock.patch("src.llm_router.get_router", return_value=router2), mock.patch.object(
    interp.settings, "cache_enabled", False
):
    q2 = interp.interpret_query("some query")
assert q2.above_sma_50 is None
assert q2.bollinger_breakout is None
assert not q2.has_derived_criteria

# has_derived_criteria True when a real boolean is set
q3 = interp.InterpretedQuery(raw_input="q", filters=[], above_sma_50=True)
assert q3.has_derived_criteria
print("✅ filters + metadata + strict boolean derived criteria")


# ── 4. Error paths + empty query ────────────────────────────────────────────
print("4. interpret_query error paths...")

interp._interpret_cache.clear()
# LLM call itself raises -> error field, empty filters
router_err = mock.Mock()
router_err.chat_completion.side_effect = RuntimeError("provider down")
with mock.patch("src.llm_router.get_router", return_value=router_err), mock.patch.object(
    interp.settings, "cache_enabled", False
):
    qe = interp.interpret_query("anything")
assert qe.filters == [] and qe.error is not None and "LLM call failed" in qe.error

# LLM returns unparseable JSON -> error field
with mock.patch(
    "src.llm_router.get_router", return_value=_mk_llm("totally not json")
), mock.patch.object(interp.settings, "cache_enabled", False):
    qe2 = interp.interpret_query("anything 2")
assert qe2.filters == [] and qe2.error is not None and "unparseable" in qe2.error

# Empty/whitespace query -> empty interpretation, NO LLM call
router_never = mock.Mock()
with mock.patch("src.llm_router.get_router", return_value=router_never), mock.patch.object(
    interp.settings, "cache_enabled", True
):
    qe3 = interp.interpret_query("   ")
assert qe3.filters == [] and qe3.error is None
assert not router_never.chat_completion.called, "empty query must not call the LLM"
print("✅ LLM failure + unparseable JSON -> error; empty query never calls LLM")


# ── 5. Cache: hits return deep copies, never shared mutable state ───────────
print("5. interpret cache semantics...")

interp._interpret_cache.clear()
router3 = _mk_llm('{"sector":"Healthcare","max_rsi":35}')
with mock.patch("src.llm_router.get_router", return_value=router3), mock.patch.object(
    interp.settings, "cache_enabled", True
):
    c1 = interp.interpret_query("Oversold Healthcare stocks")
    c2 = interp.interpret_query("Oversold Healthcare stocks")  # cache hit
    # Mutate the first caller's result — must not corrupt the cache entry
    c1.filters.append(
        interp.FilterCriterion("sector", interp.ComparisonOp.EQ, "Hacked")
    )
    c1.sector = "Corrupted"
    c3 = interp.interpret_query("Oversold Healthcare stocks")
# {"sector":..., "max_rsi":...} -> sector EQ + rsi_14 LTE = 2 filters
assert len(c2.filters) == 2, "second hit should equal first interpretation"
assert len(c3.filters) == 2, "cache entry must be a deep copy, not shared"
assert c3.sector == "Healthcare"
assert all(f.field != "sector" or f.value != "Hacked" for f in c3.filters)
assert router3.chat_completion.call_count == 1, "cache hit must not re-call the LLM"

# Cache disabled -> every call hits the LLM
interp._interpret_cache.clear()
with mock.patch(
    "src.llm_router.get_router", return_value=_mk_llm('{"sector":"Energy"}')
), mock.patch.object(interp.settings, "cache_enabled", False):
    interp.interpret_query("energy")
    interp.interpret_query("energy")
assert router3.chat_completion.call_count == 1  # untouched from before
print("✅ cache hit deep-copies; cache disabled never caches")


# ── 6. Display formatting ───────────────────────────────────────────────────
print("6. format_filters_for_display...")

qd = interp.InterpretedQuery(
    raw_input="q",
    filters=[interp.FilterCriterion("pe_ratio", interp.ComparisonOp.LTE, 15)],
    sector="Technology",
    momentum="bullish",
    above_sma_50=True,
)
out = interp.format_filters_for_display(qd)
assert "Sector: Technology" in out and "Momentum: bullish" in out
assert "price > SMA50" in out and "pe_ratio ≤ 15" in out

qe_disp = interp.InterpretedQuery(raw_input="q", filters=[], error="boom")
assert "Could not interpret" in interp.format_filters_for_display(qe_disp)

q_empty = interp.InterpretedQuery(raw_input="q", filters=[])
assert "No specific filters" in interp.format_filters_for_display(q_empty)
print("✅ filters, derived, error, and empty cases render")

mock.patch.stopall()
print()
print("🎯 All interpreter tests pass!")
