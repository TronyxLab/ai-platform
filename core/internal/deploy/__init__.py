# GREP_SUMMARY: deploy, orchestrator, channels, audit, healthcheck, history, cli
# STRUCTURE: ┌deploy/__init__┐ → exports DeployOrchestrator, DeliveryChannel, DeployAuditLogger, DeployHistory, HealthcheckPoller, orchestrator_cli
# region MODULE_CONTRACT
## @purpose  Deploy domain package — unified DeployOrchestrator facade + delivery channels + audit + healthcheck + history
## @scope    All deploy-related modules under core/internal/deploy/. Provides clean namespace for orchestration.
## @invariants
##   1. DeployOrchestrator is the single entrypoint for all deploy operations
##   2. DeliveryChannel ABC with SCPChannel and ForcedCommandChannel implementations
##   3. DeployAuditLogger — тонкий адаптер DeployOrchestrator → shared audit_logger (D1, DevPlan 116 B11 T2);
##      deploy/audit_logger.py УДАЛЁН — единый writer shared/audit_logger (audit.jsonl)
##   4. DeployHistory manages snapshot storage for rollback
##   5. HealthcheckPoller provides shared healthcheck polling
## @rationale DevPlan 089 — Unify 6+ deploy paths under single typed facade
## @changes 2026-07-30 | Created
# endregion MODULE_CONTRACT
