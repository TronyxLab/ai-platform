# GREP_SUMMARY: monitoring Makefile start stop restart status logs backup compose include module-mk
# region MODULE_CONTRACT
## @purpose  Makefile for monitoring module — Prometheus + Grafana compose lifecycle
## @scope    Prometheus metrics TSDB + Grafana dashboards
## @invariants
##   - MODULE_NAME and CONTAINER defined before include
##   - CONTAINER points to grafana (primary UI service)
##   - All targets delegated to core/templates/module.mk
# endregion MODULE_CONTRACT

MODULE_NAME := monitoring
CONTAINER := grafana
include ../../templates/module.mk
