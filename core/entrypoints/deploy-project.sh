#!/usr/bin/env bash
# GREP_SUMMARY: deploy-project entrypoint direct-deploy emergency bypass-ci audit DEPLOY-DIRECT build-deliver-verb
# STRUCTURE: ▶ validate(args) → ◇ resolve NODE→host → ⊕ extract org from path → ◆ build_deliver_verb (→ Phase B: shared/platform_deliver) → ◆ tar+ssh deliver → ◆ ssh deploy → ◆ verify → ⎋ audit summary
# region MODULE_CONTRACT
## @purpose  Direct project deploy bypassing CI (emergency fallback).
##           tar + ssh platform-deliver → ssh deploy.sh → audit DEPLOY-DIRECT.
## @scope    Called from Makefile: `make deploy-project PROJECT=<dir> NODE=<node>`
## @invariants
##   - PROJECT must contain ai-platform.yaml + docker-compose.yml|compose.yaml
##   - NODE must resolve to SSH host via NODE_HOST_MAP (K4/K5)
##   - ORG extracted from PROJECT path (~/projects/<org>/<name>/)
##   - Audit log on VPS marked DEPLOY-DIRECT
##   - shellcheck clean, set -euo pipefail
## @rationale Separate entrypoint from deploy.sh because deploy.sh is SSH forced-command
##   for VPS-side operations. This entrypoint runs on dev machine (or CI) and orchestrates
##   tar+ssh. Different responsibility, different environment.
## @changes 2026-07-21 · T3 — Initial implementation (Direct deploy entrypoint)
##           2026-07-21 · W1: Added pre-flight VPS readiness check before deliver_payload (vps-readiness.sh)
##           2026-07-21 · W6: Added --launch mode: post-deploy URL display
# endregion MODULE_CONTRACT

# 🧐 TRAP[DECISION] · 2026-07-21 · — · Direct deploy uses same ci-deploy key, not separate key
# · Rejected: separate SSH key for direct deploy (adds key management burden)
# · Reason: ci-deploy already has forced-command restriction — security boundary is the
#   authorized_keys command= prefix, not the key itself. Direct deploy adds audit logging
#   (DEPLOY-DIRECT) but does not expand ci-deploy's permissions.
# · Rev: if direct deploy abuse is detected → consider dedicated key with rate limiting

set -euo pipefail

# ── Path resolution ─────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
__LOG_PREFIX="deploy-project"

# Source libraries
# shellcheck source=core/lib/logging.sh
source "${SCRIPT_DIR}/../lib/logging.sh" 2>/dev/null || {
    echo "[FATAL] Cannot source logging.sh" >&2
    exit 1
}
# shellcheck source=core/lib/node-resolver.sh
source "${SCRIPT_DIR}/../lib/node-resolver.sh"

# ── Defaults ────────────────────────────────────────────────────
CI_DEPLOY_KEY="${PLATFORM_CI_DEPLOY_KEY_FILE:-${HOME}/.ssh/ci_deploy_key}"
SSH_OPTS="-o BatchMode=yes -o StrictHostKeyChecking=accept-new -i ${CI_DEPLOY_KEY}"

# Globals (set by parse_args)
PROJECT_DIR=""
NODE=""
SKIP_VERIFY=0
DRY_RUN=0
LAUNCH_MODE=0
ORG=""
PROJECT_NAME=""
SSH_HOST=""
GIT_SHA="unknown"
ENV="production"

