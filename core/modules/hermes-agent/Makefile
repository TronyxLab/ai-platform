# GREP_SUMMARY: hermes-agent Makefile start stop restart status logs help
# STRUCTURE: ┌hermes-agent targets┐ → ◇ module.mk include → ⊕ compose lifecycle
# region MODULE_CONTRACT
## @purpose  Module lifecycle management for hermes-agent Docker module
## @scope    Called by platform users per role; sudo-whitelist enforces per-role restrictions
## @invariants
##   - All docker compose calls use docker-compose.base.yml
##   - env-file loaded from SECRETS_ENV_FILE (default: /run/platform/secrets.env)
##   - All targets log at IMP:7 minimum
## @rationale Targets match standard module lifecycle; minimal override — include standard template
# endregion MODULE_CONTRACT

MODULE_NAME := hermes-agent
# Stateful (D1): generic docker cp snapshot — state.json (U-61).
# BACKUP_SOURCE_FILE = контейнерный путь файла состояния hermes-agent.
BACKUP_MODE := file
BACKUP_SOURCE_FILE := /app/state.json

include ../../templates/module.mk

# 🧐 TRAP[DECISION] · 2026-07-18 · — · Minimal module Makefile — hermes-agent lifecycle through module.mk
# · Rejected: inline docker compose commands per target
# · Reason: module.mk provides zero-boilerplate lifecycle for all docker modules;
# ·   hermes-agent build (L1/L2) managed by root Makefile hermes-build-* targets
# · Rev: when module needs custom behavior → override targets after include
