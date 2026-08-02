#!/usr/bin/env python3
# GREP_SUMMARY: gate-test thin-wrapper entrypoint loc function-count binary-call allowlist
# STRUCTURE: ▶ glob entrypoints → ◇ allowlist filter → ⊞ parametrize (loc|funcs|binary) → ∑ violations → ⎋ verdict
# region MODULE_CONTRACT
## @purpose  Thin-wrapper gate test: validates all core/entrypoints/*.sh conform to
##           ≤150 LOC, ≤4 function definitions, no direct rsync/ssh/scp/ssh-keygen calls.
## @scope    All files in core/entrypoints/*.sh excluding allowlisted
##           (bootstrap.sh, deploy.sh).
##           Does not require Docker or external services.
## @invariants
##   - ALL entrypoints discovered by Path.glob — no hardcoded file list
##   - Allowlist skipped entirely without any check
##   - Allowlist contains ONLY scripts >150 LOC OR with direct binary calls
##     (justified exceptions — validated by test_allowlist_current, DevPlan 119 G1)
##   - LOC checked via wc -l (per DevPlan requirement)
##   - Functions counted via grep -cE 'function|() {'
##   - Binary calls detected by reading file content, filtering comment/doc lines
## @rationale Gate prevents backsliding: entrypoints must remain thin wrappers that
##            delegate to internal/. bootstrap.sh grew to 617 LOC with 6 functions
##            because there was no gate — this test enforces the thin-wrapper contract.
##            T4 from DevPlan 020.
## @changes — 2026-07-17 | CREATED: T4 thin-wrapper gate test
## @changes — 2026-08-02 | DevPlan 119 G1: allowlist актуализирован — lint.sh (40 LOC),
##            check-doc-headers.sh (17), converge.sh (100), context-promote.sh (32)
##            удалены (все <150 LOC, 0 binary calls); bootstrap.sh/deploy.sh остаются
##            (>150 LOC); + test_allowlist_current (R5 negative на возврат удалённых)
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
# Allowlist — entrypoints outside the thin-wrapper contract enforcement
# These are skipped without any checks because they are NOT thin wrappers:
# either they exceed the 150 LOC limit, or they legitimately perform direct
# binary calls (exec ssh / scp delegation) per the language policy.
# Актуализировано DevPlan 119 G1: lint.sh, check-doc-headers.sh, converge.sh,
# context-promote.sh удалены — все <150 LOC, 0 прямых бинарных вызовов, прошли
# бы все проверки gate'а (AUDIT-6 F2: allowlist устарел после 117/118).
# ═══════════════════════════════════════════════════════════════════════════

ALLOWLIST: frozenset[str] = frozenset(
    {
        # bootstrap.sh (160 LOC, 1 func): T15-рефакторинг DevPlan 020 выполнен — остаётся
        # в allowlist из-за exec ssh (L157) + SCP-делегирования (source scp-deliver.sh /
        # build-ssh-cmd.sh → прямые rsync/ssh-вызовы, языковая политика: entrypoint =
        # тонкий фасад над scp-deliver/build-ssh-cmd). 160 LOC > 150 — превышает лимит.
        "bootstrap.sh",
        "deploy.sh",  # 175 LOC (25 over limit) — K1 verb contract dispatch; DevPlan 081
        # extended parsing; D7: переходный SSH forced-command entrypoint — канонический
        # канал уже orchestrator_cli dispatch; удаление ломает legacy-ноды (authorized_keys)
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


# region FUNC_test_allowlist_current
# 🧪 TRAP[TEST] · NEGATIVE (R5) · DevPlan 119 G1 (AUDIT-6 F2) — thin_wrapper allowlist актуальность
# · Last fail: lint.sh (221 LOC), check-doc-headers.sh (215), converge.sh (151), context-promote.sh (161)
# ·            были в allowlist при размерах <150 LOC после 117/118 — скрипты прятались от gate'а
# · Remove if: thin-wrapper enforcement moved to CI gate (allowlist removed entirely)
@pytest.mark.gate
def test_allowlist_current() -> None:
    """R5 negative: allowlist содержит ТОЛЬКО скрипты >150 LOC или с прямыми бинарными вызовами.

    ## @purpose — Anti-survivorship (DevPlan 119 G1, AC-G1.2): удалённые из allowlist
    ##            скрипты (lint.sh/check-doc-headers.sh/converge.sh/context-promote.sh)
    ##            НЕ должны вернуться в allowlist, пока их размер <150 LOC и нет прямых
    ##            бинарных вызовов. Каждая запись allowlist валидируется: файл существует,
    ##            размер >150 LOC ИЛИ обнаружен прямой rsync/ssh/scp/ssh-keygen-вызов.
    ## @io        ⎋ ∅ — fail с деталями при нарушении
    ## @complexity O(K * L) — K записей allowlist, L строк на файл
    """
    logger.info("[IMP:8][test_allowlist_current] Validating allowlist relevance (DevPlan 119 G1)")

    assert ALLOWLIST, "Allowlist must not be empty — it protects bootstrap.sh/deploy.sh (G1)"

    for name in sorted(ALLOWLIST):
        filepath = ENTRYPOINTS_DIR / name
        assert filepath.is_file(), f"[IMP:10][G1] Allowlisted file missing: {name}"
        loc = count_loc(filepath)
        binary = find_binary_violations(filepath)
        over_limit = loc > 150
        has_binary = len(binary) > 0
        logger.info(
            "[IMP:8][allowlist_current] %s: %d LOC, %d binary call(s) — over_limit=%s, has_binary=%s",
            name,
            loc,
            len(binary),
            over_limit,
            has_binary,
        )
        assert over_limit or has_binary, (
            f"[IMP:10][G1] Allowlist entry '{name}' ({loc} LOC, {len(binary)} binary calls) "
            "is no longer justified — remove from ALLOWLIST (DevPlan 119 G1: allowlist = "
            "только скрипты >150 LOC или с прямыми бинарными вызовами)"
        )

    logger.info("[IMP:9][test_allowlist_current] PASS — allowlist актуален: %d justified entries", len(ALLOWLIST))


# endregion FUNC_test_allowlist_current
