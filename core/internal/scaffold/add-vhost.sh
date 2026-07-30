#!/usr/bin/env bash
# GREP_SUMMARY: add-vhost nginx vhost generate remove render-all shell-facade vhost-renderer dispatch
# STRUCTURE: parse_args(─add/─remove/─render-all) → ┌-[add] python3 -m vhost_renderer add┐ → ┌-[render-all] python3 -m vhost_renderer render-all┐ → exit
# region MODULE_CONTRACT
## @purpose  Shell facade for vhost_renderer.py (Strangler-Fig). Dispatches to
##           python3 -m core.internal.scaffold.vhost_renderer with 3 modes:
##           --add, --remove, --render-all. Zero inline python3.
## @scope    Entry point called by: Makefile render-vhosts, add-project.sh, remove-project.sh,
##           adopt-project.sh. All business logic delegated to vhost_renderer.py.
## @invariants
##   - Zero inline python3 -c / <<PYEOF blocks (enforced by CI grep)
##   - All YAML parsing, template generation, nginx harness — in Python
##   - Exit code propagated from Python module
##   - Same CLI interface as pre-migration (926 LOC version)
## @rationale  Strangler-Fig: 926→129 LOC, 84% reduction. Language policy enforcement.
## @changes    2026-07-26 · Wave 5b — Rewritten as thin shell facade (129 LOC)
## ⚠️ TRAP[DECISION] · 2026-07-26 · — · add-vhost.sh мигрирован в vhost_renderer.py через Strangler-Fig
## · Rejected: keeping vhost generation in shell (risk: 926 LOC monolith, grep-based YAML, inline python3)
## · Reason: языковая политика (AGENTS.md), тестируемость
## · Rev: если Python vhost_renderer генерирует vhost'ы >5% медленнее shell-версии → профилировать
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_ROOT="${PLATFORM_ROOT:-$(cd "${SCRIPT_DIR}/../../.." 2>/dev/null && pwd || echo "$(dirname "$(dirname "$(dirname "$SCRIPT_DIR")")")")}"

PROJECT_DIR=""
NODE_CONFIGS_DIR=""
MODE="add"
RENDER_NODE=""

__LOG_PREFIX="add-vhost"
source "${PLATFORM_ROOT}/core/lib/logging.sh"

# ── USAGE ────────────────────────────────────────────────────────────────
usage() {
    cat <<'HELP'
USAGE: add-vhost.sh <mode> [options]

MODES:
  --add                        Generate vhost for a single project (default)
  --remove                     Remove vhost (deletes file + writes audit-log)
  --render-all --node <n>      Batch-render ALL vhosts from node.yaml#projects

REQUIRED (add/remove):
  --project-dir <path>         Path to the project directory
  --node-configs-dir <path>    Path to node-configs/ directory

REQUIRED (render-all):
  --node <n>                   Node name to render vhosts for
  --node-configs-dir <path>    Path to node-configs/ directory

OPTIONS:
  --help|-h                    Show this help

EXAMPLES:
  add-vhost.sh --add --project-dir /path/to/project --node-configs-dir /p/n
  add-vhost.sh --remove --project-dir /path/to/project --node-configs-dir /p/n
  add-vhost.sh --render-all --node tronyx-vps --node-configs-dir /p/n
HELP
    exit 1
}

# ── PARSE_ARGS ──────────────────────────────────────────────────────────
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --project-dir)       PROJECT_DIR="$2"; shift 2 ;;
            --node-configs-dir)  NODE_CONFIGS_DIR="$2"; shift 2 ;;
            --add)               MODE="add"; shift ;;
            --remove)            MODE="remove"; shift ;;
            --render-all)        MODE="render-all"; shift ;;
            --node)              RENDER_NODE="$2"; shift 2 ;;
            --help|-h)           usage ;;
            *)                   log_crit "Unknown argument: $1"; usage ;;
        esac
    done

    if [[ "$MODE" == "render-all" ]]; then
        [[ -z "$RENDER_NODE" ]] && { log_crit "--node <n> required for --render-all"; usage; }
        [[ -z "$NODE_CONFIGS_DIR" ]] && { log_crit "--node-configs-dir required"; usage; }
        [[ ! -d "$NODE_CONFIGS_DIR" ]] && { log_crit "Node configs dir not found: ${NODE_CONFIGS_DIR}"; exit 1; }
        return 0
    fi

    [[ -z "$PROJECT_DIR" || -z "$NODE_CONFIGS_DIR" ]] && { log_crit "--project-dir and --node-configs-dir required"; usage; }
    [[ ! -d "$PROJECT_DIR" ]] && { log_crit "Project dir not found: ${PROJECT_DIR}"; exit 1; }
    [[ ! -d "$NODE_CONFIGS_DIR" ]] && { log_crit "Node configs dir not found: ${NODE_CONFIGS_DIR}"; exit 1; }
}

# ── MAIN ─────────────────────────────────────────────────────────────────
main() {
    echo "[IMP:8][add-vhost][main] Starting vhost management" >&2
    parse_args "$@"

    local python_module="core.internal.scaffold.vhost_renderer"
    local common_args=""

    [[ -n "${PLATFORM_DOMAIN:-}" ]] && common_args+=" --platform-domain ${PLATFORM_DOMAIN}"
    [[ -n "${PLATFORM_ROOT:-}" ]] && common_args+=" --platform-root ${PLATFORM_ROOT}"

    if [[ "$MODE" == "render-all" ]]; then
        log_imp 8 "main" "Mode: render-all for node=${RENDER_NODE}"
        # shellcheck disable=SC2086
        exec python3 -m "$python_module" $common_args render-all \
            --node "$RENDER_NODE" \
            --node-configs-dir "$NODE_CONFIGS_DIR"
    fi

    log_imp 8 "main" "START: add-vhost mode=${MODE} for ${PROJECT_DIR}"

    if [[ "$MODE" == "remove" ]]; then
        # shellcheck disable=SC2086
        exec python3 -m "$python_module" $common_args remove \
            --project-dir "$PROJECT_DIR" \
            --node-configs-dir "$NODE_CONFIGS_DIR"
    fi

    # Add mode (default)
    # shellcheck disable=SC2086
    exec python3 -m "$python_module" $common_args add \
        --project-dir "$PROJECT_DIR" \
        --node-configs-dir "$NODE_CONFIGS_DIR"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
