#!/usr/bin/env bash
# GREP_SUMMARY: deploy-context, entrypoint, thin-wrapper, context-deployer, standalone, make-deploy-context
# STRUCTURE: ▶ init → ⎋ delegate to deploy_context_cli.py → ⎋ exit
# region MODULE_CONTRACT
## @purpose  Thin shell facade for `make deploy-context NODE=<n>`.
##           Делегирует ВСЮ логику (arg-парсинг, remote/local детекция, node.yaml резолв)
##           в core/internal/bootstrap/deploy/deploy_context_cli.py (DevPlan 173 W2.1).
## @scope    Called from Makefile deploy-context target (makefiles/bootstrap.mk).
## @invariants
##   - Thin wrapper: exec python3, 0 бизнес-логики
##   - Exit code = exit code deploy_context_cli.py (0 = success, 1/2 = errors)
## @rationale Языковая политика: 79-LOC shell (arg-парсинг + remote/local + резолв) → Python CLI.
## @changes  2026-07-22 | DevPlan 047 Phase 4 — Created standalone deploy-context entrypoint
## @changes  2026-08-16 | DevPlan 173 W2.1 — логика извлечена в deploy_context_cli.py
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_ROOT="${PLATFORM_ROOT:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
export PLATFORM_ROOT
export PYTHONPATH="${PLATFORM_ROOT}:${PYTHONPATH:-}"

exec python3 -m core.internal.bootstrap.deploy.deploy_context_cli "$@"
