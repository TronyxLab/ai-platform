# GREP_SUMMARY: gate r1-no-pass-tests test-honesty constant-assert bare-pass except no-assert ast-scan allowlist-empty R5-negative B10 per-function F1
# STRUCTURE: ┌_scan_source_for_pass_tests (ast, per-function)┐ → ◇ constant assert? ⊕ → ◇ bare-pass except? ⊕ → ◇ per-function fail-mechanism? ⊕ →
#            violations list → ◇ test_r1_no_pass_tests (walk tests/ excl. _conftest/helpers/tools/test_data/e2e-fixtures) →
#            ◇ R5 negatives (inline fixtures: assert True / bare-pass / no-assert / function-without-assert) → ⎋ gate green
# region MODULE_CONTRACT
## @purpose  Gate test enforcing Test Honesty R1 (.kilo/rules/testing.md): zero pass-tests in tests/.
##           AST-scans every tests/**/*.py (excluding non-test modules) and RED on:
##             (а) assert with a constant expression (True/False/None/number/string/tuple of constants),
##             (б) except-block with a bare `pass` (except:/except X: → pass — swallowed exception),
##             (в) a test FUNCTION whose body has no fail mechanism (assert, pytest.fail/raises, raise,
##                 mock assert_* method call) — per-function scan (DevPlan 118 F1, AC-F1). File-level
##                 rule is NOT enough: a file with one asserting function hides sibling pass-functions.
## @scope    tests/ tree only, excluding tests/_conftest/, tests/helpers/, tests/tools/, tests/test_data/,
##           tests/e2e/fixtures/ (non-test modules). Allowlist is EMPTY (strict mode, DevPlan 116 B8 D3 pattern).
## @invariants
##   - Allowlist empty — no exceptions for constant asserts or bare-pass excepts
##   - Per-function exemptions (decorators ONLY): @pytest.fixture (fixture, not test),
##     r1_delegates (tests/_conftest/r1.py — documented delegation to a raising helper/fixture),
##     pure pytest.skip body (skip-test, R3 domain — not a pass-test)
##   - Scan parses AST (code), never docstrings/comments — `assert True` in prose is ignored
##   - Rule (в) counts assert / pytest.fail / pytest.raises / raise / mock assert_* as fail mechanisms
##   - R5 negative tests prove each detector fires on the exact regression input
##   - Registered in core/entrypoint-manifest.yaml gates (trinity) with repair_class L2
## @rationale  U-69 (11-Brief AC1): pass-tests are unfalsifiable — a test that cannot fail is not a test.
##             R1 gate prevents return of `assert True` / constant asserts / swallowed-exception pass blocks.
##             DevPlan 118 F1: file-level rule (в) let pass-functions hide behind asserting siblings
##             (test_gate_compose_base_contract, test_gate_ci_env_vars, test_gate_workflow_consistency) —
##             per-function scan closes the hole; delegating tests get the r1_delegates marker.
##             CONSTITUTION-4: bare `except: pass` hides errors — flagged as test-code smell.
## @changes  2026-08-01 · Created (DevPlan 116 B10 T1)
## @changes  2026-08-02 · Per-function rule (в) + r1_delegates exemption (DevPlan 118 F1)
# endregion MODULE_CONTRACT

import ast
import pathlib

import pytest

# ── Directories that are NOT test modules (helper/infra/fixture code) ──
_EXCLUDED_DIRS = {"_conftest", "helpers", "tools", "test_data"}
# Транзиентные probe-директории других R5-гейтов (test_gate_marker_location) — создаются и
# удаляются во время gate-сессии; их содержимое (assert True) не должно влиять на вердикт R1
# (race: параллельный xdist-скан ловил probe-файл, DevPlan 119 B — фикс flaky race).
_EXCLUDED_DIRS |= {"_gate_probe_marker_tmp"}

_TESTS_DIR = pathlib.Path(__file__).resolve().parent.parent  # tests/

# Decorators that exempt a test function from the per-function fail-mechanism rule.
_EXEMPT_DECORATORS = {"fixture", "r1_delegates"}

# Mock assertion methods — these raise AssertionError on failure (real fail mechanisms).
_MOCK_ASSERT_METHODS = {
    "assert_called",
    "assert_called_once",
    "assert_called_with",
    "assert_called_once_with",
    "assert_not_called",
    "assert_any_call",
    "assert_has_calls",
}


