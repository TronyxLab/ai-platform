"""
# GREP_SUMMARY: test-domains-import-deploy-context, strict-init, platform-fatal, best-effort, deploy-context, phases-docker, φ8, φ12, strict-passthrough
# STRUCTURE: ▶ tmp_path + patch(deploy_context) → ◇ import_deploy_context strict=False (WARN, best-effort: failed≠∅ / исключение) → ◇ import_deploy_context strict=True (failed≠∅ / исключение → PlatformFatalError + IMP:10) → ◇ strict=True all-deployed/skipped → no-raise → ◇ phase passthrough: φ8 strict=True / φ12 strict=False → ⎋ LDD trajectory IMP:7-10
# region MODULE_CONTRACT
## @purpose  Unit tests for strict-семантики деплоя контекста в INIT-режиме bootstrap
##           (import_deploy_context strict= param + φ8/φ12 проброс). Критерий приёмо-сдаточной
##           валидации: конец `make bootstrap-node` = сервер healthy И все проекты контекста
##           live → strict=True в INIT (failed≠∅/исключение → PlatformFatalError → фаза failed,
##           resumable); UPDATE (φ12) сохраняет best-effort (DEPLOY_BEST_EFFORT, D2 — WARN→0).
## @scope    lifecycle/helpers/domains.py::import_deploy_context + lifecycle/phases/docker.py
##           (phase_deploy_services φ8 / phase_deploy_update φ12). Без Docker (unit, tmp_path).
## @invariants
##   - deploy_context патчится моком (никаких реальных вызовов context_deployer)
##   - strict=False + failed≠∅ → НЕ исключение (текущее поведение сохранено, D2)
##   - strict=True + failed≠∅ → PlatformFatalError с именем failed-проекта + IMP:10 лог
##   - strict=True + исключение → PlatformFatalError (from e)
##   - φ8 вызывает import_deploy_context(strict=True); φ12 — strict=False (параметр-проброс)
##   - Каждый тест валидирует IMP:9+ через ldd_trajectory (Anti-Illusion)
## @rationale Failed-проекты в INIT больше не маскируются non-fatal (гейт «все проекты live»);
##           UPDATE не ломается (D2-контракт WARN→0). Паттерн мока — follow test_phases_docker.py.
## @changes  2026-09-01 · Created (strict-семантика INIT / best-effort UPDATE)
# endregion MODULE_CONTRACT
"""

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from core.internal.bootstrap.deploy.context_deployer import ContextDeployResult, ProjectDeployResult
from core.internal.bootstrap.lifecycle.helpers import domains as domains_mod
from core.internal.bootstrap.lifecycle.phases import docker as docker_mod
from core.internal.shared.exceptions import PlatformFatalError
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


def _result_with_failures(*failed_names: str) -> ContextDeployResult:
    """Build ContextDeployResult: 1 deployed + 1 skipped + failed-записи (как deploy_context)."""
    result = ContextDeployResult()
    result.add(ProjectDeployResult(name="ok-api", status="deployed"))
    result.add(ProjectDeployResult(name="ok-web", status="skipped"))
    for name in failed_names:
        result.add(ProjectDeployResult(name=name, status="failed", error="boom"))
    return result


def _all_ok_result() -> ContextDeployResult:
    """Build ContextDeployResult без failed (все deployed/skipped)."""
    result = ContextDeployResult()
    result.add(ProjectDeployResult(name="ok-api", status="deployed"))
    result.add(ProjectDeployResult(name="ok-web", status="skipped"))
    return result


