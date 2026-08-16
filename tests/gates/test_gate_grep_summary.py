#!/usr/bin/env python3

# GREP_SUMMARY: gate grep-summary GREP_SUMMARY presence anti-drift static
# STRUCTURE: ▶ [core/**/*.sh + core/**/*.py + tests/**/*.py] → ∋ exclude() → ○ first_10_lines → ◇ GREP_SUMMARY? → ⊕ missing_set → ◇ missing? → ⎋ fail:MISSING_GREP_SUMMARY | pass

# region MODULE_CONTRACT
## @purpose  Anti-drift gate: verify every shell (.sh) and Python (.py) source file
##           has a GREP_SUMMARY line in the first 10 lines. GREP_SUMMARY is the
##           mandatory keyword header for agent-based file discovery (§MARKUP).
##           Without it, the next agent opening the file has no quick overview.
##           This gate prevents drift where new or modified files omit the header.
## @scope    Static file scanning — no runtime dependencies, no Docker.
##           Checks core/ (shell + Python) and tests/ (Python) directories.
## @invariants
##   - Both test functions use @pytest.mark.gate and @ldd_trajectory
##   - test_all_sh_files_have_grep_summary:  glob core/**/*.sh
##   - test_all_py_files_have_grep_summary:  glob core/**/*.py + tests/**/*.py
##   - Only first 10 lines per file are examined
##   - Excluded: __pycache__/, .venv/, node_modules/, test_data/, .pytest_cache/, .test_counter.json
##   - Fail message: MISSING_GREP_SUMMARY: <path\> (all missing in single message)
##   - Does NOT add GREP_SUMMARY — only verifies presence
## @rationale  GREP_SUMMARY is part of the Semantic Markup Standard (§MARKUP).
##             Every source file MUST have it for agent-based discovery. This gate
##             is the anti-drift mechanism that catches new or modified files
##             without the mandatory header. Without this gate, files silently
##             drift out of compliance and the next agent misses context.
## @changes  CREATED: 2026-07-09 | TASK-5G5a GREP_SUMMARY presence gate
def _module_contract():
    pass


# endregion MODULE_CONTRACT


import logging
import pathlib

import pytest

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)


# ── Exclusions ─────────────────────────────────────────────────────────────────

# region EXCLUSIONS

# Directory names to exclude from GREP_SUMMARY check (matched against any path component)
_EXCLUDED_DIRS: frozenset = frozenset({
    "__pycache__",
    ".venv",
    "node_modules",
    "test_data",
    ".pytest_cache",
    # Transient probe-директория test_gate_marker_location (xdist race, DevPlan 119 C):
    # probe-файл без GREP_SUMMARY живёт лишь на время соседнего gate-теста
    "_gate_probe_marker_tmp",
    # Transient probe-директории test_cross_layer_imports B11-negative (xdist race,
    # DevPlan 124, решение пользователя 2026-08-03): probe-файлы БЕЗ GREP_SUMMARY живут
    # в реальном core/modules/ на время R5 negative-тестов — сканер видел их и давал
    # ложный MISSING_GREP_SUMMARY (флейк static_audit ~15-30%, junit подтвердил)
    "_b11_negative_py_tmp",
    "_b11_negative_sh_tmp",
})

# Individual file names to exclude
_EXCLUDED_FILES: frozenset = frozenset({
    ".test_counter.json",
    # Transient probe test_gate_subprocess_io_sole (xdist race, DevPlan 129 W2):
    # core/_gate_probe_subprocess_io*.py — R5 negative пишет probe БЕЗ GREP_SUMMARY
    # в реальный core/ (сканер single-canon требует реального пути), параллельный
    # test_all_py_files_have_grep_summary ловил его → ложный MISSING_GREP_SUMMARY
    # (~15-30% под xdist). Прецедент: _gate_probe_marker_tmp (119 C), _b11_negative_* (124).
    # 2026-08-12: имя probe стало UUID-суффиксным (_gate_probe_subprocess_io_<hex8>.py,
    # фикс xdist-гонки negative-vs-negative) — точное имя заменено префиксным матчем ниже.
    "_gate_probe_subprocess_io.py",
})

