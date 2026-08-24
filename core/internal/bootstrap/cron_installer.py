#!/usr/bin/env python3
# GREP_SUMMARY: cron-installer acme.sh cronjob renew-hook s3-upload migrate cron crontab idempotent
# STRUCTURE: ▶ install_acme_cron → ◇ acme.sh exists? → ◇ crontab already has s3? → ⊕ --install-cronjob → ⊕ --renew-hook → ⎋ bool
#           ▶ migrate_acme_cron_if_needed → ◇ acme.sh exists? → ◇ crontab? → ◇ has s3? → ◇ has acme? → ⊕ reinstall + renew-hook → ⎋ bool
# region MODULE_CONTRACT
## @purpose  acme.sh cron job management extracted from cert_orchestrator.py (DevPlan 117 G T58.4).
##           Installs acme.sh --install-cronjob with an S3-upload --renew-hook, and migrates
##           no-S3 cron entries from the deleted nginx/install.sh (DRIFT-C4).
## @scope    Consumed by core/internal/bootstrap/cert_orchestrator.py (lazy import). Both functions
##           are idempotent and non-fatal (WARN + False on failure).
## @invariants
##   - Idempotent: crontab entry already carrying s3_ssl_cache → no-op
##   - Non-fatal: failure logs WARN, returns False; no crontab → True (nothing to migrate)
##   - acme.sh missing → False (cron cannot be installed without the binary)
##   - --renew-hook references s3_ssl_cache.py in the same directory as cert_orchestrator
## @rationale  DevPlan 117 G T58.4 — extracted verbatim (_install_cron + migrate_cron_if_needed,
##            ~136 LOC) with all LDD logs and docstrings preserved — no behavior change (AC-G7).
## @changes  2026-08-01 · DevPlan 117 G T58.4 — extracted from cert_orchestrator.py
##           2026-08-24 | REF-0008 В2 — s3_ssl_cache-путь renew-hook через shlex.quote (без ручных кавычек)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import pathlib
import shlex
import subprocess

logger = logging.getLogger(__name__)

# W1-A1 (план 170): литералы таймаутов → канон SoT (AMBER-зачистка research-D §D1).
# 30 (acme install-cronjob / renew-hook) → CONVERGE_DOCKER_TIMEOUT.
from core.internal.shared.timeouts import CONVERGE_DOCKER_TIMEOUT

# CRONTAB_LIST_TIMEOUT=5 — уникальное значение (crontab -l, вне SoT-набора {10,15,30,60,120,180,300,600}):
# локальная команда crontab -l выполняется мгновенно, 5s достаточно. Модульная константа с TRAP.
# 🧐 TRAP[DECISION] · 2026-08-14 · — · CRONTAB_LIST_TIMEOUT=5 — уникальное значение crontab-домена
# · Rejected: канонизация в shared/timeouts · Reason: значение 5 вне SoT-набора (правило п.12:
# ·   новые SoT-константы только при ≥3 повторах — 5 встречается 2 раза в одном модуле); DOCKER_CMD_TIMEOUT=10
# ·   избыточен для мгновенного crontab -l и изменил бы поведение
# · Rev: при появлении второго потребителя значения 5 — канонизировать в shared/timeouts
CRONTAB_LIST_TIMEOUT = 5


# region FUNC_install_acme_cron
## @purpose — Install acme.sh --install-cronjob + --renew-hook with S3 upload.
##            Called after any certs are processed (restored, issued, or skipped).
##            Idempotent: checks crontab first, skips if already present.
## @io — ⇥ acme_home: str → ⎋ bool (True = cron installed or already present)
## @complexity — O(1)
## @invariants
##   - Includes --renew-hook to upload certs to S3 after each renewal
##   - Non-fatal: failure logs WARN, returns False
##   - Idempotent: no-op if cron entry already has s3_ssl_cache reference
def install_acme_cron(acme_home: str = "/opt/acme.sh") -> bool:
    """Install acme.sh --install-cronjob + --renew-hook with S3 upload.

    ▶ ┌acme.sh exists?┐ → ◇ already installed? → no → ⚡ --install-cronjob → --renew-hook → ⎋
    """
    acme_sh = os.path.join(acme_home, "acme.sh")
    if not os.path.isfile(acme_sh):
        logger.info("[IMP:7][cert_orchestrator] acme.sh not found at %s — skipping cron install", acme_sh)
        return False

    # ruff: ignore[PLW0717] — try-тело содержит return-ветки с fall-through (после-try код) — извлечение небезопасно
    try:
        # Check if cron already has S3-aware entry
        result = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=CRONTAB_LIST_TIMEOUT, check=False
        )
        if result.returncode == 0 and "s3_ssl_cache" in result.stdout:
            logger.info("[IMP:8][cert_orchestrator] Cron already has S3 sync — skipping install")
            return True
        logger.info("[IMP:8][cert_orchestrator] Installing acme.sh cronjob")
        subprocess.run(
            [acme_sh, "--install-cronjob", "--home", acme_home],
            capture_output=True,
            text=True,
            timeout=CONVERGE_DOCKER_TIMEOUT,
            check=True,
        )
        s3_cache_py = os.path.join(pathlib.Path(__file__).parent, "s3_ssl_cache.py")
        if os.path.isfile(s3_cache_py):
            subprocess.run(
                # REF-0008: путь — shlex.quote (без ручной интерполяции кавычек)
                [acme_sh, "--renew-hook", f'python3 {shlex.quote(s3_cache_py)} upload "$Le_Domain"'],
                capture_output=True,
                text=True,
                timeout=CONVERGE_DOCKER_TIMEOUT,
                check=False,
            )
            logger.info("[IMP:9][cert_orchestrator] Cron installed with S3 sync renew-hook")
        else:
            logger.warning("[IMP:7][cert_orchestrator] s3_ssl_cache.py not found — --renew-hook skipped")

    except (subprocess.CalledProcessError, OSError, FileNotFoundError) as e:
        logger.warning("[IMP:7][cert_orchestrator] Cron install failed: %s", e)
        return False
    else:
        return True


