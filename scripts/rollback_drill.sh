#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Rollback drill (Day 15) — deploy a deliberately broken build to the local
# compose stack, notice, and put the last good image back. Target: < 5 min.
#
#   make up                      # a healthy stack first
#   scripts/rollback_drill.sh    # run from the repo root
#
# The broken build is a copy of the build context with one line appended to
# src/api.py that raises at import — the classic "it built, it won't boot"
# release. Nothing in the working tree is modified.
#
# Production is the same shape: there is no `fly rollback`; a rollback is a
# deploy of the previous image (`fly deploy --image …:sha-<last green>`), which
# is what .github/workflows/deploy.yml does. Rehearse that on the live stack
# before launch (RUNBOOK.md).
#
#   CONTEXT=./stratoscan  build context (override to test a patched copy)
#   API=http://localhost:8000
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

CONTEXT="${CONTEXT:-./stratoscan}"
API="${API:-http://localhost:8000}"
IMAGE="stratoscan-local:latest"
GOOD="stratoscan-local:known-good"

healthy() {
  curl -fsS --max-time 5 "$API/health" 2>/dev/null |
    python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get('status')=='ok' and d.get('database_alive') else 1)" 2>/dev/null
}
now() { date +%s.%N; }
secs() { python3 -c "print(f'{$2 - $1:.1f}')"; }

healthy || { echo "✗ the stack is not healthy — run 'make up' first"; exit 1; }
echo "✓ stack healthy; tagging the running image as $GOOD"
docker tag "$IMAGE" "$GOOD"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cp -r "$CONTEXT"/. "$tmp"/
echo 'raise RuntimeError("deliberately broken build (rollback drill)")' >> "$tmp/src/api.py"

t0=$(now)
docker build -q -t "$IMAGE" "$tmp" >/dev/null
t_built=$(now)
echo "→ broken image built in $(secs "$t0" "$t_built")s; deploying it"
docker compose up -d --no-build api >/dev/null 2>&1 || true
t_deployed=$(now)

# Detection: the same signal the deploy workflow uses — /health stops answering
# — and proof it is the BAD build that is failing (its own crash in the logs),
# not just a container mid-swap.
detected=""
for _ in $(seq 1 90); do
  if ! healthy && docker compose logs api 2>&1 | grep -q "deliberately broken build"; then
    detected=$(now); break
  fi
  sleep 1
done
[ -n "$detected" ] || { echo "✗ the broken build never showed its crash — drill invalid"; exit 1; }
echo "✗ broken release detected $(secs "$t_deployed" "$detected")s after deploy:"
docker compose logs api 2>&1 | grep -m1 "deliberately broken build" | sed 's/^/    /'

# Roll back: put the last good image back and redeploy it.
t_rb=$(now)
docker tag "$GOOD" "$IMAGE"
docker compose up -d --no-build api scheduler >/dev/null 2>&1
for _ in $(seq 1 120); do healthy && break; sleep 1; done
healthy || { echo "✗ rollback did not restore health"; exit 1; }
t_ok=$(now)

echo
echo "Rollback drill — $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "  deploy → detected     $(secs "$t_deployed" "$detected") s"
echo "  rollback → healthy    $(secs "$t_rb" "$t_ok") s"
echo "  bad deploy → healthy  $(secs "$t_deployed" "$t_ok") s   (target < 300 s)"
