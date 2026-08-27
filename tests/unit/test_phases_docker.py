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
from pathlib import Path
from unittest.mock import patch

import pytest

from core.internal.bootstrap.lifecycle import phases as phases_mod
from core.internal.bootstrap.lifecycle.phases import docker as docker_mod
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

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
            positional = [
                p
                for p in params
                if p not in {"env", "runner", "facts", "helpers", "sys_helpers", "val_helpers", "users_helpers"}
            ]
            assert positional == ["core_dir", "node_name", "node_yaml"], (
                f"{name} positional signature must be (core_dir, node_name, node_yaml), got {params}"
            )
            # DI-HYG (163 W-H): keyword-only DI-параметры (env/runner/facts/helpers) НЕ ломают контракт
            assert all(
                p
                in {
                    "core_dir",
                    "node_name",
                    "node_yaml",
                    "env",
                    "runner",
                    "facts",
                    "helpers",
                    "sys_helpers",
                    "val_helpers",
                    "users_helpers",
                }
                for p in params
            ), f"{name} содержит неожиданный параметр: {params}"
    logger.critical("[IMP:9][test] docker phases registry OK: %d фаз в phases/docker.py", len(_DOCKER_PHASES))


# region Tests: φ12 deploy_update — SSL provision статус (P0 2026-08-27, тот же контракт, что φ7)


def _deploy_update_ctx(tmp_path: Path) -> tuple[Path, Path]:
    """Создать tmp core_dir (с deploy-modules.sh) + node.yaml для phase_deploy_update."""
    core_dir = tmp_path / "core"
    script_dir = core_dir / "internal" / "bootstrap"
    script_dir.mkdir(parents=True)
    (script_dir / "deploy-modules.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text("projects: []\nmodules: []\n")
    return core_dir, node_yaml


# 🧪 TRAP[TEST] · 2026-08-27 · P0 · φ12: skipped_import → done_with_warnings (НЕ done)
# · Scenario: cert_orchestrator not importable + серты НЕ на диске → helpers_domains возвращает
# ·   "skipped_import" → φ12 обязана вернуть False (перевыполнится при следующем node-update).
# · Last fail: 2026-08-27 P0 на tronyx-vps (тот же маскирующий контракт в φ7/φ12)
# · Remove if: контракт статусов ssl_provision_via_orchestrator изменится
@ldd_trajectory
def test_phase_deploy_update_ssl_skipped_import_returns_false(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """φ12: ssl_provision status="skipped_import" → фаза False (done_with_warnings)."""
    caplog.set_level(logging.DEBUG)
    core_dir, node_yaml = _deploy_update_ctx(tmp_path)

    with (
        patch.object(docker_mod.helpers_subprocess, "run_subprocess"),
        patch.object(docker_mod.helpers_domains, "import_deploy_context"),
        patch.object(docker_mod.helpers_domains, "ssl_provision_via_orchestrator", return_value="skipped_import"),
        patch.object(docker_mod, "_apply_policy_script", return_value=False),
        patch.object(docker_mod, "_sweep_stale_hc_markers", return_value=0),
    ):
        result = docker_mod.phase_deploy_update(str(core_dir), "tronyx-vps", str(node_yaml))

    assert result is False, f"P0 FAIL: φ12 skipped_import обязан давать False (done_with_warnings), got {result!r}"
    messages = [r.message for r in caplog.records]
    assert any("certificates NOT provisioned" in m for m in messages), (
        "P0 FAIL: φ12 обязана логировать «certificates NOT provisioned» при skipped_import"
    )


# 🧪 TRAP[TEST] · 2026-08-27 · P0 · φ12: converged → success (серты на диске, не наказывать)
# · Scenario: orchestrate_certs недоступен, но серты уже на диске → "converged" → φ12 True.
# · Last fail: N/A (новое поведение — дисковая converged-проверка)
# · Remove if: converged-семантика изменится
@ldd_trajectory
def test_phase_deploy_update_ssl_converged_returns_true(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """φ12: ssl_provision status="converged" → фаза True (серты уже на диске)."""
    caplog.set_level(logging.DEBUG)
    core_dir, node_yaml = _deploy_update_ctx(tmp_path)

    with (
        patch.object(docker_mod.helpers_subprocess, "run_subprocess"),
        patch.object(docker_mod.helpers_domains, "import_deploy_context"),
        patch.object(docker_mod.helpers_domains, "ssl_provision_via_orchestrator", return_value="converged"),
        patch.object(docker_mod, "_apply_policy_script", return_value=False),
        patch.object(docker_mod, "_sweep_stale_hc_markers", return_value=0),
    ):
        result = docker_mod.phase_deploy_update(str(core_dir), "tronyx-vps", str(node_yaml))

    assert result is True, f"P0 FAIL: φ12 converged обязан давать True (серты на диске), got {result!r}"
    messages = [r.message for r in caplog.records]
    assert any("SSL certificates provisioned" in m for m in messages), (
        "P0 FAIL: converged-путь φ12 обязан логировать успех SSL (IMP:9)"
    )


# endregion Tests: φ12 deploy_update — SSL provision статус (P0 2026-08-27)


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
