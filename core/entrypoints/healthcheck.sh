#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint healthcheck delegation thin-wrapper
# STRUCTURE: ▶ init → ◇ --help? → ⊕ delegate to internal/healthcheck/modules-healthcheck.sh → ⎋ pass-through exit
# region MODULE_CONTRACT
## @purpose  Thin delegator entrypoint for `make healthcheck`
## @scope    Called ONLY from Makefile. Delegates to core/internal/healthcheck/modules-healthcheck.sh
## @invariants
##   - Does NOT iterate modules/ directly (cross-layer rule compliance per core/AGENTS.md)
##   - Passes through all arguments and exit code to modules-healthcheck.sh
## @rationale Q: Why a thin wrapper?
##            A: Compliance with core/AGENTS.md cross-layer rule: entrypoints → modules is forbidden.
##            internal/ → modules is permitted through typed contract (invoke_module_interface + module.yaml.interfaces).
##            The --help and PLATFORM_ROOT computation stay here
##            to maintain CLI contract for make healthcheck.
# endregion MODULE_CONTRACT
set -euo pipefail
_EP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_EP_DIR}/../lib/paths.sh"

if [[ "${1:-}" == "--help" ]]; then
    echo "Usage: $0 [NODE=<name>]"
    echo ""
    echo "Run health checks on all platform modules."
    echo "Without NODE: check local docker compose services."
    echo "With NODE: check remote node via SSH."
    echo ""
    echo "Implementation: delegates to core/internal/healthcheck/modules-healthcheck.sh"
    exit 0
fi

echo "[IMP:9][entrypoint][delegate] Running all module healthchecks (via modules-healthcheck)..."
exec bash "${PATHS_INTERNAL_DIR}/healthcheck/modules-healthcheck.sh" "$@"
