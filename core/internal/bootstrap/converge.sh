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

exec "${CONVERGE_PYTHON:-python3}" "${SCRIPT_DIR}/converge.py" "$@"
