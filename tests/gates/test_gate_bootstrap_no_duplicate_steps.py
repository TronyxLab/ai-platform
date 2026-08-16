"""
# GREP_SUMMARY: gate-bootstrap-no-duplicate-steps, static-audit, DevPlan-087-T10, bootstrap-consolidation, no-step-deploy-context, no-shell-to-python-step, no-step-underscore, no-done-files, node-lifecycle-clean
# STRUCTURE: ▶ resolve CORE_DIR + key file paths → ◇ read source files → ○ grep/regex analysis for AC invariants → ⊕ 4 gate test functions → ┌print LDD trajectory┐ → ⎋ IMP:9 assertions
# region MODULE_CONTRACT
## @purpose  Gate test (DevPlan 087 T10) verifying bootstrap consolidation invariants:
##           1. No duplicate _step_* function definitions across state_machine.py and phases.py
##           2. No SHELL_TO_PYTHON_STEP references in core/ (checkpoint_migration.py deleted)
##           3. No .done-file references in core/internal/bootstrap/ (all checkpoints via state.json)
##           4. No step_1_*, step_18_*, checkpoint_step, checkpoint_migrate_in node-lifecycle.sh
##           5. node-lifecycle.sh <80 LOC (thin facade per DevPlan 087 T7)
##           Перенесён из tests/unit/test_bootstrap_no_duplicate_steps.py (DevPlan 119 A1 —
##           зомби-гейт вне tests/gates/; self-described "Gate test").
## @scope    Static analysis — reads source files from disk, applies grep-based assertions.
##           No Docker, VPS, or network access required. All tests are gate-compatible.
## @invariants
##   - CORE_DIR resolves to project root's core/ directory (same parent as tests/)
##   - __pycache__ directories are excluded from SHELL_TO_PYTHON_STEP grep
##   - Comment-only references to deleted patterns (.done.log, .done.pid, #, //) are ignored
##   - All tests fail on first violation — no silent pass for partial matches
## @rationale DevPlan 087 consolidates 32+ bootstrap steps → 14 phases. These gate tests
##            prevent regression: duplicate step implementations (AC2/AC4 — steps.py removed
##            in DevPlan 116 B8 U-27, тесты-консерваторы удалены), stale shell-to-python
##            bridge (AC3), shell .done-file checkpoints (AC5), and shell index-addressed step
##            functions (AC9) must not reappear after refactoring.
## @changes  2026-07-30 · Created (DevPlan 087 T10 gate test)
##           2026-08-01 · DevPlan 116 B8 T1 (U-27) — steps.py deleted; STEPS_PY/_PATHS entry and
##           2 теста-консерватора (AC2 test_no_step_deploy_context_in_steps, AC4
##           test_no_step_underscore_functions_in_steps) удалены — TRAP[TEST] разрешал
##           («Remove if: steps.py is fully replaced by phases.py and deleted»).
##           2026-08-02 · DevPlan 119 A1 — перенесён из tests/unit/test_bootstrap_no_duplicate_steps.py
# endregion MODULE_CONTRACT
"""

import logging
import pathlib
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

# ── Path resolution ──
CORE_DIR = Path(__file__).resolve().parent.parent.parent / "core"

STATE_MACHINE_PY = CORE_DIR / "internal" / "bootstrap" / "lifecycle" / "state_machine.py"
NODE_LIFECYCLE_SH = CORE_DIR / "internal" / "bootstrap" / "node-lifecycle.sh"
BOOTSTRAP_DIR = CORE_DIR / "internal" / "bootstrap"

# Native file paths must exist at test time
_PATHS = {
    "state_machine.py": STATE_MACHINE_PY,
    "node-lifecycle.sh": NODE_LIFECYCLE_SH,
    "bootstrap dir": BOOTSTRAP_DIR,
}


# region HELPER__assert_grep_target_files
def _assert_grep_target_files() -> None:
    """Assert all target source files exist on disk before running tests.

    ## @purpose — Fail-fast precondition check: validates all paths are present.
    ## @io — ⎋ None, raises AssertionError if any target file is missing
    ## @complexity 1 — file existence checks
    """
    for name, path in _PATHS.items():
        assert path.exists(), f"[IMP:10][preflight] Target file not found: {name} at {path}"


# endregion HELPER__assert_grep_target_files


# region HELPER__assert_ldd
def _assert_ldd_imp9(caplog: pytest.LogCaptureFixture) -> None:
    """Assert LDD IMP:9+ logs present in caplog trajectory.

    ## @purpose — LDD telemetry: verify that at least one IMP:9 business-logic log
    ##            was emitted during the test. Prevents silent pass when no actual
    ##            grep analysis was performed.
    ## @io — caplog fixture → raises AssertionError if no IMP:9 log found
    ## @complexity 1 — linear scan of captured log records
    """
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion HELPER__assert_ldd


