#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint converge reconcile remote-cmd auto-detect-node dry-run ssh-proxy
# STRUCTURE: ▶ init ┌parse --node --dry-run --reconcile┐ → ◇ --node? → ┌python3 -m node_detect --detect-node-name┐ → ⚡ execute_remote_converge() → ◇ RC=2? → └─ exec local converge.sh ─┘ → ⎋ exit 0|1|2
# region MODULE_CONTRACT
## @purpose  Thin entrypoint for `make converge`: parses --node/--dry-run/--reconcile, delegates
##           to execute_remote_converge() in remote-cmd.sh for SSH proxy, or falls back
##           to local exec of core/internal/bootstrap/converge.sh when no SSH host.
## @scope    Called ONLY from Makefile.
##           Owns: usage, main.
## @invariants
##   - --node is recommended; if missing → python3 -m core.internal.shared.node_detect fallback
##   - --dry-run prints SSH command or local args without executing
##   - --reconcile: passthrough flag — after converge, reconcile stub projects (W4)
##   - SSH proxy logic lives entirely in remote-cmd.sh (execute_remote_converge)
##   - Without SSH_HOST: local exec (backward compatible)
##   - No AGE key handling (converge R-units don't decrypt secrets)
## @rationale Thin-wrapper per canonical operations table (core/AGENTS.md).
##            Mirrors node-update.sh pattern for SSH proxy dispatch (DevPlan 020).
## @changes 2026-07-21 | +--reconcile flag (DevPlan 025 W4)
# endregion MODULE_CONTRACT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${CORE_DIR}/lib/paths.sh"
source "${CORE_DIR}/lib/node-resolver.sh"   # 142 B28b: resolve_node_yaml/extract_node_host (rc=2 различение)
source "${CORE_DIR}/internal/bootstrap/remote-cmd.sh"
source "${CORE_DIR}/lib/args.sh"

NODE_NAME=""
DRY_RUN=false
PASSTHROUGH_ARGS=()

USAGE_SCRIPT="converge.sh"
USAGE_DESC="Idempotent desired-state reconciler for platform VPS."
USAGE_OPTIONS=(
    "--node <name>              Node name to reconcile (or auto-detect)"
    "--dry-run                  Print planned mutations without executing"
    "--report-only              Check-only JSON drift report (passthrough)"
    "--reconcile                After converge, reconcile stub projects"
)

# 🧐 TRAP[DECISION] · 2026-07-21 · — · converge.sh passthrough arg pattern
# · Rejected: full parse_args adoption (passthrough pattern incompatible)
# · Reason: minimal W1 scope, forwards unknown args via PASSTHROUGH_ARGS
# · Rev: Wave 4 — redesign passthrough into parse_args spec

