#!/usr/bin/env python3
# GREP_SUMMARY: test-shared-docker-compose docker-compose pull build up healthcheck retry image-exists
# STRUCTURE: ┌mock subprocess.run┐ → ○ test scenarios: pull → build → up → healthcheck → retry → image-exists
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/docker_compose.py
##           Uses mock subprocess.run to verify all docker compose operations.
## @scope    Tests: pull, build, up, healthcheck_poll, retry_pull, check_image_exists.
## @invariants
##   - All tests use tmp_path (no hardcoded paths)
##   - No Docker dependency (mocked subprocess)
##   - LDD: at least one IMP:9 log in each successful scenario
# endregion MODULE_CONTRACT

import logging
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from core.internal.shared.docker_compose import (
    check_image_exists,
    docker_compose_build,
    docker_compose_pull,
    docker_compose_up,
    healthcheck_poll,
    nginx_reload,
    retry_pull,
)

# ── Fixture ─────────────────────────────────────────────────────────────────


@pytest.fixture
def compose_dir(tmp_path: Path) -> str:
    """Create a mock compose directory.

    ## @purpose — Provide a valid directory path for docker compose tests.
    ## @io — ⇥ tmp_path → ⎋ str (directory path)
    """
    d = tmp_path / "my_project"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


# ── Pull tests ──────────────────────────────────────────────────────────────


