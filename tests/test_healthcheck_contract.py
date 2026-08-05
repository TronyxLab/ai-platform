#!/usr/bin/env python3
# GREP_SUMMARY: contract tests — healthcheck hardening: port 9119, check_docker_health, nginx docker, litellm healthcheck
# STRUCTURE: 4 tests → file content analysis of healthcheck.sh scripts

import logging
import os
import subprocess
from pathlib import Path

import pytest

from tests.conftest import ldd_trajectory

# region MODULE_CONTRACT
## @purpose  Контрактные тесты healthcheck-фасада: регрессии (StatusReport §2.2) +
##           поведение shell-библиотеки core/lib/healthcheck.sh. Консолидировано
##           (DevPlan 139 W3 T1, 4→2): test_lib_healthcheck.py влит сюда.
## @scope    Фасадный слой healthcheck (shell):
##
##           1. hermes-agent healthcheck использует порт 9119 (не 8080)
##           2. nginx healthcheck использует check_docker_health + MODE=deep (Docker module)
##           3. litellm healthcheck содержит check_docker_health (после разделения observability)
##           4. nginx MODE=deep содержит docker exec curl с HTTP-верификацией
##           5. core/lib/healthcheck.sh: check_docker_health (healthy/unhealthy/starting/not-found)
##           6. core/lib/healthcheck.sh: check_http (200/404/301-multi)
##           7. R5 negative: poll_until_healthy/poll_docker_health удалены (118 B6)
## @invariants
##
##   - Модульные sh-тесты анализируют только содержимое файлов (subprocess не используется)
##   - Lib-тесты: mock docker/curl через PATH-инъекцию в tmp_path (Zero Hardcode Rule)
##   - Lib-тесты: LDD через stderr (bash-логи минуют Python logging) — @ldd_trajectory неприменим
##   - Не требуют Docker, nginx или других внешних зависимостей
##   - Канон D5 (running AND healthy|""|none) — parity с Python-каноном healthcheck_poller
## @rationale Фиксирует исправленные дефекты: порт 8080→9119, system→docker module,
##            observability→5 модулей (litellm получил собственный healthcheck),
##            check_http заменил docker exec curl (DRIFT-H4). S5: unit/test_healthcheck_poller.py
##            (канон), unit/test_hermes_healthcheck.py, unit/test_project_healthcheck.py —
##            отдельные домены, НЕ консолидируются.
## @changes  2026-08-05 | DevPlan 139 W3 T1 — test_lib_healthcheck.py влит (файл удалён)
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
    через check_http (замена docker exec curl per DevPlan 083 DRIFT-H4).
    """
    content = _read_healthcheck("nginx")

    # ── Pattern 1: MODE=deep dispatch ──
    has_deep_mode = "MODE=deep" in content or '"deep"' in content or "'deep'" in content
    logger.info("[IMP:7][healthcheck-nginx-deep] MODE=deep present: %s", has_deep_mode)

    # ── Pattern 2: check_http for HTTP check (was docker exec curl — DRIFT-H4 fix) ──
    has_check_http = "check_http" in content
    logger.info("[IMP:7][healthcheck-nginx-deep] check_http present: %s", has_check_http)

    # ── Pattern 3: localhost:80 or 127.0.0.1:80 HTTP port ──
    has_port_80 = "localhost:80" in content or "127.0.0.1:80" in content
    logger.info("[IMP:7][healthcheck-nginx-deep] port 80 check: %s", has_port_80)

    # ── Pattern 4: check_docker_health for liveness ──
    has_check_docker = "check_docker_health" in content
    logger.info("[IMP:7][healthcheck-nginx-deep] check_docker_health: %s", has_check_docker)

    # ── Log relevant lines ──
    for line_num, line in enumerate(content.splitlines(), 1):
        if any(
            kw in line
            for kw in (
                "deep",
                "check_http",
                "localhost:80",
                "127.0.0.1:80",
                "check_docker_health",
            )
        ):
            logger.info("[IMP:8][healthcheck-nginx-deep] L%d: %s", line_num, line.strip())

    # ── Assertions ──
    assert has_deep_mode, (
        "[IMP:9][healthcheck-nginx-deep] FAIL: MODE=deep not found — "
        "nginx healthcheck should support deep HTTP verification"
    )
    assert has_check_http, (
        "[IMP:9][healthcheck-nginx-deep] FAIL: check_http not found — "
        "nginx deep check should use check_http (DRIFT-H4 fix replaced docker exec curl)"
    )
    assert has_port_80, (
        "[IMP:9][healthcheck-nginx-deep] FAIL: port 80 check not found — nginx HTTP verification on port 80 is missing"
    )
    assert has_check_docker, (
        "[IMP:9][healthcheck-nginx-deep] FAIL: check_docker_health not found — "
        "nginx liveness check via Docker is missing"
    )

    logger.info("[IMP:9][healthcheck-nginx-deep] PASS: nginx healthcheck has MODE=deep + check_http")


# ═══════════════════════════════════════════════════════════════════════
# ФАСАД core/lib/healthcheck.sh — поведенческие контракты (консолидировано
# из tests/test_lib_healthcheck.py, DevPlan 139 W3 T1, 4→2)
# ═══════════════════════════════════════════════════════════════════════
# Bash-библиотека — KEEP (документированное исключение языковой политики);
# поведение check_docker_health/check_http тестируется mock docker/curl через
# PATH-инъекцию в tmp_path (Zero Hardcode Rule). LDD — через stderr (bash-логи
# идут в stderr, минуя Python logging) — @ldd_trajectory неприменим.
# Критерий «здоров» (D5): running AND (healthy|""|none) — parity с Python-каноном.

# Resolve absolute path to core/lib/ once at module load time.
_LIB_DIR: Path = Path(__file__).resolve().parent.parent / "core" / "lib"


# region FUNC__run_bash
## @purpose  Write a bash script to a temp file that sources both logging.sh
##           and healthcheck.sh, then execute it via subprocess with optional
##           custom environment variables. Each call gets a fresh script in an
##           isolated tmp_path directory — no cross-test contamination.
## @io       ⇥ (tmp_path: Path, code: str, env: dict[str,str]|None) → ⎋ CompletedProcess
## @complexity O(1) — single subprocess.run with 10s timeout
## @invariants
##   - Script file is chmod 755 before execution
##   - Timeout set to 10 seconds (fail-fast on infinite loops)
##   - Does NOT add set -euo pipefail — healthcheck scripts intentionally
##     test error handling: non-zero exit codes are EXPECTED results
##   - Both logging.sh and healthcheck.sh are sourced automatically before code runs
##   - Custom env (e.g. PATH override for mock bins) merged on top of os.environ copy
def _run_bash(
    tmp_path: Path,
    code: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run bash code with logging.sh+healthcheck.sh sourced, return subprocess result.

    ## @purpose  Isolate bash script execution in a temp file for deterministic testing.
    ## @io       ⇥ tmp_path: Path, code: str, env: dict[str,str]|None
    ##           ⎋ CompletedProcess(stdout, stderr, returncode)
    ## @complexity O(1)
    """
    script = tmp_path / "test_script.sh"
    lib_dir_escaped = str(_LIB_DIR)

    script_content = (
        "#!/usr/bin/env bash\n"
        f'LIB_DIR="{lib_dir_escaped}"\n'
        'source "$LIB_DIR/logging.sh"\n'
        'source "$LIB_DIR/healthcheck.sh"\n'
        f"{code}\n"
    )
    script.write_text(script_content)
    script.chmod(0o755)

    run_env = os.environ.copy()
    if env:
        run_env.update(env)

    return subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        timeout=10,
        env=run_env,
    )


