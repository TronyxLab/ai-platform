#!/usr/bin/env bash
# GREP_SUMMARY: node-lifecycle bootstrap init update orchestrator idempotent sequential-steps checkpoint-resume docker ufw nginx sops users sudoers audit telegram scp-deploy no-git deploy-modules healthcheck per-step-content-hash state-machine delegation
# STRUCTURE: ▶ --mode {init|update} → ┌arg parser┐ → ○ resolve NODE_YAML + TOR_ENABLED → ┌python3 state_machine.py --mode $MODE ...┐ → ⎋ exit 0|1; checkpoint_step preserves .done
# region MODULE_CONTRACT
## @purpose  Thin shell facade (W4-E2) delegating all step logic to lifecycle/state_machine.py
## @scope    Called from bootstrap.sh (--mode init) or node-update.sh (--mode update)
## @invariants CLI arg parsing, NODE_YAML resolution, TOR_ENABLED detection, SOPS_AGE_KEY fallback
## @rationale 1301→<200 LOC extraction. Shell retains orchestration; Python owns step execution.
## @changes 2026-07-22 | W4-E2: All step logic → state_machine.py
# endregion MODULE_CONTRACT
set -euo pipefail; MODE=""; RESUME_MODE=false; FORCE_MODE=false; DRY_RUN_MODE=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; SM_SCRIPT="${SCRIPT_DIR}/lifecycle/state_machine.py"
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
    --age-secret-key-file) [[ -f "$2" ]] || { echo "[IMP:10][args] ERROR: file not found: $2" >&2; exit 1; }; AGE_SECRET_KEY="$(< "$2")"; export AGE_SECRET_KEY; shift 2 ;;
    --docker-hub-username-file) [[ -f "$2" ]] || { echo "[IMP:10][args] ERROR: file not found: $2" >&2; exit 1; }; DOCKER_HUB_USERNAME="$(< "$2")"; export DOCKER_HUB_USERNAME; shift 2 ;;
    --docker-hub-token-file) [[ -f "$2" ]] || { echo "[IMP:10][args] ERROR: file not found: $2" >&2; exit 1; }; DOCKER_HUB_TOKEN="$(< "$2")"; export DOCKER_HUB_TOKEN; shift 2 ;;
    --postgres-password-file) [[ -f "$2" ]] || { echo "[IMP:10][args] ERROR: file not found: $2" >&2; exit 1; }; POSTGRES_PASSWORD="$(< "$2")"; export POSTGRES_PASSWORD; shift 2 ;;
    --tor-bridges-file) export TOR_BRIDGES_FILE="$2"; shift 2 ;;
    --skip-tor-verify) export SKIP_TOR_VERIFY="true"; shift ;;
    --auto-reconcile) export AUTO_RECONCILE="true"; shift ;;
    --) shift; break ;;
    -*) echo "[IMP:10][node-lifecycle][args] ERROR: Unknown argument: $1" >&2; exit 1 ;;
    *) break ;;
esac; done

[[ -z "${AGE_SECRET_KEY:-}" && -n "${SOPS_AGE_KEY:-}" ]] && export AGE_SECRET_KEY="$SOPS_AGE_KEY" && echo "[IMP:8][node-lifecycle][args] AGE_SECRET_KEY from SOPS_AGE_KEY" >&2

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../lib/paths.sh"; CORE_DIR="${PATHS_CORE_DIR}"
source "${CORE_DIR}/lib/logging.sh"; source "${CORE_DIR}/lib/checkpoint.sh"; source "${CORE_DIR}/internal/bootstrap/content-hash.sh"
source "${CORE_DIR}/lib/secrets.sh"; source "${CORE_DIR}/lib/yaml_read.sh"
STEP=0; STEP_ERRORS=(); __LOG_PREFIX="${MODE/init/bootstrap}"; __LOG_PREFIX="${__LOG_PREFIX/update/node-update}"
step_start() { STEP=$((STEP+1)); log_step "$1" "START" "${2:-}"; }
step_done() { log_step "$1" "DONE" "${2:-}"; }
step_skip() { log_step "$1" "SKIP" "${2:-}"; }
step_warn() { log_step "$1" "WARN" "${2:-}"; STEP_ERRORS+=("Step ${STEP}: $1 — $2"); }
_step_hash() { local s="$1"; shift; compute_step_hash "$s" "${SCRIPT_DIR}/node-lifecycle.sh" "$@"; }
_delegate() { python3 "${SM_SCRIPT}" "$@"; }

