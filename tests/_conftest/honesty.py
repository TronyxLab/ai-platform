# GREP_SUMMARY: honesty, require-docker, require-script, require-env, require-service, R4-fix, mode-transition
# STRUCTURE: ▶ require_docker_or_fail → ▶ require_service_healthy(TCP connect) → ◇ mode-dispatch(marker|xfail|fail) → ⎋ skip|xfail|fail
# region MODULE_CONTRACT
## @purpose  Test Honesty enforcement: NO_SERVICE = FAIL, not skip (Test Honesty Rule R4).
##           Поэтапный переход через REQUIRE_HONESTY_MODE env var.
## @scope    All tests requiring external service (Docker, scripts, env vars, TCP services).
## @invariants
##   - REQUIRE_HONESTY_MODE env var controls behavior:
##     "marker" (default W1) → pytest.mark.skip (soft, but tagged [IMP:10][honesty])
##     "xfail"               → pytest.xfail(strict=False)
##     "fail" (target W2)    → pytest.fail (honest)
##   - Каждый вызов логирует [IMP:10][honesty] mode + missing service + action
##   - На CI без Docker: в режиме "marker" — skip; в режиме "fail" — fail (R4 compliant)
## @rationale R-RISK-2: прямой skip→fail переход временно ломает CI на staging (no Docker).
##            Поэтапность: marker → xfail → fail даёт команде время на настройку CI runners.
##            Wave 1 = marker (no behavior change, just tagging).
##            Wave 2 = переключение на fail (operator decision).
## @changes
##   LAST_CHANGE: 2026-07-21 | Created (DevPlan 028 W1-E2)
##   2026-08-01 | DevPlan 117 Brief F (T6 #46): require_service_healthy (TCP port-check)
# endregion MODULE_CONTRACT

import os
import pathlib
import shutil
import socket
import subprocess
from typing import Literal

import pytest

HonestyMode = Literal["marker", "xfail", "fail"]


def _honesty_mode() -> HonestyMode:
    """Read REQUIRE_HONESTY_MODE env var. Default: 'marker' (soft Wave 1)."""
    mode = os.environ.get("REQUIRE_HONESTY_MODE", "marker").lower().strip()
    if mode not in ("marker", "xfail", "fail"):
        raise ValueError(
            f"[IMP:10][honesty] invalid REQUIRE_HONESTY_MODE={mode!r}, expected one of: marker, xfail, fail"
        )
    return mode  # type: ignore[return-value]


def _docker_available() -> bool:
    """Check if Docker daemon is reachable."""
    if not shutil.which("docker"):
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _dispatch(mode: HonestyMode, reason: str) -> None:
    """Dispatch action based on honesty mode."""
    action_map = {"marker": "skip", "xfail": "xfail", "fail": "fail"}
    print(f"[IMP:10][honesty] mode={mode} action={action_map[mode]} reason={reason}")
    if mode == "marker":
        pytest.skip(f"[honesty:marker] {reason}")
    elif mode == "xfail":
        pytest.xfail(f"[honesty:xfail] {reason}")
    elif mode == "fail":
        pytest.fail(f"[honesty:fail] {reason}", pytrace=False)


# region PUBLIC_API


def require_docker_or_fail(reason: str = "Docker daemon required") -> None:
    """R4 fix: skip/fail when Docker not available. Mode controlled by REQUIRE_HONESTY_MODE."""
    if _docker_available():
        print("[IMP:9][honesty] Docker available, proceeding")
        return
    _dispatch(_honesty_mode(), f"Docker daemon not available — {reason}")


def require_script_or_fail(script_path: pathlib.Path, reason: str = "") -> None:
    """R4 fix: skip/fail when required script not found."""
    if script_path.exists() and os.access(script_path, os.X_OK):
        return
    _dispatch(_honesty_mode(), f"Script not found or not executable: {script_path} — {reason}")


def require_env_or_fail(var: str, reason: str = "") -> None:
    """R4 fix: skip/fail when required env var not set."""
    if os.environ.get(var):
        return
    _dispatch(_honesty_mode(), f"Env var not set: {var} — {reason}")


def require_service_healthy(host: str, port: int, reason: str = "", timeout: int = 5) -> None:
    """R4 fix: skip/fail when a TCP service on host:port is not reachable.

    ## @purpose — R4 enforcement for inline port-unreachable skips: TCP connect
    ##            probe (socket.create_connection). On success → no-op; on OSError
    ##            → dispatch через _honesty_mode() (marker→skip, xfail→xfail, fail→fail).
    ## @io — ⇥ host: str, port: int, reason: str, timeout: int=5 → ⎋ None (side-effect)
    ## @complexity — O(1) — single TCP connect with timeout
    ## @invariants
    ##   - TCP connect с таймаутом (default 5s)
    ##   - Успех → log [IMP:9] + return (без диспетчеризации)
    ##   - OSError (refused/timeout/DNS) → dispatch через _honesty_mode()
    ## @rationale — DevPlan 117 Brief F (T6 #46): миграция inline `pytest.skip("Port ... not reachable")`
    ##            на единый honesty-механизм. Один источник поведения для всех port-checks.
    """
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            print(f"[IMP:9][honesty] Service healthy: {host}:{port} — proceeding")
            return
    except OSError:
        _dispatch(_honesty_mode(), f"Service not reachable on {host}:{port} — {reason}")


# endregion PUBLIC_API
