#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint healthcheck delegation thin-wrapper
# STRUCTURE: ▶ init → ⎋ delegate to core.internal.healthcheck.modules_healthcheck → ⎋ pass-through exit
# region MODULE_CONTRACT
## @purpose  Thin delegator entrypoint for `make healthcheck`
## @scope    Called ONLY from Makefile. Delegates to core.internal.healthcheck.modules_healthcheck
## @invariants
##   - Does NOT iterate modules/ directly (cross-layer rule compliance per core/AGENTS.md)
##   - Passes through all arguments and exit code to modules_healthcheck (--help в Python CLI)
## @rationale Двух-хоповый фасад (healthcheck.sh → modules-healthcheck.sh → .py) схлопнут
##            (DevPlan 173 W1.4); оркестрация — в core.internal.healthcheck.modules_healthcheck.py.
##            Entrypoints → modules запрещён — dispatch через shared/module_interface (typed contract).
# endregion MODULE_CONTRACT
set -euo pipefail

echo "[IMP:9][entrypoint][delegate] Running all module healthchecks (via modules_healthcheck)..." >&2
exec python3 -m core.internal.healthcheck.modules_healthcheck "$@"
