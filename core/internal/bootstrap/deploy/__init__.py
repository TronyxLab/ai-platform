# GREP_SUMMARY: deploy, package, __init__, docker-orchestrator, sudoers-generator, context-overlay, secrets-validator, orphan-reconciler
# STRUCTURE: ┌deploy package┐ → ┌docker_orchestrator┐ · ┌sudoers_generator┐ · ┌context_overlay┐ · ┌secrets_validator┐ · ┌orphan_reconciler┐
# region MODULE_CONTRACT
## @purpose  Package marker for core/internal/bootstrap/deploy/ — Strangler-Fig extraction
##           of deploy-modules.sh responsibilities into typed Python modules.
## @scope    Imports will be exposed via __all__ as modules are created.
## @invariants  Empty __init__.py is valid — modules are imported by path, not package qualifier.
## @rationale  Strangler-Fig decomposition: each module extracts one responsibility from deploy-modules.sh shell monolith.
# endregion MODULE_CONTRACT
