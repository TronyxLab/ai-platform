#!/usr/bin/env python3
# GREP_SUMMARY: reconciler, converge, reconcile-perms, reconcile-audit-log, reconcile-projects, reconcile-networks, detect-hosts-drift, verify-vhosts, drift-detection, desired-state, r1, r2, r3, r4, r5, r6, r7, r8, r9, reconcile-volumes, reconcile-sudoers, reconcile-runtime, detect-only, o7, self-heal, atomic-write, visudo, cooldown, json-report, node-yaml, idempotent
# STRUCTURE: ▶ argparse ┌--node-yaml --node-name --core-dir --templates-dir --modules-dir --dry-run --report-only --units┐ → ○ _unit_enabled filter → ▶ R1 reconcile_perms ⚡ find+chmod ug+x → ▶ R2 reconcile_audit_log ⚡ 0664 root:adm + usermod → ▶ R3 reconcile_projects ⚡ yaml parse + mkdir + stub + .env.platform → ▶ R4 reconcile_networks ⚡ docker network inspect proxy-net → ▶ R5 detect_hosts_drift ⚡ grep /etc/hosts → ▶ R6 verify_vhosts ⚡ nginx -t + orphan detection → ▶ R7 reconcile_volumes ⚡ docker compose config + volume inspect (detect-only) → ▶ R8 reconcile_sudoers ⚡ template render + visudo -c + atomic write → ▶ R9 reconcile_runtime_state ⚡ docker inspect → compose up -d + cooldown → ⊕ aggregate exit_code {0,1,2} → ⎋ JSON report stdout
# region MODULE_CONTRACT
## @purpose  Idempotent desired-state reconciler for platform VPS — reads node.yaml as desired
##           state and converges 6 dimensions (R1-R6) to match. Extracted from converge.sh
##           (W4-E3 Strangler Fig decomposition) with typed Python contracts and unit tests.
## @scope    R1 reconcile_perms — executable-bit fix (defense-in-depth, *.sh outside core/lib/)
##           R2 reconcile_audit_log — audit.log 0664 root:adm + ci-deploy adm group
##           R3 reconcile_projects — per-project directory + stub ai-platform.yaml + .env.platform
##           R4 reconcile_networks — proxy-net Docker network existence + project container connectivity
##           R5 detect_hosts_drift — read-only /etc/hosts stale entry detection
##           R6 verify_vhosts — nginx vhost config integrity, GENERATED markers, orphan detection, nginx -t
##           R7 reconcile_volumes — detect-only named volume check (O7 invariant)
##           R8 reconcile_sudoers — sudoers.d drift detection + self-heal via visudo + atomic write
##           R9 reconcile_runtime_state — docker container state check + compose up -d self-heal + cooldown
## @location core/internal/bootstrap/converge/reconciler.py
## @invariants
##   - R-units are independent — one unit failure does NOT abort others
##   - Exit code: 0=converged (no drifts), 1=warnings (non-critical drift), 2=errors (critical failures)
##   - --report-only: no mutations, exit 0, JSON drift report on stdout
##   - --dry-run: prints plan without mutations, exit 0
##   - --units R1,R3,...: comma-separated unit filter; empty = all units (default)
##   - node.yaml must be present or FATAL exit 2
##   - All subprocess calls: subprocess.run(cmd, capture_output=True, text=True, timeout=30)
##   - Never modifies project data (volumes, DB, images — invariant O7)
##   - JSON report output schema: {"node":"...","timestamp":"...","exit_code":N,"status":"...","drifts":[...]}
## @rationale Centralized desired-state reconciler replaces 7 manual SSH mutations.
##            R-units are lightweight idempotent checks — fast (seconds) on repeat run.
##            Design chosen over per-mutation lifecycle checkpoints for atomic drift
##            detection + standalone usability.
## @rationale --units filter enables selective reconciliation from node-lifecycle.sh
##            step_6b (R3 only for early project scaffold) before full converge.
## @changes
##   2026-07-22 · Created (W4-E3 extraction from converge.sh)
##   2026-07-22 · Added R7 reconcile_volumes (detect-only named volumes)
##   2026-07-22 · Added R8 reconcile_sudoers (sudoers.d drift + self-heal via visudo + atomic write)
##   2026-07-22 · Added R9 reconcile_runtime_state (container state + compose up -d + cooldown)
##   2026-07-30 · T9b — replaced subprocess call to gen-env-platform.sh with direct
##              import of generate_env_platform() from gen_env_platform module
# endregion MODULE_CONTRACT

import argparse
import contextlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Constants ──
AUDIT_LOG_DIR = "/var/log/platform"
AUDIT_LOG_FILE = f"{AUDIT_LOG_DIR}/audit.log"
PROXY_NET = "proxy-net"
DOCKER_TIMEOUT = 30
"""## @invariant subprocess timeout for all docker/system commands (seconds)."""
FILE_OP_TIMEOUT = 15
"""## @invariant subprocess timeout for file operations (chmod, chown, mkdir)."""
HOSTS_FILE = "/etc/hosts"
PROJECTS_BASE = "/opt/projects"

# Internal state (module-level to mirror shell globals)
_drifts: list[dict] = []
_exit_code: int = 0
_has_errors: bool = False
_has_warnings: bool = False
_node_name: str = ""
_node_yaml_path: str = ""
_core_dir: str = ""
_dry_run: bool = False
_report_only: bool = False
_templates_dir: str = ""
_modules_dir: str = ""
_converge_run_counter: int = 0

# R8/R9 constants (overridable by tests via monkeypatch)
SUDOERS_DIR: str = "/etc/sudoers.d"
COOLDOWN_FILE: str = "/var/lib/platform/.converge_cooldown.json"
"""## @invariant Cooldown file path for R9 runtime state — stores last_healed_run per module."""


# ═══════════════════════════════════════════════════════════════════
# region FUNC__reset_state
## @purpose  Reset module-level state between reconcile runs (idempotent init)
def _reset_state() -> None:
    """Reset all module-level state variables."""
    global _drifts, _exit_code, _has_errors, _has_warnings
    _drifts = []
    _exit_code = 0
    _has_errors = False
    _has_warnings = False
    logger.info("[IMP:7][_reset_state] Module state reset")


# endregion FUNC__reset_state


# ═══════════════════════════════════════════════════════════════════
# region FUNC__unit_enabled
## @purpose  Check if a given R-unit should be executed based on --units filter.
##           If _units_filter is empty (default), all units are enabled.
## @param units_filter  Comma-separated unit filter string (e.g., "R1,R3")
## @param unit_name     Unit name to check (e.g., "R1", "R3")
## @return  True if unit is enabled, False if filtered out
## @complexity O(n) where n = number of units in filter
def _unit_enabled(units_filter: str, unit_name: str) -> bool:
    """Check unit filter membership.

    If units_filter is empty or None, all units are enabled.
    The unit_name is compared against comma-separated tokens (whitespace-trimmed).
    """
    if not units_filter:
        return True
    tokens = [t.strip() for t in units_filter.split(",") if t.strip()]
    return unit_name in tokens


# endregion FUNC__unit_enabled


# ═══════════════════════════════════════════════════════════════════
# region FUNC_report_init / report_add / report_emit
## @purpose  JSON report helpers for --report-only mode. Mirrors shell:
##           report_init() → report_add() × N → report_emit() → exit


def report_init() -> None:
    """Initialize drift report — reset drift list."""
    global _drifts
    _drifts = []
    logger.info("[IMP:7][report_init] Initialized drift report")


def report_add(unit: str, status: str, detail: str) -> None:
    """Add a drift entry to the report.

    Args:
        unit: R-unit name (e.g., "R1", "R3")
        status: One of "ok", "mutated", "skipped", "warn", "fail", "converged", "awaiting_deploy"
        detail: Human-readable description of the drift
    """
    entry = {"unit": unit, "status": status, "detail": detail}
    _drifts.append(entry)
    logger.info("[IMP:8][report_add] %s | %s | %s", unit, status, detail)


