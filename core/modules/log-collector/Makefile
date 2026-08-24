# GREP_SUMMARY: log-collector Makefile start stop restart status logs compose include module-mk alloy
# STRUCTURE: ┌log-collector targets┐ → ◇ module.mk include → ⊕ multi-service compose lifecycle
# region MODULE_CONTRACT
## @purpose  Makefile for log-collector module — Grafana Alloy log collector compose lifecycle
## @scope    Alloy log collector (all-nodes placement; DevPlan 010 T3.1)
## @invariants
##   - MODULE_NAME and CONTAINER defined before include
##   - CONTAINER points to alloy (primary collector service)
##   - All targets delegated to core/templates/module.mk
## @rationale Minimal module Makefile — lifecycle through module.mk (zero-boilerplate)
## @changes 2026-08-22 | DevPlan 010 T3.1 — Created (split из core/modules/logging/)
# endregion MODULE_CONTRACT

MODULE_NAME := log-collector
CONTAINER := alloy
include ../../templates/module.mk
