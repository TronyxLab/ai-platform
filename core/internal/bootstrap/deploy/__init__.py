# GREP_SUMMARY: deploy, package, __init__, orchestrate, ModuleDeployResult, validate-secret-charsets, pre-pull-images, batch-check-env, check-env-requires, batch-generate-sudoers, batch-orphan-reconciliation, get-module-severity, public-api
# STRUCTURE: ┌deploy package┐ → ⚡ __all__ public contract ┌orchestrate + ModuleDeployResult + 7 публичных функций┐ → ⎋ re-export из home-модулей
# region MODULE_CONTRACT
## @purpose  Package marker for core/internal/bootstrap/deploy/ — Strangler-Fig extraction
##           of deploy-modules.sh responsibilities into typed Python modules.
##           Экспортирует публичный контракт пакета через __all__ (B9 T3, U-07).
## @scope    __all__: orchestrate + ModuleDeployResult (deploy_orchestrator) + 7 публичных функций
##           (validate_secret_charsets/check_env_requires/batch_check_env/get_module_severity ←
##           secrets_validator; pre_pull_images ← docker_orchestrator; batch_generate_sudoers ←
##           sudoers_generator; batch_orphan_reconciliation ← orphan_reconciler).
## @invariants
##   - Публичный контракт пакета — через __all__ (гейт T6.1: приватные функции не используются
##     между модулями; приватные внутримодульные остаются приватными)
##   - Re-export из home-модулей — импорт по пути пакета
## @rationale DevPlan 116 B9 D5/T3: 7 приватных функций → публичные имена + экспорт через __init__.
## @changes  2026-08-01 · B9 T3 — добавлен __all__ публичный контракт
# endregion MODULE_CONTRACT

from core.internal.bootstrap.deploy.deploy_orchestrator import ModuleDeployResult, orchestrate
from core.internal.bootstrap.deploy.docker_orchestrator import pre_pull_images
from core.internal.bootstrap.deploy.orphan_reconciler import batch_orphan_reconciliation
from core.internal.bootstrap.deploy.secrets_validator import (
    batch_check_env,
    check_env_requires,
    get_module_severity,
    validate_secret_charsets,
)
from core.internal.bootstrap.deploy.sudoers_generator import batch_generate_sudoers

# Публичный контракт пакета (RUF022: case-sensitive ASCII сортировка):
# orchestration → secrets validation → docker → sudoers → orphans
__all__ = [
    "ModuleDeployResult",
    "batch_check_env",
    "batch_generate_sudoers",
    "batch_orphan_reconciliation",
    "check_env_requires",
    "get_module_severity",
    "orchestrate",
    "pre_pull_images",
    "validate_secret_charsets",
]
