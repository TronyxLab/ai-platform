# GREP_SUMMARY: test-lib-logging logging.sh bash LDD log_imp subprocess stderr IMP structured-logging wrapper auto-block prefix side-effect
# STRUCTURE: ▶ _run_bash(script → subprocess.run) → ○ ◇ assert stderr IMP:N + block + msg → ⊕ stdout_empty + returncode_0 → ⎋ test functions

# region MODULE_CONTRACT [DOMAIN(TESTING):3; CONCEPT(BASH-LDD):2; TECH(PYTEST):2]
## @purpose  Unit tests for core/lib/logging.sh — the centralised LDD logging library
##           for all bash scripts in the platform. Tests verify structured stderr output
##           format, auto-block detection, custom prefix, and zero side-effects on source.
##           Волна 118 B6: log_info (IMP:6) удалён из logging.sh (0 callers) — R5 negative.
##           Аудит 2026-08-22: log_warn/log_fail/log_crit удалены из logging.sh (0
##           production-callers) — R5 negative: type -t <wrapper> = пусто.
## @scope    Test functions covering:
##
##           - log_imp with explicit block name (test_log_imp_explicit_block)
##           - log_imp with auto-block from FUNCNAME[1] (test_log_imp_auto_block)
##           - log_imp stderr-only invariant (test_log_imp_stdout_empty)
##           - removed semantic wrappers log_warn/fail/crit (R5 negative, parametrized)
##           - custom __LOG_PREFIX injection (test_custom_log_prefix)
##           - zero side-effects on source (test_no_side_effects_on_source)
## @invariants
##
##   - Every test uses tmp_path for script isolation (Zero Hardcode Rule)
##   - LIB path resolved via Path(__file__).resolve() — no hardcoded paths
##   - All positive scenarios assert stderr contains [IMP:N] at expected level
##   - Bash scripts run with subprocess.run (bash stderr capture), NOT Python logging
##   - No caplog fixture: bash logs go to stderr, not Python logging subsystem
##   - LDD verification via stderr assertion: IMP:N present for N >= 7 in relevant tests
##   - Every test verifies returncode == 0
## @rationale Q: Why subprocess.run instead of pure Python simulation?
##            A: logging.sh is a pure bash library whose core logic (FUNCNAME auto-detection,
##            prefix resolution, stderr redirection) can only be tested in a real bash
##            environment. Subprocess with capture_output is the standard pattern for
##            bash library testing. The _run_bash helper isolates each test in a temp
##            file to avoid cross-test contamination.
##            Q: Why not @ldd_trajectory decorator?
##            A: @ldd_trajectory relies on caplog (Python logging capture). logging.sh
##            writes directly to bash stderr, bypassing Python logging entirely. LDD
##            verification is done by asserting stderr contains [IMP:N] for the expected
##            importance level.
## @changes LAST_CHANGE: 2026-07-07 · Initial implementation per DevPlan test spec
##           2026-08-02 · Волна 118 B6 — log_info параметр убран (removed API)
##           2026-08-22 · Аудит simplify-refactor-waves T0.3 — wrapper-тесты заменены
##           на R5 negative (test_log_wrapper_removed); _module_contract стаб удалён (T0.5)
## @modulemap
##   - _run_bash                     [W:30] Helper: write temp script, run bash, return result
##   - test_log_imp_explicit_block   [W:40] Explicit block in stderr contains [IMP:7][prefix][myblock]
##   - test_log_imp_auto_block       [W:40] Auto-block resolves to "main" at script top level
##   - test_log_imp_stdout_empty     [W:30] log_imp writes ONLY to stderr
##   - test_log_wrapper_removed      [W:40] R5 negative: log_warn/fail/crit не определены
##   - test_custom_log_prefix        [W:40] __LOG_PREFIX="myscript" → [myscript] in output
##   - test_no_side_effects_on_source [W:20] source logging.sh → no stdout/stderr
## @usecases
##   - Developer: run pytest after modifying logging.sh → all tests pass, no regressions
##   - Architect: verify __LOG_PREFIX contract, stderr-only invariant, auto-block behavior
# endregion MODULE_CONTRACT

import subprocess
from pathlib import Path

import pytest

# ═══════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════

# Resolve absolute path to logging.sh once at module load time.
# Relies on: tests/test_lib_logging.py → ../core/lib/logging.sh
_LIB_PATH: Path = Path(__file__).resolve().parent.parent.parent / "core" / "lib" / "logging.sh"


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════


