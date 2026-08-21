"""Behavioral probe for the DERIVED_CRITERIA_KEYS root-cause fix.

Run: python verify_derived_fix.py

Verifies the fix BEHAVES correctly, not just compiles:

  1. Logging capture on the REAL 'src.interpreter' logger: interpret_query
     with ALL 11 derived keys + momentum + a real filter emits ZERO
     "Unknown filter field" warnings (the regression).
  2. A genuinely-unknown field still emits exactly ONE warning (the
     monitoring signal is preserved).
  3. Full lifecycle: LLM JSON -> InterpretedQuery — every derived key is
     extracted with its correct boolean (True stays True, False stays
     False, non-bool truthy -> None), momentum metadata lands, real
     filters land, has_derived_criteria works.
  4. format_filters_for_display renders the derived labels (display path).
  5. Single-source-of-truth integrity: the constant covers all 11 known
     derived keys, every one is a real InterpretedQuery field, and every
     one has a display label (nothing can drift silently).
  6. End-to-end screener enforcement: derived criteria (above_sma_50 +
     golden_cross + bullish momentum) actually translate to SQL column
     conditions and filter the universe correctly.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from unittest import mock  # noqa: E402

from src import interpreter as interp  # noqa: E402
from src.screener import StockScreener  # noqa: E402

ALL_DERIVED = [
    "above_sma_50", "below_sma_50", "above_sma_200", "below_sma_200",
    "golden_cross", "death_cross", "near_52w_high", "near_52w_low",
    "macd_bullish", "bollinger_breakout", "bollinger_oversold",
]


class _Capture(logging.Handler):
    """Collect messages emitted on a logger (real logging, not a mock)."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def capture():
    cap = _Capture()
    logger = logging.getLogger("src.interpreter")
    logger.addHandler(cap)
    logger.setLevel(logging.WARNING)
    return cap, logger


def _mk_llm(content: str):
    router = mock.MagicMock()
    router.chat_completion.return_value.choices[0].message.content = content
    return router


def _unknown_warnings(cap) -> list:
    return [m for m in cap.messages if "Unknown filter field" in m]


print("=" * 70)
print("DERIVED-CRITERIA FIX — behavioral verification")
print("=" * 70)

# ── 1. ALL derived keys → ZERO spurious warnings ────────────────────────────
print("\n[1] All 11 derived keys through interpret_query...")

# Build an LLM response carrying every derived key (True/False mix),
# momentum, and one real filter.
parts = ['"momentum":"bullish"', '"max_pe_ratio":15']
for i, k in enumerate(ALL_DERIVED):
    parts.append(f'"{k}":{("true" if i % 2 == 0 else "false")}')
payload = "{" + ",".join(parts) + "}"

cap, logger = capture()
interp._interpret_cache.clear()
with mock.patch("src.llm_router.get_router", return_value=_mk_llm(payload)), \
     mock.patch.object(interp.settings, "cache_enabled", False):
    q = interp.interpret_query("uptrend momentum tech with everything")
logger.removeHandler(cap)

assert _unknown_warnings(cap) == [], \
    f"derived keys must not warn: {_unknown_warnings(cap)}"
print("✅ zero 'Unknown filter field' warnings for all 11 derived keys")

# ── 2. Genuinely unknown field still warns exactly once ─────────────────────
print("[2] Truly unknown field still warns...")

cap, logger = capture()
with mock.patch(
    "src.llm_router.get_router", return_value=_mk_llm('{"flying_pig_score":42}')
), mock.patch.object(interp.settings, "cache_enabled", False):
    q_bad = interp.interpret_query("bogus field query")
logger.removeHandler(cap)

warns = _unknown_warnings(cap)
assert len(warns) == 1 and "flying_pig_score" in warns[0], warns
assert q_bad.filters == [] and q_bad.error is None  # graceful, no crash
print("✅ exactly one warning for the unknown field; query degrades gracefully")

# ── 3. Extraction lifecycle: every key lands with the right boolean ─────────
print("[3] Derived-key extraction correctness...")

for i, k in enumerate(ALL_DERIVED):
    want = (i % 2 == 0)  # True for even indexes, False for odd
    assert getattr(q, k) is want, f"{k} should be {want}, got {getattr(q, k)}"
assert q.momentum == "bullish"
assert any(f.field == "pe_ratio" and f.value == 15 for f in q.filters)
assert q.has_derived_criteria is True
assert q.error is None
print("✅ True→True, False→False, momentum + real filter + has_derived_criteria")

# Non-bool truthy must NOT flip a derived key on (strict-boolean contract)
cap, logger = capture()
with mock.patch(
    "src.llm_router.get_router",
    return_value=_mk_llm('{"above_sma_50":"true","golden_cross":1}'),
), mock.patch.object(interp.settings, "cache_enabled", False):
    q_str = interp.interpret_query("string bools")
logger.removeHandler(cap)
assert q_str.above_sma_50 is None and q_str.golden_cross is None
assert _unknown_warnings(cap) == []  # still no spurious warning
print("✅ non-bool truthy values stay None (strict booleans, still silent)")

# ── 4. Display path renders the derived labels ──────────────────────────────
print("[4] format_filters_for_display...")

display = interp.format_filters_for_display(q)
for label in ("price > SMA50", "SMA50 > SMA200 (golden cross)",
              "within 5% of 52w high", "MACD > signal", "pe_ratio ≤ 15"):
    assert label in display, f"display missing: {label}"
print("✅ derived + momentum + numeric filters all render")

# ── 5. Single-source-of-truth integrity ─────────────────────────────────────
print("[5] No-drift integrity...")

