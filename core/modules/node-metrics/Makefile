# GREP_SUMMARY: node-metrics Makefile start stop restart status logs compose include module-mk
# STRUCTURE: ┌node-metrics targets┐ → ◇ module.mk include → ⊕ multi-service compose lifecycle
# region MODULE_CONTRACT
## @purpose  Makefile for node-metrics module — cAdvisor + Node Exporter compose lifecycle
## @scope    Per-node host/container metrics
## @invariants
##   - MODULE_NAME and CONTAINER defined before include
##   - CONTAINER points to cadvisor (primary metrics service)
##   - All targets delegated to core/templates/module.mk
# endregion MODULE_CONTRACT

MODULE_NAME := node-metrics
CONTAINER := cadvisor
include ../../templates/module.mk

# 🧐 TRAP[DECISION] · 2026-07-18 · — · Minimal module Makefile (наследие infra-metrics)
# · Rejected: inline docker compose commands per target
# · Reason: module.mk provides zero-boilerplate lifecycle for all docker modules
# · Rev: when module needs custom behavior → override targets after include