def report_emit() -> str:
    """Emit JSON report as string and return it.

    Builds the full report object with node name, timestamp, exit code,
    status reason, and collected drifts.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if _has_errors:
        status_reason = "errors"
    elif _has_warnings:
        status_reason = "mutations_applied"
    else:
        status_reason = "converged"

    report = {
        "node": _node_name,
        "timestamp": ts,
        "exit_code": _exit_code,
        "status": status_reason,
        "drifts": _drifts,
    }
    report_json = json.dumps(report, indent=2)
    logger.info("[IMP:8][report_emit] Report: %s", report_json)
    return report_json


# endregion


# ═══════════════════════════════════════════════════════════════════
# region FUNC__set_exit
## @purpose  Update exit code and flags based on severity
def _set_exit(severity: int) -> None:
    """Set exit code and flags.

    Args:
        severity: 0=ok, 1=warning, 2=error
    """
    global _exit_code, _has_warnings, _has_errors
    if severity >= 2:
        _exit_code = 2
        _has_errors = True
    elif severity == 1:
        if _exit_code < 1:
            _exit_code = 1
        _has_warnings = True
    # severity 0 = no-op


# endregion FUNC__set_exit


# ═══════════════════════════════════════════════════════════════════
# region FUNC__run_subprocess
## @purpose  Safe subprocess runner with uniform error handling
def _run_subprocess(
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
        logger.warning("[IMP:8][_run_subprocess] Binary not found: %s", cmd[0])
        return subprocess.CompletedProcess(args=cmd, returncode=127, stdout="", stderr=f"{cmd[0]}: not found")
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:8][_run_subprocess] Timeout after %ds: %s", timeout, " ".join(cmd))
        return subprocess.CompletedProcess(args=cmd, returncode=124, stdout="", stderr="timeout")


# endregion FUNC__run_subprocess


# region FUNC__try_chmod
## @purpose  Attempt chmod with OSError handling, returns success bool
def _try_chmod(path: str, unit: str) -> bool:
    """Try to chmod a file, returning True on success. Handles OSError internally."""
    try:
        os.chmod(path, os.stat(path).st_mode | 0o110)  # ug+x
        return True
    except OSError as exc:
        logger.warning("[IMP:8][converge][%s] chmod failed for %s: %s", unit, path, exc)
        return False


# endregion FUNC__try_chmod


# ═══════════════════════════════════════════════════════════════════
# R1 — reconcile_perms
# ═══════════════════════════════════════════════════════════════════
# region FUNC_reconcile_perms
## @purpose  Reconcile executable bit on *.sh files outside core/lib/.
##           M1 defense-in-depth: if rsync delivered files with 644,
##           this restores ug+x. Gate test (test_gate_executable_bit.py)
##           covers the git-index layer; this is the runtime layer.
## @io       stdout/stderr: LDD logs [IMP:7-9], count of fixed files
##           side-effect: chmod ug+x on non-executable scripts
## @param core_dir    Path to the core/ directory
## @param dry_run     If True, only report planned mutations
## @param report_only If True, skip mutations entirely
## @return  Drift report entry dict: {"unit":"R1","status":"...","detail":"..."}
## @edge-cases
##   - 0 non-executable files → SKIP
##   - Symlink → not followed (-type f)
##   - File disappears between find and chmod (rsync race) → graceful skip
##   - Large file tree → single exec find piped to while-read chmod
def reconcile_perms(
    core_dir: str,
    dry_run: bool = False,
    report_only: bool = False,
) -> dict:
    """Reconcile executable bits on *.sh files outside core/lib/.

    Returns a drift entry dict with status: ok|skipped|mutated|fail.
    """
    unit = "R1"
    logger.info("[IMP:8][converge][%s] START: reconcile_perms — fixing executable bits outside core/lib/", unit)

    core_path = Path(core_dir)
    if not core_path.is_dir():
        logger.error("[IMP:10][converge][%s] FATAL: core_dir does not exist: %s", unit, core_dir)
        entry = {"unit": unit, "status": "fail", "detail": f"Core directory not found: {core_dir}"}
        report_add(unit, "fail", f"Core directory not found: {core_dir}")
        _set_exit(2)
        return entry

    # Find non-executable *.sh files outside lib/
    non_exec_files: list[Path] = []
    for sh_file in core_path.rglob("*.sh"):
        # Skip files under lib/
        if "lib" in sh_file.parts:
            continue
        if sh_file.is_file() and not os.access(str(sh_file), os.X_OK):
            non_exec_files.append(sh_file)

    fix_count = len(non_exec_files)

    if fix_count == 0:
        logger.info("[IMP:9][converge][%s] SKIP: All scripts already executable", unit)
        entry = {"unit": unit, "status": "skipped", "detail": "All scripts already executable"}
        report_add(unit, "skipped", "All scripts already executable")
        return entry

    logger.info("[IMP:9][converge][%s] Found %d non-executable script(s)", unit, fix_count)
    for f in non_exec_files:
        logger.info("[IMP:7][converge][%s]   %s", unit, f)

    # ── Dry-run / report-only: no mutation ──
    if dry_run or report_only:
        logger.info("[IMP:9][converge][%s] WOULD fix %d file(s) with chmod ug+x", unit, fix_count)
        entry = {"unit": unit, "status": "mutated", "detail": f"{fix_count} files would get ug+x"}
        report_add(unit, "mutated", f"{fix_count} files would get ug+x")
        _set_exit(1)
        return entry

    # ── Actual mutation ──
    fixed = 0
    for f in non_exec_files:
        if _try_chmod(str(f), unit):
            fixed += 1
            logger.info("[IMP:7][converge][%s] Fixed: %s", unit, f)

    if fixed == 0:
        logger.info("[IMP:9][converge][%s] SKIP: No files could be fixed", unit)
        entry = {"unit": unit, "status": "skipped", "detail": "No files could be fixed"}
        report_add(unit, "skipped", "No files could be fixed")
    else:
        logger.info("[IMP:9][converge][%s] DONE: Fixed %d file(s) — chmod ug+x applied", unit, fixed)
        entry = {"unit": unit, "status": "mutated", "detail": f"{fixed} files fixed with ug+x"}
        report_add(unit, "mutated", f"{fixed} files fixed with ug+x")
        _set_exit(1)

    return entry


# endregion FUNC_reconcile_perms


# ═══════════════════════════════════════════════════════════════════
# R2 — reconcile_audit_log
# ═══════════════════════════════════════════════════════════════════
# region FUNC_reconcile_audit_log
## @purpose  Ensure /var/log/platform/audit.log exists with correct
##           ownership (root:adm) and permissions (0664). Also ensures
##           ci-deploy is in adm group for write access.
## @io       stdout/stderr: LDD logs [IMP:7-10]
##           side-effect: mkdir, chmod, chown, usermod (via subprocess)
## @param core_dir    Path to core/ directory (unused in R2 but kept for API consistency)
## @param dry_run     If True, only report planned mutations
## @param report_only If True, skip mutations entirely
## @return  Drift report entry dict
## @edge-cases
##   - audit.log is a symlink → fail (symlink attack prevention)
##   - ci-deploy not in adm group → usermod -aG adm
##   - File already correct → SKIP
def reconcile_audit_log(
    core_dir: str,  # unused in R2, kept for API consistency
    dry_run: bool = False,
    report_only: bool = False,
) -> dict:
    """Reconcile audit.log and ci-deploy adm group membership.

    Returns a drift entry dict with status: ok|skipped|mutated|warn|fail.
    """
    unit = "R2"
    logger.info(
        "[IMP:8][converge][%s] START: reconcile_audit_log — ensuring %s 0664 root:adm",
        unit,
        AUDIT_LOG_FILE,
    )

    log_dir = Path(AUDIT_LOG_DIR)
    audit_log = Path(AUDIT_LOG_FILE)

    # ── Security check: reject symlink targets ──
    if log_dir.is_symlink():
        msg = f"Symlink detected: {AUDIT_LOG_DIR} — possible attack"
        logger.error("[IMP:10][converge][%s] FATAL: %s", unit, msg)
        report_add(unit, "fail", msg)
        _set_exit(2)
        return {"unit": unit, "status": "fail", "detail": msg}

    if audit_log.is_symlink():
        msg = f"Symlink detected: {AUDIT_LOG_FILE} — possible attack"
        logger.error("[IMP:10][converge][%s] FATAL: %s", unit, msg)
        report_add(unit, "fail", msg)
        _set_exit(2)
        return {"unit": unit, "status": "fail", "detail": msg}

    # ── Ensure ci-deploy is in adm group ──
    _reconcile_ci_deploy_group(unit, dry_run, report_only)

    # ── Ensure directory exists ──
    if not log_dir.is_dir():
        if dry_run or report_only:
            logger.info("[IMP:9][converge][%s] WOULD create: %s 0750 root:adm", unit, AUDIT_LOG_DIR)
            report_add(unit, "mutated", f"Directory {AUDIT_LOG_DIR} would be created")
            _set_exit(1)
        else:
            logger.info("[IMP:8][converge][%s] Creating %s 0750 root:adm", unit, AUDIT_LOG_DIR)
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
                _run_subprocess(["chmod", "0750", AUDIT_LOG_DIR], timeout=FILE_OP_TIMEOUT)
                _run_subprocess(["chown", "root:adm", AUDIT_LOG_DIR], timeout=FILE_OP_TIMEOUT)
                logger.info("[IMP:9][converge][%s] DONE: %s created 0750 root:adm", unit, AUDIT_LOG_DIR)
                report_add(unit, "mutated", f"Directory {AUDIT_LOG_DIR} created")
                _set_exit(1)
            except OSError as exc:
                logger.error("[IMP:10][converge][%s] mkdir failed for %s: %s", unit, AUDIT_LOG_DIR, exc)
                report_add(unit, "fail", f"mkdir failed for {AUDIT_LOG_DIR}: {exc}")
                _set_exit(2)
                return {"unit": unit, "status": "fail", "detail": f"mkdir failed: {exc}"}

    # ── Ensure audit.log exists ──
    if not audit_log.is_file():
        if dry_run or report_only:
            logger.info("[IMP:9][converge][%s] WOULD create: %s 0664 root:adm", unit, AUDIT_LOG_FILE)
            report_add(unit, "mutated", f"File {AUDIT_LOG_FILE} would be created")
            _set_exit(1)
        else:
            logger.info("[IMP:8][converge][%s] Creating %s 0664 root:adm", unit, AUDIT_LOG_FILE)
            try:
                audit_log.touch(exist_ok=True)
                _run_subprocess(["chmod", "0664", AUDIT_LOG_FILE], timeout=FILE_OP_TIMEOUT)
                _run_subprocess(["chown", "root:adm", AUDIT_LOG_FILE], timeout=FILE_OP_TIMEOUT)
                logger.info("[IMP:9][converge][%s] DONE: %s created 0664 root:adm", unit, AUDIT_LOG_FILE)
                report_add(unit, "mutated", f"File {AUDIT_LOG_FILE} created")
                _set_exit(1)
            except OSError as exc:
                logger.error("[IMP:10][converge][%s] touch failed for %s: %s", unit, AUDIT_LOG_FILE, exc)
                report_add(unit, "fail", f"touch failed for {AUDIT_LOG_FILE}: {exc}")
                _set_exit(2)
                return {"unit": unit, "status": "fail", "detail": f"touch failed: {exc}"}
    else:
        # File exists — verify permissions via stat subprocess
        mode_r = _run_subprocess(
            ["stat", "-c", "%a", AUDIT_LOG_FILE],
            timeout=FILE_OP_TIMEOUT,
        )
        owner_r = _run_subprocess(
            ["stat", "-c", "%u:%g", AUDIT_LOG_FILE],
            timeout=FILE_OP_TIMEOUT,
        )
        current_mode = mode_r.stdout.strip() if mode_r.returncode == 0 else "000"
        current_owner = owner_r.stdout.strip() if owner_r.returncode == 0 else "0:0"

        if current_mode != "664" or current_owner != "0:4":
            if dry_run or report_only:
                logger.info(
                    "[IMP:9][converge][%s] WOULD fix: %s mode=%s owner=%s",
                    unit,
                    AUDIT_LOG_FILE,
                    current_mode,
                    current_owner,
                )
                report_add(unit, "mutated", "audit.log permissions would be fixed")
                _set_exit(1)
            else:
                logger.info(
                    "[IMP:8][converge][%s] Fixing permissions: %s mode=%s owner=%s",
                    unit,
                    AUDIT_LOG_FILE,
                    current_mode,
                    current_owner,
                )
                _run_subprocess(["chmod", "0664", AUDIT_LOG_FILE], timeout=FILE_OP_TIMEOUT)
                _run_subprocess(["chown", "root:adm", AUDIT_LOG_FILE], timeout=FILE_OP_TIMEOUT)
                logger.info("[IMP:9][converge][%s] DONE: %s permissions corrected", unit, AUDIT_LOG_FILE)
                report_add(unit, "mutated", "audit.log permissions corrected to 0664 root:adm")
                _set_exit(1)
        else:
            logger.info("[IMP:9][converge][%s] SKIP: %s already 0664 root:adm (converged)", unit, AUDIT_LOG_FILE)
            report_add(unit, "converged", "audit.log permissions correct")

    logger.info("[IMP:9][converge][%s] DONE: audit log reconciled", unit)
    return {"unit": unit, "status": "converged" if not _has_errors else "warn", "detail": "audit log reconciled"}


# endregion FUNC_reconcile_audit_log


# region FUNC__reconcile_ci_deploy_group
## @purpose  Internal helper: ensure ci-deploy user is in adm group
def _reconcile_ci_deploy_group(unit: str, dry_run: bool, report_only: bool) -> None:
    """Check and fix ci-deploy adm group membership.

    If ci-deploy user does not exist yet (pre-bootstrap), logs INFO and skips.
    """
    # Check if ci-deploy user exists
    id_r = _run_subprocess(["id", "-nG", "ci-deploy"], timeout=FILE_OP_TIMEOUT)
    if id_r.returncode != 0:
        logger.info("[IMP:8][converge][%s] INFO: ci-deploy user does not exist yet — skipping group check", unit)
        return

    groups = id_r.stdout.strip().split()
    if "adm" in groups:
        logger.info("[IMP:7][converge][%s] OK: ci-deploy is already in adm group", unit)
        return

    # ci-deploy exists but not in adm group
    if dry_run or report_only:
        logger.info("[IMP:9][converge][%s] WOULD fix: ci-deploy not in adm group — usermod -aG adm", unit)
        report_add(unit, "mutated", "ci-deploy would be added to adm group")
        _set_exit(1)
    else:
        logger.info("[IMP:9][converge][%s] Adding ci-deploy to adm group", unit)
        usermod_r = _run_subprocess(["usermod", "-aG", "adm", "ci-deploy"], timeout=FILE_OP_TIMEOUT)
        if usermod_r.returncode == 0:
            logger.info("[IMP:9][converge][%s] DONE: ci-deploy added to adm group", unit)
            report_add(unit, "mutated", "ci-deploy added to adm group")
            _set_exit(1)
        else:
            logger.warning(
                "[IMP:8][converge][%s] WARN: usermod failed — ci-deploy may not have write access to audit.log: %s",
                unit,
                usermod_r.stderr.strip(),
            )
            report_add(unit, "warn", "usermod failed for ci-deploy → adm group")


# endregion FUNC__reconcile_ci_deploy_group


# ═══════════════════════════════════════════════════════════════════
# R3 — reconcile_projects
# ═══════════════════════════════════════════════════════════════════
# region FUNC_reconcile_projects
## @purpose  Read node.yaml#projects and ensure per-project directories,
##           ownership ci-deploy:ci-deploy, stub ai-platform.yaml, and
##           .env.platform via gen-env-platform.sh (if-missing).
## @io       stdout/stderr: LDD logs [IMP:7-10]
##           side-effect: mkdir -p, chown, touch, stub creation
## @param node_yaml_path  Path to node.yaml
## @param dry_run         If True, only report planned mutations
## @param report_only     If True, skip mutations entirely
## @return  Drift report entry dict
## @edge-cases
##   - projects: [] or missing → SKIP
##   - Invalid project name (/ or ..) → fail
##   - Existing non-stub ai-platform.yaml → NOT touched
##   - Existing stub → NOT overwritten (no-op)
##   - Existing .env.platform → NOT touched (if-missing)
def reconcile_projects(
    node_yaml_path: str,
    dry_run: bool = False,
    report_only: bool = False,
) -> dict:
    """Reconcile project directories and stubs from node.yaml.

    Returns a drift entry dict with status: ok|skipped|mutated|fail.
    """
    unit = "R3"
    logger.info("[IMP:8][converge][%s] START: reconcile_projects — ensuring project directories and stubs", unit)

    node_yaml = Path(node_yaml_path)
    if not node_yaml.is_file():
        msg = f"node.yaml not found: {node_yaml_path}"
        logger.error("[IMP:10][converge][%s] FATAL: %s", unit, msg)
        report_add(unit, "fail", msg)
        _set_exit(2)
        return {"unit": unit, "status": "fail", "detail": msg}

    # ── Parse projects from node.yaml ──
    projects = _parse_projects_yaml(str(node_yaml))

    if not projects:
        logger.info("[IMP:9][converge][%s] SKIP: No projects defined in node.yaml or projects: []", unit)
        report_add(unit, "skipped", "No projects defined in node.yaml")
        return {"unit": unit, "status": "skipped", "detail": "No projects defined in node.yaml"}

    projects_dir = Path(PROJECTS_BASE)
    mutated = 0
    errors = 0

    for proj in projects:
        proj_name = proj.get("name", "")
        if not proj_name:
            continue

        logger.info("[IMP:7][converge][%s] Processing project: %s", unit, proj_name)

        # Validate project name
        if not _validate_project_name(proj_name):
            errors += 1
            report_add(unit, "fail", f"Invalid project name: {proj_name}")
            _set_exit(2)
            continue

        proj_dir = projects_dir / proj_name

        # ── mkdir -p ──
        if not proj_dir.is_dir():
            if dry_run or report_only:
                logger.info("[IMP:9][converge][%s] WOULD create directory: %s", unit, proj_dir)
                mutated += 1
            else:
                logger.info("[IMP:8][converge][%s] Creating directory: %s", unit, proj_dir)
                try:
                    proj_dir.mkdir(parents=True, exist_ok=True)
                    logger.info("[IMP:9][converge][%s] DONE: %s created", unit, proj_dir)
                    mutated += 1
                except OSError as exc:
                    logger.error("[IMP:10][converge][%s] FAIL: mkdir -p %s failed: %s", unit, proj_dir, exc)
                    errors += 1
                    _set_exit(2)
                    continue

        # ── chown ci-deploy:ci-deploy (directory only, not recursive) ──
        if not dry_run and not report_only and proj_dir.is_dir():
            _run_subprocess(["chown", "ci-deploy:ci-deploy", str(proj_dir)], timeout=FILE_OP_TIMEOUT)

        # ── stub ai-platform.yaml (if-missing) ──
        stub_file = proj_dir / "ai-platform.yaml"
        if not stub_file.is_file():
            if dry_run or report_only:
                logger.info("[IMP:9][converge][%s] WOULD create stub: %s", unit, stub_file)
                mutated += 1
            else:
                logger.info("[IMP:8][converge][%s] Creating stub: %s", unit, stub_file)
                try:
                    stub_content = (
                        f"# GENERATED-STUB by converge — overwritten by CI deliver\n"
                        f"# This is a placeholder created during node convergence.\n"
                        f"# CI deliver will replace it with the actual project configuration.\n"
                        f"project: {proj_name}\n"
                        f"service: {proj_name}\n"
                    )
                    stub_file.write_text(stub_content)
                    _run_subprocess(["chown", "ci-deploy:ci-deploy", str(stub_file)], timeout=FILE_OP_TIMEOUT)
                    logger.info("[IMP:9][converge][%s] DONE: stub created for %s", unit, proj_name)
                    mutated += 1
                except OSError as exc:
                    logger.error("[IMP:10][converge][%s] FAIL: stub creation failed for %s: %s", unit, proj_name, exc)
                    errors += 1
                    _set_exit(2)
                    continue
        else:
            if _is_stub(str(stub_file)):
                logger.info("[IMP:7][converge][%s] STUB: %s is a GENERATED-STUB (awaiting deploy)", unit, stub_file)
                report_add(unit, "awaiting_deploy", f"Project {proj_name}: stub present, awaiting CI deploy")
            else:
                logger.info("[IMP:7][converge][%s] SKIP: %s already exists (real config — deployed)", unit, stub_file)
                report_add(unit, "converged", f"Project {proj_name}: deployed")

        # ── .env.platform (if-missing) ──
        _reconcile_env_platform(proj_name, str(proj_dir), unit, dry_run, report_only)

    # Final report
    if mutated > 0:
        report_add(unit, "mutated", f"{mutated} project item(s) created/fixed")
        _set_exit(1)
    elif errors > 0:
        report_add(unit, "fail", f"{errors} project(s) had errors")
    else:
        report_add(unit, "converged", "All project directories and stubs present")

    logger.info("[IMP:9][converge][%s] DONE: projects reconciled (mutated=%d errors=%d)", unit, mutated, errors)
    return {
        "unit": unit,
        "status": "converged" if not errors else "fail",
        "detail": f"mutated={mutated} errors={errors}",
    }


# endregion FUNC_reconcile_projects


# region FUNC__parse_projects_yaml
## @purpose  Parse projects list from node.yaml
def _parse_projects_yaml(node_yaml_path: str) -> list[dict]:
    """Parse projects from node.yaml.

    Supports both dict entries (with name/domain keys) and string entries.
    Returns empty list on parse error or missing section.
    """
    try:
        from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError, ConfigValidationError
        from core.internal.shared.node_yaml import NodeYaml

        projects_raw = NodeYaml(node_yaml_path).get_list("projects")
        out: list[dict] = []
        for p in projects_raw:
            if isinstance(p, dict):
                out.append({"name": p.get("name", ""), "domain": p.get("domain", "")})
            elif isinstance(p, str):
                out.append({"name": p, "domain": ""})
        return out
    except (ConfigNotFoundError, ConfigParseError, ConfigValidationError) as exc:
        logger.warning("[IMP:8][_parse_projects_yaml] Failed to parse projects from %s: %s", node_yaml_path, exc)
        return []


# endregion FUNC__parse_projects_yaml


# region FUNC__validate_project_name
## @purpose  Validate project name — no /, .., only [a-zA-Z0-9_-]
def _validate_project_name(name: str) -> bool:
    """Validate that a project name contains no path separators and only safe chars.

    Returns True if valid, False otherwise.
    """
    if not name:
        logger.warning("[IMP:10][_validate_project_name] FAIL: Empty project name")
        return False
    if "/" in name or ".." in name:
        logger.warning("[IMP:10][_validate_project_name] FAIL: Invalid project name '%s' — contains / or ..", name)
        return False
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        logger.warning(
            "[IMP:10][_validate_project_name] FAIL: Invalid project name '%s' — only [a-zA-Z0-9_-] allowed", name
        )
        return False
    return True


# endregion FUNC__validate_project_name


# region FUNC__create_empty_env_file
## @purpose  Helper: create an empty .env.platform file with correct permissions and ownership.
##           Used as fallback when generate_env_platform() is unavailable or fails.
## @param env_file  Path to .env.platform file
## @param unit      R-unit name for logging
## @return  True if the file was created successfully, False on error
def _create_empty_env_file(env_file: Path, unit: str) -> bool:
    """Create an empty .env.platform file with 0640 ci-deploy:ci-deploy.

    Args:
        env_file: Path to the .env.platform file to create.
        unit: R-unit name for logging.

    Returns:
        True on success, False on OSError.
    """
    try:
        env_file.write_text("")
        _run_subprocess(["chmod", "0640", str(env_file)], timeout=FILE_OP_TIMEOUT)
        _run_subprocess(["chown", "ci-deploy:ci-deploy", str(env_file)], timeout=FILE_OP_TIMEOUT)
        logger.info("[IMP:9][converge][%s] DONE: %s created (fallback: empty)", unit, env_file)
        return True
    except OSError as exc:
        logger.error("[IMP:10][converge][%s] FAIL: .env.platform creation failed: %s", unit, exc)
        _set_exit(2)
        return False


# endregion FUNC__create_empty_env_file


# region FUNC__reconcile_env_platform
## @purpose  Ensure .env.platform exists in project directory (if-missing).
##           T9b: replaced subprocess call to gen-env-platform.sh with direct
##           Python import of generate_env_platform() from gen_env_platform module.
## @param proj_name    Project name (used for DSN substitution and logging)
## @param proj_dir     Path to the project directory
## @param unit         R-unit name for logging (typically "R3")
## @param dry_run      If True, only log planned mutations
## @param report_only  If True, skip mutations entirely
def _reconcile_env_platform(
    proj_name: str,
    proj_dir: str,
    unit: str,
    dry_run: bool,
    report_only: bool,
) -> None:
    """Create .env.platform via generate_env_platform() or fallback to empty file.

    Uses direct Python import (T9b) instead of shell subprocess. Falls back
    to empty file if platform-env.yaml is missing or generation fails.

    Reports via report_add and _set_exit; does not modify caller's local mutated counter —
    the approximate count from report entries is sufficient for the exit code contract.
    """

    env_file = Path(proj_dir) / ".env.platform"
    if env_file.is_file():
        logger.info("[IMP:7][converge][%s] SKIP: %s already exists (if-missing policy)", unit, env_file)
        return

    if dry_run or report_only:
        logger.info("[IMP:9][converge][%s] WOULD create: %s via generate_env_platform()", unit, env_file)
        report_add(unit, "mutated", f".env.platform would be created for {proj_name}")
        _set_exit(1)
        return

    logger.info("[IMP:8][converge][%s] Creating .env.platform via generate_env_platform() for %s", unit, proj_name)

    # Determine platform-env.yaml path relative to _core_dir
    platform_env_path = str(Path(_core_dir).parent / "platform-env.yaml")
    domain = os.environ.get("PLATFORM_DOMAIN", "ai-platform.local")

    try:
        # Lazy import to match codebase pattern (same as core.internal.shared imports)
        from core.internal.scaffold.gen_env_platform import generate_env_platform

        lines = generate_env_platform(platform_env_path, domain=domain, project_name=proj_name)
        env_file.write_text("\n".join(lines) + "\n")
        _run_subprocess(["chmod", "0640", str(env_file)], timeout=FILE_OP_TIMEOUT)
        _run_subprocess(["chown", "ci-deploy:ci-deploy", str(env_file)], timeout=FILE_OP_TIMEOUT)
        logger.info("[IMP:9][converge][%s] DONE: %s generated via generate_env_platform()", unit, env_file)
        report_add(unit, "mutated", f".env.platform created for {proj_name}")
        _set_exit(1)
    except FileNotFoundError:
        logger.warning(
            "[IMP:9][converge][%s] WARN: platform-env.yaml not found at %s — creating empty .env.platform",
            unit,
            platform_env_path,
        )
        _create_empty_env_file(env_file, unit)
    except (ImportError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        logger.warning(
            "[IMP:9][converge][%s] WARN: generate_env_platform() failed for %s — creating empty .env.platform: %s",
            unit,
            proj_name,
            exc,
        )
        _create_empty_env_file(env_file, unit)


# endregion FUNC__reconcile_env_platform


# ═══════════════════════════════════════════════════════════════════
# region FUNC__is_stub
## @purpose  Check if ai-platform.yaml is a GENERATED-STUB (not real config)
## @param ai_platform_yaml_path  Path to ai-platform.yaml
## @return  True if the file is a stub (first line contains GENERATED-STUB),
##          False if file is missing or has real config.
def _is_stub(ai_platform_yaml_path: str) -> bool:
    """Check whether ai-platform.yaml is a GENERATED-STUB.

    Reads the first line of the file and checks for the GENERATED-STUB marker.
    A missing file or a file without the marker is NOT a stub.
    """
    path = Path(ai_platform_yaml_path)
    if not path.is_file():
        return False
    try:
        first_line = path.read_text().splitlines()[0] if path.stat().st_size > 0 else ""
        return "GENERATED-STUB" in first_line
    except (OSError, IndexError):
        return False


# endregion FUNC__is_stub


# ═══════════════════════════════════════════════════════════════════
# R4 — reconcile_networks
# ═══════════════════════════════════════════════════════════════════
# region FUNC_reconcile_networks
## @purpose  Ensure proxy-net Docker network exists (runtime fallback).
##           For each running project container, verify proxy-net connectivity.
##           Does NOT auto-connect — that's the compose project's responsibility.
## @io       stdout/stderr: LDD logs [IMP:7-9]
##           side-effect: docker network create (if missing)
## @param node_yaml_path  Path to node.yaml for project list
## @param dry_run         If True, only report planned mutations
## @param report_only     If True, skip mutations entirely
## @return  Drift report entry dict
## @edge-cases
##   - Docker daemon unavailable → fail unit, continue others
##   - proxy-net exists with wrong driver → WARN, don't recreate
##   - Concurrent docker network create → handled via inspect-after-create pattern
def reconcile_networks(
    node_yaml_path: str,
    dry_run: bool = False,
    report_only: bool = False,
) -> dict:
    """Reconcile Docker proxy-net and project container connectivity.

    Returns a drift entry dict with status: ok|skipped|mutated|warn|fail.
    """
    unit = "R4"
    logger.info("[IMP:8][converge][%s] START: reconcile_networks — ensuring proxy-net exists", unit)

    # ── Check docker daemon ──
    docker_info_r = _run_subprocess(["docker", "info"], timeout=DOCKER_TIMEOUT)
    if docker_info_r.returncode != 0:
        msg = "Docker daemon not available — skipping network reconciliation"
        logger.error("[IMP:10][converge][%s] FAIL: %s", unit, msg)
        report_add(unit, "fail", msg)
        _set_exit(2)
        return {"unit": unit, "status": "fail", "detail": msg}

    # ── proxy-net: ensure exists ──
    net_inspect_r = _run_subprocess(
        ["docker", "network", "inspect", PROXY_NET],
        timeout=DOCKER_TIMEOUT,
    )

    if net_inspect_r.returncode != 0:
        # Network does not exist — create it
        if dry_run or report_only:
            logger.info("[IMP:9][converge][%s] WOULD create: proxy-net (bridge)", unit)
            report_add(unit, "mutated", "proxy-net would be created")
            _set_exit(1)
        else:
            logger.info("[IMP:8][converge][%s] Creating proxy-net (runtime fallback)", unit)
            create_r = _run_subprocess(
                ["docker", "network", "create", "--driver", "bridge", PROXY_NET],
                timeout=DOCKER_TIMEOUT,
            )
            if create_r.returncode == 0:
                logger.info("[IMP:9][converge][%s] DONE: proxy-net created", unit)
                report_add(unit, "mutated", "proxy-net created")
                _set_exit(1)
            else:
                logger.error(
                    "[IMP:10][converge][%s] FAIL: docker network create proxy-net failed: %s",
                    unit,
                    create_r.stderr.strip(),
                )
                report_add(unit, "fail", "proxy-net creation failed")
                _set_exit(2)
                return {"unit": unit, "status": "fail", "detail": "proxy-net creation failed"}
    else:
        # Network exists — check driver
        try:
            net_info = json.loads(net_inspect_r.stdout)
            current_driver = net_info[0].get("Driver", "unknown") if net_info else "unknown"
        except (json.JSONDecodeError, IndexError, KeyError):
            current_driver = "unknown"

        if current_driver != "bridge":
            logger.warning(
                "[IMP:9][converge][%s] WARN: proxy-net exists but driver=%s (expected=bridge)",
                unit,
                current_driver,
            )
            report_add(unit, "warn", f"proxy-net driver={current_driver} (expected=bridge)")
        else:
            logger.info("[IMP:9][converge][%s] SKIP: proxy-net already exists (driver=bridge, converged)", unit)

    # ── Check project containers for proxy-net connectivity ──
    _check_proxy_connectivity(node_yaml_path, unit)

    logger.info("[IMP:9][converge][%s] DONE: networks reconciled", unit)
    return {"unit": unit, "status": "converged", "detail": "networks reconciled"}


# endregion FUNC_reconcile_networks


# region FUNC__check_proxy_connectivity
## @purpose  Check each project's running containers for proxy-net connectivity
def _check_proxy_connectivity(node_yaml_path: str, unit: str) -> None:
    """Check each project's running containers for proxy-net membership.

    For each project from node.yaml, find running containers and verify they
    are connected to proxy-net. Logs WARN for containers not connected.
    """
    projects = _parse_projects_yaml(node_yaml_path)
    if not projects:
        logger.info("[IMP:9][converge][%s] SKIP: No projects to check for proxy-net connectivity", unit)
        return

    for proj in projects:
        pname = proj.get("name", "")
        if not pname:
            continue

        # Find running containers for this project
        ps_r = _run_subprocess(
            [
                "docker",
                "ps",
                "--filter",
                f"label=com.docker.compose.project={pname}",
                "--format",
                "{{.Names}}",
            ],
            timeout=DOCKER_TIMEOUT,
        )
        containers = [c.strip() for c in ps_r.stdout.splitlines() if c.strip()]

        if not containers:
            logger.info("[IMP:7][converge][%s] INFO: No running containers for project %s", unit, pname)
            continue

        for cname in containers:
            inspect_r = _run_subprocess(
                [
                    "docker",
                    "inspect",
                    cname,
                    "--format",
                    "{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}",
                ],
                timeout=DOCKER_TIMEOUT,
            )
            networks = inspect_r.stdout.strip() if inspect_r.returncode == 0 else ""
            if "proxy-net" not in networks:
                logger.warning(
                    "[IMP:9][converge][%s] WARN: Container %s (project %s) NOT connected to proxy-net",
                    unit,
                    cname,
                    pname,
                )
                report_add(unit, "warn", f"Container {cname} not connected to proxy-net")
            else:
                logger.info("[IMP:7][converge][%s] OK: Container %s connected to proxy-net", unit, cname)


# endregion FUNC__check_proxy_connectivity


# ═══════════════════════════════════════════════════════════════════
# R5 — detect_hosts_drift
# ═══════════════════════════════════════════════════════════════════
# region FUNC_detect_hosts_drift
## @purpose  Read-only detection of stale /etc/hosts entries for project names
##           from node.yaml. No mutation — only WARN.
## @io       stdout/stderr: WARN logs [IMP:9], JSON entries in report
## @param node_yaml_path  Path to node.yaml
## @return  Drift report entry dict
## @edge-cases
##   - /etc/hosts unreadable → WARN, not fail
##   - Project name matches substring of legitimate entry → word-boundary grep
def detect_hosts_drift(node_yaml_path: str) -> dict:
    """Detect stale /etc/hosts entries for project names from node.yaml.

    Read-only — no mutations. Returns drift report entry.
    """
    unit = "R5"
    logger.info("[IMP:8][converge][%s] START: detect_hosts_drift — checking /etc/hosts for stale entries", unit)

    hosts_file = Path(HOSTS_FILE)
    if not hosts_file.is_file() or not os.access(str(hosts_file), os.R_OK):
        logger.warning("[IMP:8][converge][%s] WARN: %s not readable — skipping drift detection", unit, HOSTS_FILE)
        report_add(unit, "warn", f"Cannot read {HOSTS_FILE}")
        return {"unit": unit, "status": "warn", "detail": f"Cannot read {HOSTS_FILE}"}

    # Extract project names from node.yaml
    projects = _parse_projects_yaml(node_yaml_path)
    if not projects:
        logger.info("[IMP:9][converge][%s] SKIP: No projects defined in node.yaml", unit)
        report_add(unit, "skipped", "No projects to check")
        return {"unit": unit, "status": "skipped", "detail": "No projects to check"}

    # Read /etc/hosts content
    try:
        hosts_content = hosts_file.read_text()
    except OSError as exc:
        logger.warning("[IMP:8][converge][%s] WARN: Cannot read %s: %s", unit, HOSTS_FILE, exc)
        report_add(unit, "warn", f"Cannot read {HOSTS_FILE}: {exc}")
        return {"unit": unit, "status": "warn", "detail": f"Cannot read {HOSTS_FILE}: {exc}"}

    drift_found = 0
    for proj in projects:
        pname = proj.get("name", "")
        if not pname:
            continue

        # Word-boundary match to avoid substring false positives
        pattern = re.compile(r"\b" + re.escape(pname) + r"\b")
        matches = pattern.findall(hosts_content)
        if matches:
            logger.warning(
                "[IMP:9][converge][%s] WARN: Stale /etc/hosts entry found for project '%s': %s",
                unit,
                pname,
                matches,
            )
            report_add(unit, "warn", f"Stale /etc/hosts entry for {pname}")
            drift_found += 1
            _set_exit(1)

    if drift_found == 0:
        logger.info("[IMP:9][converge][%s] SKIP: No stale /etc/hosts entries (converged)", unit)
        report_add(unit, "converged", "No stale /etc/hosts entries")
    else:
        logger.info("[IMP:9][converge][%s] DONE: %d drift(s) detected (read-only — no mutation)", unit, drift_found)

    return {
        "unit": unit,
        "status": "warn" if drift_found > 0 else "converged",
        "detail": f"{drift_found} stale entries found" if drift_found > 0 else "No stale entries",
    }


# endregion FUNC_detect_hosts_drift


# ═══════════════════════════════════════════════════════════════════
# R6 — verify_vhosts
# ═══════════════════════════════════════════════════════════════════
# region FUNC_verify_vhosts
## @purpose  Read-only verification of nginx vhost config integrity.
##           Checks: (1) for each project with domain, <domain>.conf exists;
##           (2) GENERATED marker present; (3) orphan vhosts without project
##           → WARN; (4) docker exec nginx nginx -t passes.
## @io       stdout/stderr: LDD logs [IMP:7-10], drift report entries
## @param node_yaml_path  Path to node.yaml
## @param converge_node   Node name for overlay resolution
## @param core_dir        Path to core/ directory
## @param dry_run         If True, only report planned mutations
## @param report_only     If True, skip mutations entirely
## @return  Drift report entry dict
## @edge-cases
##   - nginx container not running → WARN nginx -t, other checks proceed
##   - Project without domain → SKIP
##   - Orphan vhost (no project match) → WARN
##   - Legacy conf without GENERATED marker → WARN
def verify_vhosts(
    node_yaml_path: str,
    converge_node: str,
    core_dir: str,
    dry_run: bool = False,
    report_only: bool = False,
    overlay_base: str | None = None,
) -> dict:
    """Verify nginx vhost config integrity.

    Returns a drift entry dict with status: ok|skipped|warn|fail.
    """
    unit = "R6"
    logger.info("[IMP:8][converge][%s] START: verify_vhosts — checking nginx vhost integrity", unit)

    node_yaml = Path(node_yaml_path)
    if not node_yaml.is_file():
        msg = f"node.yaml not found: {node_yaml_path}"
        logger.error("[IMP:10][converge][%s] FATAL: %s", unit, msg)
        report_add(unit, "fail", msg)
        _set_exit(2)
        return {"unit": unit, "status": "fail", "detail": msg}

    # ── Get projects with domains from node.yaml ──
    projects = _parse_projects_yaml(str(node_yaml))
    projects_with_domains = [p for p in projects if p.get("name") and p.get("domain")]

    # ── Determine nginx overlay directory ──
    overlay_dir = _resolve_nginx_overlay(
        str(node_yaml), converge_node, base_dir=overlay_base if overlay_base else "/opt"
    )

    if not overlay_dir or not Path(overlay_dir).is_dir():
        logger.warning(
            "[IMP:8][converge][%s] WARN: nginx overlay directory not found at expected path — vhost verification limited",
            unit,
        )
        report_add(unit, "warn", "nginx overlay directory not found")

    vhost_errors = 0
    expected_domains: list[str] = []

    for proj in projects_with_domains:
        pname = proj.get("name", "")
        domain = proj.get("domain", "")
        if not pname or not domain:
            continue

        expected_domains.append(domain)

        # Check: vhost file exists
        if overlay_dir:
            vhost_file = Path(overlay_dir) / f"{domain}.conf"
            if vhost_file.is_file():
                # Check GENERATED marker
                first_line = vhost_file.read_text().splitlines()[0] if vhost_file.stat().st_size > 0 else ""
                if "GENERATED by add-vhost.sh" in first_line:
                    logger.info("[IMP:7][converge][%s] OK: %s.conf has GENERATED marker", unit, domain)
                else:
                    logger.warning(
                        "[IMP:9][converge][%s] WARN: %s.conf exists but missing GENERATED marker — legacy config",
                        unit,
                        domain,
                    )
                    report_add(unit, "warn", f"{domain}.conf: missing GENERATED marker")
            else:
                logger.error("[IMP:9][converge][%s] FAIL: Vhost file not found: %s/%s.conf", unit, overlay_dir, domain)
                report_add(unit, "fail", f"{domain}.conf not found")
                vhost_errors += 1
                _set_exit(2)
        else:
            logger.error(
                "[IMP:9][converge][%s] FAIL: Vhost overlay directory not resolved — cannot verify %s.conf", unit, domain
            )
            report_add(unit, "fail", f"overlay dir not resolved for {domain}.conf")
            vhost_errors += 1
            _set_exit(2)

    # ── Check for orphan vhosts ──
    if overlay_dir:
        _detect_orphan_vhosts(overlay_dir, expected_domains, unit)

    # ── nginx -t validation ──
    _run_nginx_test(unit)

    if not projects_with_domains:
        logger.info("[IMP:9][converge][%s] DONE: no domains to verify", unit)
        status = "skipped"
    elif vhost_errors == 0:
        logger.info("[IMP:9][converge][%s] DONE: %d vhost(s) verified — all OK", unit, len(projects_with_domains))
        status = "converged"
    else:
        logger.info(
            "[IMP:9][converge][%s] DONE: %d vhost(s), %d error(s)",
            unit,
            len(projects_with_domains),
            vhost_errors,
        )
        status = "fail"

    return {"unit": unit, "status": status, "detail": f"{len(projects_with_domains)} vhost(s), {vhost_errors} error(s)"}


# endregion FUNC_verify_vhosts


# region FUNC__resolve_nginx_overlay
## @purpose  Determine the nginx overlay directory for a given node
def _resolve_nginx_overlay(node_yaml_path: str, converge_node: str, base_dir: str = "/opt") -> str | None:
    """Resolve nginx vhost directory from node.yaml context or node fallback."""
    try:
        from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError
        from core.internal.shared.node_yaml import NodeYaml

        context_name = NodeYaml(node_yaml_path).get_context()
    except (ConfigNotFoundError, ConfigParseError):
        context_name = ""

    if context_name:
        candidate = f"{base_dir}/{context_name}/platform/modules/nginx"
        if Path(candidate).is_dir():
            return candidate
        logger.info("[IMP:7][_resolve_nginx_overlay] Context overlay not found: %s", candidate)

    # Fallback: node-configs path
    candidate_node = f"{base_dir}/{converge_node}/overlays/nginx"
    if Path(candidate_node).is_dir():
        return candidate_node

    # Fallback: node-configs standard path
    candidate_nc = f"{base_dir}/node-configs/{converge_node}/overlays/nginx"
    if Path(candidate_nc).is_dir():
        return candidate_nc

    logger.warning("[IMP:8][_resolve_nginx_overlay] No nginx overlay found for node=%s", converge_node)
    return None


# endregion FUNC__resolve_nginx_overlay


# region FUNC__detect_orphan_vhosts
## @purpose  Detect orphan vhost files with no matching project domain
def _detect_orphan_vhosts(overlay_dir: str, expected_domains: list[str], unit: str) -> None:
    """Detect orphan vhost files — .conf files that don't match any project domain."""
    overlay_path = Path(overlay_dir)
    if not overlay_path.is_dir():
        return

    for vhost_file in overlay_path.glob("*.conf"):
        fname = vhost_file.name
        # Skip non-vhost files
        if fname in ("nginx.conf",):
            continue

        domain_name = fname.removesuffix(".conf")
        if domain_name not in expected_domains:
            logger.warning(
                "[IMP:9][converge][%s] WARN: Orphan vhost detected — %s has no matching project in node.yaml",
                unit,
                fname,
            )
            report_add(unit, "warn", f"Orphan vhost: {fname}")


