# GREP_SUMMARY: modules.mk, up, up-safe, down, restart, status, healthcheck, backup, restore, discover-modules, validate-modules
# STRUCTURE: ┌compose helpers┐ → ◇ up-safe → ◇ up → ◇ down → ◇ restart → ◇ status → ◇ healthcheck → ◇ backup → ◇ restore → ◇ discover-modules → ◇ validate-modules
# region MODULE_CONTRACT
## @purpose  Module lifecycle targets — compose up/down/restart/status, healthcheck, backup/restore, discover-modules, validate-modules
## @scope    Included from root Makefile; operates on local docker compose stack
## @invariants
##   - up depends on discover-modules + dev-certs (ensure fresh compose include)
##   - restart uses soft restart (stop + start), never down && up -d
##   - healthcheck exits 0 only if all modules pass
## @rationale Makefile include-split W4-E4: module targets isolated from bootstrap/CI
# endregion MODULE_CONTRACT

.PHONY: up-safe compose-safe-up up down down-volumes restart status healthcheck backup restore discover-modules validate-modules

## up-safe: Start platform stack with preflight secret validation
##   Delegates to core/entrypoints/compose-wrapper.sh which runs compose_preflight.py
##   before docker compose up — blocks if required secrets are missing (DevPlan 049)
##   MODULES empty → COMPOSE_PROFILES passthrough (compose reads .env), like `make up`
up-safe:
	@echo "[IMP:7][make][up-safe] Running preflight and starting stack..."
	@if [ -n "$(MODULES)" ]; then \
		COMPOSE_PROFILES="$(MODULES)" core/entrypoints/compose-wrapper.sh up -d; \
	else \
		core/entrypoints/compose-wrapper.sh up -d; \
	fi

## compose-safe-up: Deprecated alias for up-safe (backward compatibility)
compose-safe-up: up-safe

## up: Start platform stack (docker compose up -d) — supports MODULES filter
up: discover-modules dev-certs
	@echo "[IMP:7][make][up] Starting platform stack..."
	# provision — канонический таргет (make provision SCOPE=...), up вызывает напрямую для двух scope
	@bash $(_platform_root)/core/internal/provision-environment.sh --scope networks --scope volumes \
		--platform-env $(_platform_root)/platform-env.yaml
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
status:
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
