#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint check-security posture remote-cmd auto-detect-node dry-run json local-fallback
# STRUCTURE: ▶ init → ⎋ delegate to check_security_cli.py → ⎋ exit 0|1|2
# region MODULE_CONTRACT
## @purpose  Thin entrypoint for `make check-security` (DevPlan 134 L2): делегирует ВСЮ логику
##           (arg-парсинг, node auto-detect, remote SSH proxy, local fallback) в
##           core/internal/bootstrap/check_security_cli.py (DevPlan 173 W2.4).
## @scope    Called ONLY from Makefile.
## @invariants
##   - Exit codes: 0=healthy 1=warnings 2=errors (НЕ маскируются — это check, не reconcile)
## @rationale Языковая политика: 90-LOC shell (парсинг+детекция+fallback) → Python CLI; entrypoint = exec.
## @changes 2026-08-04 | DevPlan 134 W2 — Created
## @changes 2026-08-16 | DevPlan 173 W2.4 — логика извлечена в check_security_cli.py
# endregion MODULE_CONTRACT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
export PYTHONPATH="${CORE_DIR}/..:${PYTHONPATH:-}"

exec python3 -m core.internal.bootstrap.check_security_cli "$@"
