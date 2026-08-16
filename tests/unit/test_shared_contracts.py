# GREP_SUMMARY: contracts shared DEPLOY_BEST_EFFORT EXIT_OK EXIT_FATAL exit-codes policy best-effort U-39 unit
# STRUCTURE: ▶ import shared.contracts → ◇ константы существуют → ◇ exit-коды согласованы с exceptions.py → ◇ DEPLOY_BEST_EFFORT is True → ⎋ PASS
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/contracts.py (DevPlan 116 B4 T1, U-39):
##           DEPLOY_BEST_EFFORT политика + machine-readable exit-коды.
## @scope    Константы контракта; согласованность с shared/exceptions.py exit_code атрибутами.
## @invariants
##   - EXIT_* константы совпадают с exit_code классов exceptions (2/3/4/10)
##   - DEPLOY_BEST_EFFORT == True (фиксация политики best-effort)
## @rationale Гейт T7 проверяет документацию; этот тест — согласованность констант↔иерархия.
## @changes 2026-08-01 | DevPlan 116 B4 T9.2 — Created
# endregion MODULE_CONTRACT

import logging

import pytest

from core.internal.shared import contracts
from core.internal.shared.exceptions import (
    ConfigNotFoundError,
    ConfigParseError,
    ConfigValidationError,
    PlatformError,
    PlatformFatalError,
)
from tests.conftest import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


@ldd_trajectory
# 🧪 TRAP[TEST] · Regression · contracts constants exist and match exception exit_codes (DevPlan 116 B4 T1)
def test_exit_code_constants_match_exception_hierarchy(caplog) -> None:
    """EXIT_* constants must match exception exit_code attributes (единый контракт)."""
    expected = {
        contracts.EXIT_OK: None,
        contracts.EXIT_GENERIC: PlatformError.exit_code,
        contracts.EXIT_CONFIG_NOT_FOUND: ConfigNotFoundError.exit_code,
        contracts.EXIT_CONFIG_PARSE: ConfigParseError.exit_code,
        contracts.EXIT_CONFIG_VALIDATION: ConfigValidationError.exit_code,
        contracts.EXIT_FATAL: PlatformFatalError.exit_code,
    }
    for code, exc_code in expected.items():
        if exc_code is not None:
            assert code == exc_code, f"EXIT_{code} != {exc_code}"
            logger.info("[IMP:8][contracts] exit_code=%d matches %s", code, exc_code)
        else:
            logger.info("[IMP:8][contracts] EXIT_OK=%d (no exception class)", code)

    assert contracts.EXIT_OK == 0
    assert contracts.EXIT_GENERIC == 1
    assert contracts.EXIT_CONFIG_NOT_FOUND == 2
    assert contracts.EXIT_CONFIG_PARSE == 3
    assert contracts.EXIT_CONFIG_VALIDATION == 4
    assert contracts.EXIT_FATAL == 10
    logger.info("[IMP:9][contracts] PASS: exit-коды 0/1/2/3/4/10 согласованы с exceptions.py")


@ldd_trajectory
# GUARD-PRESERVE (168): политика deploy best-effort — DEPLOY_BEST_EFFORT is True (U-39, DevPlan 116 B4 T1);
# единственное покрытие константы, фиксация осознанной политики (удаление = тихая смена стратегии деплоя)
# 🧪 TRAP[TEST] · Regression · DEPLOY_BEST_EFFORT policy is True (фиксация best-effort, DevPlan 116 B4 T1)
def test_deploy_best_effort_policy_true(caplog) -> None:
    """DEPLOY_BEST_EFFORT must be True — deploy-политика best-effort (U-39)."""
    assert contracts.DEPLOY_BEST_EFFORT is True
    logger.info("[IMP:9][contracts] PASS: DEPLOY_BEST_EFFORT=True (best-effort политика зафиксирована)")
