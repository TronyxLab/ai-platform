#!/usr/bin/env bash
# GREP_SUMMARY: paths, script-dir, core-dir, modules-dir, path-resolution, platform-paths
# STRUCTURE: ▶ resolve lib_dir → export PATHS_LIB_DIR PATHS_CORE_DIR PATHS_MODULES_DIR
# region MODULE_CONTRACT
## @purpose  Canonical path constants for the core/ directory tree.
##           Single source of truth for path resolution across all shell scripts.
## @scope    Provides PATHS_LIB_DIR, PATHS_CORE_DIR, PATHS_MODULES_DIR, PATHS_TEMPLATES_DIR,
##           PATHS_INTERNAL_DIR — used by bootstrap, deploy, and orchestration scripts
## @invariants
##   - This file MUST NOT source any other library (no circular dependencies)
##   - Variables are declared readonly — guard prevents re-sourcing collision
##   - Paths are resolved at source time, not call time
## @rationale Eliminates duplicated SCRIPT_DIR/../../patterns across 20+ shell scripts.
##            Centralized path resolution reduces errors from inconsistent relative paths.
##            Readonly guard ensures idempotent sourcing — second source is a no-op.
# endregion MODULE_CONTRACT

echo "[IMP:7][paths][lib] Loading paths library" >&2

# Guard against re-sourcing (readonly-collision pattern)
if [[ -n "${PATHS_LIB_DIR:-}" ]]; then
    return 0
fi
readonly PATHS_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PATHS_CORE_DIR="${PATHS_LIB_DIR}/.."
readonly PATHS_MODULES_DIR="${PATHS_CORE_DIR}/modules"
readonly PATHS_TEMPLATES_DIR="${PATHS_CORE_DIR}/templates"
readonly PATHS_INTERNAL_DIR="${PATHS_CORE_DIR}/internal"

# Canonical platform root — single source of truth for all `/opt/platform` references
# ⚠️ NOT readonly: some entrypoints (lint.sh) override PLATFORM_ROOT from PATHS_CORE_DIR/..
# for local dev where /opt/platform may not exist. Readonly guard would break those scripts.
PLATFORM_ROOT="/opt/platform"
