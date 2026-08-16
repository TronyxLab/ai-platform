#!/usr/bin/env bash
# GREP_SUMMARY: check-doc-headers pre-commit hook doc-validation facade python3 staged
# STRUCTURE: ┌staged files┐ → source paths.sh → cd PLATFORM_ROOT → exec python3 -m doc_header_validator → ⎋ exit 0/1
# region MODULE_CONTRACT
## @purpose  Thin facade — delegates doc-validation to core.internal.lint.doc_header_validator
## @scope    Called from .pre-commit-config.yaml (check-doc-headers hook, staged files)
## @invariants No business logic — delegation only; python3 -m (no -c/heredoc); exit passthrough
## @rationale Strangler-Fig (DevPlan 106): 236 LOC → ≤40 LOC facade; logic in Python
# endregion MODULE_CONTRACT

set -euo pipefail
echo "[IMP:7][check-doc-headers][main] Starting doc header validation" >&2
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/paths.sh"
PLATFORM_ROOT="$(cd "$PATHS_CORE_DIR/.." && pwd)"
cd "$PLATFORM_ROOT"

exec python3 -m core.internal.lint.doc_header_validator doc-headers "$@"
