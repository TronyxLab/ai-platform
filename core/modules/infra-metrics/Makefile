# GREP_SUMMARY: infra-metrics Makefile start stop restart status logs backup compose include module-mk
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
