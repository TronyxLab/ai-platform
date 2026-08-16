# GREP_SUMMARY: test-cron-installer install_acme_cron migrate_acme_cron_if_needed crontab s3 renew-hook
# STRUCTURE: ┌8 test functions┐ → ◇ install_acme_cron (4) → ◇ migrate_acme_cron_if_needed (4)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/bootstrap/cron_installer.py — acme.sh cron install +
##           S3-aware migration (DevPlan 117 G T58.4 extraction from cert_orchestrator.py).
## @scope    No real crontab/acme.sh — all subprocess calls mocked.
## @invariants
##   - All subprocess calls mocked
##   - Branch coverage target: ≥80% (AC-G3)
## @rationale  DevPlan 117 G T58.4 §TEST_SPEC — cron_installer direct tests after extraction.
## @changes  2026-08-01 · DevPlan 117 G T58.4 — created
# endregion MODULE_CONTRACT

import logging
from pathlib import Path
from unittest import mock

import pytest

from core.internal.bootstrap.cron_installer import (
    install_acme_cron,
    migrate_acme_cron_if_needed,
)

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


def _assert_log_event(caplog, *, levelno: int, imp: int, keyword: str) -> None:
    """Структурная проверка лог-события: severity + IMP-код + факт (DevPlan 139 W2).

    ## @purpose — Замена exact-string ассертов: устойчивость к правкам формулировок,
    ##            детекция семантических поломок через severity/IMP-код.
    ## @io — ⇥ caplog; levelno, imp, keyword → ⎋ None (assert)
    """
    assert any(r.levelno == levelno and f"[IMP:{imp}]" in r.message and keyword in r.message for r in caplog.records), (
        f"Лог-событие не найдено: levelno={levelno} [IMP:{imp}] keyword={keyword!r}\n---\n{caplog.text}"
    )


def _print_trajectory(caplog) -> bool:
    """LDD-траектория IMP:7-10; возвращает True при наличии IMP:9 (Anti-Illusion, без assert)."""
    found = False
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                logger.info("%s", record.message)
            if imp_level >= 9:
                found = True
    logger.info("--- END LDD TRAJECTORY ---")
    return found


def _mk_acme_sh(tmp_path: Path, subdir: str = "") -> Path:
    """Create a fake acme.sh binary in tmp_path and return it."""
    home = tmp_path / subdir if subdir else tmp_path
    home.mkdir(parents=True, exist_ok=True)
    acme_sh = home / "acme.sh"
    acme_sh.write_text("#!/bin/bash\necho mock\n")
    acme_sh.chmod(0o755)
    return acme_sh


# ══════════════════════════════════════════════════════════════════════
# TESTS: install_acme_cron
# ══════════════════════════════════════════════════════════════════════


