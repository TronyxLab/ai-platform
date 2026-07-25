#!/usr/bin/env bash
# GREP_SUMMARY: provision-environment thin-wrapper provisioner.py scope dispatch
# STRUCTURE: parse_args(--scope,--platform-env,--dry-run) → for scope ∈ scopes: audit_step "provision:$scope" python3 provisioner.py → ⎋ exit
# region MODULE_CONTRACT
## @purpose  Thin shell wrapper for core/internal/provisioner.py — parses CLI args,
##           expands "all" scope, dispatches per-scope with audit_step.
## @scope    Called from Makefile, CI workflows, deploy-modules.sh, state_machine.py.
## @invariants
##   - ZERO inline python3 or python3 -c calls
##   - All business logic in provisioner.py
##   - Shell wrapper: arg parsing + scope expansion + audit_step dispatch
##   - --scope is required (no default)
##   - Multi-scope via --scope A --scope B (accumulator, deduplication)
##   - Exit codes propagate from provisioner.py (0=success, 1=parse error, 2=docker unavailable)
## @rationale Strangler-Fig migration: 442 LOC shell → ~55 LOC wrapper + ~340 LOC Python.
##   Eliminates 13 inline python3 calls. Shell remains as audit_step integrator.
# endregion MODULE_CONTRACT

set -euo pipefail

__PROVISION_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
__PROVISION_PLATFORM_ROOT="$(cd "${__PROVISION_SCRIPT_DIR}/../.." && pwd)"

source "${__PROVISION_SCRIPT_DIR}/../lib/audit_logging.sh"

__PROVISION_DEFAULT_PLATFORM_ENV="${__PROVISION_PLATFORM_ROOT}/platform-env.yaml"

# ── Arg parsing ──────────────────────────────────────────────────────────────
PLATFORM_ENV=""
DRY_RUN="false"
# Accumulator for multi-scope (FIX-1 regression: scalar→array)
SCOPES=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --scope)
            shift
            if [[ -z "${1:-}" ]]; then
                echo "FATAL: --scope requires a value" >&2
                exit 1
            fi
            SCOPES+=("$1")
            ;;
        --platform-env)
            shift
            if [[ -z "${1:-}" ]]; then
                echo "FATAL: --platform-env requires a path" >&2
                exit 1
            fi
            PLATFORM_ENV="$1"
            ;;
        --dry-run)
            DRY_RUN="true"
            ;;
        --help|-h)
            cat <<'USAGE'
Usage: provision-environment.sh --scope <networks|volumes|env|all|profiles> [--platform-env <path>] [--dry-run]

Flags:
  --scope <scope>       Scope of provisioning (required)
  --platform-env <path> Path to platform-env.yaml (default: auto-detect)
  --dry-run             Print actions without executing (for testing)

Exit codes:
  0  Success (all resources created or already exist)
  1  Error parsing platform-env.yaml
  2  Docker unavailable (for --scope networks)
USAGE
            exit 0
            ;;
        *)
            echo "ERROR: Unknown argument: $1" >&2
            exit 1
            ;;
    esac
    shift
done

# ── Validate --scope ─────────────────────────────────────────────────────────
if [[ ${#SCOPES[@]} -eq 0 ]]; then
    echo "FATAL: --scope is required" >&2
    echo "Usage: $0 --scope <networks|volumes|env|all|profiles> [--platform-env <path>] [--dry-run]" >&2
    exit 1
fi

# ── Expand 'all' to concrete scopes and deduplicate ──────────────────────────
EXPANDED_SCOPES=()
declare -A _SEEN
for _s in "${SCOPES[@]}"; do
    case "$_s" in
        networks|volumes|env|profiles)
            if [[ -z "${_SEEN[$_s]:-}" ]]; then
                _SEEN[$_s]=1
                EXPANDED_SCOPES+=("$_s")
            fi
            ;;
        all)
            for _as in networks volumes env profiles; do
                if [[ -z "${_SEEN[$_as]:-}" ]]; then
                    _SEEN[$_as]=1
                    EXPANDED_SCOPES+=("$_as")
                fi
            done
            ;;
        *)
            echo "FATAL: Unknown scope '${_s}'. Valid values: networks, volumes, env, profiles, all" >&2
            exit 1
            ;;
    esac
done

# ── Scope label (original scopes, comma-separated) ────────────────────────────
_SCOPE_LABEL=$(IFS=,; echo "${SCOPES[*]}")

# ── Resolve platform-env path ────────────────────────────────────────────────
if [[ -z "$PLATFORM_ENV" ]]; then
    PLATFORM_ENV="$__PROVISION_DEFAULT_PLATFORM_ENV"
fi

# ── Dispatch ─────────────────────────────────────────────────────────────────
DRY_RUN_ARG=""
[[ "$DRY_RUN" == "true" ]] && DRY_RUN_ARG="--dry-run"

for _s in "${EXPANDED_SCOPES[@]}"; do
    audit_step "provision:${_s}" \
        python3 "${__PROVISION_SCRIPT_DIR}/provisioner.py" \
            --scope "$_s" \
            --platform-env "$PLATFORM_ENV" \
            $DRY_RUN_ARG
done

echo "[IMP:9][provision] Provision complete (scope=${_SCOPE_LABEL})" >&2