# region FUNC_is_constant_expr
## @purpose  True if an AST expression is a compile-time constant (ast.Constant) or a
##           non-empty tuple of constants. True/False/None are ast.Constant in py3.8+.
## @io       ⇥ node: ast.AST → ⎋ bool
## @complexity O(T) where T = tuple element count
def _is_constant_expr(node: ast.AST) -> bool:
    """Detect `assert <constant>`: literal constants and tuples of constants."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Tuple) and len(node.elts) > 0:
        return all(_is_constant_expr(e) for e in node.elts)
    return False


# endregion FUNC_is_constant_expr


# region FUNC_is_fail_mechanism_call
## @purpose  True for pytest.fail(...) / pytest.raises(...) calls — assertion-equivalents.
## @io       ⇥ node: ast.AST → ⎋ bool
## @complexity O(1)
def _is_fail_mechanism_call(node: ast.AST) -> bool:
    """Detect pytest.fail() / pytest.raises() — a test using only these can still fail."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "pytest"
        and node.func.attr in {"fail", "raises"}
    )


# endregion FUNC_is_fail_mechanism_call


# region FUNC_has_decorator
## @purpose  True if a function node carries any of the given decorator names (F1 per-function).
## @io       ⇥ node: ast.FunctionDef, names: set[str] → ⎋ bool
## @complexity O(D) where D = decorator count
def _has_decorator(node: ast.FunctionDef, names: set[str]) -> bool:
    """Detect decorators by final name — @pytest.fixture, r1_delegates, etc."""
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name) and dec.id in names:
            return True
        if isinstance(dec, ast.Attribute) and dec.attr in names:
            return True
        if isinstance(dec, ast.Call):
            func = dec.func
            if isinstance(func, ast.Name) and func.id in names:
                return True
            if isinstance(func, ast.Attribute) and func.attr in names:
                return True
    return False


# endregion FUNC_has_decorator


# region FUNC_is_pure_skip_function
## @purpose  True if the function body is essentially a single pytest.skip(...) call
##           (skip-test — R3 domain, not a pass-test).
## @io       ⇥ node: ast.FunctionDef → ⎋ bool
## @complexity O(S) where S = statements
def _is_pure_skip_function(node: ast.FunctionDef) -> bool:
    """A skip-only test function (body = pytest.skip call) is not a pass-test."""
    statements = [s for s in node.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    if len(statements) != 1:
        return False
    stmt = statements[0]
    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
        return False
    call = stmt.value
    if isinstance(call.func, ast.Attribute) and call.func.attr == "skip":
        return True
    return isinstance(call.func, ast.Name) and call.func.id == "skip"


# endregion FUNC_is_pure_skip_function


# region FUNC_has_fail_mechanism_in_body
## @purpose  True if a function body contains ANY fail mechanism: assert / raise /
##           pytest.fail / pytest.raises / mock assert_* method call.
## @io       ⇥ node: ast.FunctionDef → ⎋ bool
## @complexity O(N) where N = AST nodes in body
## @invariants
##   - Walks the whole function body (nested calls included) — an assert inside a helper
##     closure called by the test still counts (best-effort; the canonical escape hatch for
##     genuine delegation is r1_delegates).
def _has_fail_mechanism_in_body(node: ast.FunctionDef) -> bool:
    """Per-function (в): does the test function body contain a fail mechanism?"""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Assert):
            return True
        if isinstance(sub, ast.Raise):
            return True
        if _is_fail_mechanism_call(sub):
            return True
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr in _MOCK_ASSERT_METHODS:
            return True
    return False


# endregion FUNC_has_fail_mechanism_in_body


# region FUNC_iter_test_functions
## @purpose  Yield module-level and class-level test functions (name starts with test_).
## @io       ⇥ tree: ast.Module → ⎋ Iterator[ast.FunctionDef]
## @complexity O(F) where F = functions
def _iter_test_functions(tree: ast.Module):
    """Yield test functions: module-level `test_*` defs and methods of any class."""
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            yield node
        elif isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, ast.FunctionDef) and member.name.startswith("test_"):
                    yield member


# endregion FUNC_iter_test_functions


