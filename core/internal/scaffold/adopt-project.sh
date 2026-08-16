#!/usr/bin/env bash
# shellcheck disable=SC2034
# GREP_SUMMARY: adopt-project, shell-facade, strangler-fig, parse-args, dispatch, python-auto-detect
# STRUCTURE: ▶ source libs → parse_args (CLI only) → exec python3 -m project_adopter adopt (auto-detect+casing в Python) → exit
# region MODULE_CONTRACT
## @purpose  Shell facade (≤60 LOC) for adopt-project. CLI parsing stays in shell;
##           auto-detect (target_node/domain из ai-platform.yaml, org из пути) + casing-валидация
##           в Python (project_adopter.detect_project_config, DevPlan 118 E11) — 0 grep-YAML в shell.
## @changes  2026-07-26 · Wave 5c — Reduced from 906 LOC to ~120 LOC facade
##           2026-08-02 · DevPlan 118 E11 — YAML-парсинг в Python (PyYAML); facade ≤60 LOC
# endregion MODULE_CONTRACT

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_ROOT="${PLATFORM_ROOT:-$(cd "${SCRIPT_DIR}/../../.." 2>/dev/null && pwd || dirname "$(dirname "$(dirname "$SCRIPT_DIR")")")}"
PROJECTS_ROOT="${PROJECTS_ROOT:-$(dirname "$PLATFORM_ROOT")}"
__LOG_PREFIX="adopt-project"
source "${PLATFORM_ROOT}/core/lib/logging.sh"

PROJECT_DIR=""; PROJECT_NAME=""; PROJECT_ORG=""; PROJECT_NODE=""; PROJECT_DOMAIN=""; FORCE=0
USAGE_SCRIPT="adopt-project.sh"; USAGE_DESC="Adopt an existing project into the ai-platform lifecycle."
USAGE_OPTIONS=("--dir <dir>        Path to existing project directory" "--name <name>      Project name (auto-detected from directory basename)" "--org <org>        Organization name (from ai-platform.yaml or auto-detected)" "--node <node>      Target node name (from ai-platform.yaml or default)" "--domain <domain>  Custom domain" "--force            Regenerate Makefile/AGENTS.md even if they exist")

# region FUNC_parse_args
## @purpose  Parse CLI args (D1 — shell-bound). Auto-detection делегируется Python (E11).
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
    [[ -z "$PROJECT_NAME" ]] && PROJECT_NAME="$(basename "$PROJECT_DIR")" && log_imp 7 "-" "Auto-detected project name: ${PROJECT_NAME}" || true
}
# endregion

# region FUNC_main
main() {
    log_imp 6 "-" "Starting adopt-project.sh (Wave 118 E11 Strangler-Fig facade)"
    parse_args "$@"
    # E11: auto-detect + casing-валидация в Python (0 grep-YAML в shell); --project-org/node/domain опциональны
    python3 -m core.internal.scaffold.project_adopter adopt \
        --project-dir "$PROJECT_DIR" --project-name "$PROJECT_NAME" \
        ${PROJECT_ORG:+--project-org "$PROJECT_ORG"} \
        ${PROJECT_NODE:+--project-node "$PROJECT_NODE"} \
        ${PROJECT_DOMAIN:+--project-domain "$PROJECT_DOMAIN"} \
        ${FORCE:+--force} \
    && exit 0 || exit $?
}
# endregion
main "$@"
