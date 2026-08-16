#!/usr/bin/env python3
# GREP_SUMMARY: healthcheck-runner, readiness, liveness, invoke-module-interface, polling, retry, D1, docker-orchestrator-decomposition
# STRUCTURE: ▶ wait_for_readiness [N×attempts → invoke_healthcheck_full(readiness)] → ⎋ bool │ ▶ run_healthcheck [N×retries → invoke_healthcheck_full(liveness)] → ⎋ bool │ ▶ _invoke_healthcheck_full → module_interface_invoke (C5)
# region MODULE_CONTRACT
## @purpose  Healthcheck-инвокации docker-модулей — экстракция из docker_orchestrator.py
##           (DevPlan 118 D1, строки 1112-1260): wait_for_readiness, run_healthcheck,
##           _invoke_healthcheck, _invoke_healthcheck_full.
## @scope    bootstrap/deploy — вызывается parallel_runner.deploy_docker_group (fork-цикл HC)
##           и docker_orchestrator CLI (--action wait|healthcheck). Делегирует в
##           shared/module_interface.invoke (DevPlan 118 C5 — единая bash-обёртка).
## @invariants
##   1. wait_for_readiness — poll до max_attempts с interval_sec; timeout non-fatal (False, не raise)
##   2. run_healthcheck — retry до max_retries; первый fail логирует DIAG; timeout non-fatal (False)
##   3. _invoke_healthcheck_full — единственная точка вызова module_interface.invoke с
##      HEALTHCHECK_POLL_TIMEOUT (канон shared/timeouts, DevPlan 116 B5 T1)
##   4. Никаких локальных timeout-литералов — всё из shared/timeouts (гейт timeout_literals)
## @rationale DevPlan 118 D1 (AC-D1): docker_orchestrator 1397 LOC → оркестратор <900 LOC.
##            Healthcheck-блок (148 LOC) — независимая ответственность, отдельный модуль.
## @changes  2026-08-02 | DevPlan 118 D1 — экстракция из docker_orchestrator.py (чистая, без смены контрактов)
# endregion MODULE_CONTRACT

import logging
import time

from core.internal.shared.module_interface import invoke as module_interface_invoke
from core.internal.shared.timeouts import (
    HEALTHCHECK_POLL_INTERVAL,
    HEALTHCHECK_POLL_MAX_RETRIES,
    HEALTHCHECK_POLL_TIMEOUT,
)

logger = logging.getLogger(__name__)

# Readiness-политика (best-effort): 15 попыток × 2s = 30s окно
DEFAULT_READINESS_MAX_ATTEMPTS = 15
DEFAULT_READINESS_INTERVAL_SEC = 2
# Healthcheck retry-политика — единый реестр timeouts (DevPlan 117 D32/D34):
# HEALTHCHECK_POLL_MAX_RETRIES×HEALTHCHECK_POLL_INTERVAL = 60s окно (HEALTHCHECK_POLL_TIMEOUT=60 канон)
DEFAULT_HEALTHCHECK_MAX_RETRIES = HEALTHCHECK_POLL_MAX_RETRIES
DEFAULT_HEALTHCHECK_RETRY_INTERVAL = HEALTHCHECK_POLL_INTERVAL


# region FUNC_wait_for_readiness
## @purpose  Poll module readiness via invoke_module_interface healthcheck readiness.
##           Retries up to max_attempts times with interval_sec between attempts.
##           Timeout is non-fatal (logged WARN) — container may still be starting.
## @io       ⇥ module_name: str, max_attempts: int, interval_sec: int
##           ⎋ bool: True if readiness check passed
## @complexity 2 — polling loop with subprocess calls
## @invariants
##   - Uses module_interface.invoke (shared, C5) to call module/healthcheck.sh readiness
##   - Non-zero return from healthcheck.sh means "not ready yet" — retry
##   - Timeout returns False but does NOT raise — caller decides next action
def wait_for_readiness(
    module_name: str,
    max_attempts: int = DEFAULT_READINESS_MAX_ATTEMPTS,
    interval_sec: int = DEFAULT_READINESS_INTERVAL_SEC,
) -> bool:
    logger.info(
        "[IMP:7][wait_for_readiness][start] Waiting for %s readiness (%d attempts, %ds interval)",
        module_name,
        max_attempts,
        interval_sec,
    )
    for attempt in range(max_attempts):
        if invoke_healthcheck(module_name, "readiness"):
            logger.info(
                "[IMP:9][wait_for_readiness][ready] Module %s ready after %d attempts",
                module_name,
                attempt + 1,
            )
            return True
        if attempt < max_attempts - 1:
            time.sleep(interval_sec)

    logger.warning(
        "[IMP:5][wait_for_readiness][timeout] Readiness timeout for %s after %d attempts — continuing (non-fatal)",
        module_name,
        max_attempts,
    )
    return False