# endregion FUNC__run_bash


# region FUNC_test_poll_until_healthy_removed
## @purpose  Verify poll_until_healthy/poll_docker_health are REMOVED (волна 118 B6, R5).
## @io       ⇥ tmp_path → ⎋ assert rc==0, stderr 'REMOVED'
## @complexity O(1)
def test_poll_until_healthy_removed(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · NEGATIVE (R5) · B6 — poll_until_healthy/poll_docker_health удалены
    # · Scenario: source healthcheck.sh → type -t poll_until_healthy → пусто (не функция)
    # · Last fail: poll_until_healthy существовал до волны 118 B6 (healthcheck.sh L104-161)
    # · Remove if: poll_until_healthy будет восстановлен
    result = _run_bash(
        tmp_path,
        """
if [[ "$(type -t poll_until_healthy)" == "function" ]] || [[ "$(type -t poll_docker_health)" == "function" ]]; then
    echo "[IMP:10][test] FAIL: poll functions still defined" >&2
    exit 1
fi
echo "[IMP:9][test] poll_until_healthy/poll_docker_health REMOVED — OK" >&2
exit 0
""",
    )

    assert result.returncode == 0, (
        f"[IMP:9][test_poll_until_healthy_removed] FAIL: poll не удалён, stderr: {result.stderr}"
    )
    assert "REMOVED" in result.stderr, f"[IMP:9][test] FAIL: no REMOVED marker: {result.stderr}"
    __import__("logging").getLogger(__name__).info(
        "[IMP:9][test_poll_until_healthy_removed] PASS: poll functions removed (B6 R5)"
    )


# endregion FUNC_test_poll_until_healthy_removed


# region FUNC__run_check_docker_health
## @purpose  Helper to create a mock docker script and run check_docker_health.
## @io       ⇥ tmp_path: Path, status: str, docker_exit_code: int
##           ⎋ CompletedProcess(stdout, stderr, returncode)
## @complexity O(1)
def _run_check_docker_health(
    tmp_path: Path,
    status: str,
    docker_exit_code: int = 0,
) -> subprocess.CompletedProcess:
    """Create mock docker and run check_docker_health with PATH override.

    ## @purpose  Isolate check_docker_health test setup: creates a mock docker
    ##            script in tmp_path/mock-bin/, sets PATH to find it first,
    ##            and runs check_docker_health via _run_bash.
    ## @io       ⇥ tmp_path: Path, status: str, docker_exit_code: int
    ##           ⎋ CompletedProcess(stdout, stderr, returncode)
    ## @complexity O(1)
    """
    mock_dir = tmp_path / "mock-bin"
    mock_dir.mkdir(parents=True, exist_ok=True)
    mock_docker = mock_dir / "docker"
    mock_docker.write_text(f"#!/usr/bin/env bash\necho '{status}'\nexit {docker_exit_code}\n")
    mock_docker.chmod(0o755)

    code = f'export PATH="{mock_dir}:$PATH"\ncheck_docker_health "test-container"\nrc=$?\necho "RC=$rc" >&2\nexit $rc\n'
    return _run_bash(tmp_path, code)


# endregion FUNC__run_check_docker_health


@pytest.mark.parametrize(
    "status,docker_exit_code,expected_rc,imp_level,expected_imp_line",
    [
        ("healthy", 0, 0, 7, "[IMP:7][healthcheck][check_docker_health]"),
        ("unhealthy", 0, 1, 8, "[IMP:8][healthcheck][check_docker_health]"),
        ("starting", 0, 2, 8, "[IMP:8][healthcheck][check_docker_health]"),
        ("error", 1, 3, 8, "[IMP:8][healthcheck][check_docker_health]"),
    ],
)
def test_check_docker_health(status, docker_exit_code, expected_rc, imp_level, expected_imp_line, tmp_path, caplog):
    """Parametrized Docker health test: healthy/unhealthy/starting/not-found."""
    result = _run_check_docker_health(tmp_path, status, docker_exit_code)
    assert result.returncode == expected_rc, (
        f"Expected exit {expected_rc} ({status}), got {result.returncode}\nstderr: {result.stderr}"
    )
    assert f"RC={expected_rc}" in result.stderr, f"Expected RC={expected_rc} in stderr\ngot: {result.stderr}"
    assert expected_imp_line in result.stderr, f"Expected {expected_imp_line} in stderr\ngot: {result.stderr}"


# region FUNC__run_check_http
## @purpose  Helper to create a mock curl script and run check_http.
## @io       ⇥ tmp_path: Path, http_code: str, expected_codes: str|None
##           ⎋ CompletedProcess(stdout, stderr, returncode)
## @complexity O(1)
def _run_check_http(
    tmp_path: Path,
    http_code: str,
    expected_codes: str | None = None,
) -> subprocess.CompletedProcess:
    """Create mock curl and run check_http with PATH override.

    ## @purpose  Isolate check_http test setup: creates a mock curl script in
    ##            tmp_path/mock-bin/, sets PATH to find it first, and runs
    ##            check_http via _run_bash with optional expected_codes.
    ## @io       ⇥ tmp_path: Path, http_code: str, expected_codes: str|None
    ##           ⎋ CompletedProcess(stdout, stderr, returncode)
    ## @complexity O(1)
    """
    mock_dir = tmp_path / "mock-bin"
    mock_dir.mkdir(parents=True, exist_ok=True)
    mock_curl = mock_dir / "curl"
    mock_curl.write_text(f"#!/usr/bin/env bash\necho '{http_code}'\n")
    mock_curl.chmod(0o755)

    if expected_codes is not None:
        code = (
            f'export PATH="{mock_dir}:$PATH"\n'
            f'check_http "http://example.com/health" "{expected_codes}"\n'
            "rc=$?\n"
            'echo "RC=$rc" >&2\n'
            "exit $rc\n"
        )
    else:
        code = (
            f'export PATH="{mock_dir}:$PATH"\n'
            'check_http "http://example.com/health"\n'
            "rc=$?\n"
            'echo "RC=$rc" >&2\n'
            "exit $rc\n"
        )
    return _run_bash(tmp_path, code)


# endregion FUNC__run_check_http


@pytest.mark.parametrize(
    "http_code,expected_codes,expected_rc,imp_level,expected_imp_line",
    [
        ("200", None, 0, 7, "[IMP:7][healthcheck][check_http]"),
        ("404", "200", 1, 8, "[IMP:8][healthcheck][check_http]"),
        ("301", "200,301,302", 0, 7, "[IMP:7][healthcheck][check_http]"),
    ],
)
def test_check_http(http_code, expected_codes, expected_rc, imp_level, expected_imp_line, tmp_path):
    """Parametrized HTTP health test: success/wrong-code/custom-codes."""
    result = _run_check_http(tmp_path, http_code, expected_codes)
    assert result.returncode == expected_rc, (
        f"Expected exit {expected_rc}, got {result.returncode}\nstderr: {result.stderr}"
    )
    assert f"RC={expected_rc}" in result.stderr, f"Expected RC={expected_rc} in stderr\ngot: {result.stderr}"
    assert expected_imp_line in result.stderr, f"Expected {expected_imp_line} in stderr\ngot: {result.stderr}"
