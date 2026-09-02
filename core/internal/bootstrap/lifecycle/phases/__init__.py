#!/usr/bin/env python3
# GREP_SUMMARY: phases, bootstrap-phase, lifecycle, phase-aggregator, E3, system, docker, secrets, certs, ENV_AWARE_PHASES, FACTS_AWARE_PHASES, capability-sets
# STRUCTURE: ▶ phases package (DevPlan 119 E3) → ┌__init__.py агрегатор┐ → ◇ re-export 14 phase_* from system/docker/secrets/certs → ◇ capability-сеты (ENV/FACTS-aware) → ⎋ state_machine imports unchanged
# region MODULE_CONTRACT
## @purpose  14 standalone phase functions (DevPlan 119 E3) — thin aggregator над доменными
##           модулями lifecycle/phases/{system,docker,secrets,certs}.py. Сохраняет публичный
##           контракт: from core.internal.bootstrap.lifecycle.phases import phase_* (state_machine).
##           С plan 170 W5-C3 — также capability-сеты ENV_AWARE_PHASES/FACTS_AWARE_PHASES
##           (какие фазы принимают env=/facts= DI-параметры) — рядом с сигнатурами фаз.
## @scope    Called by node-lifecycle.sh external orchestration (shell → python3 lifecycle/cli.py)
##           or by higher-level lifecycle orchestrators. Each phase accepts unified signature:
##           core_dir, node_name, node_yaml. Phases return True on success, False on non-fatal
##           failure, raise PlatformFatalError on critical failure.
## @invariants
##   1. Every phase is idempotent — safe to re-run on a provisioned node.
##   2. Non-fatal failures log WARN and return False — do NOT raise.
##   3. Fatal failures (missing node.yaml, decrypt failure, root required) raise PlatformFatalError.
##   4. Агрегатор НЕ содержит бизнес-логики — только re-export (AC-E3.1).
##   5. Доменные модули: system.py (φ1/φ2/φ3/φ5/φ8.5/φ10/φ13), docker.py (φ6/φ8/φ11/φ12),
##      secrets.py (φ4/φ9), certs.py (φ7 + _install_acme).
##   6. ENV_AWARE_PHASES/FACTS_AWARE_PHASES — ПУБЛИЧНЫЕ имена (гейт no_private_cross_module_imports,
##      allowlist пуст): state_machine импортирует их с приватным алиасом
##      (from ...phases import ENV_AWARE_PHASES as _ENV_AWARE_PHASES — легитимный паттерн).
##   7. Capability-сеты используют строковые значения BootstrapPhase (НЕ импортируют
##      state_machine — phases → state_machine создал бы цикл; значения — литералы-константы)
## @rationale DevPlan 119 E3 (AUDIT-2 M3): phases.py 1080 LOC → доменные модули (паттерн
##           lifecycle/helpers). Агрегатор сохраняет API для state_machine и тестов.
##           W5-C3 (план 170): capability-сеты жили в state_machine.py:257-276 — переезжают
##           сюда (рядом с сигнатурами фаз — фактический источник правды о DI-параметрах);
##           state_machine импортирует (research-A §4, wave-brief W5 C3).
## @changes  2026-08-02 · DevPlan 119 E3 — phases.py конвертирован в phases/ пакет
## @changes  2026-08-15 · план 170 W5-C3 — +ENV_AWARE_PHASES/FACTS_AWARE_PHASES (из state_machine)
# endregion MODULE_CONTRACT

# ── Backward-compat: helpers re-export (монолит phases.py экспонировал helpers_* как
#    module-атрибуты; тесты monkeypatch-ят phases_mod.helpers_domains и т.п.) ──
# DevPlan 119 E3: агрегатор сохраняет публичную поверхность монолита (AC-E3.1).
from core.internal.bootstrap.lifecycle.helpers import domains as helpers_domains
from core.internal.bootstrap.lifecycle.helpers import reporting as helpers_reporting
from core.internal.bootstrap.lifecycle.helpers import secrets as helpers_secrets
from core.internal.bootstrap.lifecycle.helpers import system as helpers_system
from core.internal.bootstrap.lifecycle.helpers import users as helpers_users
from core.internal.bootstrap.lifecycle.helpers import validation as helpers_validation
from core.internal.bootstrap.lifecycle.phases.capabilities import (
    ENV_AWARE_PHASES,
    FACTS_AWARE_PHASES,
)
from core.internal.bootstrap.lifecycle.phases.certs import phase_certificates
from core.internal.bootstrap.lifecycle.phases.docker import (
    phase_deploy_services,
    phase_deploy_update,
    phase_registry_auth,
    phase_registry_update,
)
from core.internal.bootstrap.lifecycle.phases.final_verify import phase_final_verify
from core.internal.bootstrap.lifecycle.phases.secrets import phase_secrets_provision, phase_secrets_update
from core.internal.bootstrap.lifecycle.phases.system import (
    phase_converge_services,
    phase_converge_update,
    phase_node_config_update,
    phase_node_configuration,
    phase_platform_setup,
    phase_system_bootstrap,
    phase_user_accounts,
)
from core.internal.shared import (
    subprocess_io as helpers_subprocess,  # B4: единый канон (копия lifecycle/helpers удалена)
)

__all__ = [
    # _install_acme НЕ re-export'ится (приватное имя — гейт no_private_cross_module_imports;
    # используется только внутри phases/certs.py; тесты патчат phases.certs._install_acme)
    "ENV_AWARE_PHASES",
    "FACTS_AWARE_PHASES",
    "helpers_domains",
    "helpers_reporting",
    "helpers_secrets",
    "helpers_subprocess",
    "helpers_system",
    "helpers_users",
    "helpers_validation",
    "phase_certificates",
    "phase_converge_services",
    "phase_converge_update",
    "phase_deploy_services",
    "phase_deploy_update",
    "phase_final_verify",
    "phase_node_config_update",
    "phase_node_configuration",
    "phase_platform_setup",
    "phase_registry_auth",
    "phase_registry_update",
    "phase_secrets_provision",
    "phase_secrets_update",
    "phase_system_bootstrap",
    "phase_user_accounts",
]
