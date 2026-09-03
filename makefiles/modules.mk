# GREP_SUMMARY: modules.mk, up, down, down-volumes, restart, status, healthcheck, backup, restore, discover-modules, validate-modules, NODE-guard, fail-loud
# STRUCTURE: ┌compose helpers┐ → ◇ up (preflight+provision+compose) → ◇ down → ◇ restart → ◇ status (NODE-guard F9) → ◇ healthcheck → ◇ backup → ◇ restore → ◇ discover-modules → ◇ validate-modules
# region MODULE_CONTRACT
## @purpose  Module lifecycle targets — compose up/down/restart/status, healthcheck, backup/restore, discover-modules, validate-modules
## @scope    Included from root Makefile; operates on local docker compose stack
## @invariants
##   - up depends on discover-modules + dev-certs (ensure fresh compose include)
##   - up включает preflight-валидацию секретов (compose_preflight.py) первым шагом;
##     SKIP_PREFLIGHT=1 — осознанный обход (План 175 W4.2 — up-safe слит в up)
##   - restart uses soft restart (stop + start), never down && up -d
##   - healthcheck exits 0 only if all modules pass
##   - status NODE-guard (DevPlan 031 T6 / F9): NODE≠local → fail-loud (как healthcheck F-016) —
##     локальный docker compose ps не выдаётся за состояние удалённой ноды
## @rationale Makefile include-split W4-E4: module targets isolated from bootstrap/CI
## @changes 2026-08-16 | План 175 W4.2 — up-safe слит в up (preflight первым шагом)
## @changes 2026-09-03 | DevPlan 031 T6 (F9) — status: NODE-guard fail-loud (пустая локальная
##           таблица больше не молчит вместо состояния ноды)
# endregion MODULE_CONTRACT

.PHONY: up down down-volumes restart status healthcheck backup restore discover-modules validate-modules

## up: Start platform stack (docker compose up -d) — supports MODULES filter + preflight
##   Preflight-валидация секретов (compose_preflight.py) — первый шаг (после dev-certs),
##   блокирует при missing/invalid secrets (DevPlan 049, План 175 W4.2 — слияние up-safe).
##   SKIP_PREFLIGHT=1 — осознанный обход preflight.
##   ⚠️ TRAP[DECISION] · 2026-08-15 · — · provision вызывается рекурсивным make (W2.4)
##   · Rejected: `up: provision` (prerequisite) — SCOPE не передаётся избирательно:
##   ·   `make up` с пустым SCOPE выполнил бы provision SCOPE=all (CI env vars на dev).
##   ·   Прямой вызов provision-environment.sh (старый путь) дублировал канонический
##   ·   таргет мимо SCOPE-логики. Рекурсивный $(MAKE) provision SCOPE=networks,volumes —
##   ·   канонический таргет + явный scope.
##   · Rev: если provision обзаведётся target-specific SCOPE для up — вернуть prerequisite.
up: discover-modules dev-certs
	@echo "[IMP:7][make][up] Starting platform stack..."
	@if [ "$(SKIP_PREFLIGHT)" = "1" ]; then \
		echo "[IMP:8][make][up] SKIP_PREFLIGHT=1 — preflight bypassed"; \
	else \
		echo "[IMP:7][make][up] Running preflight secret validation..."; \
		COMPOSE_PROFILES="$(MODULES)" python3 $(_platform_root)/core/internal/bootstrap/deploy/compose_preflight.py up -d \
			|| { echo "[IMP:10][make][up] PREFLIGHT BLOCKED — missing/invalid secrets (SKIP_PREFLIGHT=1 — осознанный обход)" >&2; exit 1; }; \
	fi
	@$(MAKE) provision SCOPE=networks,volumes
	@if [ -n "$(MODULES)" ]; then \
		_profiles=""; \
		IFS=',' read -ra _mods <<< "$(MODULES)"; \
		for _m in "$${_mods[@]}"; do \
			_profiles="$$_profiles --profile $$_m"; \
		done; \
		echo "[IMP:7][make][up] Using profiles: $(MODULES)"; \
		docker compose $(COMPOSE_BASE_FILES) $$_profiles up -d; \
	else \
		docker compose $(COMPOSE_BASE_FILES) up -d; \
	fi
	@echo "[IMP:9][make][up] Platform stack started"