# region PARSE_ARGS
## @purpose  Parse CLI arguments: --project, --node, --skip-verify, --dry-run
## @io       ⇥ "$@" → sets globals PROJECT_DIR, NODE, SKIP_VERIFY, DRY_RUN
## ⎋ exit 1 if --project or --node missing
## @complexity O(n) where n = arg count
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --project)
                if [[ -z "${2:-}" ]]; then
                    log_imp 10 "args" "FATAL: --project requires a directory argument"
                    exit 1
                fi
                PROJECT_DIR="$2"
                shift 2
                ;;
            --node)
                if [[ -z "${2:-}" ]]; then
                    log_imp 10 "args" "FATAL: --node requires a node name argument"
                    exit 1
                fi
                NODE="$2"
                shift 2
                ;;
            --skip-verify)
                SKIP_VERIFY=1
                shift
                ;;
            --dry-run)
                DRY_RUN=1
                shift
                ;;
            --launch)
                LAUNCH_MODE=1
                shift
                ;;
            *)
                log_imp 10 "args" "FATAL: Unknown argument: $1"
                echo "Usage: $0 --project <dir> --node <name> [--skip-verify] [--dry-run] [--launch]" >&2
                exit 1
                ;;
        esac
    done

    if [[ -z "$PROJECT_DIR" ]]; then
        log_imp 10 "args" "FATAL: --project is required"
        exit 1
    fi
    if [[ -z "$NODE" ]]; then
        log_imp 10 "args" "FATAL: --node is required"
        exit 1
    fi

    log_imp 8 "args" "Parsed: PROJECT_DIR=${PROJECT_DIR} NODE=${NODE} SKIP_VERIFY=${SKIP_VERIFY} DRY_RUN=${DRY_RUN}"
}
# endregion PARSE_ARGS

# region VALIDATE_PROJECT
## @purpose  Validate that PROJECT_DIR exists and contains ai-platform.yaml + compose file
## @io       ⇥ PROJECT_DIR → ⎋ exit 1 on missing files
## @complexity O(1) — file existence checks
validate_project() {
    if [[ ! -d "$PROJECT_DIR" ]]; then
        log_imp 10 "validate" "FATAL: Project directory not found: ${PROJECT_DIR}"
        exit 1
    fi

    if [[ ! -f "${PROJECT_DIR}/ai-platform.yaml" ]]; then
        log_imp 10 "validate" "FATAL: ai-platform.yaml not found in ${PROJECT_DIR}"
        exit 1
    fi

    if [[ ! -f "${PROJECT_DIR}/docker-compose.yml" && ! -f "${PROJECT_DIR}/compose.yaml" ]]; then
        log_imp 10 "validate" "FATAL: No docker-compose.yml or compose.yaml found in ${PROJECT_DIR}"
        exit 1
    fi

    log_imp 8 "validate" "Project validation passed: ${PROJECT_DIR}"
}
# endregion VALIDATE_PROJECT

# region EXTRACT_ORG
## @purpose  Extract org and project name from path ~/projects/<org>/<name>/
## @io       ⇥ PROJECT_DIR → sets ORG and PROJECT_NAME
## @complexity O(1) — string manipulation
## @invariants — ORG may be empty if path doesn't match ~/projects/<org>/<name>/
extract_org() {
    local real_path
    real_path="$(realpath "$PROJECT_DIR" 2>/dev/null || echo "$PROJECT_DIR")"

    # Find segment after "projects/"
    if [[ "$real_path" == *"/projects/"* ]]; then
        local after_projects="${real_path#*/projects/}"
        local first_segment="${after_projects%%/*}"
        local rest="${after_projects#*/}"

        if [[ "$first_segment" == "$after_projects" ]]; then
            # No org — just a project directly under projects/
            PROJECT_NAME="${first_segment}"
            ORG=""
            log_imp 8 "org" "No org extracted from path (project directly under projects/)"
        else
            ORG="${first_segment}"
            PROJECT_NAME="${rest%%/*}"
            log_imp 8 "org" "Extracted org=${ORG}, name=${PROJECT_NAME}"
        fi
    else
        # Path doesn't contain /projects/ — use basename as project name
        PROJECT_NAME="$(basename "$real_path")"
        ORG=""
        log_imp 8 "org" "No org extracted from path (not under projects/)"
    fi

    log_imp 8 "org" "Resolved: ORG=${ORG:-none}, PROJECT_NAME=${PROJECT_NAME}"
}
# endregion EXTRACT_ORG

