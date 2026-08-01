#!/usr/bin/env bash
# GREP_SUMMARY: node-lifecycle bootstrap init update orchestrator idempotent state-machine delegation
# STRUCTURE: ▶ --mode {init|update} → ┌arg parser┐ → ○ resolve NODE_YAML + TOR_ENABLED → ┌python3 lifecycle/cli.py --mode $MODE ...┐ → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Thin shell facade (<80 LOC) delegating phase execution to lifecycle/cli.py
## @scope    Called from bootstrap.sh (--mode init) or node-update.sh (--mode update)
## @invariants CLI arg parsing, NODE_YAML resolution, TOR_ENABLED detection, SOPS_AGE_KEY fallback
## @rationale Все step-логика — в Python (cli.py → state_machine.py); compat-заглушка state_machine.py покрывает прямые запуски (B9 T1, CS-7)
# endregion MODULE_CONTRACT
set -euo pipefail; MODE=""; RESUME_MODE=false; FORCE_MODE=""; DRY_RUN_MODE=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; SM_SCRIPT="${SCRIPT_DIR}/lifecycle/cli.py"
# ⚠️ TRAP[BUG] · 2026-07-31 · P1 · PYTHONPATH отсутствовал → ModuleNotFoundError: core (script-path не добавляет CWD в sys.path); Fix: корень + lifecycle/ (паттерн converge.sh:64)
export PYTHONPATH="${SCRIPT_DIR}/../../..:${SCRIPT_DIR}/lifecycle:${PYTHONPATH:-}"
[[ "${1:-}" == "--mode" ]] && { shift; MODE="${1:-}"; shift || true; }
[[ "$MODE" == @(init|update) ]] || { echo "[IMP:10][node-lifecycle][args] ERROR: --mode init|update required" >&2; exit 1; }
while [[ $# -gt 0 ]]; do case "$1" in
    --resume) RESUME_MODE=true; shift ;;
    --force) FORCE_MODE=true; shift ;;
    --dry-run) DRY_RUN_MODE=true; shift ;;
    --node-name) export NODE_NAME="$2"; shift 2 ;;
    --node-yaml) export NODE_YAML="$2"; shift 2 ;;
    --owner-key) [[ -z "${PLATFORM_OWNER_KEY:-}" ]] && export PLATFORM_OWNER_KEY="$2"; shift 2 ;;
    --ci-deploy-key) [[ -z "${PLATFORM_CI_DEPLOY_KEY:-}" ]] && export PLATFORM_CI_DEPLOY_KEY="$2"; shift 2 ;;
    --age-secret-key) [[ -z "${AGE_SECRET_KEY:-}" ]] && export AGE_SECRET_KEY="$2"; shift 2 ;;
    --docker-hub-username) [[ -z "${DOCKER_HUB_USERNAME:-}" ]] && export DOCKER_HUB_USERNAME="$2"; shift 2 ;;
    --docker-hub-token) [[ -z "${DOCKER_HUB_TOKEN:-}" ]] && export DOCKER_HUB_TOKEN="$2"; shift 2 ;;
    --postgres-password) [[ -z "${POSTGRES_PASSWORD:-}" ]] && export POSTGRES_PASSWORD="$2"; shift 2 ;;
    --age-secret-key-file) [[ -f "$2" ]] || { echo "[IMP:10][args] file not found: $2" >&2; exit 1; }; AGE_SECRET_KEY="$(<"$2")"; export AGE_SECRET_KEY; shift 2 ;;
    --docker-hub-username-file) [[ -f "$2" ]] || { echo "[IMP:10][args] file not found: $2" >&2; exit 1; }; DOCKER_HUB_USERNAME="$(<"$2")"; export DOCKER_HUB_USERNAME; shift 2 ;;
    --docker-hub-token-file) [[ -f "$2" ]] || { echo "[IMP:10][args] file not found: $2" >&2; exit 1; }; DOCKER_HUB_TOKEN="$(<"$2")"; export DOCKER_HUB_TOKEN; shift 2 ;;
    --postgres-password-file) [[ -f "$2" ]] || { echo "[IMP:10][args] file not found: $2" >&2; exit 1; }; POSTGRES_PASSWORD="$(<"$2")"; export POSTGRES_PASSWORD; shift 2 ;;
    --tor-bridges-file) export TOR_BRIDGES_FILE="$2"; shift 2 ;;
    --skip-tor-verify) export SKIP_TOR_VERIFY="true"; shift ;;
    --auto-reconcile) export AUTO_RECONCILE="true"; shift ;;
    --context) export CONTEXT="$2"; shift 2 ;;
    --platform-domain) export PLATFORM_DOMAIN="$2"; shift 2 ;;
    --) shift; break ;;
    -*) echo "[IMP:10][node-lifecycle][args] ERROR: Unknown: $1" >&2; exit 1 ;;
    *) break ;;
