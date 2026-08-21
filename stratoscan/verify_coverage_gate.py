"""Coverage-gate verifier: every filterable column must be > 50% populated.

The coverage gate exists because a column that is silently empty (0%
populated) makes EVERY filter on it return 0 candidates — the screener
looks broken when the real problem is the data. This script queries the
live DB, reports per-column coverage, and exits non-zero when any gate
column is below the threshold so it can gate CI / deploys.

Usage:
    python verify_coverage_gate.py [--min-pct 50]

Exit code 0 = all gate columns pass; 1 = one or more columns are thin
(the output lists them so a refresh/backfill can be targeted).
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")

from src.database import PostgresDB, coverage_gap_columns  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--min-pct", type=float, default=50.0,
        help="minimum required non-null percentage (default 50)",
    )
    args = parser.parse_args()

    db = PostgresDB()
    counts = db.column_coverage()
    db.close()

    total = max(counts.values())
    print(f"Coverage gate (min {args.min_pct:.0f}% of {total} rows):")
    for col in sorted(counts):
        n = counts[col]
        pct = 100.0 * n / total if total else 0.0
        flag = "" if pct >= args.min_pct else "  <-- GAP"
        print(f"  {col:>20}: {n:>6} ({pct:5.1f}%){flag}")

    gaps = coverage_gap_columns(counts, min_pct=args.min_pct)
    if gaps:
        print(
            f"\n❌ COVERAGE GATE FAILED: {len(gaps)} column(s) below "
            f"{args.min_pct:.0f}%: {', '.join(gaps)}"
        )
        print("   Run the relevant backfill before shipping data-dependent queries.")
        return 1
    print("\n✅ Coverage gate passed — every filterable column is populated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