# endregion FUNC__detect_orphan_vhosts


# region FUNC__run_nginx_test
## @purpose  Run nginx -t via docker exec
def _run_nginx_test(unit: str) -> None:
    """Run nginx -t syntax check via docker exec.

    If nginx container is not running, logs WARN and skips.
    """
    # First check if nginx container is running
    ps_r = _run_subprocess(
        ["docker", "ps", "--format", "{{.Names}}"],
        timeout=DOCKER_TIMEOUT,
    )
    running = [n.strip() for n in ps_r.stdout.splitlines() if n.strip()]

    if "nginx" not in running:
        logger.warning(
            "[IMP:8][converge][%s] WARN: nginx container not running — skipping nginx -t",
            unit,
        )
        report_add(unit, "warn", "nginx container not running — nginx -t skipped")
        return

    logger.info("[IMP:8][converge][%s] Running nginx -t...", unit)
    nginx_t_r = _run_subprocess(
        ["docker", "exec", "nginx", "nginx", "-t"],
        timeout=DOCKER_TIMEOUT,
    )

    if nginx_t_r.returncode == 0:
        logger.info("[IMP:9][converge][%s] OK: nginx -t passed", unit)
    else:
        logger.error(
            "[IMP:10][converge][%s] FAIL: nginx -t failed — nginx reload blocked: %s",
            unit,
            nginx_t_r.stderr.strip(),
        )
        report_add(unit, "fail", "nginx -t failed — reload blocked")
        _set_exit(2)