# region HELPER__grep_core_excluding_pycache
# Transient probe-артефакты параллельных xdist-тестов в core/ — НЕ продукт, исключаются
# из скана (тот же класс защиты, что _EXCLUDED_DIRS в test_gate_grep_summary.py и
# _EXCLUDE_DIRS в test_gate_no_unregistered_entrypoint.py; DevPlan 119 C / 124 / 129 W2):
#   _gate_probe_marker_tmp       — test_gate_marker_location R5 probe
#   _b11_negative_py_tmp/_sh_tmp — test_cross_layer_imports B11-negative probes
#   _gate_probe_subprocess_io.py — test_gate_subprocess_io_sole R5 probe (core/ root)
_PROBE_SEGMENTS: frozenset = frozenset({"_gate_probe_marker_tmp", "_b11_negative_py_tmp", "_b11_negative_sh_tmp"})
_PROBE_FILENAMES: frozenset = frozenset({"_gate_probe_subprocess_io.py"})
_EXTENSIONS: tuple[str, ...] = (".py", ".sh", ".yaml", ".yml", ".json", ".toml", ".cfg", ".md")


def _grep_core_excluding_pycache(pattern: str, include_extensions: tuple[str, ...]) -> list[str]:
    """Recursive grep for `pattern` in core/ excluding __pycache__ directories.

    ## @purpose — Search source files for a forbidden pattern, silently skipping
    ##            compiled Python bytecode in __pycache__.
    ## @io — pattern(str) + include_extensions(tuple) → ⎋ list[str] of matching file paths
    ## @complexity — O(N * L) where N = files, L = avg lines per file
    ## @invariants
    ##   - __pycache__ directories excluded
    ##   - Probe-артефакты параллельных xdist-тестов (см. _PROBE_SEGMENTS/_PROBE_FILENAMES)
    ##     исключаются — транзиентные файлы чужих R5-тестов не должны влиять на гейт
    """
    matches: list[str] = []
    for fpath in CORE_DIR.rglob("*"):
        if fpath.suffix not in include_extensions:
            continue
        if "__pycache__" in fpath.parts:
            continue
        if any(part in _PROBE_SEGMENTS for part in fpath.parts):
            continue
        if fpath.name in _PROBE_FILENAMES:
            continue
        if not fpath.is_file():
            continue
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
            if pattern in text:
                matches.append(str(fpath))
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("[IMP:6][grep_core] Skipping %s: %s", fpath, exc)
    return matches


# endregion HELPER__grep_core_excluding_pycache


# ══════════════════════════════════════════════════════════════════════════════
# AC3: No SHELL_TO_PYTHON_STEP references in core/
# ══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_no_shell_to_python_step_references
# 🧪 TRAP[TEST] · DevPlan AC3 · SHELL_TO_PYTHON_STEP mapping deleted
# · Regression: If SHELL_TO_PYTHON_STEP reappears, checkpoint_migration.py bridge
#   has been resurrected → dual state machine returns
# · Scenario: Recursive grep for SHELL_TO_PYTHON_STEP in core/ files
# · Last fail: N/A (first test)
# · Remove if: core/internal/ implements 14-phase enum-only design
@pytest.mark.gate
@pytest.mark.static_audit
def test_no_shell_to_python_step_references(caplog: pytest.LogCaptureFixture) -> None:
    """Verify core/ contains NO SHELL_TO_PYTHON_STEP references.

    ## @purpose — DevPlan 087 AC3: SHELL_TO_PYTHON_STEP mapping deleted with
    ##            checkpoint_migration.py (T6). All checkpoints via state.json directly.
    ##            False positives: __pycache__ bytecode is excluded.
    ## @io — recursive grep core/ → ⎋ None, raises AssertionError if pattern found
    ## @complexity — O(N) file scan over core/ source tree
    """
    caplog.set_level(logging.INFO)

    matches = _grep_core_excluding_pycache(
        "SHELL_TO_PYTHON_STEP",
        include_extensions=_EXTENSIONS,
    )

    logger.info("[IMP:9][test_no_shell_to_python_step_references] Grep SHELL_TO_PYTHON_STEP in core/")

    if matches:
        logger.error(
            "[IMP:10][test_no_shell_to_python_step_references] FAIL: %d file(s) contain SHELL_TO_PYTHON_STEP",
            len(matches),
        )
        for m in matches:
            logger.error("[IMP:10][test_no_shell_to_python_step_references]   %s", m)
        pytest.fail(f"SHELL_TO_PYTHON_STEP found in {len(matches)} file(s): {matches}")

    logger.info("[IMP:9][test_no_shell_to_python_step_references] PASS: No SHELL_TO_PYTHON_STEP in core/")
    _assert_ldd_imp9(caplog)


