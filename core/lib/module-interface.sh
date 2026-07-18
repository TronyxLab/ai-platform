#!/usr/bin/env bash
# GREP_SUMMARY: module-interface, invoke-module-interface, typed-contract, cross-layer, dispatch
# STRUCTURE: ┌module + interface + args┐ → ◇ validate via yaml_get_list(interfaces) → ◇ dispatch(healthcheck|install|deploy-hook|remove-hook) → ⎋ exit 0(skip/success)|1(script fail)|2(invalid)
# region MODULE_CONTRACT
## @purpose  Typed contract for cross-layer module invocation — replaces `bash "$variable"`
##           patterns with enforceable interface dispatch. Validates that the requested
##           interface is registered in module.yaml.interfaces before execution.
## @scope    Called from core/internal/ scripts (bootstrap, deploy) to invoke module scripts.
##           NOT a user-facing entrypoint — called programmatically by lifecycle scripts.
## @invariants
##   - Requires PATHS_MODULES_DIR from paths.sh — must be sourced before this library
##   - Requires yaml_get_list from yaml_read.sh — must be sourced before this library
##   - Requires log_imp from logging.sh — must be sourced before this library
##   - Does NOT source paths.sh/yaml_read.sh itself — avoids circular deps
##   - PATHS_MODULES_DIR must be set in environment (set by paths.sh in all callers)
##   - Exit codes: 0=success or graceful skip, 1=script failed, 2=invalid config
##   - Empty interfaces: [] or missing field → return 0 (skip) for any invocation
##   - Unknown interface name → return 0 (skip), not error
## @rationale Replaces 6 invisible `bash "$variable"` cross-layer calls with an enforceable
##            typed contract. The module declares supported interfaces in module.yaml;
##            invoke_module_interface validates and dispatches. This makes the cross-layer
##            boundary gate-enforceable (Gate #8 v2). See 05-DevPlan.md §1 for architecture.
## @changes
##   2026-07-18 · Created per Brief-CallSites.md / 05-DevPlan.md T2
# endregion MODULE_CONTRACT

set -euo pipefail

# Source yaml_read.sh for yaml_get_list/yaml_get_field
# ⚠️ NOT sourcing paths.sh — callers must source paths.sh first (PATHS_MODULES_DIR required)
_IM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_IM_DIR}/yaml_read.sh"

echo "[IMP:7][module-interface][lib] Loading module interface library" >&2

# ═══════════════════════════════════════════════════════════════════════════════
# region INVOKE_MODULE_INTERFACE
## @purpose  Validate and dispatch a typed cross-layer module call.
##           Checks that the requested interface is registered in module.yaml.interfaces.
##           Dispatch: healthcheck → healthcheck.sh, install → install.sh,
##           deploy-hook/remove-hook → hooks script path from module.yaml hooks field.
## @io       Input: $1=module_name, $2=interface_name, $@...=args
##           Output: stdout/stderr from invoked script (passthrough)
##           Return: 0=success or graceful skip, 1=script failed, 2=invalid config
## @example
##   invoke_module_interface postgres healthcheck liveness
##   invoke_module_interface platform-secrets install
##   invoke_module_interface monitoring deploy-hook /opt/projects/foo myapp my-vps
## @complexity O(1) — one Python call for validation, then bash dispatch
invoke_module_interface() {
    echo "[IMP:8][module-interface][invoke] Invoking module=${1} interface=${2}" >&2
    local module="$1"
    local interface="$2"
    shift 2

    local module_dir="${PATHS_MODULES_DIR}/${module}"
    local module_yaml="${module_dir}/module.yaml"

    # ── Module exists? ──
    if [[ ! -f "$module_yaml" ]]; then
        log_imp 9 "invoke_module_interface" \
            "INVALID: module.yaml not found for '${module}' at ${module_yaml}"
        return 2
    fi

    # ── Validate interface is registered ──
    if ! _invoke_validate_interface "$module_yaml" "$interface"; then
        # Interface not registered → graceful skip (exit 0)
        echo "[IMP:8][module-interface][skip] Interface '${interface}' not registered for module '${module}' — skipping" >&2
        return 0
    fi

    # ── Dispatch ──
    case "$interface" in
        healthcheck)
            _invoke_dispatch_healthcheck "$module_dir" "$@"
            ;;
        install)
            _invoke_dispatch_install "$module_dir"
            ;;
        deploy-hook)
            _invoke_dispatch_hook "$module_yaml" "hooks.on_project_deploy" "$@"
            ;;
        remove-hook)
            _invoke_dispatch_hook "$module_yaml" "hooks.on_project_remove" "$@"
            ;;
        *)
            log_imp 9 "invoke_module_interface" \
                "SKIP: Unknown interface '${interface}' for module '${module}' — skipping"
            return 0
            ;;
    esac
}
# endregion INVOKE_MODULE_INTERFACE

