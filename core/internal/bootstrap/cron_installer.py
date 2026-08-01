#!/usr/bin/env python3
# GREP_SUMMARY: cron-installer acme.sh cronjob renew-hook s3-upload migrate cron crontab idempotent
# STRUCTURE: ▶ install_acme_cron → ◇ acme.sh exists? → ◇ crontab already has s3? → ⊕ --install-cronjob → ⊕ --renew-hook → ⎋ bool
#           ▶ migrate_acme_cron_if_needed → ◇ acme.sh exists? → ◇ crontab? → ◇ has s3? → ◇ has acme? → ⊕ reinstall + renew-hook → ⎋ bool
# region MODULE_CONTRACT
## @purpose  acme.sh cron job management extracted from cert_orchestrator.py (DevPlan 117 G T58.4).
##           Installs acme.sh --install-cronjob with an S3-upload --renew-hook, and migrates
##           legacy no-S3 cron entries from the deleted nginx/install.sh (DRIFT-C4).
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
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import subprocess

logger = logging.getLogger(__name__)


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

    try:
        # Check if cron already has S3-aware entry
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and "s3_ssl_cache" in result.stdout:
            logger.info("[IMP:8][cert_orchestrator] Cron already has S3 sync — skipping install")
            return True

        # ── Install cronjob ──
        logger.info("[IMP:8][cert_orchestrator] Installing acme.sh cronjob")
        subprocess.run(
            [acme_sh, "--install-cronjob", "--home", acme_home],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )

        # ── Install renew-hook for S3 upload ──
        s3_cache_py = os.path.join(os.path.dirname(__file__), "s3_ssl_cache.py")
        if os.path.isfile(s3_cache_py):
            subprocess.run(
                [acme_sh, "--renew-hook", f"python3 '{s3_cache_py}' upload \"$Le_Domain\""],
                capture_output=True,
                text=True,
                timeout=30,
            )
            logger.info("[IMP:9][cert_orchestrator] Cron installed with S3 sync renew-hook")
        else:
            logger.warning("[IMP:7][cert_orchestrator] s3_ssl_cache.py not found — --renew-hook skipped")
        return True

    except (subprocess.CalledProcessError, OSError, FileNotFoundError) as e:
        logger.warning("[IMP:7][cert_orchestrator] Cron install failed: %s", e)
        return False


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

    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            logger.info("[IMP:8][cron_migrate] No crontab — nothing to migrate")
            return True  # No crontab = nothing to fix

        cron_content = result.stdout
        if "s3_ssl_cache" in cron_content:
            logger.info("[IMP:8][cron_migrate] Cron already has S3 sync — no migration needed")
            return True

        if acme_sh not in cron_content or "--cron" not in cron_content:
            logger.info("[IMP:8][cron_migrate] No acme.sh cron entry — nothing to migrate")
            return True

        # ── Old entry found — reinstall cron ──
        logger.warning("[IMP:8][cron_migrate] Old acme.sh cron without S3 sync — migrating")
        subprocess.run(
            [acme_sh, "--install-cronjob", "--home", acme_home],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )

        # Install renew-hook for S3
        s3_cache_py = os.path.join(os.path.dirname(__file__), "s3_ssl_cache.py")
        if os.path.isfile(s3_cache_py):
            subprocess.run(
                [acme_sh, "--renew-hook", f"python3 '{s3_cache_py}' upload \"$Le_Domain\""],
                capture_output=True,
                text=True,
                timeout=30,
            )
            logger.info("[IMP:9][cron_migrate] Cron migration complete — S3 sync enabled")
        else:
            logger.warning("[IMP:7][cron_migrate] s3_ssl_cache.py not found — --renew-hook skipped")
        return True

    except (subprocess.CalledProcessError, OSError, FileNotFoundError) as e:
        logger.warning("[IMP:7][cron_migrate] Migration failed: %s", e)
        return False


# endregion FUNC_migrate_acme_cron_if_needed
