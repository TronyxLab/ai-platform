#!/usr/bin/env python3
# GREP_SUMMARY: converge-infra, report-add, report-emit, set-exit, run-subprocess, try-chmod, unit-enabled, reset-state, module-globals
# STRUCTURE: ▶ reset_state() → ⚡ set_exit(severity) / report_add(unit,status,detail) → ⚡ report_emit() → ⚡ run_subprocess(cmd,timeout,check) → ⚡ try_chmod(path,unit) → ⚡ unit_enabled(filter,name) → ⎋ модульные глобалы (drifts/exit_code/has_errors/...)
# region MODULE_CONTRACT
## @purpose  Инфраструктурный модуль converge/ пакета — модульные глобалы (drifts, exit code,
##           node/core paths, dry-run/report-only флаги) + report/subprocess/chmod/unit-filter
##           хелперы. Извлечён из reconciler.py (B9 T2, U-31). Все имена ПУБЛИЧНЫЕ.
## @scope    infra.py: reset_state, unit_enabled, report_init, report_add, report_emit,
##           set_exit, run_subprocess, try_chmod + модульные глобалы (drifts, exit_code,
##           has_errors, has_warnings, node_name, node_yaml_path, core_dir, dry_run,
##           report_only, templates_dir, modules_dir) + константы (AUDIT_LOG_DIR, PROXY_NET,
##           DOCKER_TIMEOUT, FILE_OP_TIMEOUT, HOSTS_FILE, PROJECTS_BASE, SUDOERS_DIR,
##           COOLDOWN_FILE, BAD_DOCKER_STATES).
##           Доменные модули обращаются к глобалам через `import ...infra as infra` +
##           `infra.core_dir` (публичный атрибут) — from-import НЕ отслеживает переназначение.
## @invariants
##   - Модульные глобалы — единый source of truth для всех R-юнитов (мигрировано из reconciler.py)
##   - set_exit: severity 2 → exit_code=2 + has_errors; 1 → exit_code≥1 + has_warnings; 0 → no-op
##   - report_emit: JSON-отчёт с node/timestamp/exit_code/status/drifts (--report-only контракт)
##   - run_subprocess: FileNotFoundError → rc=127, TimeoutExpired → rc=124 (graceful, никогда не raise)
##   - unit_enabled: пустой filter = все юниты включены
## @rationale DevPlan 116 B9 D3: инфраструктура reconciler (report/exit/subprocess — модульные
##            глобалы) вынесена в converge/infra.py — домены R1-R9 чистые, без глобального состояния.
## @changes  2026-08-01 · Extracted from reconciler.py (B9 T2)
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── Constants (мигрированы из reconciler.py) ──
AUDIT_LOG_DIR = "/var/log/platform"
AUDIT_LOG_FILE = f"{AUDIT_LOG_DIR}/audit.log"
PROXY_NET = "proxy-net"
DOCKER_TIMEOUT = 30
"""## @invariant subprocess timeout for all docker/system commands (seconds)."""
FILE_OP_TIMEOUT = 15
"""## @invariant subprocess timeout for file operations (chmod, chown, mkdir)."""
HOSTS_FILE = "/etc/hosts"
PROJECTS_BASE = "/opt/projects"

# R8/R9 constants (overridable by tests via monkeypatch)
SUDOERS_DIR: str = "/etc/sudoers.d"
COOLDOWN_FILE: str = "/var/lib/platform/.converge_cooldown.json"
"""## @invariant Cooldown file path for R9 runtime state — stores last_healed_run per module."""

BAD_DOCKER_STATES = {"exited", "restarting", "dead", "unhealthy", "paused"}
"""## @invariant Container states that trigger self-heal via docker compose up -d."""

# ── Модульные глобалы (мигрированы из reconciler.py — публичные имена) ──
drifts: list[dict] = []
exit_code: int = 0
has_errors: bool = False
has_warnings: bool = False
node_name: str = ""
node_yaml_path: str = ""
core_dir: str = ""
dry_run: bool = False
report_only: bool = False
templates_dir: str = ""
modules_dir: str = ""
converge_run_counter: int = 0


# ═══════════════════════════════════════════════════════════════════
# region FUNC_reset_state
## @purpose  Reset module-level state between reconcile runs (idempotent init)
def reset_state() -> None:
    """Reset all module-level state variables."""
    global drifts, exit_code, has_errors, has_warnings
    drifts = []
    exit_code = 0
    has_errors = False
    has_warnings = False
    logger.info("[IMP:7][reset_state] Module state reset")


