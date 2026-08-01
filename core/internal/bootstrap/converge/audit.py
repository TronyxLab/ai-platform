#!/usr/bin/env python3
# GREP_SUMMARY: converge-audit, reconcile-audit-log, r2, audit-log, adm-group, ci-deploy, symlink-attack
# STRUCTURE: ▶ symlink check ┌log_dir + audit.jsonl┐ → ⚡ reconcile_ci_deploy_group ┌id -nG → usermod -aG adm┐ → ⚡ mkdir/chmod/chown 0750 root:adm → ⚡ stat verify 0664 root:adm → ⎋ drift entry {R2}
# region MODULE_CONTRACT
## @purpose  R2 reconcile_audit_log — audit.jsonl 0664 root:adm + ci-deploy adm group.
##           Извлечён из reconciler.py (B9 T2, U-31).
## @scope    converge/audit.py: reconcile_audit_log, reconcile_ci_deploy_group.
##           Вызывается оркестратором reconciler.py.
## @invariants
##   - Symlink на log_dir/audit.jsonl → FATAL (symlink attack prevention)
##   - ci-deploy в adm группе (usermod -aG adm); пользователь не существует → INFO + skip
##   - dry_run/report_only → мутации не выполняются
## @rationale DevPlan 116 B9 D3: 8 доменов reconciler по модулям.
## @changes  2026-08-01 · Extracted from reconciler.py (B9 T2)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
from pathlib import Path

