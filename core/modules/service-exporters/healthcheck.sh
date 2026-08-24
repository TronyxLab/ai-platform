#!/usr/bin/env bash
# GREP_SUMMARY: service-exporters healthcheck nginx-exporter redis-exporter postgres-exporter liveness lib/healthcheck
# STRUCTURE: source lib/healthcheck.sh → healthcheck_mode → check_containers → exit0/1
# region MODULE_CONTRACT
## @purpose  Liveness healthcheck for service-exporters module (все три exporter'а — scratch/busybox)
## @scope    Called by module system or Docker HEALTHCHECK override
## @invariants
##   - Default mode: check_docker_health for all containers (nginx/redis/postgres exporter'ы)
##   - MODE=deep: только docker inspect — scratch-образы без shell/wget, HTTP-проб недоступно
##     (readiness внешний через Prometheus up{} + alert-rules — liveness-only канон)
##   - Exits 0 only if all containers are healthy
## @rationale Standard module healthcheck contract per core/modules/AGENTS.md;
##           унаследовано из infra-metrics при split (DevPlan 010 T3.2).
## @changes — 2026-08-22 | DevPlan 010 T3.2 — создан из infra-metrics healthcheck.sh
# endregion MODULE_CONTRACT

set -euo pipefail

echo "[IMP:7][service-exporters-hc][main] Starting service-exporters healthcheck" >&2
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"

CONTAINERS=(
    "${NGINX_EXPORTER_CONTAINER_NAME:-nginx-prometheus-exporter}"
    "${REDIS_EXPORTER_CONTAINER_NAME:-redis-exporter}"
    "${POSTGRES_EXPORTER_CONTAINER_NAME:-postgres-exporter}"
)
MODE="${1:-}"

# Env-параметризация портов (эталон паттерна W10 T10.12; гейт test_gate_healthcheck_drift):
# scratch-образы без shell/wget — HTTP-пробы недоступны, порты документируются для
# смещённых test-оверлеев (19113/19121/19187) и консистентности контракта.
# ⚠️ TRAP[BUG] · 2026-07-18 · HIGH · hardcoded canonical порты ломали smoke (наследие infra-metrics)
NGINX_EXPORTER_PORT="${NGINX_EXPORTER_PORT:-9113}"
REDIS_EXPORTER_PORT="${REDIS_EXPORTER_PORT:-9121}"
POSTGRES_EXPORTER_PORT="${POSTGRES_EXPORTER_PORT:-9187}"

# Default + deep: docker inspect health for all containers
# (scratch images: HTTP deep-check недоступен — канон liveness-only с комментарием)
for container in "${CONTAINERS[@]}"; do
    check_docker_health "$container" || exit 1
done

if [ "$MODE" = "deep" ]; then
    log_imp 8 "healthcheck" "exporters: deep HTTP check unavailable (scratch images) — readiness via Prometheus up{}"
fi

log_imp 9 "healthcheck" "All service-exporters containers healthy"
exit 0
