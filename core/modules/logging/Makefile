# GREP_SUMMARY: logging Makefile start stop restart status logs backup compose include module-mk
# region MODULE_CONTRACT
## @purpose  Makefile for logging module — Loki + Promtail compose lifecycle
## @scope    Loki log storage + Promtail log collector
## @invariants
##   - MODULE_NAME and CONTAINER defined before include
##   - CONTAINER points to loki (primary storage service)
##   - All targets delegated to core/templates/module.mk
# endregion MODULE_CONTRACT

MODULE_NAME := logging
CONTAINER := loki
include ../../templates/module.mk