# endregion FUNC_reset_state


# ═══════════════════════════════════════════════════════════════════
# region FUNC_unit_enabled
## @purpose  Check if a given R-unit should be executed based on --units filter.
##           If units_filter is empty (default), all units are enabled.
## @param units_filter  Comma-separated unit filter string (e.g., "R1,R3")
## @param unit_name     Unit name to check (e.g., "R1", "R3")
## @return  True if unit is enabled, False if filtered out
## @complexity O(n) where n = number of units in filter
def unit_enabled(units_filter: str, unit_name: str) -> bool:
    """Check unit filter membership.

    If units_filter is empty or None, all units are enabled.
    The unit_name is compared against comma-separated tokens (whitespace-trimmed).
    """
    if not units_filter:
        return True
    tokens = [t.strip() for t in units_filter.split(",") if t.strip()]
    return unit_name in tokens


# endregion FUNC_unit_enabled


# ═══════════════════════════════════════════════════════════════════
# region FUNC_report_init / report_add / report_emit
## @purpose  JSON report helpers for --report-only mode. Mirrors shell:
##           report_init() → report_add() × N → report_emit() → exit


def report_init() -> None:
    """Initialize drift report — reset drift list."""
    global drifts
    drifts = []
    logger.info("[IMP:7][report_init] Initialized drift report")


def report_add(unit: str, status: str, detail: str) -> None:
    """Add a drift entry to the report.

    Args:
        unit: R-unit name (e.g., "R1", "R3")
        status: One of "ok", "mutated", "skipped", "warn", "fail", "converged", "awaiting_deploy"
        detail: Human-readable description of the drift
    """
    entry = {"unit": unit, "status": status, "detail": detail}
    drifts.append(entry)
    logger.info("[IMP:8][report_add] %s | %s | %s", unit, status, detail)


def report_emit() -> str:
    """Emit JSON report as string and return it.

    Builds the full report object with node name, timestamp, exit code,
    status reason, and collected drifts.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if has_errors:
        status_reason = "errors"
    elif has_warnings:
        status_reason = "mutations_applied"
    else:
        status_reason = "converged"

    report = {
        "node": node_name,
        "timestamp": ts,
        "exit_code": exit_code,
        "status": status_reason,
        "drifts": drifts,
    }
    report_json = json.dumps(report, indent=2)
    logger.info("[IMP:8][report_emit] Report: %s", report_json)
    return report_json


# endregion FUNC_report_init / report_add / report_emit


# ═══════════════════════════════════════════════════════════════════
# region FUNC_set_exit
## @purpose  Update exit code and flags based on severity
def set_exit(severity: int) -> None:
    """Set exit code and flags.

    Args:
        severity: 0=ok, 1=warning, 2=error
    """
    global exit_code, has_warnings, has_errors
    if severity >= 2:
        exit_code = 2
        has_errors = True
    elif severity == 1:
        if exit_code < 1:
            exit_code = 1
        has_warnings = True
    # severity 0 = no-op


# endregion FUNC_set_exit


# ═══════════════════════════════════════════════════════════════════
# region FUNC_run_subprocess
## @purpose  Safe subprocess runner with uniform error handling
def run_subprocess(
    cmd: list[str],
    timeout: int = DOCKER_TIMEOUT,
    check: bool = False,
) -> subprocess.CompletedProcess:
    """Run a subprocess with consistent error handling.

    Returns a CompletedProcess on success. On failure:
    - If check=True, the exception propagates
    - If check=False, returns a failed CompletedProcess with returncode != 0
    """
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        logger.warning("[IMP:8][run_subprocess] Binary not found: %s", cmd[0])
        return subprocess.CompletedProcess(args=cmd, returncode=127, stdout="", stderr=f"{cmd[0]}: not found")
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:8][run_subprocess] Timeout after %ds: %s", timeout, " ".join(cmd))
        return subprocess.CompletedProcess(args=cmd, returncode=124, stdout="", stderr="timeout")


# endregion FUNC_run_subprocess


# region FUNC_try_chmod
## @purpose  Attempt chmod with OSError handling, returns success bool
def try_chmod(path: str, unit: str) -> bool:
    """Try to chmod a file, returning True on success. Handles OSError internally."""
    try:
        os.chmod(path, os.stat(path).st_mode | 0o110)  # ug+x
        return True
    except OSError as exc:
        logger.warning("[IMP:8][converge][%s] chmod failed for %s: %s", unit, path, exc)
        return False


# endregion FUNC_try_chmod
