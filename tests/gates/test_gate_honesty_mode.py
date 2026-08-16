#!/usr/bin/env python3
# GREP_SUMMARY: gate honesty-mode R4 no-service-fail require-docker REQUIRE_HONESTY_MODE marker-xfail-fail CI-fail local-marker DevPlan-119-A3
# STRUCTURE: ▶ monkeypatch _docker_available→False → ◇ REQUIRE_HONESTY_MODE=fail → require_docker_or_fail → ⟦pytest.fail (не skip)⟧ → ◇ marker (default) → ⟦skip⟧ → ◇ CI workflows содержат fail → ⎋ assert
# region MODULE_CONTRACT
## @purpose  Gate (DevPlan 119 A3, AUDIT-5 R4-1): честный fail-mode Test Honesty R4.
##           В CI REQUIRE_HONESTY_MODE=fail — отсутствие Docker = FAIL (не skip).
##           Локальная dev-машина (без переменной) — marker (skip). R5 negative:
##           без Docker + fail-mode → FAIL, не skip.
## @scope    tests/_conftest/honesty.py (require_docker_or_fail + mode-dispatch) +
##           .github/workflows/platform-gate-fast.yml + platform-test.yml (env-контракт).
## @invariants
##   - REQUIRE_HONESTY_MODE=fail + Docker недоступен → pytest.fail (R4: NO_SERVICE = FAIL, not skip)
##   - REQUIRE_HONESTY_MODE не задан (локальная машина) → default "marker" → pytest.skip
##   - Оба CI-workflow объявляют REQUIRE_HONESTY_MODE: fail
## @rationale R4 (Test Honesty): skip-as-bug-masking запрещён. CI-раннеры имеют Docker —
##            переход marker→fail (D46-C закрыт DevPlan 119 A3). Локально переменная
##            не задаётся → marker через default в _honesty_mode().
## @changes  2026-08-02 | DevPlan 119 A3 — Created (fail-mode в CI, R5 negative)
# endregion MODULE_CONTRACT

import logging

import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
_WORKFLOWS = (
    ROOT / ".github" / "workflows" / "platform-gate-fast.yml",
    ROOT / ".github" / "workflows" / "platform-test.yml",
)


# region TEST_honesty_fail_on_missing_docker
@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · NEGATIVE (R5) · fail-mode без Docker → FAIL (DevPlan 119 A3, AUDIT-5 R4-1)
# · Scenario: REQUIRE_HONESTY_MODE=fail + docker недоступен → pytest.fail (не skip)
# · Last fail: N/A (новый negative-тест — исходный вход R4: Docker отсутствует, а тест skip-ится)
# · Remove if: honesty-механизм отменяется
def test_honesty_fail_on_missing_docker(caplog, monkeypatch) -> None:
    """R5 negative: без Docker в fail-mode → FAIL (не skip) (Test Honesty R4)."""
    caplog.set_level(logging.INFO)

    from _conftest import honesty

    monkeypatch.setenv("REQUIRE_HONESTY_MODE", "fail")
    # already-DI (W-H 163): docker_available_fn передаётся напрямую — 0 патчей module-атрибута

    with pytest.raises(pytest.fail.Exception, match=r"\[honesty:fail\]"):
        honesty.require_docker_or_fail(
            "Docker daemon required — R4 negative probe",
            docker_available_fn=lambda: False,
        )

    logger.info("[IMP:9][honesty][R5] PASS: fail-mode without Docker → pytest.fail (не skip)")


# endregion TEST_honesty_fail_on_missing_docker


# region TEST_honesty_marker_default_skips
@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · REGRESSION · локальная dev-машина → marker (skip) (DevPlan 119 A3)
# · Scenario: REQUIRE_HONESTY_MODE не задан (default marker) + docker недоступен → pytest.skip
# · Last fail: N/A (новый тест — фиксирует контракт «локально = marker»)
# · Remove if: honesty-механизм отменяется
def test_honesty_marker_default_skips(caplog, monkeypatch) -> None:
    """Локальная dev-машина (без REQUIRE_HONESTY_MODE) → marker: skip при отсутствии Docker."""
    caplog.set_level(logging.INFO)

    from _conftest import honesty

    monkeypatch.delenv("REQUIRE_HONESTY_MODE", raising=False)
    # already-DI (W-H 163): docker_available_fn передаётся напрямую — 0 патчей module-атрибута

    with pytest.raises(pytest.skip.Exception, match=r"\[honesty:marker\]"):
        honesty.require_docker_or_fail(
            "Docker daemon required — marker probe",
            docker_available_fn=lambda: False,
        )

    logger.info("[IMP:9][honesty][marker] PASS: default marker mode without Docker → pytest.skip")


# endregion TEST_honesty_marker_default_skips


# region TEST_ci_workflows_require_honesty_fail
@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · REGRESSION · CI workflow env-контракт (DevPlan 119 A3)
# · Scenario: оба CI-workflow объявляют REQUIRE_HONESTY_MODE: fail
# · Last fail: marker в platform-gate-fast.yml:44 и platform-test.yml:74 (skip-mode на CI)
# · Remove if: REQUIRE_HONESTY_MODE механизм отменяется
def test_ci_workflows_require_honesty_fail(caplog) -> None:
    """CI-workflow'ы объявляют REQUIRE_HONESTY_MODE: fail (R4 compliant, DevPlan 119 A3)."""
    caplog.set_level(logging.INFO)
    violations: list[str] = []
    for wf in _WORKFLOWS:
        assert wf.is_file(), f"[IMP:10][honesty] workflow not found: {wf}"
        content = wf.read_text(errors="replace")
        if "REQUIRE_HONESTY_MODE: fail" not in content:
            violations.append(str(wf.relative_to(ROOT)))

    assert not violations, (
        f"[IMP:10][honesty] CI workflows missing REQUIRE_HONESTY_MODE: fail (R4 NO_SERVICE = FAIL на CI): {violations}"
    )

    logger.info("[IMP:9][honesty][ci] PASS: both CI workflows declare REQUIRE_HONESTY_MODE: fail")


# endregion TEST_ci_workflows_require_honesty_fail
