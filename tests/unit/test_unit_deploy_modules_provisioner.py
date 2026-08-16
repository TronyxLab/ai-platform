"""
# GREP_SUMMARY: deploy-modules postgres severity-critical module-yaml static-audit
# STRUCTURE: ▶ test_postgres_module_severity_critical → ◇ read postgres/module.yaml → ⊕ assert severity: critical
# region MODULE_CONTRACT
## @file test_unit_deploy_modules_provisioner.py
## @purpose  Static audit tests for module.yaml severity declaration. After W4-E1 Strangler-Fig
##           decomposition, shell-level structural contract tests moved to
##           tests/unit/test_shell_facade_contract.py (S1-S6).
##           Only test_postgres_module_severity_critical remains — it checks module.yaml, not shell.
## @scope    Static analysis of core/modules/postgres/module.yaml.
##           Does NOT require Docker, VPS, or network access — reads YAML file.
## @invariants
##   - postgres/module.yaml must have severity: critical
##   - CRITICAL severity → exit 2 on failure (blocks node-update)
## @rationale DevPlan 042 Phase 4: 9 obsolete tests replaced by structural contract tests
##           (S1-S6 in test_shell_facade_contract.py). Only the postgres module.yaml check
##           remains because it tests a DATA file, not shell wiring.
## @changes   2026-07-22 · DevPlan 042 — removed 9 obsolete shell-grep tests, kept postgres severity
# endregion MODULE_CONTRACT
"""

import logging
import pathlib
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.static_audit

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# region TEST_test_postgres_module_severity_critical
# 🧪 TRAP[TEST] · 2026-07-17 · postgres module.yaml severity: critical
# · Prevents: regression where postgres severity is downgraded from critical
# · Scenario: postgres is a CRITICAL module — failure must block node-update
# · Last fail: never
# · Remove if: postgres is intentionally changed to warn severity
def test_postgres_module_severity_critical() -> None:
    """postgres/module.yaml must have severity: critical."""
    postgres_yaml = Path(PROJECT_ROOT) / "core" / "modules" / "postgres" / "module.yaml"

    assert pathlib.Path(postgres_yaml).exists(), f"postgres/module.yaml not found at {postgres_yaml}"

    with pathlib.Path(postgres_yaml).open(encoding="utf-8") as f:
        content = f.read()

    # Verify severity: critical is present
    assert "severity: critical" in content, (
        "postgres/module.yaml must have severity: critical\n"
        "DevPlan 020 T21: CRITICAL module failure → exit 2 (blocks node-update)"
    )

    logger.info("[IMP:9][test][postgres-severity] postgres/module.yaml has severity: critical")


# endregion TEST_test_postgres_module_severity_critical