# ═══════════════════════════════════════════════════════════════════════════════
# region VALIDATE_INTERFACE
## @purpose  Check that an interface name is listed in module.yaml.interfaces.
##           Uses yaml_get_list to read the interfaces list, then greps for match.
## @io       Input: $1=module_yaml_path, $2=interface_name
##           Return: 0=registered, 1=not registered or field missing
## @complexity O(n) where n = number of registered interfaces
_invoke_validate_interface() {
    echo "[IMP:8][module-interface][validate] Checking interface=${2} in ${1}" >&2
    local module_yaml="$1"
    local interface="$2"

    # yaml_get_list reads a YAML list field — returns one item per line
    local interfaces
    interfaces="$(yaml_get_list "$module_yaml" "interfaces" 2>/dev/null)" || {
        # Field not found or not a list → treat as empty (no interfaces)
        echo "[IMP:8][module-interface][validate] No 'interfaces' field in ${module_yaml} — treating as empty" >&2
        return 1
    }

    # Check if interface is in the list
    while IFS= read -r iface; do
        if [[ "$iface" == "$interface" ]]; then
            echo "[IMP:8][module-interface][validate] Interface '${interface}' REGISTERED" >&2
            return 0
        fi
    done <<< "$interfaces"

    echo "[IMP:8][module-interface][validate] Interface '${interface}' NOT REGISTERED" >&2
    return 1
}
# endregion VALIDATE_INTERFACE

# ═══════════════════════════════════════════════════════════════════════════════
# region DISPATCH_HEALTHCHECK
## @purpose  Dispatch healthcheck interface — invoke module/healthcheck.sh with args.
## @io       Input: $1=module_dir, $@...=healthcheck args (liveness|readiness|deep)
##           Passes through exit code from healthcheck.sh.
##           Returns 0 if script not found (skip).
## @complexity 1 — single bash call
_invoke_dispatch_healthcheck() {
    echo "[IMP:8][module-interface][dispatch] Healthcheck: module_dir=${1}" >&2
    local module_dir="$1"
    shift

    local script="${module_dir}/healthcheck.sh"
    if [[ ! -f "$script" ]]; then
        echo "[IMP:8][module-interface][dispatch] No healthcheck.sh found — skipping" >&2
        return 0
    fi

    bash "$script" "$@"
}
# endregion DISPATCH_HEALTHCHECK

# ═══════════════════════════════════════════════════════════════════════════════
# region DISPATCH_INSTALL
## @purpose  Dispatch install interface — invoke module/install.sh.
## @io       Input: $1=module_dir
##           Returns 0 if script not found (skip).
## @complexity 1 — single bash call
_invoke_dispatch_install() {
    echo "[IMP:8][module-interface][dispatch] Install: module_dir=${1}" >&2
    local module_dir="$1"

    local script="${module_dir}/install.sh"
    if [[ ! -f "$script" ]]; then
        echo "[IMP:8][module-interface][dispatch] No install.sh found — skipping" >&2
        return 0
    fi

    bash "$script"
}
# endregion DISPATCH_INSTALL

# ═══════════════════════════════════════════════════════════════════════════════
# region DISPATCH_HOOK
## @purpose  Dispatch deploy-hook or remove-hook interface.
##           Reads hook script path from module.yaml hooks field (on_project_deploy
##           or on_project_remove), then invokes it with remaining args.
## @io       Input: $1=module_yaml, $2=hook_field (e.g. "hooks.on_project_deploy"),
##           $@...=hook args (PROJECT_DIR, PROJECT, NODE_NAME)
##           Returns 0 if hook script not found or field missing (skip).
## @rationale Reads hook path from module.yaml instead of hardcoding hooks/on-project-deploy.sh
##            — supports non-standard paths like nginx's nginx_reload_hook.sh (D4).
## @complexity O(1) — single yaml_get_field call + single bash call
_invoke_dispatch_hook() {
    echo "[IMP:8][module-interface][dispatch] Hook: yaml=${1} field=${2}" >&2
    local module_yaml="$1"
    local hook_field="$2"
    shift 2

    # Read hook path from module.yaml
    local hook_path
    hook_path="$(yaml_get_field "$module_yaml" "$hook_field" 2>/dev/null)" || {
        echo "[IMP:8][module-interface][dispatch] Hook field '${hook_field}' not found — skipping" >&2
        return 0
    }

    local module_dir
    module_dir="$(dirname "$module_yaml")"
    local script="${module_dir}/${hook_path}"

    if [[ ! -f "$script" ]]; then
        echo "[IMP:8][module-interface][dispatch] Hook script not found: ${script} — skipping" >&2
        return 0
    fi

    bash "$script" "$@"
}
# endregion DISPATCH_HOOK
