# GREP_SUMMARY: gate test-naming ast-scan dead-tests test-prefix underscore coverage R5-negative 171-W2.3
# STRUCTURE: ┌_scan_for_dead_test_names (ast walk tests/)┐ → ◇ name ^test(?!_) + (assert | args)? → violations →
#            ◇ test_no_dead_test_names → ◇ R5-negative (tmp_path fixture: testcheck_* / testrender_* caught) → ⎋ gate green
# region MODULE_CONTRACT
## @purpose  Gate enforcing pytest naming convention (DevPlan 171 W2.3): функция с именем,
##           начинающимся на `test` БЕЗ подчёркивания-разделителя (testcheck_x, testrender_x,
##           testFoo), в теле которой есть assert или которая принимает параметры (фикстурный
##           профиль), молча выпадает из pytest-сбора — мёртвое покрытие. Gate = RED.
## @scope    tests/**/*.py, excluding __pycache__. Allowlist EMPTY (strict mode).
## @invariants
##   - Детектор: имя матчится ^test(?!_) и (тело содержит assert ИЛИ функция имеет параметры)
##   - Конвенция: pytest-тест = test_* (underscore-разделитель); testFoo / testcheck_x — нарушение
##   - Скан парсит AST (код), никогда docstrings/comments
##   - R5-negative доказывает, что детектор ловит точный регрессионный вход (testcheck_/testrender_)
##   - Тринити: файл в tests/gates/ + @pytest.mark.gate + запись в entrypoint-manifest (G3)
## @rationale  19 функций в 6 файлах потеряли покрытие из-за отсутствия test_-префикса
##             (мёртвые тесты, найденные аудитом W2.1/W2.2). Структурный детектор предотвращает
##             возврат класса дефекта — категорийное правило, не список имён.
## @changes  2026-08-15 · Created (DevPlan 171 W2.3)
# endregion MODULE_CONTRACT

import ast
import pathlib
import re

import pytest

_TESTS_DIR = pathlib.Path(__file__).resolve().parent.parent  # tests/

# Имя теста: начинается с "test", следующий символ — НЕ "_" (testFoo, testcheck_x, testrender_x).
_NAME_RE = re.compile(r"^test(?!_)[a-zA-Z0-9_]*$")


# region FUNC_scan_for_dead_test_names
## @purpose  AST-скан tests/**: функции с именем ^test(?!_) и (assert в теле или параметрами).
## @io       ⇥ root: Path → ⎋ list[tuple[Path, int, str]] — (file, lineno, name) нарушений
## @complexity O(F * N) where F = files, N = AST nodes
def _scan_for_dead_test_names(root: pathlib.Path) -> list[tuple[pathlib.Path, int, str]]:
    """Find pytest-dead test names: `test` prefix without underscore separator."""
    violations: list[tuple[pathlib.Path, int, str]] = []
    for py_file in sorted(root.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _NAME_RE.match(node.name):
                continue
            has_assert = any(isinstance(n, ast.Assert) for n in ast.walk(node))
            has_args = bool(node.args.args)
            if has_assert or has_args:
                violations.append((py_file, node.lineno, node.name))
    return violations


# endregion FUNC_scan_for_dead_test_names


# 🧪 TRAP[TEST] · Regression · test-names without underscore silently lose coverage (DevPlan 171 W2.3)
# · Scenario: tests/test_x.py: `def testcheck_foo(...)` with assert → pytest never collects it
# · Last fail: N/A (new gate)
# · Remove if: pytest naming convention changes
@pytest.mark.gate
def test_no_dead_test_names() -> None:
    """RED if any function in tests/ has `test`-prefix without underscore and assert/args."""
    violations = _scan_for_dead_test_names(_TESTS_DIR)
    if violations:
        lines = "\n".join(f"  {f.relative_to(_TESTS_DIR.parent)}:{lineno}: {name}" for f, lineno, name in violations)
        pytest.fail(
            f"Dead pytest names detected ({len(violations)}): functions starting with `test` "
            f"without underscore are never collected. Rename to test_<name>.\n{lines}"
        )


# 🧪 TRAP[TEST] · R5-negative · 171-W2.3 · detector fires on the exact regression input
# · Original form: testcheck_env_requires_* (6), testvalidate_secret_charsets_* (4),
# ·   testget_module_severity_* (4), testrender_sudoers_rules_*, testbatch_generate_sudoers_*,
# ·   testtopo_sort_*, testpre_pull_images_single, testjunit_counts_reads_testsuite_attrs —
# ·   функции с assert, потерявшие покрытие из-за имени без test_-префикса.
# · Scenario: tmp_path с `def testcheck_dead(manifest): assert True` → детектор должен поймать.
@pytest.mark.gate
def test_naming_detector_negative_dead_name(tmp_path: pathlib.Path) -> None:
    """R5-negative: dead test name in tmp_path is detected by the scanner."""
    probe = tmp_path / "test_x.py"
    probe.write_text(
        "def testcheck_dead(manifest):\n    assert manifest is not None\n",
        encoding="utf-8",
    )

    violations = _scan_for_dead_test_names(tmp_path)

    assert len(violations) == 1, f"Expected exactly 1 violation, got {violations}"
    assert violations[0][2] == "testcheck_dead", f"Expected testcheck_dead, got {violations[0][2]}"


# 🧪 TRAP[TEST] · R5-negative · 171-W2.3 · valid names are NOT flagged (no false positives)
# · Scenario: tmp_path с `def test_check_ok(...)` и `def helper_plain(...)` → 0 нарушений.
@pytest.mark.gate
def test_naming_detector_negative_valid_names(tmp_path: pathlib.Path) -> None:
    """R5-negative: proper test_* names and non-test helpers are NOT flagged."""
    probe = tmp_path / "test_ok.py"
    probe.write_text(
        "def test_check_ok(manifest):\n    assert manifest is not None\n\ndef helper_plain(x):\n    return x\n",
        encoding="utf-8",
    )

    violations = _scan_for_dead_test_names(tmp_path)

    assert violations == [], f"Expected no violations, got {violations}"
