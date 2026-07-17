# GREP_SUMMARY: ldd, trajectory, caplog, e2e, error-handling, volume-dirs, ci-detection
# STRUCTURE: ┌_print_ldd_trajectory┐ → ┌ldd_trajectory (decorator)┐ → ┌_is_ci_environment┐ → ┌_handle_e2e_error┐ → ┌_ensure_volume_dirs┐
# region MODULE_CONTRACT
## @purpose  LDD telemetry helpers, trajectory decorator, E2E error handler, and volume-dir utility extracted from conftest.py
## @scope    Shared across all test files; eliminates duplication of LDD boilerplate and error handling
## @invariants
##   - _print_ldd_trajectory() prints IMP:7-10 logs from caplog; returns True if IMP:9 found
##   - ldd_trajectory decorator auto-sets caplog level and asserts IMP:9 presence
##   - _handle_e2e_error() maps SSLError/ConnectionError/ProxyError → pytest.fail, Timeout → pytest.skip
##   - _ensure_volume_dirs() creates host directories for Docker bind-mount volumes
##   - All functions are module-level (not fixtures) for direct import
## @rationale DRY: previously duplicated in 6 test files; centralize in tests/conftest/ldd.py
# endregion MODULE_CONTRACT

import functools
import logging
import os

import pytest

_logger = logging.getLogger(__name__)


# region LDD_HELPERS
## @purpose — Shared LDD trajectory printer and E2E error handler for all test files.
## @scope — Used by test_e2e_*.py files; extracted here to eliminate duplication.
## @invariants
##   - _print_ldd_trajectory(): prints IMP:7-10 logs from caplog; used in all E2E tests
##   - _handle_e2e_error(): maps SSLError/ConnectionError/ProxyError → FAIL, Timeout → skip
##   - Both functions are module-level (not fixtures) for direct import in test files
## @rationale — DRY: previously duplicated in 6 test files; centralize in conftest.


def _print_ldd_trajectory(caplog) -> bool:
    """Print LDD trajectory filtered to IMP:7-10 from caplog records.

    ## @purpose — Extract and display IMP:7-10 log entries for agent-visible telemetry.
    ## @io — ⇥ caplog: pytest fixture → ⎋ bool (True if IMP:9 found)
    ## @complexity — O(n) where n = number of caplog records
    """
    found = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found = True
    print("--- END LDD TRAJECTORY ---")
    return found


# region LDD_DECORATOR


