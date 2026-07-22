#!/usr/bin/env python3
# GREP_SUMMARY: reconciler, converge, reconcile-perms, reconcile-audit-log, reconcile-projects, reconcile-networks, detect-hosts-drift, verify-vhosts, drift-detection, desired-state, r1, r2, r3, r4, r5, r6, json-report, node-yaml, idempotent
# STRUCTURE: ▶ argparse ┌--node-yaml --node-name --core-dir --dry-run --report-only --units┐ → ○ _unit_enabled filter → ▶ R1 reconcile_perms ⚡ find+chmod ug+x → ▶ R2 reconcile_audit_log ⚡ 0664 root:adm + usermod → ▶ R3 reconcile_projects ⚡ yaml parse + mkdir + stub + .env.platform → ▶ R4 reconcile_networks ⚡ docker network inspect proxy-net → ▶ R5 detect_hosts_drift ⚡ grep /etc/hosts → ▶ R6 verify_vhosts ⚡ nginx -t + orphan detection → ⊕ aggregate exit_code {0,1,2} → ⎋ JSON report stdout
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
# endregion MODULE_CONTRACT

import argparse
import json
import logging
import os
import re
import subprocess
import sys
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
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result
    except FileNotFoundError:
        logger.warning("[IMP:8][_run_subprocess] Binary not found: %s", cmd[0])
        return subprocess.CompletedProcess(args=cmd, returncode=127, stdout="", stderr=f"{cmd[0]}: not found")
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:8][_run_subprocess] Timeout after %ds: %s", timeout, " ".join(cmd))
        return subprocess.CompletedProcess(args=cmd, returncode=124, stdout="", stderr="timeout")
