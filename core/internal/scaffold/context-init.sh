#!/usr/bin/env bash
# GREP_SUMMARY: context-init scaffold context node-configs hermes-agent gh-repo node-yaml registration idempotent declarative
# STRUCTURE: ▶ validate_name → ⚡ check_idempotent ─┬─ create_dirs ─┬─ create_skeleton_node_yaml ── gh_repo_create ── register_in_platform_yaml
# region MODULE_CONTRACT
## @purpose  Scaffold a new deployment context: create directory structure, skeleton
##           node.yaml, GitHub repos, and register the context in the platform's
##           node.yaml under the contexts[] array.
## @scope    Developer machine only (local scaffold) — no SSH, no VPS operations.
## @location core/internal/scaffold/context-init.sh — moved from core/scripts/context-init.sh
## @invariants
##   - Idempotent: if ~/projects/<name>/ exists → SKIP (exit 0 with message)
##   - Skeleton node.yaml is created with placeholder host, must be edited by user
##   - GitHub repo creation is optional (--skip-gh-repo flag)
##   - Registration appends to contexts[] array in platform node.yaml
##   - All steps are independent — script continues on non-fatal GitHub failures
##   - Exit codes: 0=success/skip, 1=validation error, 2=registration error
## @rationale First step of the Scaffold → Declare → Apply workflow.
# endregion MODULE_CONTRACT

set -euo pipefail

echo "[IMP:7][context-init][main] Starting context scaffold" >&2
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PLATFORM_ROOT="${PLATFORM_ROOT:-$(cd "${SCRIPT_DIR}/../../.." 2>/dev/null && pwd || true)}"
readonly PROJECTS_DIR="${HOME}/projects"
readonly DEFAULT_NODE="${NODE:-tronyx-vps}"
readonly ORG_DEFAULT="${NODE_ORG:-tronyx-lab}"

CONTEXT_NAME=""
CONTEXT_DESC=""
GITHUB_ORG=""
SKIP_GH_REPO=false
PLATFORM_NODE_YAML=""
CONTEXT_DIR=""
CREATED_HERMES_AGENT_REPO=""
CREATED_NODE_CONFIGS_REPO=""
WARNINGS=0

__LOG_PREFIX="context-init"
source "${PLATFORM_ROOT}/core/lib/logging.sh"
source "${PLATFORM_ROOT}/core/lib/node-resolver.sh"

# region FUNC_usage
_usage() {
    cat <<'USAGE'
USAGE: context-init.sh <name> [options]

Scaffold a new deployment context: create directories, skeleton node.yaml,
GitHub repos, and register in platform node.yaml.

REQUIRED:
  <name>                Context name (also used as directory name in ~/projects/)

OPTIONS:
  --description <desc>  Human-readable description
  --org <org>           GitHub org/username for repo creation
  --node-yaml <path>    Explicit path to platform node.yaml
  --skip-gh-repo        Skip GitHub repository creation
  -h, --help            Show this help message

EXIT CODES:
  0  Success (or SKIP — context already exists)
  1  Validation error
  2  Registration error

EXAMPLES:
  context-init.sh asi-group --description "ASI group projects"
USAGE
    exit 0
}
# endregion FUNC_usage

# region FUNC_parse_args
_parse_args() {
    for arg in "$@"; do
        if [[ "$arg" == "-h" || "$arg" == "--help" ]]; then
            _usage
        fi
    done

    if [[ $# -eq 0 ]]; then
        log_imp 10 "parse_args" "FATAL: No context name provided"
        _usage
    fi

    CONTEXT_NAME="$1"
    shift

    if [[ ! "$CONTEXT_NAME" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]]; then
        log_imp 10 "parse_args" "FATAL: Invalid context name '${CONTEXT_NAME}'"
        exit 1
    fi
    log_imp 8 "parse_args" "Context name: ${CONTEXT_NAME}"

    CONTEXT_DESC=""
    GITHUB_ORG="${ORG_DEFAULT}"
    SKIP_GH_REPO=false
    PLATFORM_NODE_YAML=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --description) CONTEXT_DESC="$2"; shift 2 ;;
            --org) GITHUB_ORG="$2"; shift 2 ;;
            --node-yaml) PLATFORM_NODE_YAML="$2"; shift 2 ;;
            --skip-gh-repo) SKIP_GH_REPO=true; shift ;;
            -h|--help) _usage ;;
            *) echo "ERROR: Unknown argument: $1" >&2; _usage ;;
        esac
    done

    log_imp 7 "parse_args" "Config: name=${CONTEXT_NAME} org=${GITHUB_ORG} skip_gh=${SKIP_GH_REPO}"
}
# endregion FUNC_parse_args

