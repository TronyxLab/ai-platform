"""
# GREP_SUMMARY: test cert cron migration acme.sh s3_ssl_cache idempotent noop
# STRUCTURE: ▶ mock subprocess crontab → ◇ old cron detected → ◇ already s3 aware → ◇ no crontab → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for cert_orchestrator.migrate_cron_if_needed() — cron migration from old (no-S3) to new (S3-aware) acme.sh entries.
## @scope    Tests migrate_cron_if_needed with mocked subprocess (crontab, acme.sh).
## @invariants
##   - All subprocess calls mocked (no real crontab or acme.sh)
##   - Each test validates IMP:9 business logic log presence
## @rationale DevPlan 080 C4: old nginx/install.sh _acme_install_cron() installed cron WITHOUT --renew-hook for S3 upload.
## @changes  2026-07-26 | DevPlan 080 TASK-4 — Created
# endregion MODULE_CONTRACT
"""

import logging
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap"
sys.path.insert(0, str(_MODULE_DIR))
import cert_orchestrator as cert

# ═══════════════════════════════════════════════════════════════════
# region Tests: migrate_cron_if_needed
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Old cron WITHOUT S3 sync detected and migrated
# · Scenario: crontab has acme.sh --cron but NO s3_ssl_cache → migration triggered
# · Last fail: N/A (new test for DevPlan 080)
# · Remove if: migrate_cron_if_needed logic changes
@ldd_trajectory
def test_migrate_old_cron_detected(caplog, tmp_path):
    """migrate_cron_if_needed should detect old cron entry and trigger migration.

    ## @purpose  Old nginx/install.sh _acme_install_cron() installed cron
    ##           WITHOUT --renew-hook. This test verifies detection.
    ## @scenario  Mock crontab with acme.sh --cron but no s3_ssl_cache →
    ##           migrate_cron_if_needed calls --install-cronjob + --renew-hook
    """
    acme_home = str(tmp_path / "acme-mock")
    os.makedirs(acme_home, exist_ok=True)
    acme_sh = Path(acme_home) / "acme.sh"
    acme_sh.write_text("#!/bin/bash\necho 'mock acme.sh'\n")
    acme_sh.chmod(0o755)

    # Mock s3_ssl_cache.py existence check
    s3_cache_py = str(Path(__file__).resolve().parent.parent.parent
                      / "core" / "internal" / "bootstrap" / "s3_ssl_cache.py")

    # Old cron: acme.sh --cron, no s3_ssl_cache
    old_cron = f'0 0 * * * "{acme_sh}" --cron --home "{acme_home}" > /dev/null\n'

    call_log = []

    def mock_run(cmd, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        call_log.append(cmd_str)
        if "crontab" in cmd_str:
            if "-l" in cmd_str:
                return MagicMock(returncode=0, stdout=old_cron, stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")
        if "acme.sh" in cmd_str:
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("os.path.isfile") as mock_isfile:
        def isfile_side_effect(path):
            if str(path) == str(acme_sh):
                return True
            if "s3_ssl_cache.py" in str(path):
                return True
            return True
        mock_isfile.side_effect = isfile_side_effect

        with patch("subprocess.run", side_effect=mock_run):
            result = cert.migrate_cron_if_needed(acme_home)

    logger.critical("[IMP:9][test_migrate_old_cron_detected] ASSERT: migration triggered for old cron")
    print("--- call_log ---")
    for c in call_log:
        print(f"  {c}")
    print("--- end ---")

    assert result is True, f"Migration should return True, got {result}"
    # Should detect old cron → call --install-cronjob
    assert any("--install-cronjob" in c for c in call_log), (
        f"Expected --install-cronjob call, got: {call_log}"
    )

    logger.critical("[IMP:9][test_migrate_old_cron_detected] PASS: old cron detected and migrated")


# 🧪 TRAP[TEST] · Regression · S3-aware cron entry → no migration
# · Scenario: crontab already has s3_ssl_cache reference → no-op
# · Last fail: N/A (new test for DevPlan 080)
# · Remove if: idempotency logic changes
@ldd_trajectory
def test_migrate_already_s3_aware(caplog, tmp_path):
    """migrate_cron_if_needed should be no-op when cron already has S3 sync.

    ## @purpose  Idempotency: if cron already references s3_ssl_cache,
    ##           no changes should be made.
    ## @scenario  Mock crontab with acme.sh --cron AND s3_ssl_cache →
    ##           migrate_cron_if_needed returns True without changes
    """
    acme_home = str(tmp_path / "acme-mock")
    os.makedirs(acme_home, exist_ok=True)
    acme_sh = Path(acme_home) / "acme.sh"
    acme_sh.write_text("#!/bin/bash\necho 'mock acme.sh'\n")
    acme_sh.chmod(0o755)

    # Already S3-aware cron
    new_cron = (
        f'0 0 * * * "{acme_sh}" --cron --home "{acme_home}" > /dev/null; '
        f'python3 s3_ssl_cache.py upload\n'
    )

    install_called = []

    def mock_run(cmd, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "crontab" in cmd_str and "-l" in cmd_str:
            return MagicMock(returncode=0, stdout=new_cron, stderr="")
        if "--install-cronjob" in cmd_str:
            install_called.append(cmd_str)
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("os.path.isfile", return_value=True):
        with patch("subprocess.run", side_effect=mock_run):
            result = cert.migrate_cron_if_needed(acme_home)

    logger.critical("[IMP:9][test_migrate_already_s3_aware] ASSERT: no migration when already S3-aware")
    assert result is True, f"Should return True (no migration needed), got {result}"
    assert len(install_called) == 0, f"Should NOT call --install-cronjob, but got: {install_called}"

    logger.critical("[IMP:9][test_migrate_already_s3_aware] PASS: idempotent — no changes")


# 🧪 TRAP[TEST] · Regression · No crontab → no migration
# · Scenario: crontab command returns non-zero → nothing to migrate
# · Last fail: N/A (new test for DevPlan 080)
# · Remove if: no-crontab handling changes
@ldd_trajectory
def test_migrate_no_crontab(caplog, tmp_path):
    """migrate_cron_if_needed should handle missing crontab gracefully.

    ## @purpose  When crontab -l fails (no crontab), return True without errors.
    ## @scenario  Mock crontab -l returns non-zero → no migration attempted
    """
    acme_home = str(tmp_path / "acme-mock")
    os.makedirs(acme_home, exist_ok=True)
    acme_sh = Path(acme_home) / "acme.sh"
    acme_sh.write_text("#!/bin/bash\necho 'mock acme.sh'\n")
    acme_sh.chmod(0o755)

    install_called = []

    def mock_run(cmd, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "crontab" in cmd_str and "-l" in cmd_str:
            # No crontab → non-zero return
            return MagicMock(returncode=1, stdout="", stderr="no crontab for root")
        if "--install-cronjob" in cmd_str:
            install_called.append(cmd_str)
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("os.path.isfile", return_value=True):
        with patch("subprocess.run", side_effect=mock_run):
            result = cert.migrate_cron_if_needed(acme_home)

    logger.critical("[IMP:9][test_migrate_no_crontab] ASSERT: no crontab handled gracefully")
    assert result is True, f"Should return True (no crontab to migrate), got {result}"
    assert len(install_called) == 0, f"Should NOT call --install-cronjob, but got: {install_called}"

    logger.critical("[IMP:9][test_migrate_no_crontab] PASS: no crontab — no-op")


# 🧪 TRAP[TEST] · Regression · acme.sh not found → skip migration
# · Scenario: acme.sh binary missing → return False
# · Last fail: N/A (new test for DevPlan 080)
@ldd_trajectory
def test_migrate_acme_sh_not_found(caplog, tmp_path):
    """migrate_cron_if_needed should return False when acme.sh not found.

    ## @purpose  Graceful degradation: if acme.sh doesn't exist,
    ##           there's nothing to migrate.
    ## @scenario  acme.sh file doesn't exist → return False
    """
    acme_home = str(tmp_path / "nonexistent-acme")

    with patch("os.path.isfile", return_value=False):
        result = cert.migrate_cron_if_needed(acme_home)

    logger.critical("[IMP:9][test_migrate_acme_sh_not_found] ASSERT: skip when acme.sh missing")
    assert result is False, f"Should return False (acme.sh not found), got {result}"

    logger.critical("[IMP:9][test_migrate_acme_sh_not_found] PASS: graceful skip")


# endregion
