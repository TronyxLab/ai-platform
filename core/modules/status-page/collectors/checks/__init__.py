# GREP_SUMMARY: status-page collectors checks package http containers platform probes
# STRUCTURE: ┌checks/ subpackage┐ → {http, containers, platform} probe modules → ⎋ (marker + doc)
# region MODULE_CONTRACT
## @purpose  Probe subpackage of status-page collectors — http/containers/platform check modules
## @scope    Internal package boundary (DevPlan 170 W7-E2); names imported via collectors facade
## @invariants
##   - NO core/internal imports (cross-layer violation forbidden for modules)
## @rationale  DevPlan 170 W7-E2 — checks/ groups the three probe types (targeted decomposition).
## @changes  2026-08-15 · DevPlan 170 W7-E2 — created
# endregion MODULE_CONTRACT
