#!/usr/bin/env bash
# GREP_SUMMARY: provision-environment, platform-env, docker-network, volume-dir, ci-env, idempotent
# STRUCTURE: parse_args → _load_platform_env → dispatch(--scope) → networks(via inspect∨create) | volumes(mkdir -p) | env(→$GITHUB_ENV) → ⎋ exit 0|1|2
# region MODULE_CONTRACT
## @purpose  Idempotent environment provisioner — reads platform-env.yaml and creates
##           Docker networks, volume directories, and CI env vars.
## @scope    Called from Makefile, CI workflows, and standalone.
##           Guaranteed to run on macOS (bash), CI (ubuntu-latest), and VPS (bash).
## @location core/internal/provision-environment.sh
## @invariants
##   - Reading platform-env.yaml via core/lib/yaml_read.sh.
##     Fallback chain: yq→python3, then grep/awk for basic list extraction.
##   - --scope networks: docker network inspect || docker network create (idempotent)
##   - --scope volumes:  mkdir -p (idempotent by design)
##   - --scope env:      export KEY=value to $GITHUB_ENV (CI only)
##   - --dry-run:        print actions without executing (for testing)
##   - Exit codes: 0=success, 1=parse error, 2=docker unavailable
## @rationale  Eliminates 4 independent sources of env config (Makefile, infra.py,
##             CI workflows, deploy-modules.sh). Single idempotent entry point.
## @changes 2026-07-21 | W2-E3 — Added audit_step wrapper for provision dispatch (source audit_logging.sh)
# endregion MODULE_CONTRACT

set -euo pipefail

# ── Constants ──────────────────────────────────────────────────────────────────
__PROVISION_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
__PROVISION_DEFAULT_PLATFORM_ENV=""

# Auto-detect platform root: script is at core/internal/provision-environment.sh
__PROVISION_PLATFORM_ROOT="$(cd "${__PROVISION_SCRIPT_DIR}/../.." && pwd)"

# Source yaml_read library — replaces inline python3+yaml calls
# yaml_read.sh is always under core/lib/ regardless of deployment layout
# ⚠️ TRAP[BUG] · 2026-07-17 · MED · dual-path source masked missing file with || true
# · Old code used if/else with 2>/dev/null || true — silently skipped missing yaml_read.sh
# · Fix: single absolute path via __PROVISION_SCRIPT_DIR/../
# ·   Path `${ROOT}/lib/yaml_read.sh` (without core/) never existed in any layout
source "${__PROVISION_SCRIPT_DIR}/../lib/yaml_read.sh"
source "${__PROVISION_SCRIPT_DIR}/../lib/audit_logging.sh"
__PROVISION_DEFAULT_PLATFORM_ENV="${__PROVISION_PLATFORM_ROOT}/platform-env.yaml"

# ── Logging ────────────────────────────────────────────────────────────────────
__provision_log() {
    local level="$1"
    local message="$2"
    echo "[IMP:${level}][provision] ${message}" >&2
}

__provision_log_section() {
    local level="$1"
    local section="$2"
    local message="$3"
    echo "[IMP:${level}][provision][${section}] ${message}" >&2
}

# ── YAML Parsing ───────────────────────────────────────────────────────────────
# Primary: core/lib/yaml_read.sh (yaml_get_field / yaml_get_list)
# Fallback: yq → json → python3
# Last resort: grep/awk for simple lists

_load_platform_env_yaml() {
    local yaml_path="$1"
    local section="$2"  # networks, volumes, env_defaults, profiles

    if [[ ! -f "$yaml_path" ]]; then
        __provision_log "10" "FATAL: platform-env.yaml not found at ${yaml_path}"
        return 1
    fi

    # Primary: yaml_read.sh — returns JSON for dict/list sections
    if declare -f yaml_get_field >/dev/null 2>&1; then
        yaml_get_field "$yaml_path" "$section" 2>/dev/null && return 0
    fi

    # Fallback: yq → json → python3
    if command -v yq &>/dev/null; then
        yq -o=json "$yaml_path" 2>/dev/null | python3 -c "
import json, sys
data = json.load(sys.stdin)
section = data.get('${section}', [])
print(json.dumps(section))
" 2>/dev/null && return 0
    fi

    # Last resort: grep/awk for simple lists
    # Only works for list[str] (networks, volumes, profiles)
    case "$section" in
        networks)
            grep -E '^\s+- name:' "$yaml_path" 2>/dev/null | awk -F': ' '{print $2}' | tr -d ' "' && return 0
            ;;
        volumes)
            grep -E '^\s+- path:' "$yaml_path" 2>/dev/null | awk -F': ' '{print $2}' | tr -d ' "' && return 0
            ;;
        profiles)
            grep -E '^\s+- [a-z]' "$yaml_path" 2>/dev/null | awk '{print $2}' | tr -d ' "' && return 0
            ;;
        env_defaults)
            # Extract key: value pairs from env_defaults section
            awk '/^env_defaults:/{flag=1; next} /^[a-z]/{flag=0} flag && /^  [A-Z]/' "$yaml_path" 2>/dev/null && return 0
            ;;
    esac

    __provision_log "10" "FATAL: Cannot parse ${yaml_path} section=${section} — yaml_read.sh, yq, and grep fallback all failed"
    return 1
}

