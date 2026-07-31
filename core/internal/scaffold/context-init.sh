#!/usr/bin/env bash
# GREP_SUMMARY: context-init facade python-dispatch strangler-fig
# STRUCTURE: ▶ init → ⚡ exec python3 -m core.internal.scaffold.context_initializer "$@" → ⊕ exit
# region MODULE_CONTRACT
## @purpose  Thin facade → delegates to context_initializer.py (Strangler-Fig, DP-092 Wave 2)
## @scope    Arg passthrough + exec python3
## @invariants  <50 LOC; zero business logic; exit code passthrough
# endregion MODULE_CONTRACT
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_ROOT="${PLATFORM_ROOT:-$(cd "${SCRIPT_DIR}/../../.." 2>/dev/null && pwd || dirname "$(dirname "$(dirname "$SCRIPT_DIR")")")}"

__LOG_PREFIX="context-init"
source "${PLATFORM_ROOT}/core/lib/logging.sh"

exec python3 -m core.internal.scaffold.context_initializer "$@"
