#!/usr/bin/env python3
# GREP_SUMMARY: converge-audit, reconcile-audit-log, r2, audit-log, ci-deploy, symlink-attack, setfacl, acl, ensure-audit-writable, fallback-0660, adm-group
# STRUCTURE: ▶ symlink check ┌log_dir + audit.jsonl┐ → ⚡ reconcile_ci_deploy_group ┌id -nG → usermod -aG adm┐ → ⚡ mkdir/chmod/chown 0750 root:adm → ⚡ audit_permissions_status ┌acl | group | none┐ → ◇ converged | ⚡ ensure_audit_writable (setfacl primary / 0660 fallback) → ⎋ drift entry {R2}
# region MODULE_CONTRACT
## @purpose  R2 reconcile_audit_log — audit.jsonl writable by root AND ci-deploy
##           (POSIX ACL setfacl primary / chgrp ci-deploy + 0660 fallback) + ci-deploy adm group.
##           P1 fix 2026-08-27: прежнее состояние 0664 root:adm БОРОЛОСЬ с runtime —
##           receive/deploy (ci-deploy forced-command) терял запись после chmod 640 от
##           audit_logger → постбутстрапный аудит молча пропадал. Целевое состояние вынесено
##           в shared/audit_logger.ensure_audit_writable (единый SoT) — R2 и logger сходятся.
##           Извлечён из reconciler.py (B9 T2, U-31).
## @scope    converge/audit.py: reconcile_audit_log, reconcile_ci_deploy_group.
##           Вызывается оркестратором reconciler.py.
## @invariants
##   - Symlink на log_dir/audit.jsonl → FATAL (symlink attack prevention)
##   - Целевое состояние файла (P1 fix): владелец root; PRIMARY — setfacl -m u:ci-deploy:rw,m::rw
##     + default ACL на dir (ротации); FALLBACK без setfacl — chgrp ci-deploy + chmod 0660.
##     Детект конвергентности — audit_permissions_status (acl|group), НЕ stat-mode 0664.
##   - ci-deploy в adm группе (usermod -aG adm) — сохранено (чтение adm-логов); write-канал = ACL/группа
##   - dry_run/report_only → мутации не выполняются
## @rationale DevPlan 116 B9 D3: 8 доменов reconciler по модулям.
##            P1 fix 2026-08-27 (D1 root cause): 0664 root:adm зависел от группового write через
##            adm-членство и молча ломался при root-записи (audit_logger chmod 640 → mask/group r--).
##            POSIX ACL — явный named-user write для ci-deploy, не зависящий от group-битов; graceful
##            fallback 0660 — честный trade-off (TRAP[DECISION] в ensure_audit_writable).
## @changes  2026-08-01 · Extracted from reconciler.py (B9 T2)
## @changes  2026-08-27 · P1 fix — целевое состояние ACL/0660 через shared ensure_audit_writable;
##                      audit_permissions_status; CI_DEPLOY_USER из shared/file_lock (single source)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
from pathlib import Path

from core.internal.bootstrap.converge import infra
from core.internal.bootstrap.converge.infra import (
    AUDIT_LOG_DIR,
    report_add,
    run_subprocess,
    set_exit,
)

