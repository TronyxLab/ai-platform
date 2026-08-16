"""Static layer: bare-raise detector tests (DevPlan 163 W-C C3).

# GREP_SUMMARY: test-static bare-raise ValueError RuntimeError PlatformError typed-exceptions R5 U-12
# STRUCTURE: ▶ synthetic raise RuntimeError в probe → RED | ▶ R5-оригинал U-12 (raise ValueError
#            в core/internal-фикстуре) → RED | ▶ control: raise ConfigValidationError → PASS → ⎋
"""
# region MODULE_CONTRACT
## @purpose  R5-пары детектора bare_raise (DevPlan 163 W-C C3): позитивный тест на
##           синтетическое нарушение (raise RuntimeError), R5-негатив на ОРИГИНАЛЬНЫЙ
##           вход гейта (raise ValueError в core/internal — класс U-12: 40 bare raise,
##           caller не мог различить тип ошибки), PASS-контроль (типизированная иерархия).
## @scope    Native imports; probe-файлы в tmp_path (для деревьев без core/ детектор
##           сканирует root.rglob("*.py")).
## @invariants
##   - raise ValueError/RuntimeError (Call|Name) → RED
##   - raise ConfigValidationError (typed hierarchy) → PASS
##   - Allowlist пуст
## @rationale R5 anti-survivorship (U-12): 40 bare raise — caller не может программно
##            различить тип ошибки; иерархия PlatformError создана (038a).
## @changes 2026-08-13 | DevPlan 163 W-C C3 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging

from core.internal.static.bare_raise import detect
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · POSITIVE · synthetic raise RuntimeError → RED
# · Scenario: probe `raise RuntimeError("boom")` — Call-форма запрещённого типа → RED
# · Last fail: N/A (синтетический вариант)
# · Remove if: bare-raise гейт отменяется
@ldd_trajectory
def test_bare_raise_synthetic_runtime_error(caplog, tmp_path) -> None:
    """Synthetic positive: raise RuntimeError (Call-форма) детектируется."""
    probe = tmp_path / "_probe_bare.py"
    probe.write_text('def run():\n    raise RuntimeError("boom")\n', encoding="utf-8")
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_bare" in f.file]
    assert hits, "R5 FAIL: raise RuntimeError not detected"
    assert "RuntimeError" in hits[0].message
    logger.info("[IMP:9][test_bare_raise] synthetic RuntimeError RED: %s", hits[0])


# 🧪 TRAP[TEST] · NEGATIVE (R5) · оригинал U-12: raise ValueError в core/internal → RED
# · Scenario: probe core/internal/probe.py с `raise ValueError("x")` — точный класс
# ·   U-12 (40 bare raise в core/internal, гейт B4 T5 запрещает ValueError/RuntimeError)
# · Last fail: DevPlan 116 B4 T5 — 40 bare raise, caller не мог различить тип ошибки
# · Remove if: bare-raise гейт отменяется
@ldd_trajectory
def test_bare_raise_negative_original_u12_input(caplog, tmp_path) -> None:
    """R5 negative: оригинальный вход U-12 — raise ValueError в core/internal фикстуре."""
    probe_dir = tmp_path / "core" / "internal"
    probe_dir.mkdir(parents=True)
    probe = probe_dir / "probe.py"
    probe.write_text('def parse():\n    raise ValueError("invalid")\n', encoding="utf-8")
    findings = detect(tmp_path)
    hits = [f for f in findings if "probe.py" in f.file]
    assert hits, "R5 FAIL: raise ValueError (U-12 original class) not detected"
    assert "ValueError" in hits[0].message
    logger.info("[IMP:9][test_bare_raise] R5 U-12 ValueError RED: %s", hits[0])


# 🧪 TRAP[TEST] · CONTROL · типизированная иерархия → PASS
# · Scenario: probe `raise ConfigValidationError("x")` — типизированная иерархия
# ·   PlatformError (shared/exceptions.py), НЕ ValueError/RuntimeError → 0 RED
# · Last fail: N/A (control — typed hierarchy легитимна)
# · Remove if: bare-raise гейт отменяется
@ldd_trajectory
def test_bare_raise_typed_hierarchy_not_flagged(caplog, tmp_path) -> None:
    """PASS-контроль: raise типизированной иерархии PlatformError не RED."""
    probe = tmp_path / "_probe_typed.py"
    probe.write_text('def parse():\n    raise ConfigValidationError("invalid")\n', encoding="utf-8")
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_typed" in f.file]
    assert not hits, f"PASS-control FAIL: typed hierarchy flagged: {hits}"
    logger.info("[IMP:9][test_bare_raise] typed PlatformError hierarchy not flagged")
