#!/usr/bin/env bash
# GREP_SUMMARY: args, parse-args, usage, standardization, boilerplate-dedup
# STRUCTURE: ▶ parse_args(spec, "$@") → ◇ loop args → ⊕ match spec → ⎋ assoc array | usage+exit
#            ▶ usage(script, desc, options) → ◇ format → ⎋ print stderr + exit 0
# region MODULE_CONTRACT
## @purpose  Стандартизированная обработка аргументов для entrypoints + scaffold.
##           Заменяет 12 локальных usage() и 8+ локальных parse_args.
## @scope    Sourced by entrypoints/*.sh, internal/scaffold/*.sh, internal/bootstrap/*.sh.
## @invariants
##   - Bash >= 4.0 (declare -A для assoc arrays)
##   - Поддерживает: --help, -h, --context <val>, --mode <val>, --dry-run, --verbose
##   - Custom options через spec array: ["--option"]="value_required|flag"
##   - На --help / -h → вызов usage() + exit 0
## @rationale Brief 027 §3.1 W1-E5: единый lib-layer, -400 строк boilerplate.
## @changes
##   LAST_CHANGE: 2026-07-21 | Created (DevPlan 028 W1-E5)
# endregion MODULE_CONTRACT

# Requires: lib/logging.sh (sourced by caller)

# region USAGE


usage() {
    local script_name="$1"
    local description="$2"
    shift 2
    local -a options=("$@")

    echo "Usage: ${script_name} [OPTIONS]"
    echo ""
    echo "  ${description}"
    echo ""
    echo "Options:"
    for opt in "${options[@]}"; do
        # Format: "--flag <value> | description"
        echo "  ${opt}"
    done
    echo ""
    echo "Common options:"
    echo "  --help, -h        Show this help and exit"
    echo "  --context <name>  Platform context (default: from path)"
    echo "  --mode <mode>     Operation mode"
    echo "  --dry-run         Show actions without executing"
    echo "  --verbose         Enable verbose logging"
    echo ""
    exit 0
}


# endregion USAGE


# region PARSE_ARGS


parse_args() {
    # Usage: parse_args <spec_assoc_array_name> -- "$@"
    # spec format: declare -A SPEC=( [--context]="value" [--dry-run]="flag" ... )
    # Returns: assoc array with parsed values, exit 0
    # On --help: calls usage (caller must set USAGE_SCRIPT/USAGE_DESC before)
    local -n _spec_ref="$1"
    local -n _result_ref="$2"
    shift 2
    # shift past "--"
    [[ "${1:-}" == "--" ]] && shift

    # Initialize result with defaults
    local opt
    for opt in "${!_spec_ref[@]}"; do
        _result_ref["$opt"]=""
    done

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --help|-h)
                if [[ -n "${USAGE_SCRIPT:-}" ]]; then
                    usage "$USAGE_SCRIPT" "${USAGE_DESC:-}" "${USAGE_OPTIONS[@]:-}"
                fi
                exit 0
                ;;
            --*)
                opt="$1"
                if [[ -z "${_spec_ref[$opt]+x}" ]]; then
                    echo "[IMP:10][args] unknown option: $opt" >&2
                    return 1
                fi
                if [[ "${_spec_ref[$opt]}" == "flag" ]]; then
                    _result_ref["$opt"]="1"
                    shift
                else
                    # value required
                    if [[ $# -lt 2 ]]; then
                        echo "[IMP:10][args] option $opt requires value" >&2
                        return 1
                    fi
                    _result_ref["$opt"]="$2"
                    shift 2
                fi
                ;;
            *)
                echo "[IMP:10][args] unexpected positional arg: $1" >&2
                return 1
                ;;
        esac
    done

    return 0
}


# endregion PARSE_ARGS
