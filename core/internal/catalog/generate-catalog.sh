#!/usr/bin/env bash
# Facade — delegates to Python core (generate_catalog.py)
# GREP_SUMMARY: generate-catalog, catalog.json, project-registry, ai-platform-yaml, index
# STRUCTURE: ┌bash facade┐ → exec python3 generate_catalog.py "$@" → catalog.json
# region MODULE_CONTRACT
## @purpose  Shell facade — delegates catalog generation to Python core generate_catalog.py
## @scope    Called from deploy pipeline to regenerate catalog.json after project deployment
## @invariants
##   - Thin facade only — all logic in generate_catalog.py
##   - Must pass-through all arguments to Python script
##   - Must exit with same exit code as Python script
## @rationale Consistent entrypoint pattern: shell facade → Python core. Enables calling from
##            Makefile without direct python3 knowledge.
# endregion MODULE_CONTRACT
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/generate_catalog.py" "$@"
