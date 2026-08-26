#!/usr/bin/env bash
# GREP_SUMMARY: remove-project facade python-dispatch strangler-fig
# STRUCTURE: ▶ init → ⚡ exec python3 -m core.internal.scaffold.project_remover "$@" → ⊕ exit
# region MODULE_CONTRACT
## @purpose  Thin facade → delegates to project_remover.py (Strangler-Fig, DP-092 Wave 3)
## @scope    Arg passthrough + exec python3
## @invariants  <50 LOC; zero business logic; exit code passthrough
# endregion MODULE_CONTRACT
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_ROOT="${PLATFORM_ROOT:-$(cd "${SCRIPT_DIR}/../../.." 2>/dev/null && pwd || dirname "$(dirname "$(dirname "$SCRIPT_DIR")")")}"

# AI-0076 (DevPlan 17 T7.10): PLATFORM_ROOT вычислялся, но НЕ экспортировался —
# `python3 -m core.*` падал ModuleNotFoundError вне repo root / с чистым PYTHONPATH
export PYTHONPATH="${PLATFORM_ROOT}:${PYTHONPATH:-}"

exec python3 -m core.internal.scaffold.project_remover "$@"