# Probe-префикс имён файлов (канон DevPlan 119 H): ЛЮБОЙ _gate_probe_* файл — transient
# артефакт R5-negative теста без GREP_SUMMARY (uuid-суффиксы исключают точное сопоставление).
_PROBE_FILE_PREFIX = "_gate_probe_"
# endregion EXCLUSIONS


# ── Helpers ─────────────────────────────────────────────────────────────────────

# region HELPERS


def _is_excluded(file_path: pathlib.Path) -> bool:
    """Check if a file path matches any exclusion pattern.

    ## @purpose — Filter out generated, third-party, and cache paths that
    ##            are not expected to have GREP_SUMMARY.
    ## @io — ⇥ file_path: Path → ⎋ bool (True if path should be excluded)
    ## @complexity — O(N) where N = number of path components
    ## @invariants
    ##   - Matches any single path segment against _EXCLUDED_DIRS
    ##   - Matches exact filename against _EXCLUDED_FILES
    ##   - Returns False for non-excluded paths
    """
    for part in file_path.parts:
        if part in _EXCLUDED_DIRS:
            logger.debug("[IMP:3][_is_excluded] Dir '%s' in path %s → excluded", part, file_path)
            return True
    if file_path.name in _EXCLUDED_FILES:
        logger.debug("[IMP:3][_is_excluded] File '%s' → excluded", file_path.name)
        return True
    if file_path.name.startswith(_PROBE_FILE_PREFIX):
        logger.debug("[IMP:3][_is_excluded] Probe-префикс '%s' в %s → excluded", _PROBE_FILE_PREFIX, file_path)
        return True
    return False


def _find_missing_grep_summary(files: list[pathlib.Path]) -> list[str]:
    """Scan files for GREP_SUMMARY in the first 10 lines.

    ## @purpose — Core scan: for each file, read first 10 lines and check
    ##            whether any line contains 'GREP_SUMMARY'. Returns sorted
    ##            list of absolute paths missing the header. Excluded files
    ##            are silently skipped.
    ## @io — ⇥ files: list[Path] → ⎋ list[str] (sorted absolute paths)
    ## @complexity — O(N * 10) where N = file count (only first 10 lines checked)
    ## @invariants
    ##   - Excluded dirs and files are skipped silently (IMP:3 log)
    ##   - Unreadable/empty files produce IMP:4 warning and are skipped
    ##   - Returns empty list when all scanned files pass
    ##   - Paths are resolved to absolute form for deterministic output
    """
    missing: list[str] = []
    for fp in sorted(files):
        if _is_excluded(fp):
            continue
        try:
            with pathlib.Path(fp).open(encoding="utf-8") as fh:
                # Read up to 10 lines safely — zip stops at shortest iterable
                first_lines = [line for _, line in zip(range(10), fh, strict=False)]
        except OSError:
            # No read permission or other I/O error — skip defensively
            logger.warning("[IMP:4][_find_missing_grep_summary] Skipping unreadable: %s", fp)
            continue
        has_grep = any("GREP_SUMMARY" in line for line in first_lines)
        if not has_grep:
            resolved = str(fp.resolve())
            missing.append(resolved)
            logger.info("[IMP:8][_find_missing_grep_summary] MISSING_GREP_SUMMARY: %s", resolved)
    return sorted(missing)


# endregion HELPERS


# ── Project root ───────────────────────────────────────────────────────────────
# Used as base directory for recursive globs. Resolved from test file location
# (tests/gates/test_gate_grep_summary.py → ../../ = project root).

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


# ── Tests ──────────────────────────────────────────────────────────────────────

# region TESTS


