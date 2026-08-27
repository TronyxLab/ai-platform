#!/usr/bin/env bash
# GREP_SUMMARY: converge reconciler flock facade python-delegation R1-R10 dry-run report-only reconcile
# STRUCTURE: ▶ ┌args┐ → ⚡ PYTHONPATH export (script-path канон) → ▶ exec python3 converge.py "$@" → ⎋ exit {0,1,2,3}
# region MODULE_CONTRACT
## @purpose  Thin shell facade (DevPlan 164 W3.5-1, SH→Python) — вся оркестрация в converge.py
##           (147 LOC shell → фасад <100). Прямое замещение: имя/путь/аргументы сохранены.
## @scope    Arg passthrough → PYTHONPATH export → exec python3 converge.py. Аргументы:
##           --node <name> [--dry-run] [--report-only] [--reconcile] [--units <R..>] [--help]
## @invariants
##   - Exit-коды {0,1,2} + 3 (lock-conflict) — passthrough converge.py (контракт прежнего .sh)
##   - PYTHONPATH="${CORE_DIR}/.." — script-path exec канон (TRAP[BUG] 2026-07-31: core.* imports)
##   - CONVERGE_PYTHON env (DI тестов test_project_scaffold) → python-интерпретатор (default python3)
##   - НИКАКОЙ бизнес-логики — только exec (flock/резолв node.yaml/диспатч R1-R10 — в converge.py)
## @rationale Языковая политика (root AGENTS.md): shell — тонкие фасады <100 LOC; бизнес-логика —
##            Python. flock переехал в converge.py (fcntl.flock, Rev TRAP[DECISION] 2026-07-22).
##            `exec` сохраняет exit-код процесса (0/1/2/3) без обёртки.
## @changes 2026-08-14 | DevPlan 164 W3.5-1 — 147 LOC shell-оркестрация → фасад (converge.py создан)
## @links   core/internal/bootstrap/converge.py (оркестратор), converge/reconciler.py (R1-R10),
##          core/internal/reconciler_projects.py (--reconcile), tests/unit/test_converge.py
# endregion MODULE_CONTRACT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# ⚠️ TRAP[BUG] · 2026-07-31 · P1 · ModuleNotFoundError: 'core' при script-path exec (мигрирован из
# · прежнего converge.sh) — любой фасад, запускающий Python с core.* импортами, ОБЯЗАН
# · экспортировать PYTHONPATH="${ROOT}:${PYTHONPATH:-}".
export PYTHONPATH="${CORE_DIR}/..:${PYTHONPATH:-}"

# ⚠️ TRAP[BUG] · 2026-08-27 · P1 · F-015-класс на ноде: R7/R9 compose-introspection
# (root-compose include nginx ${NGINX_OVERLAY_DIR:?required}) падала на чистом ssh-env —
# disabled-flow/R9-fallback слепы. Self-env по канону self-sufficiency (F-023 план 012):
# secrets.env + NODE_NAME auto-detect (node_detect) + NGINX_OVERLAY_DIR default.
# Fake-путь НЕ создаётся: export только при непустом NODE_NAME; явный не перезаписывается.
_CONVERGE_SECRETS_ENV="${SECRETS_ENV_FILE:-/var/lib/platform/run/secrets.env}"
if [[ -f "$_CONVERGE_SECRETS_ENV" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$_CONVERGE_SECRETS_ENV"
    set +a
    echo "[IMP:8][converge][self-env] Sourced ${_CONVERGE_SECRETS_ENV}" >&2
fi
if [[ -z "${NODE_NAME:-}" ]] && _CONV_NODE="$(python3 -m core.internal.shared.node_detect --detect-node-name 2>/dev/null)"; then
    export NODE_NAME="$_CONV_NODE"
    echo "[IMP:9][converge][self-env] Auto-detected NODE_NAME=${NODE_NAME}" >&2
fi
if [[ -n "${NODE_NAME:-}" ]]; then
    export NGINX_OVERLAY_DIR="${NGINX_OVERLAY_DIR:-/opt/node-configs/${NODE_NAME}/overlays/nginx}"
    echo "[IMP:8][converge][self-env] NGINX_OVERLAY_DIR=${NGINX_OVERLAY_DIR}" >&2
fi

exec "${CONVERGE_PYTHON:-python3}" "${SCRIPT_DIR}/converge.py" "$@"