esac; done
[[ -z "${AGE_SECRET_KEY:-}" && -n "${SOPS_AGE_KEY:-}" ]] && export AGE_SECRET_KEY="$SOPS_AGE_KEY" && echo "[IMP:8][node-lifecycle][args] AGE_SECRET_KEY from SOPS_AGE_KEY" >&2
source "${SCRIPT_DIR}/../../lib/paths.sh"; CORE_DIR="${PATHS_CORE_DIR}"
source "${CORE_DIR}/lib/logging.sh"; source "${CORE_DIR}/lib/secrets.sh"
STEP=0; STEP_ERRORS=(); __LOG_PREFIX="${MODE/init/bootstrap}"; __LOG_PREFIX="${__LOG_PREFIX/update/node-update}"
step_start() { STEP=$((STEP+1)); log_step "$1" "START" "${2:-}"; }
step_done() { log_step "$1" "DONE" "${2:-}"; }
step_skip() { log_step "$1" "SKIP" "${2:-}"; }
step_warn() { log_step "$1" "WARN" "${2:-}"; STEP_ERRORS+=("Step ${STEP}: $1 — $2"); }
_delegate() { python3 "${SM_SCRIPT}" "$@"; }
detect_tor_enabled(){
    # ⚠️ TRAP[BUG] · 2026-07-31 · P1 · set -e убивал bootstrap при tor.enabled=false — [[ ]] && в конце функции = rc1; Fix: if-форма без else = rc0
    TOR_ENABLED=false; local val
    [[ -n "${NODE_YAML:-}" && -f "$NODE_YAML" ]] && val="$(python3 -m core.internal.shared.node_yaml --file "$NODE_YAML" --get tor.enabled --default "false" 2>/dev/null || echo "false")"
    if [[ "${val:-false}" == "true" ]]; then TOR_ENABLED=true; fi
}
main() {
    if [[ "$MODE" == "init" ]]; then
        for var in NODE_NAME NODE_YAML PLATFORM_OWNER_KEY; do [[ -z "${!var:-}" ]] && { echo "[IMP:10][bootstrap] FAIL: Missing ${var}" >&2; exit 1; }; done
        detect_tor_enabled; export TOR_ENABLED
        if [[ -z "${SKIP_PREFLIGHT:-}" && -f "${SCRIPT_DIR}/preflight.py" ]]; then
            echo "[IMP:8][node-lifecycle][preflight] Running pre-flight checks" >&2
            PREFLIGHT_RESULT="$(python3 "${SCRIPT_DIR}/preflight.py" --node-yaml "${NODE_YAML}" --context "${CONTEXT:-}" --node-name "${NODE_NAME}" 2>&1)" || { echo "$PREFLIGHT_RESULT" >&2; echo "[IMP:10][node-lifecycle][preflight] Pre-flight checks FAILED" >&2; exit 1; }
            echo "$PREFLIGHT_RESULT" | python3 "${SCRIPT_DIR}/preflight.py" --parse-warnings 2>&1 || true
        fi
        _delegate --mode init --node-name "${NODE_NAME}" --node-yaml "${NODE_YAML}" \
            ${PLATFORM_OWNER_KEY:+--owner-key "$PLATFORM_OWNER_KEY"} ${PLATFORM_CI_DEPLOY_KEY:+--ci-deploy-key "$PLATFORM_CI_DEPLOY_KEY"} \
            ${CONTEXT:+--context "$CONTEXT"} ${FORCE_MODE:+--force}
    elif [[ "$MODE" == "update" ]]; then
        [[ -z "${NODE_NAME:-}" ]] && { echo "[IMP:10][node-lifecycle][update] NODE_NAME required" >&2; exit 1; }
        if [[ -z "${NODE_YAML:-}" || ! -f "${NODE_YAML:-}" ]]; then
            source "${CORE_DIR}/lib/node-resolver.sh"
            NODE_YAML="$(resolve_node_yaml "${NODE_NAME}")" || { echo "[IMP:10][node-lifecycle][update] Cannot resolve NODE_YAML for node=${NODE_NAME}" >&2; exit 1; }
            export NODE_YAML
        fi
        _delegate --mode update --node-name "${NODE_NAME}" --node-yaml "${NODE_YAML}" \
            ${CONTEXT:+--context "$CONTEXT"} ${FORCE_MODE:+--force}
    fi
}
main "$@"
