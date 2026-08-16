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

# Source module-interface library for typed contract cross-layer calls
source "${PATHS_LIB_DIR}/module-interface.sh"

# Canonical platform root — single source of truth for all `/opt/platform` references
# ⚠️ NOT readonly: some entrypoints (lint.sh) override PLATFORM_ROOT from PATHS_CORE_DIR/..
# for local dev where /opt/platform may not exist. Readonly guard would break those scripts.
# ⚠️ TRAP[BUG] · 2026-07-31 · P1 · PLATFORM_ROOT env silently dropped → Python resolver misses node.yaml
# · Symptom: `PLATFORM_ROOT=/x converge.sh --node N` → resolve_node_yaml (python3 -m
# ·   core.internal.shared.node_yaml --resolve) reports "node.yaml not found" even though
# ·   /x/node-configs/N/node.yaml exists.
# · Root: plain assignment `PLATFORM_ROOT="/opt/platform"` overwrote the caller's env value;
# ·   and since the var was not exported, the shell→Python CLI delegation (DP-088/091:
# ·   node-resolver.sh → NodeYaml.resolve() reads os.environ["PLATFORM_ROOT"]) never saw it.
# · Fix: honor a pre-set value, default to /opt/platform, and export so child Python
# ·   processes (NodeYaml.resolve, reconciler) resolve the same root.
# · Prevention: keep PLATFORM_ROOT exportable — tests and entrypoints override it.
export PLATFORM_ROOT="${PLATFORM_ROOT:-/opt/platform}"
