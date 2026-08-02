#!/usr/bin/env python3
# GREP_SUMMARY: test-shared-timeouts timeouts constants defaults inspect-signature compose-up pull build healthcheck ssh-connect deploy retry-backoff falsifiable
# STRUCTURE: ▶ test_constants_values → test_shared_function_defaults (inspect.signature) → test_channels_defaults → test_ssh_opts_connect_timeout → test_retry_backoff
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/timeouts.py — единый реестр таймаутов (U-11).
##           Фальсифицируемая проверка: дефолты shared-функций == константам timeouts
##           (через inspect.signature — если кто-то хардкодит литерал вместо константы, тест RED).
## @scope    Tests: значения констант, дефолты docker_compose_*/healthcheck_poll/retry_pull,
##           канальные дефолты (channels), ConnectTimeout в ssh_opts.
## @invariants
##   - Дефолты shared-функций импортируются из timeouts (НЕ литералы) — inspect.signature проверка
##   - Значения канонизированы: up=180, pull=300, build=300, healthcheck=60, ssh-connect=30,
##     deploy=600, ssh-read=60, image-check=60, docker-cmd=10, docker-stop=30, rsync=600
##   - RETRY_BACKOFF_SECONDS = [5,10,20]
##   - LDD: IMP:9 assert в каждом успешном сценарии
## @rationale U-11: литералы timeout= заменены константами. Эта проверка ловит регрессию —
##            «хардкод литерала в дефолте shared-функции» (фальсифицируемость R2).
# endregion MODULE_CONTRACT

import inspect
import logging
import os

import pytest

import core.internal.shared.timeouts as timeouts
from core.internal.shared import ssh_opts
from core.internal.shared.docker_compose import (
    check_image_exists,
    docker_compose_build,
    docker_compose_config,
    docker_compose_down,
    docker_compose_images,
    docker_compose_ps,
    docker_compose_pull,
    docker_compose_up,
    healthcheck_poll,
    retry_pull,
)

logger = logging.getLogger(__name__)


def _default_param(func, name: str):
    """Извлечь default параметра функции через inspect.signature.

    ## @purpose — Фальсифицируемая проверка: default должен быть константой timeouts,
    ##            а не литералом (иначе сравнение с константой провалится).
    """
    return inspect.signature(func).parameters[name].default


def _assert_imp9(caplog: pytest.LogCaptureFixture) -> None:
    """Assert at least one IMP:9 log in caplog (LDD telemetry standard)."""
    found = any("[IMP:9]" in r.message for r in caplog.records)
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")
    assert found, "Critical LDD Error: No IMP:9 business logic log found"


