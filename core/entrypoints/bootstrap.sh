#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint bootstrap node orchestrator node-resolver resolve ssh scp age-key detect-age-key dry-run
# STRUCTURE: ▶ init → ◇ --help? → ◇ --resolve? → ○ resolve_node_yaml → ○ extract owner_key → ○ extract host → ◇ host? → ⚡ SCP core+node-configs → ⚡ SSH orchestrator | ⎋ exec orchestrator --resume
# region MODULE_CONTRACT
## @purpose  Entry-point for `make bootstrap-node`: resolves node.yaml → detects SSH host → SCPs
##           core + node-configs → SSH-executes orchestrator (or locally if no host).
##           Thin-wrapper: delegates SCP/SSH to internal/ libraries.
## @scope    Called ONLY from Makefile. Owns: usage, detect_age_key, auto_detect_node_name, main.
##           Delegates scp_to_server + prepare_ssh_opts → scp-deliver.sh, build_ssh_cmd → remote-cmd.sh.
## @invariants
##   - 4 functions max: usage, detect_age_key, auto_detect_node_name, main
##   - --auto-reconcile: passed through to node-lifecycle.sh → converge --reconcile (DevPlan 025 W4)
##   - NODE=<name> is OPTIONAL in --resolve mode (auto-detection from /opt/node-configs/)
##   - AGE_SECRET_KEY detection chain: env → SOPS_AGE_KEY env → AGE_SECRET_KEY_FILE
##   - Missing AGE key = WARN (not fatal)
##   - --dry-run prints SCP + SSH commands without executing
## 🧐 TRAP[DECISION] · 2026-07-21 · — · Encrypted secrets path
## · Context secrets лежат в <node-configs-dir>/secrets/<NODE>.enc.yaml
## · bootstrap ищет node-configs/secrets/ — скопировать файл перед bootstrap если нет
## · Rejected: symlink или fallback search (overhead > benefit)
## · Rev: если CI научится auto-deploy, пересмотреть доставку secrets через core
##   - --resume always passed to node-lifecycle.sh --mode init for idempotency
##   - Without --resolve: passes all args through to node-lifecycle.sh --mode init (manual mode)
## @rationale Thin-wrapper per DevPlan 020 T4+T15. Auto-SSH + SCP eliminates manual rsync + SSH steps.
## @changes 2026-07-17 | T15 — Layer re-homing: scp_to_server+prepare_ssh_opts→scp-deliver.sh, build_ssh_cmd→remote-cmd.sh
##           2026-07-17 | Lifecycle refactoring: ORCHESTRATOR→NODE_LIFECYCLE, --mode init passthrough
##           2026-07-21 | W4: +--auto-reconcile flag passthrough (DevPlan 025)
# endregion MODULE_CONTRACT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${CORE_DIR}/lib/paths.sh"
source "${CORE_DIR}/internal/bootstrap/scp-deliver.sh"
source "${CORE_DIR}/internal/bootstrap/remote-cmd.sh"
source "${CORE_DIR}/lib/args.sh"
NODE_LIFECYCLE="${PATHS_INTERNAL_DIR}/bootstrap/node-lifecycle.sh"

USAGE_SCRIPT="bootstrap.sh"
USAGE_DESC="Entry-point for idempotent node bootstrap. Resolves node.yaml, detects SSH host, SCPs core+node-configs, delegates to node-lifecycle.sh."
USAGE_OPTIONS=(
    "--node <name>       Node name to bootstrap"
    "--resolve           Extract owner_key and host from node.yaml"
    "--dry-run           Print SCP+SSH commands without executing"
    "--auto-reconcile    Passthrough to node-lifecycle.sh --reconcile"
)

# 🧐 TRAP[DECISION] · 2026-07-21 · — · bootstrap.sh passthrough arg pattern
# · Rejected: full parse_args adoption (passthrough pattern incompatible)
# · Reason: minimal W1 scope, bootstrap.sh forwards unknown args via PASSTHROUGH_ARGS
# · Rev: Wave 4 — redesign passthrough into parse_args spec

