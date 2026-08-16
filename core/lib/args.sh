#!/usr/bin/env bash
# GREP_SUMMARY: args, usage, standardization, boilerplate-dedup
# STRUCTURE: ▶ usage(script, desc, options) → ◇ format → ⎋ print stderr + exit 0
# region MODULE_CONTRACT
## @purpose  Стандартизированная обработка аргументов для entrypoints + scaffold.
##           usage() — единая справка (--help/-h).
##           parse_args в этом lib нет: все entrypoints (bootstrap/node-update/converge/
##           adopt-project) определяют СВОЙ parse_args — контракт args.sh несовместим
##           с passthrough-паттерном (shared-версии нет).
## @scope    Sourced by entrypoints/*.sh, internal/scaffold/*.sh, internal/bootstrap/*.sh.
## @invariants
##   - usage() на --help / -h → печать + exit 0
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
