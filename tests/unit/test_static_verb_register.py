"""Static layer: verb-register detector tests (DevPlan 163 W-C C2).

# GREP_SUMMARY: test-static verb-register phony-targets allowed-verbs system-exceptions G1.2 R5
# STRUCTURE: ▶ Makefile .PHONY вне allowed_verbs (R5: extra-класс G1.2) → RED
#            → ▶ verb без таргета (missing-класс) → RED
#            → ▶ служебные категории + allowed_verbs в .PHONY → PASS → ⎋
"""
# region MODULE_CONTRACT
## @purpose  R5-пары детектора verb_register (DevPlan 163 W-C C2): негативный тест на класс
##           дефекта исходного гейта test_all_phony_targets_discovered (extra-таргет: .PHONY
##           вне allowed_verbs ∪ служебных категорий — G1.2 drift hardcoded target sets),
##           позитивный тест (allowed_verb без таргета),
##           PASS-контроль (полная parity + system_exceptions).
## @scope    Native imports; probe-дерево tmp_path: Makefile + makefiles/*.mk +
##           core/entrypoint-manifest.yaml (Zero Hardcode Rule).
## @invariants
##   - .PHONY таргет вне allowed_verbs ∪ system_exceptions → RED (extra, R5 G1.2)
##   - allowed_verb без .PHONY таргета → RED (missing)
##   - system_exceptions + allowed_verbs покрыты таргетами → PASS
## @rationale R5 anti-survivorship (G1.2): hardcoded target sets в гейтах/дрейф Makefile↔
##            манифест — класс «один писатель инварианта»; детектор ловит расхождение.
## @changes 2026-08-13 | DevPlan 163 W-C C2 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging

from core.internal.static.verb_register import detect
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


def _write_verb_tree(root, phony_lines: list[str], allowed: list[str]) -> None:
    """Записать probe-дерево verb_register: Makefile(.PHONY) + манифест(allowed_verbs).

    ## @purpose  Переиспользуемая фикстура-хелпер (DRY внутри файла).
    ## @io       ⇥ root: Path, phony_lines: list[str], allowed: list[str] → None
    ## @complexity  O(1)
    """
    (root / "Makefile").write_text("\n".join(phony_lines) + "\n", encoding="utf-8")
    (root / "core").mkdir(parents=True, exist_ok=True)
    (root / "core" / "entrypoint-manifest.yaml").write_text(
        "allowed_verbs:\n" + "".join(f"- {v}\n" for v in allowed) + "\n"
        "name_linter:\n"
        "  system_prefixes:\n"
        "  - test-\n"
        "  - gate-\n"
        "  - pre-commit-\n",
        encoding="utf-8",
    )


# 🧪 TRAP[TEST] · NEGATIVE (R5) · .PHONY таргет вне allowed_verbs → RED (extra, G1.2)
# · Scenario: Makefile .PHONY: rogue-verb при allowed_verbs=[my-verb] — точный класс
# ·   инварианта гейта test_all_phony_targets_discovered (extra = targets − expected ≠ ∅)
# · Last fail: hardcoded target sets / нерегистрированные таргеты (G1.2 drift)
# · Remove if: name-linter / verb-register гейт отменяется
@ldd_trajectory
def test_verb_register_negative_unregistered_target(caplog, tmp_path) -> None:
    """R5 negative: .PHONY таргет вне allowed_verbs (extra-класс G1.2) детектируется."""
    _write_verb_tree(tmp_path, phony_lines=[".PHONY: my-verb rogue-verb"], allowed=["my-verb"])
    findings = detect(tmp_path)
    hits = [f for f in findings if "not registered" in f.message]
    assert hits, "R5 FAIL: unregistered .PHONY target (extra) not detected"
    assert "rogue-verb" in hits[0].message
    logger.info("[IMP:9][test_verb_register] R5 extra target RED: %s", hits[0])


