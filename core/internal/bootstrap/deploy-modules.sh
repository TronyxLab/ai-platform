#!/usr/bin/env bash
# GREP_SUMMARY: deploy-modules docker system sudoers orphan context-overlay python-delegation thin-facade
# STRUCTURE: ▶ argparse → ⚡ validate(root+NODE_YAML) → ◇ provision → ◇ docker_login → ⚡ exec python3 deploy_orchestrator.py → ⎋ {0,1,2}
# region MODULE_CONTRACT
## @purpose  Thin shell facade (≤50 LOC) — ALL module deploy logic (routing, deploy, sudoers, orphans, severity) → deploy/deploy_orchestrator.py (DevPlan 100)
## @scope    node-lifecycle.sh --mode init/update: arg parsing, root/NODE_YAML check, provisioner, docker login, exec python3 orchestrator
## @invariants Shell: args/root/NODE_YAML/provision/docker-login. Python: routing/deploy/sudoers/orphans/severity.
##   - exec python3 replaces shell (same PID) — exit {0,1,2} auto-propagates (D2); docker_login writes ~/.docker/config.json (R2)
##   - PYTHONPATH exported for core.* imports (converge.sh:64 pattern); set -euo pipefail preserved
##   - plan 012 T9: --strict-init → deploy_orchestrator --strict-init (failed≠∅ ИЛИ crit>0 → exit 2;
##     вызывает φ8 INIT; update φ12 НЕ передаёт флаг — контракт WARN→0 сохранён)
## @changes   2026-07-31 · DevPlan 100 TASK-2 — routing+severity extracted to deploy_orchestrator.py (260→≤50 LOC)
##            2026-08-26 · plan 012 T9 — +--strict-init passthrough
# endregion MODULE_CONTRACT

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/paths.sh"
source "${SCRIPT_DIR}/../../lib/docker.sh"
__LOG_PREFIX="deploy-modules"

# ── Arg parsing ──
MODULES_FILTER=""; SKIP_PROVISION=false; STRICT_INIT=false
while [[ $# -gt 0 ]]; do case "$1" in
    --modules) MODULES_FILTER="$2"; shift 2 ;; --skip-provision) SKIP_PROVISION=true; shift ;;
    --strict-init) STRICT_INIT=true; shift ;;
    *) break ;; esac
done
[[ "$(id -u)" -eq 0 ]] || { echo "[IMP:10][deploy-modules] ERROR: must run as root" >&2; exit 1; }
NODE_YAML="${NODE_YAML:-}"; [[ -n "$NODE_YAML" && -f "$NODE_YAML" ]] || { echo "[IMP:10][deploy-modules] ERROR: NODE_YAML not set" >&2; exit 1; }

# ── Network/volume provision (bash — system-level, must stay) ──
# ⚠️ TRAP[BUG] · 2026-08-05 · HI · `|| true` маскировал провал provision → молчаливый деплой без сетей/volumes
# · Symptom: provision-environment.sh (--scope networks/volumes) падал, но фасад продолжал
# ·   deploy → контейнеры на несозданных сетях/volumes → тихие 502/ошибки монтирования
# ·   (латентный класс C/F, DevPlan 136 W2 T2.4).
# · Root: строки 31-32 `bash provision-environment.sh --scope networks || true` — exit-код глотался.
# · Fix: провал provision → ВИДИМЫЙ FAIL [IMP:10] + exit 1 (Fail-Fast, никакой маскировки).
# ·   Fallback (T2.3): provision-environment.sh отсутствует → прямой вызов provisioner.py,
# ·   который создаёт ВСЕ сети/volumes из platform-env.yaml (было: только proxy-net).
# · Prevention: provision — обязательный пре-шаг деплоя; его провал не может быть non-fatal.
# · DevPlan 136 W2 T2.3/T2.4: тест mock provision exit 1 → фасад логирует FAIL и НЕ продолжает молча.
if [[ "${SKIP_PROVISION}" != "true" ]]; then
    if [[ -f "${PATHS_INTERNAL_DIR}/provision-environment.sh" ]]; then
        if ! bash "${PATHS_INTERNAL_DIR}/provision-environment.sh" --scope networks; then
            echo "[IMP:10][deploy-modules][provision] FATAL: network provision failed (scope=networks)" >&2
            exit 1
        fi
        if ! bash "${PATHS_INTERNAL_DIR}/provision-environment.sh" --scope volumes; then
            echo "[IMP:10][deploy-modules][provision] FATAL: volume provision failed (scope=volumes)" >&2
            exit 1
        fi
    else
        # Fallback (T2.3): все сети/volumes из platform-env.yaml через provisioner.py (не только proxy-net)
        if ! python3 "${PATHS_INTERNAL_DIR}/provisioner.py" --scope networks --platform-env "${PATHS_INTERNAL_DIR}/../../platform-env.yaml"; then
            echo "[IMP:10][deploy-modules][provision] FATAL: fallback network provision failed (provisioner.py)" >&2
            exit 1
        fi
        if ! python3 "${PATHS_INTERNAL_DIR}/provisioner.py" --scope volumes --platform-env "${PATHS_INTERNAL_DIR}/../../platform-env.yaml"; then
            echo "[IMP:10][deploy-modules][provision] FATAL: fallback volume provision failed (provisioner.py)" >&2
            exit 1
        fi
    fi
fi

docker_login; ghcr_login

# ⚠️ TRAP[CROSS-LAYER] provision-llm.sh call REMOVED — internal/ must not call entrypoints/
# Provisioning happens in state_machine.py (post-deploy lifecycle step), not here.
# ── Exec Python orchestrator (DevPlan 100): routing + deploy + severity in Python ──
export PYTHONPATH="${SCRIPT_DIR}/../../..:${PYTHONPATH:-}"
exec python3 "${SCRIPT_DIR}/deploy/deploy_orchestrator.py" \
    --node-yaml "$NODE_YAML" \
    --modules-dir "$PATHS_MODULES_DIR" \
    --core-dir "$PATHS_CORE_DIR" \
    --templates-dir "$PATHS_TEMPLATES_DIR" \
    --modules-filter "$MODULES_FILTER" \
    --deploy-parallel "${DEPLOY_PARALLEL:-false}" --deploy-orchestrator "${DEPLOY_ORCHESTRATOR:-false}" \
    --strict-init "$STRICT_INIT"
