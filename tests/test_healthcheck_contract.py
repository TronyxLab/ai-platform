#!/usr/bin/env python3
# GREP_SUMMARY: contract tests — healthcheck hardening: port 9119, check_docker_health, nginx docker, litellm healthcheck
# STRUCTURE: 4 tests → file content analysis of healthcheck.sh scripts

import logging
import os

import pytest

from tests.conftest import ldd_trajectory

# region MODULE_CONTRACT
## @purpose  Регрессионные контрактные тесты для healthcheck-фиксов (StatusReport §2.2)
## @scope    Четыре теста:
##           1. hermes-agent healthcheck использует порт 9119 (не 8080)
##           2. nginx healthcheck использует check_docker_health + MODE=deep (Docker module)
##           3. litellm healthcheck содержит check_docker_health (после разделения observability)
##           4. nginx MODE=deep содержит docker exec curl с HTTP-верификацией
## @invariants
##   - Тесты анализируют только содержимое файлов (subprocess не используется)
##   - Не требуют Docker, nginx или других внешних зависимостей
##   - IMP:9 логирование для LDD-трассировки
## @rationale Фиксирует исправленные дефекты: порт 8080→9119, system→docker module,
##            observability→5 модулей (litellm получил собственный healthcheck)
# endregion MODULE_CONTRACT

logger = logging.getLogger(__name__)

CORE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "core",
    "modules",
)


def _read_healthcheck(module_name: str) -> str:
    """Читает healthcheck.sh указанного модуля."""
    path = os.path.join(CORE_DIR, module_name, "healthcheck.sh")
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ═══════════════════════════════════════════════════════════
# Test 1: hermes-agent healthcheck → port 9119
# ═══════════════════════════════════════════════════════════


@ldd_trajectory
def test_hermes_healthcheck_uses_port_9119(caplog: pytest.LogCaptureFixture) -> None:
    """
    Проверяет, что hermes-agent/healthcheck.sh использует порт 9119, а не 8080.
    Фиксирует TRAP[INCIDENT] 2026-07-10: порт 8080 → 9119.

    Проверяет только код (не комментарии): AGENT_URL="http://127.0.0.1:9119".
    Строки с '8080' в комментариях допустимы (документация фикса).
    """

    content = _read_healthcheck("hermes-agent")

    # ── Проверка: AGENT_URL в коде (не в комментариях) содержит 9119 ──
    # Извлекаем строки, не начинающиеся с #
    code_lines = [line for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]
    code_text = "\n".join(code_lines)

    has_9119_in_code = "9119" in code_text
    has_8080_in_code = "8080" in code_text

    logger.info("[IMP:7][healthcheck-hermes] AGENT_URL in code contains '9119': %s", has_9119_in_code)
    logger.info("[IMP:7][healthcheck-hermes] AGENT_URL in code contains '8080': %s", has_8080_in_code)

    # Ищем конкретные строки для детального лога
    for line_num, line in enumerate(content.splitlines(), 1):
        if "AGENT_URL" in line or "9119" in line or "8080" in line or "localhost" in line.lower():
            logger.info(
                "[IMP:8][healthcheck-hermes] L%d: %s",
                line_num,
                line.strip(),
            )

    assert has_9119_in_code, "[IMP:9][healthcheck-hermes] FAIL: AGENT_URL in code does not contain port 9119"
    assert not has_8080_in_code, (
        "[IMP:9][healthcheck-hermes] FAIL: Port 8080 found in active code (not comments) — port regression risk"
    )

    logger.info("[IMP:9][healthcheck-hermes] PASS: AGENT_URL uses port 9119, not 8080")


# ═══════════════════════════════════════════════════════════
# Test 2: nginx healthcheck → check_docker_health (Docker module)
# ═══════════════════════════════════════════════════════════


@ldd_trajectory
def test_nginx_docker_healthcheck(caplog: pytest.LogCaptureFixture) -> None:
    """
    Проверяет, что nginx/healthcheck.sh использует check_docker_health (docker module).
    После конвертации nginx из system в docker-модуль, healthcheck использует
    check_docker_health и MODE=deep (docker exec curl).
    """
    content = _read_healthcheck("nginx")

    has_check_docker = "check_docker_health" in content
    has_deep_mode = "MODE=deep" in content or '"deep"' in content or "'deep'" in content

    logger.info(
        "[IMP:7][healthcheck-nginx] Contains check_docker_health: %s",
        has_check_docker,
    )
    logger.info(
        "[IMP:7][healthcheck-nginx] Contains deep mode: %s",
        has_deep_mode,
    )

    for line_num, line in enumerate(content.splitlines(), 1):
        if "check_docker_health" in line or "deep" in line.lower():
            logger.info(
                "[IMP:8][healthcheck-nginx] L%d: %s",
                line_num,
                line.strip(),
            )

    assert has_check_docker, (
        "[IMP:9][healthcheck-nginx] FAIL: check_docker_health not found — nginx should use Docker healthcheck"
    )
    assert has_deep_mode, (
        "[IMP:9][healthcheck-nginx] FAIL: MODE=deep not found — nginx should support deep HTTP verification"
    )

    logger.info("[IMP:9][healthcheck-nginx] PASS: nginx healthcheck uses Docker pattern + deep mode")


