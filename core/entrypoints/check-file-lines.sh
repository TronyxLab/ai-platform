#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint check-file-lines file-line-limit 500-line-warning scanning
# STRUCTURE: ▶ init → ⎋ delegate to file_lines_checker.py → ⎋ exit 0
# region MODULE_CONTRACT
## @purpose  Scan repository files for line count exceeding configurable limit (default 500) — non-blocking warning
## @scope    Called from check-suite.yaml (`bash core/entrypoints/check-file-lines.sh`, суит
##           check-file-lines; План 175 W2.1 — make-таргет check-file-lines удалён).
##           Делегирует core/internal/lint/file_lines_checker.py (DevPlan 173 W2.2 — find-скан → Python rglob).
## @invariants
##   - Always exits 0 — non-blocking by design per DevPlan 030 AC5
##   - Default max-lines is 500; overridable via --max-lines argument
## @rationale Языковая политика: find+wc-цикл (89 LOC shell) → Python; entrypoint = exec.
## @changes  2026-08-16 | DevPlan 173 W2.2 — логика извлечена в file_lines_checker.py
# endregion MODULE_CONTRACT
set -euo pipefail
_EP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec python3 "${_EP_DIR}/../internal/lint/file_lines_checker.py" "$@"
