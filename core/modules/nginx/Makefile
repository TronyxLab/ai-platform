# GREP_SUMMARY: nginx module docker Makefile reverse-proxy stateless
# STRUCTURE: ┌MODULE_NAME:=nginx┐ → include module.mk → all docker targets
# region MODULE_CONTRACT
## @purpose  Nginx reverse proxy — stateless, config-driven, no spool or persistent data
## @scope    HTTP/HTTPS ingress for all platform services (Grafana, Hermes, Langfuse, Loki, Prometheus)
## @invariants
##   - MODULE_NAME := nginx — stateless reverse proxy, no spool_dir, no resources
##   - include ../../templates/module.mk — все канонические таргеты
##   - Config bind-mounts via NGINX_CONF_DIR (read-only)
##   - Ports 80 (HTTP) + 443 (HTTPS) — host-mapped
##   - Healthcheck: check_http (не systemctl) — macOS-совместимость
## @rationale Nginx — единственный модуль без spool_dir/persistent volumes.
##   Стандартный контракт модуля (core/modules/AGENTS.md).
# endregion MODULE_CONTRACT

MODULE_NAME := nginx
include ../../templates/module.mk

# 🧐 TRAP[DECISION] · 2026-07-18 · — · Minimal module Makefile — includes module.mk без overrides
# · Rejected: inline docker compose commands per target
# · Reason: module.mk предоставляет zero-boilerplate lifecycle; overrides только для
# ·   module-specific поведения (redis PONG, postgres pg_dump, backup-cron docker exec)
# · Rev: когда модулю требуется кастомный таргет → override ПОСЛЕ include (GNU Make last-recipe-wins)