# endregion FUNC__run_nginx_test


# ═══════════════════════════════════════════════════════════════════
# R7 — reconcile_volumes
# ═══════════════════════════════════════════════════════════════════
# region FUNC__parse_node_modules_yaml
## @purpose  Parse enabled modules from node.yaml for docker module detection.
##           Returns list of dicts with name, enabled status.
## @param node_yaml_path  Path to node.yaml
## @return  List of module dicts (name, enabled). Empty list on parse error.
def _parse_node_modules_yaml(node_yaml_path: str) -> list[dict]:
    """Parse enabled modules from node.yaml.

    Supports dict entries (with name/enabled keys).
    Returns empty list on parse error or missing section.
    """
    try:
        from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError, ConfigValidationError
        from core.internal.shared.node_yaml import NodeYaml

        modules_raw = NodeYaml(node_yaml_path).get_list("modules")
        out: list[dict] = []
        for m in modules_raw:
            if isinstance(m, dict):
                out.append(
                    {
                        "name": m.get("name", ""),
                        "enabled": m.get("enabled", True),
                    }
                )
            elif isinstance(m, str):
                out.append({"name": m, "enabled": True})
        return out
    except (ConfigNotFoundError, ConfigParseError, ConfigValidationError) as exc:
        logger.warning("[IMP:8][_parse_node_modules_yaml] Failed to parse modules from %s: %s", node_yaml_path, exc)
        return []


