#!/usr/bin/env python3
# GREP_SUMMARY: deploy-modules-packages, test, static-audit, parallel-deploy, docker-group, image-check, failure-isolation, os-fork
# STRUCTURE: ▶ test_parallel_healthcheck (static grep parallel_runner.py os.fork + run_healthcheck) → ▶ test_parallel_deploy_failure_isolates_modules (tuple[int,int,list[str]] contract) → ▶ test_image_exists_short_circuit (docker_orchestrator.py _check_image_exists) → ⎋ LDD [IMP:9]
# region MODULE_CONTRACT
## @purpose  Static audit пакетного (docker) домена деплоя: parallel_runner.deploy_docker_group
##           (параллельный healthcheck, изоляция сбоя модуля) и docker_orchestrator._check_image_exists
##           (short-circuit docker pull). Сплит test_deploy_modules.py (DevPlan 139 W3 T6):
##           пакеты-подобласть выделена из 62KB-монолита.
## @scope    S4: deploy_docker_group — параллельный healthcheck (os.fork + run_healthcheck).
##           W4-E5: deploy_docker_group — изоляция сбоя (tuple[int, int, list[str]]).
##           W4-E5: _check_image_exists — short-circuit pull (docker manifest/image inspect).
## @invariants
##   - Все тесты — static audit (чтение исходников как текст, _extract_python_func)
##   - LDD: _assert_ldd_trajectory (≥1 IMP:9)
##   - Контракты W4-E1 extraction (parallel_runner/docker_orchestrator) не нарушены
## @rationale  Группировка по бизнес-подобласти (docker-пакеты) — файл легче читать;
##             coverage W4-E5 страховок сохранён (AC W3e).
## @changes  2026-08-05 | DevPlan 139 W3 T6 — вынесен из test_deploy_modules.py
# endregion MODULE_CONTRACT

import logging

import pytest

from tests.helpers.deploy_modules_audit import DEPLOY_PYTHON_DIR, _assert_ldd_trajectory, _extract_python_func

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# S4: Parallel healthcheck
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_parallel_healthcheck
## @purpose  Static audit: deploy_docker_group() в parallel_runner.py имеет параллельный
##           healthcheck (os.fork + run_healthcheck). DevPlan 118 D1: deploy_docker_group
##           переехал из docker_orchestrator.py в parallel_runner.py.
## @io       ⇥ caplog, DEPLOY_PYTHON_DIR/parallel_runner.py → ⎋ None
## @complexity 1 — static grep on file content


@pytest.mark.static_audit
def test_parallel_healthcheck(caplog) -> None:
    """deploy_docker_group: os.fork() параллельность + run_healthcheck на каждый модуль."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_parallel_healthcheck] Reading parallel_runner.py ...")
    content = _extract_python_func(DEPLOY_PYTHON_DIR / "parallel_runner.py", "deploy_docker_group")

    assert "os.fork()" in content or "os.fork" in content, (
        "S4 violation: os.fork for parallel healthcheck not found in deploy_docker_group (parallel_runner.py)"
    )
    logger.info("[IMP:9][test_parallel_healthcheck] os.fork() parallelism in deploy_docker_group OK")

    assert "run_healthcheck" in content, "S4 violation: run_healthcheck not called in deploy_docker_group"
    logger.info("[IMP:9][test_parallel_healthcheck] run_healthcheck invocation in deploy_docker_group OK")

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: S4 parallel healthchecks must replace sequential healthchecks
# · Remove if: healthcheck strategy changes fundamentally
# endregion FUNC_test_parallel_healthcheck


# region FUNC_test_parallel_deploy_failure_isolates_modules
## @purpose  W4-E5: deploy_docker_group изолирует сбой 1 модуля в группе (failure isolation).
## @io       caplog → ⎋ None
## @complexity 1 — static audit of function signature + docstring


@pytest.mark.static_audit
def test_parallel_deploy_failure_isolates_modules(caplog) -> None:
    """deploy_docker_group: return tuple[int, int, list[str]] + os.fork (per-module isolation)."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_parallel_deploy_failure] START — verifying deploy_docker_group in parallel_runner.py")

    content = _extract_python_func(DEPLOY_PYTHON_DIR / "parallel_runner.py", "deploy_docker_group")

    assert "def deploy_docker_group(" in content, (
        "W4-E5 violation: deploy_docker_group() not found in parallel_runner.py"
    )
    logger.info("[IMP:9][test_parallel_deploy_failure] deploy_docker_group() declared OK")

    assert "tuple[int, int, list[str]]" in content or "tuple[int, int, list" in content, (
        "W4-E5 violation: deploy_docker_group must return tuple[int, int, list[str]] (failure isolation contract)"
    )
    logger.info("[IMP:9][test_parallel_deploy_failure] Return type tuple[int,int,list[str]] OK (failure isolation)")

    assert "os.fork()" in content or "os.fork" in content, (
        "W4-E5 violation: deploy_docker_group must use os.fork() for per-module isolation"
    )
    logger.info("[IMP:9][test_parallel_deploy_failure] os.fork() per-module isolation OK")

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: W4-E5 parallel deploy failure isolation (1 of N fails, others succeed)
# · Scenario: 3-module group where redis fails → deployed=2, failed=1, FAILED_MODULE_NAMES=[redis]
# · Last fail: N/A (W4-E5 baseline)
# · Remove if: deploy_docker_group transactional rollback (W5-E1) changes failure semantics
# endregion FUNC_test_parallel_deploy_failure_isolates_modules


# ══════════════════════════════════════════════════════════════════════════════
# W4-E5: Image exists short-circuit
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_image_exists_short_circuit
## @purpose  W4-E5: _check_image_exists в docker_orchestrator.py short-circuit'ит docker pull.
## @io       caplog → ⎋ None
## @complexity 1 — static grep


@pytest.mark.static_audit
def test_image_exists_short_circuit(caplog) -> None:
    """_check_image_exists: manifest/image inspect + return True (short-circuit pull)."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_image_exists_short_circuit] START — checking docker_orchestrator.py")
    content = _extract_python_func(DEPLOY_PYTHON_DIR / "docker_orchestrator.py", "_check_image_exists")

    assert "def _check_image_exists(" in content, (
        "W4-E5 violation: _check_image_exists() function not found in docker_orchestrator.py"
    )
    logger.info("[IMP:9][test_image_exists_short_circuit] _check_image_exists() declared OK")

    assert "docker manifest inspect" in content or "docker image inspect" in content or "docker images" in content, (
        "W4-E5 violation: _check_image_exists must use docker manifest/image inspect for cache/registry check"
    )
    logger.info("[IMP:9][test_image_exists_short_circuit] docker manifest/image inspect present")

    assert "return True" in content, (
        "W4-E5 violation: _check_image_exists must return True (short-circuit) when image found"
    )
    logger.info("[IMP:9][test_image_exists_short_circuit] short-circuit return True present")

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: W4-E5 _check_image_exists short-circuits pull on cached image
# · Scenario: docker image inspect succeeds → pull skipped (idempotent, saves bandwidth)
# · Last fail: N/A (W4-E5 baseline)
# · Remove if: image cache check moves to docker_orchestrator.py (then point test at new module)
# endregion FUNC_test_image_exists_short_circuit
