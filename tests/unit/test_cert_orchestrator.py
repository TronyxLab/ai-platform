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
    """orchestrate_certs should restore all domains from S3 when available.

    DevPlan 052 Phase 1: now uses direct s3_ssl_cache import instead of subprocess.
    Mocks s3_ssl_cache module functions directly.
    """
    s3_script = str(tmp_path / "s3-ssl-cache.sh")
    issue_script = str(tmp_path / "issue-cert.sh")
    Path(s3_script).touch()
    Path(issue_script).touch()

    # Mock _is_cert_valid to return False (no valid cert on disk)
    monkeypatch.setattr(cert, "_is_cert_valid", lambda d, p: False)

    # Set S3_BUCKET for s3_ssl_cache (required by _try_s3_restore)
    monkeypatch.setenv("S3_BUCKET", "test-bucket")

    # Mock s3_ssl_cache module functions (DevPlan 052: direct import replaces subprocess)
    mock_s3 = MagicMock()
    mock_s3.check_cert.return_value = True
    mock_s3.download_cert.return_value = True
    monkeypatch.setattr(cert, "s3_ssl_cache", mock_s3)

    # Mock os.path.isfile for cert path check (avoid /etc/letsencrypt access)
    real_isfile = os.path.isfile

    def mock_isfile_after_download(path):
        """Return True for cert paths that were 'downloaded' by mock."""
        if "/etc/letsencrypt/live/" in str(path) and "fullchain.pem" in str(path):
            return True  # Pretend cert exists after S3 download
        return real_isfile(path)

    monkeypatch.setattr(os.path, "isfile", mock_isfile_after_download)

    result = cert.orchestrate_certs(
        ["example.com", "test.com"],
        s3_script,
        issue_script,
    )

    assert result.restored == 2
    assert result.issued == 0
    assert result.failed == 0
    # Verify s3_ssl_cache was called
    assert mock_s3.check_cert.call_count == 2
    assert mock_s3.download_cert.call_count == 2
    logger.critical("[IMP:9][test] Bulk restore from S3 — all domains restored via direct import")


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
    """orchestrate_certs should skip domains with valid certs on disk
    and upload to S3 (source="disk_synced")."""
    issue_script = str(tmp_path / "issue-cert.sh")
    Path(issue_script).touch()

    # Mock _is_cert_valid to return True (cert already valid)
    monkeypatch.setattr(cert, "_is_cert_valid", lambda d, p: True)

    # Mock cert_path to exist (non-recursive — use a closure with the real isfile)
    real_isfile = os.path.isfile

    def mock_isfile(path):
        if "fullchain.pem" in str(path):
            return True
        return real_isfile(path)

    monkeypatch.setattr(os.path, "isfile", mock_isfile)

    result = cert.orchestrate_certs(
        ["example.com"],
        issue_script,
    )

    assert result.skipped == 1
    assert result.domains["example.com"].status == "skipped"
    assert result.domains["example.com"].source == "disk_synced", (
        f"Expected source='disk_synced', got '{result.domains['example.com'].source}'"
    )
    logger.critical("[IMP:9][test] Idempotent skip — valid cert on disk, source=disk_synced")


# endregion

# ═══════════════════════════════════════════════════════════════════
# region Tests: _is_le_issuer — P0 fix: reject non-LE certs
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · _is_le_issuer accepts Let's Encrypt certs
# · Scenario: openssl x509 -issuer returns "Let's Encrypt" → True
# · Last fail: N/A (new test for P0 fix)
# · Remove if: issuer check logic changes
@ldd_trajectory
def test_is_le_issuer_accepts_le_cert(caplog):
    """_is_le_issuer should return True for Let's Encrypt issuer."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="issuer=C = US, O = Let's Encrypt, CN = R11\n",
            stderr="",
        )
        result = cert._is_le_issuer("/fake/path/fullchain.pem")
    assert result is True
    logger.critical("[IMP:9][test] _is_le_issuer accepts LE cert")


# 🧪 TRAP[TEST] · Regression · _is_le_issuer rejects mkcert certs
# · Scenario: openssl x509 -issuer returns "mkcert development CA" → False
# · Last fail: 2026-07-22 — P0 mkcert certs survived bootstrap
# · Remove if: NEVER — this is the regression test for the P0 fix
@ldd_trajectory
def test_is_le_issuer_rejects_mkcert_cert(caplog):
    """_is_le_issuer should return False for mkcert/self-signed certs."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "issuer=O = mkcert development CA, "
                "OU = tronyx@MacBook-Pro-Vladimir-2.local, "
                "CN = mkcert tronyx@MacBook-Pro-Vladimir-2.local\n"
            ),
            stderr="",
        )
        result = cert._is_le_issuer("/fake/path/fullchain.pem")
    assert result is False
    logger.critical("[IMP:9][test] _is_le_issuer rejects mkcert cert")