# endregion FUNC__run_subprocess


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
        try:
            os.chmod(str(f), os.stat(str(f)).st_mode | 0o110)  # ug+x
            fixed += 1
            logger.info("[IMP:7][converge][%s] Fixed: %s", unit, f)
        except OSError as exc:
            logger.warning("[IMP:8][converge][%s] chmod failed for %s: %s", unit, f, exc)
            continue

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
                    unit, AUDIT_LOG_FILE, current_mode, current_owner,
                )
                report_add(unit, "mutated", "audit.log permissions would be fixed")
                _set_exit(1)
            else:
                logger.info(
                    "[IMP:8][converge][%s] Fixing permissions: %s mode=%s owner=%s",
                    unit, AUDIT_LOG_FILE, current_mode, current_owner,
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
    return {"unit": unit, "status": "converged" if not errors else "fail", "detail": f"mutated={mutated} errors={errors}"}
# endregion FUNC_reconcile_projects


# region FUNC__parse_projects_yaml
## @purpose  Parse projects list from node.yaml
def _parse_projects_yaml(node_yaml_path: str) -> list[dict]:
    """Parse projects from node.yaml.

    Supports both dict entries (with name/domain keys) and string entries.
    Returns empty list on parse error or missing section.
    """
    try:
        import yaml
        with open(node_yaml_path) as f:
            data = yaml.safe_load(f)
        projects_raw = data.get("projects", []) if data else []
        out: list[dict] = []
        for p in projects_raw:
            if isinstance(p, dict):
                out.append({"name": p.get("name", ""), "domain": p.get("domain", "")})
            elif isinstance(p, str):
                out.append({"name": p, "domain": ""})
        return out
    except Exception as exc:
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
        logger.warning("[IMP:10][_validate_project_name] FAIL: Invalid project name '%s' — only [a-zA-Z0-9_-] allowed", name)
        return False
    return True
# endregion FUNC__validate_project_name


# region FUNC__reconcile_env_platform
## @purpose  Ensure .env.platform exists in project directory (if-missing)
def _reconcile_env_platform(
    proj_name: str,
    proj_dir: str,
    unit: str,
    dry_run: bool,
    report_only: bool,
) -> None:
    """Create .env.platform via gen-env-platform.sh or fallback to empty file.

    Reports via report_add and _set_exit; does not modify caller's local mutated counter —
    the approximate count from report entries is sufficient for the exit code contract.
    """

    env_file = Path(proj_dir) / ".env.platform"
    if env_file.is_file():
        logger.info("[IMP:7][converge][%s] SKIP: %s already exists (if-missing policy)", unit, env_file)
        return

    if dry_run or report_only:
        logger.info("[IMP:9][converge][%s] WOULD create: %s via gen-env-platform.sh", unit, env_file)
        report_add(unit, "mutated", f".env.platform would be created for {proj_name}")
        _set_exit(1)
        return

    logger.info("[IMP:8][converge][%s] Creating .env.platform via gen-env-platform.sh for %s", unit, proj_name)

    # Try gen-env-platform.sh first; fall back to empty file
    core_dir_global = _core_dir  # use module-level _core_dir
    gen_env_script = Path(core_dir_global) / "internal" / "scaffold" / "gen-env-platform.sh"

    if gen_env_script.is_file():
        r = _run_subprocess(
            ["bash", str(gen_env_script), "--name", proj_name, "--output", str(env_file)],
            timeout=DOCKER_TIMEOUT,
        )
        if r.returncode == 0:
            _run_subprocess(["chmod", "0640", str(env_file)], timeout=FILE_OP_TIMEOUT)
            _run_subprocess(["chown", "ci-deploy:ci-deploy", str(env_file)], timeout=FILE_OP_TIMEOUT)
            logger.info("[IMP:9][converge][%s] DONE: %s generated via gen-env-platform.sh", unit, env_file)
        else:
            logger.warning(
                "[IMP:9][converge][%s] WARN: gen-env-platform.sh failed for %s — creating empty .env.platform",
                unit,
                proj_name,
            )
            try:
                env_file.write_text("")
                _run_subprocess(["chmod", "0640", str(env_file)], timeout=FILE_OP_TIMEOUT)
                _run_subprocess(["chown", "ci-deploy:ci-deploy", str(env_file)], timeout=FILE_OP_TIMEOUT)
                logger.info("[IMP:9][converge][%s] DONE: %s created (fallback: empty)", unit, env_file)
            except OSError as exc:
                logger.error("[IMP:10][converge][%s] FAIL: .env.platform creation failed: %s", unit, exc)
                _set_exit(2)
                return
    else:
        logger.warning(
            "[IMP:8][converge][%s] WARN: gen-env-platform.sh not found — creating empty .env.platform",
            unit,
        )
        try:
            env_file.write_text("")
            _run_subprocess(["chmod", "0640", str(env_file)], timeout=FILE_OP_TIMEOUT)
            _run_subprocess(["chown", "ci-deploy:ci-deploy", str(env_file)], timeout=FILE_OP_TIMEOUT)
            logger.info("[IMP:9][converge][%s] DONE: %s created (fallback: empty)", unit, env_file)
        except OSError as exc:
            logger.error("[IMP:10][converge][%s] FAIL: .env.platform creation failed: %s", unit, exc)
            _set_exit(2)
            return

    report_add(unit, "mutated", f".env.platform created for {proj_name}")
    _set_exit(1)
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
                logger.error("[IMP:10][converge][%s] FAIL: docker network create proxy-net failed: %s", unit, create_r.stderr.strip())
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
                "docker", "ps",
                "--filter", f"label=com.docker.compose.project={pname}",
                "--format", "{{.Names}}",
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
                    "docker", "inspect", cname,
                    "--format", "{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}",
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
    overlay_dir = _resolve_nginx_overlay(str(node_yaml), converge_node)

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
            logger.error("[IMP:9][converge][%s] FAIL: Vhost overlay directory not resolved — cannot verify %s.conf", unit, domain)
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
def _resolve_nginx_overlay(node_yaml_path: str, converge_node: str) -> str | None:
    """Resolve nginx vhost directory from node.yaml context or node fallback."""
    try:
        import yaml
        with open(node_yaml_path) as f:
            data = yaml.safe_load(f)
        context_name = data.get("context", "") if data else ""
    except Exception:
        context_name = ""

    if context_name:
        candidate = f"/opt/{context_name}/platform/modules/nginx"
        if Path(candidate).is_dir():
            return candidate
        logger.info("[IMP:7][_resolve_nginx_overlay] Context overlay not found: %s", candidate)

    # Fallback: node-configs path
    candidate_node = f"/opt/{converge_node}/overlays/nginx"
    if Path(candidate_node).is_dir():
        return candidate_node

    # Fallback: node-configs standard path
    candidate_nc = f"/opt/node-configs/{converge_node}/overlays/nginx"
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
# CLI entry point
# ═══════════════════════════════════════════════════════════════════
# region FUNC_main
## @purpose  CLI entry point: parse args, dispatch R1-R6, aggregate exit code,
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
                              [--dry-run] [--report-only] [--units <R1,R2,...>]

    Exit codes:
        0 — fully converged (no drifts, no warnings)
        1 — warnings (non-critical drift detected)
        2 — one or more R-units failed (critical errors)
    """
    parser = argparse.ArgumentParser(
        description="Platform desired-state reconciler — converge 6 dimensions (R1-R6) from node.yaml.",
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
    _reset_state()

    _node_yaml_path = args.node_yaml
    _node_name = args.node_name
    _dry_run = args.dry_run
    _report_only = args.report_only

    # Resolve core_dir: argument → auto-detect from __file__
    if args.core_dir:
        _core_dir = args.core_dir
    else:
        # Auto-detect: go up from .../bootstrap/converge/ to core/
        _core_dir = str(Path(__file__).resolve().parents[3])

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