# endregion FUNC__parse_node_modules_yaml


# region FUNC__extract_named_volumes
## @purpose  Extract named volume source names from docker compose config JSON.
##           Filters out bind mounts (type=bind). Only returns named volumes
##           (type=volume or no type specified).
## @param compose_json  Parsed docker compose config dict (from --format json)
## @return  List of named volume source names (deduplicated)
## @invariant O7 — detect-only, never create volumes
def _extract_named_volumes(compose_json: dict) -> list[str]:
    """Extract named volume source names from compose config JSON.

    Only returns volumes with type: volume or no type (not bind mounts).
    """
    volumes_set: set[str] = set()
    services = compose_json.get("services", {})

    for svc_config in services.values():
        vol_entries = svc_config.get("volumes", [])
        if not isinstance(vol_entries, list):
            continue
        for entry in vol_entries:
            if not isinstance(entry, dict):
                continue
            vol_type = entry.get("type", "volume")  # default type is volume
            vol_source = entry.get("source", "")
            if vol_type == "volume" and vol_source:
                volumes_set.add(vol_source)

    return list(volumes_set)


# endregion FUNC__extract_named_volumes


# region FUNC_reconcile_volumes
## @purpose  Detect-only volume reconciliation (O7 invariant). Reads node.yaml
##           to find docker modules, inspects compose config for named volumes,
##           and verifies they exist via `docker volume inspect`. NEVER creates
##           volumes — only reports missing ones.
## @complexity O(N×M×V) — N=modules, M=volumes per module, V=volume inspect
## @io       stdout/stderr: LDD logs [IMP:7-10]
##           side-effect: subprocess calls to docker compose config + volume inspect
## @param node_yaml_path  Path to node.yaml
## @param dry_run         If True, only report planned mutations
## @param report_only     If True, skip mutations entirely
## @return  Drift report entry dict
## @invariant O7 — detect-only, NEVER docker volume create
## @edge-cases
##   - Docker daemon unavailable → status=fail, no further checks
##   - Module without compose.yml → skipped (not a docker module)
##   - All volumes exist → status=converged
##   - One or more volumes missing → status=warn, never create
##   - Bind mounts (type=bind) → excluded from inspection
def reconcile_volumes(
    node_yaml_path: str,
    dry_run: bool = False,
    report_only: bool = False,
) -> dict:
    """Reconcile Docker named volumes — detect-only (O7 invariant).

    Returns a drift entry dict with status: ok|skipped|converged|warn|fail.
    """
    unit = "R7"
    logger.info("[IMP:8][converge][%s] START: reconcile_volumes — detect-only named volume check (O7)", unit)

    # ── Check docker daemon ──
    docker_info_r = _run_subprocess(["docker", "info"], timeout=DOCKER_TIMEOUT)
    if docker_info_r.returncode != 0:
        msg = "Docker daemon not available — skipping volume reconciliation"
        logger.error("[IMP:10][converge][%s] FAIL: %s", unit, msg)
        report_add(unit, "fail", msg)
        _set_exit(2)
        return {"unit": unit, "status": "fail", "detail": msg}

    # ── Parse modules from node.yaml ──
    modules = _parse_node_modules_yaml(node_yaml_path)
    if not modules:
        logger.info("[IMP:9][converge][%s] SKIP: No modules defined in node.yaml", unit)
        report_add(unit, "skipped", "No modules defined in node.yaml")
        return {"unit": unit, "status": "skipped", "detail": "No modules defined in node.yaml"}

    # Derive modules directory from _core_dir
    modules_dir = Path(_core_dir) / "modules"

    missing_volumes: list[str] = []
    checked_modules = 0

    for mod in modules:
        mod_name = mod.get("name", "")
        if not mod_name or not mod.get("enabled", True):
            continue

        # Find compose file for this module
        compose_file = None
        mod_dir = modules_dir / mod_name
        for cf in ("compose.yaml", "compose.yml", "docker-compose.yml"):
            candidate = mod_dir / cf
            if candidate.is_file():
                compose_file = candidate
                break

        if not compose_file:
            logger.info("[IMP:7][converge][%s] Module %s has no compose file — skipping (not docker)", unit, mod_name)
            continue

        checked_modules += 1
        logger.info("[IMP:7][converge][%s] Checking module: %s (compose: %s)", unit, mod_name, compose_file)

        # Run docker compose config to get resolved JSON
        cmd = [
            "docker",
            "compose",
            "-f",
            str(compose_file),
            "--profile",
            mod_name,
            "config",
            "--format",
            "json",
        ]
        result = _run_subprocess(cmd, timeout=DOCKER_TIMEOUT)
        if result.returncode != 0:
            logger.warning(
                "[IMP:8][converge][%s] docker compose config failed for %s: %s",
                unit,
                mod_name,
                result.stderr.strip(),
            )
            continue

        try:
            compose_json = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            logger.warning("[IMP:8][converge][%s] Failed to parse compose JSON for %s: %s", unit, mod_name, exc)
            continue

        named_volumes = _extract_named_volumes(compose_json)
        if not named_volumes:
            logger.info("[IMP:7][converge][%s] No named volumes in %s", unit, mod_name)
            continue

        logger.info("[IMP:7][converge][%s] Named volumes in %s: %s", unit, mod_name, named_volumes)

        for vol_name in named_volumes:
            inspect_r = _run_subprocess(
                ["docker", "volume", "inspect", vol_name],
                timeout=DOCKER_TIMEOUT,
            )
            if inspect_r.returncode != 0:
                logger.warning(
                    "[IMP:9][converge][%s] VOLUME MISSING: %s (module: %s) — detect-only, NOT creating (O7)",
                    unit,
                    vol_name,
                    mod_name,
                )
                missing_volumes.append(vol_name)
            else:
                logger.info("[IMP:7][converge][%s] Volume OK: %s", unit, vol_name)

    # ── Report ──
    if not checked_modules:
        logger.info("[IMP:9][converge][%s] SKIP: No docker modules with compose files found", unit)
        report_add(unit, "skipped", "No docker modules with compose files")
        return {"unit": unit, "status": "skipped", "detail": "No docker modules with compose files"}

    if missing_volumes:
        logger.warning(
            "[IMP:9][converge][%s] DONE: %d named volume(s) missing (detect-only — O7) — %s",
            unit,
            len(missing_volumes),
            missing_volumes,
        )
        report_add(unit, "warn", f"{len(missing_volumes)} named volume(s) missing: {missing_volumes}")
        _set_exit(1)
        return {
            "unit": unit,
            "status": "warn",
            "detail": f"{len(missing_volumes)} named volume(s) missing",
        }

    logger.info("[IMP:9][converge][%s] DONE: All named volumes exist (converged)", unit)
    report_add(unit, "converged", "All named volumes exist")
    return {"unit": unit, "status": "converged", "detail": "All named volumes exist"}