# region FUNC_check_idempotent
_check_idempotent() {
    CONTEXT_DIR="${PROJECTS_DIR}/${CONTEXT_NAME}"

    if [[ -d "$CONTEXT_DIR" ]]; then
        log_imp 9 "idempotent" "SKIP: Context '${CONTEXT_NAME}' already exists at ${CONTEXT_DIR}"
        exit 0
    fi

    log_imp 7 "idempotent" "Context '${CONTEXT_NAME}' does not exist — proceeding with scaffold"
}
# endregion FUNC_check_idempotent

# region FUNC_create_dirs
_create_dirs() {
    log_imp 7 "create_dirs" "Creating context directory structure under ${CONTEXT_DIR}"

    mkdir -p "${CONTEXT_DIR}/hermes-agent"
    log_imp 8 "create_dirs" "Created: ${CONTEXT_DIR}/hermes-agent/"

    mkdir -p "${CONTEXT_DIR}/node-configs"
    log_imp 8 "create_dirs" "Created: ${CONTEXT_DIR}/node-configs/"

    log_imp 9 "create_dirs" "Context directory structure created: ${CONTEXT_DIR}"
    echo "  ✅ Created: ${CONTEXT_DIR}/"
    echo "  ✅ Created: ${CONTEXT_DIR}/hermes-agent/"
    echo "  ✅ Created: ${CONTEXT_DIR}/node-configs/"
}
# endregion FUNC_create_dirs

# 🧐 TRAP[DECISION] · 2026-07-21 · — · secrets-init called at bootstrap, not context-init
# · Rejected: calling secrets-init.sh from context-init.sh
# · Reason: PLATFORM_MASTER_PASSWORD not available at scaffold time — operator sets it in SOPS secrets before bootstrap
# · Rev: if context-init gains access to PLATFORM_MASTER_PASSWORD at scaffold time → call secrets-init.sh here

# region FUNC_create_skeleton_node_yaml
_create_skeleton_node_yaml() {
    local skeleton_path="${CONTEXT_DIR}/node-configs/node.yaml"

    log_imp 8 "skeleton" "Creating skeleton node.yaml"

    cat > "$skeleton_path" <<SKELETON
# GREP_SUMMARY: ${CONTEXT_NAME} node context declarative apply declarative-deploy
# STRUCTURE: ▶ resolve → ┌node+modules┐ → ◇ validate ← ⊕ projects+secrets+firewall → ⚡ apply

# Skeleton node.yaml for context '${CONTEXT_NAME}'.
# MUST EDIT: Replace placeholder values below with actual configuration.

# Deployment context this node belongs to
context: ${CONTEXT_NAME}

# --- Node definition (MUST EDIT) ---
node:
  name: ${CONTEXT_NAME}
  host: "127.0.0.1"
  owner_key: ""
  timezone: "Europe/Moscow"

# --- Modules to deploy (MUST EDIT) ---
modules:
  - name: nginx
    enabled: true
  - name: postgres
    enabled: true
  - name: platform-secrets
    enabled: true

# --- Projects ---
projects: []
SKELETON

    log_imp 9 "skeleton" "Skeleton node.yaml created: ${skeleton_path}"
    echo "  ✅ Created: ${skeleton_path}"
    echo "  ⚠️  Edit this file: set node.host, node.owner_key, and modules"
}
# endregion FUNC_create_skeleton_node_yaml

