# GREP_SUMMARY: containers, docker-inspect, R4-fail-fast, container-running, live-check, module-container, recovered
# STRUCTURE: ◇ _module_container_running(platform_services_result + docker inspect) → ◇ started? True | ◇ failed? inspect recover | ◇ unknown? poll 120s → ⎋ bool

# region MODULE_CONTRACT
## @purpose  Live container verification helper (R4 fail-fast): resolves whether a module's
##           test container is actually running, accounting for restart: unless-stopped
##           recovery semantics (module in failed list may have recovered after first
##           `--wait` timeout). Extracted from smoke.py (DevPlan 170 W8).
## @scope    Consumed by tests/conftest.py re-export (conftest-level public API) and
##           test files via `from conftest import _module_container_running`
##           (test_smoke_langfuse, test_smoke_litellm, test_smoke_hermes).
## @invariants
##   - container_name указывает на test-контейнер (с -test суффиксом)
##   - Модуль в started → True без docker inspect (контейнер стартовал успешно)
##   - Модуль в failed → docker inspect: True если контейнер фактически running
##     (recovered после --wait timeout), иначе False
##   - Модуль НЕ в списках (волна ещё выполняется в фоновом потоке) → poll docker inspect
##     до 120s — устойчивость вместо ложного False (142 W8 R13)
##   - Модуль нигде и никогда не стартовал (нет requires_docker маркера) → False
## @rationale  Липкая failed-метка не учитывает recover-семантику restart-политик.
##             R4 должен фейлить только при реальном отсутствии контейнера.
## @changes    CREATED: 2026-08-15 | DevPlan 170 W8: вынесен из tests/_conftest/smoke.py
##             (R4_HELPER region, TRAP[BUG] 2026-07-23 сохранён 1:1)
# endregion MODULE_CONTRACT

import logging
import subprocess as _sp
import time as _time

logger = logging.getLogger(__name__)


# region R4_HELPER
## @purpose — Live container check для R4 fail-fast. Когда модуль в failed списке
##            platform_services, это может быть ложноположительным из-за restart:
##            unless-stopped — контейнер может восстановиться после первого --wait
##            timeout. Делаем docker inspect для верификации фактического состояния.
## @rationale — Липкая failed-метка не учитывает recover-семантику restart-политик.
##              R4 должен фейлить только при реальном отсутствии контейнера.
## @invariants
##   - container_name указывает на test-контейнер (с -test суффиксом)
##   - Возвращает True если контейнер запущен, False если отсутствует/не запущен
##   - logging.getLogger('conftest') для LDD-логов


def _module_container_running(
    platform_services_result: dict[str, list[str]],
    module_name: str,
    container_name: str,
    logger: logging.Logger,
    timeout: int = 10,
) -> bool:
    """Verify module test container is actually running.

    ## @purpose — If module is in failed list (--wait timeout), check live
    ##            container state via docker inspect. restart: unless-stopped
    ##            may have recovered the container after the initial timeout.
    ## @io — ⇥ platform_services_result, module_name, container_name, logger
    ##       → ⎋ bool (True if running)
    ## @complexity — O(1) — single docker inspect call
    """
    # ⚠️ TRAP[BUG] · 2026-07-23 · MED · False-positive when started=[] AND failed=[]
    # · Symptom: _module_container_running returns True for module that was never started
    # ·   because platform_services was a no-op (missing @requires_docker marker).
    # ·   Returns True when module_name is not in failed list, but also not in started list.
    # · Fix: check both lists — module must be in started (or recovered via docker inspect
    # ·   if in failed). Neither → container was never started → return False.
    started = platform_services_result.get("started", [])
    failed = platform_services_result.get("failed", [])
    if module_name in started:
        return True  # module started successfully
    if module_name not in failed:
        # Module not yet in started/failed — волна ещё выполняется в фоновом потоке
        # (wave-pipeline: поздние волны стартуют параллельно с тестами; ресурсная гонка
        # Docker Desktop → модуль может быть НЕ в списках на момент проверки, 142 W8 R13).
        # Poll docker inspect до WAVE_WAIT_TIMEOUT — устойчивость вместо ложного False.
        deadline = _time.monotonic() + 120
        while _time.monotonic() < deadline:
            try:
                r = _sp.run(
                    ["docker", "inspect", "-f", "{{.State.Running}}", container_name],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
                if r.returncode == 0 and r.stdout.strip() == "true":
                    logger.info(
                        "[IMP:8][R4][%s] Module '%s' container '%s' detected running (wave in progress)",
                        module_name,
                        module_name,
                        container_name,
                    )
                    return True
            except (_sp.TimeoutExpired, OSError):
                pass
            _time.sleep(5)
        logger.error(
            "[IMP:9][R4][%s] Module '%s' was never started by platform_services "
            "(missing @pytest.mark.requires_docker on test?)",
            module_name,
            module_name,
        )
        return False

    # Module in failed list — check if container actually recovered
    try:
        r = _sp.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container_name],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if r.returncode == 0 and r.stdout.strip() == "true":
            logger.warning(
                "[IMP:8][R4][%s] Module in failed list but container '%s' IS running"
                " — recovered after first --wait timeout",
                module_name,
                container_name,
            )
            return True
    except (_sp.TimeoutExpired, OSError):
        pass

    logger.error(
        "[IMP:9][R4][%s] Container '%s' is NOT running — module truly failed",
        module_name,
        container_name,
    )
    return False


# endregion R4_HELPER
