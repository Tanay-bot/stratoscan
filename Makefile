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
verify:
	@echo "── /health ──────────────────────────────────────────────"
	@curl -fsS http://localhost:8000/health | head -c 200; echo
	@echo
	@echo "── the scheduler is a scheduler, not a second API ──────"
	@printf '  PID 1: '
	@$(COMPOSE) exec -T scheduler sh -c 'cat /proc/1/cmdline | tr "\000" " "' 2>/dev/null || echo "(exec failed)"
	@echo
	@$(COMPOSE) exec -T scheduler sh -c 'cat /proc/1/cmdline | tr "\000" " "' 2>/dev/null \
		| grep -q "src.main scheduler" \
		&& echo "  ✓ runs the scheduler" \
		|| { echo "  ✗ PID 1 is not the scheduler"; exit 1; }
	@$(COMPOSE) exec -T scheduler sh -c 'cat /proc/1/cmdline | tr "\000" " "' 2>/dev/null \
		| grep -q "uvicorn" \
		&& { echo "  ✗ PID 1 is uvicorn — the exact bug task 005 exists to prevent"; exit 1; } \
		|| echo "  ✓ not uvicorn"
	@echo
	@echo "── Redis is really in use (not the in-memory fallback) ──"
	@$(COMPOSE) logs api 2>/dev/null | grep -iE "redis|rate.?limit store" | tail -5 || true
	@if $(COMPOSE) logs api 2>/dev/null | grep -qiE "falling back to the in-memory|in-memory store"; then \
		echo "  ✗ API fell back to the in-memory rate-limit store"; exit 1; \
	else \
		echo "  ✓ no in-memory fallback logged"; \
	fi
	@echo
	@echo "── mailpit ──────────────────────────────────────────────"
	@curl -fsS http://localhost:8025 >/dev/null && echo "  ✓ mailpit up (nothing will arrive in it — see .env.local.example)"