# region FUNC_gh_repo_create
_gh_repo_create() {
    local org="$1" ctx="$2"

    if [[ "$SKIP_GH_REPO" == true ]]; then
        log_imp 7 "gh_repo" "SKIP: GitHub repo creation disabled"
        echo "  ⏭ SKIP: GitHub repo creation (--skip-gh-repo)"
        return 0
    fi

    if ! command -v gh &>/dev/null; then
        log_imp 9 "gh_repo" "WARNING: gh CLI not found — skipping GitHub repo creation"
        WARNINGS=$((WARNINGS + 1))
        return 0
    fi

    if ! gh auth status &>/dev/null; then
        log_imp 9 "gh_repo" "WARNING: gh CLI not authenticated — skipping"
        WARNINGS=$((WARNINGS + 1))
        return 0
    fi

    local node_repo="${org}/${ctx}-node-configs"
    log_imp 8 "gh_repo" "Creating repo: ${node_repo}"

    local node_output node_rc=0
    node_output="$(gh repo create "${node_repo}" --private --description "Node configurations for context '${ctx}'" 2>&1)" || node_rc=$?

    if [[ "$node_rc" -eq 0 ]]; then
        CREATED_NODE_CONFIGS_REPO="${node_repo}"
        log_imp 9 "gh_repo" "Created GitHub repo: ${node_repo}"
        echo "  ✅ Created GitHub repo: ${node_repo} (private)"

        (
            cd "${CONTEXT_DIR}/node-configs"
            if ! git init --initial-branch=main 2>/dev/null; then
                git init 2>/dev/null && git checkout -b main 2>/dev/null
            fi
            git add -A
            git commit -m "chore: initial scaffold for context '${ctx}'" 2>/dev/null || true
            git remote add origin "git@github.com:${node_repo}.git" 2>/dev/null || true
            git push -u origin main 2>&1
        ) || log_imp 9 "gh_repo" "WARNING: Initial push to ${node_repo} failed"
    else
        if echo "$node_output" | grep -qi "already exists"; then
            log_imp 9 "gh_repo" "Repo already exists: ${node_repo}"
            CREATED_NODE_CONFIGS_REPO="${node_repo}"
        else
            log_imp 9 "gh_repo" "WARNING: Failed to create ${node_repo}: ${node_output}"
            WARNINGS=$((WARNINGS + 1))
        fi
    fi

    local agent_repo="${org}/${ctx}-hermes-agent"
    log_imp 8 "gh_repo" "Creating repo: ${agent_repo}"

    local agent_output agent_rc=0
    agent_output="$(gh repo create "${agent_repo}" --private --description "Hermes-agent overlay for context '${ctx}'" 2>&1)" || agent_rc=$?

    if [[ "$agent_rc" -eq 0 ]]; then
        CREATED_HERMES_AGENT_REPO="${agent_repo}"
        log_imp 9 "gh_repo" "Created GitHub repo: ${agent_repo}"
        echo "  ✅ Created GitHub repo: ${agent_repo} (private)"
    else
        if echo "$agent_output" | grep -qi "already exists"; then
            log_imp 9 "gh_repo" "Repo already exists: ${agent_repo}"
            CREATED_HERMES_AGENT_REPO="${agent_repo}"
        else
            log_imp 9 "gh_repo" "WARNING: Failed to create ${agent_repo}: ${agent_output}"
            WARNINGS=$((WARNINGS + 1))
        fi
    fi
}
# endregion FUNC_gh_repo_create

