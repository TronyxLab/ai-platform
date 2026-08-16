# GREP_SUMMARY: utilities, source_and_run, assert_ldd_stderr, shared-test-utilities, LDD, subprocess
# STRUCTURE: source_and_run → tempfile + subprocess | assert_ldd_stderr → grep IMP:7-10 → assert IMP:9
# region MODULE_CONTRACT
## @purpose  Shared test utilities extracted from tests/conftest.py — provides source_and_run for shell-function testing
##           and assert_ldd_stderr for LDD telemetry verification in subprocess-based tests.
## @scope    Two functions: source_and_run (source a bash script and invoke a function in isolated subprocess),
##           assert_ldd_stderr (parse stderr for IMP:7-10 log lines and assert IMP:9 presence).
## @invariants
##   - source_and_run always creates a temp .sh file (never pipes) to preserve bash source semantics.
##   - The temp script is deleted (os.unlink) after subprocess completion regardless of success.
##   - assert_ldd_stderr prints all IMP:7-10 lines to stdout before asserting; zero output means failure.
##   - script_path is required (raises TypeError if None) — no auto-detection.
## @rationale  Extracted from conftest.py to avoid circular imports and keep the test infrastructure modular.
##             The temp-script approach (not pipe) is required because bash source semantics are lost when
##             piping commands into bash.
# endregion MODULE_CONTRACT

import logging
import os
import pathlib
import subprocess

logger = logging.getLogger(__name__)

# region SHARED_TEST_UTILITIES


def source_and_run(
    function_call: str, env: dict[str, str] | None = None, script_path: str | None = None
) -> subprocess.CompletedProcess:
    """Source a bash script and run a function, returning CompletedProcess.

    ## @purpose — Run a single shell function in isolated subprocess after sourcing the script.
    ##            Uses a temp script (not pipe) to preserve bash source semantics.
    ## @io       ⇥ script_path, function_call, env → ⎋ CompletedProcess
    ## @complexity O(1)
    """
    import tempfile

    sp = script_path
    if sp is None:
        msg = "script_path is required — pass script_path=str(SCRIPT_PATH) from calling module"
        raise TypeError(msg)
    with tempfile.NamedTemporaryFile(encoding="utf-8", mode="w", suffix=".sh", delete=False) as f:
        f.write(f"source '{sp}' && {function_call}\n")
        tmp_script = f.name
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    result = subprocess.run(["bash", tmp_script], capture_output=True, text=True, env=full_env, check=False)
    pathlib.Path(tmp_script).unlink()
    return result


def assert_ldd_stderr(result: subprocess.CompletedProcess, expected_patterns: list[str] | None = None) -> None:
    """Print LDD trajectory from stderr and assert at least one IMP:9 log present.

    ## @purpose — LDD telemetry for shell-based tests (caplog doesn't capture subprocess)
    ## @io       ⇥ result.CompletedProcess, expected_patterns: optional patterns to find in stderr
    ##           ⎛ None (assert side-effect)
    ## @complexity O(n) where n = stderr lines
    """
    found = False
    logger.info("--- LDD TRAJECTORY (IMP:7-10) [from stderr] ---")
    for line in result.stderr.splitlines():
        if "[IMP:" in line:
            imp_level = _parse_imp_level(line)
            if imp_level is not None:
                if imp_level >= 7:
                    logger.info("%s", line)
                if imp_level >= 9:
                    found = True
    logger.info("--- END LDD TRAJECTORY ---")
    assert found, "Critical LDD Error: No IMP:9 business logic log found in stderr"
    if expected_patterns:
        for pattern in expected_patterns:
            assert pattern in result.stderr, f"Expected '{pattern}' in stderr:\n{result.stderr}"


def _parse_imp_level(line: str) -> int | None:
    """Извлечь уровень IMP:N из строки LDD-лога (PLW0717-хелпер).

    ## @io — ⇥ line: str (содержит "[IMP:N]") → ⎋ int|None (уровень или None при мусоре)
    ## @complexity O(1)
    """
    try:
        imp_str = line.split("[IMP:", 1)[1].split("]", 1)[0]
        return int(imp_str)
    except (ValueError, IndexError):
        return None


# endregion SHARED_TEST_UTILITIES
