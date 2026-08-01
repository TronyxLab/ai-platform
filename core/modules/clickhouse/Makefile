# GREP_SUMMARY: clickhouse Makefile start stop restart status logs help
# STRUCTURE: ┌clickhouse targets┐ → ◇ module.mk include → ⊕ compose lifecycle
# region MODULE_CONTRACT
## @purpose  Module lifecycle management for clickhouse Docker module
## @scope    Called by platform users per role; sudo-whitelist enforces per-role restrictions
## @invariants
##   - All docker compose calls use docker-compose.base.yml
##   - env-file loaded from SECRETS_ENV_FILE (default: /run/platform/secrets.env)
##   - No backup/restore — stateless module (D1 DevPlan 116: контракт сужен, таргеты НЕ объявляются)
##   - All targets log at IMP:7 minimum
## @rationale Targets match standard module lifecycle; minimal override — include standard template
# endregion MODULE_CONTRACT

MODULE_NAME := clickhouse

include ../../templates/module.mk

# 🧐 TRAP[DECISION] · 2026-07-18 · — · Minimal module Makefile — clickhouse lifecycle through module.mk
# · Rejected: inline docker compose commands per target
# · Reason: module.mk provides zero-boilerplate lifecycle for all docker modules
# · Rev: when module needs custom behavior → override targets after include