# region FUNC_scan_source_for_pass_tests
## @purpose  AST-scan a single test-file source for R1 violations:
##           (а) constant assert, (б) bare-pass except, (в) per-function no-fail-mechanism.
## @io       ⇥ source: str, file_name: str → ⎋ list[str] violations (empty = clean)
## @complexity O(N) where N = AST nodes
## @invariants
##   - Rule (в) applies per-function with decorator exemptions (pytest.fixture, r1_delegates,
##     pure-skip) — closes the file-level hole where an asserting sibling hides pass-functions
##   - Constant assert detection ignores the assert message (second arg)
##   - Bare-pass except: body is exactly one `pass` statement
def _scan_source_for_pass_tests(source: str, file_name: str) -> list[str]:
    """Return R1 violation descriptions for a test-file source (empty = clean)."""
    violations: list[str] = []
    try:
        tree = ast.parse(source, filename=file_name)
    except SyntaxError as exc:
        return [f"{file_name}: SYNTAX ERROR in test file: {exc.msg} (line {exc.lineno})"]

    # ── File-level rules: (а) constant assert, (б) bare-pass except ──
    has_fail_mechanism = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            has_fail_mechanism = True
            if _is_constant_expr(node.test):
                expr = ast.unparse(node.test)
                violations.append(
                    f"{file_name}:{node.lineno}: constant assert ({expr}) — R1 pass-test "
                    "(assert on a literal can never fail)"
                )
        elif isinstance(node, ast.ExceptHandler):
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                violations.append(
                    f"{file_name}:{node.lineno}: bare `pass` in except handler — swallowed "
                    "exception, R1/CONSTITUTION-4 violation"
                )
        elif isinstance(node, ast.Raise) or _is_fail_mechanism_call(node):
            has_fail_mechanism = True

    if not has_fail_mechanism and pathlib.Path(file_name).name.startswith("test_"):
        # A file whose ONLY test functions are exempt (@pytest.fixture / @r1_delegates /
        # pure-skip) is not a pass-file — the per-function rule already handles each function.
        has_exempt_test = any(
            _has_decorator(func, _EXEMPT_DECORATORS) or _is_pure_skip_function(func)
            for func in _iter_test_functions(tree)
        )
        if not has_exempt_test:
            violations.append(
                f"{file_name}: test file without any assertion mechanism "
                "(no assert, no pytest.fail/raises, no raise) — R1 pass-test"
            )

    # ── Per-function rule (в) — DevPlan 118 F1: a test function with no fail mechanism
    #    in its own body is a pass-test even if sibling functions in the file assert. ──
    if pathlib.Path(file_name).name.startswith("test_"):
        for func in _iter_test_functions(tree):
            if _has_decorator(func, _EXEMPT_DECORATORS):
                continue  # fixture or documented @r1_delegates delegation
            if _is_pure_skip_function(func):
                continue  # skip-test — R3 domain, not a pass-test
            if not _has_fail_mechanism_in_body(func):
                violations.append(
                    f"{file_name}:{func.lineno}: test function '{func.name}' without assertion "
                    "mechanism in its body (no assert, no pytest.fail/raises, no raise, no "
                    "mock assert_*) — R1 pass-test (DevPlan 118 F1 per-function scan)"
                )
    return violations


# endregion FUNC_scan_source_for_pass_tests


# region FUNC_iter_test_files
## @purpose  Iterate scannable test files under tests/ — excluding non-test module dirs.
## @io       ⇥ tests_dir: pathlib.Path → ⎋ Iterator[pathlib.Path]
## @complexity O(F) where F = files
## @invariants
##   - Excludes tests/_conftest/, tests/helpers/, tests/tools/, tests/test_data/,
##     and tests/e2e/fixtures/ (any depth)
def _iter_test_files(tests_dir: pathlib.Path):
    """Yield scannable test files (excludes non-test module directories)."""
    for path in sorted(tests_dir.rglob("*.py")):
        rel = path.relative_to(tests_dir)
        if any(part in _EXCLUDED_DIRS for part in rel.parts):
            continue
        if rel.parts[0] == "e2e" and "fixtures" in rel.parts:
            continue
        yield path


# endregion FUNC_iter_test_files


# region FUNC_test_r1_no_pass_tests
## @purpose  Gate: scan the whole tests/ tree for R1 pass-test violations.
## @io       ⇥ (none) → ⎋ None (assert side-effect)
## @complexity O(F * N) where F = test files, N = AST nodes per file
## @invariants
##   - RED when ANY scanned file has a constant assert, bare-pass except, or no assert mechanism
##   - Allowlist is empty by construction — no violation is tolerated
@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-08-01 · R5-negative paired with test_r1_negative_*_detected
# · Regression: U-69 — test_gate_fixture_schema.py:48 and test_llm_policy_schema.py:273 had `assert True`
# · Scenario: any test file introduces assert True / constant assert / except: pass / no-assert file
# · Last fail: N/A (new gate, B10 T1)
# · Remove if: Test Honesty R1 is superseded by a different enforcement mechanism
def test_r1_no_pass_tests() -> None:
    """Test Honesty R1: zero pass-tests across tests/ (constant asserts, bare-pass, no-assert)."""
    violations: list[str] = []
    scanned = 0
    for path in _iter_test_files(_TESTS_DIR):
        scanned += 1
        violations.extend(_scan_source_for_pass_tests(path.read_text(), str(path)))

    assert not violations, (
        f"[GATE:FAIL][id:r1_no_pass_tests] {len(violations)} R1 pass-test violation(s) "
        f"across {scanned} scanned files:\n"
        + "\n".join(violations)
        + "\nTest Honesty R1 (.kilo/rules/testing.md): a test that cannot fail is not a test. "
        "Remove the constant assert / bare-pass except / add a real assertion."
    )
    import logging

    logging.getLogger(__name__).info("[IMP:9][r1_no_pass_tests] Scanned %d test files — 0 R1 violations", scanned)