def _phase_ctx(tmp_path: Path) -> tuple[Path, Path]:
    """tmp core_dir (с deploy-modules.sh) + node.yaml — паттерн test_phases_docker._deploy_update_ctx."""
    core_dir = tmp_path / "core"
    script_dir = core_dir / "internal" / "bootstrap"
    script_dir.mkdir(parents=True)
    (script_dir / "deploy-modules.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text("projects: []\nmodules: []\n")
    return core_dir, node_yaml


# 🧪 TRAP[TEST] · 2026-09-01 · strict · strict=True + failed≠∅ → PlatformFatalError (INIT)
# · Regression: helpers/domains.py глотал ВСЁ как non-fatal (line 118) → bootstrap exit 0
# ·   при упавших проектах (нарушение критерия «конец bootstrap = все проекты live»)
# · Scenario: deploy_context вернул 1 failed (shop-api) → strict=True → PlatformFatalError
# ·   с именем проекта + IMP:10 лог с перечнем failed
# · Last fail: N/A (новая strict-семантика; до фикса фейл маскировался)
# · Remove if: strict-семантика INIT отменена
@ldd_trajectory
def test_import_deploy_context_strict_failed_raises(caplog: pytest.LogCaptureFixture) -> None:
    """strict=True + result.failed≠∅ → PlatformFatalError (имя failed-проекта в сообщении)."""
    with (
        patch.object(domains_mod, "deploy_context", return_value=_result_with_failures("shop-api")),
        pytest.raises(PlatformFatalError) as excinfo,
    ):
        domains_mod.import_deploy_context("core", "tronyx-vps", "node.yaml", strict=True)
    assert "shop-api" in str(excinfo.value), f"FATAL-сообщение обязано содержать имя failed-проекта: {excinfo.value}"
    messages = [r.message for r in caplog.records]
    assert any("[IMP:10]" in m and "shop-api" in m for m in messages), (
        "strict-фейл обязан логировать IMP:10 с перечнем failed-имён"
    )


# 🧪 TRAP[TEST] · 2026-09-01 · strict · strict=True + исключение из deploy_context → PlatformFatalError
# · Scenario: deploy_context поднял RuntimeError → strict=True → PlatformFatalError (from e)
# · Last fail: N/A (до фикса — WARN non-fatal в обеих режимах)
# · Remove if: strict-семантика INIT отменена
@ldd_trajectory
def test_import_deploy_context_strict_exception_raises(caplog: pytest.LogCaptureFixture) -> None:
    """strict=True + исключение deploy_context → PlatformFatalError (причина в сообщении)."""
    with (
        patch.object(domains_mod, "deploy_context", side_effect=RuntimeError("boom")),
        pytest.raises(PlatformFatalError) as excinfo,
    ):
        domains_mod.import_deploy_context("core", "tronyx-vps", "node.yaml", strict=True)
    assert "boom" in str(excinfo.value), f"FATAL-сообщение обязано содержать причину: {excinfo.value}"


# 🧪 TRAP[TEST] · 2026-09-01 · strict · strict=True + все deployed/skipped → нет исключения
# · Scenario: deploy_context вернул 0 failed → strict=True НЕ поднимает PlatformFatalError
# · Last fail: N/A (новое поведение)
# · Remove if: strict-семантика INIT отменена
@ldd_trajectory
def test_import_deploy_context_strict_all_ok_no_raise(caplog: pytest.LogCaptureFixture) -> None:
    """strict=True + failed=0 (все deployed/skipped) → нет исключения (фаза успешна)."""
    with patch.object(domains_mod, "deploy_context", return_value=_all_ok_result()):
        domains_mod.import_deploy_context("core", "tronyx-vps", "node.yaml", strict=True)
    messages = [r.message for r in caplog.records]
    assert any("Complete: deployed=1 skipped=1 failed=0" in m for m in messages), (
        "strict-успех обязан логировать IMP:9 Complete c failed=0"
    )
    assert not any("STRICT FAIL" in m for m in messages), "strict=True + failed=0 не должен фейлить"


# 🧪 TRAP[TEST] · 2026-09-01 · best-effort · strict=False + failed≠∅ → WARN, без исключения (D2)
# · Scenario: UPDATE-режим (φ12): failed-проекты НЕ роняют фазу (DEPLOY_BEST_EFFORT, WARN→0)
# · Last fail: N/A (поведение-инвариант D2, зафиксировано чтобы не сломать)
# · Remove if: best-effort семантика UPDATE отменена
@ldd_trajectory
def test_import_deploy_context_non_strict_failed_no_raise(caplog: pytest.LogCaptureFixture) -> None:
    """strict=False + failed≠∅ → НЕ исключение (текущее best-effort поведение сохранено, D2)."""
    with patch.object(domains_mod, "deploy_context", return_value=_result_with_failures("shop-api")):
        domains_mod.import_deploy_context("core", "tronyx-vps", "node.yaml", strict=False)
    messages = [r.message for r in caplog.records]
    assert any("Complete: deployed=1 skipped=1 failed=1" in m for m in messages), (
        "best-effort обязан логировать IMP:9 Complete c failed=1"
    )
    assert not any("STRICT FAIL" in m for m in messages), "strict=False не должен включать strict-фейл"


# 🧪 TRAP[TEST] · 2026-09-01 · best-effort · strict=False + исключение → WARN non-fatal (D2)
# · Scenario: UPDATE-режим: исключение deploy_context → WARN «non-fatal», exit 0 (D2-контракт)
# · Last fail: N/A (поведение-инвариант D2, зафиксировано чтобы не сломать)
# · Remove if: best-effort семантика UPDATE отменена
@ldd_trajectory
def test_import_deploy_context_non_strict_exception_warns_only(caplog: pytest.LogCaptureFixture) -> None:
    """strict=False + исключение → WARN non-fatal, без PlatformFatalError (D2-контракт)."""
    with patch.object(domains_mod, "deploy_context", side_effect=RuntimeError("boom")):
        domains_mod.import_deploy_context("core", "tronyx-vps", "node.yaml", strict=False)
    messages = [r.message for r in caplog.records]
    assert any("non-fatal" in m and "boom" in m for m in messages), (
        "best-effort исключение обязано логировать WARN non-fatal с причиной"
    )
    logger.critical("[IMP:9][test] best-effort исключение не поднимает PlatformFatalError (D2)")


# 🧪 TRAP[TEST] · 2026-09-01 · passthrough · φ8 (INIT) вызывает import_deploy_context(strict=True)
# · Regression: контекстная часть φ8 глотала failed → bootstrap exit 0 при упавших проектах
# · Scenario: phase_deploy_services → import_deploy_context получает strict=True (параметр-проброс)
# · Last fail: N/A (до фикса φ8 вызывал без strict — non-fatal)
# · Remove if: strict-семантика INIT отменена
@ldd_trajectory
def test_phase_deploy_services_passes_strict_true(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """φ8: import_deploy_context вызывается со strict=True (INIT-критерий «все проекты live»)."""
    core_dir, node_yaml = _phase_ctx(tmp_path)
    with (
        patch.object(docker_mod.helpers_subprocess, "run_subprocess"),
        patch.object(docker_mod, "_sweep_stale_hc_markers", return_value=0),
        patch.object(docker_mod.helpers_domains, "import_deploy_context") as m_import,
    ):
        result = docker_mod.phase_deploy_services(str(core_dir), "tronyx-vps", str(node_yaml))

    assert result is True, f"φ8 успешный деплой обязан давать True, got {result!r}"
    m_import.assert_called_once()
    assert m_import.call_args.kwargs.get("strict") is True, (
        f"φ8 (INIT) обязан передавать strict=True, got kwargs={m_import.call_args.kwargs}"
    )


# 🧪 TRAP[TEST] · 2026-09-01 · passthrough · φ12 (UPDATE) вызывает import_deploy_context(strict=False)
# · Regression: strict-семантика INIT НЕ должна задеть UPDATE (D2: WARN→0, best-effort)
# · Scenario: phase_deploy_update → import_deploy_context получает strict=False
# · Last fail: N/A (новый параметр-проброс)
# · Remove if: best-effort семантика UPDATE отменена
@ldd_trajectory
def test_phase_deploy_update_passes_strict_false(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """φ12: import_deploy_context вызывается со strict=False (best-effort, DEPLOY_BEST_EFFORT D2)."""
    core_dir, node_yaml = _phase_ctx(tmp_path)
    with (
        patch.object(docker_mod.helpers_subprocess, "run_subprocess"),
        patch.object(docker_mod.helpers_domains, "ssl_provision_via_orchestrator", return_value="converged"),
        patch.object(docker_mod, "_apply_policy_script", return_value=False),
        patch.object(docker_mod, "_sweep_stale_hc_markers", return_value=0),
        patch.object(docker_mod.helpers_domains, "import_deploy_context") as m_import,
    ):
        result = docker_mod.phase_deploy_update(str(core_dir), "tronyx-vps", str(node_yaml))

    assert result is True, f"φ12 успешный update обязан давать True, got {result!r}"
    m_import.assert_called_once()
    assert m_import.call_args.kwargs.get("strict") is False, (
        f"φ12 (UPDATE) обязан передавать strict=False, got kwargs={m_import.call_args.kwargs}"
    )
