#!/usr/bin/env bash
# GREP_SUMMARY: scripts-audit thin-facade python3 -m scripts_audit shebang-registration
# STRUCTURE: ▶ resolve PLATFORM_ROOT → exec python3 -m core.internal.scripts.scripts_audit → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Тонкий фасад (DevPlan 118 E6): вся бизнес-логика аудита регистрации shebang-скриптов
##           (yaml-парсер entrypoint-manifest, exception fnmatch, отчёт) — в
##           core/internal/scripts/scripts_audit.py. Этот файл — self-exception для аудита.
## @scope    Вызывается из make scripts-audit (makefiles/ci.mk:320) и pre-commit hook
##           (.pre-commit-config.yaml:271 scripts-audit).
## @io       stdin/stdout passthrough → exit 0 (clean) | exit 1 (violations)
## @invariants
##   - <10 LOC thin facade — язык политика: shell не содержит бизнес-логики
##   - EXCEPTIONS паттерны живут в Python (scripts_audit.py) — единственный SoT
## @rationale Strangler E6: grep-аудит → Python yaml-парсер (устойчив к реформаттингу manifest)
## @changes  2026-08-02 | DevPlan 118 E6 — сокращён до фасада (было 97 LOC)
# endregion MODULE_CONTRACT

set -euo pipefail
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_ROOT="${PLATFORM_ROOT:-$(cd "${_SCRIPT_DIR}/../.." && pwd)}"
exec python3 -m core.internal.scripts.scripts_audit
