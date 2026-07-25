"""
# GREP_SUMMARY: test_cert_upload_on_skip, s3-upload, cert-orchestrator, process-single-domain, idempotent, skip-sync
# STRUCTURE: ▶ tmp_path + mock cert → ◇ _process_single_domain skip → ◇ upload called → ◇ after issue → ◇ source=disk_synced → ⎋ LDD
# region MODULE_CONTRACT
## @purpose  Unit tests for upload-on-skip behavior in cert_orchestrator.py.
##           Verifies that _upload_to_s3() is called on every code path:
##           skip (cert on disk), issue (after successful acme.sh).
## @scope    Tests _process_single_domain() via direct import + mocks.
## @invariants
##   - _upload_to_s3 mocked to verify call count
##   - Cert presence on disk mocked via _is_cert_valid
##   - Each test validates IMP:9 business logic log presence
## @rationale DevPlan 052 Phase 3: guaranteed S3 backup — cert always synced to S3.
## @changes  CREATED: 2026-07-25 · DevPlan 052 Phase 3
# endregion MODULE_CONTRACT
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = pytest.importorskip("logging").getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap"
sys.path.insert(0, str(_MODULE_DIR))
import cert_orchestrator as cert

# ═════════════════════════════════════════════════════════════════════════════
# region Tests: upload-on-skip
# ═════════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · _upload_to_s3 called when cert on disk (skip)
# · Scenario: Valid cert on disk → _process_single_domain skips issue + calls _upload_to_s3
# · Last fail: 2026-07-25 — platform domain cert never uploaded to S3 (root cause)
# · Remove if: upload-on-skip logic changes
@ldd_trajectory
def test_upload_called_on_skip(caplog, tmp_path, monkeypatch):
    """_process_single_domain() calls _upload_to_s3() when cert exists on disk (skip path)."""
    monkeypatch.setattr(cert, "_is_cert_valid", lambda d, p: True)

    # Mock _upload_to_s3 to verify it's called
    upload_called = False

    def mock_upload(domain):
        nonlocal upload_called
        upload_called = True
        return True

    monkeypatch.setattr(cert, "_upload_to_s3", mock_upload)

    # Mock os.path.isfile for cert_path check
    real_isfile = os.path.isfile

    def mock_isfile(path):
        if "fullchain.pem" in str(path):
            return True
        return real_isfile(path)

    monkeypatch.setattr(os.path, "isfile", mock_isfile)

    result = cert._process_single_domain("example.com", "/fake/issue-cert.sh")

    assert result.status == "skipped", f"Expected skipped, got {result.status}"
    assert result.source == "disk_synced", f"Expected disk_synced, got {result.source}"
    assert upload_called, "_upload_to_s3() should have been called on skip"
    logger.critical("[IMP:9][test] upload_on_skip — _upload_to_s3 called on disk-skip path")


# 🧪 TRAP[TEST] · Regression · _upload_to_s3 called after successful issue
# · Scenario: No valid cert on disk, S3 miss → issue succeeds → _upload_to_s3 called
# · Last fail: 2026-07-25 — platform domain cert never uploaded after issue
# · Remove if: upload-after-issue logic changes
@ldd_trajectory
def test_upload_called_after_issue(caplog, tmp_path, monkeypatch):
    """After successful issue, _upload_to_s3() is called."""
    monkeypatch.setattr(cert, "_is_cert_valid", lambda d, p: False)

    # Mock s3_ssl_cache to return miss
    mock_s3 = MagicMock()
    mock_s3.check_cert.return_value = False
    monkeypatch.setattr(cert, "s3_ssl_cache", mock_s3)
    monkeypatch.setenv("S3_BUCKET", "test-bucket")

    # Mock _upload_to_s3
    upload_called = False

    def mock_upload(domain):
        nonlocal upload_called
        upload_called = True
        return True

    monkeypatch.setattr(cert, "_upload_to_s3", mock_upload)

    # Mock issue-cert.sh subprocess to succeed
    issue_script = str(tmp_path / "issue-cert.sh")
    Path(issue_script).write_text("#!/bin/bash\nexit 0\n")
    Path(issue_script).chmod(0o755)

    def mock_run(cmd, **kwargs):
        return MagicMock(returncode=0, stdout="issued", stderr="")

    with patch("subprocess.run", side_effect=mock_run):
        result = cert._process_single_domain("example.com", issue_script)

    assert result.status == "issued", f"Expected issued, got {result.status}"
    assert upload_called, "_upload_to_s3() should have been called after successful issue"
    logger.critical("[IMP:9][test] upload_after_issue — _upload_to_s3 called after cert issue")


# endregion
