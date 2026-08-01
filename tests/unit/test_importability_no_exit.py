#!/usr/bin/env python3
# GREP_SUMMARY: importability no-exit SystemExit library-modules provisioner deploy-engine context-registry scaffold shared U-29 unit
# STRUCTURE: ▶ import 15 library-модулей → ◇ SystemExit на import? → RED · ○ direct вызов provision_networks без docker → ◇ PlatformFatalError (не SystemExit) → ⎋ PASS
# region MODULE_CONTRACT
## @purpose  Importability-тест (DevPlan 116 B4 T6.2, U-29): библиотечные модули core/internal
##           ИМПОРТИРУЮТСЯ без побочных SystemExit (процесс жив), а бизнес-функции с
##           невосстановимыми ошибками RAISE PlatformError вместо sys.exit.
## @scope    Модули, где раньше sys.exit жил в библиотечных функциях (provisioner,
##           deploy_engine, context_registry, context_initializer, scaffold-модули, shared/*).
## @invariants
##   - import модуля не бросает SystemExit (нет sys.exit на module level)
##   - provision_networks без docker → PlatformFatalError с exit_code == 10 (D4)
## @rationale Гейт T6 — статический (AST); этот тест — динамический: реальный import и
##            реальный вызов функции с отсутствующим docker (mock shutil.which).
## @changes 2026-08-01 | DevPlan 116 B4 T9.2 — Created
# endregion MODULE_CONTRACT

import importlib
import logging

import pytest

from core.internal.shared.exceptions import PlatformFatalError
from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# Библиотечные модули — import не должен завершать процесс (U-29, контракт T6)
_IMPORTABLE_MODULES: tuple[str, ...] = (
    "core.internal.provisioner",
    "core.internal.deploy.deploy_engine",
    "core.internal.scaffold.context_registry",
    "core.internal.scaffold.context_initializer",
    "core.internal.scaffold.project_scaffolder",
    "core.internal.scaffold.project_lister",
    "core.internal.scaffold.project_remover",
    "core.internal.scaffold.gen_env_platform",
    "core.internal.shared.ssh_command_parser",
    "core.internal.shared.secrets_manifest_reader",
    "core.internal.shared.node_yaml",
    "core.internal.reconciler_projects",
    "core.internal.bootstrap.core_deliverer",
)


@ldd_trajectory
# 🧪 TRAP[TEST] · Regression · import библиотечных модулей без SystemExit (DevPlan 116 B4 T6.2)
def test_import_library_modules_no_system_exit(caplog) -> None:
    """Importing library modules must not raise SystemExit (процесс жив)."""
    for mod_name in _IMPORTABLE_MODULES:
        try:
            importlib.import_module(mod_name)
        except SystemExit as exc:
            pytest.fail(f"IMPORT {mod_name} → SystemExit({exc.code}) — sys.exit живёт в библиотечном коде!")
        logger.info("[IMP:8][importability] OK: %s", mod_name)

    logger.info("[IMP:9][importability] PASS: %d модулей импортируются без SystemExit", len(_IMPORTABLE_MODULES))


@ldd_trajectory
# 🧪 TRAP[TEST] · Regression · provision_networks без docker → PlatformFatalError (DevPlan 116 B4 T3.2/D4)
def test_provision_networks_no_docker_raises_platform_fatal(caplog, tmp_path, monkeypatch) -> None:
    """provision_networks без docker → PlatformFatalError (exit_code 10), НЕ SystemExit."""
    from core.internal.provisioner import provision_networks

    # docker недоступен: shutil.which("docker") → None (D4: docker-unavailable = Fatal 10)
    monkeypatch.setattr("shutil.which", lambda name: None if name == "docker" else "/usr/bin/" + name)

    platform_env = tmp_path / "platform-env.yaml"
    platform_env.write_text("networks:\n  - name: test-net\n    driver: bridge\n")

    class _FakeEnv:
        def __init__(self) -> None:
            self.networks = [type("Net", (), {"name": "test-net", "driver": "bridge"})()]

    with pytest.raises(PlatformFatalError) as excinfo:
        provision_networks(_FakeEnv(), dry_run=False)

    assert excinfo.value.exit_code == 10
    assert "Docker is not available" in str(excinfo.value)
    logger.info("[IMP:9][provisioner][no-docker] PASS: PlatformFatalError(exit=10) — процесс жив")


@ldd_trajectory
# 🧪 TRAP[TEST] · Regression · deploy_engine._handle_first_deploy raises (DevPlan 116 B4 T3.1)
def test_deploy_engine_first_deploy_raises_platform_fatal(caplog) -> None:
    """_handle_first_deploy → PlatformFatalError (а не SystemExit), exit_code 10."""
    from core.internal.deploy.deploy_engine import DeployEngine

    engine = DeployEngine()
    with pytest.raises(PlatformFatalError) as excinfo:
        engine._handle_first_deploy("proj", "svc", "ref", "test reason")

    assert excinfo.value.exit_code == 10
    assert "no rollback possible" in str(excinfo.value)
    logger.info("[IMP:9][deploy_engine][first-deploy] PASS: PlatformFatalError(exit=10) вместо SystemExit")