# region RESOLVE_NODE_HOST
## @purpose  Resolve NODE name to SSH host via NODE_HOST_MAP env or node-resolver
## @io       ⇥ NODE, NODE_HOST_MAP env → sets SSH_HOST
## ⎋ exit 2 if resolution fails
## @complexity O(1) — single JSON lookup
resolve_node_host() {
    local resolved_host=""

    # Try NODE_HOST_MAP env (CI or dev machine)
    if [[ -n "${NODE_HOST_MAP:-}" ]]; then
        log_imp 8 "resolve" "Resolving NODE=${NODE} from NODE_HOST_MAP env..."
        resolved_host="$(resolve_node_from_env "$NODE" "$NODE_HOST_MAP")" || {
            log_imp 10 "resolve" "FATAL: Failed to resolve NODE=${NODE} from NODE_HOST_MAP"
            echo "K5: NODE '${NODE}' not found in NODE_HOST_MAP. Check NODE_HOST_MAP environment variable." >&2
            exit 2
        }
    else
        log_imp 10 "resolve" "FATAL: NODE_HOST_MAP not set — unable to resolve NODE=${NODE}"
        echo "K5: NODE_HOST_MAP environment variable is required. Set it to a JSON map of node→host." >&2
        exit 2
    fi

    SSH_HOST="$resolved_host"
    log_imp 8 "resolve" "NODE=${NODE} → SSH_HOST=${SSH_HOST}"
}
# endregion RESOLVE_NODE_HOST

# ═══════════════════════════════════════════════════════════════════
# FUNCTION — build_deliver_verb (Phase B: delegates to shared platform_deliver)
# ═══════════════════════════════════════════════════════════════════
# region FUNC_build_deliver_verb
## @purpose  Build the platform-deliver verb string via shared platform_deliver module.
##           DevPlan 081 Phase B (TASK-081B9): replaced local string construction with:
##             python3 -m core.internal.shared.platform_deliver build --org "$org" --project "$project"
##           DRIFT-D5 resolved: unified platform-deliver builder.
## @param $1 org (may be empty)
## @param $2 project name
## @stdout  platform-deliver verb string
build_deliver_verb() {
    local org="${1:-}"
    local project="${2:-}"
    # Phase B (TASK-081B9): shared platform_deliver module
    python3 -m core.internal.shared.platform_deliver build --org "$org" --project "$project"
}
# endregion FUNC_build_deliver_verb

