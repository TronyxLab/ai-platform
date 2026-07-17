# GREP_SUMMARY: postgres Makefile start stop restart status logs backup restore
# region MODULE_CONTRACT
## @purpose  Module lifecycle management for postgres Docker module
## @scope    Called by platform users per role; sudo-whitelist enforces per-role restrictions
## @invariants
##   - All docker compose calls use docker-compose.base.yml
##   - env-file loaded from SECRETS_ENV_FILE (default: /run/platform/secrets.env)
##   - backup target uses pg_dump via docker exec
##   - restore is allowed only for owner role (sudo-whitelist.conf enforces at OS level)
## @rationale Targets match standard module lifecycle
# endregion MODULE_CONTRACT

MODULE_NAME := postgres
CONTAINER := postgres
SECRETS_ENV := $(or $(SECRETS_ENV_FILE),/run/platform/secrets.env)

include ../../templates/module.mk

# ── Custom start ──
## start: Start postgres container
start: ## Start postgres (compose up -d)
	@echo "[IMP:7][postgres-make][start] Starting postgres"
	$(COMPOSE_CMD) up -d
	@echo "[IMP:8][postgres-make][start] postgres started"

# ── Custom stop (60s grace for clean shutdown) ──
## stop: Stop postgres container (owner only)
stop: ## Stop postgres (compose stop — 60s grace)
	@echo "[IMP:7][postgres-make][stop] Stopping postgres (grace 60s)"
	$(COMPOSE_CMD) stop --timeout 60
	@echo "[IMP:8][postgres-make][stop] postgres stopped"

# ── Custom restart (hard restart) ──
## restart: Hard restart postgres
restart: ## Hard restart postgres (compose down && up -d)
	@echo "[IMP:7][postgres-make][restart] Hard restarting postgres"
	$(COMPOSE_CMD) down && $(COMPOSE_CMD) up -d
	@echo "[IMP:8][postgres-make][restart] postgres hard restarted"

# ── Custom status ──
## status: Show container status
status: ## Show postgres container status
	@echo "[IMP:7][postgres-make][status] postgres status"
	$(COMPOSE_CMD) ps
	docker inspect --format='{{.State.Health.Status}}' $(CONTAINER) 2>/dev/null || echo "container not running"

# ── Custom logs ──
## logs: Show container logs
logs: ## Show postgres logs (follow)
	@echo "[IMP:7][postgres-make][logs] Streaming postgres logs"
	$(COMPOSE_CMD) logs -f --tail=100

# ── Custom backup: pg_dumpall via docker exec ──
## backup: Dump all databases to /var/lib/platform/backup-spool/
backup: ## pg_dump all databases to backup spool
	@echo "[IMP:7][postgres-make][backup] Starting pg_dump backup"
	@TIMESTAMP=$$(date -u '+%Y%m%dT%H%M%SZ'); \
	BACKUP_DIR=/var/lib/platform/backup-spool/postgres; \
	mkdir -p "$$BACKUP_DIR"; \
	docker exec $(CONTAINER) pg_dumpall -U postgres \
		> "$$BACKUP_DIR/pgdumpall_$${TIMESTAMP}.sql" && \
	echo "[IMP:9][postgres-make][backup] DONE: pgdumpall_$${TIMESTAMP}.sql" || \
	{ echo "[IMP:9][postgres-make][backup] FAIL: pg_dump failed" >&2; exit 1; }

# ── Module-specific targets ──
## restore: Restore from dump file DUMP_FILE=<path> (owner only)
restore: ## Restore from dump file (owner only, requires DUMP_FILE=)
	@echo "[IMP:7][postgres-make][restore] Restoring from DUMP_FILE=$(DUMP_FILE)"
	@if [[ -z "$(DUMP_FILE)" ]]; then \
		echo "[IMP:9][postgres-make][restore] ERROR: DUMP_FILE not set" >&2; exit 1; \
	fi
	@if [[ ! -f "$(DUMP_FILE)" ]]; then \
		echo "[IMP:9][postgres-make][restore] ERROR: DUMP_FILE not found: $(DUMP_FILE)" >&2; exit 1; \
	fi
	docker exec -i $(CONTAINER) psql -U postgres < "$(DUMP_FILE)"
	@echo "[IMP:8][postgres-make][restore] Restore complete from $(DUMP_FILE)"