# endregion FUNC_reconcile_volumes


# ═══════════════════════════════════════════════════════════════════
# R8 — reconcile_sudoers
# ═══════════════════════════════════════════════════════════════════
# region FUNC__import_sudoers_generator
## @purpose  Lazy-import the sudoers_generator module. Adds the deploy/
##           directory to sys.path for cross-module imports.
_sudoers_generator_imported = False


def _import_sudoers_generator() -> None:
    """Import sudoers_generator module (lazy, one-time)."""
    global _sudoers_generator_imported
    if _sudoers_generator_imported:
        return
    # Reconciler is at core/internal/bootstrap/converge/reconciler.py
    # sudoers_generator is at core/internal/bootstrap/deploy/sudoers_generator.py
    deploy_dir = Path(__file__).resolve().parent.parent / "deploy"
    if str(deploy_dir) not in sys.path:
        sys.path.insert(0, str(deploy_dir))
    global sudoers_generator
    import sudoers_generator

    _sudoers_generator_imported = True


# endregion FUNC__import_sudoers_generator


# region FUNC__build_sudoers_content
## @purpose  Build complete sudoers file content for a module given rules.
## @param module_name  Module name (for header comment)
## @param rules        List of sudoers rule strings
## @return  Full file content as string
def _build_sudoers_content(module_name: str, rules: list[str]) -> str:
    """Build sudoers file content with header and rules."""
    lines = [
        f"# platform module sudoers — {module_name}",
        "# Generated by reconciler.py (R8)",
        "# DO NOT edit manually — managed by core bootstrap",
        "",
    ]
    lines.extend(rules)
    lines.append("")
    return "\n".join(lines)


# endregion FUNC__build_sudoers_content


# region FUNC__atomic_write_sudoers
## @purpose  Atomic sudoers write: temp file → visudo -c -f validation → os.replace.
##           On validation failure: original file untouched, temp file cleaned up.
## @param target_path  Path to target sudoers file (e.g. /etc/sudoers.d/platform-nginx)
## @param content      Full sudoers file content
## @param module_name  Module name for temp file prefix
## @return  True if write + validation succeeded, False otherwise
def _atomic_write_sudoers(target_path: Path, content: str, module_name: str) -> bool:
    """Write sudoers file atomically with visudo validation."""
    parent_dir = target_path.parent
    parent_dir.mkdir(parents=True, exist_ok=True)
    tmp_path: str = ""

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            prefix=f".platform-sudoers-{module_name}-",
            suffix=".tmp",
            dir=str(parent_dir),
            delete=False,
        ) as tmp_fh:
            tmp_path = tmp_fh.name
            tmp_fh.write(content)

        # Set mode 0440 before validation
        os.chmod(tmp_path, 0o440)

        # Validate with visudo -c -f
        validate_r = _run_subprocess(["visudo", "-c", "-f", tmp_path], timeout=FILE_OP_TIMEOUT)
        if validate_r.returncode != 0:
            logger.error(
                "[IMP:10][_atomic_write_sudoers] visudo -c FAILED for %s — original untouched: %s",
                target_path,
                validate_r.stderr.strip(),
            )
            _safe_cleanup_tmp(tmp_path)
            return False

        # Atomic replace via os.replace (same-filesystem atomic rename)
        os.replace(tmp_path, str(target_path))
        os.chmod(str(target_path), 0o440)
        logger.info("[IMP:9][_atomic_write_sudoers] DONE: %s written and validated", target_path)
        return True

    except OSError as exc:
        logger.error("[IMP:10][_atomic_write_sudoers] OS error writing %s: %s", target_path, exc)
        _safe_cleanup_tmp(tmp_path)
        return False
    # noqa: EXC — catch-all after OSError already handled, prevents silent sudoers write failure
    except Exception as exc:  # noqa: EXC — catch-all after OSError already handled
        logger.error("[IMP:9][_atomic_write_sudoers] Unexpected error writing %s: %s", target_path, exc)
        _safe_cleanup_tmp(tmp_path)
        return False


# endregion FUNC__atomic_write_sudoers


# region FUNC__safe_cleanup_tmp
## @purpose  Remove a temp file if it exists (best-effort)
def _safe_cleanup_tmp(tmp_path: str) -> None:
    """Remove a temp file if it exists (best-effort cleanup)."""
    if tmp_path and os.path.exists(tmp_path):
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)


# endregion FUNC__safe_cleanup_tmp


