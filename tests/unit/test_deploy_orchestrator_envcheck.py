# GREP_SUMMARY: deploy-orchestrator-envcheck env-check-crash fail-closed sentinel missing AI-0011 DevPlan-17 required-secrets
# STRUCTURE: ▶ monkeypatch check_env_requires → ◇ raise | [] → ⊕ _deploy_sequential verdict → ⎋ failed/deployed + IMP:9 env_check_error
# region MODULE_CONTRACT
## @purpose  AI-0011 (DevPlan 17 T1.1): краш secrets_validator.check_env_requires обязан быть
##           громким — модуль FAILED с sentinel «<env_check_error>: …», а не «missing=[]» pass.
##           R5-негатив: валидатор вернул [] → деплой продолжается (регрессия на old behavior).
## @scope    tests/unit: _deploy_sequential c monkeypatched валидатором/docker-деплоем;
##           без subprocess/docker.
## @invariants
##   - Краш валидатора → модуль в failed, deployed=0, в логах IMP:9 env_check_error + sentinel
##   - Пустой missing от валидатора → деплой идёт (deployed растёт)
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path

import pytest

from core.internal.bootstrap.deploy import deploy_orchestrator

logger = logging.getLogger(__name__)


def _print_ldd_trajectory(caplog: pytest.LogCaptureFixture) -> bool:
    """Вывод IMP:7-10 траектории; return True если найден хотя бы один IMP:9."""
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        msg = record.getMessage()
        if "[IMP:" in msg:
            print(msg)
            match = re.search(r"\[IMP:(\d+)\]", msg)
            if match and int(match.group(1)) >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")
    return found_imp9


# 🧪 TRAP[TEST] · 2026-08-26 · P1 · краш env-check валидатора ≠ pass (AI-0011)
# · Regression: except-ветка DEPLOY_BEST_EFFORT глотала raise валидатора как missing=[]
# · Scenario: check_env_requires raises RuntimeError("boom") → модуль FAILED, deployed=0,
#   в логе IMP:9 env_check_error + sentinel «<env_check_error>: boom» + счётчик env_check_errors=1
# · Last fail: DevPlan 17 верификация @64c2090 (аудит 08-ai-code AI-0011)
# · Remove if: env-check переезжает в отдельный verb с собственным fail-closed контрактом
def test_env_check_crash_is_loud(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    core_dir = tmp_path / "core"
    core_dir.mkdir()

    def _boom(module_name: str, manifest_path: str) -> list[str]:
        err = "validator exploded"
        raise RuntimeError(err)

    monkeypatch.setattr(deploy_orchestrator.secrets_validator, "check_env_requires", _boom)

    with caplog.at_level(logging.INFO, logger="core.internal.bootstrap.deploy.deploy_orchestrator"):
        deployed, failed = deploy_orchestrator._deploy_sequential(["postgres"], str(modules_dir), str(core_dir))

    assert _print_ldd_trajectory(caplog), "LDD Error: нет IMP:9 записи о env_check_error"
    assert failed == ["postgres"], f"модуль обязан быть FAILED при краше валидатора: failed={failed}"
    assert deployed == 0
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "<env_check_error>" in joined, "sentinel обязателен в отчёте/логе деплоя"
    assert "env_check_errors=1" in joined, "счётчик env_check_errors обязателен в [done]-сводке"


# 🧪 TRAP[TEST] · 2026-08-26 · P1 · R5-негатив: пустой missing от валидатора → деплой идёт
# · Regression: фикс T1.1 не должен превращать легитимный pass в false-failure
# · Scenario: check_env_requires возвращает [] (monkeypatch) + docker-deploy ok=True
#   → deployed=1, failed=[], env_check_error записей нет
# · Last fail: регрессия-охранник фикса AI-0011 (DevPlan 17)
# · Remove if: env-check переезжает в отдельный verb с собственным контрактом
def test_env_check_pass_deploys_negative(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    core_dir = tmp_path / "core"
    core_dir.mkdir()

    monkeypatch.setattr(deploy_orchestrator.secrets_validator, "check_env_requires", lambda _name, _manifest: [])
    calls: list[tuple[str, ...]] = []

    def _fake_deploy(name: str, **kwargs: object) -> bool:
        calls.append((name,))
        return True

    monkeypatch.setattr(deploy_orchestrator.docker_orchestrator, "deploy_docker_module", _fake_deploy)

    with caplog.at_level(logging.INFO, logger="core.internal.bootstrap.deploy.deploy_orchestrator"):
        deployed, failed = deploy_orchestrator._deploy_sequential(["redis"], str(modules_dir), str(core_dir))

    for record in caplog.records:
        if "[IMP:" in record.getMessage():
            print(record.getMessage())
    assert calls == [("redis",)], "docker-деплой обязан вызваться после чистого env-check"
    assert (deployed, failed) == (1, [])
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "<env_check_error>" not in joined, "pass-сценарий не должен содержать env_check_error"
