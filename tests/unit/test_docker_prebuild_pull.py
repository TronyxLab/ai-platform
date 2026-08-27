# GREP_SUMMARY: test-docker-prebuild-pull prebuild dockerfile-bases retry backoff subprocess-mock sleep-fn partial-success deterministic-bootstrap
# STRUCTURE: ┌tmp_path module + Dockerfile┐ → ○ docker_prebuild_pull → ○ monkeypatch subprocess.run (fake docker pull)
#           → ○ sleep_fn recorder (backoff замер) → ◇ success/partial/fail/no-Dockerfile → ⎋ asserts + LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/docker_compose.docker_prebuild_pull — pre-pull
##           пинненных баз build-модулей (F-03, 017-launch-validation P0): retry-цикл, backoff
##           5/15/45 (замеряется через sleep_fn DI-шов), мягкий partial-success, no-Dockerfile no-op.
## @scope    Tests: success first attempt / 2 failures then success (backoff) / all failures → False /
##           partial success IMP:7 / no Dockerfile → True. БЕЗ реальных docker-вызовов (subprocess mock).
## @invariants
##   - tmp_path для модульной директории (Zero Hardcode Rule)
##   - subprocess.run мокается через monkeypatch (никаких реальных docker pull)
##   - Backoff засекается через sleep_fn recorder (DI-шов retry.py, паттерн test_retry.py)
##   - LDD: траектория IMP:7-10 печатается; IMP:9 обязателен в успешных сценариях
## @rationale Детерминированный cold-bootstrap (buildkit не ретраит pull внутри сборки) — тесты
##            фиксируют ретрай-контракт без сетевых зависимостей (R4: no-service → FAIL, не skip).
# endregion MODULE_CONTRACT

import logging
import subprocess
from pathlib import Path

import pytest

from core.internal.shared.docker_compose import docker_prebuild_pull
from core.internal.shared.timeouts import PREBUILD_PULL_ATTEMPTS

logger = logging.getLogger(__name__)

_DOCKERFILE = "FROM python:3.12-alpine@sha256:78098ea6a3a9c6a7727a5d4674e4a44e57e01fac878ee9cb4d24a86bd93916ff\n"


def _module_dir_with_dockerfile(tmp_path: Path, content: str = _DOCKERFILE) -> Path:
    """Создать модульную директорию с Dockerfile во tmp_path.

    ## @purpose — helper: валидная модульная директория (Zero Hardcode — tmp_path).
    ## @io — ⇥ tmp_path, content → ⎋ Path (модульная директория)
    """
    mod_dir = tmp_path / "modules" / "test-mod"
    mod_dir.mkdir(parents=True)
    (mod_dir / "Dockerfile").write_text(content, encoding="utf-8")
    return mod_dir


def _fake_completed(returncode: int) -> subprocess.CompletedProcess[str]:
    """Фабрика CompletedProcess для фейкового `docker pull`.

    ## @purpose — двойник subprocess.run: returncode-контроль без сети.
    ## @io — ⇥ returncode: int → ⎋ CompletedProcess[str]
    """
    return subprocess.CompletedProcess([], returncode=returncode, stdout="", stderr="")


def _print_trajectory(caplog: pytest.LogCaptureFixture) -> None:
    """Печать LDD-траектории (IMP:7-10) — Anti-Illusion: агент видит реальный путь выполнения.

    ## @purpose — общий helper печати траектории (DRY внутри файла тестов).
    ## @io — ⇥ caplog → ⎋ None (side-effect: stdout-печать)
    """
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            logger.info("%s", record.message)
    logger.info("--- END LDD TRAJECTORY ---")


