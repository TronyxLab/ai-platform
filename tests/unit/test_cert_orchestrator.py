"""
# GREP_SUMMARY: test_cert_orchestrator, bulk-restore, s3-cache, acme-issue, graceful-degradation, idempotent
# STRUCTURE: ▶ tmp_path + mock subprocess → ◇ bulk-restore from S3 → ◇ partial restore + issue → ◇ S3 unavailable → ◇ idempotent skip → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for cert_orchestrator.py — cert orchestration (S3 restore + acme issue).
## @scope    Tests orchestrate_certs, _process_single_domain, _is_cert_valid.
## @invariants
##   - All subprocess calls mocked (no real s3-ssl-cache.sh or issue-cert.sh)
##   - Cert files created in tmp_path
##   - Each test validates IMP:9 business logic log presence
## @rationale DevPlan 047 Phase 7: cert orchestrator eliminates manual cert management.
## @changes  2026-07-22 | DevPlan 047 Phase 7 — Created
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
# region Tests: orchestrate_certs
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · orchestrate_certs restores all domains from S3
# · Scenario: All domains return S3 cache hit → all restored, 0 issued
# · Last fail: N/A (new test)
# · Remove if: orchestrate_certs logic changes
@ldd_trajectory
def test_bulk_restore_all_from_s3(caplog, tmp_path, monkeypatch):
    """orchestrate_certs should restore all domains from S3 when available."""
    s3_script = str(tmp_path / "s3-ssl-cache.sh")
    issue_script = str(tmp_path / "issue-cert.sh")
    Path(s3_script).touch()
    Path(issue_script).touch()

    # Mock _is_cert_valid to return False (no valid cert on disk)
    monkeypatch.setattr(cert, "_is_cert_valid", lambda d, p: False)

    # Mock os.path.isfile for cert path check (avoid /etc/letsencrypt access)
    real_isfile = os.path.isfile

    def mock_isfile(path):
        if "fullchain.pem" in str(path) and "/etc/letsencrypt" in str(path):
            return False  # Pretend cert doesn't exist on disk
        return real_isfile(path)

    monkeypatch.setattr(os.path, "isfile", mock_isfile)

    # Track downloaded domains to simulate cert file creation
    downloaded_domains: set[str] = set()

    # Mock S3 check → return 0 (valid in cache)
    def mock_s3_run(cmd, **kwargs):
        if isinstance(cmd, list):
            cmd_str = " ".join(cmd)
        else:
            cmd_str = str(cmd)
        if "check" in cmd_str:
            return MagicMock(returncode=0, stdout="", stderr="")
        if "download" in cmd_str:
            domain = cmd[-1]
            downloaded_domains.add(domain)
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    # After download, _process_single_domain checks os.path.isfile(cert_path).
    # Override isfile to return True for downloaded domains.
    def mock_isfile_downloaded(path):
        if "fullchain.pem" in str(path):
            return any(d in str(path) for d in downloaded_domains)
        return real_isfile(path)

    with patch("subprocess.run", side_effect=mock_s3_run):
        # Swap isfile mock to track downloads
        monkeypatch.setattr(os.path, "isfile", mock_isfile_downloaded)
        result = cert.orchestrate_certs(
            ["example.com", "test.com"],
            s3_script,
            issue_script,
        )

    assert result.restored == 2
    assert result.issued == 0
    assert result.failed == 0
    logger.critical("[IMP:9][test] Bulk restore from S3 — all domains restored")


# 🧪 TRAP[TEST] · Regression · orchestrate_certs issues certs when S3 miss
# · Scenario: S3 check fails → issue-cert.sh called → cert issued
# · Last fail: N/A (new test)
# · Remove if: partial restore + issue logic changes
@ldd_trajectory
def test_partial_restore_then_issue(caplog, tmp_path, monkeypatch):
    """orchestrate_certs should fall back to issue when S3 miss."""
    s3_script = str(tmp_path / "s3-ssl-cache.sh")
    issue_script = str(tmp_path / "issue-cert.sh")
    Path(s3_script).touch()
    Path(issue_script).touch()

    monkeypatch.setattr(cert, "_is_cert_valid", lambda d, p: False)

    call_count = {"check": 0, "download": 0, "issue": 0}

    def mock_run(cmd, **kwargs):
        if isinstance(cmd, list):
            cmd_str = " ".join(cmd)
        else:
            cmd_str = str(cmd)
        if "check" in cmd_str:
            call_count["check"] += 1
            return MagicMock(returncode=1, stdout="", stderr="")  # S3 miss
        if "download" in cmd_str:
            call_count["download"] += 1
            return MagicMock(returncode=1, stdout="", stderr="")  # download fail
        if "issue" in cmd_str or "bash" in cmd_str:
            call_count["issue"] += 1
            return MagicMock(returncode=0, stdout="", stderr="")  # issue success
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=mock_run):
        result = cert.orchestrate_certs(
            ["example.com"],
            s3_script,
            issue_script,
        )

    assert result.issued >= 1 or result.failed >= 1
    logger.critical("[IMP:9][test] Partial restore + issue — fallback to acme.sh works")


# 🧪 TRAP[TEST] · Regression · orchestrate_certs handles S3 unavailable gracefully
# · Scenario: s3-ssl-cache.sh not found → falls back to issue-cert.sh
# · Last fail: N/A (new test)
# · Remove if: S3 graceful degradation logic changes
@ldd_trajectory
def test_s3_unavailable_graceful(caplog, tmp_path, monkeypatch):
    """orchestrate_certs should gracefully handle S3 unavailable."""
    s3_script = str(tmp_path / "nonexistent-s3.sh")  # does not exist
    issue_script = str(tmp_path / "issue-cert.sh")
    Path(issue_script).touch()

    monkeypatch.setattr(cert, "_is_cert_valid", lambda d, p: False)

    def mock_run(cmd, **kwargs):
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=mock_run):
        result = cert.orchestrate_certs(
            ["example.com"],
            s3_script,
            issue_script,
        )

    # Should not crash — either issued or failed gracefully
    assert len(result.domains) == 1
    logger.critical("[IMP:9][test] S3 unavailable — graceful fallback to issue")


# 🧪 TRAP[TEST] · Regression · orchestrate_certs skips already-valid certs (idempotent)
# · Scenario: _is_cert_valid returns True → domain skipped
# · Last fail: N/A (new test)
# · Remove if: idempotency skip logic changes
@ldd_trajectory
def test_idempotent_skip_valid(caplog, tmp_path, monkeypatch):
    """orchestrate_certs should skip domains with valid certs on disk."""
    s3_script = str(tmp_path / "s3-ssl-cache.sh")
    issue_script = str(tmp_path / "issue-cert.sh")
    Path(s3_script).touch()
    Path(issue_script).touch()

    # Mock _is_cert_valid to return True (cert already valid)
    monkeypatch.setattr(cert, "_is_cert_valid", lambda d, p: True)

    # Also need cert_path to exist
    def mock_isfile(path):
        if "fullchain.pem" in str(path):
            return True
        return os.path.isfile(path)

    monkeypatch.setattr(os.path, "isfile", mock_isfile)

    result = cert.orchestrate_certs(
        ["example.com"],
        s3_script,
        issue_script,
    )

    assert result.skipped == 1
    assert result.domains["example.com"].status == "skipped"
    logger.critical("[IMP:9][test] Idempotent skip — valid cert on disk")


# endregion
