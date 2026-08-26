# GREP_SUMMARY: healthchecks-retry-predicate permanent-error single-pass fail-fast AI-0015 spec-row
# STRUCTURE: ▶ monkeypatched invoke → FileNotFoundError-stderr → ⎋ ровно 1 вызов (без retry-бюджета)
# region MODULE_CONTRACT
## @purpose  AI-0015 (DevPlan 17 T3.4, $TEST_SPEC row): постоянная ошибка
##           (FileNotFoundError/module.yaml отсутствует) → fail БЕЗ retry-бюджета.
##           Специфицированный артефакт $TEST_SPEC; расширенная версия —
##           tests/unit/test_reporting_retry.py.
## @scope    tests/unit: monkeypatched invoke/sleep.
## @invariants
##   - Permanent stderr → ровно 1 вызов invoke + IMP:9 no-retry запись
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest

from core.internal.bootstrap.lifecycle.helpers import reporting

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · 2026-08-26 · P3 · FileNotFoundError → fail без бюджета (AI-0015)
# · Scenario: invoke возвращает not-found stderr → 1 вызов, IMP:9 «PERMANENT failure»,
#   retries не тратятся
# · Last fail: DevPlan 17 верификация @64c2090
# · Remove if: вместе с test_reporting_retry.py (общий контракт)
def test_permanent_error_single_pass(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(reporting.time, "sleep", lambda _s: None)

    calls: list[str] = []

    def _not_found(mod_name: str, interface: str, *args: str, timeout: int = 60):
        calls.append(mod_name)
        return False, "bash: /opt/.../module.yaml: No such file or directory"

    monkeypatch.setattr(reporting, "module_interface_invoke", _not_found)

    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text("modules:\n  ghost:\n    enabled: true\n", encoding="utf-8")
    reporting.run_healthchecks(str(node_yaml))

    assert len(calls) == 1, f"permanent ошибка обязана дать один проход: {len(calls)}"
    assert any("PERMANENT failure" in r.getMessage() for r in caplog.records)
    logger.critical("[IMP:9][test] permanent error single pass — OK (AI-0015)")
