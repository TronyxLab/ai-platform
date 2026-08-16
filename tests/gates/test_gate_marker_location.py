#!/usr/bin/env python3
# GREP_SUMMARY: gate marker-location zombie-gates pytest-mark-gate-outside-tests-gates anti-drift ast-scan R5-negative DevPlan-119-A1
# STRUCTURE: ▶ AST-скан tests/**/*.py (вне tests/gates/) → ○ decorator == pytest.mark.gate? → ⊕ offenders (rel, lineno) → ⟦RED: file:line⟧ | 0 → PASS → ⎋ R5 negative (probe-файл вне gates/ → обнаружен)
# region MODULE_CONTRACT
## @purpose  Anti-drift gate (DevPlan 119 A1, AUDIT-6 F1): НИ ОДИН @pytest.mark.gate не должен
##           находиться вне tests/gates/. Зомби-гейты (маркер вне gates/) не исполняются ни одним
##           таргетом (`make gate MODE=fast` = pytest tests/gates/ -m gate) — дыра обнаружения.
##           Гейт сканирует ВСЕ .py-файлы tests/ рекурсивно (AST — не regex: ловит только
##           реальные декораторы, не строки/комментарии) и RED при любом маркере вне gates/.
## @scope    Все .py-файлы под tests/ (исключая tests/gates/ и __pycache__). Читает AST-декораторы
##           FunctionDef/ClassDef: @pytest.mark.gate и @pytest.mark.gate(...) — оба детектируются.
## @invariants
##   - RED: FunctionDef/ClassDef с декоратором pytest.mark.gate вне tests/gates/
##   - Исключение: tests/gates/ и __pycache__ (скомпилированные .pyc не сканируются)
##   - Regex-вхождения "pytest.mark.gate" в строках/комментариях — НЕ нарушение (AST-only)
##   - R5 negative: искусственный маркер вне tests/gates/ → обнаружен (anti-survivorship)
## @rationale  Gate Trinity (tests/gates/AGENTS.md): файл в tests/gates/ + маркер + manifest-запись.
##             Зомби-маркер вне gates/ = «гейт заявлен, но не исполняется» — silent loophole.
##             26 зомби-маркеров волны 118 (12 cross-layer + 6 smoke-isolation + 3 template-syntax
##             + 4 bootstrap-no-duplicate + 1 no-backward-compat) устранены в A1 (перенос/снятие).
## @changes  2026-08-02 | DevPlan 119 A1 — Created (анти-drift гейт, закрывает AUDIT-6 F1)
# endregion MODULE_CONTRACT

import ast
import logging

import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
_TESTS_DIR = ROOT / "tests"
_GATES_DIR = _TESTS_DIR / "gates"


# region FUNC_is_gate_marker
def _is_gate_marker(decorator: ast.AST) -> bool:
    """Определить, является ли AST-декоратор `@pytest.mark.gate` (с или без скобок).

    ▶ ┌decorator node┐ → ◇ Call? (unwrap func) → ◇ Attribute attr=='gate' ∧ value==pytest.mark → ⎋ bool
    ## @purpose  AST-матчинг декоратора: покрывает `@pytest.mark.gate` и `@pytest.mark.gate(...)`.
    ## @io — ⇥ decorator: ast.AST → ⎋ bool
    ## @complexity — O(1) — константная проверка структуры узла
    """
    node = decorator
    if isinstance(node, ast.Call):
        node = node.func
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "gate"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "pytest"
    )


# endregion FUNC_is_gate_marker


# region FUNC_find_gate_markers_outside_gates
def _find_gate_markers_outside_gates(include_probes: bool = False) -> list[tuple[str, int]]:
    """Найти @pytest.mark.gate-декораторы вне tests/gates/ (AST-скан).

    ▶ ┌tests/**/*.py (вне gates/)┐ → ○ ast.parse → ○ walk FunctionDef/ClassDef → ◇ decorator gate?
      → ⊕ offenders (rel, lineno) → ⎋ list
    ## @purpose  Ядро анти-drift гейта: рекурсивный AST-скан всех .py под tests/,
    ##            исключая tests/gates/ (легитимная зона) и __pycache__.
    ## @io — ⇥ include_probes: bool — True → включать _gate_probe_* (R5-negative), False → исключать
    ## @complexity — O(F * N) где F = файлы, N = AST-узлы на файл
    ## @invariants
    ##   - Пропускает __pycache__ (скомпилированные артефакты)
    ##   - Пропускает tests/gates/ (единственная легитимная зона gate-маркеров)
    ##   - Синтаксически-битые файлы (SyntaxError) пропускаются (не тест-файлы)
    ##   - _gate_probe_* исключается по умолчанию (канон DevPlan 119 H): probe-артефакты
    ##     R5-тестов — НЕ продукт; позитивный скан не должен видеть параллельный negative-probe
    ##     (xdist-гонка 2026-08-12 — см. TRAP в negative-тесте)
    """
    offenders: list[tuple[str, int]] = []
    for p in sorted(_TESTS_DIR.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(_TESTS_DIR).as_posix()
        if rel.startswith("gates/"):
            continue
        if not include_probes and "_gate_probe_" in rel:
            continue
        try:
            tree = ast.parse(p.read_text(errors="replace"))
        except (OSError, SyntaxError):
            continue
        offenders.extend(
            (rel, node.lineno)
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))
            for dec in node.decorator_list
            if _is_gate_marker(dec)
        )
    return offenders


