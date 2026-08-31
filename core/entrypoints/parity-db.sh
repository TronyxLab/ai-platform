#!/usr/bin/env bash
# GREP_SUMMARY: parity-db, entrypoint, thin-shell-facade, parity database, python3-m, make-target, privileged-path
# STRUCTURE: ▶ set -euo pipefail → ◇ resolve SCRIPT_DIR → ◇ resolve PROJECT_ROOT → ◇ PYTHONPATH→export → ◇ exec python3 -m core.internal.deploy.parity_db "$@" → ⎋ exit_code
# region MODULE_CONTRACT
## @purpose  Thin shell facade (<30 LOC) for parity_db.py per language policy — unconditionally
##           delegates to `python3 -m core.internal.deploy.parity_db` (privileged parity-DB path,
##           DevPlan 019 TASK-6 / AC5), passing all arguments through.
## @scope    Called from Makefile (`make parity-db ACTION=<create|drop> PROJECT=<name> NODE=<node>`).
##           stdout контракт сохраняется: create → РОВНО одна DSN-строка; логи — stderr.
## @invariants
##   - Must be executable (chmod +x) — executable-bit gate
##   - All arguments passed through as-is
##   - Exit code propagated from Python (0/1/2)
##   - PYTHONPATH includes PROJECT_ROOT so `python3 -m core.internal.*` imports resolve
##     (pattern provision-llm.sh)
## @rationale Python-first: business logic lives in parity_db.py (DI CommandRunner, ssh, psql-канон);
##            shell — тонкий фасад (языковая политика root AGENTS.md §Языковая политика)
## @changes 2026-08-31 | DevPlan 019 TASK-6 — Created (AC5)
# endregion MODULE_CONTRACT

set -euo pipefail

# region parity-db-entrypoint
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export PYTHONPATH="${PROJECT_ROOT}:${PYTHONPATH:-}"

exec python3 -m core.internal.deploy.parity_db "$@"
# endregion parity-db-entrypoint
