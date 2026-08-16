"""Static layer: Finding model + registry infrastructure tests (DevPlan 163 W-C C2).

# GREP_SUMMARY: test-static finding model round-trip json serialization registry run-all ordering severity
# STRUCTURE: ▶ Finding round-trip (to_dict/from_dict/to_json) → ◇ invalid severity → ValueError
#            → ▶ registry.run_all (empty tmp tree → 0) → ▶ reports (human/json) → ⎋
"""
# region MODULE_CONTRACT
## @purpose  Unit-тесты модели дефекта Finding и реестра детекторов (core/internal/static):
##           round-trip сериализации, fail-fast на невалидном severity, run_all на пустом
##           дереве (все детекторы грациозно пропускают отсутствующий core/), структура
##           human/json отчётов. Zero Hardcode Rule: tmp_path только.
## @scope    Тесты инфраструктуры (не детекторов — те у каждого свои test_static_* файлы).
## @invariants
##   - from_dict(to_dict(f)) == f (round-trip стабилен)
##   - from_dict с неизвестным severity → ValueError (fail-fast, никаких тихих значений)
##   - run_all на пустом дереве → [] (все 9 детекторов пропускают отсутствующий core/)
##   - json_report парсится; summary.total == len(findings)
## @rationale Модель и реестр — общий контракт всех детекторов (T3.1 JSON); их сбой
##            ломает весь статический слой. Тесты — страховка контракта.
## @changes 2026-08-13 | DevPlan 163 W-C C2 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging

import pytest

from core.internal.static.finding import Finding
from core.internal.static.registry import (
    DETECTORS,
    count_by_rule,
    human_report,
    json_report,
    run_all,
)
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · POSITIVE · Finding round-trip (to_dict → from_dict → to_json)
# · Scenario: сериализация находки в dict/JSON и обратно; все поля сохраняются
# · Last fail: N/A (new)
# · Remove if: Finding model replaced
@ldd_trajectory
def test_finding_round_trip(caplog) -> None:
    """Finding → to_dict → from_dict → to_json: round-trip стабилен."""
    original = Finding(
        rule="cross-layer",
        file="core/internal/deploy/x.py",
        line=12,
        message="[deploy→bootstrap] forbidden",
    )
    restored = Finding.from_dict(original.to_dict())
    assert restored == original, f"Round-trip mismatch: {restored!r} != {original!r}"

    parsed = json.loads(original.to_json())
    assert parsed["rule"] == "cross-layer"
    assert parsed["file"] == "core/internal/deploy/x.py"
    assert parsed["line"] == 12
    assert parsed["severity"] == "error"

    logger.info("[IMP:9][test_static_finding] Finding round-trip OK: %s", original)


# 🧪 TRAP[TEST] · NEGATIVE · from_dict с неизвестным severity → ValueError (fail-fast)
# · Scenario: severity="fatal" не входит в {error, warning} — конструктор обязан отвергнуть
# · Last fail: N/A (fail-fast контракт)
# · Remove if: Finding model replaced
@ldd_trajectory
def test_finding_invalid_severity_rejected(caplog) -> None:
    """from_dict с невалидным severity поднимает ValueError (никаких тихих значений)."""
    with pytest.raises(ValueError, match="severity"):
        Finding.from_dict({"rule": "x", "file": "f.py", "line": 1, "message": "m", "severity": "fatal"})
    logger.info("[IMP:9][test_static_finding] Invalid severity rejected (fail-fast)")


# 🧪 TRAP[TEST] · POSITIVE · registry.run_all на пустом дереве → 0 findings
# · Scenario: все 9 детекторов грациозно пропускают дерево без core/ (не падают)
# · Last fail: N/A (graceful degradation контракт)
# · Remove if: registry replaced
@ldd_trajectory
def test_registry_run_all_empty_tree(caplog, tmp_path) -> None:
    """run_all на пустом tmp_path — 0 находок, ни один детектор не падает."""
    findings = run_all(tmp_path)
    assert findings == [], f"Expected 0 findings on empty tree, got {findings}"
    assert len(DETECTORS) >= 9, f"Registry must register >=9 detectors, got {len(DETECTORS)}"
    logger.info("[IMP:9][test_static_finding] run_all on empty tree: 0 findings across %d detectors", len(DETECTORS))


# 🧪 TRAP[TEST] · POSITIVE · отчёты: human содержит находки, json парсится + summary
# · Scenario: 2 находки → human_report показывает обе, json_report имеет summary.total
# · Last fail: N/A (report contract T3.1)
# · Remove if: reports replaced
@ldd_trajectory
def test_reports_include_findings(caplog) -> None:
    """human_report и json_report отражают все находки и summary."""
    findings = [
        Finding(rule="a", file="f1.py", line=1, message="one"),
        Finding(rule="b", file="f2.py", line=2, message="two"),
    ]
    human = human_report(findings)
    assert "f1.py" in human and "f2.py" in human
    assert "FAIL" in human

    data = json.loads(json_report(findings))
    assert data["summary"]["total"] == 2
    assert count_by_rule(findings) == {"a": 1, "b": 1}
    assert data["summary"]["by_rule"] == {"a": 1, "b": 1}
    logger.info("[IMP:9][test_static_finding] Reports contract OK: %s", data["summary"])
