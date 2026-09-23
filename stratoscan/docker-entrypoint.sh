#!/bin/sh
# Stratoscan container entrypoint.
#
# Runs Alembic migrations (idempotent — ALTER/CREATE ... IF NOT EXISTS) so a
# fresh `docker run` always has the users/news/stripe schema before the API
# accepts requests. Without this, auth endpoints fail with
# `relation "users" does not exist` (verified against a real deployment).

set -e

# Retry migrations a few times: Neon suspends idle databases and a cold
# wake can take >15s, which can exceed alembic's first connect attempt
# (env.py honors DB_CONNECT_TIMEOUT, default 15s).
run_migrations() {
    echo "→ Running database migrations (alembic upgrade head)..."
    for attempt in 1 2 3 4 5; do
        if python -m alembic upgrade head; then
            echo "   ✅ migrations up to date"
            return 0
        fi
        echo "   ⚠️  migration attempt ${attempt}/5 failed — retrying in 5s (cold DB?)..."
        sleep 5
    done
    echo "   ❌ migrations failed after 5 attempts"
    return 1
}

start_api() {
    echo "→ Starting Stratoscan API..."
    exec uvicorn src.api:app --host 0.0.0.0 --port 8000 --no-access-log
}

# ── Role dispatch ──────────────────────────────────────────────────────────
# This script is the image ENTRYPOINT, so whatever a caller passes as the
# command (docker-compose `command:`, a Fly [processes] entry, `docker run
# … scheduler`) arrives here as "$@". Until this dispatch existed the script
# ignored "$@" entirely and always ran uvicorn — so a compose service asking
# for the scheduler would silently have started A SECOND API and no
# scheduler, with both containers reporting healthy. That is the failure
# this block exists to prevent; do not collapse it back to a single path.
#
# Migrations run for the API role ONLY. Two containers racing
# `alembic upgrade head` against one cold serverless database is a lock
# contention bug waiting to happen, and the schema is a precondition the API
# owns. Order the scheduler after the API in compose (`depends_on` with a
# healthcheck) so the schema exists before its first job runs.

case "${1:-}" in
    ""|api|serve)
        # No argument keeps the original behaviour byte for byte, so the
        # existing Fly deployment (which passes no command) is unaffected.
        run_migrations
        start_api
        ;;
    scheduler)
        echo "→ Starting Stratoscan scheduler (migrations are the API's job)..."
        exec python -m src.main scheduler
        ;;
    migrate)
        # Useful as a compose one-shot or a release step.
        run_migrations
        ;;
    *)
        # Anything else runs verbatim — `docker run … python -m src.main
        # warm-cache`, a shell, a one-off CLI command.
        exec "$@"
        ;;
esac