def ldd_trajectory(func):
    """Decorator: auto-print LDD trajectory and assert IMP:9 for test functions.

    ## @purpose — Eliminate boilerplate: caplog.set_level() + _print_ldd_trajectory() + assert found_imp9.
    ## @io — ⇥ func: test function → ⎋ wrapped function with automatic LDD handling
    ## @invariants
    ##   - Sets caplog.set_level(logging.DEBUG) before test runs
    ##   - Prints LDD trajectory (IMP:7-10) from caplog after test completes
    ##   - On test success: asserts at least one IMP:9 log was emitted
    ##   - On test failure: prints trajectory for debugging, re-raises original exception
    ##   - No-op if test function does not request caplog fixture
    ## @rationale — Medium #9: replaces 3 lines of boilerplate × ~250 function calls across 42 files
    ##              with a single decorator line. Reduced from ~750 lines of boilerplate to ~0.

    Usage:
        @ldd_trajectory
        def test_something(caplog):
            logger.critical("[IMP:9][test] ...")
            # test logic — no more caplog.set_level(), _print_ldd_trajectory(), or assert found_imp9

    Compatible with: @pytest.mark.parametrize, @pytest.mark.* markers.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        import logging as _logging

        caplog = kwargs.get("caplog")

        if caplog is not None:
            caplog.set_level(_logging.DEBUG)

        try:
            return func(*args, **kwargs)
        except Exception:
            if caplog is not None:
                _print_ldd_trajectory(caplog)
            _logger.exception("[IMP:9][conftest][ldd_trajectory] Test failed — re-raising after LDD trajectory dump")
            raise
        else:
            if caplog is not None:
                found = _print_ldd_trajectory(caplog)
                assert found, "Critical LDD Error: No IMP:9 business logic log found"

    return wrapper


# endregion LDD_DECORATOR


def _is_ci_environment() -> bool:
    """Detect if running in CI environment.

    ## @purpose — Distinguish CI from local runs for E2E error handling.
    ##            ConnectionError on CI → pytest.fail; on local → pytest.skip.
    ## @io — ⎋ bool: True if CI or E2E_MODE=ci
    ## @complexity — O(1)
    """
    _logger = logging.getLogger(__name__)
    is_ci = os.environ.get("CI") == "true" or os.environ.get("E2E_MODE") == "ci"
    _logger.info(
        "[IMP:7][conftest][_is_ci_environment] CI=%s, E2E_MODE=%s → is_ci=%s",
        os.environ.get("CI"),
        os.environ.get("E2E_MODE"),
        is_ci,
    )
    return is_ci


def _handle_e2e_error(exc: BaseException, url: str, caplog, logger=None) -> None:
    """Handle common E2E request errors: SSLError/ConnectionError/ProxyError → FAIL, Timeout → skip.

    ## @purpose — Standardised error handling for E2E HTTP tests.
    ##            This is the ONLY standard way to handle HTTP errors in E2E tests.
    ##            ALL E2E tests MUST use this function instead of writing their own except blocks.
    ## @standard — ALL E2E tests MUST use this function for HTTP error handling
    ## @io — ⇥ exc, url, caplog, logger(optional) → ⎋ None (side-effect: pytest.fail or pytest.skip)
    ## @complexity — O(1)
    ## @invariants
    ##   - SSLError → pytest.fail with SSL error message
    ##   - ConnectionError → pytest.fail with connection refused message (unless offline mode → skip)
    ##   - ProxyError → pytest.fail with proxy error message
    ##   - Timeout → pytest.skip with timeout message
    ##   - Any other → pytest.fail with generic error message
    ## @rationale — Auto-detect offline mode: local dev → skip ConnectionError by default,
    ##            CI → fail ConnectionError (services should be available).
    ##              Explicit E2E_OFFLINE env var overrides auto-detection in both cases.
    ##              Timeout stays SKIP because it's typically transient network issues, not service down.
    """
    from requests.exceptions import (
        ConnectionError as RequestsConnectionError,
    )
    from requests.exceptions import (
        ProxyError,
        SSLError,
    )
    from requests.exceptions import (
        Timeout as RequestsTimeout,
    )

    if logger is None:
        logger = logging.getLogger(__name__)

    if isinstance(exc, SSLError):
        logger.error("[IMP:9][e2e][fail] SSLError at %s: %s", url, exc)
        _print_ldd_trajectory(caplog)
        pytest.fail(f"SSL error at {url}: {exc}")
    elif isinstance(exc, ProxyError):
        logger.error("[IMP:9][e2e][fail] ProxyError at %s: %s", url, exc)
        _print_ldd_trajectory(caplog)
        pytest.fail(f"Proxy error at {url}: {exc}. Check HTTPS_PROXY/HTTP_PROXY env vars.")
    elif isinstance(exc, RequestsConnectionError):
        # Auto-detect offline mode: local dev → skip by default, CI → fail.
        # Explicit E2E_OFFLINE env var always wins over auto-detection.
        offline_env = os.environ.get("E2E_OFFLINE")
        if offline_env is None:
            offline_env = "true" if not _is_ci_environment() else "false"

        if offline_env == "true":
            logger.info("[IMP:7][e2e][skip] ConnectionError at %s: %s — offline mode, skip", url, exc)
            pytest.skip("E2E target not available — offline mode")
        else:
            logger.error("[IMP:9][e2e][fail] ConnectionError at %s: %s", url, exc)
            _print_ldd_trajectory(caplog)
            pytest.fail(f"Connection refused at {url}: {exc}")
    elif isinstance(exc, RequestsTimeout):
        logger.info("[IMP:7][e2e][skip] Timeout at %s: %s", url, exc)
        pytest.skip(f"Timeout at {url}: {exc}")
    else:
        logger.error("[IMP:9][e2e][fail] Request error at %s: %s", url, exc)
        _print_ldd_trajectory(caplog)
        pytest.fail(f"Request error at {url}: {exc}")


def _ensure_volume_dirs(dirs: list[str]) -> None:
    """Create volume bind-mount directories if they do not exist.

    ## @purpose — Docker compose bind-mount volumes require host directories
    ##            to exist before container start, otherwise Docker fails with
    ##            "failed to mount local volume: no such file or directory".
    ## @io — ⇥ dirs: list[str] of host paths → ⎋ None (side-effect: os.makedirs)
    ## @complexity — O(N) where N = len(dirs)
    ## @invariants
    ##   - PermissionError is caught and logged as warning (non-fatal)
    ##   - exist_ok=True prevents FileExistsError on concurrent runs
    """
    for d in dirs:
        try:
            os.makedirs(d, exist_ok=True)
        except PermissionError:
            import logging as _logging

            _logging.getLogger(__name__).warning(
                "[IMP:7][_ensure_volume_dirs] Cannot create '%s' (permission denied)",
                d,
            )


# endregion LDD_HELPERS
