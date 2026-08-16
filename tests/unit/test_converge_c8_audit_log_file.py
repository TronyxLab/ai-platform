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

import pytest

from core.internal.bootstrap.converge import infra
from core.internal.shared.audit_logger import DEFAULT_LOG_FILE

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · Regression · C8 (parametrize) — converge AUDIT_LOG_FILE == shared DEFAULT_LOG_FILE
# · Scenario: infra.AUDIT_LOG_FILE (источник) и converge/audit.AUDIT_LOG_FILE (re-export)
# ·   == shared канон "/var/log/platform/audit.jsonl" — единый файл audit.jsonl
# · Last fail: ручная синхронизация f"{AUDIT_LOG_DIR}/audit.jsonl" (дрейф при правке shared)
# · Remove if: converge отвязывается от shared/audit_logger.DEFAULT_LOG_FILE
@pytest.mark.parametrize("module_name", ["infra", "converge/audit"], ids=["infra", "converge_audit"])
def test_audit_log_file_is_shared_default(module_name: str) -> None:
    """C8: AUDIT_LOG_FILE — единый источник shared/audit_logger.DEFAULT_LOG_FILE (infra + re-export)."""
    if module_name == "infra":
        target = infra.AUDIT_LOG_FILE
    else:
        from core.internal.bootstrap.converge import audit as converge_audit

        target = converge_audit.AUDIT_LOG_FILE
    assert target == DEFAULT_LOG_FILE, f"C8 FAIL: converge пишет в {target}, shared канон {DEFAULT_LOG_FILE}"
    logger.info("[IMP:9][test] converge AUDIT_LOG_FILE=%s (shared канон)", target)
