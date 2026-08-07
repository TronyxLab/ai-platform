#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint core-deliver fallback deploy node-update provision git-outage age-key dry-run python3 thin-facade
# STRUCTURE: ▶ init ┌parse --node --dry-run --age-secret-key-file┐ → ◇ --node required → ○ resolve node.yaml (node_resolver CLI) → ○ detect AGE key (node_detect, exit 3 non-fatal) → ⚡ python3 core_deliverer fallback-deliver (deliver+provision+node-update, exit 0|1) → ⎋ passthrough
# region MODULE_CONTRACT
## @purpose  Thin entrypoint for `make core-deliver` — ЛОКАЛЬНОЕ зеркало core-deploy.yml
##           CI-воркфлоу (142 W5, A6): доставка core/ + scripts/ + makefiles/ + platform-env.yaml
##           → /opt/platform (guard'ы как в workflow) → provision → node-update (AGE_SECRET_KEY
##           из локальной цепочки node_detect). Использование: GitHub Actions недоступны
##           (Major Outage) / ручной деплой core. ВСЯ оркестрация — в core_deliverer.py
##           fallback-deliver (Strangler-Fig: shell = фасад, бинарные вызовы = Python-слой).
## @scope    Called ONLY from Makefile (makefiles/bootstrap.mk core-deliver). Owns usage+main.
## @invariants
##   - НЕ трогает /opt/node-configs (орг-репозиторий, gitignored — доставлен bootstrap'ом)
##   - 0 прямых бинарных вызовов в entrypoint (гейт test_entrypoint_no_direct_binary_calls):
##     вся доставка делегируется `python3 -m core.internal.bootstrap.core_deliverer fallback-deliver`
##   - AGE_SECRET_KEY уходит в remote ТОЛЬКО как env (канон W4 DevPlan 140; путь на remote не передаётся)
##   - --dry-run: печатает команды без мутаций (R5 142 W5)
## @rationale 142 W5 (Q4 «а»): fallback-таргет CI-канала. В циклах 1/2 141 GitHub Major Outage
##           (16:30-20:30) блокировал core-deploy — ручной эквивалент воспроизводился вручную (A6).
##           Имя core-deliver НЕ конфликтует с forbidden-глаголами. Оркестрация доставки
##           переиспользует канонический Core-канал core_deliverer.py (DevPlan 108) — 0 дублей.
## @changes 2026-08-06 | Created (142 W5)
##           2026-08-07 | Refactored: 152 LOC shell → тонкий фасад (гейты thin-wrapper/layer2 RED)
# endregion MODULE_CONTRACT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${CORE_DIR}/lib/args.sh"

USAGE_SCRIPT="core-deliver.sh"
USAGE_DESC="Локальное зеркало CI core-доставки: деплой core → provision → node-update (fallback при GitHub Outage)."
USAGE_OPTIONS=(
    "--node <name>              Node name (required)"
    "--dry-run                  Print commands without executing"
    "--age-secret-key-file <f>  Path to AGE secret key (читается ЛОКАЛЬНО)"
)

NODE_NAME=""; DRY_RUN=false; AGE_SECRET_KEY_FILE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --node|--node-name) NODE_NAME="$2"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        --age-secret-key-file) AGE_SECRET_KEY_FILE="$2"; export AGE_SECRET_KEY_FILE; shift 2 ;;
        --help|-h) usage "$USAGE_SCRIPT" "${USAGE_DESC:-}" "${USAGE_OPTIONS[@]:-}" ;;
        *) echo "[IMP:10][core-deliver][entrypoint] Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "${NODE_NAME}" ]]; then
    echo "[IMP:10][core-deliver][entrypoint] FATAL: --node is required" >&2
    echo "  Usage: core-deliver.sh --node <name> [--dry-run] [--age-secret-key-file <f>]" >&2
    exit 1
fi

## @purpose  Тонкий фасад: resolve node.yaml + host + AGE key → делегирование в Python core_deliverer.
main() {
    echo "[IMP:9][core-deliver][entrypoint] Starting core-deliver for NODE=${NODE_NAME}" >&2

    # ── Resolve node.yaml + host (Python CLI, exit 0/1 — канон node-resolver.sh) ──
    local node_yaml=""
    node_yaml="$(python3 -m core.internal.shared.node_resolver resolve --node "${NODE_NAME}" 2>/dev/null)" || {
        echo "[IMP:10][core-deliver][entrypoint] FATAL: Cannot resolve node.yaml for node=${NODE_NAME}" >&2
        exit 1
    }
    local host=""
    host="$(python3 -m core.internal.shared.node_resolver host --file "${node_yaml}" 2>/dev/null)" || {
        echo "[IMP:10][core-deliver][entrypoint] FATAL: Cannot read node.yaml (${node_yaml})" >&2
        exit 1
    }
    if [[ -z "${host}" ]]; then
        echo "[IMP:10][core-deliver][entrypoint] FATAL: No host in node.yaml (${node_yaml}) — core-deliver требует remote VPS" >&2
        exit 1
    fi
    echo "[IMP:8][core-deliver][entrypoint] node.yaml=${node_yaml} host=${host}" >&2

    # ── Detect AGE key (локальная цепочка node_detect; exit 3 = key absent, non-fatal) ──
    local detected_age_key=""
    detected_age_key="$(python3 -m core.internal.shared.node_detect --detect-age-key 2>/dev/null)" || {
        local _detect_rc=$?
        if [[ ${_detect_rc} -eq 3 ]]; then
            detected_age_key=""
            echo "[IMP:7][core-deliver][entrypoint] WARN: AGE key not found — φ9 decrypt will be skipped" >&2
        else
            echo "[IMP:10][core-deliver][entrypoint] FATAL: python3 or node_detect unavailable" >&2
            exit 1
        fi
    }

    # ── Делегирование: фазы доставки + provision + node-update в Python (exit 0|1) ──
    local dry_arg=""
    [[ "${DRY_RUN}" == "true" ]] && dry_arg="--dry-run"
    echo "[IMP:8][core-deliver][entrypoint] Delegating to core_deliverer fallback-deliver (host=${host})" >&2
    python3 -m core.internal.bootstrap.core_deliverer fallback-deliver \
        --host "${host}" \
        --node "${NODE_NAME}" \
        --core-dir "${CORE_DIR}" \
        --age-secret-key "${detected_age_key}" \
        ${dry_arg:+--dry-run}
}

main "$@"
