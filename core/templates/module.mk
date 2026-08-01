# GREP_SUMMARY: module-mk Makefile fragment start stop restart restart-hard status logs backup restore down compose include Makefile.common BACKUP_MODE BACKUP_SOURCE_FILE RESTORE_FILE STOP_TIMEOUT
# STRUCTURE: MODULE_NAME + COMPOSE_FILE + CONTAINER → targets: start ──→ up -d ──→ IMP:9; stop ──→ compose stop --timeout ──→ IMP:9; down ──→ compose down; restart ──→ stop start (soft); restart-hard ──→ down && up -d --force-recreate; status ──→ ps + inspect; logs ──→ compose logs -f --tail; backup/restore ──→ BACKUP_MODE gate (file|custom); help ──→ grep targets
# region MODULE_CONTRACT
## @purpose  Reusable Makefile fragment for Docker module lifecycle management
## @scope    Include in module Makefiles after defining MODULE_NAME, COMPOSE_FILE, CONTAINER
## @usage    Include this fragment after declaring required variables:
##              MODULE_NAME := my-module
##              COMPOSE_FILE := $(MODULE_DIR)/docker-compose.base.yml
##              CONTAINER := my-module
##              include ../../templates/module.mk
## @invariants
##   - MODULE_NAME, COMPOSE_FILE, CONTAINER must be defined before include
##   - All targets use COMPOSE_CMD (docker compose with optional secrets env file)
##   - Env-file loaded from SECRETS_ENV_FILE (default: /run/platform/secrets.env)
##   - stop = compose stop --timeout $(STOP_TIMEOUT) (default 30) — контейнеры СОХРАНЯЮТСЯ
##   - down = compose down — отдельный реальный таргет (удаление контейнеров)
##   - restart = stop start — soft restart БЕЗ пересоздания (сеть/монтирования/состояние сохраняются)
##   - restart-hard = down && up -d --force-recreate — hard restart с пересозданием
##   - up = up -d --force-recreate (документированная семантика — force-recreate)
##   - Backup/restore — ОПЦИОНАЛЬНЫЙ контракт stateful-модулей (D1, DevPlan 116 B7):
##       BACKUP_MODE = none (default) | file | custom
##         file   — generic docker cp: BACKUP_SOURCE_FILE (контейнерный путь) + RESTORE_FILE (локальный)
##         custom — модуль объявляет рецепты backup/restore ПОСЛЕ include (GNU Make last-recipe-wins)
##   - BACKUP_MODE=none (stateless) → таргеты backup/restore НЕ объявляются:
##       make restore на stateless-модуле = «No rule to make target» (не тихий no-op)
##   - agent: NO stop, NO restore — enforced by sudo-whitelist (sudo-whitelist.template)
##   - Test-оверрайды (docker-compose.test.yml) НЕ переопределяют volumes in-place —
##     объявляют новый volume с суффиксом -test (канон volume-rename, core/modules/AGENTS.md T8)
##   - All targets log at IMP:7 minimum, critical paths at IMP:9
##   - Does NOT include build, deploy, build-local targets (removed in DevPlan 020)
## @rationale Template pattern prevents Makefile duplication across modules (RC-6).
##   Targets match Brief_2 §3.2 canonical lifecycle operations. Agent restricted from stop
##   by both sudo-whitelist and convention. Build/deploy targets excluded per Phase 2
##   consolidation: only two hermes-build targets exist at root Makefile.
## @changes
##   2026-07-09 · Created from hermes-agent/Makefile as parameterized template
##   · Removed build/deploy/build-local targets (moved to root Makefile)
##   · Fixed variable assignment (?=) for module overrides — modules set vars before include
##   · Replaced {{MODULE_NAME}} placeholders with $(MODULE_NAME) Make references
##   2026-08-01 · B7 (DevPlan 116 T1, U-25): stop ≠ down; restart явный soft (stop start);
##               down — реальный compose down; backup/restore параметризованы через
##               BACKUP_MODE (none|file|custom) + BACKUP_SOURCE_FILE + RESTORE_FILE;
##               WARNING-путь state.json удалён (U-61); .PHONY сужен условно.
# endregion MODULE_CONTRACT

