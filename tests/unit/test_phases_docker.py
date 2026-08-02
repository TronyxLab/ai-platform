#!/usr/bin/env python3
"""
# GREP_SUMMARY: test-phases-docker, phases-domain, registry-update, registry-auth, deploy-services, deploy-update, E3, R5, unit-tests
# STRUCTURE: ▶ test_docker_phases_registry ┌domain module docker.py┐ → ◇ 4 docker-фазы определены → ⎋ PASS │ ▶ test_phases_domain_imports_negative ┌агрегатор phases/__init__┐ → ◇ все 14 фаз re-export → ◇ helpers re-export → ⎋ PASS
# region MODULE_CONTRACT
## @purpose  Unit tests for DevPlan 119 E3 ($TEST_SPEC): test_docker_phases_registry (docker-фазы
##           в registry) + R5 test_phases_domain_imports_negative (все фазы в registry).
## @scope    Проверяет доменный сплит phases.py (1080 LOC) → phases/{system,docker,secrets,certs}.
##           Агрегатор phases/__init__.py re-export'ит все 14 фаз (state_machine контракт).
## @invariants
##   - Native imports (без subprocess)
##   - Все 14 phase_* доступны через агрегатор (AC-E3.1/AC-E3.4)
##   - Docker-домен: registry_auth, deploy_services, registry_update, deploy_update
##   - R5: negative — доменные модули существуют, функции НЕ дублируются в агрегаторе
## @rationale  E3 (AUDIT-2 M3): phases.py → доменные модули (паттерн lifecycle/helpers). Тест
##             фиксирует регистрацию всех фаз в агрегаторе (state_machine import contract).
## @changes  2026-08-02 · Created (DevPlan 119 E3)
# endregion MODULE_CONTRACT
"""

import logging

import pytest

from core.internal.bootstrap.lifecycle import phases as phases_mod
from core.internal.bootstrap.lifecycle.phases import docker as docker_mod
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# Все 14 фаз (публичный контракт state_machine)
_ALL_PHASES = [
    "phase_system_bootstrap",
    "phase_user_accounts",
    "phase_platform_setup",
    "phase_secrets_provision",
    "phase_node_configuration",
    "phase_registry_auth",
    "phase_certificates",
    "phase_deploy_services",
    "phase_converge_services",
    "phase_secrets_update",
    "phase_node_config_update",
    "phase_registry_update",
    "phase_deploy_update",
    "phase_converge_update",
]

# Docker-домен (φ6/φ8/φ11/φ12)
_DOCKER_PHASES = [
    "phase_registry_auth",
    "phase_deploy_services",
    "phase_registry_update",
    "phase_deploy_update",
]


# 🧪 TRAP[TEST] · 2026-08-02 · unit · E3 docker-фазы в registry
# · Regression: DevPlan 119 E3 — phases.py → phases/docker.py (φ6/φ8/φ11/φ12)
# · Last fail: N/A (new domain split)
# · Remove if: domain mapping changes
@ldd_trajectory
def test_docker_phases_registry(caplog: pytest.LogCaptureFixture) -> None:
    """Docker-домен: 4 фазы определены в phases/docker.py (registry + deploy)."""
    with caplog.at_level(logging.INFO):
        for name in _DOCKER_PHASES:
            assert hasattr(docker_mod, name), f"phases/docker.py must define {name}"
            fn = getattr(docker_mod, name)
            import inspect

            params = list(inspect.signature(fn).parameters)
            assert params == ["core_dir", "node_name", "node_yaml"], (
                f"{name} signature must be (core_dir, node_name, node_yaml), got {params}"
            )
    logger.critical("[IMP:9][test] docker phases registry OK: %d фаз в phases/docker.py", len(_DOCKER_PHASES))


# 🧪 TRAP[TEST] · 2026-08-02 · R5 · E3 все фазы в registry (агрегатор)
# · Regression: DevPlan 119 E3 — phases.py конвертирован в пакет; state_machine импортирует
#   from core.internal.bootstrap.lifecycle.phases import phase_* — контракт должен сохраниться
# · Last fail: N/A (new domain split); R5 negative: если агрегатор потеряет re-export, state_machine сломается
# · Remove if: phase registry contract changes
@ldd_trajectory
def test_phases_domain_imports_negative(caplog: pytest.LogCaptureFixture) -> None:
    """R5 (E3): все 14 фаз доступны через агрегатор phases/__init__.py (state_machine contract)."""
    with caplog.at_level(logging.INFO):
        for name in _ALL_PHASES:
            assert hasattr(phases_mod, name), (
                f"Aggregator phases/__init__ must re-export {name} (state_machine contract)"
            )
        # Helpers re-export (монолит экспонировал helpers_* как module-атрибуты)
        for helper in ("helpers_domains", "helpers_system", "helpers_subprocess", "helpers_reporting"):
            assert hasattr(phases_mod, helper), f"Aggregator must re-export {helper} (backward-compat)"
    logger.critical("[IMP:9][test] phases domain imports OK: %d фаз через агрегатор", len(_ALL_PHASES))
