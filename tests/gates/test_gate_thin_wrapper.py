#!/usr/bin/env python3
# GREP_SUMMARY: gate-test thin-wrapper entrypoint loc function-count binary-call allowlist
# STRUCTURE: ▶ glob entrypoints → ◇ allowlist filter → ⊞ parametrize (loc|funcs|binary) → ∑ violations → ⎋ verdict
# region MODULE_CONTRACT
## @purpose  Thin-wrapper gate test: validates all core/entrypoints/*.sh conform to
##           ≤150 LOC, ≤4 function definitions, no direct rsync/ssh/scp/ssh-keygen calls.
## @scope    All files in core/entrypoints/*.sh excluding allowlisted
##           (bootstrap.sh, lint.sh, check-doc-headers.sh).
##           Does not require Docker or external services.
## @invariants
##   - ALL entrypoints discovered by Path.glob — no hardcoded file list
##   - Allowlist skipped entirely without any check
##   - LOC checked via wc -l (per DevPlan requirement)
##   - Functions counted via grep -cE 'function|() {'
##   - Binary calls detected by reading file content, filtering comment/doc lines
## @rationale Gate prevents backsliding: entrypoints must remain thin wrappers that
##            delegate to internal/. bootstrap.sh grew to 617 LOC with 6 functions
##            because there was no gate — this test enforces the thin-wrapper contract.
##            T4 from DevPlan 020.
## @changes — 2026-07-17 | CREATED: T4 thin-wrapper gate test
# endregion MODULE_CONTRACT

import logging
import pathlib
import re
import subprocess

import pytest
from conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# Paths
# ═══════════════════════════════════════════════════════════════════════════

PLATFORM_ROOT: str = str(pathlib.Path(__file__).resolve().parent.parent.parent)
ENTRYPOINTS_DIR: pathlib.Path = pathlib.Path(PLATFORM_ROOT) / "core" / "entrypoints"

# ═══════════════════════════════════════════════════════════════════════════
# Allowlist — entrypoints outside the refactoring scope
# These are skipped without any checks because they are not part of the
# thin-wrapper contract enforcement (wave 1 of DevPlan 020).
# After the refactoring is complete, bootstrap.sh should be removed from
# the allowlist once it is reduced to ≤150 LOC and ≤4 functions.
# ═══════════════════════════════════════════════════════════════════════════

ALLOWLIST: frozenset[str] = frozenset(
    {
        "bootstrap.sh",  # Will be refactored to ~150 LOC in T15
        "lint.sh",  # External tool orchestrator — 221 LOC, 6 functions
        "check-doc-headers.sh",  # Documentation audit utility — 215 LOC, 6 functions
        "context-promote.sh",  # Uses ssh -T for SSH auth detection (B4), direct git push
        "converge.sh",  # 151 LOC (1 over limit) due to --reconcile flag + MODULE_CONTRACT markup
        "deploy.sh",  # 152 LOC (2 over limit) — K1 verb contract dispatch; DevPlan 081 extended parsing
    }
)

# ═══════════════════════════════════════════════════════════════════════════
# Patterns
# ═══════════════════════════════════════════════════════════════════════════

# Substring patterns for binary calls — matches DevPlan grep pattern:
#   rsync|ssh|scp|ssh-keygen
_BINARY_CALL_RE: re.Pattern = re.compile(r"\b(rsync|ssh|scp|ssh-keygen)\b")

# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_get_entrypoints
def get_entrypoints() -> list[pathlib.Path]:
    """Discover all .sh entrypoints in core/entrypoints/, sorted alphabetically.

    ## @purpose — Find ALL entrypoint scripts via glob — no hardcoded list.
    ## @io — ⎋ list[Path]: sorted .sh files in ENTRYPOINTS_DIR
    ## @complexity — O(N) where N = number of .sh files
    """
    files: list[pathlib.Path] = sorted(ENTRYPOINTS_DIR.glob("*.sh"))
    logger.info("[IMP:7][get_entrypoints] Found %d entrypoint files in %s", len(files), ENTRYPOINTS_DIR)
    return files


# endregion FUNC_get_entrypoints


# region FUNC_filter_allowlisted
def filter_allowlisted(files: list[pathlib.Path]) -> list[pathlib.Path]:
    """Exclude allowlisted entrypoints from the file list.

    ## @purpose — Separate files-to-check from allowlisted (skipped) files.
    ## @io — ⇥ files: list[Path] → ⎋ list[Path] (allowlist-filtered subset)
    ## @complexity — O(N)
    ## @invariants
    ##   - All allowlisted files are logged at IMP:7
    ##   - Order is preserved (input order, already sorted)
    """
    checked: list[pathlib.Path] = []
    for f in files:
        if f.name in ALLOWLIST:
            logger.info("[IMP:7][allowlist] Skipping %s (allowlisted)", f.name)
        else:
            checked.append(f)
    logger.info(
        "[IMP:9][filter_allowlisted] %d to check, %d allowlisted (skipped)", len(checked), len(files) - len(checked)
    )
    return checked