# region FUNC__run_bash
## @purpose  Write a bash script to a temp file and execute it via subprocess,
##           capturing stdout/stderr/returncode. Each call gets a fresh script
##           in an isolated tmp_path directory — no cross-test contamination.
## @io       ⇥ (tmp_path: Path, code: str) → ⎋ CompletedProcess(stdout, stderr, returncode)
## @complexity O(1) — single subprocess.run with 10s timeout
## @invariants
##   - Always prepends #!/usr/bin/env bash + set -euo pipefail
##   - Always sources logging.sh via _LIB_PATH
##   - Script file is chmod 755 before execution
##   - Timeout set to 10 seconds (fail-fast on infinite loops)
def _run_bash(tmp_path: Path, code: str) -> subprocess.CompletedProcess:
    """Run bash code with logging.sh sourced, return subprocess result.

    ## @purpose  Isolate bash script execution in a temp file for deterministic testing.
    ##            Sources the library under test (_LIB_PATH) before executing user code.
    ## @io       ⇥ tmp_path: Path — pytest fixture for temp dir
    ##             code: str — bash commands to execute after sourcing logging.sh
    ##           ⎋ CompletedProcess with stdout, stderr, returncode attributes
    ## @complexity O(1)
    """
    script = tmp_path / "test_script.sh"
    lib_path_escaped = str(_LIB_PATH)

    script_content = f'#!/usr/bin/env bash\nset -euo pipefail\nLIB="{lib_path_escaped}"\nsource "$LIB"\n{code}\n'
    script.write_text(script_content, encoding="utf-8")
    script.chmod(0o755)

    return subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=10, check=False)


# endregion FUNC__run_bash


