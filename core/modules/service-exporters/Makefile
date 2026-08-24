# GREP_SUMMARY: service-exporters Makefile start stop restart status logs compose include module-mk
# STRUCTURE: ┌service-exporters targets┐ → ◇ module.mk include → ⊕ multi-service compose lifecycle
# region MODULE_CONTRACT
## @purpose  Makefile for service-exporters module — Nginx/Redis/Postgres exporters compose lifecycle
## @scope    Service metrics bridges
## @invariants
##   - MODULE_NAME and CONTAINER defined before include
##   - CONTAINER points to postgres-exporter (primary service metrics)
##   - All targets delegated to core/templates/module.mk
# endregion MODULE_CONTRACT

MODULE_NAME := service-exporters
CONTAINER := postgres-exporter
include ../../templates/module.mk

# 🧐 TRAP[DECISION] · 2026-07-18 · — · Minimal module Makefile (наследие infra-metrics)
# · Rejected: inline docker compose commands per target
# · Reason: module.mk provides zero-boilerplate lifecycle for all docker modules
# · Rev: when module needs custom behavior → override targets after include