# Step functions (thin wrappers → state_machine.py)
step_1_ssh_access(){ _delegate --mode "${MODE}" --run-step 1; }
step_2_apt_deps(){ _delegate --mode "${MODE}" --run-step 2; }
step_3_tor_proxy(){ _delegate --mode "${MODE}" --run-step 3; }
step_4_install_docker(){ _delegate --mode "${MODE}" --run-step 4; }
step_5_create_platform_user(){ _delegate --mode "${MODE}" --run-step 5; }
step_6_create_ci_deploy_user(){ _delegate --mode "${MODE}" --run-step 6; }
step_6b_create_projects_base(){ _delegate --mode "${MODE}" --run-step 7; }
step_7_firewall(){ _delegate --mode "${MODE}" --run-step 8; }
step_8_verify_core(){ _delegate --mode "${MODE}" --run-step 9; }
step_9_verify_node_configs(){ _delegate --mode "${MODE}" --run-step 10; }
step_10_decrypt_secrets(){ _delegate --mode "${MODE}" --run-step 11; }
step_11_read_node_yaml(){ _delegate --mode "${MODE}" --run-step 14; }
step_12_ghcr_auth(){ _delegate --mode "${MODE}" --run-step 15; }
step_13_sudoers(){ _delegate --mode "${MODE}" --run-step 16; }
step_14_node_update(){ _delegate --mode "${MODE}" --run-step 18; }
step_15_converge(){ _delegate --mode "${MODE}" --run-step 19; }
step_16_audit_log(){ _delegate --mode "${MODE}" --run-step 20; }
step_17_telegram(){ _delegate --mode "${MODE}" --run-step 21; }
update_step_1_verify_core(){ _delegate --mode "${MODE}" --run-step 1; }
update_step_2_provision(){ _delegate --mode "${MODE}" --run-step 2; }
update_step_2_5_deliver_overlays(){ _delegate --mode "${MODE}" --run-step 3; }
update_step_3_ssl_provision(){
    local ssl_script="${SM_SCRIPT}" secrets_env="${SECRETS_ENV_FILE:-/run/platform/secrets.env}"
    [[ -f "$secrets_env" ]] && { set -a; source "$secrets_env"; set +a; unset HTTP_PROXY HTTPS_PROXY
        echo "[IMP:9][ssl-provision] WEBNAMES_API_KEY loaded from ${secrets_env}" >&2; } ||
        log_step "ssl-provision" "WARN" "${secrets_env} missing — cert renewal may fail"
    python3 "$ssl_script" --mode "${MODE}" --run-step 4; }
update_step_4_deploy_modules(){ _delegate --mode "${MODE}" --run-step 5; }
update_step_6_healthcheck(){ _delegate --mode "${MODE}" --run-step 6; }

# TOR_ENABLED detected from node.yaml (tor.enabled key, default false)
detect_tor_enabled(){
    TOR_ENABLED=false; local val
    [[ -n "${NODE_YAML:-}" && -f "$NODE_YAML" ]] && val="$(yaml_read_key "$NODE_YAML" "tor.enabled" 2>/dev/null || echo "false")"
    [[ "${val:-false}" == "true" ]] && TOR_ENABLED=true
    echo "[IMP:8][node-lifecycle][tor] TOR_ENABLED=${TOR_ENABLED} from node.yaml" >&2
}

