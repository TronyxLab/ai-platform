#!/usr/bin/env bash
# GREP_SUMMARY: logging healthcheck loki alloy liveness lib/healthcheck
# STRUCTURE: source lib/healthcheck.sh → healthcheck_mode → check_containers → exit0/1
# region MODULE_CONTRACT
## @purpose  Liveness healthcheck for logging module: Loki + Alloy (promtail EOL, 164 W1-5)
## @scope    Called by module system or Docker HEALTHCHECK override
## @invariants
##   - Default mode: check_docker_health for all containers
##   - MODE=deep: HTTP endpoint check on loki (port 3100 /ready)
##   - Имена контейнеров и порты env-параметризованы (паттерн infra-metrics, W10 T10.12):
##     LOKI_CONTAINER_NAME/ALLOY_CONTAINER_NAME/LOKI_PORT
##   - Exits 0 only if all containers are healthy
## @rationale Standard module healthcheck contract per core/modules/AGENTS.md
## @changes — 2026-08-05 | DevPlan 136 W10 T10.12 — env-параметризация имён/портов
## @changes — 2026-08-13 | DevPlan 164 W1-5 — promtail→Alloy (EOL REPLACE)
# endregion MODULE_CONTRACT

set -euo pipefail

echo "[IMP:7][logging-hc][main] Starting logging healthcheck" >&2
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"

CONTAINERS=(
    "${LOKI_CONTAINER_NAME:-loki}"
    "${ALLOY_CONTAINER_NAME:-alloy}"
)
LOKI_PORT="${LOKI_PORT:-3100}"
MODE="${1:-}"

if [ "$MODE" = "deep" ]; then
    # 🧐 TRAP[DECISION] · 2026-07-15 · — · Collector HTTP /ready check replaced with docker inspect
    # · Rejected: check_http http://127.0.0.1:9080/ready
    # · Reason: Alloy — внутренний коллектор; порт на хост не мапится (module.yaml).
    # ·   Образ Alloy minimal — HTTP-проба изнутри не нужна (liveness /bin/alloy -version).
    # · Rev: при появлении потребности в readiness-пробе коллектора — включить http-блок в config.alloy.

    log_imp 8 "healthcheck" "Deep mode: checking Docker health + HTTP endpoints (LOKI_PORT=${LOKI_PORT})"

    # Step 1: Check Docker health status for all containers
    for container in "${CONTAINERS[@]}"; do
        check_docker_health "$container" || exit 1
    done

    # Step 2: Service-specific diagnostics via check_http (Loki)
    check_http "http://127.0.0.1:${LOKI_PORT}/ready" "200" || exit 1

    log_imp 9 "healthcheck" "All logging deep checks passed"
    exit 0
fi

# Default: docker inspect health for all containers
for container in "${CONTAINERS[@]}"; do
    check_docker_health "$container" || exit 1
done

log_imp 9 "healthcheck" "All logging containers healthy"
exit 0