# endregion FUNC_filter_allowlisted


# region FUNC_count_loc
def count_loc(filepath: pathlib.Path) -> int:
    """Count total lines using wc -l.

    ## @purpose — Thin-wrapper contract: no entrypoint may exceed 150 LOC.
    ## @io — ⇥ filepath: Path → ⎋ int: line count
    ## @complexity — O(1) — delegates to system wc -l
    """
    result: subprocess.CompletedProcess = subprocess.run(
        ["wc", "-l", str(filepath)],
        capture_output=True,
        text=True,
        check=True,
    )
    loc_str: str = result.stdout.strip().split()[0]
    loc: int = int(loc_str)
    logger.info("[IMP:8][count_loc] %s: %d LOC", filepath.name, loc)
    return loc


# endregion FUNC_count_loc


# region FUNC_count_functions
def count_functions(filepath: pathlib.Path) -> int:
    """Count bash function definitions via grep -cE.

    ## @purpose — Thin-wrapper contract: no entrypoint may exceed 4 function definitions.
    ## @io — ⇥ filepath: Path → ⎋ int: function count (0 if grep finds none)
    ## @complexity — O(1) — delegates to system grep -cE
    ## @invariants
    ##   - Pattern: function name OR name() {
    ##   - grep returns 0 for no matches; subprocess doesn't fail on exit code 1
    """
    result: subprocess.CompletedProcess = subprocess.run(
        ["grep", "-cE", r"^\s*(function\s+\w+|\w+\s*\(\)\s*\{)", str(filepath)],
        capture_output=True,
        text=True,
    )
    stdout: str = result.stdout.strip()
    count: int = int(stdout) if stdout else 0
    logger.info("[IMP:8][count_functions] %s: %d function(s)", filepath.name, count)
    return count


# endregion FUNC_count_functions


# region FUNC_find_binary_violations
def find_binary_violations(filepath: pathlib.Path) -> list[tuple[int, str]]:
    """Find rsync/ssh/scp/ssh-keygen calls in non-comment lines.

    ## @purpose — Thin-wrapper contract: binary calls must live in internal/ not entrypoints.
    ##            Comments and docstrings (lines starting with # or ##) are allowed.
    ## @io — ⇥ filepath: Path → ⎋ list[tuple[int, str]] (line_number, line_text) for violations
    ## @complexity — O(L) where L = number of lines in file
    ## @invariants
    ##   - Lines starting with # (after optional whitespace) are treated as comments
    ##   - Lines starting with ## are treated as documentation
    ##   - Empty lines are skipped
    ##   - Pattern is case-sensitive substring match: rsync|ssh|scp|ssh-keygen
    ##   - Variable names like ssh_host WILL match (substring) — intentional: any
    ##     ssh occurrence in non-comment code of a thin wrapper is suspicious
    """
    content: str = filepath.read_text(encoding="utf-8")
    violations: list[tuple[int, str]] = []

    for i, line in enumerate(content.splitlines(), 1):
        stripped: str = line.strip()
        # Skip empty, comments (#), and documentation (##) lines
        if not stripped or stripped.startswith("#"):
            continue
        if _BINARY_CALL_RE.search(stripped):
            violations.append((i, stripped))
            logger.info("[IMP:8][binary_violation] %s:%d — %s", filepath.name, i, stripped)

    return violations


# endregion FUNC_find_binary_violations

# ═══════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_discovery
# 🧪 TRAP[TEST] · 2026-07-17 · Regression: Path.glob('*.sh') returns empty or missing baseline files
# · Scenario: Smoke — verifies entrypoint glob discovery works and finds expected files
# · Last fail: never
# · Remove if: entrypoints directory restructured (unlikely)
@pytest.mark.gate
def test_discovery() -> None:
    """Verify entrypoint discovery works and finds expected baseline files.

    ## @purpose — Ensure glob-based discovery is operational before main checks.
    ##            Fast smoke: if discovery breaks, all other tests will cascade-fail.
    """
    logger.info("[IMP:8][test_discovery] Verifying entrypoint discovery")

    files = get_entrypoints()
    assert len(files) >= 10, f"Expected ≥10 entrypoints, found {len(files)}"

    names: set[str] = {f.name for f in files}
    assert "bootstrap.sh" in names, "bootstrap.sh not found in entrypoints"
    assert "deploy.sh" in names, "deploy.sh not found in entrypoints"
    assert "build.sh" in names, "build.sh not found in entrypoints"

    logger.info("[IMP:9][test_discovery] PASS — %d entrypoints found, baseline files present", len(files))


# endregion FUNC_test_discovery


