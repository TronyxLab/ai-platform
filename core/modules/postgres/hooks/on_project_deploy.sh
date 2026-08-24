#!/usr/bin/env bash
# GREP_SUMMARY: postgres deploy-hook thin-wrapper python3 on_project_deploy PROJECT_DIR PROJECT NODE_NAME post-deploy chain module-interface dispatch
# STRUCTURE: ▶ bash facade → ⚡ exec python3 on_project_deploy.py "$@" → ⎋ exit-code passthrough
# region MODULE_CONTRACT
## @purpose  Thin shell facade for the postgres on-project-deploy hook (REF-0002).
##           module_interface.dispatch runs registered hook scripts via `bash <script>`,
##           so hooks.on_project_deploy must point at a shell entrypoint — a .py cannot
##           be executed by bash directly. ALL business logic lives in
##           on_project_deploy.py (language policy: new code = Python only).
## @scope    Invoked by DeployOrchestrator post-deploy chain via
##           shared/module_interface.invoke("postgres", "deploy-hook", PROJECT_DIR,
##           PROJECT, NODE_NAME); args passed through 1:1.
## @invariants
##   - Pure passthrough: exec preserves the Python exit code (0 ok/skip, 1 fatal)
##   - No business logic here (Strangler canon: shell facade <150 LOC, 0 inline python3)
##   - Path resolved from BASH_SOURCE — location-independent invocation
# endregion MODULE_CONTRACT

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/on_project_deploy.py" "$@"
