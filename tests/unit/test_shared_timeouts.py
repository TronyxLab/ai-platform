# GREP_SUMMARY: test-shared-timeouts timeouts constants defaults inspect-signature compose-up pull build healthcheck ssh-connect deploy retry-backoff falsifiable
# STRUCTURE: ▶ test_constants_values → test_shared_function_defaults (inspect.signature) → test_channels_defaults → ⎋ (test_ssh_opts_connect_timeout удалён 168: 1:1 дубль test_shared_ssh_opts.py)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/timeouts.py — единый реестр таймаутов (U-11).
##           Фальсифицируемая проверка: дефолты shared-функций == константам timeouts
##           (через inspect.signature — если кто-то хардкодит литерал вместо константы, тест RED).
## @scope    Tests: значения констант, дефолты docker_compose_*/healthcheck_poll/retry_pull,
##           канальные дефолты (channels). ConnectTimeout-связка с ssh_opts покрыта
##           в test_shared_ssh_opts.py::test_connect_timeout_from_timeouts (1:1 дубль удалён, 168).
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

import pytest

from core.internal.shared import timeouts
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
from tests.helpers.gate_helpers import assert_ldd_imp9

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


def _default_param(func, name: str):
    """Извлечь default параметра функции через inspect.signature.

    ## @purpose — Фальсифицируемая проверка: default должен быть константой timeouts,
    ##            а не литералом (иначе сравнение с константой провалится).
    """
    return inspect.signature(func).parameters[name].default


# T2.16a: _assert_imp9 консолидирован в gate_helpers.assert_ldd_imp9
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
    assert timeouts.DEPLOY_TIMEOUT == 900  # v1.0.1: 600→900 — холодный φ8 deploy-modules
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
    # RC 121 (долг 119 C2): watchdog-домен удалён вместе с подсистемой — констант WATCHDOG_* больше нет
    # B6 (DevPlan 119): [8080,8000] → [3000,4000,8000,8080,9000] — покрытие реальных compose-портов
    assert timeouts.PROJECT_HEALTHCHECK_PORTS == [3000, 4000, 8000, 8080, 9000]
    # B7 (DevPlan 119): +converge-домен
    assert timeouts.CONVERGE_DOCKER_TIMEOUT == 30
    assert timeouts.FILE_OP_TIMEOUT == 15
    # W1-A1 (план 170): +системные/lifecycle-команды (AMBER-зачистка research-D §D1)
    assert timeouts.SYSTEM_CMD_TIMEOUT == 60
    assert timeouts.LIFECYCLE_CMD_TIMEOUT == 120
    logger.info("[IMP:9][test_constants_values] Все %d констант канонизированы", 26)
    assert_ldd_imp9(caplog)


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
    assert_ldd_imp9(caplog)


# endregion


# region FUNC_test_channels_defaults
## @purpose — Verify канальные дефолты (channels) — из timeouts (T1/T7).
## @complexity — O(1)
def test_channels_defaults(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """Канальные дефолты (channels) должны импортироваться из timeouts."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: DEFAULT_DEPLOY_TIMEOUT/RETRY_COUNT/RETRY_BACKOFF
    # · Last fail: N/A (T1.3 — константы канала перенесены в timeouts)
    # · Remove if: канальные дефолты меняются
    # W4a (DevPlan 160 T4.1): import-time env-чтение DEFAULT_DEPLOY_TIMEOUT УБРАНО —
    # константа стала ЧИСТОЙ (= timeouts.DEPLOY_TIMEOUT); env-оверрайд PLATFORM_DEPLOY_TIMEOUT
    # резолвится ЛЕНИВО на конструировании канала (AppConfig.from_env().deploy_timeout).
    # importlib.reload-механизм env-детерминизма (D-11, DevPlan 125 T13) заменён проверкой
    # ленивого резолва на инстансе канала — поведение env-оверрайда сохранено.
    from core.internal.deploy import channels

    assert channels.DEFAULT_RETRY_COUNT == timeouts.RETRY_COUNT
    assert timeouts.RETRY_BACKOFF_SECONDS[0] == channels.DEFAULT_RETRY_BACKOFF
    # (а) константа — чистая (env отсутствует → базовое значение == DEPLOY_TIMEOUT)
    monkeypatch.delenv("PLATFORM_DEPLOY_TIMEOUT", raising=False)
    assert channels.DEFAULT_DEPLOY_TIMEOUT == timeouts.DEPLOY_TIMEOUT
    # (б) env-оверрайд на конструировании канала (значение не из dev-машины)
    monkeypatch.setenv("PLATFORM_DEPLOY_TIMEOUT", "12345")
    ch_override = channels.LocalChannel()
    assert ch_override.timeout == 12345
    # (в) восстановить каноническое состояние (без cross-test pollution)
    monkeypatch.delenv("PLATFORM_DEPLOY_TIMEOUT", raising=False)
    ch_default = channels.LocalChannel()
    assert ch_default.timeout == timeouts.DEPLOY_TIMEOUT
    logger.info("[IMP:9][test_channels_defaults] Канальные дефолты == timeouts (env-детерминизм, W4a)")
    assert_ldd_imp9(caplog)


# endregion