# ═══════════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_log_imp_explicit_block
## @purpose  Verify log_imp with explicit block name produces correct stderr format:
##           [IMP:7][testprefix][myblock] hello world
## @io       ⇥ tmp_path → ⎋ assert stderr == expected, stdout empty, returncode == 0
## @complexity O(1)
def test_log_imp_explicit_block(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · Regression: explicit block must appear verbatim in output
    # · Scenario: log_imp 7 "myblock" "hello world" with __LOG_PREFIX="testprefix"
    # · Last fail: Never
    # · Remove if: log_imp signature changes (removes $2 block parameter)
    result = _run_bash(
        tmp_path,
        """
__LOG_PREFIX="testprefix"
log_imp 7 "myblock" "hello world"
""",
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    assert not result.stdout, f"stdout not empty: {result.stdout!r}"
    assert "[IMP:7][testprefix][myblock] hello world" in result.stderr, (
        f"Expected [IMP:7][testprefix][myblock] hello world in stderr, got: {result.stderr}"
    )


# endregion FUNC_test_log_imp_explicit_block


# region FUNC_test_log_imp_auto_block
## @purpose  Verify log_imp with block="-" auto-detects block from FUNCNAME[1].
##           When called at script top-level (not inside another function),
##           FUNCNAME[1] is unset → falls back to "main".
## @io       ⇥ tmp_path → ⎋ assert stderr block == "main"
## @complexity O(1)
def test_log_imp_auto_block(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · Regression: auto-block resolution when FUNCNAME[1] is unset
    # · Scenario: log_imp 7 "-" "hello" at script top level → block = "main"
    # · Last fail: Never
    # · Remove if: log_imp auto-block logic changes (FUNCNAME fallback)
    result = _run_bash(
        tmp_path,
        """
__LOG_PREFIX="testprefix"
log_imp 7 "-" "hello"
""",
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    assert "[IMP:7][testprefix][main] hello" in result.stderr, (
        f"Expected auto-block 'main' in stderr, got: {result.stderr}"
    )


# endregion FUNC_test_log_imp_auto_block


# region FUNC_test_log_imp_stdout_empty
## @purpose  Verify log_imp writes strictly to stderr only — stdout must be empty.
##           This is the stderr-only invariant from the MODULE_CONTRACT.
## @io       ⇥ tmp_path → ⎋ assert stdout == "", stderr contains IMP line
## @complexity O(1)
def test_log_imp_stdout_empty(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · Regression: log_imp MUST never write to stdout
    # · Scenario: log_imp 7 "b" "msg" → stdout empty, stderr has [IMP:7]
    # · Last fail: Never
    # · Remove if: log_imp changes stderr redirection (echo >&2 → echo)
    result = _run_bash(
        tmp_path,
        """
__LOG_PREFIX="test"
log_imp 7 "b" "msg"
""",
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    assert not result.stdout, f"log_imp wrote to stdout: {result.stdout!r}"
    assert "[IMP:7]" in result.stderr, f"Expected IMP:7 in stderr, got: {result.stderr}"


# endregion FUNC_test_log_imp_stdout_empty


@pytest.mark.parametrize(
    "func_name",
    ["log_warn", "log_fail", "log_crit"],
)
def test_log_wrapper_removed(func_name, tmp_path):
    """R5 negative (аудит 2026-08-22): log_warn/log_fail/log_crit удалены из logging.sh (0 callers)."""
    # 🧪 TRAP[TEST] · NEGATIVE (R5) · аудит 2026-08-22 — semantic wrappers удалены
    # · Scenario: source logging.sh → type -t <wrapper> → пусто (не функция)
    # · Last fail: wrappers существовали до аудита 2026-08-22 (logging.sh L92-114)
    # · Remove if: semantic wrappers будут восстановлены
    result = _run_bash(
        tmp_path,
        f"""
if [[ "$(type -t {func_name})" == "function" ]]; then
    echo "[IMP:10][test] FAIL: {func_name} still defined" >&2
    exit 1
fi
echo "[IMP:9][test] {func_name} REMOVED — OK" >&2
exit 0
""",
    )
    assert result.returncode == 0, f"[IMP:9][test_log_wrapper_removed] FAIL: {func_name} не удалён: {result.stderr}"
    assert "REMOVED" in result.stderr, f"[IMP:9][test] FAIL: no REMOVED marker: {result.stderr}"
    __import__("logging").getLogger(__name__).critical("[IMP:9][test_log_wrapper_removed] %s removed (R5)", func_name)


# region FUNC_test_log_info_removed
## @purpose  R5 negative (волна 118 B6): log_info/log_ok удалены из logging.sh (0 callers).
## @io       ⇥ tmp_path → ⎋ assert rc==0, stderr 'REMOVED'
## @complexity O(1)
def test_log_info_removed(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · NEGATIVE (R5) · B6 — log_info/log_ok удалены из logging.sh
    # · Scenario: source logging.sh → type -t log_info → пусто (не функция)
    # · Last fail: log_info существовал до волны 118 B6 (logging.sh L98)
    # · Remove if: log_info/log_ok будут восстановлены
    result = _run_bash(
        tmp_path,
        """
if [[ "$(type -t log_info)" == "function" ]] || [[ "$(type -t log_ok)" == "function" ]]; then
    echo "[IMP:10][test] FAIL: log_info/log_ok still defined" >&2
    exit 1
fi
echo "[IMP:9][test] log_info/log_ok REMOVED — OK" >&2
exit 0
""",
    )
    assert result.returncode == 0, (
        f"[IMP:9][test_log_info_removed] FAIL: log_info/log_ok не удалены, stderr: {result.stderr}"
    )
    assert "REMOVED" in result.stderr, f"[IMP:9][test] FAIL: no REMOVED marker: {result.stderr}"
    logger = __import__("logging").getLogger(__name__)
    logger.critical("[IMP:9][test_log_info_removed] PASS: log_info/log_ok removed (B6 R5)")


# endregion FUNC_test_log_info_removed


# region FUNC_test_custom_log_prefix
## @purpose  Verify __LOG_PREFIX env variable is reflected in stderr output.
##           Prefix defaults to "unknown" when unset; setting it to "myscript"
##           should produce [myscript] in the output.
## @io       ⇥ tmp_path → ⎋ assert stderr contains [myscript]
## @complexity O(1)
def test_custom_log_prefix(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · Regression: __LOG_PREFIX must be evaluated at call time
    # · Scenario: __LOG_PREFIX="myscript" → log line contains [myscript]
    # · Last fail: Never
    # · Remove if: __LOG_PREFIX resolution changes (must remain call-time, not source-time)
    result = _run_bash(
        tmp_path,
        """
__LOG_PREFIX="myscript"
log_imp 7 "b" "msg"
""",
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    assert "[myscript]" in result.stderr, f"Expected [myscript] in stderr, got: {result.stderr}"
    assert "[IMP:7][myscript]" in result.stderr, f"Expected [IMP:7][myscript] in stderr, got: {result.stderr}"


# endregion FUNC_test_custom_log_prefix


# region FUNC_test_no_side_effects_on_source
## @purpose  Verify sourcing logging.sh produces zero output on stdout and stderr.
##           The library declares a @invariants: "source MUST NOT execute any code".
##           This test guards against accidental top-level code injection.
## @io       ⇥ tmp_path → ⎋ assert stdout == "", stderr == "", returncode == 0
## @complexity O(1)
def test_no_side_effects_on_source(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · Regression: source with no output prevents silent side-effects
    # · Scenario: source logging.sh (no function calls) → stdout empty, stderr empty
    # · Last fail: Never
    # · Remove if: library adds intentional init output (unlikely — violates contract)
    result = _run_bash(
        tmp_path,
        """
# Source only — no function calls
true
""",
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    assert not result.stdout, f"stdout not empty after source: {result.stdout!r}"
    assert not result.stderr, f"stderr not empty after source: {result.stderr!r}"


# endregion FUNC_test_no_side_effects_on_source
