# GREP_SUMMARY: monitoring package-init generators constants
# STRUCTURE: ┌package-init┐ → ◇ constants → ◇ 7 generator modules → ⎋ lazy consumers
# region MODULE_CONTRACT
## @purpose  Package init for core.internal.monitoring — post-deploy monitoring generators
##            (DevPlan 117 G T54). Each generator extracted from monitoring_config_renderer.py.
## @scope    Consumed by monitoring_config_renderer.py (lazy import inside main/function bodies).
## @invariants
##   - Generators are imported LAZILY by monitoring_config_renderer (AC-G5, start-up time unchanged)
##   - No cross-generator imports (each generator is self-contained)
##   - Shared constants live in monitoring/constants.py (R5)
## @rationale  DevPlan 117 G T54 — split of monitoring_config_renderer.py by generator domain.
## @changes  2026-08-01 · DevPlan 117 G T54 — created
# endregion MODULE_CONTRACT