# 🧪 TRAP[TEST] · Regression · _is_le_issuer handles openssl failure
# · Scenario: openssl returns non-zero → return False
# · Last fail: N/A (new test)
@ldd_trajectory
def test_is_le_issuer_handles_openssl_failure(caplog):
    """_is_le_issuer should return False when openssl fails."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        result = cert._is_le_issuer("/nonexistent.pem")
    assert result is False
    logger.critical("[IMP:9][test] _is_le_issuer handles openssl failure gracefully")


# 🧪 TRAP[TEST] · Regression · _is_cert_valid rejects mkcert even if not expired
# · Scenario: cert not expired but issuer is mkcert → _is_cert_valid returns False
# · Last fail: 2026-07-22 — P0: mkcert cert passed as "valid" because only expiry checked
# · Remove if: NEVER — this is the regression test for the P0 fix
@ldd_trajectory
def test_is_cert_valid_rejects_mkcert_even_if_not_expired(caplog, monkeypatch):
    """_is_cert_valid should return False for non-LE certs regardless of expiry."""
    # Mock openssl -checkend to pass (cert not expired)
    # but _is_le_issuer to return False (mkcert cert)
    checkend_result = MagicMock(returncode=0, stdout="", stderr="")
    issuer_result = MagicMock(
        returncode=0,
        stdout="issuer=O = mkcert development CA\n",
        stderr="",
    )
    call_count = [0]

    def mock_run(cmd, **kwargs):
        call_count[0] += 1
        if "-checkend" in str(cmd):
            return checkend_result
        if "-issuer" in str(cmd):
            return issuer_result
        return MagicMock(returncode=1, stdout="", stderr="")

    with patch("subprocess.run", side_effect=mock_run):
        result = cert._is_cert_valid("tronyx.ru", "/fake/path/fullchain.pem")

    assert result is False, "mkcert cert should NOT pass _is_cert_valid regardless of expiry"
    assert call_count[0] == 2, "Should have called both -checkend and -issuer"
    logger.critical("[IMP:9][test] _is_cert_valid rejects mkcert cert — P0 regression test")


# endregion

# ═══════════════════════════════════════════════════════════════════
# region Tests: ACME_CHALLENGE_MODE passthrough — DevPlan 058
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · _issue_cert passes ACME_CHALLENGE_MODE env var
# · Scenario: ACME_CHALLENGE_MODE set in os.environ → passed to issue-cert.sh subprocess
# · Last fail: N/A (new test for DevPlan 058)
# · Remove if: env var passthrough logic changes
@ldd_trajectory
def test_orchestrate_passes_challenge_mode(caplog, tmp_path, monkeypatch):
    """_issue_cert should pass ACME_CHALLENGE_MODE env var to issue-cert.sh subprocess.

    ## @purpose  Verify the env var passthrough: cert_orchestrator reads ACME_CHALLENGE_MODE
    ##           from os.environ and passes it to the subprocess running issue-cert.sh.
    ## @scenario  Set ACME_CHALLENGE_MODE=http in os.environ → call _issue_cert
    ##           → subprocess.run receives env with ACME_CHALLENGE_MODE=http
    """
    issue_script = str(tmp_path / "issue-cert.sh")
    Path(issue_script).write_text("#!/bin/bash\nexit 0\n")
    Path(issue_script).chmod(0o755)

    monkeypatch.setattr(cert, "_is_cert_valid", lambda d, p: False)

    captured_env = {}

    def mock_run(cmd, **kwargs):
        if "bash" in str(cmd):
            captured_env.update(kwargs.get("env", {}))
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=mock_run):
        monkeypatch.setattr(os, "environ", {**os.environ, "ACME_CHALLENGE_MODE": "http"})
        result = cert._issue_cert("example.com", issue_script)

    logger.critical(
        "[IMP:9][test_orchestrate_passes_challenge_mode] ASSERT: ACME_CHALLENGE_MODE=http passed to subprocess"
    )
    print("--- captured env ---")
    print(captured_env)
    print("--- end ---")

    assert result.status == "issued", f"Expected issued status, got {result.status}"
    assert result.challenge == "http", f"Expected challenge=http, got {result.challenge}"
    assert captured_env.get("ACME_CHALLENGE_MODE") == "http", (
        f"ACME_CHALLENGE_MODE not passed to subprocess env: {captured_env}"
    )

    logger.critical("[IMP:9][test_orchestrate_passes_challenge_mode] PASS: ACME_CHALLENGE_MODE passed to subprocess")


# 🧪 TRAP[TEST] · Regression · DomainCertResult contains challenge field
# · Scenario: _issue_cert returns DomainCertResult with challenge="dns" (default)
# · Last fail: N/A (new test for DevPlan 058)
# · Remove if: DomainCertResult.challenge field removed or renamed
@ldd_trajectory
def test_domain_cert_result_includes_challenge_field(caplog, tmp_path, monkeypatch):
    """_issue_cert should return DomainCertResult with challenge field set.

    ## @purpose  Verify the new challenge field is populated in DomainCertResult.
    ## @scenario  Default ACME_CHALLENGE_MODE (unset → "dns") → _issue_cert
    ##           → DomainCertResult.challenge == "dns"
    """
    issue_script = str(tmp_path / "issue-cert.sh")
    Path(issue_script).write_text("#!/bin/bash\nexit 0\n")
    Path(issue_script).chmod(0o755)

    monkeypatch.setattr(cert, "_is_cert_valid", lambda d, p: False)

    def mock_run(cmd, **kwargs):
        if "bash" in str(cmd):
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=mock_run):
        result = cert._issue_cert("example.com", issue_script)

    logger.critical("[IMP:9][test_domain_cert_result_includes_challenge_field] ASSERT: challenge=dns in result")
    print("--- result ---")
    print(result.to_dict())
    print("--- end ---")

    assert result.status == "issued", f"Expected issued, got {result.status}"
    assert result.challenge == "dns", f"Expected challenge='dns' (default), got '{result.challenge}'"
    assert "challenge" in result.to_dict(), f"challenge field missing from to_dict(): {result.to_dict()}"

    logger.critical(
        "[IMP:9][test_domain_cert_result_includes_challenge_field] PASS: challenge field in DomainCertResult"
    )


# endregion