## down: Stop platform stack (data preserved — NO -v, volumes remain)
##   Destructive teardown is explicit: make down-volumes
down:
	@echo "[IMP:7][make][down] Stopping platform stack (volumes preserved)..."
	@docker compose $(COMPOSE_BASE_FILES) down
	@echo "[IMP:9][make][down] Platform stack stopped (volumes preserved)"

## down-volumes: Stop platform stack AND remove volumes (destructive)
##   ⚠️ Data loss: removes all compose-managed volumes — explicit operator action
down-volumes:
	@echo "[IMP:9][make][down-volumes] WARNING: volumes will be removed (destructive)"
	@docker compose $(COMPOSE_BASE_FILES) down -v
	@echo "[IMP:9][make][down-volumes] Platform stack stopped, volumes removed"

## restart: Soft restart all Docker compose services (stop + start)
restart:
	@echo "[IMP:7][make][restart] Soft restarting all services..."
	@docker compose $(COMPOSE_BASE_FILES) stop && docker compose $(COMPOSE_BASE_FILES) start
	@echo "[IMP:9][make][restart] All services soft restarted"

## status: Show running Docker compose services status
##   NODE-guard (DevPlan 031 T6 / F9): status смотрит ЛОКАЛЬНЫЙ docker compose. Операторская
##   машина с NODE=<remote> НЕ должна молча получать пустую таблицу локального стека вместо
##   состояния ноды (F9: NODE молча игнорировался → пустая таблица ≠ нода) — fail-loud,
##   зеркало healthcheck-контракта (F-016). Состояние удалённой ноды: project-status/e2e-verify.
status:
	@if [[ -n "$(NODE)" && "$(NODE)" != "local" ]]; then \
		echo "[IMP:10][make][status] ERROR: NODE=$(NODE) задан, но status показывает ЛОКАЛЬНЫЙ docker compose." >&2; \
		echo "  Для удалённой ноды используйте: make project-status NAME=<project> NODE=$(NODE)" >&2; \
		echo "  или make e2e-verify NODE=$(NODE) (HTTP+TLS sweep) / make healthcheck NODE=$(NODE)." >&2; \
		echo "  NODE=local / без NODE → локальная проверка стека." >&2; \
		exit 1; \
	fi
	@echo "[IMP:7][make][status] Displaying running services..."
	@docker compose $(COMPOSE_BASE_FILES) ps
	@echo "[IMP:9][make][status] Status displayed"

## healthcheck: Run all module healthcheck.sh scripts via entrypoint — exit 0 only if all pass
healthcheck:
	@echo "[IMP:7][make][healthcheck] Running all module healthchecks via entrypoint..."
	@bash $(_platform_root)/core/entrypoints/healthcheck.sh

## backup: Trigger platform backup via backup-cron module
##   Delegates to core/modules/backup-cron/Makefile backup target
backup:
	@echo "[IMP:7][make][backup] Triggering backup via backup-cron module..."
	@make -C core/modules/backup-cron backup
	@echo "[IMP:9][make][backup] Backup complete"

## restore: Restore platform from backup via backup-cron module
##   Usage: make restore DUMP_FILE=<path>
##   Delegates to core/modules/backup-cron/Makefile restore target
restore:
	@echo "[IMP:7][make][restore] Restoring from backup via backup-cron module..."
	@if [[ -z "$(DUMP_FILE)" ]]; then \
		echo "[IMP:9][make][restore] ERROR: DUMP_FILE not set — usage: make restore DUMP_FILE=<path>" >&2; \
		exit 1; \
	fi
	@make -C core/modules/backup-cron restore DUMP_FILE="$(DUMP_FILE)"
	@echo "[IMP:9][make][restore] Restore complete"

## discover-modules: Auto-discover modules and regenerate docker-compose.yml include section
discover-modules:
	@echo "[IMP:7][make][discover-modules] Discovering modules..."
	@python3 core/internal/bootstrap/discover_modules.py
	@echo "[IMP:9][make][discover-modules] Module discovery complete"

## validate-modules: Run D5 module.yaml contract validator (Wave 3 W3-E5).
##   Invoked from CI after lint step. Calls validate_module_yaml.py --all.
validate-modules:
	@echo "[IMP:7][make][validate-modules] Running D5 module.yaml contract validator..."
	@python3 $(_platform_root)/core/internal/scripts/validate_module_yaml.py --all
	@echo "[IMP:9][make][validate-modules] D5 module contract validation complete"
