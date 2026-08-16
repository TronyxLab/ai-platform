# GREP_SUMMARY: honesty R4 skip-to-fail docker negative require_docker_or_fail require_script_or_fail require_env_or_fail mode-dispatch
# STRUCTURE: ▶ mock docker unavailable → ◇ REQUIRE_HONESTY_MODE=fail → ⚡ pytest.fail (R4 negative) → ◇ mode=marker → skip
# region MODULE_CONTRACT
## @purpose  R5 anti-survivorship для R4 skip→fail (DevPlan 119 F1). Проверяет, что
##           honesty-диспетчер в режиме REQUIRE_HONESTY_MODE=fail реально FAIL'ит при
##           отсутствии зависимости (Docker/скрипт/env), а не молча проходит или skip'ает.
## @scope    Negative-тесты на отсутствие зависимостей: Docker daemon, скрипт, env var.
## @invariants
##   - require_docker_or_fail при недоступном Docker + REQUIRE_HONESTY_MODE=fail → pytest.fail
##   - require_script_or_fail при отсутствующем скрипте + REQUIRE_HONESTY_MODE=fail → pytest.fail
##   - require_env_or_fail при незаданной env var + REQUIRE_HONESTY_MODE=fail → pytest.fail
##   - В режиме marker → pytest.skip (локальная разработка, обратная совместимость)
##   - monkeypatch-изоляция: env REQUIRE_HONESTY_MODE восстанавливается между тестами
## @rationale  R5 ANTI-SURVIVORSHIP (Test Honesty R5): каждый skip→fail переход (F1 R4-2..R4-7)
##             обязан иметь negative-тест с исходным входом — verify что тесты FAIL без
##             зависимостей, а не маскируются skip'ом.
## @changes    2026-08-02 | Created (DevPlan 119 F1, TEST_SPEC tests/unit/test_honesty.py)
# endregion MODULE_CONTRACT

import logging

import pytest
from _conftest.honesty import require_docker_or_fail, require_env_or_fail, require_script_or_fail

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# region FUNC_test_skip_to_fail_without_docker_negative
## @purpose  R5 negative: Docker недоступен + REQUIRE_HONESTY_MODE=fail → pytest.fail (не skip).
##           Исходный вход: отсутствующий Docker daemon (R4-4: test_gate_make_contract:376).
# 🧪 TRAP[TEST] · NEGATIVE (R5) · honesty skip→fail — R4-4 docker
# · Last fail: pytest.skip("docker not available") — R4 violation
# · Remove if: REQUIRE_HONESTY_MODE=fail семантика меняется
def test_skip_to_fail_without_docker_negative() -> None:
    """R5: без Docker + fail-режим → pytest.fail (не skip)."""
    # DI (W-H): mode_env + docker_available_fn (0 патчей)
    with pytest.raises(pytest.fail.Exception, match=r"Docker daemon not available"):
        require_docker_or_fail(
            reason="R5 negative: docker missing",
            mode_env={"REQUIRE_HONESTY_MODE": "fail"},
            docker_available_fn=lambda: False,
        )
    logger.info("[IMP:9][test_honesty] R5 PASS: Docker unavailable + fail-mode → pytest.fail")


# endregion FUNC_test_skip_to_fail_without_docker_negative


# region FUNC_test_marker_mode_still_skips
## @purpose  Обратная совместимость: REQUIRE_HONESTY_MODE=marker (локальная разработка)
##           → pytest.skip (не fail). Wave 1 переходный режим.
# 🧪 TRAP[TEST] · DevPlan 119 F1 · marker-mode skip (обратная совместимость)
# · Last fail: N/A — честность требует fail в CI, но локально marker→skip
# · Remove if: REQUIRE_HONESTY_MODE=marker семантика удаляется
def test_marker_mode_still_skips() -> None:
    """marker-режим + Docker недоступен → skip (локальная разработка)."""
    with pytest.raises(pytest.skip.Exception, match=r"Docker daemon not available"):
        require_docker_or_fail(
            reason="marker mode check",
            mode_env={"REQUIRE_HONESTY_MODE": "marker"},
            docker_available_fn=lambda: False,
        )
    logger.info("[IMP:9][test_honesty] marker-mode skip подтверждён (обратная совместимость)")


# endregion FUNC_test_marker_mode_still_skips


# region FUNC_test_script_missing_fail_mode
## @purpose  R5 negative: отсутствующий скрипт + fail-режим → pytest.fail.
##           Исходный вход: acme.sh не найден (R4-3: test_tls_wildcard:819).
# 🧪 TRAP[TEST] · NEGATIVE (R5) · honesty skip→fail — R4-3 acme.sh
# · Last fail: pytest.skip("acme.sh not found in PATH")
# · Remove if: REQUIRE_HONESTY_MODE=fail семантика меняется
def test_script_missing_fail_mode(tmp_path) -> None:
    """R5: отсутствующий скрипт + fail-режим → pytest.fail (не skip)."""
    missing = tmp_path / "does-not-exist.sh"

    with pytest.raises(pytest.fail.Exception, match=r"Script not found"):
        require_script_or_fail(missing, reason="R5 negative: script missing", mode_env={"REQUIRE_HONESTY_MODE": "fail"})
    logger.info("[IMP:9][test_honesty] R5 PASS: script missing + fail-mode → pytest.fail")


# endregion FUNC_test_script_missing_fail_mode


# region FUNC_test_env_missing_fail_mode
## @purpose  R5 negative: незаданная env var + fail-режим → pytest.fail.
##           Исходный вход: GF_SECURITY_ADMIN_PASSWORD не задан (e2e_grafana creds).
# 🧪 TRAP[TEST] · DevPlan 119 F1 · env missing + fail-mode → pytest.fail
# · Last fail: N/A — env-absence маскировалась skip'ом в grafana-фикстурах
# · Remove if: require_env_or_fail семантика меняется
def test_env_missing_fail_mode() -> None:
    """R5: отсутствующая env var + fail-режим → pytest.fail (не skip)."""
    with pytest.raises(pytest.fail.Exception, match=r"Env var not set"):
        require_env_or_fail(
            "HONESTY_TEST_REQUIRED_VAR", reason="R5 negative: env missing", mode_env={"REQUIRE_HONESTY_MODE": "fail"}
        )
    logger.info("[IMP:9][test_honesty] R5 PASS: env missing + fail-mode → pytest.fail")


# endregion FUNC_test_env_missing_fail_mode


# region FUNC_test_invalid_mode_raises
## @purpose  Некорректное значение REQUIRE_HONESTY_MODE → ValueError (fail-fast, R5).
# 🧪 TRAP[TEST] · DevPlan 119 F1 · invalid honesty mode → ValueError
# · Last fail: N/A — некорректный mode молча дефолтился на marker
# · Remove if: валидация REQUIRE_HONESTY_MODE удаляется
def test_invalid_mode_raises() -> None:
    """REQUIRE_HONESTY_MODE=invalid → ValueError (валидация входа)."""
    with pytest.raises(ValueError, match=r"invalid REQUIRE_HONESTY_MODE"):
        require_docker_or_fail(
            reason="invalid mode check",
            mode_env={"REQUIRE_HONESTY_MODE": "invalid-mode"},
            docker_available_fn=lambda: False,
        )
    logger.info("[IMP:9][test_honesty] invalid mode → ValueError подтверждён")


# endregion FUNC_test_invalid_mode_raises