# endregion FUNC_test_no_shell_to_python_step_references


# ══════════════════════════════════════════════════════════════════════════════
# AC5: No .done file references in core/internal/bootstrap/
# ══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_no_done_file_references_in_bootstrap
# 🧪 TRAP[TEST] · REGRESSION(087) · .done checkpoint references forbidden
# · Regression: Shell .done files reintroduce dual checkpoint mechanism (state.json + .done)
# · Scenario: grep for ".done" in core/internal/bootstrap/ excluding false positives
# · Last fail: N/A (first test)
# · Remove if: bootstrap fully standardised on state.json-only checkpoints
def _scan_done_references(fpath: pathlib.Path, excluded: tuple[str, ...]) -> list[str]:
    """Найти .done-ссылки в одном файле (PLW0717-хелпер, исключая false positives).

    ## @purpose — Извлечение тела try: построчный скан файла на ".done" с
    ##             исключениями .done.log/.done.pid и комментариев.
    ## @io — ⇥ fpath, excluded → ⎋ list[str] нарушающих строк ("<fpath>: <line>")
    ## @complexity — O(L) где L = строки файла
    """
    violating_lines: list[str] = []
    try:
        lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("[IMP:6][test_no_done] Skipping %s: %s", fpath, exc)
        return violating_lines
    violating_lines.extend(f"{fpath}: {line.strip()}" for line in lines if _is_done_reference(line, excluded))
    return violating_lines


def _is_done_reference(line: str, excluded: tuple[str, ...]) -> bool:
    """True если строка — .done-ссылка (не false positive и не комментарий).

    ## @purpose — PLW0717-хелпер: фильтрация строки на .done с исключениями.
    ## @io — ⇥ line, excluded → ⎋ bool
    ## @complexity — O(1)
    """
    if ".done" not in line:
        return False
    # Skip false positives
    stripped = line.strip()
    if any(excl in stripped for excl in excluded):
        return False
    return not stripped.startswith(("#", "//"))


@pytest.mark.gate
@pytest.mark.static_audit
def test_no_done_file_references_in_bootstrap(caplog: pytest.LogCaptureFixture) -> None:
    """Verify core/internal/bootstrap/ has NO .done file checkpoint references.

    ## @purpose — DevPlan 087 AC5: All checkpoints must use state.json directly.
    ##            Shell .done-files (touch .done, if [[ -f .done ]], etc.) are forbidden.
    ##            False positives excluded: .done.log, .done.pid, comment lines (#, //).
    ## @io — recursive grep BOOTSTRAP_DIR → ⎋ None, raises AssertionError if .done refs found
    ## @complexity — O(N) file scan over bootstrap directory
    """
    caplog.set_level(logging.INFO)

    assert BOOTSTRAP_DIR.is_dir(), f"[IMP:10][preflight] Bootstrap dir not found: {BOOTSTRAP_DIR}"

    # Collect all .done references excluding common false positives
    EXCLUDED_PATTERNS = (".done.log", ".done.pid")
    violating_lines: list[str] = []

    for fpath in sorted(BOOTSTRAP_DIR.rglob("*")):
        if not fpath.is_file():
            continue
        if fpath.suffix in {
            ".pyc",
        }:
            continue
        violating_lines.extend(_scan_done_references(fpath, EXCLUDED_PATTERNS))

    logger.info(
        "[IMP:9][test_no_done_file_references_in_bootstrap] Check .done references in %s",
        BOOTSTRAP_DIR,
    )

    if violating_lines:
        logger.error(
            "[IMP:10][test_no_done_file_references_in_bootstrap] FAIL: %d .done reference(s) found",
            len(violating_lines),
        )
        for vl in violating_lines:
            logger.error("[IMP:10][test_no_done_file_references_in_bootstrap]   %s", vl)
        pytest.fail(f"Found {len(violating_lines)} .done reference(s) in bootstrap/:\n" + "\n".join(violating_lines))

    logger.info("[IMP:9][test_no_done_file_references_in_bootstrap] PASS: No .done checkpoint files")
    _assert_ldd_imp9(caplog)


# endregion FUNC_test_no_done_file_references_in_bootstrap


