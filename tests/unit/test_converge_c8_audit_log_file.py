#!/usr/bin/env python3
# GREP_SUMMARY: test-converge-audit-log-file C8 DEFAULT_LOG_FILE shared sole-source converge-infra
# STRUCTURE: ▶ test_infra_audit_log_file_is_shared_default → test_audit_imports_shared
# region MODULE_CONTRACT
## @purpose  Unit tests for DevPlan 118 C8 — converge/infra.AUDIT_LOG_FILE = shared/audit_logger.DEFAULT_LOG_FILE
##           (второй источник правды удалён). converge/audit.py пишет в тот же файл.
## @scope    Tests: identity AUDIT_LOG_FILE (infra) == DEFAULT_LOG_FILE (shared); audit.py импортирует.
## @invariants
##   - infra.AUDIT_LOG_FILE — тот же объект/значение, что shared DEFAULT_LOG_FILE
##   - converge/audit.AUDIT_LOG_FILE — re-export из infra (пишет в единый файл)
## @rationale DevPlan 118 C8 §TEST — unit: converge пишет в DEFAULT_LOG_FILE.
## @changes 2026-08-02 | DevPlan 118 C8 — created
# endregion MODULE_CONTRACT

import logging

from core.internal.bootstrap.converge import infra
from core.internal.shared.audit_logger import DEFAULT_LOG_FILE

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · Regression · C8 — converge AUDIT_LOG_FILE == shared DEFAULT_LOG_FILE
# · Scenario: infra.AUDIT_LOG_FILE == "/var/log/platform/audit.jsonl" (shared канон)
# · Last fail: ручная синхронизация f"{AUDIT_LOG_DIR}/audit.jsonl" (дрейф при правке shared)
# · Remove if: converge отвязывается от shared/audit_logger.DEFAULT_LOG_FILE
def test_infra_audit_log_file_is_shared_default() -> None:
    """C8: converge/infra.AUDIT_LOG_FILE — единый источник shared/audit_logger.DEFAULT_LOG_FILE."""
    assert infra.AUDIT_LOG_FILE == DEFAULT_LOG_FILE, (
        f"C8 FAIL: converge пишет в {infra.AUDIT_LOG_FILE}, shared канон {DEFAULT_LOG_FILE}"
    )
    logger.info("[IMP:9][test] converge AUDIT_LOG_FILE=%s (shared канон)", infra.AUDIT_LOG_FILE)


# 🧪 TRAP[TEST] · Regression · C8 — converge/audit.py пишет в тот же файл
# · Scenario: audit.py импортирует AUDIT_LOG_FILE из infra (единый файл audit.jsonl)
# · Last fail: N/A (C8 unit)
# · Remove if: converge/audit импорт AUDIT_LOG_FILE меняется
def test_converge_audit_imports_shared_log_file() -> None:
    """converge/audit.py импортирует AUDIT_LOG_FILE из infra (re-export shared канона)."""
    from core.internal.bootstrap.converge import audit as converge_audit

    assert converge_audit.AUDIT_LOG_FILE == DEFAULT_LOG_FILE
    logger.info("[IMP:9][test] converge/audit пишет в %s", converge_audit.AUDIT_LOG_FILE)
