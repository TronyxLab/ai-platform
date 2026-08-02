#!/usr/bin/env python3
"""
# GREP_SUMMARY: test-deploy-preflight, preflight, fqdn, port-conflict, first-deploy, PlatformFatalError, E4, R5, unit-tests
# STRUCTURE: ▶ test_preflight_checks ┌tmp project + mock validate.sh┐ → run_preflight_checks → ⎋ no raise (clean) │ ▶ test_preflight_fqdn_conflict_negative → validate.sh rc!=0 → ValidationError │ ▶ test_handle_first_deploy → PlatformFatalError (exit 10)
# region MODULE_CONTRACT
## @purpose  Unit tests for deploy/preflight.py + deploy/first_deploy.py (DevPlan 119 E4 $TEST_SPEC:
##           test_preflight_checks + R5 test_deploy_engine_preflight_parity).
## @scope    Изолированные module-level функции после экстракции из DeployEngine._preflight_checks/
##           _handle_first_deploy (874→<600 LOC монолит).
## @invariants
##   - Native imports; tmp_path; mock subprocess (validate.sh / ss)
##   - R5: parity — DeployEngine._preflight_checks делегирует в preflight.py (тот же контракт)
## @rationale  $TEST_SPEC E4 — preflight-проверки тестируются изолированно; parity-тест фиксирует
##             сохранение поведения после экстракции.
## @changes  2026-08-02 · Created (DevPlan 119 E4)
# endregion MODULE_CONTRACT
"""

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.internal.deploy import first_deploy, preflight
from core.internal.deploy.deploy_engine import DeployEngine
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · 2026-08-02 · unit · E4 preflight checks clean
# · Regression: DevPlan 119 E4 — _preflight_checks вынесен в deploy/preflight.py
# · Last fail: N/A (new module)
# · Remove if: preflight contract changes
@ldd_trajectory
def test_preflight_checks(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """run_preflight_checks passes when no FQDN/port conflict (validate.sh rc=0, no host_port)."""
    caplog.set_level(logging.INFO)
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "ai-platform.yaml").write_text("name: testproj\nmonitoring: {}\n")
    validate_script = tmp_path / "validate.sh"
    validate_script.write_text("#!/bin/sh\nexit 0\n")
    validate_script.chmod(0o755)

    with patch("core.internal.deploy.preflight.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        preflight.run_preflight_checks(str(project_dir), "web", str(validate_script))

    mock_run.assert_called_once()  # only validate.sh (no host_port → no ss call)
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    found_log = False
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_log = True
    print("--- END LDD TRAJECTORY ---")
    assert found_log, "Critical LDD Error: No IMP:9 business logic log found"


# 🧪 TRAP[TEST] · 2026-08-02 · unit · E4 preflight FQDN conflict → ValidationError
# · Regression: validate.sh rc!=0 → deploy blocked (ValidationError)
# · Remove if: FQDN check contract changes
def test_preflight_fqdn_conflict_negative(tmp_path: Path) -> None:
    """validate.sh --check-fqdn returns non-zero → ValidationError (deploy blocked)."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    validate_script = tmp_path / "validate.sh"
    validate_script.write_text("#!/bin/sh\nexit 1\n")
    validate_script.chmod(0o755)

    with patch("core.internal.deploy.preflight.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="FQDN already in use")
        with pytest.raises(preflight.ValidationError):
            preflight.run_preflight_checks(str(project_dir), "web", str(validate_script))


# 🧪 TRAP[TEST] · 2026-08-02 · unit · E4 port conflict → DeployError
# · Regression: ss -tlnp shows host_port → deploy blocked (DeployError)
# · Remove if: port check contract changes
def test_preflight_port_conflict_negative(tmp_path: Path) -> None:
    """host_port in ss -tlnp output → DeployError (deploy blocked)."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "ai-platform.yaml").write_text("name: testproj\nmonitoring:\n  host_port: 8080\n")

    with patch("core.internal.deploy.preflight.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="tcp LISTEN 0 1 0.0.0.0:8080 0.0.0.0:*", stderr="")
        with pytest.raises(preflight.DeployError):
            preflight.run_preflight_checks(str(project_dir), "web", "/nonexistent/validate.sh")


# 🧪 TRAP[TEST] · 2026-08-02 · unit · E4 first deploy → PlatformFatalError
# · Regression: первый деплой без previous image → FATAL exit 10 (нет rollback)
# · Remove if: first-deploy semantics change
def test_handle_first_deploy_negative() -> None:
    """handle_first_deploy always raises PlatformFatalError (exit 10, no rollback possible)."""
    from core.internal.shared.exceptions import PlatformFatalError

    with pytest.raises(PlatformFatalError) as exc_info:
        first_deploy.handle_first_deploy("proj", "web", "v1", "pull failed")
    assert "no rollback possible" in str(exc_info.value)


# 🧪 TRAP[TEST] · 2026-08-02 · R5 · E4 parity — DeployEngine делегирует в preflight.py
# · Regression: DevPlan 119 E4 — _preflight_checks экстракция (старый/новый код — одинаковый контракт)
# · Scenario: DeployEngine._preflight_checks → run_preflight_checks (тот же ValidationError/DeployError)
# · Remove if: preflight delegation changes
def test_deploy_engine_preflight_parity_negative(tmp_path: Path, monkeypatch) -> None:
    """R5: DeployEngine._preflight_checks делегирует в preflight.run_preflight_checks (классы совпадают)."""
    engine = DeployEngine(projects_base=str(tmp_path))
    # Классы исключений ЕДИНЫЕ (deploy_engine re-export из preflight — E4)
    from core.internal.deploy.deploy_engine import DeployError as EngineDeployError
    from core.internal.deploy.deploy_engine import ValidationError as EngineValidationError

    assert EngineDeployError is preflight.DeployError, "DeployError must be the SAME class (E4)"
    assert EngineValidationError is preflight.ValidationError, "ValidationError must be the SAME class (E4)"

    calls: list[str] = []

    def _fake_preflight(pdir: str, service: str, validate_script: str) -> None:
        calls.append(f"{pdir}:{service}")

    # _preflight_checks импортирует run_preflight_checks лениво внутри метода —
    # патчим целевой модуль (preflight.run_preflight_checks), как делает _preflight_checks.
    monkeypatch.setattr(preflight, "run_preflight_checks", _fake_preflight)

    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    engine._preflight_checks(str(proj_dir), "web")
    assert calls == [f"{proj_dir}:web"], "DeployEngine._preflight_checks must delegate to preflight module"
    logger.critical("[IMP:9][test] preflight parity OK — delegation contract preserved")
