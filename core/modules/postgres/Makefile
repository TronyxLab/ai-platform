# GREP_SUMMARY: postgres Makefile start stop restart status logs backup restore BACKUP_MODE custom
# STRUCTURE: ┌postgres lifecycle┐ → ◇ module.mk include (start/restart/down/up) → ⊕ custom start/backup/restore + STOP_TIMEOUT=60
# region MODULE_CONTRACT
## @purpose  Module lifecycle management for postgres Docker module
## @scope    Called by platform users per role; sudo-whitelist enforces per-role restrictions
## @invariants
##   - All docker compose calls use docker-compose.base.yml
##   - env-file loaded from SECRETS_ENV_FILE (default: /run/platform/secrets.env)
##   - BACKUP_MODE := custom — postgres объявляет рецепты backup/restore ПОСЛЕ include (D1)
##   - STOP_TIMEOUT := 60 — WAL flush grace для compose stop (D2); наследуется restart/down
##   - backup target uses pg_dumpall via docker exec
##   - restore is allowed only for owner role (sudo-whitelist.conf enforces at OS level)
## @rationale Targets match standard module lifecycle; postgres-specific: 60s stop grace,
##   pg_dumpall backup, psql restore — объявлены после include (last-recipe-wins).
# endregion MODULE_CONTRACT

MODULE_NAME := postgres
CONTAINER := postgres
SECRETS_ENV := $(or $(SECRETS_ENV_FILE),/run/platform/secrets.env)
# 60s grace для WAL flush при compose stop (наследуется restart/down/restart-hard из module.mk)
STOP_TIMEOUT := 60
BACKUP_MODE := custom

include ../../templates/module.mk

# ── Custom start ──
## start: Start postgres container
start: ## Start postgres (compose up -d)
	@echo "[IMP:7][postgres-make][start] Starting postgres"
	$(COMPOSE_CMD) up -d
	@echo "[IMP:8][postgres-make][start] postgres started"

# ── Custom backup: pg_dumpall via docker exec ──
## backup: Dump all databases to /var/lib/platform/backup-spool/
## ⚠️ TRAP[BUG] · 1.0.0 · HI · -U postgres хардкод → «role postgres does not exist» на кластерах
## · с кастомным POSTGRES_USER (secrets). Fix: суперюзер из env КОНТЕЙНЕРА (compose-инъекция).
backup: ## pg_dump all databases to backup spool
	@echo "[IMP:7][postgres-make][backup] Starting pg_dump backup"
	@TIMESTAMP=$$(date -u '+%Y%m%dT%H%M%SZ'); \
	BACKUP_DIR=/var/lib/platform/backup-spool/postgres; \
	mkdir -p "$$BACKUP_DIR"; \
	docker exec $(CONTAINER) sh -c 'pg_dumpall -U "$$POSTGRES_USER"' \
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
	docker exec -i $(CONTAINER) sh -c 'psql -U "$$POSTGRES_USER" < /dev/stdin' < "$(DUMP_FILE)"
	@echo "[IMP:8][postgres-make][restore] Restore complete from $(DUMP_FILE)"

# 🧐 TRAP[DECISION] · 2026-07-18 · — · Custom overrides: postgres-specific lifecycle
# · Rejected: use module.mk defaults only (not flexible enough)
# · Reason: postgres требует 60s stop grace (WAL flush), pg_dump backup via docker exec,
# ·   restore через psql. Эти опции не покрываются module.mk без variable-параметров.
# · Rev: 2026-08-01 ВЫПОЛНЕНО — module.mk получил configurable stop_timeout
# ·   через STOP_TIMEOUT := 60 (этот Makefile) + BACKUP_MODE=custom контракт.
# ·   stop/restart-hard/status/logs предоставляет module.mk (канон lifecycle).
# ·   Новое Rev: если потребуется кастомный stop/status для postgres — override ПОСЛЕ include.
