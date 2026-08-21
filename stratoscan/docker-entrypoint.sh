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

run_migrations

echo "→ Starting Stratoscan API..."
exec uvicorn src.api:app --host 0.0.0.0 --port 8000 --no-access-log
