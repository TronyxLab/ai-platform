# GREP_SUMMARY: monitoring Makefile start stop restart status logs compose include module-mk
# STRUCTURE: ┌monitoring targets┐ → ◇ module.mk include → ⊕ multi-service compose lifecycle
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

# 🧐 TRAP[DECISION] · 2026-07-18 · — · Minimal module Makefile — lifecycle through module.mk
# · Rejected: inline docker compose commands per target
# · Reason: module.mk provides standard lifecycle; envsubst-генерация prometheus.yml
# ·   реализована через init-контейнер в docker-compose.base.yml, не через Makefile
# · Rev: если потребуется pre-up генерация конфига — добавить в Makefile override
