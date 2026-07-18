# GREP_SUMMARY: infra-metrics Makefile start stop restart status logs backup compose include module-mk
# STRUCTURE: ┌infra-metrics targets┐ → ◇ module.mk include → ⊕ multi-service compose lifecycle
# region MODULE_CONTRACT
## @purpose  Makefile for infra-metrics module — cAdvisor + Node Exporter + Nginx Exporter compose lifecycle
## @scope    Infrastructure metrics collection
## @invariants
##   - MODULE_NAME and CONTAINER defined before include
##   - CONTAINER points to cadvisor (primary metrics service)
##   - All targets delegated to core/templates/module.mk
# endregion MODULE_CONTRACT

MODULE_NAME := infra-metrics
CONTAINER := cadvisor
include ../../templates/module.mk

# 🧐 TRAP[DECISION] · 2026-07-18 · — · Minimal module Makefile — infra-metrics lifecycle through module.mk
# · Rejected: inline docker compose commands per target
# · Reason: module.mk provides zero-boilerplate lifecycle for all docker modules
# · Rev: when module needs custom behavior → override targets after include