# ── Scope: Networks ────────────────────────────────────────────────────────────
_provision_networks() {
    local yaml_path="$1"
    local dry_run="${2:-false}"

    __provision_log_section "7" "networks" "Reading platform-env.yaml networks from ${yaml_path}"

    local networks_json
    networks_json="$(_load_platform_env_yaml "$yaml_path" "networks")" || {
        __provision_log_section "10" "networks" "Failed to parse networks from platform-env.yaml"
        return 1
    }

    local created=0
    local skipped=0

    # Parse JSON list of {name, driver, internal}
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        local net_name
        net_name=$(echo "$line" | python3 -c "import json,sys; print(json.load(sys.stdin).get('name',''))" 2>/dev/null)
        local net_driver
        net_driver=$(echo "$line" | python3 -c "import json,sys; print(json.load(sys.stdin).get('driver','bridge'))" 2>/dev/null)
        [[ -z "$net_name" ]] && continue

        if [[ "$dry_run" == "true" ]]; then
            __provision_log_section "7" "networks" "DRY-RUN: Would create network: ${net_name} (driver: ${net_driver})"
            created=$((created + 1))
            continue
        fi

        if docker network inspect "$net_name" &>/dev/null 2>&1; then
            __provision_log_section "7" "networks" "SKIP: network ${net_name} already exists"
            skipped=$((skipped + 1))
        else
            __provision_log_section "7" "networks" "Creating network: ${net_name} (driver: ${net_driver})"
            docker network create --driver "$net_driver" "$net_name" >/dev/null 2>&1
            created=$((created + 1))
        fi
    done < <(echo "$networks_json" | python3 -c "
import json, sys
nets = json.load(sys.stdin)
for n in nets:
    print(json.dumps(n))
" 2>/dev/null)

    __provision_log_section "9" "networks" "Networks provisioned: ${created} created, ${skipped} skipped"
}

# ── Scope: Volumes ─────────────────────────────────────────────────────────────
_provision_volumes() {
    local yaml_path="$1"
    local dry_run="${2:-false}"

    __provision_log_section "7" "volumes" "Reading platform-env.yaml volumes from ${yaml_path}"

    local volumes_json
    volumes_json="$(_load_platform_env_yaml "$yaml_path" "volumes")" || {
        __provision_log_section "10" "volumes" "Failed to parse volumes from platform-env.yaml"
        return 1
    }

    local created=0
    local skipped=0

    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        local vol_path
        vol_path=$(echo "$line" | python3 -c "import json,sys; print(json.load(sys.stdin).get('path',''))" 2>/dev/null)
        [[ -z "$vol_path" ]] && continue

        if [[ "$dry_run" == "true" ]]; then
            __provision_log_section "7" "volumes" "DRY-RUN: Would create directory: ${vol_path}"
            created=$((created + 1))
            continue
        fi

        if [[ -d "$vol_path" ]]; then
            __provision_log_section "7" "volumes" "SKIP: directory already exists: ${vol_path}"
            skipped=$((skipped + 1))
        else
            __provision_log_section "7" "volumes" "Creating directory: ${vol_path}"
            mkdir -p "$vol_path" 2>/dev/null || {
                __provision_log_section "7" "volumes" "WARN: Cannot create ${vol_path} (permission denied)"
                skipped=$((skipped + 1))
                continue
            }
            created=$((created + 1))
        fi
    done < <(echo "$volumes_json" | python3 -c "
import json, sys
vols = json.load(sys.stdin)
for v in vols:
    print(json.dumps(v))
" 2>/dev/null)

    __provision_log_section "9" "volumes" "Volumes provisioned: ${created} created, ${skipped} skipped"
}

# ── Scope: Env ─────────────────────────────────────────────────────────────────
_provision_env() {
    local yaml_path="$1"
    local dry_run="${2:-false}"

    __provision_log_section "7" "env" "Reading platform-env.yaml env_defaults from ${yaml_path}"

    local env_json
    env_json="$(_load_platform_env_yaml "$yaml_path" "env_defaults")" || {
        __provision_log_section "10" "env" "Failed to parse env_defaults from platform-env.yaml"
        return 1
    }

    local count=0

    if [[ "$dry_run" == "true" ]]; then
        # Print env vars without writing
        echo "$env_json" | python3 -c "
import json, sys
envs = json.load(sys.stdin)
for k, v in envs.items():
    print(f'DRY-RUN: Would export {k}={v}')
" 2>/dev/null
        count=$(echo "$env_json" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null)
        __provision_log_section "7" "env" "DRY-RUN: Would export ${count} env vars"
        return 0
    fi

    # Export to GITHUB_ENV (CI) or stdout (local)
    if [[ -n "${GITHUB_ENV:-}" ]]; then
        __provision_log_section "7" "env" "Exporting env vars to GITHUB_ENV=${GITHUB_ENV}"
        echo "$env_json" | python3 -c "
import json, sys
envs = json.load(sys.stdin)
for k, v in envs.items():
    print(f'{k}={v}')
" 2>/dev/null >> "$GITHUB_ENV"
        count=$(echo "$env_json" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null)
        __provision_log_section "9" "env" "${count} env vars exported to GITHUB_ENV"
    else
        # Local mode: print to stderr for visibility
        __provision_log_section "7" "env" "GITHUB_ENV not set — printing env vars to stderr"
        echo "$env_json" | python3 -c "
import json, sys
envs = json.load(sys.stdin)
for k, v in envs.items():
    print(f'  {k}={v}')
" 2>/dev/null >&2
        __provision_log_section "9" "env" "Env vars printed (GITHUB_ENV not set — local mode)"
    fi
}

# ── Scope: Profiles ────────────────────────────────────────────────────────────
_provision_profiles() {
    local yaml_path="$1"

    local profiles_json
    profiles_json="$(_load_platform_env_yaml "$yaml_path" "profiles")" || {
        __provision_log_section "10" "profiles" "Failed to parse profiles from platform-env.yaml"
        return 1
    }

    local count
    count=$(echo "$profiles_json" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null)
    __provision_log_section "8" "profiles" "Profiles available: ${count}"
}

# ── Main ───────────────────────────────────────────────────────────────────────
__provision_usage() {
    echo "Usage: $0 --scope <networks|volumes|env|all|profiles> [--platform-env <path>] [--dry-run]"
    echo ""
    echo "Flags:"
    echo "  --scope <scope>       Scope of provisioning (required)"
    echo "  --platform-env <path> Path to platform-env.yaml (default: auto-detect)"
    echo "  --dry-run             Print actions without executing (for testing)"
    echo ""
    echo "Exit codes:"
    echo "  0  Success (all resources created or already exist)"
    echo "  1  Error parsing platform-env.yaml"
    echo "  2  Docker unavailable (for --scope networks)"
}

main() {
    # ⚠️ TRAP[BUG] · 2026-07-15 · P2 · Multi-scope: scalar→array accumulator
    # · Symptom: --scope networks --scope volumes → only volumes executed (last-wins)
    # · Root: `local scope=""` scalar overwrites previous value on each --scope flag
    # · Fix: `local -a scopes=()` accumulates all --scope values, dispatches iteratively
    # · Prevention: gate test test_parse_multi_scope_accumulates in unit tests
    local -a scopes=()
    local platform_env=""
    local dry_run="false"

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --scope)
                shift
                if [[ -z "${1:-}" ]]; then
                    __provision_log "10" "FATAL: --scope requires a value"
                    __provision_usage
                    exit 1
                fi
                scopes+=("$1")
                ;;
            --platform-env)
                shift
                if [[ -z "${1:-}" ]]; then
                    __provision_log "10" "FATAL: --platform-env requires a path"
                    __provision_usage
                    exit 1
                fi
                platform_env="$1"
                ;;
            --dry-run)
                dry_run="true"
                ;;
            --help|-h)
                __provision_usage
                exit 0
                ;;
            *)
                __provision_log "10" "Unknown argument: $1"
                __provision_usage
                exit 1
                ;;
        esac
        shift
    done

    # ── Validate --scope ──────────────────────────────────────────────────
    if [[ ${#scopes[@]} -eq 0 ]]; then
        __provision_log "10" "FATAL: --scope is required"
        __provision_usage
        exit 1
    fi

    # Expand 'all' to concrete scopes and deduplicate
    local -a expanded_scopes=()
    local -A _seen_scopes
    for _s in "${scopes[@]}"; do
        case "$_s" in
            networks|volumes|env|profiles)
                if [[ -z "${_seen_scopes[$_s]:-}" ]]; then
                    _seen_scopes[$_s]=1
                    expanded_scopes+=("$_s")
                fi
                ;;
            all)
                for _as in networks volumes env profiles; do
                    if [[ -z "${_seen_scopes[$_as]:-}" ]]; then
                        _seen_scopes[$_as]=1
                        expanded_scopes+=("$_as")
                    fi
                done
                ;;
            *)
                __provision_log "10" "FATAL: Unknown scope '${_s}'. Valid values: networks, volumes, env, profiles, all"
                __provision_usage
                exit 1
                ;;
        esac
    done
    # If 'all' was among scopes, record it for the final log message
    local _scope_label
    _scope_label=$(IFS=,; echo "${scopes[*]}")

    # ── Resolve platform-env path ─────────────────────────────────────────
    if [[ -z "$platform_env" ]]; then
        platform_env="$__PROVISION_DEFAULT_PLATFORM_ENV"
    fi

    __provision_log "7" "Reading platform-env.yaml from ${platform_env}"

    if [[ ! -f "$platform_env" ]]; then
        __provision_log "10" "FATAL: platform-env.yaml not found at ${platform_env}"
        exit 1
    fi

    # ── Check Docker availability (based on expanded scopes) ──────────────
    local _need_docker=false
    local _need_env=false
    for _s in "${expanded_scopes[@]}"; do
        case "$_s" in
            networks) _need_docker=true ;;
            env)      _need_env=true ;;
        esac
    done

    if [[ "$_need_docker" == "true" && "$dry_run" != "true" ]]; then
        if ! docker info &>/dev/null 2>&1; then
            __provision_log "10" "FATAL: Docker is not available (required for --scope networks)"
            exit 2
        fi
    fi

    # ── Check GITHUB_ENV for env scope (only if not dry-run) ──────────────
    if [[ "$_need_env" == "true" && "$dry_run" != "true" ]]; then
        if [[ -z "${GITHUB_ENV:-}" ]]; then
            __provision_log "7" "env" "GITHUB_ENV not set — env vars will be printed to stderr (local mode)"
        fi
    fi

    # ── Parse count info ──────────────────────────────────────────────────
    local net_count=0 vol_count=0 env_count=0 prof_count=0
    if declare -f yaml_get_field >/dev/null 2>&1; then
        net_count=$(yaml_get_list "$platform_env" "networks" 2>/dev/null | wc -l) || net_count=0
        vol_count=$(yaml_get_list "$platform_env" "volumes" 2>/dev/null | wc -l) || vol_count=0
        prof_count=$(yaml_get_list "$platform_env" "profiles" 2>/dev/null | wc -l) || prof_count=0
        local _env_json
        _env_json=$(yaml_get_field "$platform_env" "env_defaults" 2>/dev/null) || _env_json="{}"
        env_count=$(echo "$_env_json" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null) || env_count=0
    fi
    __provision_log "8" "Parsed: ${net_count} networks, ${vol_count} volumes, ${env_count} env vars, ${prof_count} profiles"

    # ── Dispatch (iterate expanded scopes) — wrapped in audit_step ──
    _do_provision() {
        local platform_env="$1"
        local dry_run="${2:-false}"
        shift 2
        local -a expanded=("$@")

        for _s in "${expanded[@]}"; do
            case "$_s" in
                networks)   _provision_networks "$platform_env" "$dry_run" ;;
                volumes)    _provision_volumes "$platform_env" "$dry_run" ;;
                env)        _provision_env "$platform_env" "$dry_run" ;;
                profiles)   _provision_profiles "$platform_env" ;;
            esac
        done
    }

    audit_step "provision:${_scope_label}" \
        _do_provision "$platform_env" "$dry_run" "${expanded_scopes[@]}"

    __provision_log "9" "Provision complete (scope=${_scope_label})"
}

main "$@"