## @purpose  Detect AGE_SECRET_KEY from env chain via shared/age_key.py (DevPlan 078 T2)
##           Delegates to Python single-source-of-truth replacing duplicate shell logic.
##           Returns: key to stdout + exit 0 (found) / exit 1 (not found).
detect_age_key() {
    local age_key_script="${CORE_DIR}/internal/shared/age_key.py"
    if [[ -f "$age_key_script" ]]; then
        python3 "$age_key_script" 2>/dev/null && return 0 || return 1
    fi
    # Fallback: direct env check if Python module unavailable
    if [[ -n "${AGE_SECRET_KEY:-}" ]]; then
        echo "${AGE_SECRET_KEY}"; return 0
    fi
    if [[ -n "${SOPS_AGE_KEY:-}" ]]; then
        echo "${SOPS_AGE_KEY}"; return 0
    fi
    return 1
}
## @purpose  Auto-detect node name from /opt/node-configs/ directories
auto_detect_node_name() {
    local d="/opt/node-configs"
    [[ -d "$d" ]] || { echo "[IMP:8][bootstrap][auto-detect] ${d} does not exist" >&2; return 1; }
    local candidates=() dir
    for dir in "$d"/*/; do
        [[ -d "$dir" ]] || continue
        local b; b="$(basename "$dir")"
        [[ "$b" == "scripts" || "$b" == "secrets" ]] && continue
        candidates+=("$b")
    done
    [[ ${#candidates[@]} -eq 0 ]] && { echo "[IMP:10][bootstrap][auto-detect] No node directories found" >&2; return 1; }
    [[ ${#candidates[@]} -gt 1 ]] && { echo "[IMP:10][bootstrap][auto-detect] Multiple directories: ${candidates[*]}" >&2; return 1; }
    echo "${candidates[0]}"
    echo "[IMP:9][bootstrap][auto-detect] Auto-detected node: ${candidates[0]}" >&2
    return 0
}
NODE_NAME=""; RESOLVE_MODE=false; DRY_RUN=false; PASSTHROUGH_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --node|--node-name) NODE_NAME="$2"; shift 2 ;;
        --help|-h) usage "$USAGE_SCRIPT" "${USAGE_DESC:-}" "${USAGE_OPTIONS[@]:-}" ;;
        --resolve) RESOLVE_MODE=true; shift ;;
        --dry-run) DRY_RUN=true; shift ;;
        --auto-reconcile) PASSTHROUGH_ARGS+=("--auto-reconcile"); shift ;;
        *) PASSTHROUGH_ARGS+=("$1"); shift ;;
    esac
done

## @purpose  Execute bootstrap workflow — resolve mode or passthrough
main() {
    if ! $RESOLVE_MODE; then exec "${NODE_LIFECYCLE}" "--mode" "init" "$@"; fi

    # ── Validate/resolve node name ──────────────────────────────────
    if [[ -z "$NODE_NAME" ]]; then
        echo "[IMP:8][bootstrap][entrypoint] Auto-detecting node name"
        NODE_NAME=$(auto_detect_node_name) || {
            echo "[IMP:10][bootstrap][entrypoint] FATAL: Cannot detect node — use make bootstrap-node NODE=<name>" >&2; exit 1
        }
        echo "[IMP:9][bootstrap][entrypoint] Auto-detected NODE_NAME=${NODE_NAME}"
    fi

    source "${CORE_DIR}/lib/node-resolver.sh"
    echo "[IMP:8][bootstrap][entrypoint] Resolving node.yaml for node=${NODE_NAME}"
    NODE_YAML=$(resolve_node_yaml "$NODE_NAME" "${PLATFORM_ROOT}" "${HOME}/projects") || {
        echo "[IMP:10][bootstrap][entrypoint] FATAL: Cannot resolve node.yaml" >&2; exit 1
    }

    echo "[IMP:8][bootstrap][entrypoint] Extracting owner_key"
    OWNER_KEY=$(python3 "${CORE_DIR}/internal/bootstrap/yaml_helpers.py" "${NODE_YAML}" "node.owner_key" 2>/dev/null) || true
    [[ -n "$OWNER_KEY" ]] || { echo "[IMP:10][bootstrap][entrypoint] FATAL: owner_key not found" >&2; exit 1; }
    echo "[IMP:9][bootstrap][entrypoint] Resolved: node=${NODE_NAME}"

    # ── Extract ci_deploy_key ───────────────────────────────────────────
    # ⚠️ TRAP[BUG] · 2026-07-17 · P1 · ci_deploy_key not consumed by bootstrap channel
    # · Symptom: ci_deploy_key declared in node.schema.json (node.ci_deploy_key) but never
    #   extracted by bootstrap.sh — step_6_create_ci_deploy_user always skipped on first
    #   bootstrap (no --ci-deploy-key / PLATFORM_CI_DEPLOY_KEY env). Every first bootstrap
    #   of a new node required manual env override.
    # · Root: bootstrap.sh extracted owner_key (line 114) but not ci_deploy_key — the key
    #   was schema-valid but had no delivery channel to node-lifecycle.sh.
    # · Fix: extract ci_deploy_key by same python3+yaml pattern as owner_key; env-приоритет:
    #   явный PLATFORM_CI_DEPLOY_KEY (env) > node.yaml.
    # · Prevention: schema-based contract test — every key in node.schema.json → extracted
    #   by bootstrap.sh and passed to step_*.
    # · Source: .ai/plans/007-dance-site-launch/02-Debt.md D1
    echo "[IMP:8][bootstrap][entrypoint] Extracting ci_deploy_key"
    CI_DEPLOY_KEY=$(python3 "${CORE_DIR}/internal/bootstrap/yaml_helpers.py" "${NODE_YAML}" "node.ci_deploy_key" 2>/dev/null) || true
    # Env override: explicit PLATFORM_CI_DEPLOY_KEY takes priority over node.yaml
    if [[ -n "${PLATFORM_CI_DEPLOY_KEY:-}" ]]; then
        CI_DEPLOY_KEY="$PLATFORM_CI_DEPLOY_KEY"
        echo "[IMP:8][bootstrap][entrypoint] CI_DEPLOY_KEY from env PLATFORM_CI_DEPLOY_KEY (override)"
    fi
    if [[ -n "$CI_DEPLOY_KEY" ]]; then
        echo "[IMP:9][bootstrap][entrypoint] ci_deploy_key resolved"
    else
        echo "[IMP:8][bootstrap][entrypoint] ci_deploy_key not set — ci-deploy restricted key setup will be skipped"
    fi

    # ── Extract PLATFORM_DOMAIN + CONTEXT from node.yaml (F4) ──
    echo "[IMP:8][bootstrap][entrypoint] Extracting PLATFORM_DOMAIN and CONTEXT"
    PLATFORM_DOMAIN=$(python3 "${CORE_DIR}/internal/bootstrap/yaml_helpers.py" "${NODE_YAML}" "domain" 2>/dev/null) || true
    CONTEXT=$(python3 "${CORE_DIR}/internal/bootstrap/yaml_helpers.py" "${NODE_YAML}" "context" 2>/dev/null) || true
    if [[ -z "$CONTEXT" ]]; then
        CONTEXT=$(python3 "${CORE_DIR}/internal/bootstrap/yaml_helpers.py" "${NODE_YAML}" "contexts.0.name" 2>/dev/null) || true
    fi
    [[ -n "$PLATFORM_DOMAIN" ]] && echo "[IMP:9][bootstrap][entrypoint] PLATFORM_DOMAIN=${PLATFORM_DOMAIN}"
    [[ -n "$CONTEXT" ]] && echo "[IMP:9][bootstrap][entrypoint] CONTEXT=${CONTEXT}"

    SSH_HOST="$(extract_node_host "${NODE_YAML}")" || { echo "[IMP:8][bootstrap][entrypoint] WARN: No SSH host — local mode" >&2; SSH_HOST=""; }
    DETECTED_AGE_KEY="$(detect_age_key)" || DETECTED_AGE_KEY=""

    # ── Local bootstrap ─────────────────────────────────────────────
    if [[ -z "${SSH_HOST}" ]]; then
        echo "[IMP:9][bootstrap][entrypoint] No SSH host — executing node-lifecycle.sh --mode init LOCALLY"
        local a=(--node-name "$NODE_NAME" --node-yaml "$NODE_YAML" --owner-key "$OWNER_KEY" --resume)
        [[ -n "${DETECTED_AGE_KEY}" ]] && a+=(--age-secret-key "${DETECTED_AGE_KEY}")
        [[ -n "${CI_DEPLOY_KEY}" ]] && a+=(--ci-deploy-key "${CI_DEPLOY_KEY}")
        [[ -n "${PLATFORM_DOMAIN:-}" ]] && a+=(--platform-domain "${PLATFORM_DOMAIN}")
        [[ -n "${CONTEXT:-}" ]] && a+=(--context "${CONTEXT}")
        a+=("${PASSTHROUGH_ARGS[@]}")
        $DRY_RUN && { echo "[IMP:8][bootstrap][dry-run] DRY-RUN: ${NODE_LIFECYCLE} ${a[*]}" >&2; exit 0; }
        exec "${NODE_LIFECYCLE}" "--mode" "init" "${a[@]}"
    fi

    echo "[IMP:9][bootstrap][entrypoint] SSH host: ${SSH_HOST} — REMOTE bootstrap"
    NODE_CONFIGS_DIR="$(dirname "$(dirname "${NODE_YAML}")")"
    if $DRY_RUN; then
        echo "[IMP:8][bootstrap][dry-run] DRY-RUN: Would rsync core/ + platform-env.yaml + Makefile → ${SSH_HOST}"
        echo "[IMP:8][bootstrap][dry-run] DRY-RUN: Would rsync node-configs/ → /opt/node-configs/"
        [[ -d "${NODE_CONFIGS_DIR}/${NODE_NAME}/secrets" ]] && echo "[IMP:8][bootstrap][dry-run] DRY-RUN: Would rsync secrets/"
    else
        prepare_ssh_opts "${SSH_HOST}" "init"
        scp_to_server "${SSH_HOST}" "${NODE_NAME}" "${NODE_CONFIGS_DIR}" "${CORE_DIR}" || { echo "[IMP:10][bootstrap][entrypoint] FATAL: SCP phase failed" >&2; exit 1; }
        echo "[IMP:9][bootstrap][scp] SCP phase complete"
    fi

    REMOTE_CMD="$(build_ssh_cmd "${NODE_NAME}" "${OWNER_KEY}" "${CI_DEPLOY_KEY}" "${DETECTED_AGE_KEY}" "${PASSTHROUGH_ARGS[@]}")"
    local masked_remote_cmd="${REMOTE_CMD}"
    if [[ -n "${DETECTED_AGE_KEY}" ]]; then
        local m; m="$(echo "${DETECTED_AGE_KEY}" | cut -c1-8)"
        masked_remote_cmd="${REMOTE_CMD//${DETECTED_AGE_KEY}/<AGE_KEY:${m}...>}"
    fi
    $DRY_RUN && {
        echo "[IMP:8][bootstrap][dry-run] DRY-RUN: ssh ${SSH_OPTS[*]} root@${SSH_HOST} ${masked_remote_cmd}" >&2
        echo "[IMP:9][bootstrap][dry-run] DRY-RUN complete" >&2; exit 0
    }
    echo "[IMP:9][bootstrap][entrypoint] SSH node-lifecycle.sh --mode init on root@${SSH_HOST}"
    exec ssh "${SSH_OPTS[@]}" "root@${SSH_HOST}" "${REMOTE_CMD}"
}

main "$@"