# region FUNC_register_in_platform_yaml
_register_in_platform_yaml() {
    local ctx_name="$1" ctx_desc="$2"
    local node_cfg_repo="${CREATED_NODE_CONFIGS_REPO:-}"
    local hermes_agent_repo="${CREATED_HERMES_AGENT_REPO:-}"

    log_imp 8 "register" "Adding context entry: name=${ctx_name}"

    local py_output py_rc=0
    py_output="$(python3 "$SCRIPT_DIR/context_registry.py" register \
        --yaml-path "${PLATFORM_NODE_YAML}" \
        --name "${ctx_name}" \
        --desc "${ctx_desc}" \
        --node-cfg-repo "${node_cfg_repo}" \
        --hermes-agent-repo "${hermes_agent_repo}" 2>&1)" || py_rc=$?

    if [[ "$py_rc" -ne 0 ]]; then
        log_imp 10 "register" "FATAL: YAML registration failed (exit=${py_rc})"
        exit 2
    fi

    if [[ "$py_output" == "EXISTS" ]]; then
        log_imp 9 "register" "SKIP: Context '${ctx_name}' already registered"
        return 0
    fi

    log_imp 9 "register" "Context '${ctx_name}' registered in ${PLATFORM_NODE_YAML}"
    echo "  ✅ Registered context '${ctx_name}' in: ${PLATFORM_NODE_YAML}"
}
# endregion FUNC_register_in_platform_yaml

# region FUNC_report_summary
_report_summary() {
    echo ""
    echo "┌─ Context Init Summary ─────────────────────────────────┐"
    echo "│ Context:     ${CONTEXT_NAME}"
    echo "│ Directory:   ${CONTEXT_DIR}"
    echo "│ Warnings:    ${WARNINGS}"
    echo "│"
    echo "│ Created:"
    echo "│   ✅ ${CONTEXT_DIR}/"
    echo "│   ✅ ${CONTEXT_DIR}/hermes-agent/"
    echo "│   ✅ ${CONTEXT_DIR}/node-configs/"
    echo "│   ✅ ${CONTEXT_DIR}/node-configs/node.yaml (skeleton)"
    if [[ -n "$CREATED_NODE_CONFIGS_REPO" ]]; then
        echo "│   ✅ GitHub: ${CREATED_NODE_CONFIGS_REPO}"
    fi
    if [[ -n "$CREATED_HERMES_AGENT_REPO" ]]; then
        echo "│   ✅ GitHub: ${CREATED_HERMES_AGENT_REPO}"
    fi
    echo "│   ✅ Registered in: ${PLATFORM_NODE_YAML}"
    echo "└────────────────────────────────────────────────────────┘"
    echo ""

    log_imp 9 "summary" "Context '${CONTEXT_NAME}' initialized | Warnings: ${WARNINGS}"
}
# endregion FUNC_report_summary

# region FUNC_main
main() {
    local start_time
    start_time="$(date +%s)"

    log_imp 9 "main" "══════════════════════════════════════════"
    log_imp 9 "main" "  context-init — Declarative Context Scaffold"
    log_imp 9 "main" "══════════════════════════════════════════"

    _parse_args "$@"
    _check_idempotent
    _create_dirs
    _create_skeleton_node_yaml
    _gh_repo_create "${GITHUB_ORG}" "${CONTEXT_NAME}"

    if [[ -n "${PLATFORM_NODE_YAML:-}" && -f "$PLATFORM_NODE_YAML" ]]; then
        log_imp 8 "resolve_yaml" "Using explicit node.yaml: ${PLATFORM_NODE_YAML}"
    else
        local resolved_path
        resolved_path="$(resolve_node_yaml "${DEFAULT_NODE}" "${PLATFORM_ROOT}" "${PROJECTS_DIR}")" || {
            log_imp 10 "main" "FATAL: Could not resolve node.yaml for NODE=${DEFAULT_NODE}"
            exit 1
        }
        PLATFORM_NODE_YAML="${resolved_path}"
        log_imp 7 "resolve_yaml" "Platform node.yaml resolved: ${PLATFORM_NODE_YAML}"
    fi

    _register_in_platform_yaml "${CONTEXT_NAME}" "${CONTEXT_DESC}"
    _report_summary

    local end_time duration
    end_time="$(date +%s)"
    duration=$(( end_time - start_time ))
    log_imp 9 "main" "context-init COMPLETE — ${duration}s"
}
# endregion FUNC_main

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