# endregion FUNC_find_gate_markers_outside_gates


# region TEST_no_gate_markers_outside_gates_dir
@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · REGRESSION · зомби-гейты вне tests/gates/ (DevPlan 119 A1, AUDIT-6 F1)
# · Scenario: AST-скан tests/ → 0 @pytest.mark.gate вне tests/gates/
# · Last fail: 26 зомби-маркеров в 5 файлах (cross_layer 12, smoke_isolation 6, template_syntax 3,
# ·   bootstrap_no_duplicate 4, no_backward_compat 1) — не исполнялись ни одним таргетом
# · Remove if: Gate Trinity (файл + маркер + manifest) отменяется
def test_no_gate_markers_outside_gates_dir(caplog) -> None:
    """0 @pytest.mark.gate вне tests/gates/ — зомби-гейты запрещены (DevPlan 119 A1)."""
    caplog.set_level(logging.INFO)
    offenders = _find_gate_markers_outside_gates()
    if offenders:
        for rel, lineno in offenders:
            logger.error("[IMP:10][marker_location] %s:%d @pytest.mark.gate outside tests/gates/", rel, lineno)
        pytest.fail(
            f"@pytest.mark.gate outside tests/gates/ ({len(offenders)}):\n"
            + "\n".join(f"  - {rel}:{lineno}" for rel, lineno in offenders)
            + "\n\nGate-тесты живут ТОЛЬКО в tests/gates/ (Gate Trinity, tests/gates/AGENTS.md). "
            "Перенеси gate-функции в tests/gates/ ИЛИ сними маркер с unit (DevPlan 119 A1)."
        )

    logger.info("[IMP:9][marker_location] PASS: 0 @pytest.mark.gate outside tests/gates/")


# endregion TEST_no_gate_markers_outside_gates_dir


# region TEST_gate_marker_outside_detected_negative
@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · NEGATIVE (R5) · детектор зомби-маркеров (DevPlan 119 A1)
# · Scenario: искусственный tests/_gate_probe_marker_tmp/test_zombie_probe.py с @pytest.mark.gate
# · Last fail: N/A (новый negative-тест — исходный вход AUDIT-6 F1 = маркер вне gates/)
# · Remove if: анти-drift гейт отменяется
def test_gate_marker_outside_detected_negative(caplog) -> None:
    """R5 negative: искусственный @pytest.mark.gate вне tests/gates/ → обнаружен.

    ## @purpose — Anti-survivorship: доказывает, что детектор ловит исходный вход
    ##            (маркер вне gates/), а не пропускает его после переноса 26 зомби-гейтов.
    ## @io — ⎋ None (assert: probe-маркер обнаружен)
    ## @complexity — O(F) — один временный файл
    """
    caplog.set_level(logging.INFO)
    # 2026-08-04 (DevPlan 129 W2): xdist-гонка устранена exclusions
    # · сканеров-жертв (DevPlan 119 C / 124): _gate_probe_marker_tmp в _EXCLUDED_DIRS
    # · test_gate_grep_summary.py и _PROBE_DIR_PARTS test_gate_test_infra_consistency.py.
    # · Probe остаётся в РЕАЛЬНОМ tests/ (не tmp_path) НАМЕРЕННО: _find_gate_markers_outside_gates()
    # · сканирует рабочий tests/ — probe вне его не был бы детектирован (см. TRAP[DECISION] ниже).
    # ⚠️ TRAP[BUG] · 2026-08-12 · HI · xdist-гонка R5 probe: фиксированное имя probe + параллельные
    # · make gate (pre-push hook) → negative падал «NOT detected» (другой воркер удалил чужой probe
    # · в finally) ИЛИ позитивный скан видел чужой probe. Fix: уникальное имя probe (uuid) +
    # · позитивный скан исключает _gate_probe_* (канон DevPlan 119 H), negative — include_probes=True.
    import uuid

    probe_dir = _TESTS_DIR / "_gate_probe_marker_tmp"
    probe_name = f"test_zombie_probe_{uuid.uuid4().hex[:8]}.py"
    probe = probe_dir / probe_name
    probe_dir.mkdir(parents=True, exist_ok=True)
    probe.write_text("import pytest\n@pytest.mark.gate\ndef test_zombie_probe():\n    assert True\n")
    try:
        offenders = _find_gate_markers_outside_gates(include_probes=True)
        hits = [o for o in offenders if probe_name in o[0]]
        assert hits, "R5 FAIL: @pytest.mark.gate outside tests/gates/ was NOT detected"
        logger.info("[IMP:9][marker_location][R5] PASS: probe %s:%d detected", hits[0][0], hits[0][1])
    finally:
        probe.unlink(missing_ok=True)
        try:
            probe_dir.rmdir()
        except OSError as exc:
            logger.warning("[IMP:7][marker_location] Probe dir cleanup skipped: %s", exc)


# endregion TEST_gate_marker_outside_detected_negative
