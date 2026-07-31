#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint context-promote platform context org git-mirror push ssh fallback facade python
# STRUCTURE: ▶ validate CONTEXT=$1 → export PYTHONPATH → exec python3 -m core.internal.deploy.context_promoter → ⎋ exit code passthrough
# region MODULE_CONTRACT
## @purpose  Thin shell facade for `make context-promote`: validate CONTEXT and delegate to
##           core/internal/deploy/context_promoter.py (Strangler-Fig migration, DevPlan 103)
## @scope    Called ONLY from Makefile (makefiles/deploy.mk:95). All business logic lives in Python.
## @invariants
##   - CONTEXT positional arg ($1) required — Makefile already passes it (deploy.mk:95)
##   - PYTHONPATH exported by the facade itself (paths.sh does NOT set it — add-vhost.sh TRAP[BUG])
##   - exec replaces the shell process → Python exit code becomes the entrypoint exit code
##   - Audit (START/DONE/FAIL) written by Python via shared/audit_logger — audit.sh NOT sourced (D3)
##   - GIT_MIRROR_TOKEN inherited via env — never passed as an argument
## @rationale Strangler-Fig: 161→~25 LOC (−85%). Language policy: new logic only in Python (AGENTS.md).
## @changes 2026-07-31 | DevPlan 103 — rewritten as thin facade (was 161 LOC)
# endregion MODULE_CONTRACT
set -euo pipefail

_EP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_EP_DIR}/../lib/paths.sh"
export PYTHONPATH="${_EP_DIR}/../..:${PYTHONPATH:-}"

# region CONTEXT_VALIDATION
## @purpose Validate CONTEXT positional argument — must resolve to a GitHub org name
CONTEXT="${1:-}"
if [[ -z "$CONTEXT" ]]; then
    echo "[IMP:10][context-promote] ERROR: CONTEXT required — usage: make context-promote CONTEXT=<context>" >&2
    exit 1
fi
# endregion CONTEXT_VALIDATION

exec python3 -m core.internal.deploy.context_promoter "$CONTEXT"
