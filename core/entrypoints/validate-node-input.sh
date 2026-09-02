#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint validate-node-input preflight input-contract node-yaml resolve thin-facade devplan-029 T7
# STRUCTURE: ▶ init ┌--node/--node-yaml/--dry-run┐ → ◇ resolve node.yaml (node_resolver 4-path) → ⚡ python3 preflight.py --scope input → ⎋ exit {0,1}
# region MODULE_CONTRACT
## @purpose  Thin entrypoint for `make validate-node-input` (DevPlan 029 T7, DD-4): входной
##           контракт ноды (AGE-форма single-line, env-vs-file приоритет, SOPS enc-file наличие,
##           required-ключи) проверяется ЛОКАЛЬНО через core/internal/bootstrap/preflight.py
##           --scope input — exit 1 с причиной ДО любого SSH (0 remote). Тот же preflight-модуль,
##           что первый шаг bootstrap.sh (один input-contract, два входа — dual-mechanism дрейфа нет).
## @scope    Called ONLY from Makefile (make validate-node-input NODE=<name>).
## @invariants
##   - --node опционален при --node-yaml (прямой путь); иначе node.yaml резолвится node_resolver
##   - 0 inline python3: единственные вызовы — script-path python3 (preflight --scope input) и
##     канон node_resolver (python3 -m core.internal.shared.node_resolver — прецедент converge.sh)
##   - exit 0 = input-contract OK (включая warn-уровни env-приоритета); exit 1 = FATAL input-нарушение
## @rationale DD-4: validate-node-input = фасад над preflight, НЕ новый модуль — preflight уже
##            владеет probe-классификацией (FATAL/WARN); расширен скоупом input (D5).
## @changes 2026-09-02 · DevPlan 029 T7 — created
# endregion MODULE_CONTRACT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

NODE_NAME=""
NODE_YAML=""
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --node) NODE_NAME="$2"; shift 2 ;;
        --node-yaml) NODE_YAML="$2"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        --help|-h)
            echo "Usage: validate-node-input.sh --node <name> [--node-yaml <path>] [--dry-run]"
            echo "  Validates LOCAL input contract (AGE/sops/required keys) before any SSH (DevPlan 029 T7)."
            exit 0 ;;
        *) echo "[IMP:10][validate-node-input] FATAL: unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "${NODE_NAME}" && -z "${NODE_YAML}" ]]; then
    echo "[IMP:10][validate-node-input] FATAL: --node <name> or --node-yaml <path> required" >&2
    exit 1
fi

# ── Resolve node.yaml (если не передан напрямую): node_resolver — канон 4-path поиска ──
if [[ -z "${NODE_YAML}" ]]; then
    if ! NODE_YAML="$(PLATFORM_ROOT="${PLATFORM_ROOT:-}" python3 -m core.internal.shared.node_resolver resolve --node "${NODE_NAME}")"; then
        echo "[IMP:10][validate-node-input] FATAL: cannot resolve node.yaml for node=${NODE_NAME}" >&2
        exit 1
    fi
fi
if [[ ! -f "${NODE_YAML}" ]]; then
    echo "[IMP:10][validate-node-input] FATAL: node.yaml not found: ${NODE_YAML}" >&2
    exit 1
fi

echo "[IMP:8][validate-node-input] Validating input contract (0 remote): node=${NODE_NAME:-auto} node_yaml=${NODE_YAML}"
if $DRY_RUN; then
    echo "[IMP:8][validate-node-input][dry-run] Would run: python3 -m core.internal.bootstrap.preflight --scope input --node-yaml ${NODE_YAML} ${NODE_NAME:+--node-name ${NODE_NAME}}"
    exit 0
fi
# PYTHONPATH: репозиторий доступен как core.internal (dev-машина/оператор); preflight печатает
# JSON на stdout, причины fatal — в stderr (logging).
exec python3 -m core.internal.bootstrap.preflight --scope input --node-yaml "${NODE_YAML}" ${NODE_NAME:+--node-name "${NODE_NAME}"}