class TestInstallAcmeCron:
    """Tests for install_acme_cron()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: crontab already has s3_ssl_cache
    # · Expect: True (no-op), no --install-cronjob call
    # · Last fail: None (new test for DevPlan 117 G T58.4)
    # · Remove if: install idempotency logic changes
    def test_install_cron_already_present(self, tmp_path: Path, caplog) -> None:
        """Crontab already contains s3_ssl_cache → True (no-op)."""
        caplog.set_level(0)
        acme_sh = _mk_acme_sh(tmp_path)
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return mock.MagicMock(
                returncode=0, stdout=f'0 0 * * * "{acme_sh}" --cron --renew-hook "python3 s3_ssl_cache.py"\n', stderr=""
            )

        with (
            mock.patch("core.internal.bootstrap.cron_installer.os.path.isfile", return_value=True),
            mock.patch("core.internal.bootstrap.cron_installer.subprocess.run", side_effect=mock_run),
        ):
            result = install_acme_cron(str(tmp_path))

        assert result is True
        install_calls = [c for c in calls if "--install-cronjob" in c]
        assert len(install_calls) == 0

    # 🧪 TRAP[TEST] · Regression · Scenario: fresh install with s3_ssl_cache.py present
    # · Expect: --install-cronjob + --renew-hook called, True
    # · Last fail: None (new test for DevPlan 117 G T58.4)
    # · Remove if: install logic changes
    def test_install_cron_fresh(self, tmp_path: Path, caplog) -> None:
        """Fresh install → install-cronjob + renew-hook, True."""
        caplog.set_level(0)
        acme_sh = _mk_acme_sh(tmp_path)
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return mock.MagicMock(returncode=0, stdout="", stderr="")

        def isfile(path):
            return str(path).endswith("s3_ssl_cache.py") or str(path) == str(acme_sh)

        with (
            mock.patch("core.internal.bootstrap.cron_installer.os.path.isfile", side_effect=isfile),
            mock.patch("core.internal.bootstrap.cron_installer.subprocess.run", side_effect=mock_run),
        ):
            result = install_acme_cron(str(tmp_path))

        assert result is True
        assert any("--install-cronjob" in c for c in calls)
        assert any("--renew-hook" in c for c in calls)
        _assert_log_event(caplog, levelno=logging.INFO, imp=9, keyword="Cron installed")
        assert _print_trajectory(caplog), "LDD: нет IMP:9-лога в успешном сценарии"

    # 🧪 TRAP[TEST] · Regression · Scenario: acme.sh missing
    # · Expect: False (no cron without binary)
    # · Last fail: None (new test for DevPlan 117 G T58.4)
    # · Remove if: acme.sh-missing handling changes
    def test_install_cron_no_acme_sh(self, tmp_path: Path, caplog) -> None:
        """acme.sh not found → False."""
        caplog.set_level(0)
        with (
            mock.patch("core.internal.bootstrap.cron_installer.os.path.isfile", return_value=False),
            mock.patch("core.internal.bootstrap.cron_installer.subprocess.run") as mock_run,
        ):
            result = install_acme_cron(str(tmp_path))

        assert result is False
        mock_run.assert_not_called()

    # 🧪 TRAP[TEST] · Regression · Scenario: subprocess raises (crontab access denied)
    # · Expect: False (non-fatal WARN)
    # · Last fail: None (new test for DevPlan 117 G T58.4)
    # · Remove if: failure handling changes
    def test_install_cron_subprocess_error(self, tmp_path: Path, caplog) -> None:
        """subprocess.CalledProcessError → False."""
        caplog.set_level(0)
        _mk_acme_sh(tmp_path)

        with (
            mock.patch("core.internal.bootstrap.cron_installer.os.path.isfile", return_value=True),
            mock.patch(
                "core.internal.bootstrap.cron_installer.subprocess.run",
                side_effect=__import__("subprocess").CalledProcessError(1, "crontab"),
            ),
        ):
            result = install_acme_cron(str(tmp_path))

        assert result is False
        _assert_log_event(caplog, levelno=logging.WARNING, imp=7, keyword="Cron install failed")
        _print_trajectory(caplog)


# ══════════════════════════════════════════════════════════════════════
# TESTS: migrate_acme_cron_if_needed
# ══════════════════════════════════════════════════════════════════════


class TestMigrateCron:
    """Tests for migrate_acme_cron_if_needed()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: crontab has S3 sync already
    # · Expect: True, no reinstall
    # · Last fail: None (new test for DevPlan 117 G T58.4)
    # · Remove if: migration idempotency logic changes
    def test_migrate_cron_already_s3(self, tmp_path: Path, caplog) -> None:
        """Crontab already has s3_ssl_cache → True (no migration)."""
        caplog.set_level(0)
        _mk_acme_sh(tmp_path)
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return mock.MagicMock(returncode=0, stdout="s3_ssl_cache upload\n", stderr="")

        with (
            mock.patch("core.internal.bootstrap.cron_installer.os.path.isfile", return_value=True),
            mock.patch("core.internal.bootstrap.cron_installer.subprocess.run", side_effect=mock_run),
        ):
            result = migrate_acme_cron_if_needed(str(tmp_path))

        assert result is True
        assert not any("--install-cronjob" in c for c in calls)

    # 🧪 TRAP[TEST] · Regression · Scenario: no crontab at all
    # · Expect: True (nothing to migrate)
    # · Last fail: None (new test for DevPlan 117 G T58.4)
    # · Remove if: no-crontab handling changes
    def test_migrate_cron_no_crontab(self, tmp_path: Path, caplog) -> None:
        """crontab -l fails → True (nothing to migrate)."""
        caplog.set_level(0)
        _mk_acme_sh(tmp_path)

        with (
            mock.patch("core.internal.bootstrap.cron_installer.os.path.isfile", return_value=True),
            mock.patch(
                "core.internal.bootstrap.cron_installer.subprocess.run",
                return_value=mock.MagicMock(returncode=1, stdout="", stderr=""),
            ) as mock_run,
        ):
            result = migrate_acme_cron_if_needed(str(tmp_path))

        assert result is True
        install_calls = [c for c in mock_run.call_args_list if "--install-cronjob" in c.args[0]]
        assert len(install_calls) == 0

    # 🧪 TRAP[TEST] · Regression · Scenario: old acme cron (no S3) detected → migrate
    # · Expect: reinstall + renew-hook, True, IMP:9 log
    # · Last fail: None (new test for DevPlan 117 G T58.4)
    # · Remove if: migration flow changes
    def test_migrate_cron_old_entry(self, tmp_path: Path, caplog) -> None:
        """Old acme.sh --cron without s3_ssl_cache → reinstalled with renew-hook."""
        caplog.set_level(0)
        acme_sh = _mk_acme_sh(tmp_path)
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return mock.MagicMock(returncode=0, stdout=f'0 0 * * * "{acme_sh}" --cron --home "{tmp_path}"\n', stderr="")

        def isfile(path):
            return str(path).endswith("s3_ssl_cache.py") or str(path) == str(acme_sh)

        with (
            mock.patch("core.internal.bootstrap.cron_installer.os.path.isfile", side_effect=isfile),
            mock.patch("core.internal.bootstrap.cron_installer.subprocess.run", side_effect=mock_run),
        ):
            result = migrate_acme_cron_if_needed(str(tmp_path))

        assert result is True
        assert any("--install-cronjob" in c for c in calls)
        assert any("--renew-hook" in c for c in calls)
        _assert_log_event(caplog, levelno=logging.INFO, imp=9, keyword="Cron migration complete")
        assert _print_trajectory(caplog), "LDD: нет IMP:9-лога в успешном сценарии"

    # 🧪 TRAP[TEST] · Regression · Scenario: no acme.sh cron entry at all
    # · Expect: True (nothing to migrate)
    # · Last fail: None (new test for DevPlan 117 G T58.4)
    # · Remove if: no-acme-entry handling changes
    def test_migrate_cron_no_acme_entry(self, tmp_path: Path, caplog) -> None:
        """Crontab exists but no acme.sh --cron → True (nothing to migrate)."""
        caplog.set_level(0)
        _mk_acme_sh(tmp_path)

        with (
            mock.patch("core.internal.bootstrap.cron_installer.os.path.isfile", return_value=True),
            mock.patch(
                "core.internal.bootstrap.cron_installer.subprocess.run",
                return_value=mock.MagicMock(returncode=0, stdout="0 3 * * * /usr/bin/backup\n", stderr=""),
            ) as mock_run,
        ):
            result = migrate_acme_cron_if_needed(str(tmp_path))

        assert result is True
        install_calls = [c for c in mock_run.call_args_list if "--install-cronjob" in c.args[0]]
        assert len(install_calls) == 0
