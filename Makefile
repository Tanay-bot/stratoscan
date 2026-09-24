# ═══════════════════════════════════════════════════════════════════════════════
# Stratoscan — local, production-shaped stack
#
#   make up        build + start everything, wait until /health is ok
#   make down      stop and DELETE volumes (a true reset, not a pause)
#   make rebuild   down, then up, from zero
#   make logs      follow all services
#   make ps        what is running
#   make verify    the task-005 checks, in one command
#
# Prerequisite: Docker with the Compose plugin (`docker compose version`).
# ═══════════════════════════════════════════════════════════════════════════════

COMPOSE  := docker compose
ENV_FILE := .env.local

.PHONY: up down rebuild logs ps verify check-env

# ── guard ─────────────────────────────────────────────────────────────────────
# Fail BEFORE touching Docker when the (git-ignored) env file is missing. Compose
# would otherwise start with an empty environment and the API would boot with no
# database URL and no JWT secret — a confusing failure two layers further down.
check-env:
	@test -f $(ENV_FILE) || { \
		echo "✗ $(ENV_FILE) is missing."; \
		echo "  Run:  cp .env.local.example $(ENV_FILE)"; \
		exit 1; \
	}

# ── lifecycle ─────────────────────────────────────────────────────────────────
up: check-env
	$(COMPOSE) up -d --build
	@printf 'waiting for /health to report status=ok '
	@for i in $$(seq 1 90); do \
		if curl -fsS http://localhost:8000/health 2>/dev/null \
			| python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get('status')=='ok' and d.get('database_alive') else 1)" 2>/dev/null; then \
			echo; echo "✓ API healthy:"; curl -fsS http://localhost:8000/health; echo; exit 0; \
		fi; \
		printf '.'; sleep 2; \
	done; \
	echo; echo "✗ API did not report ok + database_alive within 180s"; \
	$(COMPOSE) ps; echo "--- api logs ---"; $(COMPOSE) logs --tail=30 api; exit 1

# `-v` is the whole point. A reset that leaves the database behind is not a reset,
# and `make rebuild` is the command this stack gets used through most.
down:
	$(COMPOSE) down -v --remove-orphans

rebuild:
	$(MAKE) down
	$(MAKE) up

logs:
	$(COMPOSE) logs -f --tail=50

ps:
	$(COMPOSE) ps

# ── the task-005 checks ───────────────────────────────────────────────────────
# The first two are the ones that matter, and BOTH fail silently when wrong:
#   - a scheduler service mis-wired to uvicorn looks perfectly healthy;
#   - a bypassed Redis looks perfectly healthy.
# So each is asserted by name, never inferred from "the containers are up".
#
# On the scheduler check: spec 005 quotes `docker compose exec -T scheduler ps ax`,
# but python:3.14-slim ships no procps (so there is no `ps`), and installing it
# would mean editing the Dockerfile — which that spec forbids. PID 1's
# /proc/1/cmdline answers the same question more directly, so it is used instead.
#
# The mail check is the third, and it is the one that proves the SMTP transport
# end to end: "the mailpit container is up" says nothing about whether the app
# can deliver into it, so this registers a throwaway account and reads the
# message back out of mailpit's own REST API. Two details are deliberate:
#
#   - The address carries a timestamp. /auth/register rejects a duplicate email
#     with 409, so a FIXED address would make `make verify` fail on its second
#     run without a rebuild. A per-run address keeps this target idempotent and
#     makes the match unambiguous — an earlier run's message cannot satisfy it.
#   - It spends real rate-limit budget: the `register` scope is 3/min and 10/day
#     per IP (a SendGrid cost shield, see CLAUDE.md §10). A 429 here means wait a
#     minute, or `make down` and back up — Redis holds the counters and has no
#     volume, so teardown resets them. Raising that cap to make this pass would
#     defeat the reason it exists.
verify:
	@echo "── /health ──────────────────────────────────────────────"
	@body=$$(curl -fsS http://localhost:8000/health); \
		echo "  $$body"; \
		echo "$$body" | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get('status')=='ok' and d.get('database_alive') else 1)" 2>/dev/null \
			|| { echo "  ✗ /health is not status=ok with database_alive=true"; exit 1; }; \
		echo "  ✓ /health ok, database_alive true"
	@echo
	@echo "── the scheduler is a scheduler, not a second API ──────"
	@cmd=$$($(COMPOSE) exec -T scheduler sh -c 'cat /proc/1/cmdline | tr "\000" " "' 2>/dev/null); \
		echo "  PID 1: $$cmd"; \
		echo "$$cmd" | grep -q "src.main scheduler" || { echo "  ✗ PID 1 is not the scheduler"; exit 1; }
	@echo
	@echo "── Redis is really in use (not the in-memory fallback) ──"
	@log=$$($(COMPOSE) logs api 2>/dev/null); \
		echo "$$log" | grep -m1 "Upstash Redis backend" || { echo "  ✗ no 'Upstash Redis backend' line — Redis is not in use"; exit 1; }; \
		if echo "$$log" | grep -qE "falling back to the in-memory|in-memory store"; then echo "  ✗ fell back to the in-memory store"; exit 1; fi
	@echo
	@echo "── a real message reaches mailpit (not just that it is up) ──"
	@addr="smtp-check-$$(date +%s)@example.test"; \
		curl -fsS -X POST http://localhost:8000/auth/register \
			-H 'Content-Type: application/json' \
			--max-time 20 \
			-d "{\"email\":\"$$addr\",\"password\":\"local-dev-password\"}" \
			| python3 -c "import json,sys; sys.exit(0 if json.load(sys.stdin).get('email_sent') else 1)" \
			|| { echo "  ✗ register did not report email_sent=true"; exit 1; }; \
		curl -fsS --max-time 10 http://localhost:8025/api/v1/messages \
			| python3 -c "import json,sys; m=json.load(sys.stdin); sys.exit(0 if any(sys.argv[1] in str(x) for x in m.get('messages',[])) else 1)" "$$addr" \
			|| { echo "  ✗ the message never arrived in mailpit"; exit 1; }; \
		echo "  ✓ register → $$addr → mailpit (read back via its REST API)"