# endregion FUNC_wait_for_readiness


# region FUNC_run_healthcheck
## @purpose  Run healthcheck for a module via invoke_module_interface healthcheck liveness.
##           Retries up to max_retries times with retry_interval between attempts.
##           Failure is non-fatal (logged WARN) — module may still function.
## @io       ⇥ module_name: str, install_type: str, max_retries: int, retry_interval: int
##           ⎋ bool: True if healthcheck passed
## @complexity 2 — retry loop with subprocess calls
## @invariants
##   - Uses module_interface.invoke (shared, C5) to call module/healthcheck.sh liveness
##   - First failure logs DIAG with healthcheck stderr for debugging
##   - Failure after max_retries returns False — caller decides severity
def run_healthcheck(
    module_name: str,
    install_type: str,
    max_retries: int = DEFAULT_HEALTHCHECK_MAX_RETRIES,
    retry_interval: int = DEFAULT_HEALTHCHECK_RETRY_INTERVAL,
) -> bool:
    logger.info("[IMP:7][run_healthcheck][start] Healthcheck for %s (%s)", module_name, install_type)
    last_output = ""
    for attempt in range(max_retries):
        success, output = invoke_healthcheck_full(module_name, "liveness")
        if success:
            logger.info(
                "[IMP:9][run_healthcheck][pass] Healthcheck PASS for %s (attempt %d/%d)",
                module_name,
                attempt + 1,
                max_retries,
            )
            return True

        last_output = output
        if attempt == 0:
            logger.info("[IMP:8][run_healthcheck][diag] Healthcheck stderr: %s", output[:300] if output else "(none)")

        if attempt < max_retries - 1:
            logger.info(
                "[IMP:8][run_healthcheck][retry] Healthcheck attempt %d/%d failed for %s, retrying in %ds",
                attempt + 1,
                max_retries,
                module_name,
                retry_interval,
            )
            time.sleep(retry_interval)

    logger.warning(
        "[IMP:5][run_healthcheck][fail] Healthcheck FAILED for %s after %d attempts (last: %s)",
        module_name,
        max_retries,
        last_output[:200] if last_output else "",
    )
    return False


# endregion FUNC_run_healthcheck


# region FUNC_invoke_healthcheck
## @purpose  Call invoke_module_interface for healthcheck (readiness or liveness) via bash.
##           Returns True on zero exit code.
## @io       ⇥ module_name: str, check_type: str ("readiness" | "liveness")
##           ⎋ bool: True if check passed
## @complexity 1 — single subprocess call
## @invariants
##   - stderr is captured and logged at IMP:8 on failure
def invoke_healthcheck(module_name: str, check_type: str) -> bool:
    success, _ = invoke_healthcheck_full(module_name, check_type)
    return success


# endregion FUNC_invoke_healthcheck


# region FUNC_invoke_healthcheck_full
## @purpose  Call invoke_module_interface for healthcheck via shared/module_interface.invoke (C5).
##           Returns (bool, str) tuple with success flag and stderr output.
## @io       ⇥ module_name: str, check_type: str ("readiness" | "liveness")
##           ⎋ tuple[bool, str] — (success, stderr_output)
## @complexity 1 — single subprocess call (делегирование в shared, DevPlan 118 C5)
def invoke_healthcheck_full(module_name: str, check_type: str) -> tuple[bool, str]:
    # C5: единая bash-обёртка shared/module_interface.invoke (timeout — канон HEALTHCHECK_POLL_TIMEOUT)
    return module_interface_invoke(
        module_name,
        "healthcheck",
        check_type,
        timeout=HEALTHCHECK_POLL_TIMEOUT,
    )


# endregion FUNC_invoke_healthcheck_full