main() {
    if [[ "$MODE" == "init" ]]; then
        echo "[IMP:9][node-lifecycle][main] ==============================" >&2
        echo "[IMP:9][node-lifecycle][main] Platform Node Bootstrap START (--mode init)" >&2
        echo "[IMP:9][node-lifecycle][main] ==============================" >&2
        for var in NODE_NAME NODE_YAML PLATFORM_OWNER_KEY; do [[ -z "${!var:-}" ]] && { echo "[IMP:10][bootstrap][validate] FAIL: Missing ${var}" >&2; exit 1; }; done
        log_step "validate-env" "OK" "All required env vars present"
        CHECKPOINT_DIR="/var/lib/platform/.bootstrap-checkpoints"
        if [[ "${DRY_RUN_MODE:-}" == "true" ]]; then
            echo "[IMP:9][node-lifecycle][dry-run] ===== DRY RUN: init mode =====" >&2
            echo "[IMP:9][node-lifecycle][dry-run] Bootstrap DRY RUN — no mutations, exit 0" >&2
            _delegate --mode init --dry-run --node-name "${NODE_NAME}" --node-yaml "${NODE_YAML}"; exit 0
        fi
        [[ "$FORCE_MODE" == "true" ]] && rm -rf "$CHECKPOINT_DIR"
        mkdir -p "$CHECKPOINT_DIR"; detect_tor_enabled; export TOR_ENABLED
        CHECKPOINT_STEP_HASH="$(_step_hash "ssh-access")"           checkpoint_step "ssh-access" step_1_ssh_access
        CHECKPOINT_STEP_HASH="$(_step_hash "apt-deps")"             checkpoint_step "apt-deps" step_2_apt_deps
        if [[ "${TOR_ENABLED:-false}" == "true" ]]; then CHECKPOINT_STEP_HASH="$(_step_hash "tor-proxy")" checkpoint_step "tor-proxy" step_3_tor_proxy
        else echo "[IMP:8][node-lifecycle][main] Tor disabled — skipping tor-proxy" >&2; fi
        CHECKPOINT_STEP_HASH="$(_step_hash "install-docker")"       checkpoint_step "install-docker" step_4_install_docker
        CHECKPOINT_STEP_HASH="$(_step_hash "user-platform")"        checkpoint_step "user-platform" step_5_create_platform_user
        CHECKPOINT_STEP_HASH="$(_step_hash "user-ci-deploy")"       checkpoint_step "user-ci-deploy" step_6_create_ci_deploy_user
        CHECKPOINT_STEP_HASH="$(_step_hash "projects-base")"        checkpoint_step "projects-base" step_6b_create_projects_base
        CHECKPOINT_STEP_HASH="$(_step_hash "firewall")"             checkpoint_step "firewall" step_7_firewall
        CHECKPOINT_STEP_HASH="$(_step_hash "verify-core")"          checkpoint_step "verify-core" step_8_verify_core
        CHECKPOINT_STEP_HASH="$(_step_hash "verify-node-configs")"  checkpoint_step "verify-node-configs" step_9_verify_node_configs
        CHECKPOINT_STEP_HASH="$(_step_hash "decrypt-secrets")"      checkpoint_step "decrypt-secrets" step_10_decrypt_secrets
        CHECKPOINT_STEP_HASH="$(_step_hash "read-node-yaml")"       checkpoint_step "read-node-yaml" step_11_read_node_yaml
        CHECKPOINT_STEP_HASH="$(_step_hash "ghcr-auth")"            checkpoint_step "ghcr-auth" step_12_ghcr_auth
        CHECKPOINT_STEP_HASH="$(_step_hash "sudoers")"              checkpoint_step "sudoers" step_13_sudoers
        # Full init: remaining steps via state_machine.py (step_14_node_update through step_17_telegram)
        _delegate --mode init --node-name "${NODE_NAME}" --node-yaml "${NODE_YAML}" \
            ${PLATFORM_OWNER_KEY:+--owner-key "$PLATFORM_OWNER_KEY"} \
            ${PLATFORM_CI_DEPLOY_KEY:+--ci-deploy-key "$PLATFORM_CI_DEPLOY_KEY"} \
            ${FORCE_MODE:+--force}
        echo "[IMP:9][node-lifecycle][main] ==============================" >&2
        echo "[IMP:9][node-lifecycle][main] Bootstrap COMPLETE (warnings: ${#STEP_ERRORS[@]})" >&2
        echo "[IMP:9][node-lifecycle][main] ==============================" >&2
    elif [[ "$MODE" == "update" ]]; then
        echo "[IMP:9][node-lifecycle][main] ==============================" >&2
        echo "[IMP:9][node-lifecycle][main] Node Update START (--mode update)" >&2
        echo "[IMP:9][node-lifecycle][main] ==============================" >&2
        [[ -z "${NODE_NAME:-}" ]] && { echo "[IMP:10][node-lifecycle][update] FATAL: NODE_NAME required" >&2; exit 1; }
        if [[ -z "${NODE_YAML:-}" || ! -f "${NODE_YAML:-}" ]]; then
            source "${CORE_DIR}/lib/node-resolver.sh"
            NODE_YAML="$(resolve_node_yaml "${NODE_NAME}")" || { echo "[IMP:10][node-lifecycle][update] FATAL: Cannot resolve NODE_YAML for node=${NODE_NAME}" >&2
                echo "  Tried candidate paths: platform_root/node-configs/, projects/*/node-configs/, /opt/node-configs/" >&2; exit 1; }
            export NODE_YAML
        fi
        if [[ "${DRY_RUN_MODE:-}" == "true" ]]; then
            echo "[IMP:9][node-lifecycle][dry-run] ===== DRY RUN: update mode =====" >&2
            echo "[IMP:9][node-lifecycle][dry-run] Steps: verify-core → provision → ssl → deploy-modules → healthcheck → converge" >&2
            echo "[IMP:9][node-lifecycle][dry-run] Node update DRY RUN — no mutations, exit 0" >&2; exit 0
        fi
        CHECKPOINT_DIR="/var/lib/platform/.bootstrap-checkpoints"
        [[ "$FORCE_MODE" == "true" ]] && rm -rf "$CHECKPOINT_DIR"
        mkdir -p "$CHECKPOINT_DIR"
        _do_update_steps() {
            CHECKPOINT_STEP_HASH="$(_step_hash "verify-core")"      checkpoint_step "verify-core" update_step_1_verify_core
            CHECKPOINT_STEP_HASH="$(_step_hash "provision")"        checkpoint_step "provision" update_step_2_provision
            CHECKPOINT_STEP_HASH="$(_step_hash "deliver-overlays")" checkpoint_step "deliver-overlays" update_step_2_5_deliver_overlays
            CHECKPOINT_STEP_HASH="$(_step_hash "ssl-provision")"    checkpoint_step "ssl-provision" update_step_3_ssl_provision
            CHECKPOINT_STEP_HASH="$(_step_hash "deploy-modules")"   checkpoint_step "deploy-modules" update_step_4_deploy_modules
            CHECKPOINT_STEP_HASH="$(_step_hash "healthcheck-all")"  checkpoint_step "healthcheck-all" update_step_6_healthcheck
            _delegate --mode update --node-name "${NODE_NAME}" --node-yaml "${NODE_YAML}" ${FORCE_MODE:+--force}
        }
        audit_step "node-update:${NODE_NAME:-<unset>}" _do_update_steps
        echo "[IMP:9][node-lifecycle][main] ==============================" >&2
        echo "[IMP:9][node-lifecycle][main] Node Update COMPLETE (warnings: ${#STEP_ERRORS[@]})" >&2
        echo "[IMP:9][node-lifecycle][main] ==============================" >&2
    fi
}
main "$@"