# endregion FUNC_test_r1_no_pass_tests


# region R5_NEGATIVES
## @purpose  Negative tests (Anti-Survivorship R5): each detector must fire on the exact
##           regression input. If a detector breaks, the corresponding negative fails.


# 🧪 TRAP[TEST] · 2026-08-01 · R5-negative for constant-assert detector
# · Scenario: inline fixture with `assert True` (the exact U-69 regression form)
# · Last fail: N/A (new gate)
# · Remove if: constant-assert rule removed
@pytest.mark.gate
def test_r1_negative_constant_assert_detected() -> None:
    """Negative (R5): `assert True` source must be detected as a violation."""
    src = "def test_always_passes():\n    assert True  # U-69 regression form\n"
    violations = _scan_source_for_pass_tests(src, "test_fake_constant.py")
    assert any("constant assert" in v for v in violations), f"R1 detector FAILED to flag `assert True`: {violations}"
    assert any("test_fake_constant.py" in v for v in violations)


# 🧪 TRAP[TEST] · 2026-08-01 · R5-negative for bare-pass detector
# · Scenario: inline fixture with `except Exception: pass`
# · Last fail: N/A (new gate)
# · Remove if: bare-pass rule removed
@pytest.mark.gate
def test_r1_negative_bare_pass_except_detected() -> None:
    """Negative (R5): `except X: pass` source must be detected as a violation."""
    src = "def test_swallows():\n    try:\n        helper()\n    except OSError:\n        pass\n"
    violations = _scan_source_for_pass_tests(src, "test_fake_barepass.py")
    assert any("bare `pass` in except" in v for v in violations), (
        f"R1 detector FAILED to flag bare-pass except: {violations}"
    )


# 🧪 TRAP[TEST] · 2026-08-01 · R5-negative for no-assert detector
# · Scenario: inline fixture with a test function that only logs (no fail mechanism)
# · Last fail: N/A (new gate)
# · Remove if: no-assert rule removed
@pytest.mark.gate
def test_r1_negative_no_assert_file_detected() -> None:
    """Negative (R5): test file with zero assertion mechanism must be detected."""
    src = "def test_does_nothing(caplog):\n    caplog.set_level(0)\n    logger.info('done')\n"
    violations = _scan_source_for_pass_tests(src, "test_fake_noassert.py")
    assert any("without any assertion mechanism" in v for v in violations), (
        f"R1 detector FAILED to flag assert-free test file: {violations}"
    )


# 🧪 TRAP[TEST] · 2026-08-02 · R5-negative for per-function detector (DevPlan 118 F1)
# · Scenario: file where one function asserts but a SIBLING function has no fail mechanism
# · Last fail: F1 targets (test_gate_compose_base_contract / test_gate_ci_env_vars /
#   test_gate_workflow_consistency) — file-level scan missed pass-functions behind asserting siblings
# · Remove if: per-function rule (в) removed
@pytest.mark.gate
def test_r1_negative_function_without_assert_detected() -> None:
    """Negative (R5): sibling test function without assert must be detected per-function (F1)."""
    src = (
        "def test_good():\n"
        "    assert helper() == 1\n"
        "\n"
        "def test_bad(caplog):\n"
        "    caplog.set_level(0)\n"
        "    logger.info('PASS')\n"
    )
    violations = _scan_source_for_pass_tests(src, "test_fake_perfunc.py")
    per_func = [v for v in violations if "test function 'test_bad'" in v]
    assert per_func, f"R1 per-function detector FAILED to flag assert-free sibling: {violations}"
    good_flagged = [v for v in violations if "test function 'test_good'" in v]
    assert not good_flagged, f"R1 per-function detector false-positive on asserting function: {good_flagged}"


# 🧪 TRAP[TEST] · 2026-08-02 · R5-negative: @r1_delegates exemption works (F1)
# · Scenario: test function decorated with @r1_delegates (documented delegation) is NOT flagged
# · Last fail: N/A (new gate, F1 exemption mechanism)
# · Remove if: @r1_delegates exemption removed
@pytest.mark.gate
def test_r1_negative_r1_delegates_exempt() -> None:
    """Negative (R5): @r1_delegates-decorated function must be exempt from per-function scan."""
    src = (
        "from tests._conftest.r1 import r1_delegates\n"
        "\n"
        "@r1_delegates\n"
        "def test_delegates(caplog):\n"
        "    state.precondition_check(phase)  # raises on failure\n"
    )
    violations = _scan_source_for_pass_tests(src, "test_fake_delegates.py")
    assert not violations, f"R1 detector FAILED to honor @r1_delegates exemption: {violations}"


# endregion R5_NEGATIVES