# region FUNC_test_pull_success
## @purpose — Verify docker_compose_pull returns True on success.
##            AC: mock subprocess.run returncode=0 → True.
## @complexity — O(1)
def test_pull_success(compose_dir: str, caplog: pytest.LogCaptureFixture) -> None:
    """docker compose pull returns True on success."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: pull success
    # · Last fail: N/A (new test)
    # · Remove if: docker_compose_pull behavior changes

    with patch("core.internal.shared.docker_compose.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""

        result = docker_compose_pull(compose_dir)

    found_imp9 = any("[IMP:9]" in r.message for r in caplog.records)
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert result is True
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region FUNC_test_pull_failure
## @purpose — Verify docker_compose_pull returns False on failure.
##            AC: mock subprocess.run returncode=1 → False.
## @complexity — O(1)
def test_pull_failure(compose_dir: str, caplog: pytest.LogCaptureFixture) -> None:
    """docker compose pull returns False on failure."""
    caplog.set_level(logging.WARNING)

    # 🧪 TRAP[TEST] · Regression · Scenario: pull failure
    # · Last fail: N/A (new test)
    # · Remove if: docker_compose_pull behavior changes

    with patch("core.internal.shared.docker_compose.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "error"

        result = docker_compose_pull(compose_dir)

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert result is False


# endregion


# ── Build tests ─────────────────────────────────────────────────────────────


# region FUNC_test_build_success
## @purpose — Verify docker_compose_build returns True on success.
##            AC: mock subprocess.run returncode=0 → True.
## @complexity — O(1)
def test_build_success(compose_dir: str, caplog: pytest.LogCaptureFixture) -> None:
    """docker compose build returns True on success."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: build success
    # · Last fail: N/A (new test)
    # · Remove if: docker_compose_build behavior changes

    with patch("core.internal.shared.docker_compose.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0

        result = docker_compose_build(compose_dir)

    found_imp9 = any("[IMP:9]" in r.message for r in caplog.records)
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert result is True
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# ── Up tests ────────────────────────────────────────────────────────────────


# region FUNC_test_up_success
## @purpose — Verify docker_compose_up returns True on success.
##            AC: mock subprocess.run returncode=0 → True.
## @complexity — O(1)
def test_up_success(compose_dir: str, caplog: pytest.LogCaptureFixture) -> None:
    """docker compose up -d returns True on success."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: compose up success
    # · Last fail: N/A (new test)
    # · Remove if: docker_compose_up behavior changes

    with patch("core.internal.shared.docker_compose.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0

        result = docker_compose_up(compose_dir)

    found_imp9 = any("[IMP:9]" in r.message for r in caplog.records)
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert result is True
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region FUNC_test_up_passes_flags_service_env
## @purpose — Verify docker_compose_up passes flags/service/env_override (DevPlan 116 B5 T3, D7).
##            AC: command order ["docker","compose",*compose_args,"up","-d",*flags,service] + env merge.
## @complexity — O(1)
def test_up_passes_flags_service_env(compose_dir: str, caplog: pytest.LogCaptureFixture) -> None:
    """docker_compose_up должен передавать flags/service/env_override (D7)."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: up с политическими флагами и env IMAGE_TAG
    # · Last fail: N/A (new test — D7 параметры)
    # · Remove if: docker_compose_up signature changes

    with patch("core.internal.shared.docker_compose.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0

        result = docker_compose_up(
            compose_dir,
            compose_args=["-f", "compose.yaml"],
            service="app",
            env_override={"IMAGE_TAG": "v1.0.0"},
            flags=["--remove-orphans", "--force-recreate"],
        )

    assert result is True
    cmd = mock_run.call_args.args[0]
    assert cmd[:2] == ["docker", "compose"]
    assert "-f" in cmd and "compose.yaml" in cmd
    up_idx = cmd.index("up")
    assert cmd[up_idx : up_idx + 2] == ["up", "-d"]
    assert "--remove-orphans" in cmd and "--force-recreate" in cmd
    assert cmd[-1] == "app", "service должен быть последним аргументом"
    # env: копия os.environ + override (НЕ замена)
    env = mock_run.call_args.kwargs.get("env")
    assert env is not None
    assert env["IMAGE_TAG"] == "v1.0.0"
    assert env["PATH"] == os.environ["PATH"], "env_override должен быть поверх os.environ, не заменой"


# endregion


# ── Healthcheck tests ───────────────────────────────────────────────────────


# region FUNC_test_healthcheck_poll_healthy
## @purpose — Verify healthcheck_poll returns "healthy" via inspect-критерий (D5).
##            AC: docker ps → cid, docker inspect → running|healthy → "healthy".
## @complexity — O(1)
def test_healthcheck_poll_healthy(caplog: pytest.LogCaptureFixture) -> None:
    """healthcheck_poll returns 'healthy' when container is running+healthy (inspect)."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: container healthy via inspect
    # · Last fail: N/A (T3.4 — критерий переработан на inspect State.Health)
    # · Remove if: healthcheck_poll criterion changes

    with patch("core.internal.shared.docker_compose.subprocess.run") as mock_run:
        # docker ps --filter name= → cid; docker inspect → running|healthy
        mock_run.side_effect = [
            subprocess.CompletedProcess([], returncode=0, stdout="abc123\n", stderr=""),
            subprocess.CompletedProcess([], returncode=0, stdout="running|healthy", stderr=""),
        ]

        result = healthcheck_poll("test_project", timeout=5, interval=1)

    found_imp9 = any("[IMP:9]" in r.message for r in caplog.records)
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert result == "healthy"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region FUNC_test_healthcheck_poll_running_without_healthcheck
## @purpose — Verify running-без-healthcheck (Health.Status="") считается здоровым (D5, канон).
##            AC: docker inspect → running| → "healthy".
## @complexity — O(1)
def test_healthcheck_poll_running_without_healthcheck(caplog: pytest.LogCaptureFixture) -> None:
    """healthcheck_poll: контейнер running без healthcheck (""|none) → healthy (D5)."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: running без HEALTHCHECK — канон D5
    # · Last fail: N/A (T3.4 — единый критерий «здоров»)
    # · Remove if: healthcheck_poll criterion changes
    with patch("core.internal.shared.docker_compose.subprocess.run") as mock_run:
        mock_run.side_effect = [
            subprocess.CompletedProcess([], returncode=0, stdout="cid1\n", stderr=""),
            subprocess.CompletedProcess([], returncode=0, stdout="running|", stderr=""),  # Health.Status == ""
        ]

        result = healthcheck_poll("svc", timeout=5, interval=1)

    assert result == "healthy"


# endregion


# region FUNC_test_healthcheck_poll_timeout
## @purpose — Verify healthcheck_poll returns "unhealthy" after timeout when container unhealthy.
##            AC: docker inspect → running|unhealthy → ждём → timeout → "unhealthy".
## @complexity — O(1)
def test_healthcheck_poll_timeout(caplog: pytest.LogCaptureFixture) -> None:
    """healthcheck_poll returns 'unhealthy' after timeout with unhealthy container."""
    caplog.set_level(logging.WARNING)

    # 🧪 TRAP[TEST] · Regression · Scenario: timeout с unhealthy контейнером
    # · Last fail: N/A (T3.4 — «unhealthy» ждём, timeout → unhealthy)
    # · Remove if: healthcheck_poll criterion changes

    with (
        patch("core.internal.shared.docker_compose.subprocess.run") as mock_run,
        patch("core.internal.shared.docker_compose.time.sleep", return_value=None),
        patch(
            "core.internal.shared.docker_compose.time.monotonic",
            side_effect=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1],
        ),
    ):
        # docker ps всегда возвращает cid; inspect всегда running|unhealthy → ждём → timeout
        def _fake_run(cmd, **kwargs):
            if "inspect" in cmd:
                return subprocess.CompletedProcess([], returncode=0, stdout="running|unhealthy", stderr="")
            return subprocess.CompletedProcess([], returncode=0, stdout="abc123\n", stderr="")

        mock_run.side_effect = _fake_run

        result = healthcheck_poll("test_project", timeout=1, interval=1)

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert result == "unhealthy"


# endregion


# region FUNC_test_healthcheck_poll_service_filter
## @purpose — Verify healthcheck_poll with service= uses `docker compose ps -q {service}` (T3.4).
##            AC: первый subprocess-вызов — ["docker","compose","ps","-q",service].
## @complexity — O(1)
def test_healthcheck_poll_service_filter(caplog: pytest.LogCaptureFixture) -> None:
    """healthcheck_poll с service= фильтрует через docker compose ps -q (deploy_engine)."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: service-фильтр (T3.4, T5.3)
    # · Last fail: N/A (new test)
    # · Remove if: healthcheck_poll signature changes
    with patch("core.internal.shared.docker_compose.subprocess.run") as mock_run:
        mock_run.side_effect = [
            subprocess.CompletedProcess([], returncode=0, stdout="cid9\n", stderr=""),  # compose ps -q app
            subprocess.CompletedProcess([], returncode=0, stdout="running|healthy", stderr=""),
        ]

        result = healthcheck_poll("app", timeout=5, interval=1, service="app")

    assert result == "healthy"
    first_cmd = mock_run.call_args_list[0].args[0]
    assert first_cmd == ["docker", "compose", "ps", "-q", "app"]


# endregion


# ── Retry pull tests ────────────────────────────────────────────────────────


# region FUNC_test_retry_pull_success_second_attempt
## @purpose — Verify retry_pull succeeds on second attempt.
##            AC: 1st fail, 2nd success → True.
## @complexity — O(1)
def test_retry_pull_success_second_attempt(compose_dir: str, caplog: pytest.LogCaptureFixture) -> None:
    """retry_pull returns True when second attempt succeeds."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: retry success on 2nd attempt
    # · Last fail: N/A (new test)
    # · Remove if: retry_pull behavior changes

    with patch("core.internal.shared.docker_compose.docker_compose_pull") as mock_pull:
        mock_pull.side_effect = [False, True]

        result = retry_pull(compose_dir, max_attempts=3, backoff_seconds=[1, 1, 1])

    found_imp9 = any("[IMP:9]" in r.message for r in caplog.records)
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert result is True
    assert mock_pull.call_count == 2
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region FUNC_test_retry_pull_all_fail
## @purpose — Verify retry_pull returns False after all attempts fail.
##            AC: all 3 attempts fail → False.
## @complexity — O(1)
def test_retry_pull_all_fail(compose_dir: str, caplog: pytest.LogCaptureFixture) -> None:
    """retry_pull returns False when all attempts fail."""
    caplog.set_level(logging.WARNING)

    # 🧪 TRAP[TEST] · Regression · Scenario: all retries fail
    # · Last fail: N/A (new test)
    # · Remove if: retry_pull behavior changes

    with patch("core.internal.shared.docker_compose.docker_compose_pull") as mock_pull:
        mock_pull.return_value = False

        result = retry_pull(compose_dir, max_attempts=3, backoff_seconds=[1, 1, 1])

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert result is False
    assert mock_pull.call_count == 3


# endregion


# ── Check image exists tests ────────────────────────────────────────────────


# region FUNC_test_check_image_exists_found
## @purpose — Verify check_image_exists returns True when image is found.
##            AC: docker manifest inspect returncode=0 → True.
## @complexity — O(1)
def test_check_image_exists_found(caplog: pytest.LogCaptureFixture) -> None:
    """check_image_exists returns True when docker manifest inspect succeeds."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: image found in registry
    # · Last fail: N/A (new test)
    # · Remove if: check_image_exists behavior changes

    with patch("core.internal.shared.docker_compose.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0

        result = check_image_exists("ghcr.io/test/image:latest")

    found_imp9 = any("[IMP:9]" in r.message for r in caplog.records)
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert result is True
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region FUNC_test_check_image_exists_not_found
## @purpose — Verify check_image_exists returns False when image not found.
##            AC: docker manifest inspect returncode=1 → False.
## @complexity — O(1)
def test_check_image_exists_not_found(caplog: pytest.LogCaptureFixture) -> None:
    """check_image_exists returns False when docker manifest inspect fails."""
    caplog.set_level(logging.WARNING)

    # 🧪 TRAP[TEST] · Regression · Scenario: image not found in registry
    # · Last fail: N/A (new test)
    # · Remove if: check_image_exists behavior changes

    with patch("core.internal.shared.docker_compose.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1

        result = check_image_exists("ghcr.io/test/image:latest")

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert result is False


# endregion


# ── nginx_reload tests (HOLE-1, DevPlan 119 F4) ──────────────────────────────


# region FUNC_test_nginx_reload_success
## @purpose — nginx_reload() успешный: docker exec nginx nginx -s reload (rc=0) → no-raise.
##            Закрывает HOLE-1 (shared/docker_compose.py:694 — создан в 118 D6, 0 тестов).
## @io — ⇥ caplog → ⎋ None (asserts no exception + docker exec вызван)
## @complexity — O(1)
## @invariants
##   - mock subprocess.run: docker exec nginx nginx -s reload → returncode=0
##   - Команда содержит ["docker", "exec", container, "nginx", "-s", "reload"]
##   - IMP:9 лог обязателен (LDD)
# 🧪 TRAP[TEST] · DevPlan 119 F4 (HOLE-1) · nginx_reload success
# · Last fail: N/A — функция не была покрыта тестами (0 tests)
# · Remove if: nginx_reload сигнатура/семантика меняется
def test_nginx_reload_success(caplog: pytest.LogCaptureFixture) -> None:
    """nginx_reload успешно выполняет docker exec nginx -s reload."""
    caplog.set_level(logging.INFO)

    with patch("core.internal.shared.docker_compose.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0

        nginx_reload("nginx", timeout=30)

    cmd = mock_run.call_args.args[0]
    assert cmd == ["docker", "exec", "nginx", "nginx", "-s", "reload"], f"Unexpected cmd: {cmd}"

    found_imp9 = any("[IMP:9]" in r.message for r in caplog.records)
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_nginx_reload_success


# region FUNC_test_nginx_reload_failure_mode
## @purpose — nginx_reload: контейнер не существует (docker exec rc≠0) — non-fatal контракт.
##            Функция НЕ бросает исключение (caller решает severity — _step_nginx_reload).
## @io — ⇥ caplog → ⎋ None (asserts no-raise при rc=1)
## @complexity — O(1)
## @invariants
##   - subprocess.run → returncode=1 (контейнер отсутствует) — функция продолжает (check=False)
##   - no-raise: docker exec несуществующего контейнера — handled, не исключение
## @rationale — DevPlan 118 D6: nginx_reload — non-fatal фасад; caller (шаг D6) ловит
##              subprocess.TimeoutExpired/OSError. rc≠0 НЕ вызывает исключение.
# 🧪 TRAP[TEST] · DevPlan 119 F4 (HOLE-1) · nginx_reload failure mode
# · Last fail: N/A — функция не была покрыта тестами
# · Remove if: nginx_reload контракт меняется на fatal
def test_nginx_reload_failure_mode(caplog: pytest.LogCaptureFixture) -> None:
    """nginx_reload: docker exec rc=1 (контейнер отсутствует) → no-raise (non-fatal)."""
    caplog.set_level(logging.INFO)

    with patch("core.internal.shared.docker_compose.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "Error: No such container: nginx"

        # Должно пройти без исключения (non-fatal контракт D6)
        nginx_reload("nginx", timeout=30)

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")
    assert mock_run.call_count == 1


# endregion FUNC_test_nginx_reload_failure_mode


# region FUNC_test_nginx_reload_timeout
## @purpose — nginx_reload: subprocess.TimeoutExpired → пробрасывается (R5 контракт:
##            исключения TimeoutExpired/OSError/FileNotFoundError — caller ловит, шаг D6).
## @io — ⇥ caplog → ⎋ None (asserts TimeoutExpired raised)
## @complexity — O(1)
## @invariants
##   - subprocess.run → subprocess.TimeoutExpired → nginx_reload НЕ глотает (пробрасывает)
##   - Caller (_step_nginx_reload) ловит и логирует WARN
# 🧪 TRAP[TEST] · DevPlan 119 F4 (HOLE-1) · nginx_reload timeout
# · Last fail: N/A — функция не была покрыта тестами
# · Remove if: nginx_reload начинает глотать TimeoutExpired
def test_nginx_reload_timeout(caplog: pytest.LogCaptureFixture) -> None:
    """nginx_reload: TimeoutExpired → пробрасывается (caller решает severity)."""
    caplog.set_level(logging.INFO)

    with (
        patch(
            "core.internal.shared.docker_compose.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["docker", "exec", "nginx"], timeout=30),
        ),
        pytest.raises(subprocess.TimeoutExpired),
    ):
        nginx_reload("nginx", timeout=30)

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")


# endregion FUNC_test_nginx_reload_timeout


# region FUNC_test_nginx_reload_container_missing_negative
## @purpose  R5 negative (DevPlan 119 F4 AC-F4.3): отсутствующий контейнер — исходный вход,
##           поймавший проблему (docker exec несуществующего контейнера rc=1). Проверяем,
##           что nginx_reload НЕ падает с необработанным исключением — non-fatal контракт.
## @io — ⇥ caplog → ⎋ None (asserts no-raise + корректная команда)
## @complexity — O(1)
# 🧪 TRAP[TEST] · NEGATIVE (R5) · nginx_reload container missing — DevPlan 119 F4
# · Last fail: отсутствующий контейнер → необработанный exception (до non-fatal контракта D6)
# · Remove if: nginx_reload перестаёт быть non-fatal
def test_nginx_reload_container_missing_negative(caplog: pytest.LogCaptureFixture) -> None:
    """R5: отсутствующий контейнер → no-raise (non-fatal), docker exec вызван с верной командой."""
    caplog.set_level(logging.INFO)

    with patch("core.internal.shared.docker_compose.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1  # "No such container"
        mock_run.return_value.stderr = "Error response from daemon: No such container: nginx"

        nginx_reload("nginx", timeout=30)  # Должно пройти — non-fatal

    cmd = mock_run.call_args.args[0]
    assert cmd[0] == "docker" and cmd[1] == "exec" and cmd[2] == "nginx"
    print("[IMP:9][test] R5 PASS: отсутствующий контейнер handled (no-raise)")


# endregion FUNC_test_nginx_reload_container_missing_negative