# ═══════════════════════════════════════════════════════════
# Test 3: litellm healthcheck → check_docker_health
# ═══════════════════════════════════════════════════════════


@ldd_trajectory
def test_litellm_healthcheck_fallback(caplog: pytest.LogCaptureFixture) -> None:
    """
    Проверяет, что litellm/healthcheck.sh содержит check_docker_health fallback.
    После разделения observability → 5 модулей, litellm получил собственный healthcheck.
    """
    content = _read_healthcheck("litellm")

    has_check_docker = "check_docker_health" in content
    has_litellm_ref = "litellm" in content.lower()

    logger.info(
        "[IMP:7][healthcheck-litellm] Contains check_docker_health: %s",
        has_check_docker,
    )
    logger.info(
        "[IMP:7][healthcheck-litellm] References litellm: %s",
        has_litellm_ref,
    )

    for line_num, line in enumerate(content.splitlines(), 1):
        if "check_docker_health" in line or "litellm" in line.lower():
            logger.info(
                "[IMP:8][healthcheck-litellm] L%d: %s",
                line_num,
                line.strip(),
            )

    assert has_check_docker, (
        "[IMP:9][healthcheck-litellm] FAIL: check_docker_health not found — Docker healthcheck for Litellm is missing"
    )

    logger.info("[IMP:9][healthcheck-litellm] PASS: check_docker_health present")


# ═══════════════════════════════════════════════════════════
# Test 4: nginx healthcheck → MODE=deep HTTP verification
# ═══════════════════════════════════════════════════════════


@ldd_trajectory
def test_nginx_healthcheck_deep_http(caplog: pytest.LogCaptureFixture) -> None:
    """
    Проверяет, что nginx/healthcheck.sh содержит MODE=deep с HTTP-верификацией
    через docker exec curl (Docker-only модуль, без systemd).
    """
    content = _read_healthcheck("nginx")

    # ── Pattern 1: MODE=deep dispatch ──
    has_deep_mode = "MODE=deep" in content or '"deep"' in content or "'deep'" in content
    logger.info("[IMP:7][healthcheck-nginx-deep] MODE=deep present: %s", has_deep_mode)

    # ── Pattern 2: docker exec curl for HTTP check ──
    has_docker_exec_curl = "docker exec" in content and "curl" in content
    logger.info("[IMP:7][healthcheck-nginx-deep] docker exec curl: %s", has_docker_exec_curl)

    # ── Pattern 3: localhost:80 HTTP port ──
    has_localhost_80 = "localhost:80" in content
    logger.info("[IMP:7][healthcheck-nginx-deep] localhost:80 check: %s", has_localhost_80)

    # ── Pattern 4: check_docker_health for liveness ──
    has_check_docker = "check_docker_health" in content
    logger.info("[IMP:7][healthcheck-nginx-deep] check_docker_health: %s", has_check_docker)

    # ── Pattern 5: --max-time (non-blocking curl) ──
    has_max_time = "--max-time" in content
    logger.info("[IMP:7][healthcheck-nginx-deep] --max-time (non-blocking): %s", has_max_time)

    # ── Log relevant lines ──
    for line_num, line in enumerate(content.splitlines(), 1):
        if any(
            kw in line
            for kw in (
                "deep",
                "docker exec",
                "curl",
                "localhost:80",
                "check_docker_health",
                "max-time",
            )
        ):
            logger.info("[IMP:8][healthcheck-nginx-deep] L%d: %s", line_num, line.strip())

    # ── Assertions ──
    assert has_deep_mode, (
        "[IMP:9][healthcheck-nginx-deep] FAIL: MODE=deep not found — "
        "nginx healthcheck should support deep HTTP verification"
    )
    assert has_docker_exec_curl, (
        "[IMP:9][healthcheck-nginx-deep] FAIL: docker exec curl not found — "
        "nginx deep check should verify HTTP inside container"
    )
    assert has_localhost_80, (
        "[IMP:9][healthcheck-nginx-deep] FAIL: localhost:80 check not found — "
        "nginx HTTP verification on port 80 is missing"
    )
    assert has_check_docker, (
        "[IMP:9][healthcheck-nginx-deep] FAIL: check_docker_health not found — "
        "nginx liveness check via Docker is missing"
    )

    logger.info("[IMP:9][healthcheck-nginx-deep] PASS: nginx healthcheck has MODE=deep + docker exec curl")
