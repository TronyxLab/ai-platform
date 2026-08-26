# GREP_SUMMARY: health-criterion-parity collector-canon D5 running-no-healthcheck healthy unhealthy none AI-0065
# STRUCTURE: ▶ synthetic State dicts (healthy/unhealthy/none/no-healthcheck/exited) → ◇ collector._get_health_status == docker_compose.is_container_healthy → ⎋ parity на всех состояниях
# region MODULE_CONTRACT
## @purpose  AI-0065 (DevPlan 17 T1.3): вердикт коллектора обязан совпадать с каноном
##           «здоров» (shared/docker_compose.is_container_healthy, критерий D5) на всех
##          состояниях Health.Status — особенно running-без-healthcheck ⇒ healthy.
## @scope    tests/unit: чистые функции над synthetic dict; без subprocess/docker.
## @invariants
##   - healthy/unhealthy/none/отсутствие Health: collector == canon
##   - exited при любом Health → False у обоих
# endregion MODULE_CONTRACT

import logging

from core.internal.healthcheck.metrics import docker_collector
from core.internal.shared.docker_compose import is_container_healthy

logger = logging.getLogger(__name__)

_CASES: list[tuple[dict[str, object], str | None]] = [
    # (docker inspect State dict, ожидаемый health-токен канона)
    ({"Status": "running", "Health": {"Status": "healthy"}}, "healthy"),
    ({"Status": "running", "Health": {"Status": "unhealthy"}}, "unhealthy"),
    ({"Status": "running", "Health": {"Status": "starting"}}, "starting"),
    ({"Status": "running", "Health": {"Status": ""}}, ""),
    ({"Status": "running", "Health": {"Status": "none"}}, "none"),
    ({"Status": "running"}, None),  # контейнер БЕЗ HEALTHCHECK — ключевой случай AI-0065
    ({"Status": "exited", "Health": {"Status": "healthy"}}, "healthy"),
    ({"Status": "exited"}, None),
]


def _canon_verdict(state: dict[str, object]) -> bool:
    """Канон D5 поверх того же State-dict (как healthcheck_poll читает inspect)."""
    health = state.get("Health")
    health_status = health.get("Status") if isinstance(health, dict) else None  # type: ignore[union-attr]
    return is_container_healthy(cast_state(state), health_status)  # type: ignore[arg-type]


def cast_state(state: dict[str, object]) -> str | None:
    value = state.get("Status")
    return value if isinstance(value, str) or value is None else str(value)


# 🧪 TRAP[TEST] · 2026-08-26 · P2 · parity collector↔canon на трёх+ состояниях (AI-0065)
# · Regression: docker_collector матчил только Status=='healthy' → running-без-healthcheck
#   считался больным → вечный WARN на статус-странице + aggregate FAIL
# · Scenario: для каждого synthetic State: collector-вердикт == is_container_healthy;
#   ключевой кейс running-без-Health ⇒ True у обоих; unhealthy ⇒ False
# · Last fail: DevPlan 17 верификация @64c2090 (аудит 08-ai-code AI-0065)
# · Remove if: критерий здоровья переезжает в единый типизированный контракт с одним ридером
def test_running_without_healthcheck_is_healthy() -> None:
    for state, _token in _CASES:
        collector_verdict = docker_collector._get_health_status(state)
        canon_verdict = _canon_verdict(state)
        print(f"[IMP:8][parity] state={state} collector={collector_verdict} canon={canon_verdict}")
        assert collector_verdict == canon_verdict, (
            f"parity нарушен для {state}: collector={collector_verdict} canon={canon_verdict}"
        )
        logger.info("[IMP:8][test] parity OK for %s", state)

    # Явные контракты (не только паритет): ключевые вердикты канона
    assert docker_collector._get_health_status({"Status": "running"}) is True, (
        "running-без-healthcheck обязан быть healthy (AI-0065)"
    )
    assert docker_collector._get_health_status({"Status": "running", "Health": {"Status": "unhealthy"}}) is False
    assert docker_collector._get_health_status({"Status": "exited"}) is False
    logger.critical("[IMP:9][test] collector==canon on all states incl. no-healthcheck — OK (AI-0065)")
