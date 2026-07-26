# GREP_SUMMARY: module-mk Makefile fragment start stop restart status logs backup compose include Makefile.common
# STRUCTURE: MODULE_NAME + COMPOSE_FILE + CONTAINER → targets: start ──→ up -d & wait-for-ready ──→ IMP:9; stop ──→ down --timeout 30 ──→ IMP:9; restart ──→ down && up -d; status ──→ ps + inspect + curl; logs ──→ compose logs -f --tail; backup ──→ curl POST + docker cp snapshot ──→ IMP:9; help ──→ grep targets
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
  ##   - agent: NO stop, NO restore — enforced by sudo-whitelist (sudo-whitelist.template)
  ## 🧐 TRAP[DECISION] · 2026-07-10 · — · redis/Makefile has NO stop/restart/logs targets · Rejected: adding stop/restart/logs targets · Reason: deferred, sudo-whitelist design intentionally blocks agent stop (module.mk:15) · Rev: if sudo-whitelist policy changes for redis
##   - backup target: triggers state snapshot via HTTP POST, then docker cp
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
# endregion MODULE_CONTRACT

# ── Overridable variables (modules set these before include) ──
MODULE_NAME ?= my-module
SHELL := /bin/bash
MODULE_DIR := $(shell dirname $(realpath $(firstword $(MAKEFILE_LIST))))
COMPOSE_FILE ?= $(MODULE_DIR)/docker-compose.base.yml
SECRETS_ENV ?= $(or $(SECRETS_ENV_FILE),/run/platform/secrets.env)
CONTAINER ?= $(MODULE_NAME)
COMPOSE_PROFILES ?= $(MODULE_NAME)

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

.PHONY: start stop restart restart-hard status logs build up down backup restore help

## start: Start $(MODULE_NAME) container
start: ## Start $(MODULE_NAME) (compose up -d)
	@echo "[IMP:7][$(MODULE_NAME)-mk][start] Starting $(MODULE_NAME)"
	$(COMPOSE_CMD) $(_COMPOSE_PROFILE_FLAG) up -d
	@echo "[IMP:8][$(MODULE_NAME)-mk][start] $(MODULE_NAME) started"

## stop: Stop $(MODULE_NAME) container (owner only — sudo-whitelist)
## agent: NO stop, NO restore (07 §2.3)
stop: ## Stop $(MODULE_NAME) (compose down) — OWNER ONLY
	@echo "[IMP:7][$(MODULE_NAME)-mk][stop] Stopping $(MODULE_NAME) (grace 30s)"
	$(COMPOSE_CMD) down --timeout 30
	@echo "[IMP:9][$(MODULE_NAME)-mk][stop] $(MODULE_NAME) stopped"

## restart-hard: Hard restart $(MODULE_NAME) with --force-recreate
# ALIAS: hard restart with --force-recreate
restart-hard: ## Hard restart $(MODULE_NAME) (--force-recreate)
	@echo "[IMP:7][$(MODULE_NAME)-mk][restart-hard] Hard restarting $(MODULE_NAME) with force-recreate"
	$(COMPOSE_CMD) down && $(COMPOSE_CMD) up -d --force-recreate
	@echo "[IMP:8][$(MODULE_NAME)-mk][restart-hard] $(MODULE_NAME) hard restarted"

## down: Alias for stop (discoverability)
# ALIAS for discoverability — see 'stop' for implementation
down: stop ## ALIAS: stop the module (discoverability alias)

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

## backup: Trigger module state snapshot backup
backup: ## Trigger $(MODULE_NAME) state snapshot
	@echo "[IMP:7][$(MODULE_NAME)-mk][backup] Triggering $(MODULE_NAME) state snapshot"
	docker cp $(CONTAINER):/app/state.json "$(MODULE_DIR)/backups/state-$$(date +%Y%m%d-%H%M%S).json" 2>/dev/null || \
		echo "[IMP:9][$(MODULE_NAME)-mk][backup] WARNING: No state.json found in container" >&2

## help: Show available targets
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