# 🧪 TRAP[TEST] · POSITIVE · allowed_verb без .PHONY таргета → RED (missing-класс)
# · Scenario: манифест allowed_verbs=[my-verb, ghost-verb], Makefile .PHONY: my-verb —
# ·   ghost-verb зарегистрирован, но таргета нет (missing = expected − targets ≠ ∅)
# · Last fail: N/A (зеркальный класс к extra)
# · Remove if: verb-register детектор отменяется
@ldd_trajectory
def test_verb_register_missing_target_for_verb(caplog, tmp_path) -> None:
    """Positive: allowed_verb без .PHONY таргета (missing-класс) детектируется."""
    _write_verb_tree(tmp_path, phony_lines=[".PHONY: my-verb"], allowed=["my-verb", "ghost-verb"])
    findings = detect(tmp_path)
    hits = [f for f in findings if "without .PHONY target" in f.message]
    assert hits, "R5 FAIL: registered verb without target (missing) not detected"
    assert "ghost-verb" in hits[0].message
    logger.info("[IMP:9][test_verb_register] missing target RED: %s", hits[0])


# 🧪 TRAP[TEST] · CONTROL · полная parity + system_exceptions → PASS
# · Scenario: Makefile .PHONY: my-verb help help-all venv; allowed_verbs=[my-verb]; system_exceptions
# ·   = {help, help-all, venv} → полное покрытие, 0 RED
# · Last fail: N/A (control — легитимная parity не должна быть RED)
# · Remove if: verb-register детектор отменяется
@ldd_trajectory
def test_verb_register_full_parity_not_flagged(caplog, tmp_path) -> None:
    """PASS-контроль: allowed_verbs + system_exceptions полностью покрыты таргетами."""
    _write_verb_tree(tmp_path, phony_lines=[".PHONY: my-verb help help-all venv"], allowed=["my-verb"])
    findings = detect(tmp_path)
    assert findings == [], f"PASS-control FAIL: full parity flagged: {findings}"
    logger.info("[IMP:9][test_verb_register] full parity (allowed + system_exceptions) not flagged")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · hardcoded target set в gate-файле → RED (G1.2)
# · Scenario: tests/gates/_gate_probe_targets.py с set-литералом 3+ таргет-паттернов
# ·   ({deploy, up, down, restart}) — точный класс exception_audit (hardcoded target sets
# ·   должны читаться из entrypoint-manifest.yaml, G1.2) → RED
# · Last fail: N/A (R5-пара для verb_register расширения exception-audit)
# · Remove if: verb-register детектор отменяется
@ldd_trajectory
def test_verb_register_negative_hardcoded_target_set(caplog, tmp_path) -> None:
    """R5 negative: hardcoded target set (3+ таргет-паттернов) в gate-файле → RED."""
    _write_verb_tree(tmp_path, phony_lines=[".PHONY: my-verb help help-all venv"], allowed=["my-verb"])
    gates_dir = tmp_path / "tests" / "gates"
    gates_dir.mkdir(parents=True)
    probe = gates_dir / "test_gate_probe_targets.py"
    probe.write_text('_TARGETS = {"deploy", "up", "down", "restart"}\n', encoding="utf-8")
    findings = detect(tmp_path)
    hits = [f for f in findings if "hardcoded target set" in f.message]
    assert hits, "R5 FAIL: hardcoded target set in gate (G1.2) not detected"
    assert "deploy" in hits[0].message
    logger.info("[IMP:9][test_verb_register] R5 hardcoded target set RED: %s", hits[0])


# 🧪 TRAP[TEST] · CONTROL · allowlisted non-target set → PASS
# · Scenario: tests/gates/_gate_probe_allowed.py с set-литералом, присвоенным allowlisted
# ·   non-target имени (_CRITICAL_15S = {10, 15, 30}) → 0 RED (не make-таргеты, D4)
# · Last fail: N/A (control — healthcheck interval классы легитимны)
# · Remove if: verb-register детектор отменяется
@ldd_trajectory
def test_verb_register_allowlisted_non_target_set_not_flagged(caplog, tmp_path) -> None:
    """PASS-контроль: allowlisted non-target set (healthcheck классы) не RED."""
    _write_verb_tree(tmp_path, phony_lines=[".PHONY: my-verb help help-all venv"], allowed=["my-verb"])
    gates_dir = tmp_path / "tests" / "gates"
    gates_dir.mkdir(parents=True)
    probe = gates_dir / "test_gate_probe_allowed.py"
    probe.write_text("_CRITICAL_15S = {10, 15, 30}\n", encoding="utf-8")
    findings = detect(tmp_path)
    hits = [f for f in findings if "hardcoded target set" in f.message]
    assert not hits, f"PASS-control FAIL: allowlisted non-target set flagged: {hits}"
    logger.info("[IMP:9][test_verb_register] allowlisted non-target set not flagged")