# ══════════════════════════════════════════════════════════════════════════════
# AC9: No step_1_*, step_18_*, checkpoint_step in node-lifecycle.sh
# ══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_no_step_1_step_18_in_node_lifecycle
# 🧪 TRAP[TEST] · DevPlan AC9 · No index-addressed step functions in node-lifecycle.sh
# · Regression: step_1_*, step_18_* functions or checkpoint_step calls reintroduce
#   the old shell index-addressed architecture
# · Scenario: grep for step_1_, step_18_, checkpoint_step, checkpoint_migrate_,
#   checkpoint_reset_all in node-lifecycle.sh
# · Last fail: N/A (first test)
# · Remove if: node-lifecycle.sh is entirely replaced by Python phase dispatcher
@pytest.mark.gate
@pytest.mark.static_audit
def test_no_step_1_step_18_in_node_lifecycle(caplog: pytest.LogCaptureFixture) -> None:
    """Verify node-lifecycle.sh has NO step_1_*, step_18_*, or checkpoint_step patterns.

    ## @purpose — DevPlan 087 AC9: Shell facade must use BootstrapPhase enum directly,
    ##            not index-addressed functions (step_1_ssh_access, step_18_deploy_context)
    ##            or checkpoint_step calls. checkpoint_migrate_legacy() and
    ##            checkpoint_reset_all() are also forbidden.
    ## @io — reads NODE_LIFECYCLE_SH → ⎋ None, raises AssertionError if pattern found
    ## @complexity 1 — single regex scan
    """
    caplog.set_level(logging.INFO)
    _assert_grep_target_files()

    content = NODE_LIFECYCLE_SH.read_text(encoding="utf-8")

    forbidden_patterns = [
        "step_1_",
        "step_18_",
        "checkpoint_step",
        "checkpoint_migrate_legacy",
        "checkpoint_reset_all",
    ]

    violations: list[str] = [
        f"  line {line_num}: {line.strip()}  (pattern: {pat})"
        for line_num, line in enumerate(content.splitlines(), start=1)
        for pat in forbidden_patterns
        if pat in line and not line.strip().startswith("#")
    ]

    logger.info(
        "[IMP:9][test_no_step_1_step_18_in_node_lifecycle] Check node-lifecycle.sh for index-addressed step patterns"
    )

    if violations:
        logger.error(
            "[IMP:10][test_no_step_1_step_18_in_node_lifecycle] FAIL: %d violation(s) found",
            len(violations),
        )
        for v in violations:
            logger.error("[IMP:10]   %s", v)
        pytest.fail(
            f"node-lifecycle.sh contains {len(violations)} index-addressed step pattern(s):\n" + "\n".join(violations)
        )

    logger.info("[IMP:9][test_no_step_1_step_18_in_node_lifecycle] PASS: No index-addressed step patterns")
    _assert_ldd_imp9(caplog)


# endregion FUNC_test_no_step_1_step_18_in_node_lifecycle


# ══════════════════════════════════════════════════════════════════════════════
# AC9 (file size): node-lifecycle.sh <80 LOC
# ══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_node_lifecycle_under_80_loc
# 🧪 TRAP[TEST] · DevPlan AC9 · node-lifecycle.sh must be thin facade <80 LOC
# · Regression: Adding step logic back to node-lifecycle.sh defeats the Strangler-Fig
# · Scenario: Count non-empty lines in node-lifecycle.sh, assert < 80
# · Last fail: N/A (first test)
# · Remove if: node-lifecycle.sh is entirely replaced by Python (pure Python bootstrap)
@pytest.mark.gate
@pytest.mark.static_audit
def test_node_lifecycle_under_80_loc(caplog: pytest.LogCaptureFixture) -> None:
    """Verify node-lifecycle.sh is a thin facade under 80 lines.

    ## @purpose — DevPlan 087 T7 target: node-lifecycle.sh must be <80 LOC after
    ##            full refactoring. All step logic lives in Python lifecycle/ modules.
    ##            This test counts total lines in the file (including shebang, comments).
    ## @io — reads NODE_LIFECYCLE_SH → ⎋ None, raises AssertionError if >= 80 lines
    ## @complexity 1 — line count
    """
    caplog.set_level(logging.INFO)
    _assert_grep_target_files()

    content = NODE_LIFECYCLE_SH.read_text(encoding="utf-8")
    total_lines = len(content.splitlines())

    logger.info(
        "[IMP:9][test_node_lifecycle_under_80_loc] node-lifecycle.sh: %d lines (limit: 80)",
        total_lines,
    )

    if total_lines >= 80:
        logger.error(
            "[IMP:10][test_node_lifecycle_under_80_loc] FAIL: %d lines exceeds 80 LOC limit",
            total_lines,
        )
        pytest.fail(f"node-lifecycle.sh has {total_lines} lines, expected < 80 (thin facade)")

    logger.info(
        "[IMP:9][test_node_lifecycle_under_80_loc] PASS: node-lifecycle.sh is %d lines (<80)",
        total_lines,
    )
    _assert_ldd_imp9(caplog)


# endregion FUNC_test_node_lifecycle_under_80_loc
