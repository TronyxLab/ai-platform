# GREP_SUMMARY: skip-gate automatic-skip-gate pytest-skip architectural-gate runtest-makereport
# STRUCTURE: ┌pytest_runtest_makereport┐ → ◇ call.when=call + excinfo=skip.Exception → IMP:8 log → ⎋ None || ┌automatic_skip_gate fixture┐ → yield (passive autouse)
# region MODULE_CONTRACT
## @purpose  Architectural gate: pytest.skip must only be used for genuine infrastructure absence
##           (no Docker, no env vars, no network). Logs at IMP:8 when a test uses pytest.skip,
##           providing visibility for agents to verify skip is not masking a real bug.
## @scope    Passive logging-only; runs after every test via autouse fixture + runtest hook.
##           Extracted from tests/conftest.py for modularity.
## @invariants
##   - Does NOT block pytest.skip or modify test execution
##   - Logs at [IMP:8] with skip reason for LDD trajectory visibility
##   - CHECKLIST entry + TRAP[DECISION] document the architectural rule
## @rationale Prevents agents from masking real bugs with pytest.skip (TASK-9).
##            The hook+fixture combo ensures every skip is visible in output;
##            agents reviewing LDD logs can verify the skip is justified.
##            Extracted from conftest.py to reduce file size and keep gate logic modular.
# endregion MODULE_CONTRACT

import logging

import pytest

logger = logging.getLogger(__name__)

# region AUTOMATIC_SKIP_GATE
## @purpose — Architectural gate: pytest.skip must only be used for genuine infrastructure
##            absence (no Docker, no env vars, no network). The autouse fixture logs at IMP:8
##            when a test uses pytest.skip, providing visibility for agents to verify skip is
##            not masking a real bug. Does NOT block or modify test behavior.
## @scope — Passive logging-only; runs after every test via autouse fixture + runtest hook.
## @invariants
##   - Does NOT block pytest.skip or modify test execution
##   - Logs at [IMP:8] with skip reason for LDD trajectory visibility
##   - CHECKLIST entry + TRAP[DECISION] document the architectural rule
## @rationale — Prevents agents from masking real bugs with pytest.skip (TASK-9).
##              The hook+fixture combo ensures every skip is visible in output;
##              agents reviewing LDD logs can verify the skip is justified.
# 2026-08-04 (DevPlan 129 W1 T6): uniform-обработка подтверждена.
# · Все tests/test_e2e_*.py используют _handle_e2e_error (grep-аудит: 0 e2e без хендлера).
# · Smoke-тесты (test_smoke_*, test_component_hermes) используют каноническую альтернативу —
# · retry с экспоненциальным backoff (паттерн 1s/2s/4s, TRAP[BUG] 2026-07-23) — оба паттерна
# · допускаются гейтом G3 (tests/gates/test_gate_http_retry_policy.py: retry ИЛИ _handle_e2e_error).
# · CHECKLIST-запись (tests/_conftest/checklist.py:33) сохранена как напоминание для новых тестов.


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_makereport(item, call):
    """Log at IMP:8 when a test uses pytest.skip.

    ## @purpose — Runs after each test call phase; if test was skipped, logs at
    ##            IMP:8 for LDD trajectory visibility. Does NOT block skip.
    ## @io — ⇥ item, call → ⎛ None (side-effect: IMP:8 log on skip)
    ## @complexity — O(1)
    """
    if call.when == "call" and call.excinfo is not None and isinstance(call.excinfo.value, pytest.skip.Exception):
        reason = str(call.excinfo.value)
        logger.info(
            "%s",
            f"[IMP:8][automatic_skip_gate] Test '{item.nodeid}' skipped: {reason}. Verify skip is for env absence only (no Docker, no env vars, no network).",
        )


@pytest.fixture(autouse=True)
def automatic_skip_gate():
    """
    ## @purpose — Architectural gate: pytest.skip must only be used for genuine
    ##            infrastructure absence (no Docker, no env vars, no network).
    ##            Passive autouse fixture — the actual skip detection is in
    ##            pytest_runtest_makereport hook above.
    ## @io — ⎛ None (side-effect: none — passive gate, runs after every test)
    ## @complexity — O(1)
    ## @rationale — Prevents agents from masking real bugs with pytest.skip.
    ##              The CHECKLIST item + TRAP[DECISION] document the rule.
    """
    yield


# endregion AUTOMATIC_SKIP_GATE