# R2-каноны констант — прямые импорты из shared SoT (pyright reportPrivateLocalImportUsage)
from core.internal.shared.audit_logger import DEFAULT_LOG_FILE as AUDIT_LOG_FILE
from core.internal.shared.audit_logger import audit_permissions_status, ensure_audit_writable
from core.internal.shared.file_lock import CI_DEPLOY_USER
from core.internal.shared.timeouts import FILE_OP_TIMEOUT

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# R2 — reconcile_audit_log
# ═══════════════════════════════════════════════════════════════════
# region FUNC_reconcile_audit_log
## @purpose  Ensure /var/log/platform/audit.jsonl exists with the canonical writable state:
##           owner root, write for root AND ci-deploy — POSIX ACL (setfacl) primary,
##           chgrp ci-deploy + chmod 0660 graceful fallback (P1 fix 2026-08-27).
##           Also ensures ci-deploy is in adm group (чтение adm-логов).
## @io       stdout/stderr: LDD logs [IMP:7-10]
##           side-effect: mkdir, chmod, chown, usermod, setfacl/chgrp (via subprocess)
## @param _core_dir   Path to core/ directory (unused in R2 but kept for API consistency)
## @param dry_run     If True, only report planned mutations
## @param report_only If True, skip mutations entirely
## @return  Drift report entry dict
## @edge-cases
##   - audit.jsonl is a symlink → fail (symlink attack prevention)
##   - ci-deploy not in adm group → usermod -aG adm
##   - File already converged (acl|group) → SKIP
##   - setfacl недоступен на ноде → fallback 0660 (TRAP[DECISION] в ensure_audit_writable)
def reconcile_audit_log(
    _core_dir: str,  # unused in R2, kept for API consistency
    dry_run: bool = False,
    report_only: bool = False,
) -> dict[str, str]:
    """Reconcile audit.jsonl writable state and ci-deploy adm group membership.

    Returns a drift entry dict with status: ok|skipped|mutated|warn|fail.
    """
    unit = "R2"
    logger.info(
        "[IMP:8][converge][%s] START: reconcile_audit_log — ensuring %s writable by root + %s",
        unit,
        AUDIT_LOG_FILE,
        CI_DEPLOY_USER,
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
            # ruff: ignore[PLW0717] — try вложен в условный блок внутри функции — после-try чтение локалей неанализир...
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
                _ = run_subprocess(["chmod", "0750", AUDIT_LOG_DIR], timeout=FILE_OP_TIMEOUT)
                _ = run_subprocess(["chown", "root:adm", AUDIT_LOG_DIR], timeout=FILE_OP_TIMEOUT)
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
            logger.info(
                "[IMP:9][converge][%s] WOULD create: %s (writable by root + %s)",
                unit,
                AUDIT_LOG_FILE,
                CI_DEPLOY_USER,
            )
            report_add(unit, "mutated", f"File {AUDIT_LOG_FILE} would be created")
            set_exit(1)
        else:
            logger.info(
                "[IMP:8][converge][%s] Creating %s (writable by root + %s)",
                unit,
                AUDIT_LOG_FILE,
                CI_DEPLOY_USER,
            )
            try:
                audit_log.touch(exist_ok=True)
                # Единый SoT прав (shared ensure_audit_writable): setfacl primary / 0660 fallback.
                # Default ACL на dir проставляется внутри — ротации наследуют запись для ci-deploy.
                applied = ensure_audit_writable(AUDIT_LOG_FILE, CI_DEPLOY_USER)
                logger.info(
                    "[IMP:9][converge][%s] DONE: %s created, permissions → %s (writable by root + %s)",
                    unit,
                    AUDIT_LOG_FILE,
                    applied,
                    CI_DEPLOY_USER,
                )
                report_add(
                    unit,
                    "mutated",
                    f"File {AUDIT_LOG_FILE} created (permissions {applied}, writable by root + {CI_DEPLOY_USER})",
                )
                set_exit(1)
            except OSError as exc:
                logger.error("[IMP:10][converge][%s] touch failed for %s: %s", unit, AUDIT_LOG_FILE, exc)
                report_add(unit, "fail", f"touch failed for {AUDIT_LOG_FILE}: {exc}")
                set_exit(2)
                return {"unit": unit, "status": "fail", "detail": f"touch failed: {exc}"}
    else:
        # File exists — verify canonical writable state via audit_permissions_status
        # (НЕ stat-mode 0664: ACL-состояние не отражается в mode-битах — P1 fix 2026-08-27)
        status = audit_permissions_status(AUDIT_LOG_FILE, CI_DEPLOY_USER)

        if status in {"acl", "group"}:
            logger.info(
                "[IMP:9][converge][%s] SKIP: %s already converged (%s) — writable by root + %s",
                unit,
                AUDIT_LOG_FILE,
                status,
                CI_DEPLOY_USER,
            )
            report_add(unit, "converged", f"audit.jsonl permissions correct ({status})")
        elif dry_run or report_only:
            logger.info(
                "[IMP:9][converge][%s] WOULD fix: %s state=%s (target: acl|group, writable by root + %s)",
                unit,
                AUDIT_LOG_FILE,
                status,
                CI_DEPLOY_USER,
            )
            report_add(
                unit,
                "mutated",
                f"audit.jsonl permissions would be corrected (state={status} → writable by root + {CI_DEPLOY_USER})",
            )
            set_exit(1)
        else:
            logger.info(
                "[IMP:8][converge][%s] Fixing permissions: %s state=%s",
                unit,
                AUDIT_LOG_FILE,
                status,
            )
            applied = ensure_audit_writable(AUDIT_LOG_FILE, CI_DEPLOY_USER)
            logger.info(
                "[IMP:9][converge][%s] DONE: %s permissions corrected → %s (writable by root + %s)",
                unit,
                AUDIT_LOG_FILE,
                applied,
                CI_DEPLOY_USER,
            )
            report_add(
                unit,
                "mutated",
                f"audit.jsonl permissions corrected to {applied} (writable by root + {CI_DEPLOY_USER})",
            )
            set_exit(1)

    logger.info("[IMP:9][converge][%s] DONE: audit log reconciled", unit)
    return {
        "unit": unit,
        "status": "converged" if not infra.has_errors else "warn",
        "detail": "audit log reconciled",
    }


# endregion FUNC_reconcile_audit_log


# region FUNC_reconcile_ci_deploy_group
## @purpose  Ensure ci-deploy user is in adm group (публичный — B9 T2). Сохранено с прежнего
##           R2: adm-членство даёт ci-deploy чтение adm-логов; write-канал аудита — ACL/группа
##           (P1 fix 2026-08-27), поэтому adm-группа больше не является механизмом записи.
def reconcile_ci_deploy_group(unit: str, dry_run: bool, report_only: bool) -> None:
    """Check and fix ci-deploy adm group membership.

    If ci-deploy user does not exist yet (pre-bootstrap), logs INFO and skips.
    """
    # Check if ci-deploy user exists
    id_r = run_subprocess(["id", "-nG", CI_DEPLOY_USER], timeout=FILE_OP_TIMEOUT)
    if id_r.returncode != 0:
        logger.info(
            "[IMP:8][converge][%s] INFO: %s user does not exist yet — skipping group check",
            unit,
            CI_DEPLOY_USER,
        )
        return

    groups = id_r.stdout.strip().split()
    if "adm" in groups:
        logger.info("[IMP:7][converge][%s] OK: %s is already in adm group", unit, CI_DEPLOY_USER)
        return

    # ci-deploy exists but not in adm group
    if dry_run or report_only:
        logger.info(
            "[IMP:9][converge][%s] WOULD fix: %s not in adm group — usermod -aG adm",
            unit,
            CI_DEPLOY_USER,
        )
        report_add(unit, "mutated", f"{CI_DEPLOY_USER} would be added to adm group")
        set_exit(1)
    else:
        logger.info("[IMP:9][converge][%s] Adding %s to adm group", unit, CI_DEPLOY_USER)
        usermod_r = run_subprocess(["usermod", "-aG", "adm", CI_DEPLOY_USER], timeout=FILE_OP_TIMEOUT)
        if usermod_r.returncode == 0:
            logger.info("[IMP:9][converge][%s] DONE: %s added to adm group", unit, CI_DEPLOY_USER)
            report_add(unit, "mutated", f"{CI_DEPLOY_USER} added to adm group")
            set_exit(1)
        else:
            logger.warning(
                "[IMP:8][converge][%s] WARN: usermod failed — %s may not read adm logs: %s",
                unit,
                CI_DEPLOY_USER,
                usermod_r.stderr.strip(),
            )
            report_add(unit, "warn", f"usermod failed for {CI_DEPLOY_USER} → adm group")


# endregion FUNC_reconcile_ci_deploy_group