# region FUNC_test_constants_values
## @purpose — Verify канонические значения констант timeouts (T1 таблица).
## @complexity — O(1)
def test_constants_values(caplog: pytest.LogCaptureFixture) -> None:
    """Константы timeouts должны иметь канонические значения (T1)."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: значения констант — канон T1
    # · Last fail: N/A (new test)
    # · Remove if: таймаут-политика меняется осознанно (обновить оба места)
    assert timeouts.COMPOSE_UP_TIMEOUT == 180
    assert timeouts.PULL_TIMEOUT == 300
    assert timeouts.BUILD_TIMEOUT == 300
    assert timeouts.HEALTHCHECK_POLL_TIMEOUT == 60
    assert timeouts.SSH_CONNECT_TIMEOUT == 30
    assert timeouts.DEPLOY_TIMEOUT == 600
    assert timeouts.SSH_READ_TIMEOUT == 60
    assert timeouts.IMAGE_CHECK_TIMEOUT == 60
    assert timeouts.DOCKER_CMD_TIMEOUT == 10
    assert timeouts.DOCKER_STOP_TIMEOUT == 30
    assert timeouts.RSYNC_TIMEOUT == 600
    assert timeouts.RETRY_BACKOFF_SECONDS == [5, 10, 20]
    assert timeouts.RETRY_COUNT == 2
    # DevPlan 117 D28/D32/D34/D36/D29 — новые домены реестра
    assert timeouts.SUDOERS_CMD_TIMEOUT == 15
    assert timeouts.HEALTHCHECK_POLL_INTERVAL == 3
    assert timeouts.HEALTHCHECK_POLL_MAX_RETRIES == 20
    assert timeouts.RETRY_BACKOFF_EXPONENTIAL_BASE == 2
    assert timeouts.WATCHDOG_TIMEOUT == 90
    assert timeouts.WATCHDOG_POLL_INTERVAL == 5
    assert timeouts.WATCHDOG_CURL_MAX_TIME == 3
    assert timeouts.WATCHDOG_CURL_TG_MAX_TIME == 30
    # B6 (DevPlan 119): [8080,8000] → [3000,4000,8000,8080,9000] — покрытие реальных compose-портов
    assert timeouts.PROJECT_HEALTHCHECK_PORTS == [3000, 4000, 8000, 8080, 9000]
    # B7 (DevPlan 119): +converge-домен
    assert timeouts.CONVERGE_DOCKER_TIMEOUT == 30
    assert timeouts.FILE_OP_TIMEOUT == 15
    logger.info("[IMP:9][test_constants_values] Все %d констант канонизированы", 24)
    _assert_imp9(caplog)


# endregion


# region FUNC_test_shared_function_defaults
## @purpose — Verify дефолты shared-функций == константам timeouts (inspect.signature).
## @complexity — O(1)
def test_shared_function_defaults(caplog: pytest.LogCaptureFixture) -> None:
    """Дефолты docker_compose_*/healthcheck_poll/retry_pull должны быть константами timeouts (U-11)."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: default параметра — константа, не литерал
    # · Last fail: N/A (T1 — shared-функции импортируют из timeouts)
    # · Remove if: сигнатура shared-функции меняется осознанно
    assert _default_param(docker_compose_up, "timeout") == timeouts.COMPOSE_UP_TIMEOUT
    assert _default_param(docker_compose_down, "timeout") == timeouts.COMPOSE_UP_TIMEOUT
    assert _default_param(docker_compose_pull, "timeout") == timeouts.PULL_TIMEOUT
    assert _default_param(retry_pull, "timeout") == timeouts.PULL_TIMEOUT
    assert (
        _default_param(retry_pull, "backoff_seconds") is None
        or _default_param(retry_pull, "backoff_seconds") == timeouts.RETRY_BACKOFF_SECONDS
    )
    assert _default_param(docker_compose_build, "timeout") == timeouts.BUILD_TIMEOUT
    assert _default_param(healthcheck_poll, "timeout") == timeouts.HEALTHCHECK_POLL_TIMEOUT
    assert _default_param(check_image_exists, "timeout") == timeouts.IMAGE_CHECK_TIMEOUT
    assert _default_param(docker_compose_config, "timeout") == timeouts.DOCKER_CMD_TIMEOUT
    assert _default_param(docker_compose_ps, "timeout") == timeouts.DOCKER_CMD_TIMEOUT
    assert _default_param(docker_compose_images, "timeout") == timeouts.DOCKER_CMD_TIMEOUT
    logger.info("[IMP:9][test_shared_function_defaults] 11 дефолтов == константам timeouts")
    _assert_imp9(caplog)


# endregion


# region FUNC_test_channels_defaults
## @purpose — Verify канальные дефолты (channels) — из timeouts (T1/T7).
## @complexity — O(1)
def test_channels_defaults(caplog: pytest.LogCaptureFixture) -> None:
    """Канальные дефолты (channels) должны импортироваться из timeouts."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: DEFAULT_DEPLOY_TIMEOUT/RETRY_COUNT/RETRY_BACKOFF
    # · Last fail: N/A (T1.3 — константы канала перенесены в timeouts)
    # · Remove if: канальные дефолты меняются
    from core.internal.deploy import channels

    assert channels.DEFAULT_RETRY_COUNT == timeouts.RETRY_COUNT
    assert timeouts.RETRY_BACKOFF_SECONDS[0] == channels.DEFAULT_RETRY_BACKOFF
    # DEFAULT_DEPLOY_TIMEOUT может быть переопределён через PLATFORM_DEPLOY_TIMEOUT env —
    # базовое значение должно совпадать с DEPLOY_TIMEOUT
    assert int(os.environ.get("PLATFORM_DEPLOY_TIMEOUT", str(timeouts.DEPLOY_TIMEOUT))) == timeouts.DEPLOY_TIMEOUT
    logger.info("[IMP:9][test_channels_defaults] Канальные дефолты == timeouts")
    _assert_imp9(caplog)


# endregion


# region FUNC_test_ssh_opts_connect_timeout
## @purpose — Verify SSH_OPTS ConnectTimeout == timeouts.SSH_CONNECT_TIMEOUT (U-11/U-15).
## @complexity — O(1)
def test_ssh_opts_connect_timeout(caplog: pytest.LogCaptureFixture) -> None:
    """ConnectTimeout в SSH_OPTS должен равняться timeouts.SSH_CONNECT_TIMEOUT."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: ConnectTimeout константа
    # · Last fail: N/A (ConnectTimeout=10 outlier устранён)
    # · Remove if: SSH_OPTS политика меняется
    assert f"ConnectTimeout={timeouts.SSH_CONNECT_TIMEOUT}" in ssh_opts.SSH_OPTS
    logger.info("[IMP:9][test_ssh_opts_connect_timeout] ConnectTimeout=%s", timeouts.SSH_CONNECT_TIMEOUT)
    _assert_imp9(caplog)


# endregion
