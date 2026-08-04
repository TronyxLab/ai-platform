#!/usr/bin/env bash
# GREP_SUMMARY: compose-wrapper, preflight, secrets-validation, docker-compose, safe-up
# STRUCTURE: ▶ parse COMPOSE_PREFLIGHT_DEBUG → ◇ python3 compose_preflight.py "$@" → ◇ exit 1 if blocked → exec docker compose "$@"
# region MODULE_CONTRACT
## @purpose  Docker compose wrapper that runs preflight secret validation before `up`.
##           Called from `make compose-safe-up MODULES=<list>` or directly.
## @scope    Pre-flight check: validates required secrets exist for target modules before `docker compose up`.
## @invariants
##   - Delegates to `python3 compose_preflight.py` for all validation logic
##   - Passes through ERRORLEVEL from preflight: 0 → exec compose, non-zero → block
##   - Does NOT modify or intercept compose arguments — only prepends validation
##   --skip-preflight arg passthrough: if preflight passes --skip-preflight, wrapper skips validation
## @rationale Shell facade (<30 LOC) follows Python-first policy: logic in Python, thin shell wrapper.
## @changes 2026-07-22 | Initial — TASK-4 Plan 049
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFLIGHT_PY="${SCRIPT_DIR}/../internal/bootstrap/deploy/compose_preflight.py"

# Canonical PYTHONPATH export (pattern: context-promote.sh) — compose_preflight.py
# imports core.internal.* (deploy_paths/secrets_env_parser), standalone run needs
# repo root on sys.path. Pre-existing bug: missing since 119da0f (core.* imports).
export PYTHONPATH="${SCRIPT_DIR}/../..:${PYTHONPATH:-}"

# Export debug flag if set
if [ -n "${COMPOSE_PREFLIGHT_DEBUG:-}" ]; then
    export COMPOSE_PREFLIGHT_DEBUG
fi

# ── 1. Run preflight (exit 0 = allow, exit 1 = block) ───────────────────────
# [IMP:7][compose-wrapper] Running preflight secret validation
if python3 "${PREFLIGHT_PY}" "$@"; then
    echo "[IMP:9][compose-wrapper] Preflight passed — proceeding to docker compose" >&2
else
    echo "[IMP:9][compose-wrapper] PREFLIGHT BLOCKED — missing or invalid secrets" >&2
    exit 1
fi

# ── 2. Pass through to docker compose ──────────────────────────────────────
# [IMP:7][compose-wrapper] Exec: docker compose "$@"
exec docker compose "$@"
