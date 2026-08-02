#!/usr/bin/env bash
# shellcheck disable=SC2034
# GREP_SUMMARY: adopt-project, shell-facade, strangler-fig, parse-args, dispatch
# STRUCTURE: ▶ source libs → parse_args (auto-detection) → validate_org (fast grep) → dispatch python3 -m project_adopter → exit
# region MODULE_CONTRACT
## @purpose  Shell facade (≤150 LOC) for adopt-project. parse_args and org validation stay in shell per D1/D6.
##           All business logic delegates to project_adopter.py.
## @changes  2026-07-26 · Wave 5c — Reduced from 906 LOC to ~120 LOC facade
# endregion MODULE_CONTRACT

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_ROOT="${PLATFORM_ROOT:-$(cd "${SCRIPT_DIR}/../../.." 2>/dev/null && pwd || dirname "$(dirname "$(dirname "$SCRIPT_DIR")")")}"
PROJECTS_ROOT="${PROJECTS_ROOT:-$(dirname "$PLATFORM_ROOT")}"
__LOG_PREFIX="adopt-project"
source "${PLATFORM_ROOT}/core/lib/logging.sh"
source "${PLATFORM_ROOT}/core/lib/args.sh"

PROJECT_DIR=""; PROJECT_NAME=""; PROJECT_ORG=""; PROJECT_NODE=""; PROJECT_DOMAIN=""; FORCE=0
USAGE_SCRIPT="adopt-project.sh"; USAGE_DESC="Adopt an existing project into the ai-platform lifecycle."
USAGE_OPTIONS=("--dir <dir>        Path to existing project directory" "--name <name>      Project name (auto-detected from directory basename)" "--org <org>        Organization name (from ai-platform.yaml or auto-detected)" "--node <node>      Target node name (from ai-platform.yaml or default)" "--domain <domain>  Custom domain" "--force            Regenerate Makefile/AGENTS.md even if they exist")

# region FUNC_parse_args
## @purpose  Parse CLI + auto-detect name/node/domain/org from dir/yaml (D1 — shell-bound).
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dir) shift; PROJECT_DIR="$1" ;;
            --name) shift; PROJECT_NAME="$1" ;;
            --org) shift; PROJECT_ORG="$1" ;;
            --node) shift; PROJECT_NODE="$1" ;;
            --domain) shift; PROJECT_DOMAIN="$1" ;;
            --force) FORCE=1 ;;
            --help|-h) usage "$USAGE_SCRIPT" "${USAGE_DESC:-}" "${USAGE_OPTIONS[@]:-}"; exit 0 ;;
            *) log_imp 9 "-" "Unknown arg: $1"; usage "$USAGE_SCRIPT" "${USAGE_DESC:-}" "${USAGE_OPTIONS[@]:-}" >&2; exit 1 ;;
        esac; shift
    done
    [[ -z "$PROJECT_DIR" ]] && { log_imp 10 "-" "FAIL-FAST: --dir is required"; usage >&2; exit 1; }
    [[ ! -d "$PROJECT_DIR" ]] && { log_imp 10 "-" "FAIL-FAST: project directory not found: ${PROJECT_DIR}"; exit 1; }
    [[ -z "$PROJECT_NAME" ]] && PROJECT_NAME="$(basename "$PROJECT_DIR")" && log_imp 7 "-" "Auto-detected project name: ${PROJECT_NAME}"

    local yaml_file="${PROJECT_DIR}/ai-platform.yaml"
    if [[ -f "$yaml_file" ]]; then
        [[ -z "$PROJECT_NODE" ]] && PROJECT_NODE="$(grep -E '^\s*target_node:\s*' "$yaml_file" 2>/dev/null | head -1 | awk '{print $2}' || true)" && [[ -n "$PROJECT_NODE" ]] && log_imp 6 "-" "Node from ai-platform.yaml: ${PROJECT_NODE}"
        if [[ -z "$PROJECT_DOMAIN" ]]; then
            local dm; dm="$(grep -E '^\s*domain:\s*' "$yaml_file" 2>/dev/null | head -1 | awk '{sub(/^[[:space:]]*domain:[[:space:]]*/, ""); gsub(/["'"'"']/, ""); print $1}' || true)"
            [[ -n "$dm" && "$dm" != "false" ]] && PROJECT_DOMAIN="$dm" && log_imp 6 "-" "Domain from ai-platform.yaml: ${PROJECT_DOMAIN}"
        fi
    fi
    if [[ -z "${PROJECT_ORG:-}" ]]; then
        local _dir_abs; _dir_abs="$(cd "$PROJECT_DIR" && pwd -P 2>/dev/null || echo "$PROJECT_DIR")"
        local _org; _org="$(basename "$(dirname "$_dir_abs")")"
        [[ -n "$_org" ]] && PROJECT_ORG="$_org" && log_imp 7 "-" "Derived org from path: ${PROJECT_ORG}"
    fi
    [[ -z "${PROJECT_ORG:-}" ]] && PROJECT_ORG="${PLATFORM_ORG:-}"
    PROJECT_NODE="${PROJECT_NODE:-${PLATFORM_DEFAULT_NODE:-tronyx-vps}}"

    # ⚠️ TRAP[BUG] · 2026-07-17 · B1 — молчаливый дефолт "personal" → конфиг-drift (FIXED)
    [[ -z "${PROJECT_ORG:-}" ]] && { log_imp 10 "-" "FAIL-FAST: PROJECT_ORG is not set. Use --org <github-org> or set PLATFORM_ORG env."; usage >&2; exit 1; }
    log_imp 7 "-" "Args: dir=${PROJECT_DIR} name=${PROJECT_NAME} org=${PROJECT_ORG} node=${PROJECT_NODE} domain=${PROJECT_DOMAIN:-<none>} force=${FORCE}"
}
# endregion