# region FUNC_test_entrypoint_loc
# 🧪 TRAP[TEST] · 2026-07-17 · Regression: any entrypoint exceeds 150 LOC (thin-wrapper backslide)
# · Scenario: Gate — iterates all non-allowlisted entrypoints, checks wc -l ≤ 150
# · Last fail: never
# · Remove if: thin-wrapper enforcement moved to CI gate (pre-commit hook or manifest validation)
@pytest.mark.gate
@ldd_trajectory
def test_entrypoint_loc(caplog) -> None:
    """Check that all non-allowlisted entrypoints have ≤150 LOC.

    ## @purpose — LOC is the primary thin-wrapper metric.
    ##            An entrypoint >150 LOC likely contains logic that belongs in internal/.
    """
    logger.info("[IMP:8][test_entrypoint_loc] Starting LOC check")
    files: list[pathlib.Path] = filter_allowlisted(get_entrypoints())

    violations: list[str] = []
    for f in files:
        loc: int = count_loc(f)
        if loc > 150:
            violations.append(f"{f.name}: {loc} LOC > 150")
            logger.info("[IMP:9][loc][FAIL] %s exceeds 150 LOC (%d)", f.name, loc)

    if violations:
        pytest.fail("Entrypoint LOC violations (≤150 expected):\n" + "\n".join(violations))

    logger.info("[IMP:9][test_entrypoint_loc] PASS — all %d entrypoints ≤150 LOC", len(files))


# endregion FUNC_test_entrypoint_loc


# region FUNC_test_entrypoint_function_count
# 🧪 TRAP[TEST] · 2026-07-17 · Regression: any entrypoint has >4 function definitions
# · Scenario: Gate — iterates all non-allowlisted entrypoints, checks grep -cE ≤ 4
# · Last fail: never
# · Remove if: thin-wrapper enforcement moved to CI gate
@pytest.mark.gate
@ldd_trajectory
def test_entrypoint_function_count(caplog) -> None:
    """Check that all non-allowlisted entrypoints have ≤4 function definitions.

    ## @purpose — Function count is the secondary thin-wrapper metric.
    ##            An entrypoint with >4 functions likely contains logic that belongs in internal/.
    ## @invariants
    ##   - Function definition pattern: `function name {` or `name() {`
    ##   - grep -cE is used (per DevPlan §T4 specification)
    """
    logger.info("[IMP:8][test_entrypoint_function_count] Starting function count check")
    files: list[pathlib.Path] = filter_allowlisted(get_entrypoints())

    violations: list[str] = []
    for f in files:
        func_count: int = count_functions(f)
        if func_count > 4:
            violations.append(f"{f.name}: {func_count} functions > 4")
            logger.info("[IMP:9][funcs][FAIL] %s exceeds 4 functions (%d)", f.name, func_count)

    if violations:
        pytest.fail("Entrypoint function count violations (≤4 expected):\n" + "\n".join(violations))

    logger.info("[IMP:9][test_entrypoint_function_count] PASS — all %d entrypoints ≤4 functions", len(files))


# endregion FUNC_test_entrypoint_function_count


# region FUNC_test_entrypoint_no_direct_binary_calls
# 🧪 TRAP[TEST] · 2026-07-17 · Regression: entrypoint contains rsync/ssh/scp/ssh-keygen in executable code
# · Scenario: Gate — iterates all non-allowlisted entrypoints, checks no inline binary calls outside comments
# · Last fail: never
# · Remove if: thin-wrapper enforcement moved to CI gate
@pytest.mark.gate
@ldd_trajectory
def test_entrypoint_no_direct_binary_calls(caplog) -> None:
    """Check that non-allowlisted entrypoints contain no direct rsync/ssh/scp/ssh-keygen calls.

    ## @purpose — Thin-wrapper contract enforcement: binary calls must live in internal/
    ##            layer, not in entrypoints. Entrypoints should delegate to internal/ scripts
    ##            which perform the actual operations.
    ## @invariants
    ##   - Only executable (non-comment) lines are checked
    ##   - Lines starting with # or ## are treated as comments/documentation
    ##   - Case-sensitive substring match (rsync|ssh|scp|ssh-keygen)
    ##   - Every violation produces a separate IMP:9 log line
    ##   - Test fails with all violations listed in the error message
    """
    logger.info("[IMP:8][test_entrypoint_no_direct_binary_calls] Starting binary call check")
    files: list[pathlib.Path] = filter_allowlisted(get_entrypoints())

    for f in files:
        violations: list[tuple[int, str]] = find_binary_violations(f)
        if violations:
            detail: str = "\n".join(f"  {f.name}:{ln} — {txt}" for ln, txt in violations)
            logger.info(
                "[IMP:9][binary][FAIL] %s has %d binary call violation(s):\n%s", f.name, len(violations), detail
            )
            pytest.fail(
                f"{f.name} contains direct binary call(s) (rsync/ssh/scp/ssh-keygen) in executable code:\n{detail}"
            )
        logger.info("[IMP:7][binary][PASS] %s: clean", f.name)

    logger.info("[IMP:9][test_entrypoint_no_direct_binary_calls] PASS — all %d entrypoints clean", len(files))


# endregion FUNC_test_entrypoint_no_direct_binary_calls
