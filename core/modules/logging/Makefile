# GREP_SUMMARY: logging Makefile start stop restart status logs compose include module-mk loki
# STRUCTURE: ┌logging targets┐ → ◇ module.mk include → ⊕ multi-service compose lifecycle
# region MODULE_CONTRACT
## @purpose  Makefile for logging module — Loki compose lifecycle (DevPlan 010 T3.1: alloy → log-collector)
## @scope    Loki log storage (singleton-хранилище)
## @invariants
##   - MODULE_NAME and CONTAINER defined before include
##   - CONTAINER points to loki (primary storage service)
##   - All targets delegated to core/templates/module.mk
# endregion MODULE_CONTRACT

MODULE_NAME := logging
CONTAINER := loki
include ../../templates/module.mk

# 🧐 TRAP[DECISION] · 2026-07-18 · — · Minimal module Makefile — logging lifecycle through module.mk
# · Rejected: inline docker compose commands per target
# · Reason: module.mk provides zero-boilerplate lifecycle for all docker modules
# · Rev: when module needs custom behavior → override targets after include
