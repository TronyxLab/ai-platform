#!/bin/bash
# GREP_SUMMARY: provision-llm, entrypoint, thin-shell-facade, key-provisioner, LLM, virtual-keys
# STRUCTURE: ▶ set -euo pipefail → ◇ resolve SCRIPT_DIR → ◇ resolve PROJECT_ROOT → ◇ python3 key_provisioner.py "$@" → ⎋ exit_code
# region MODULE_CONTRACT
## @purpose  Thin shell facade (<30 lines) for key_provisioner.py per language policy.
##           Unconditionally delegates to Python implementation, passing all arguments through.
## @scope    Called from Makefile (provision-llm target), deploy-modules.sh, and deploy-context.
## @invariants
##   - Must be executable (chmod +x)
##   - All arguments are passed through to Python script as-is
##   - Exit code is propagated from Python script
## @rationale Python-first: new business logic lives in .py files. Shell is a thin wrapper.
## @changes — 2026-07-24 | Created (DevPlan 049 Phase 4)
# endregion MODULE_CONTRACT

set -euo pipefail

# region provision-llm-entrypoint
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

python3 "${PROJECT_ROOT}/core/internal/llm/key_provisioner.py" "$@"
# endregion provision-llm-entrypoint