# region FUNC_reconcile_sudoers
## @purpose  Reconcile sudoers.d files for all enabled modules. Generates desired
##           content via sudoers_generator (template_engine.render_template native),
##           compares
##           with actual files at SUDOERS_DIR, and self-heals via atomic write.
## @complexity O(N×M) — N=modules, M=sudoers files per module
## @io       stdout/stderr: LDD logs [IMP:7-10]
##           side-effect: temp file write, visudo -c, os.replace of sudoers files
## @param node_yaml_path  Path to node.yaml
## @param templates_dir   Path to templates/ directory (contains sudo-whitelist.template)
## @param dry_run         If True, only report planned mutations
## @param report_only     If True, skip mutations entirely
## @return  Drift report entry dict
## @edge-cases
##   - Docker daemon unavailable → status=fail, no further checks
##   - Module without compose.yml → skipped (not a docker module)
##   - All volumes exist → status=converged
##   - visudo validation fails → warn, original file unchanged
##   - SUDOERS_DIR does not exist → created on write
def reconcile_sudoers(
    node_yaml_path: str,
    templates_dir: str,
    dry_run: bool = False,
    report_only: bool = False,
) -> dict:
    """Reconcile sudoers.d files — detect drift and self-heal via atomic write.

    Returns a drift entry dict with status: ok|skipped|converged|mutated|warn|fail.
    """
    unit = "R8"
    logger.info("[IMP:8][converge][%s] START: reconcile_sudoers — drift detection and self-heal", unit)

    # ── Import sudoers_generator ──
    _import_sudoers_generator()

    # ── Parse modules from node.yaml ──
    modules = _parse_node_modules_yaml(node_yaml_path)
    if not modules:
        logger.info("[IMP:9][converge][%s] SKIP: No modules defined in node.yaml", unit)
        report_add(unit, "skipped", "No modules defined in node.yaml")
        return {"unit": unit, "status": "skipped", "detail": "No modules defined in node.yaml"}

    # Derive modules dir and platform root
    core_dir_path = Path(_core_dir)
    modules_dir = core_dir_path / "modules"
    platform_root = str(core_dir_path.parent)
    templates_dir_path = Path(templates_dir)

    mutated = 0
    errors = 0
    skipped = 0
    warnings = 0

    for mod in modules:
        mod_name = mod.get("name", "")
        if not mod_name or not mod.get("enabled", True):
            continue

        logger.info("[IMP:7][converge][%s] Processing module: %s", unit, mod_name)

        # Render rules via sudoers_generator
        try:
            rules = sudoers_generator._render_sudoers_rules(
                module_name=mod_name,
                modules_dir=modules_dir,
                templates_dir=templates_dir_path,
                platform_root=platform_root,
            )
        except (OSError, FileNotFoundError) as exc:
            logger.warning("[IMP:8][converge][%s] Failed to render rules for %s: %s", unit, mod_name, exc)
            errors += 1
            _set_exit(2)
            continue

        if not rules:
            logger.info("[IMP:7][converge][%s] No sudoers rules for %s — skipping", unit, mod_name)
            skipped += 1
            continue

        # Build desired content
        desired_content = _build_sudoers_content(mod_name, rules)

        # Read actual content
        sudoers_file = Path(SUDOERS_DIR) / f"platform-{mod_name}"
        actual_content = ""
        try:
            if sudoers_file.is_file():
                actual_content = sudoers_file.read_text()
        except OSError:
            actual_content = ""

        # Compare
        if actual_content == desired_content:
            logger.info("[IMP:9][converge][%s] CONVERGED: %s already matches desired state", unit, sudoers_file)
            report_add(unit, "converged", f"{sudoers_file.name} converged")
            continue

        # Drift detected
        if dry_run or report_only:
            logger.info("[IMP:9][converge][%s] WOULD update: %s (drift detected)", unit, sudoers_file)
            report_add(unit, "mutated", f"{sudoers_file.name} would be updated")
            mutated += 1
            _set_exit(1)
            continue

        # Self-heal via atomic write
        logger.info("[IMP:8][converge][%s] Drift detected in %s — self-healing via atomic write", unit, sudoers_file)
        success = _atomic_write_sudoers(sudoers_file, desired_content, mod_name)

        if success:
            logger.info("[IMP:9][converge][%s] DONE: %s updated successfully", unit, sudoers_file)
            report_add(unit, "mutated", f"{sudoers_file.name} updated")
            mutated += 1
            _set_exit(1)
        else:
            logger.warning(
                "[IMP:9][converge][%s] WARN: visudo validation failed for %s — original untouched",
                unit,
                sudoers_file,
            )
            report_add(unit, "warn", f"{sudoers_file.name}: visudo validation failed")
            warnings += 1
            _set_exit(1)

    # ── Final report ──
    if mutated > 0:
        status = "mutated"
        detail = f"{mutated} sudoers file(s) updated"
    elif warnings > 0:
        status = "warn"
        detail = f"{warnings} module(s) had visudo validation warnings"
    elif errors > 0:
        status = "fail"
        detail = f"{errors} module(s) had errors"
    else:
        status = "converged"
        detail = "All sudoers files match desired state"

    logger.info(
        "[IMP:9][converge][%s] DONE: mutated=%d errors=%d warnings=%d skipped=%d",
        unit,
        mutated,
        errors,
        warnings,
        skipped,
    )
    report_add(unit, status, detail)
    return {"unit": unit, "status": status, "detail": detail}


# endregion FUNC_reconcile_sudoers


# ═══════════════════════════════════════════════════════════════════
# R9 — reconcile_runtime_state
# ═══════════════════════════════════════════════════════════════════
BAD_DOCKER_STATES = {"exited", "restarting", "dead", "unhealthy", "paused"}
"""## @invariant Container states that trigger self-heal via docker compose up -d."""


# region FUNC__resolve_container_name
## @purpose  Get container name(s) for a module via docker ps --filter.
## @param module_name  Module name (used as name filter)
## @return  List of container names matching the module
def _resolve_container_name(module_name: str) -> list[str]:
    """Resolve container names for a module via docker ps --filter name.

    Returns list of container names. Empty list if no matching containers.
    """
    ps_r = _run_subprocess(
        [
            "docker",
            "ps",
            "--filter",
            f"name={module_name}",
            "--format",
            "{{.Names}}",
        ],
        timeout=DOCKER_TIMEOUT,
    )
    if ps_r.returncode != 0:
        logger.warning("[IMP:8][_resolve_container_name] docker ps failed for module %s", module_name)
        return []
    containers = [c.strip() for c in ps_r.stdout.splitlines() if c.strip()]
    logger.info("[IMP:7][_resolve_container_name] Module %s → containers: %s", module_name, containers)
    return containers


# endregion FUNC__resolve_container_name


# region FUNC__get_container_state
## @purpose  Get Docker container state via docker inspect.
## @param container_name  Container name to inspect
## @return  State string (e.g. "running", "exited"). "unknown" on failure.
def _get_container_state(container_name: str) -> str:
    """Get container state via docker inspect --format '{{.State.Status}}'."""
    inspect_r = _run_subprocess(
        [
            "docker",
            "inspect",
            container_name,
            "--format",
            "{{.State.Status}}",
        ],
        timeout=DOCKER_TIMEOUT,
    )
    if inspect_r.returncode != 0:
        logger.warning("[IMP:8][_get_container_state] docker inspect failed for %s", container_name)
        return "unknown"
    state = inspect_r.stdout.strip()
    logger.info("[IMP:7][_get_container_state] Container %s → state=%s", container_name, state)
    return state


# endregion FUNC__get_container_state


# region FUNC__load_cooldown
## @purpose  Load cooldown tracking data from JSON file.
## @return  Dict with structure: {"run": int, "containers": {name: {"last_healed_run": int}}}
def _load_cooldown() -> dict:
    """Load cooldown tracking data from COOLDOWN_FILE.

    Returns default structure if file is missing or corrupted.
    """
    filepath = Path(COOLDOWN_FILE)
    if filepath.is_file():
        try:
            data = json.loads(filepath.read_text())
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("[IMP:8][_load_cooldown] Failed to read cooldown file: %s", exc)
    return {"run": 0, "containers": {}}


# endregion FUNC__load_cooldown


# region FUNC__save_cooldown
## @purpose  Save cooldown tracking data to JSON file.
## @param data  Dict with run counter and container cooldown entries
def _save_cooldown(data: dict) -> None:
    """Save cooldown tracking data to COOLDOWN_FILE."""
    filepath = Path(COOLDOWN_FILE)
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(json.dumps(data, indent=2))
        logger.info("[IMP:8][_save_cooldown] Cooldown saved to %s", COOLDOWN_FILE)
    except OSError as exc:
        logger.warning("[IMP:8][_save_cooldown] Failed to save cooldown: %s", exc)


# endregion FUNC__save_cooldown


