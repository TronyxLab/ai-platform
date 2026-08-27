#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint healthcheck delegation thin-wrapper NODE-guard F-016 remote-hint auto-detect NODE_NAME node_detect
# STRUCTURE: ▶ NODE-guard (F-016: NODE≠local → fail-loud hint) → ◇ auto-detect NODE_NAME (node_detect: нода = единый node-configs → export; dev/ambiguous → skip) → ⎋ delegate to core.internal.healthcheck.modules_healthcheck → ⎋ pass-through exit
# region MODULE_CONTRACT
## @purpose  Thin delegator entrypoint for `make healthcheck`
## @scope    Called ONLY from Makefile. Delegates to core.internal.healthcheck.modules_healthcheck
## @invariants
##   - Does NOT iterate modules/ directly (cross-layer rule compliance per core/AGENTS.md)
##   - Passes through all arguments and exit code to modules_healthcheck (--help в Python CLI)
##   - plan 012 T16 (F-016): NODE=<n> с операторской машины НЕ молча проверяет ЛОКАЛЬНЫЙ docker —
##     fail-loud с подсказкой (remote-здоровье ноды — через `make e2e-verify NODE=<n>` / ssh).
##     NODE=local / пустой → прежнее поведение (локальный стек).
##   - 017 Phase E (F-находка): NODE_NAME пуст + канон-детектор node_detect (CLI
##     `python3 -m core.internal.shared.node_detect --detect-node-name`, exit 0) находит РОВНО
##     один каталог <name>/node.yaml в node_configs_remote() (/opt/node-configs) → export
##     NODE_NAME=<name> ДО вызова python (enabled-фильтр ноды; иначе — ложные FAIL по модулям
##     вне node.yaml, e.g. log-collector). dev (нет /opt/node-configs) или >1 кандидатов
##     (exit 1) → NODE_NAME остаётся пустым → прежнее поведение (все модули). Явно заданный
##     NODE_NAME НЕ перезаписывается.
## @rationale Двух-хоповый фасад (healthcheck.sh → modules-healthcheck.sh → .py) схлопнут
##            (DevPlan 173 W1.4); оркестрация — в core.internal.healthcheck.modules_healthcheck.py.
##            Entrypoints → modules запрещён — dispatch через shared/module_interface (typed contract).
##            Авто-детект НЕ дублируется в shell — вызывается существующий канон
##            node_detect.auto_detect_node_name (0 inline python3; прецеденты bootstrap.sh,
##            platform-export-metrics.sh).
## @changes  2026-08-26 · plan 012 T16 (F-016) — NODE-guard: fail-loud вместо молчаливого локального прогона
## @changes  2026-08-27 · 017 Phase E (F-находка) — авто-детект NODE_NAME через
##            core.internal.shared.node_detect (нода: единый node-configs → enabled-фильтр;
##            dev/ambiguous → без изменений)
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

# ── 017 Phase E (F-находка): авто-детект NODE_NAME на нодовой инсталляции ──────
# Нода: /opt/node-configs (node_configs_remote) содержит РОВНО один каталог <name>/node.yaml
# → канон-детектор node_detect отдаёт name (exit 0) → export NODE_NAME ДО вызова python:
# modules_healthcheck._resolve_enabled_modules() фильтрует ТОЛЬКО enabled-модули этой ноды
# (иначе healthcheck на ноде без env перебирает ВСЕ модули infra — ложные FAIL «SOME MODULES
# UNHEALTHY», в т.ч. log-collector вне node.yaml). dev (нет /opt/node-configs) или >1
# кандидатов (exit 1) → NODE_NAME не задаётся → прежнее поведение (все модули).
if [[ -z "${NODE_NAME:-}" ]]; then
    if _HC_NODE="$(python3 -m core.internal.shared.node_detect --detect-node-name 2>/dev/null)"; then
        export NODE_NAME="${_HC_NODE}"
        echo "[IMP:9][healthcheck] Auto-detected NODE_NAME=${NODE_NAME} (node.yaml filter enabled)" >&2
    else
        echo "[IMP:7][healthcheck] Node auto-detection skipped (no unique node-configs) — checking all modules" >&2
    fi
fi

echo "[IMP:9][entrypoint][delegate] Running all module healthchecks (via modules_healthcheck)..." >&2
exec python3 -m core.internal.healthcheck.modules_healthcheck "$@"