# region FUNC_test_all_sh_files_have_grep_summary
## @purpose — Verify every .sh file under core/ has GREP_SUMMARY in first 10 lines.
## @io — ⇥ caplog: LogCaptureFixture → ⎋ None (pytest.fail if files are missing)
## @complexity — O(N * 10) where N = number of shell files
## @invariants
##   - Globs core/**/*.sh recursively from repo root
##   - Exclusion rules apply (__pycache__/, .venv/, etc.)
##   - All missing paths reported in single pytest.fail message
##   - Format: MISSING_GREP_SUMMARY: <absolute_path\>


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate invariant — first line of defense against drift in platform contracts
# · Last fail: N/A (preventive)
# · Remove if: entire gate category is superseded by a newer mechanism
def test_all_sh_files_have_grep_summary(caplog: pytest.LogCaptureFixture) -> None:
    """
    # ◇ core/**/*.sh → ∋ _is_excluded → ○ first_10_lines → ◇ contains "GREP_SUMMARY"?
    # → ⊕ missing_paths → ◇ empty? → ⎋ pass | fail:MISSING_GREP_SUMMARY:...
    """
    # region BLOCK_CollectShellFiles

    shell_files: list[pathlib.Path] = list((_REPO_ROOT / "core").rglob("*.sh"))
    logger.info(
        "[IMP:7][test_all_sh_files_have_grep_summary] Found %d .sh file(s) in core/",
        len(shell_files),
    )
    # endregion BLOCK_CollectShellFiles

    # region BLOCK_Scan
    missing = _find_missing_grep_summary(shell_files)
    # endregion BLOCK_Scan

    # region BLOCK_Assert
    if missing:
        logger.error(
            "[IMP:9][test_all_sh_files_have_grep_summary] %d shell file(s) missing GREP_SUMMARY",
            len(missing),
        )
        pytest.fail("\n".join(f"MISSING_GREP_SUMMARY: {p}" for p in missing))

    logger.info(
        "[IMP:9][test_all_sh_files_have_grep_summary] ✅ All %d shell file(s) have GREP_SUMMARY",
        len(shell_files),
    )
    # endregion BLOCK_Assert


# endregion FUNC_test_all_sh_files_have_grep_summary


# region FUNC_test_all_py_files_have_grep_summary
## @purpose — Verify every .py file under core/ and tests/ has GREP_SUMMARY
##            in the first 10 lines.
## @io — ⇥ caplog: LogCaptureFixture → ⎋ None (pytest.fail if files are missing)
## @complexity — O(N * 10) where N = number of Python files
## @invariants
##   - Globs core/**/*.py + tests/**/*.py recursively from repo root
##   - Exclusion rules apply (__pycache__/, .venv/, etc.)
##   - All missing paths reported in single pytest.fail message
##   - Format: MISSING_GREP_SUMMARY: <absolute_path\>


@pytest.mark.gate
@ldd_trajectory
def test_all_py_files_have_grep_summary(caplog: pytest.LogCaptureFixture) -> None:
    """
    # ◇ [core/**/*.py + tests/**/*.py] → ∋ _is_excluded → ○ first_10_lines
    # → ◇ contains "GREP_SUMMARY"? → ⊕ missing_paths → ◇ empty? → ⎋ pass | fail
    """
    # region BLOCK_CollectPythonFiles

    py_files: list[pathlib.Path] = list((_REPO_ROOT / "core").rglob("*.py")) + list(
        (_REPO_ROOT / "tests").rglob("*.py")
    )
    logger.info(
        "[IMP:7][test_all_py_files_have_grep_summary] Found %d .py file(s) in core/ and tests/",
        len(py_files),
    )
    # endregion BLOCK_CollectPythonFiles

    # region BLOCK_Scan
    missing = _find_missing_grep_summary(py_files)
    # endregion BLOCK_Scan

    # region BLOCK_Assert
    if missing:
        logger.error(
            "[IMP:9][test_all_py_files_have_grep_summary] %d Python file(s) missing GREP_SUMMARY",
            len(missing),
        )
        pytest.fail("\n".join(f"MISSING_GREP_SUMMARY: {p}" for p in missing))

    logger.info(
        "[IMP:9][test_all_py_files_have_grep_summary] ✅ All %d Python file(s) have GREP_SUMMARY",
        len(py_files),
    )
    # endregion BLOCK_Assert


# endregion FUNC_test_all_py_files_have_grep_summary

# endregion TESTS
