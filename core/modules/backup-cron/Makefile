# GREP_SUMMARY: backup-cron Makefile start stop restart status logs backup restore BACKUP_MODE custom
# STRUCTURE: ┌backup lifecycle┐ → ◇ module.mk include (start/restart/down/up) → ⊕ custom start/backup/restore + STOP_TIMEOUT=120
# region MODULE_CONTRACT
## @purpose  Module lifecycle management for backup-cron Docker module
## @scope    Called by platform users per role; sudo-whitelist enforces per-role restrictions
## @invariants
##   - All docker compose calls use docker-compose.base.yml
##   - env-file loaded from SECRETS_ENV_FILE (default: /run/platform/secrets.env)
##   - BACKUP_MODE := custom — backup-cron объявляет рецепты backup/restore ПОСЛЕ include (D1)
##   - STOP_TIMEOUT := 120 — grace для long-running backup jobs (D2)
##   - backup target triggers manual backup run (via docker exec cron job)
##   - restore is allowed only for owner role (sudo-whitelist.conf enforces at OS level)
##   - agent: NO stop, NO restore (07 §2.3)
## @rationale Targets match 06 §4.1 specification; agent restricted from stop/restore
# endregion MODULE_CONTRACT

MODULE_NAME := backup-cron
CONTAINER := backup-cron
# 120s grace для long-running backup jobs при compose stop (наследуется restart/down)
STOP_TIMEOUT := 120
BACKUP_MODE := custom

include ../../templates/module.mk

# ── Custom start (no readiness loop — backup-cron is a cron container) ──

## start: Start backup-cron container
start: ## Start backup-cron (compose up -d)
	@echo "[IMP:7][backup-cron-make][start] Starting backup-cron"
	$(COMPOSE_CMD) up -d
	@echo "[IMP:8][backup-cron-make][start] backup-cron started"

# ── Custom backup: trigger via docker exec ──

## backup: Trigger manual backup run
backup: ## Trigger immediate backup run (exec backup-postgres.sh)
	@echo "[IMP:7][backup-cron-make][backup] Triggering manual postgres backup"
	docker exec $(CONTAINER) /usr/local/bin/backup-postgres.sh
	@echo "[IMP:8][backup-cron-make][backup] Manual backup triggered"

# ── Module-specific targets (not in template) ──

## restore: Restore postgres from dump (owner only)
restore: ## Restore postgres from DUMP_FILE (owner only)
	@echo "[IMP:7][backup-cron-make][restore] Restoring from DUMP_FILE=$(DUMP_FILE)"
	@if [[ -z "$(DUMP_FILE)" ]]; then \
		echo "[IMP:9][backup-cron-make][restore] ERROR: DUMP_FILE not set" >&2; exit 1; \
	fi
	@if [[ ! -f "$(DUMP_FILE)" ]]; then \
		echo "[IMP:9][backup-cron-make][restore] ERROR: DUMP_FILE not found: $(DUMP_FILE)" >&2; exit 1; \
	fi
	@echo "[IMP:8][backup-cron-make][restore] Delegating restore to postgres module"
	make -C "$(MODULE_DIR)/../postgres" restore DUMP_FILE="$(DUMP_FILE)"

# 🧐 TRAP[DECISION] · 2026-07-18 · — · Custom overrides: backup-cron специфичная lifecycle
# · Rejected: module.mk defaults (120s stop timeout, exec-based backup)
# · Reason: backup-cron требует 120s grace для long-running backup jobs (WAL dump),
# ·   backup триггерит docker exec backup-postgres.sh, restore делегирует postgres модулю
# · Rev: 2026-08-01 ВЫПОЛНЕНО (DevPlan 116 T1) — module.mk получил configurable stop_timeout
# ·   через STOP_TIMEOUT := 120 (этот Makefile) + BACKUP_MODE=custom контракт (D1).
# ·   stop/restart-hard/status/logs теперь даёт module.mk (дубли удалены в B7).