# ═══════════════════════════════════════════════════════════════════
# region FUNC_main
## @purpose  Parse CLI args, auto-detect node, delegate to SSH proxy or local exec
main() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --node|--node-name) NODE_NAME="$2"; shift 2 ;;
            --dry-run) DRY_RUN=true; shift ;;
            --reconcile) PASSTHROUGH_ARGS+=("--reconcile"); shift ;;
            --help|-h) usage "$USAGE_SCRIPT" "${USAGE_DESC:-}" "${USAGE_OPTIONS[@]:-}" ;;
            *) PASSTHROUGH_ARGS+=("$1"); shift ;;
        esac
    done

    # ── Auto-detect if --node not provided ──
    if [[ -z "${NODE_NAME}" ]]; then
        echo "[IMP:9][converge][entrypoint] --node not provided — attempting auto-detect" >&2
        NODE_NAME="$(python3 -m core.internal.shared.node_detect --detect-node-name 2>/dev/null)" || {
            echo "[IMP:10][converge][entrypoint] FATAL: --node is required" >&2
            echo "  Usage: converge.sh --node <name> [--dry-run]" >&2
            exit 1
        }
        echo "[IMP:9][converge][entrypoint] Auto-detected NODE=${NODE_NAME}" >&2
    fi

    echo "[IMP:9][converge][entrypoint] Starting converge for NODE=${NODE_NAME}" >&2

    # ── SSH proxy (preferred) ──
    # ⚠️ TRAP[BUG] · 2026-08-03 · P0 · set -e убивал скрипт на rc=2 (локальный fallback-сигнал)
    # · Symptom: execute_remote_converge возвращает 2 (self-detect: мы на VPS) → set -euo pipefail
    # ·   завершал entrypoint ДО локального fallback (строки 79-95) → reconcile не выполнялся.
    # · Root: plain-вызов без || в set -e контексте (тот же класс, что node-update.sh:89-90 TRAP 2026-07-23).
    # · Fix: идиома `local rc=0; cmd || rc=$?` — захват non-zero без триггера set -e (копия node-update.sh:89-90).
    # · Prevention: remote-прокси-вызовы в entrypoints всегда через || rc=$? при set -e.
    # ⚠️ TRAP[BUG] · 2026-08-07 · P1 · 142 B28b: rc=2 от REMOTE converge (R-units errors) ложно
    # ·   трактовался как «self-detect/no SSH host» → двойной ЛОКАЛЬНЫЙ прогон на dev-машине
    # ·   (R3 mkdir /opt Permission denied, R6 vhost overlay not resolved — артефакты macOS).
    # · Root: execute-converge не имеет self-SSH detect (только execute-update); rc=2 сквозь ssh —
    # ·   это errors converge на ноде. Различение: host из node.yaml ДО вызова — host есть → rc=2
    # ·   = ошибки ноды (exit 2, БЕЗ локального прогона); host пуст → rc=2 = no-SSH-host (fallback).
    local ssh_host=""
    local resolved_yaml=""
    resolved_yaml="$(resolve_node_yaml "${NODE_NAME}" 2>/dev/null)" && ssh_host="$(extract_node_host "${resolved_yaml}" 2>/dev/null)" || ssh_host=""
    local remote_rc=0
    execute_remote_converge "${NODE_NAME}" "${PASSTHROUGH_ARGS[@]}" || remote_rc=$?

    # ── Local exec fallback (no SSH host) ──
    if [[ $remote_rc -eq 2 ]]; then
        if [[ -n "${ssh_host}" ]]; then
            echo "[IMP:8][converge][entrypoint] Remote converge on ${ssh_host} returned rc=2 (R-unit errors) — forwarding, NO local fallback" >&2
            exit 2
        fi
        echo "[IMP:9][converge][entrypoint] No SSH host — executing converge.sh LOCALLY" >&2
        local internal="${PATHS_INTERNAL_DIR}/bootstrap/converge.sh"
        if [[ ! -f "$internal" ]]; then
            echo "[IMP:10][converge][entrypoint] FATAL: Internal script not found at ${internal}" >&2
            exit 1
        fi
        local args=("--node" "${NODE_NAME}")
        $DRY_RUN && args+=("--dry-run")
        args+=("${PASSTHROUGH_ARGS[@]}")
        # ⚠️ TRAP[BUG] · 2026-07-23 · P0 · converge.sh --dry-run exited before delegating
        # ·   Root: refactoring added early exit (exit 0) on dry-run instead of delegating
        # ·   to internal/bootstrap/converge.sh. Tests expect WOULD-create output from
        # ·   the actual converge logic, not just a command preview.
        # ·   Fix: always delegate to internal script; it handles --dry-run itself.
        echo "[IMP:8][converge][entrypoint] Delegating to ${internal}" >&2
        exec bash "${internal}" "${args[@]}"
    fi
    # ⚠️ TRAP[BUG] · 2026-08-03 · P1 · неявный rc=1 от [[ ]] вместо проброса remote_rc (RC 121 e2e)
    # · Symptom: remote converge с warnings (rc=1) — entrypoint возвращал rc=1 (make → 2), а при
    #   remote_rc=0 — возвращал 1 (ложный fail). Теперь: явный проброс 0/1/2.
    exit $remote_rc
}
# endregion FUNC_main

main "$@"
