#!/usr/bin/env bash
# GREP_SUMMARY: validate yaml json-schema ajv python-jsonschema pre-commit exit1
# STRUCTURE: ▶ init → ◇ exec python3 orchestrator (вся оркестрация в Python, DevPlan 107) → ⎋ exit code passthrough
# region MODULE_CONTRACT
## @purpose  Тонкий shell-фасад (≤50 LOC) над validate_orchestrator.py — вся оркестрация
##           (discovery, schema-routing, validator selection, error aggregation) в Python.
##           Бывший 251-LOC скрипт мигрирован (DevPlan 107 Strangler-завершение области validate).
## @scope    Вызывается core/entrypoints/validate.sh (18 LOC, не меняется) для make validate / make lint.
##           НЕ source'ит logging.sh/python_deps.sh — вся логика в Python (AGENTS.md §Языковая политика).
## @invariants
##   - Все аргументы передаются в orchestrator без изменений
##   - exit code = exit code Python-процесса (0=ok, 1=validation errors)
##   - stderr-формат [IMP:N][validate][block] msg байт-идентичен (генерируется emit() в Python)
##   - НЕ содержит inline python3 heredoc/-c (AC9: DevPlan 093 AC3 не регрессирует)
## @rationale Фасад = только exec по абсолютному пути скрипта (DD3: устойчив к cwd ≠ repo root);
##   subprocess-вызовы внутри orchestrator'а получают cwd=REPO_ROOT явно.
## ⚠️ TRAP[DECISION] — полный контекст перенесён в validate_orchestrator.py MODULE_CONTRACT:
##   · Single manifest format (ai-platform.yaml only, AD-2) — 2026-07-01
##   · Port conflict check via --check-ports — 2026-07-01
##   · os.walk+sorted discovery вместо find|sort -z (D3-фикс) — 2026-07-31
##   · subprocess вместо native import CLI (DD2) — 2026-07-31
##   · exec по абсолютному пути (DD3) — 2026-07-31
## @changes 2026-07-31 | DevPlan 107 T3: 251 → 24 LOC, оркестрация → validate_orchestrator.py
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec python3 "${SCRIPT_DIR}/validate_orchestrator.py" "$@"