# endregion FUNC_install_acme_cron


# region FUNC_migrate_acme_cron_if_needed
## @purpose — Detect and fix old (no-S3) acme.sh cron entries from deleted nginx/install.sh.
##            The old _acme_install_cron() installed cron WITHOUT --renew-hook for S3 upload.
##            This function detects the old entry and reinstalls cron with --renew-hook.
## @io — ⇥ acme_home: str → ⎋ bool (True = migration succeeded or was not needed)
## @complexity — O(1) + crontab subprocess
## @invariants
##   - Idempotent: if cron already has s3_ssl_cache reference, skips
##   - Non-fatal: failure logs WARN, returns False
##   - Non-fatal: no crontab → returns True (nothing to migrate)
##   - Runs on bootstrap init (step_18_deploy_context) and update
## @rationale DRIFT-C4: old nginx/install.sh _acme_install_cron() installed
##            cron WITHOUT --renew-hook for S3 upload. This function detects
##            the old entry and reinstalls cron with --renew-hook.
def migrate_acme_cron_if_needed(acme_home: str = "/opt/acme.sh") -> bool:
    """Check crontab for old acme.sh entry (no S3 sync) → replace with new one.

    ▶ ┌crontab -l┐ → ◇ grep acme.sh --cron → ◇ grep -v s3_ssl_cache → ◇ found?
    → ⚡ --install-cronjob + --renew-hook → ⎋ return True
    """
    acme_sh = os.path.join(acme_home, "acme.sh")
    if not os.path.isfile(acme_sh):
        logger.info("[IMP:7][cron_migrate] acme.sh not found — skipping cron migration")
        return False

    # ruff: ignore[PLW0717] — try-тело содержит return-ветки с fall-through (после-try код) — извлечение небезопасно
    try:
        result = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=CRONTAB_LIST_TIMEOUT, check=False
        )
        if result.returncode != 0:
            logger.info("[IMP:8][cron_migrate] No crontab — nothing to migrate")
            return True
        cron_content = result.stdout
        if "s3_ssl_cache" in cron_content:
            logger.info("[IMP:8][cron_migrate] Cron already has S3 sync — no migration needed")
            return True
        if acme_sh not in cron_content or "--cron" not in cron_content:
            logger.info("[IMP:8][cron_migrate] No acme.sh cron entry — nothing to migrate")
            return True
        logger.warning("[IMP:8][cron_migrate] Old acme.sh cron without S3 sync — migrating")
        subprocess.run(
            [acme_sh, "--install-cronjob", "--home", acme_home],
            capture_output=True,
            text=True,
            timeout=CONVERGE_DOCKER_TIMEOUT,
            check=True,
        )
        s3_cache_py = os.path.join(pathlib.Path(__file__).parent, "s3_ssl_cache.py")
        if os.path.isfile(s3_cache_py):
            subprocess.run(
                # REF-0008: путь — shlex.quote (без ручной интерполяции кавычек)
                [acme_sh, "--renew-hook", f'python3 {shlex.quote(s3_cache_py)} upload "$Le_Domain"'],
                capture_output=True,
                text=True,
                timeout=CONVERGE_DOCKER_TIMEOUT,
                check=False,
            )
            logger.info("[IMP:9][cron_migrate] Cron migration complete — S3 sync enabled")
        else:
            logger.warning("[IMP:7][cron_migrate] s3_ssl_cache.py not found — --renew-hook skipped")

    except (subprocess.CalledProcessError, OSError, FileNotFoundError) as e:
        logger.warning("[IMP:7][cron_migrate] Migration failed: %s", e)
        return False
    else:
        return True


# endregion FUNC_migrate_acme_cron_if_needed
