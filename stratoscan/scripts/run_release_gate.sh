#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Stratoscan — Release Gate (P0-2)
#
# Runs ALL 15 verify_*.py behavior probes against the isolated staging
# environment (stratoscan_probes DB, staging LLM/Finnhub keys, rate limiter
# handled per-probe) and BLOCKS the release if any probe fails.
#
# Usage:
#   scripts/run_release_gate.sh                 # full gate (15 probes)
#   scripts/run_release_gate.sh --only verify_throughput.py   # subset
#   scripts/run_release_gate.sh --report /tmp/gate.md         # write report
#
# Exit code: 0 = ALL probes passed → deploy is allowed.
#            1 = at least one probe failed → DEPLOY IS BLOCKED.
#
# Environment:
#   Local:  relies on staging.env in the repo root (STAGING_DATABASE_URL
#           must point at the stratoscan_probes DB — see seed_probes_db.py).
#   CI:     same vars provided as env/secrets; staging.env is optional.
# ═══════════════════════════════════════════════════════════════════════════════
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Resolve the Python interpreter:
#   1. $PYTHON env override (CI sets this to `python` from setup-python)
#   2. a python3.14 that's already on PATH (local dev / containers)
#   3. the macOS convention path (this repo's local dev machine)
#   4. any python3 on PATH
PY="${PYTHON:-}"
if [ -z "$PY" ] || ! command -v "$PY" >/dev/null 2>&1; then
  for cand in python3.14 python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
  done
fi
[ -z "$PY" ] && PY=/usr/local/bin/python3.14   # last-resort fallback
REPORT=""

# ── Arg parsing ───────────────────────────────────────────────────────────────
ONLY=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --only) shift; ONLY+=("$1") ;;
    --report) shift; REPORT="$1" ;;
    --help|-h)
      sed -n '1,22p' "$0"
      exit 0 ;;
    *)
      echo "Unknown arg: $1 (see --help)" >&2
      exit 2 ;;
  esac
  shift
done

if ! command -v "$PY" >/dev/null 2>&1 && [ ! -x "$PY" ]; then
  echo "❌ Python not found (tried PYTHON, python3.14, python3, python) — set PYTHON=/path/to/python" >&2
  exit 2
fi

cd "$HERE" || exit 2

echo "═══════════════════════════════════════════════════════════════════════"
echo "  STRATOSCAN RELEASE GATE — staging (isolated stratoscan_probes DB)"
echo "═══════════════════════════════════════════════════════════════════════"

ARGS=()
if [ "${#ONLY[@]}" -gt 0 ]; then
  # run_probes.py accepts multiple names after --only; forward them all.
  ARGS+=(--only)
  for name in "${ONLY[@]}"; do ARGS+=("$name"); done
fi
if [ -n "$REPORT" ]; then
  ARGS+=(--report "$REPORT")
fi

"$PY" run_probes.py "${ARGS[@]}"
STATUS=$?

echo ""
if [ "$STATUS" -eq 0 ]; then
  echo "✅ RELEASE GATE PASSED — all probes green. Deployment may proceed."
else
  echo "❌ RELEASE GATE FAILED — fix the failing probe(s) before any public push." >&2
fi
echo "   (exit code: $STATUS)"
exit "$STATUS"
