#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint healthcheck delegation thin-wrapper NODE-guard F-016 remote-hint
# STRUCTURE: ▶ NODE-guard (F-016: NODE≠local → fail-loud hint) → ⎋ delegate to core.internal.healthcheck.modules_healthcheck → ⎋ pass-through exit
# region MODULE_CONTRACT
## @purpose  Thin delegator entrypoint for `make healthcheck`
## @scope    Called ONLY from Makefile. Delegates to core.internal.healthcheck.modules_healthcheck
## @invariants
##   - Does NOT iterate modules/ directly (cross-layer rule compliance per core/AGENTS.md)
##   - Passes through all arguments and exit code to modules_healthcheck (--help в Python CLI)
##   - plan 012 T16 (F-016): NODE=<n> с операторской машины НЕ молча проверяет ЛОКАЛЬНЫЙ docker —
##     fail-loud с подсказкой (remote-здоровье ноды — через `make e2e-verify NODE=<n>` / ssh).
##     NODE=local / пустой → прежнее поведение (локальный стек).
## @rationale Двух-хоповый фасад (healthcheck.sh → modules-healthcheck.sh → .py) схлопнут
##            (DevPlan 173 W1.4); оркестрация — в core.internal.healthcheck.modules_healthcheck.py.
##            Entrypoints → modules запрещён — dispatch через shared/module_interface (typed contract).
## @changes  2026-08-26 · plan 012 T16 (F-016) — NODE-guard: fail-loud вместо молчаливого локального прогона
# endregion MODULE_CONTRACT
set -euo pipefail

# ── plan 012 T16 (F-016): NODE-guard ─────────────────────────────────────────
# NODE — фильтр конфига для make-таргетов; healthcheck исполняется ТОЛЬКО на ноде,
# где docker-стек локальный. Операторская машина с NODE=<prod> НЕ должна молча
# проверять свой локальный docker (F-016: ложный успех).
if [[ -n "${NODE:-}" && "${NODE}" != "local" ]]; then
    echo "[IMP:10][healthcheck] ERROR: NODE=${NODE} задан, но healthcheck проверяет ЛОКАЛЬНЫЙ docker." >&2
    echo "  Для удалённой ноды используйте: make e2e-verify NODE=${NODE}  (HTTP+TLS sweep)" >&2
    echo "  или выполните healthcheck НА САМОЙ НОДЕ (удалённый вход — make-контракт ноды)." >&2
    echo "  NODE=local / без NODE → локальная проверка стека." >&2
    exit 1
fi

echo "[IMP:9][entrypoint][delegate] Running all module healthchecks (via modules_healthcheck)..." >&2
exec python3 -m core.internal.healthcheck.modules_healthcheck "$@"