# region FUNC_validate_org_against_node_yaml
## @purpose  Fast grep-based org validation vs node.yaml context (D6 — duplicated in Python).
validate_org_against_node_yaml() {
    local node_yaml="${PROJECTS_ROOT}/${PROJECT_ORG}/node-configs/${PROJECT_NODE}/node.yaml"
    [[ ! -f "$node_yaml" ]] && { log_imp 6 "-" "node.yaml not found — skipping context validation"; return 0; }
    local ctx; ctx="$(grep -E '^\s*context:\s*' "$node_yaml" 2>/dev/null | head -1 | awk '{print $2}' || true)"
    [[ -z "$ctx" ]] && { log_imp 6 "-" "node.yaml has no context field — skipping validation"; return 0; }
    [[ "${PROJECT_ORG,,}" != "${ctx,,}" ]] && { log_imp 10 "-" "FAIL-FAST: PROJECT_ORG='${PROJECT_ORG}' != node.yaml context='${ctx}'"; exit 1; }
    [[ "$PROJECT_ORG" != "$ctx" ]] && log_imp 8 "-" "Casing mismatch: org='${PROJECT_ORG}' vs context='${ctx}' — using node.yaml variant" && PROJECT_ORG="$ctx"
    log_imp 7 "-" "node.yaml context validated: ${PROJECT_ORG}"
}
# endregion

# region FUNC_main
main() {
    log_imp 6 "-" "Starting adopt-project.sh (Wave 5c Strangler-Fig facade)"
    parse_args "$@"; validate_org_against_node_yaml
    python3 -m core.internal.scaffold.project_adopter adopt \
        --project-dir "$PROJECT_DIR" --project-name "$PROJECT_NAME" \
        --project-org "$PROJECT_ORG" --project-node "$PROJECT_NODE" \
        ${PROJECT_DOMAIN:+--project-domain "$PROJECT_DOMAIN"} ${FORCE:+--force} \
    && exit 0 || exit $?
}
# endregion
main "$@"
