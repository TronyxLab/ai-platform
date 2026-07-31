#!/usr/bin/env bash
# GREP_SUMMARY: project-list facade python-dispatch strangler-fig
# STRUCTURE: ▶ init → ⚡ exec python3 -m core.internal.scaffold.project_lister "$@" → ⊕ exit
# region MODULE_CONTRACT
## @purpose  Thin facade → delegates to project_lister.py (Strangler-Fig, DP-092 Wave 1)
## @scope    Arg passthrough + exec python3
## @invariants  <30 LOC; zero business logic; exit code passthrough
# endregion MODULE_CONTRACT
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_ROOT="${PLATFORM_ROOT:-$(cd "${SCRIPT_DIR}/../../.." 2>/dev/null && pwd || dirname "$(dirname "$(dirname "$SCRIPT_DIR")")")}"

# Map --list / --status to explicit flags for project_lister.py
exec python3 -m core.internal.scaffold.project_lister "$@"