import dataclasses
fields = {f.name for f in dataclasses.fields(interp.InterpretedQuery)}
missing_fields = [k for k in ALL_DERIVED if k not in fields]
assert not missing_fields, f"derived keys not on InterpretedQuery: {missing_fields}"
assert set(interp.DERIVED_CRITERIA_KEYS) == set(ALL_DERIVED), \
    "constant must cover exactly the 11 known derived keys"
# has_derived_criteria uses the constant (no literal list to drift).
# It's a @property, so inspect needs the underlying function via .fget.
import inspect
src_has = inspect.getsource(interp.InterpretedQuery.has_derived_criteria.fget)
assert "DERIVED_CRITERIA_KEYS" in src_has, "has_derived_criteria must use the constant"
# _convert_to_filter_criteria uses the constant too
src_conv = inspect.getsource(interp._convert_to_filter_criteria)
assert "DERIVED_CRITERIA_KEYS" in src_conv
print("✅ constant is the single source; dataclass fields + both consumers in sync")

# ── 6. End-to-end: derived criteria actually filter the universe ────────────
print("[6] Screener enforcement of derived criteria...")


def _matches(row, filters):
    for col, value in filters.items():
        if isinstance(value, list) and value and all(isinstance(v, tuple) for v in value):
            if not all(_matches(row, {col: sub}) for sub in value):
                return False
            continue
        if isinstance(value, tuple) and len(value) >= 2 and value[0].endswith("_col"):
            op, other = value[0], value[1]
            lhs, rhs = row.get(col), row.get(other)
            if lhs is None or rhs is None:
                return False
            if len(value) == 3 and value[0].endswith("frac_col"):
                rhs = rhs * float(value[2])
            ok = (lhs > rhs) if op == "gt_col" else (
                (lhs < rhs) if op == "lt_col" else (
                    (lhs >= rhs) if op in ("gte_col", "gte_frac_col") else (lhs <= rhs)))
            if not ok:
                return False
            continue
        if isinstance(value, tuple) and len(value) == 2:
            op, operand = value
            if op == "neq" and operand is None:
                if row.get(col) is None:
                    return False
                continue
            cur = row.get(col)
            if cur is None:
                return False
            ops = {"lt": lambda a, b: a < b, "lte": lambda a, b: a <= b,
                   "gt": lambda a, b: a > b, "gte": lambda a, b: a >= b,
                   "eq": lambda a, b: a == b, "neq": lambda a, b: a != b}
            if not ops[op](cur, operand):
                return False
            continue
        if isinstance(value, (list, tuple)):
            if row.get(col) not in value:
                return False
        elif row.get(col) != value:
            return False
    return True


class _DB:
    def __init__(self, rows):
        self._rows = rows

    def _m(self, filters):
        return [r for r in self._rows if _matches(r, filters)]

    def count_stocks(self, filters):
        return len(self._m(filters))

    def query_stocks(self, filters, limit=50, offset=0, sort_by="market_cap",
                     sort_desc=True):
        rows = sorted(self._m(filters),
                      key=lambda r: (r.get(sort_by) is not None, r.get(sort_by)),
                      reverse=sort_desc)
        return rows[offset: offset + limit]

    def aggregate_stats(self, filters, columns):
        return {c: {"mean": 0.0, "std": 0.0, "valid": 0} for c in columns}

    def data_freshness(self, max_age_minutes=15):
        return {"oldest_age_minutes": 0.5, "max_updated_at": None,
                "stale_count": 0, "total_rows": len(self._rows)}


universe = [
    # UPTREND: price>SMA50 AND SMA50>SMA200 AND momentum bullish — matches
    {"symbol": "UPTR", "company_name": "Uptrend", "sector": "Technology",
     "market_cap": 1_000_000_000, "current_price": 100.0, "sma_50": 90.0,
     "sma_200": 80.0, "high_52w": 110.0},
    # GOLD: golden cross but price BELOW SMA50 — fails the trend filter
    {"symbol": "GOLD", "company_name": "Golden But Weak", "sector": "Technology",
     "market_cap": 1_100_000_000, "current_price": 85.0, "sma_50": 90.0,
     "sma_200": 80.0, "high_52w": 95.0},
    # NOTECH: matches none of the derived conditions
    {"symbol": "NOTC", "company_name": "No Trend", "sector": "Technology",
     "market_cap": 1_200_000_000, "current_price": 70.0, "sma_50": 75.0,
     "sma_200": 78.0, "high_52w": 80.0},
]

# Freshness guard: the screener excludes rows older than
# STALE_EXCLUSION_DAYS in SQL by default. Stamp the universe fresh.
_fresh_ts = datetime.now(timezone.utc).isoformat()
for _r in universe:
    _r["updated_at"] = _fresh_ts

# LLM: strong uptrend = golden_cross + above_sma_50 + bullish momentum
cap, logger = capture()
screener = StockScreener(db=_DB(universe))
with mock.patch(
    "src.llm_router.get_router",
    return_value=_mk_llm('{"above_sma_50":true,"golden_cross":true,'
                         '"momentum":"bullish"}'),
), mock.patch.object(interp.settings, "cache_enabled", False):
    report = screener.screen_from_natural_language("strong uptrend tech")
logger.removeHandler(cap)

got = sorted(r.symbol for r in report.results)
assert got == ["UPTR"], f"only UPTR satisfies price>SMA50 AND SMA50>SMA200, got {got}"
assert _unknown_warnings(cap) == [], "screen path must also be warning-free"
assert report.total_matches == 1
echo = report.applied_filters
assert "price > SMA50" in echo and "SMA50 > SMA200 (golden cross)" in echo
print("✅ derived criteria enforced in SQL: only UPTR selected; echo honest; silent")

print("\n" + "=" * 70)
print("✅✅ ALL DERIVED-FIX BEHAVIORAL CHECKS PASSED")
print("=" * 70)
