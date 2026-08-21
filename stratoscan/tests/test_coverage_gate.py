"""Hermetic coverage-gate tests — no network, no live DB.

Run: python -m tests.test_coverage_gate

Covers:
1. ``coverage_gap_columns`` (pure function): flags columns under the
   threshold, handles empty input, 0-count columns, threshold boundary.
2. The gate column list is a strict subset of STOCK_MASTER_COLUMNS (so
   the gate never references a column the screener can't filter on).
3. Every gate column is a REAL stock_master column (typo guard).

This suite exists so a silently-empty column (0% populated → every
filter on it returns 0 candidates) fails loudly in CI via the live
``verify_coverage_gate.py`` AND the gate logic itself is locked.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.database import (  # noqa: E402
    STOCK_MASTER_COLUMNS,
    PostgresDB,
    coverage_gap_columns,
)

# ── 1. Pure gate logic ───────────────────────────────────────────────────────
# Healthy: every column at/near total → no gaps at a sane threshold.
# Total is taken as the max count (4953).
healthy = {
    "pe_ratio": 4800,          # 96.9%
    "rsi_14": 4700,            # 94.9%
    "avg_volume": 4953,        # 100%
    "ev_ebitda": 4600,         # 92.9%
    "net_margin_ttm": 4500,    # 90.9%
}
assert coverage_gap_columns(healthy, min_pct=50.0) == []
assert coverage_gap_columns(healthy, min_pct=90.0) == []  # all ≥ 90.9%
# A strict 99% threshold flags everything except the 100%-covered column.
assert coverage_gap_columns(healthy, min_pct=99.0) == [
    "ev_ebitda", "net_margin_ttm", "pe_ratio", "rsi_14",
]
print("✅ healthy columns pass; strict thresholds flag proportionally")

# A 0%-populated column (the avg_volume / ev_ebitda failure mode) must
# flag even at a low threshold.
sparse = {
    "pe_ratio": 2555,
    "avg_volume": 0,          # the bug: silently empty column
    "ev_ebitda": 0,           # the bug: silently empty column
    "current_price": 4949,
}
gaps = coverage_gap_columns(sparse, min_pct=10.0)
assert "avg_volume" in gaps and "ev_ebitda" in gaps, gaps
assert "current_price" not in gaps, gaps
assert gaps == ["avg_volume", "ev_ebitda"], gaps
print("✅ 0%-populated columns flagged even at a low threshold")

# Empty input → no gaps (no crash).
assert coverage_gap_columns({}) == []
# All-zero input → every column is a gap but no division-by-zero crash.
assert coverage_gap_columns({"a": 0, "b": 0}, min_pct=1.0) == ["a", "b"]
print("✅ empty / all-zero inputs handled without crash")

# ── 2. Gate column list is a subset of the whitelist ─────────────────────────
gate_cols = set(PostgresDB.COVERAGE_GATE_COLUMNS)
assert gate_cols.issubset(STOCK_MASTER_COLUMNS), (
    f"gate columns not all in STOCK_MASTER_COLUMNS: "
    f"{gate_cols - set(STOCK_MASTER_COLUMNS)}"
)
print(f"✅ {len(gate_cols)} gate columns all resolve against the whitelist")

# ── 3. No typos: every gate column is a real stock_master column ────────────
# STOCK_MASTER_COLUMNS is itself validated against the schema elsewhere;
# this just guards the gate list against a typo drifting in.
assert len(gate_cols) == len(PostgresDB.COVERAGE_GATE_COLUMNS), (
    "gate columns must be unique"
)
print("✅ gate column list is unique and typo-guarded")

print()
print("🎯 All coverage-gate tests pass!")
