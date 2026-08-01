#!/usr/bin/env bash
# GREP_SUMMARY: postgres hook on-project-deploy thin-wrapper auto-create-db
# STRUCTURE: parse_args(PROJECT_DIR,PROJECT,NODE_NAME) → exec python3 on_project_deploy.py → ⎋ exit
# region MODULE_CONTRACT
## @purpose  Thin wrapper for postgres post-deploy hook — delegates all logic to
##           core/modules/postgres/hooks/on_project_deploy.py (Python, DevPlan 117 H D65).
## @scope    Invoked after successful project deploy via invoke_module_interface
##           deploy-hook; receives PROJECT_DIR, PROJECT, NODE_NAME as positional args.
## @invariants
##   - Business logic (value conversion, regex validation, psql parsing) in Python
##   - Python exit code is propagated (0 = ok/skip, 1 = fatal)
##   - module.yaml path unchanged (hooks/on-project-deploy.sh) — backward compatible
## @rationale Strangler-Fig: 100 LOC shell → 15 LOC wrapper + ~150 LOC Python
##            (language policy: business logic in Python, shell = thin facade)
## @changes
##   LAST_CHANGE: 2026-08-02 | Rewritten as thin wrapper (DevPlan 117 Brief H D65)
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec python3 "${SCRIPT_DIR}/on_project_deploy.py" "$@"