import core.internal.bootstrap.converge.infra as infra
from core.internal.bootstrap.converge.infra import (
    AUDIT_LOG_DIR,
    AUDIT_LOG_FILE,
    FILE_OP_TIMEOUT,
    report_add,
    run_subprocess,
    set_exit,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# R2 — reconcile_audit_log
# ═══════════════════════════════════════════════════════════════════
# region FUNC_reconcile_audit_log
## @purpose  Ensure /var/log/platform/audit.jsonl exists with correct
##           ownership (root:adm) and permissions (0664). Also ensures
##           ci-deploy is in adm group for write access.
## @io       stdout/stderr: LDD logs [IMP:7-10]
##           side-effect: mkdir, chmod, chown, usermod (via subprocess)
## @param core_dir    Path to core/ directory (unused in R2 but kept for API consistency)
## @param dry_run     If True, only report planned mutations
## @param report_only If True, skip mutations entirely
## @return  Drift report entry dict
## @edge-cases
##   - audit.jsonl is a symlink → fail (symlink attack prevention)
##   - ci-deploy not in adm group → usermod -aG adm
##   - File already correct → SKIP
def reconcile_audit_log(
    core_dir: str,  # unused in R2, kept for API consistency
    dry_run: bool = False,
    report_only: bool = False,
) -> dict:
    """Reconcile audit.jsonl and ci-deploy adm group membership.

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
        set_exit(2)
        return {"unit": unit, "status": "fail", "detail": msg}

    if audit_log.is_symlink():
        msg = f"Symlink detected: {AUDIT_LOG_FILE} — possible attack"
        logger.error("[IMP:10][converge][%s] FATAL: %s", unit, msg)
        report_add(unit, "fail", msg)
        set_exit(2)
        return {"unit": unit, "status": "fail", "detail": msg}

    # ── Ensure ci-deploy is in adm group ──
    reconcile_ci_deploy_group(unit, dry_run, report_only)

    # ── Ensure directory exists ──
    if not log_dir.is_dir():
        if dry_run or report_only:
            logger.info("[IMP:9][converge][%s] WOULD create: %s 0750 root:adm", unit, AUDIT_LOG_DIR)
            report_add(unit, "mutated", f"Directory {AUDIT_LOG_DIR} would be created")
            set_exit(1)
        else:
            logger.info("[IMP:8][converge][%s] Creating %s 0750 root:adm", unit, AUDIT_LOG_DIR)
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
                run_subprocess(["chmod", "0750", AUDIT_LOG_DIR], timeout=FILE_OP_TIMEOUT)
                run_subprocess(["chown", "root:adm", AUDIT_LOG_DIR], timeout=FILE_OP_TIMEOUT)
                logger.info("[IMP:9][converge][%s] DONE: %s created 0750 root:adm", unit, AUDIT_LOG_DIR)
                report_add(unit, "mutated", f"Directory {AUDIT_LOG_DIR} created")
                set_exit(1)
            except OSError as exc:
                logger.error("[IMP:10][converge][%s] mkdir failed for %s: %s", unit, AUDIT_LOG_DIR, exc)
                report_add(unit, "fail", f"mkdir failed for {AUDIT_LOG_DIR}: {exc}")
                set_exit(2)
                return {"unit": unit, "status": "fail", "detail": f"mkdir failed: {exc}"}

    # ── Ensure audit.jsonl exists ──
    if not audit_log.is_file():
        if dry_run or report_only:
            logger.info("[IMP:9][converge][%s] WOULD create: %s 0664 root:adm", unit, AUDIT_LOG_FILE)
            report_add(unit, "mutated", f"File {AUDIT_LOG_FILE} would be created")
            set_exit(1)
        else:
            logger.info("[IMP:8][converge][%s] Creating %s 0664 root:adm", unit, AUDIT_LOG_FILE)
            try:
                audit_log.touch(exist_ok=True)
                run_subprocess(["chmod", "0664", AUDIT_LOG_FILE], timeout=FILE_OP_TIMEOUT)
                run_subprocess(["chown", "root:adm", AUDIT_LOG_FILE], timeout=FILE_OP_TIMEOUT)
                logger.info("[IMP:9][converge][%s] DONE: %s created 0664 root:adm", unit, AUDIT_LOG_FILE)
                report_add(unit, "mutated", f"File {AUDIT_LOG_FILE} created")
                set_exit(1)
            except OSError as exc:
                logger.error("[IMP:10][converge][%s] touch failed for %s: %s", unit, AUDIT_LOG_FILE, exc)
                report_add(unit, "fail", f"touch failed for {AUDIT_LOG_FILE}: {exc}")
                set_exit(2)
                return {"unit": unit, "status": "fail", "detail": f"touch failed: {exc}"}
    else:
        # File exists — verify permissions via stat subprocess
        mode_r = run_subprocess(
            ["stat", "-c", "%a", AUDIT_LOG_FILE],
            timeout=FILE_OP_TIMEOUT,
        )
        owner_r = run_subprocess(
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
                report_add(unit, "mutated", "audit.jsonl permissions would be fixed")
                set_exit(1)
            else:
                logger.info(
                    "[IMP:8][converge][%s] Fixing permissions: %s mode=%s owner=%s",
                    unit,
                    AUDIT_LOG_FILE,
                    current_mode,
                    current_owner,
                )
                run_subprocess(["chmod", "0664", AUDIT_LOG_FILE], timeout=FILE_OP_TIMEOUT)
                run_subprocess(["chown", "root:adm", AUDIT_LOG_FILE], timeout=FILE_OP_TIMEOUT)
                logger.info("[IMP:9][converge][%s] DONE: %s permissions corrected", unit, AUDIT_LOG_FILE)
                report_add(unit, "mutated", "audit.jsonl permissions corrected to 0664 root:adm")
                set_exit(1)
        else:
            logger.info("[IMP:9][converge][%s] SKIP: %s already 0664 root:adm (converged)", unit, AUDIT_LOG_FILE)
            report_add(unit, "converged", "audit.jsonl permissions correct")

    logger.info("[IMP:9][converge][%s] DONE: audit log reconciled", unit)
    return {
        "unit": unit,
        "status": "converged" if not infra.has_errors else "warn",
        "detail": "audit log reconciled",
    }


# endregion FUNC_reconcile_audit_log


# region FUNC_reconcile_ci_deploy_group
## @purpose  Ensure ci-deploy user is in adm group (публичный — B9 T2)
def reconcile_ci_deploy_group(unit: str, dry_run: bool, report_only: bool) -> None:
    """Check and fix ci-deploy adm group membership.

    If ci-deploy user does not exist yet (pre-bootstrap), logs INFO and skips.
    """
    # Check if ci-deploy user exists
    id_r = run_subprocess(["id", "-nG", "ci-deploy"], timeout=FILE_OP_TIMEOUT)
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
        set_exit(1)
    else:
        logger.info("[IMP:9][converge][%s] Adding ci-deploy to adm group", unit)
        usermod_r = run_subprocess(["usermod", "-aG", "adm", "ci-deploy"], timeout=FILE_OP_TIMEOUT)
        if usermod_r.returncode == 0:
            logger.info("[IMP:9][converge][%s] DONE: ci-deploy added to adm group", unit)
            report_add(unit, "mutated", "ci-deploy added to adm group")
            set_exit(1)
        else:
            logger.warning(
                "[IMP:8][converge][%s] WARN: usermod failed — ci-deploy may not have write access to audit.jsonl: %s",
                unit,
                usermod_r.stderr.strip(),
            )
            report_add(unit, "warn", "usermod failed for ci-deploy → adm group")


# endregion FUNC_reconcile_ci_deploy_group