# ── Overridable variables (modules set these before include) ──
MODULE_NAME ?= my-module
SHELL := /bin/bash
MODULE_DIR := $(shell dirname $(realpath $(firstword $(MAKEFILE_LIST))))
COMPOSE_FILE ?= $(MODULE_DIR)/docker-compose.base.yml
SECRETS_ENV ?= $(or $(SECRETS_ENV_FILE),/run/platform/secrets.env)
CONTAINER ?= $(MODULE_NAME)
COMPOSE_PROFILES ?= $(MODULE_NAME)

# Grace-период для `compose stop` (D2): default 30s, модули могут переопределить ДО include
STOP_TIMEOUT ?= 30

# COMPOSE_PROFILES_MODE: "auto" (default) — use $(COMPOSE_PROFILES) when set, no profiles when empty
#                        "none" — never pass --profile (start all services)
#                        "explicit" — always pass COMPOSE_PROFILES even if empty
COMPOSE_PROFILES_MODE ?= auto

# Build COMPOSE_ARGS: if MODULES variable is set from root Makefile, use it as COMPOSE_PROFILES
ifeq ($(COMPOSE_PROFILES_MODE),auto)
  ifneq ($(COMPOSE_PROFILES),)
    _COMPOSE_PROFILE_FLAG = --profile $(COMPOSE_PROFILES)
  else
    _COMPOSE_PROFILE_FLAG =
  endif
else ifeq ($(COMPOSE_PROFILES_MODE),explicit)
  _COMPOSE_PROFILE_FLAG = --profile $(COMPOSE_PROFILES)
else
  _COMPOSE_PROFILE_FLAG =
endif

COMPOSE_CMD ?= docker compose -f $(COMPOSE_FILE) $(if $(wildcard $(SECRETS_ENV)),--env-file $(SECRETS_ENV),)

include ../../Makefile.common

# ── Backup/restore capability — опциональный контракт stateful-модулей (D1) ──
# BACKUP_MODE: none (default) | file | custom
#   file   — generic docker cp: BACKUP_SOURCE_FILE + RESTORE_FILE
#   custom — модуль объявляет рецепты backup/restore ПОСЛЕ include (last-recipe-wins)
# Статус BACKUP_MODE=none (stateless): таргеты НЕ объявляются — make restore = «No rule»,
# не тихий no-op (U-25 не возвращается). Условная .PHONY обязательна (T1 DevPlan 116).
BACKUP_MODE ?= none
BACKUP_SOURCE_FILE ?=
RESTORE_FILE ?=

ifeq ($(BACKUP_MODE),file)
.PHONY: backup restore
backup: ## Trigger $(MODULE_NAME) state snapshot (docker cp)
	@if [[ -z "$(BACKUP_SOURCE_FILE)" ]]; then \
		echo "[IMP:9][$(MODULE_NAME)-mk][backup] ERROR: BACKUP_SOURCE_FILE not set" >&2; exit 1; fi
	@mkdir -p "$(MODULE_DIR)/backups"
	docker cp $(CONTAINER):$(BACKUP_SOURCE_FILE) \
		"$(MODULE_DIR)/backups/state-$$(date +%Y%m%d-%H%M%S).json"
	@echo "[IMP:9][$(MODULE_NAME)-mk][backup] snapshot saved"
restore: ## Restore state snapshot (RESTORE_FILE=<path>) + soft restart
	@if [[ -z "$(RESTORE_FILE)" || -z "$(BACKUP_SOURCE_FILE)" ]]; then \
		echo "[IMP:9][$(MODULE_NAME)-mk][restore] ERROR: RESTORE_FILE and BACKUP_SOURCE_FILE required" >&2; exit 1; fi
	@if [[ ! -f "$(RESTORE_FILE)" ]]; then \
		echo "[IMP:9][$(MODULE_NAME)-mk][restore] ERROR: RESTORE_FILE not found: $(RESTORE_FILE)" >&2; exit 1; fi
	docker cp "$(RESTORE_FILE)" $(CONTAINER):$(BACKUP_SOURCE_FILE)
	$(COMPOSE_CMD) restart
	@echo "[IMP:8][$(MODULE_NAME)-mk][restore] state restored from $(RESTORE_FILE)"
else ifeq ($(BACKUP_MODE),custom)
.PHONY: backup restore
endif

.PHONY: start stop restart restart-hard status logs build up down help