# region FUNC_reconcile_runtime_state
## @purpose  Reconcile Docker container runtime state. For each docker module,
##           inspect container state. If state is bad (exited, restarting, dead,
##           unhealthy, paused), self-heal via `docker compose up -d`. Cooldown
##           tracking prevents repeated self-heal of flapping containers.
## @complexity O(N×C) — N=modules, C=containers per module
## @io       stdout/stderr: LDD logs [IMP:7-10]
##           side-effect: docker compose up -d (self-heal), cooldown file update
## @param node_yaml_path  Path to node.yaml
## @param modules_dir     Path to modules/ directory
## @param dry_run         If True, only report planned mutations
## @param report_only     If True, skip mutations entirely
## @return  Drift report entry dict
## @edge-cases
##   - Docker daemon unavailable → status=fail
##   - All containers running → status=converged
##   - Container exited → self-heal via docker compose up -d (NOT docker restart)
##   - Container in cooldown (healed within last 3 runs) → skip self-heal
def reconcile_runtime_state(
    node_yaml_path: str,
    modules_dir: str,
    dry_run: bool = False,
    report_only: bool = False,
) -> dict:
    """Reconcile Docker container runtime state — self-heal via compose up -d.

    Returns a drift entry dict with status: ok|skipped|converged|mutated|warn|fail.
    """
    unit = "R9"
    logger.info("[IMP:8][converge][%s] START: reconcile_runtime_state — checking container states", unit)

    # ── Check docker daemon ──
    docker_info_r = _run_subprocess(["docker", "info"], timeout=DOCKER_TIMEOUT)
    if docker_info_r.returncode != 0:
        msg = "Docker daemon not available — skipping runtime reconciliation"
        logger.error("[IMP:10][converge][%s] FAIL: %s", unit, msg)
        report_add(unit, "fail", msg)
        _set_exit(2)
        return {"unit": unit, "status": "fail", "detail": msg}

    # ── Parse modules from node.yaml ──
    modules = _parse_node_modules_yaml(node_yaml_path)
    if not modules:
        logger.info("[IMP:9][converge][%s] SKIP: No modules defined in node.yaml", unit)
        report_add(unit, "skipped", "No modules defined in node.yaml")
        return {"unit": unit, "status": "skipped", "detail": "No modules defined in node.yaml"}

    # ── Load cooldown data ──
    cooldown = _load_cooldown()
    current_run = cooldown.get("run", 0) + 1
    cooldown["run"] = current_run
    if "containers" not in cooldown:
        cooldown["containers"] = {}

    # ── Check for global cooldown (any container healed in last 3 runs) ──
    global_cooldown = False
    for cname, cdata in cooldown["containers"].items():
        last_healed = cdata.get("last_healed_run", 0)
        if last_healed > 0 and current_run - last_healed < 3:
            global_cooldown = True
            logger.info(
                "[IMP:7][converge][%s] Global cooldown active — %s healed at run %d (diff=%d < 3)",
                unit,
                cname,
                last_healed,
                current_run - last_healed,
            )
            break

    if global_cooldown:
        logger.info(
            "[IMP:9][converge][%s] COOLDOWN: Previously healed containers still in cooldown — skipping all healing",
            unit,
        )
        report_add(unit, "converged", "In cooldown — previously healed containers")
        return {"unit": unit, "status": "converged", "detail": "Cooldown active, no healing"}

    modules_dir_path = Path(modules_dir)
    healed = 0
    errors = 0

    for mod in modules:
        mod_name = mod.get("name", "")
        if not mod_name or not mod.get("enabled", True):
            continue

        # Check if module has a compose file (docker module)
        compose_file = None
        mod_dir = modules_dir_path / mod_name
        for cf in ("compose.yaml", "compose.yml", "docker-compose.yml"):
            candidate = mod_dir / cf
            if candidate.is_file():
                compose_file = candidate
                break

        if not compose_file:
            logger.info("[IMP:7][converge][%s] %s has no compose file — skipping (not docker)", unit, mod_name)
            continue

        logger.info("[IMP:7][converge][%s] Checking module: %s", unit, mod_name)

        # Get container names for this module
        containers = _resolve_container_name(mod_name)
        if not containers:
            logger.info("[IMP:7][converge][%s] No running containers for module %s", unit, mod_name)
            continue

        needs_heal = False
        for cname in containers:
            state = _get_container_state(cname)
            if state in BAD_DOCKER_STATES:
                logger.warning(
                    "[IMP:9][converge][%s] Container %s state=%s — needs self-heal",
                    unit,
                    cname,
                    state,
                )
                needs_heal = True
            elif state == "running":
                logger.info("[IMP:7][converge][%s] Container %s OK (running)", unit, cname)

        if not needs_heal:
            logger.info("[IMP:9][converge][%s] Module %s all containers OK", unit, mod_name)
            continue

        # ── Self-heal via docker compose up -d ──
        if dry_run or report_only:
            logger.info("[IMP:9][converge][%s] WOULD heal module %s via docker compose up -d", unit, mod_name)
            report_add(unit, "mutated", f"{mod_name}: would be restarted via compose up -d")
            healed += 1
            _set_exit(1)
            continue

        logger.info("[IMP:8][converge][%s] Self-healing module %s via docker compose up -d", unit, mod_name)
        heal_r = _run_subprocess(
            [
                "docker",
                "compose",
                "-f",
                str(compose_file),
                "up",
                "-d",
            ],
            timeout=DOCKER_TIMEOUT,
        )

        if heal_r.returncode == 0:
            logger.info("[IMP:9][converge][%s] Module %s healed successfully", unit, mod_name)
            report_add(unit, "mutated", f"{mod_name}: restarted via compose up -d")
            healed += 1
            _set_exit(1)
            # Record heal in cooldown
            cooldown["containers"][mod_name] = {"last_healed_run": current_run}
        else:
            logger.error(
                "[IMP:10][converge][%s] Failed to heal module %s: %s",
                unit,
                mod_name,
                heal_r.stderr.strip(),
            )
            report_add(unit, "fail", f"{mod_name}: compose up -d failed")
            errors += 1
            _set_exit(2)

    # ── Save cooldown data ──
    _save_cooldown(cooldown)

    # ── Final report ──
    if healed > 0:
        status = "mutated"
        detail = f"{healed} module(s) healed via compose up -d"
    elif errors > 0:
        status = "fail"
        detail = f"{errors} module(s) had errors"
    else:
        status = "converged"
        detail = "All containers running"

    logger.info("[IMP:9][converge][%s] DONE: healed=%d errors=%d", unit, healed, errors)
    return {"unit": unit, "status": status, "detail": detail}


# endregion FUNC_reconcile_runtime_state


# ═══════════════════════════════════════════════════════════════════
# CLI entry point
# ═══════════════════════════════════════════════════════════════════
# region FUNC_main
## @purpose  CLI entry point: parse args, dispatch R1-R9, aggregate exit code,
##           emit JSON report if --report-only.
## @io       ⇥ sys.argv → ⎋ exit 0|1|2; stdout: JSON report (--report-only)
## @complexity 2 — argument dispatch + unit filter loop
## @invariants
##   - Exit codes: 0=converged, 1=warnings, 2=errors
##   - --report-only: JSON report to stdout, exit 0
##   - --dry-run: LDD logs to stderr, exit 0
##   - Unit failure does NOT abort other units
def main() -> None:
    """CLI entry point for reconciler.py.

    Usage:
        python3 reconciler.py --node-yaml <path> [--node-name <name>] [--core-dir <path>]
                              [--templates-dir <path>] [--modules-dir <path>]
                              [--dry-run] [--report-only] [--units <R1,R2,...>]

    Exit codes:
        0 — fully converged (no drifts, no warnings)
        1 — warnings (non-critical drift detected)
        2 — one or more R-units failed (critical errors)
    """
    parser = argparse.ArgumentParser(
        description="Platform desired-state reconciler — converge 6 dimensions (R1-R9) from node.yaml.",
    )
    parser.add_argument(
        "--node-yaml",
        required=True,
        type=str,
        help="Path to node.yaml (required)",
    )
    parser.add_argument(
        "--node-name",
        default="",
        type=str,
        help="Node name for R6 overlay resolution (default: derived from node-yaml context)",
    )
    parser.add_argument(
        "--core-dir",
        default="",
        type=str,
        help="Path to core/ directory for R1/R2 path resolution (default: auto-detect from script location)",
    )
    parser.add_argument(
        "--templates-dir",
        default="",
        type=str,
        help="Path to templates/ directory for R8 sudoers generation (default: auto-detect from core-dir)",
    )
    parser.add_argument(
        "--modules-dir",
        default="",
        type=str,
        help="Path to modules/ directory for R9 runtime state (default: auto-detect from core-dir)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print planned mutations without executing (exit 0)",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        default=False,
        help="Check-only mode: emit JSON drift report to stdout (exit 0)",
    )
    parser.add_argument(
        "--units",
        default="",
        type=str,
        help="Comma-separated R-unit filter (e.g., 'R1,R3'). Empty = all units.",
    )

    args = parser.parse_args()

    # ── Set module-level state ──
    global _node_yaml_path, _node_name, _core_dir, _dry_run, _report_only
    global _templates_dir, _modules_dir
    _reset_state()

    _node_yaml_path = args.node_yaml
    _node_name = args.node_name
    _dry_run = args.dry_run
    _report_only = args.report_only

    # Resolve core_dir: argument → auto-detect from __file__
    # Auto-detect: go up from .../bootstrap/converge/ to core/
    _core_dir = args.core_dir or str(Path(__file__).resolve().parents[3])

    # Resolve templates_dir and modules_dir from args or core_dir
    _templates_dir = args.templates_dir if args.templates_dir else str(Path(_core_dir) / "templates")
    _modules_dir = args.modules_dir if args.modules_dir else str(Path(_core_dir) / "modules")

    units_filter = args.units

    # ── Validate node.yaml exists ──
    if not Path(_node_yaml_path).is_file():
        logger.error("[IMP:10][converge][main] FATAL: node.yaml not found at %s", _node_yaml_path)
        print(f'{{"error":"node.yaml not found: {_node_yaml_path}","exit_code":2}}')
        sys.exit(2)

    # ── Init report ──
    report_init()

    # ── Print header ──
    mode_str = "DRY-RUN" if _dry_run else ("REPORT-ONLY" if _report_only else "CONVERGE")
    logger.info("[IMP:9][converge][main] ==============================")
    logger.info("[IMP:9][converge][main] Platform Converge START")
    logger.info("[IMP:9][converge][main] Node: %s", _node_name)
    logger.info("[IMP:9][converge][main] Mode: %s", mode_str)
    logger.info("[IMP:9][converge][main] node.yaml: %s", _node_yaml_path)
    logger.info("[IMP:9][converge][main] core_dir: %s", _core_dir)
    logger.info("[IMP:9][converge][main] units: %s", units_filter if units_filter else "ALL")
    logger.info("[IMP:9][converge][main] ==============================")

    # ── Dispatch R-units with --units filter ──
    if _unit_enabled(units_filter, "R1"):
        reconcile_perms(_core_dir, dry_run=_dry_run, report_only=_report_only)
    else:
        logger.info("[IMP:7][converge][main] SKIP: R1 filtered out by --units=%s", units_filter)

    if _unit_enabled(units_filter, "R2"):
        reconcile_audit_log(_core_dir, dry_run=_dry_run, report_only=_report_only)
    else:
        logger.info("[IMP:7][converge][main] SKIP: R2 filtered out by --units=%s", units_filter)

    if _unit_enabled(units_filter, "R3"):
        reconcile_projects(_node_yaml_path, dry_run=_dry_run, report_only=_report_only)
    else:
        logger.info("[IMP:7][converge][main] SKIP: R3 filtered out by --units=%s", units_filter)

    if _unit_enabled(units_filter, "R4"):
        reconcile_networks(_node_yaml_path, dry_run=_dry_run, report_only=_report_only)
    else:
        logger.info("[IMP:7][converge][main] SKIP: R4 filtered out by --units=%s", units_filter)

    if _unit_enabled(units_filter, "R5"):
        detect_hosts_drift(_node_yaml_path)
    else:
        logger.info("[IMP:7][converge][main] SKIP: R5 filtered out by --units=%s", units_filter)

    if _unit_enabled(units_filter, "R6"):
        verify_vhosts(_node_yaml_path, _node_name, _core_dir, dry_run=_dry_run, report_only=_report_only)
    else:
        logger.info("[IMP:7][converge][main] SKIP: R6 filtered out by --units=%s", units_filter)

    # ── R7: reconcile_volumes (detect-only, O7) ──
    if _unit_enabled(units_filter, "R7"):
        reconcile_volumes(_node_yaml_path, dry_run=_dry_run, report_only=_report_only)
    else:
        logger.info("[IMP:7][converge][main] SKIP: R7 filtered out by --units=%s", units_filter)

    # ── R8: reconcile_sudoers (drift detection + self-heal) ──
    if _unit_enabled(units_filter, "R8"):
        reconcile_sudoers(_node_yaml_path, _templates_dir, dry_run=_dry_run, report_only=_report_only)
    else:
        logger.info("[IMP:7][converge][main] SKIP: R8 filtered out by --units=%s", units_filter)

    # ── R9: reconcile_runtime_state (container state + self-heal) ──
    if _unit_enabled(units_filter, "R9"):
        reconcile_runtime_state(_node_yaml_path, _modules_dir, dry_run=_dry_run, report_only=_report_only)
    else:
        logger.info("[IMP:7][converge][main] SKIP: R9 filtered out by --units=%s", units_filter)

    # ── Final summary ──
    logger.info("[IMP:9][converge][main] ==============================")
    if _has_errors:
        logger.info("[IMP:9][converge][main] ERRORS DETECTED — some R-units failed (exit 2)")
    elif _has_warnings:
        logger.info("[IMP:9][converge][main] WARNINGS DETECTED — non-critical drift (exit 1)")
    else:
        logger.info("[IMP:9][converge][main] FULLY CONVERGED — all R-units converged (exit 0)")
    logger.info("[IMP:9][converge][main] ==============================")

    # ── Report-only: JSON to stdout ──
    if _report_only:
        report_json = report_emit()
        print(report_json)
        sys.exit(0)

    # ── Final exit code ──
    if _has_errors:
        sys.exit(2)
    elif _has_warnings:
        sys.exit(1)
    else:
        sys.exit(0)


# endregion FUNC_main


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )
    main()