# region DELIVER_PAYLOAD
## @purpose  Tar project files and deliver via SSH platform-deliver verb
## @io       ⇥ PROJECT_DIR, ORG, PROJECT_NAME → tar + ssh to ci-deploy@SSH_HOST
## ⎋ exit 2 on SSH failure
## @complexity O(n) where n = file count (typically 3-4 files)
deliver_payload() {
    cd "$PROJECT_DIR" || exit 2

    # Build file list — only existing files
    local files=()
    [[ -f "ai-platform.yaml" ]] && files+=("ai-platform.yaml")
    [[ -f "docker-compose.yml" ]] && files+=("docker-compose.yml")
    [[ -f "compose.yaml" ]] && files+=("compose.yaml")
    [[ -f ".env.platform" ]] && files+=(".env.platform")

    if [[ ${#files[@]} -eq 0 ]]; then
        log_imp 9 "deliver" "WARNING: No deliverable files found in ${PROJECT_DIR} — continuing anyway"
    fi

    if [[ -f "docker-compose.yml" || -f "compose.yaml" ]]; then
        log_imp 8 "deliver" "Compose file found — will deliver ${#files[@]} file(s)"
    else
        log_imp 9 "deliver" "WARNING: No docker-compose.yml or compose.yaml — deliver payload lacks compose config"
    fi

    # Build platform-deliver verb via shared helper
    # Phase B (TASK-081B9): migrate to python3 -m core.internal.shared.platform_deliver build
    local deliver_verb
    deliver_verb="$(build_deliver_verb "$ORG" "$PROJECT_NAME")"

    local tar_cmd="tar czf - ${files[*]}"
    local ssh_cmd="ssh ${SSH_OPTS} ci-deploy@${SSH_HOST} \"${deliver_verb}\""

    log_imp 7 "deliver" "Delivering ${PROJECT_NAME} to ${SSH_HOST} (org=${ORG:-none})..."

    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "[DRY-RUN] ${tar_cmd} | ${ssh_cmd}"
        log_imp 7 "deliver" "DRY-RUN: would deliver ${#files[@]} file(s) to ${SSH_HOST}"
        return 0
    fi

    # Execute delivery
    eval "${tar_cmd}" | eval "${ssh_cmd}" || {
        local exit_code=$?
        log_imp 10 "deliver" "FATAL: Delivery failed (exit=${exit_code}) for ${PROJECT_NAME} to ${SSH_HOST}"
        exit 2
    }

    log_imp 8 "deliver" "Delivery complete: ${PROJECT_NAME} → ${SSH_HOST}"
}
# endregion DELIVER_PAYLOAD

# region SSH_DEPLOY
## @purpose  Execute deploy.sh on VPS via SSH with PLATFORM_DEPLOY_DIRECT=1
## @io       ⇥ PROJECT_NAME, GIT_SHA, ENV → ssh to ci-deploy@SSH_HOST
## ⎋ exit 2 on SSH failure
## @complexity O(1) — single SSH command
ssh_deploy() {
    # Resolve git SHA
    if git -C "$PROJECT_DIR" rev-parse HEAD >/dev/null 2>&1; then
        GIT_SHA="$(git -C "$PROJECT_DIR" rev-parse HEAD)"
    else
        GIT_SHA="unknown"
        log_imp 8 "deploy" "Not a git repository or no commits — SHA=unknown"
    fi

    # Build project path with org prefix (consistent with deliver_payload path)
    local full_project_name="${ORG:+${ORG}/}${PROJECT_NAME}"

    local deploy_cmd="PLATFORM_DEPLOY_DIRECT=1 /opt/platform/core/entrypoints/deploy.sh ${full_project_name} ${GIT_SHA} ${ENV}"
    local ssh_cmd="ssh ${SSH_OPTS} ci-deploy@${SSH_HOST} \"${deploy_cmd}\""

    log_imp 7 "deploy" "Deploying ${PROJECT_NAME}@${GIT_SHA} on ${SSH_HOST}..."

    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "[DRY-RUN] ${ssh_cmd}"
        log_imp 7 "deploy" "DRY-RUN: would deploy ${PROJECT_NAME}@${GIT_SHA}"
        return 0
    fi

    eval "${ssh_cmd}" || {
        local exit_code=$?
        log_imp 10 "deploy" "FATAL: Deploy failed (exit=${exit_code}) for ${PROJECT_NAME}@${GIT_SHA}"
        exit 2
    }

    log_imp 8 "deploy" "Deploy command sent: ${PROJECT_NAME}@${GIT_SHA}"
}
# endregion SSH_DEPLOY

# region VERIFY_DEPLOY
## @purpose  Post-deploy verification via ssh deploy.sh verify <node>
## @io       ⇥ NODE → ssh to ci-deploy@SSH_HOST
## ⎋ exit 3 on verification failure (unless --skip-verify)
## @complexity O(1) — single SSH command
verify_deploy() {
    if [[ "$SKIP_VERIFY" -eq 1 ]]; then
        log_imp 8 "verify" "Verification skipped (--skip-verify)"
        return 0
    fi

    local verify_cmd="/opt/platform/core/entrypoints/deploy.sh verify ${NODE}"
    local ssh_cmd="ssh ${SSH_OPTS} ci-deploy@${SSH_HOST} \"${verify_cmd}\""

    log_imp 8 "verify" "Verifying deploy on ${SSH_HOST}..."

    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "[DRY-RUN] ${ssh_cmd}"
        log_imp 8 "verify" "DRY-RUN: would verify deploy on ${SSH_HOST}"
        return 0
    fi

    eval "${ssh_cmd}" || {
        local exit_code=$?
        log_imp 10 "verify" "FATAL: Verification failed (exit=${exit_code}) for ${PROJECT_NAME} on ${SSH_HOST}"
        echo "Deploy verification FAILED. Check VPS logs: /var/log/platform/audit.log" >&2
        exit 3
    }

    log_imp 9 "verify" "Deploy verification passed for ${PROJECT_NAME} on ${SSH_HOST}"
}
# endregion VERIFY_DEPLOY

# region MAIN
main() {
    log_imp 9 "main" "=== DIRECT DEPLOY START ==="

    parse_args "$@"
    validate_project
    extract_org
    resolve_node_host

    # ── W1: Pre-flight VPS readiness check ──
    if [[ "${SKIP_VERIFY}" -ne 1 ]]; then
        # Source vps-readiness.sh if available
        local vps_script="${SCRIPT_DIR}/../lib/vps-readiness.sh"
        if [[ -f "${vps_script}" ]]; then
            # shellcheck source=../lib/vps-readiness.sh
            source "${vps_script}"
            echo "[IMP:8][deploy-project][preflight] Checking VPS readiness for NODE=${NODE} (SSH_HOST=${SSH_HOST})" >&2
            # Set NODE_HOST_MAP env for check_vps_ready
            export NODE_HOST_MAP="${NODE_HOST_MAP:-{\"${NODE}\":\"${SSH_HOST}\"}}"
            if ! check_vps_ready "${NODE}" --quick; then
                log_imp 10 "preflight" "FATAL: VPS readiness check failed for ${NODE}@${SSH_HOST}"
                echo "Run: make bootstrap-node NODE=${NODE} first" >&2
                exit 2
            fi
            echo "[IMP:9][deploy-project][preflight] VPS readiness check passed" >&2
        else
            echo "[IMP:7][deploy-project][preflight] vps-readiness.sh not found at ${vps_script} — skipping pre-flight" >&2
        fi
    fi

    deliver_payload
    ssh_deploy
    verify_deploy

    # ── W6: --launch mode — post-deploy verification ──
    if [[ "${LAUNCH_MODE}" -eq 1 ]]; then
        echo "[IMP:9][deploy-project][launch] LAUNCH mode: verifying deployment..." >&2
        local verify_url=""
        if [[ -n "${ORG:-}" ]]; then
            verify_url="https://${PROJECT_NAME}.${ORG}.example.com"
        else
            verify_url="https://${SSH_HOST}/${PROJECT_NAME}"
        fi
        # Try to get URL from ai-platform.yaml
        local ai_yaml="${PROJECT_DIR}/ai-platform.yaml"
        if [[ -f "${ai_yaml}" ]]; then
            local domain
            domain="$(grep -E '^domain:' "${ai_yaml}" 2>/dev/null | awk '{print $2}' | head -1 || true)"
            if [[ -n "${domain}" ]]; then
                verify_url="https://${domain}"
            fi
        fi
        echo "[IMP:9][deploy-project][launch] PROJECT=${PROJECT_NAME} deployed"
        echo "[IMP:9][deploy-project][launch] URL: ${verify_url}"
        echo "[IMP:9][deploy-project][launch] NODE: ${SSH_HOST}"
    fi

    log_imp 9 "main" "=== DIRECT DEPLOY COMPLETE ==="
    echo "[IMP:9][deploy-project][main] Direct deploy complete: ${PROJECT_NAME} → ${SSH_HOST} (org=${ORG:-none})" >&2
}
# endregion MAIN

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