## start: Start $(MODULE_NAME) container
start: ## Start $(MODULE_NAME) (compose up -d)
	@echo "[IMP:7][$(MODULE_NAME)-mk][start] Starting $(MODULE_NAME)"
	$(COMPOSE_CMD) $(_COMPOSE_PROFILE_FLAG) up -d
	@echo "[IMP:8][$(MODULE_NAME)-mk][start] $(MODULE_NAME) started"

## stop: Stop $(MODULE_NAME) container (owner only — sudo-whitelist)
## agent: NO stop (07 §2.3)
stop: ## Stop $(MODULE_NAME) (compose stop — containers preserved) — OWNER ONLY
	@echo "[IMP:7][$(MODULE_NAME)-mk][stop] Stopping $(MODULE_NAME) (grace $(STOP_TIMEOUT)s)"
	$(COMPOSE_CMD) stop --timeout $(STOP_TIMEOUT)
	@echo "[IMP:9][$(MODULE_NAME)-mk][stop] $(MODULE_NAME) stopped (containers preserved)"

## down: Remove $(MODULE_NAME) containers (compose down)
down: ## Remove $(MODULE_NAME) containers (compose down)
	@echo "[IMP:7][$(MODULE_NAME)-mk][down] Removing $(MODULE_NAME) containers"
	$(COMPOSE_CMD) down --timeout $(STOP_TIMEOUT)
	@echo "[IMP:9][$(MODULE_NAME)-mk][down] $(MODULE_NAME) containers removed"

## restart: Soft restart (stop + start, containers preserved) — наследуется из Makefile.common (T2)
# ⚠️ TRAP[DECISION] · 2026-08-01 · — · restart определён в Makefile.common, НЕ в module.mk
# · Rejected: явное `restart: stop start` в module.mk (DevPlan 116 T1 п.3)
# · Reason: гейт tests/gates/test_restart_consistency.py::test_module_mk_restart_hard_exists
# ·   требует restart_section is None в module.mk (наследование из Makefile.common).
# ·   Семантика идентична после T2 (Makefile.common: restart: stop start, stop = compose stop):
# ·   soft restart без пересоздания — контейнеры/сеть/монтирования сохраняются.
# · Rev: если module.mk потребует модуль-специфичный restart — добавить override ПОСЛЕ include.

## restart-hard: Hard restart $(MODULE_NAME) with --force-recreate
restart-hard: ## Hard restart $(MODULE_NAME) (--force-recreate)
	@echo "[IMP:7][$(MODULE_NAME)-mk][restart-hard] Hard restarting $(MODULE_NAME) with force-recreate"
	$(COMPOSE_CMD) down && $(COMPOSE_CMD) up -d --force-recreate
	@echo "[IMP:8][$(MODULE_NAME)-mk][restart-hard] $(MODULE_NAME) hard restarted"

## status: Show container status with liveness
status: ## Show $(MODULE_NAME) container status
	@echo "[IMP:7][$(MODULE_NAME)-mk][status] $(MODULE_NAME) status"
	$(COMPOSE_CMD) $(_COMPOSE_PROFILE_FLAG) ps
	@echo "[IMP:7][$(MODULE_NAME)-mk][status] Docker health status:"
	docker inspect --format='{{.State.Health.Status}}' $(CONTAINER) 2>/dev/null || echo "container not running"

## logs: Tail container logs
logs: ## Tail $(MODULE_NAME) logs
	@echo "[IMP:7][$(MODULE_NAME)-mk][logs] $(MODULE_NAME) logs (last 100 lines, follow)"
	$(COMPOSE_CMD) $(_COMPOSE_PROFILE_FLAG) logs --tail 100 -f

## build: Build $(MODULE_NAME) docker image
build: ## Build $(MODULE_NAME) image
	@echo "[IMP:7][$(MODULE_NAME)-mk][build] Building $(MODULE_NAME) image"
	$(COMPOSE_CMD) build
	@echo "[IMP:9][$(MODULE_NAME)-mk][build] $(MODULE_NAME) image built"

## up: Start $(MODULE_NAME) container with force-recreate
up: ## Start $(MODULE_NAME) (compose up -d --force-recreate)
	@echo "[IMP:7][$(MODULE_NAME)-mk][up] Starting $(MODULE_NAME) with force-recreate"
	$(COMPOSE_CMD) up -d --force-recreate
	@echo "[IMP:9][$(MODULE_NAME)-mk][up] $(MODULE_NAME) started"

## help: Show available targets
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
