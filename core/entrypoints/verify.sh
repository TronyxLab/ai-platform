#!/usr/bin/env bash
# GREP_SUMMARY: verify, post-deploy, entrypoint, thin-wrapper, node-verify
# STRUCTURE: ▶ ┌NODE┐ → ◇ --help? → ○ validate args → ▶ delegate to internal/verify/verify-domains.sh → ⎋ exit
# region MODULE_CONTRACT
## @purpose  Thin wrapper for post-deploy HTTPS verification — delegates to core/internal/verify/verify-domains.sh
## @scope    Called from Makefile (`make verify NODE=<node>`). Validates args, sets up PLATFORM_ROOT, delegates.
## @invariants
##   - Requires exactly one positional argument: node_name (or NODE env var)
##   - Does NOT contain business logic (YAML parsing, curl) — all delegated to internal/
##   - LOC ≤ 150 (thin-wrapper contract)
## @rationale Extracted business logic to internal/verify/verify-domains.sh to comply with thin-wrapper contract
## @changes  REFACTORED: 2026-07-18 · I-4 fix · Moved business logic to core/internal/verify/verify-domains.sh
# endregion MODULE_CONTRACT
set -euo pipefail

_EP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_ORIG_PLATFORM_ROOT="${PLATFORM_ROOT:-}"
source "${_EP_DIR}/../lib/paths.sh"
# Restore PLATFORM_ROOT if set by caller (paths.sh hardcodes /opt/platform)
if [[ -n "${_ORIG_PLATFORM_ROOT}" ]]; then
    PLATFORM_ROOT="${_ORIG_PLATFORM_ROOT}"
fi

# ═══════════════════════════════════════════════════════════════════
# FUNC: show_usage
# ═══════════════════════════════════════════════════════════════════
# region FUNC_show_usage
## @purpose  Print usage information
show_usage() {
    cat <<EOF
Usage: $0 <node_name> [project_name]

Post-deploy verification — checks HTTP 200 for all expose:true domains
in the node's node.yaml configuration. Optional project_name restricts
scope to a single project (verify per-project, DevPlan 125 T1 — P-22).

Arguments:
  node_name     Node name (e.g. tronyx-vps). Required.
  project_name  Project name (e.g. tronyx-site). Optional — без него
                проверяются ВСЕ expose:true домены ноды.

Environment:
  CURL_TIMEOUT    curl --max-time in seconds (default: 10)
  PLATFORM_ROOT   platform root for node-configs search (default: /opt/platform)

Example:
  $0 tronyx-vps
  make verify NODE=tronyx-vps
  make verify NODE=tronyx-vps PROJECT=tronyx-site

Exit codes:
  0   All domains respond HTTP 200
  1   One or more domains failed (non-200 or unreachable)
EOF
}
# endregion FUNC_show_usage

# ═══════════════════════════════════════════════════════════════════
# MAIN — validate args, delegate
# ═══════════════════════════════════════════════════════════════════
# region FUNC_main
## @purpose  Entry point: parse args, validate, delegate to internal/verify/verify-domains.sh
## @param $1  Node name (positional, required)
## @param $2  Project name (positional, optional — verify per-project, DevPlan 125 T1)
## @exitcode proxied from internal script (0 all pass, 1 any fail)
main() {
    local node_name="${1:-${NODE:-}}"
    local project_name="${2:-${PROJECT:-}}"

    if [[ "$*" == *--help* ]] || [[ "$*" == *-h* ]]; then
        show_usage
        exit 0
    fi

    if [[ -z "${node_name}" ]]; then
        echo "[IMP:10][verify][main] Missing required argument: node_name" >&2
        show_usage
        exit 1
    fi

    local platform_root="${PLATFORM_ROOT:-/opt/platform}"
    local internal_script="${_EP_DIR}/../internal/verify/verify-domains.sh"

    if [[ ! -f "${internal_script}" ]]; then
        echo "[IMP:10][verify][main] Internal script not found: ${internal_script}" >&2
        exit 1
    fi

    # Delegate all business logic to internal script (project — optional 3-й аргумент)
    "${internal_script}" "${node_name}" "${platform_root}" "${project_name}"
    exit $?
}
# endregion FUNC_main

main "$@"