# region FUNC_test_prebuild_pull_success_first_attempt
# 🧪 TRAP[TEST] · Regression · F-03 (017-launch-validation) · успех с первой попытки
# · Scenario: docker pull базового образа rc=0 сразу — pre-pull возвращает True без ретраев
# · Last fail: N/A (new test)
# · Remove if: docker_prebuild_pull retry-контракт меняется
def test_prebuild_pull_success_first_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """docker_prebuild_pull: успех с первой попытки → True, 0 sleep (no backoff), верная команда."""
    caplog.set_level(logging.INFO)
    mod_dir = _module_dir_with_dockerfile(tmp_path)
    sleeps: list[float] = []

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        assert "docker" in cmd and "pull" in cmd
        return _fake_completed(0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = docker_prebuild_pull(str(mod_dir), sleep_fn=sleeps.append)

    _print_trajectory(caplog)
    assert result is True
    assert calls == [
        ["docker", "pull", "python:3.12-alpine@sha256:78098ea6a3a9c6a7727a5d4674e4a44e57e01fac878ee9cb4d24a86bd93916ff"]
    ]
    assert sleeps == [], "успех с первой попытки — backoff не должен срабатывать"
    assert any("[IMP:9][docker_prebuild_pull][done]" in r.message for r in caplog.records), (
        "Critical LDD Error: No IMP:9 business logic log found"
    )


# endregion FUNC_test_prebuild_pull_success_first_attempt


# region FUNC_test_prebuild_pull_two_failures_then_success
# 🧪 TRAP[TEST] · Regression · F-03 · 2 неудачи потом успех — backoff засекается (5s, 15s)
# · Scenario: docker pull rc=1, rc=1, rc=0 — ретрай-цикл отрабатывает backoff 5/15 перед успехом
# · Last fail: N/A (new test)
# · Remove if: docker_prebuild_pull backoff-расписание меняется
def test_prebuild_pull_two_failures_then_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """2 неудачи потом успех → True, backoff [5, 15] засекается через sleep_fn recorder."""
    caplog.set_level(logging.INFO)
    mod_dir = _module_dir_with_dockerfile(tmp_path)
    sleeps: list[float] = []

    returncodes = iter([1, 1, 0])

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return _fake_completed(next(returncodes))

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = docker_prebuild_pull(str(mod_dir), sleep_fn=sleeps.append)

    _print_trajectory(caplog)
    assert result is True
    assert sleeps == [5.0, 15.0], "backoff между попытками 2-3 и 3-4 (задержки 5s, 15s) не засечён"
    assert any("[IMP:9][docker_prebuild_pull][pulled]" in r.message for r in caplog.records), (
        "Critical LDD Error: No IMP:9 business logic log found"
    )


# endregion FUNC_test_prebuild_pull_two_failures_then_success


# region FUNC_test_prebuild_pull_all_fail
# 🧪 TRAP[TEST] · Regression · F-03 · все попытки исчерпаны → False + IMP:10
# · Scenario: docker pull стабильно rc=1 — 4 попытки, backoff 5/15/45, итог False
# · Last fail: N/A (new test)
# · Remove if: docker_prebuild_pull all-fail контракт меняется
def test_prebuild_pull_all_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """все попытки неудачны → False, PREBUILD_PULL_ATTEMPTS вызовов, полный backoff [5, 15, 45]."""
    caplog.set_level(logging.WARNING)
    mod_dir = _module_dir_with_dockerfile(tmp_path)
    sleeps: list[float] = []
    call_count = [0]

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        call_count[0] += 1
        return _fake_completed(1)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = docker_prebuild_pull(str(mod_dir), sleep_fn=sleeps.append)

    _print_trajectory(caplog)
    assert result is False
    assert call_count[0] == PREBUILD_PULL_ATTEMPTS, (
        f"ожидалось ровно {PREBUILD_PULL_ATTEMPTS} попыток docker pull (1 + 3 ретрая)"
    )
    assert sleeps == [5.0, 15.0, 45.0], "исчерпание: backoff 5/15/45 должен быть засечён полностью"
    assert any("[IMP:10][docker_prebuild_pull][exhausted]" in r.message for r in caplog.records), (
        "ожидался IMP:10 exhausted для исчерпанного ретрая"
    )
    assert any("[IMP:10][docker_prebuild_pull][fail]" in r.message for r in caplog.records), (
        "ожидался IMP:10 fail (все базы не спулены)"
    )


# endregion FUNC_test_prebuild_pull_all_fail


# region FUNC_test_prebuild_pull_no_dockerfile_noop
# 🧪 TRAP[TEST] · Edge-case · F-03 · нет Dockerfile → True без subprocess-вызовов
# · Scenario: модуль без собственного образа — pre-pull no-op (не ошибка)
# · Last fail: N/A (new test)
# · Remove if: no-bases семантика docker_prebuild_pull меняется
def test_prebuild_pull_no_dockerfile_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """модуль без Dockerfile → True (no-op), 0 subprocess-вызовов docker pull."""
    caplog.set_level(logging.INFO)
    mod_dir = tmp_path / "modules" / "no-image-mod"
    mod_dir.mkdir(parents=True)
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return _fake_completed(0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = docker_prebuild_pull(str(mod_dir))

    _print_trajectory(caplog)
    assert result is True
    assert calls == [], "no Dockerfile — docker pull не должен вызываться"
    assert any("[IMP:8][docker_prebuild_pull][no_bases]" in r.message for r in caplog.records)


# endregion FUNC_test_prebuild_pull_no_dockerfile_noop


# region FUNC_test_prebuild_pull_partial_success_soft
# 🧪 TRAP[TEST] · Regression · F-03 · частичный success → True + warning IMP:7 (мягкий проход)
# · Scenario: 2 базы — первая исчерпала ретраи, вторая спулена сразу → pre-pull считается успешным
# · Last fail: N/A (new test)
# · Remove if: partial-success контракт docker_prebuild_pull меняется
def test_prebuild_pull_partial_success_soft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """частичный success (1 из 2 баз) → True + warning IMP:7 partial (build остаётся арбитром)."""
    caplog.set_level(logging.WARNING)
    dockerfile = "FROM python:3.12-alpine@sha256:aaa\nFROM debian:bookworm-slim@sha256:bbb\n"
    mod_dir = _module_dir_with_dockerfile(tmp_path, dockerfile)
    sleeps: list[float] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        # python-база (aaa) всегда падает; debian-база (bbb) — успех с первой попытки
        return _fake_completed(0 if any("bbb" in part for part in cmd) else 1)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = docker_prebuild_pull(str(mod_dir), sleep_fn=sleeps.append)

    _print_trajectory(caplog)
    assert result is True
    assert sleeps == [5.0, 15.0, 45.0], "исчерпание первой базы — полный backoff"
    assert any("[IMP:7][docker_prebuild_pull][partial]" in r.message for r in caplog.records), (
        "частичный success обязан логироваться мягко (IMP:7 partial)"
    )
    assert any("[IMP:10][docker_prebuild_pull][exhausted]" in r.message for r in caplog.records)


# endregion FUNC_test_prebuild_pull_partial_success_soft
