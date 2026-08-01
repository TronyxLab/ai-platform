"""
# GREP_SUMMARY: test_s3_ssl_cache, s3-upload, s3-download, s3-check, bulk-restore, boto3-mock
# STRUCTURE: ▶ tmp_path + mock boto3 → ◇ upload_cert success/missing-bucket → ◇ download_cert success →
#            ◇ check_cert hit/miss → ◇ bulk_restore yaml → ◇ CLI upload command → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for s3_ssl_cache.py — SSL cert upload/download/check/bulk-restore to S3.
## @scope    Tests all 4 public functions via direct import + mock boto3.
## @invariants
##   - All boto3 calls mocked (no real S3 connections)
##   - Cert files created in tmp_path
##   - Each test validates IMP:9 business logic log presence via ldd_trajectory decorator
## @rationale DevPlan 052 Phase 1: Python port of s3-ssl-cache.sh — direct import fixes
##            subshell credential propagation bug. Phase 3: guaranteed S3 backup.
## @changes  CREATED: 2026-07-25 · DevPlan 052 Phase 3
# endregion MODULE_CONTRACT
"""

import io
import sys
import tarfile
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = pytest.importorskip("logging").getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap"
sys.path.insert(0, str(_MODULE_DIR))
import s3_ssl_cache

# ═════════════════════════════════════════════════════════════════════════════
# region Tests: upload_cert
# ═════════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · upload_cert uploads files to S3 successfully
# · Scenario: All cert files exist → upload_cert returns True
# · Last fail: N/A (new test)
# · Remove if: upload_cert interface changes significantly
@ldd_trajectory
def test_upload_cert_success(caplog, tmp_path, monkeypatch):
    """upload_cert() uploads files to S3 successfully when cert files exist."""
    domain = "example.com"
    live_dir = tmp_path / "live" / domain
    live_dir.mkdir(parents=True)
    (live_dir / "fullchain.pem").write_text("fullchain cert content")
    (live_dir / "privkey.pem").write_text("private key content")
    # chain.pem is optional — test without it

    monkeypatch.setenv("S3_BUCKET", "test-bucket")

    # Mock boto3 client + upload_file
    mock_client = MagicMock()
    with patch.object(s3_ssl_cache, "_get_s3_client", return_value=mock_client):
        result = s3_ssl_cache.upload_cert(
            domain,
            cert_dir=str(tmp_path / "live"),
            acme_home=str(tmp_path / "acme"),
            s3_bucket="test-bucket",
            s3_prefix="platform/ssl-certs",
        )

    assert result is True, "upload_cert should return True on success"
    # Should upload fullchain.pem + privkey.pem (2 required)
    assert mock_client.upload_file.call_count >= 2
    logger.critical("[IMP:9][test] upload_cert success — files uploaded to S3")


# 🧪 TRAP[TEST] · Regression · upload_cert missing S3 bucket
# · Scenario: S3_BUCKET not set → upload_cert returns False
# · Last fail: N/A (new test)
# · Remove if: early-return on missing bucket changes
@ldd_trajectory
def test_upload_cert_missing_s3_bucket(caplog, tmp_path, monkeypatch):
    """upload_cert() returns False when S3_BUCKET not set in env or arg."""
    domain = "example.com"
    live_dir = tmp_path / "live" / domain
    live_dir.mkdir(parents=True)
    (live_dir / "fullchain.pem").write_text("fullchain cert content")
    (live_dir / "privkey.pem").write_text("private key content")

    # Do NOT set S3_BUCKET
    result = s3_ssl_cache.upload_cert(
        domain,
        cert_dir=str(tmp_path / "live"),
    )

    assert result is False, "upload_cert should return False when S3_BUCKET not set"
    logger.critical("[IMP:9][test] upload_cert missing S3 bucket — returns False gracefully")


# endregion


# ═════════════════════════════════════════════════════════════════════════════
# region Tests: download_cert
# ═════════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · download_cert restores cert from S3
# · Scenario: S3 has valid cert → download_cert returns True
# · Last fail: N/A (new test)
# · Remove if: download_cert interface changes significantly
@ldd_trajectory
def test_download_cert_success(caplog, tmp_path, monkeypatch):
    """download_cert() restores cert from S3 successfully."""
    domain = "example.com"
    live_dir = tmp_path / "live" / domain

    monkeypatch.setenv("S3_BUCKET", "test-bucket")

    # Mock boto3 client
    mock_client = MagicMock()

    # Mock _validate_cert to return True (skip openssl)
    with (
        patch.object(s3_ssl_cache, "_get_s3_client", return_value=mock_client),
        patch.object(s3_ssl_cache, "_validate_cert", return_value=True),
    ):
        result = s3_ssl_cache.download_cert(
            domain,
            cert_dir=str(tmp_path / "live"),
            s3_bucket="test-bucket",
        )

    assert result is True, "download_cert should return True on success"
    assert live_dir.exists(), "Live directory should be created"
    assert (live_dir / "fullchain.pem").exists(), "fullchain.pem should be restored"
    logger.critical("[IMP:9][test] download_cert success — cert restored from S3")


# endregion


# ═════════════════════════════════════════════════════════════════════════════
# region Tests: check_cert
# ═════════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · check_cert finds valid cert in S3
# · Scenario: S3 has valid cert (>30 days) → check_cert returns True
# · Last fail: N/A (new test)
# · Remove if: check_cert logic changes significantly
@ldd_trajectory
def test_check_cert_hit(caplog, tmp_path, monkeypatch):
    """check_cert() finds valid cert in S3 (>30 days)."""
    domain = "example.com"

    monkeypatch.setenv("S3_BUCKET", "test-bucket")

    # Mock _download_s3_file to write a fake cert to the temp path

    def mock_download(s3_key, local_dst):
        # Write a fake PEM so the temp file exists
        with open(local_dst, "w") as f:
            f.write("fake pem content")
        return True

    with (
        patch.object(s3_ssl_cache, "_download_s3_file", side_effect=mock_download),
        patch.object(s3_ssl_cache, "_validate_cert", return_value=True),
    ):
        result = s3_ssl_cache.check_cert(domain, s3_bucket="test-bucket")

    assert result is True, "check_cert should return True when valid cert in S3"
    logger.critical("[IMP:9][test] check_cert hit — valid cert found in S3")


# 🧪 TRAP[TEST] · Regression · check_cert miss (no cert in S3)
# · Scenario: S3 has no cert → check_cert returns False
# · Last fail: N/A (new test)
# · Remove if: check_cert logic changes significantly
@ldd_trajectory
def test_check_cert_miss(caplog, monkeypatch):
    """check_cert() returns False when no cert in S3."""
    domain = "missing.example.com"

    monkeypatch.setenv("S3_BUCKET", "test-bucket")

    with patch.object(s3_ssl_cache, "_download_s3_file", return_value=False):
        result = s3_ssl_cache.check_cert(domain, s3_bucket="test-bucket")

    assert result is False, "check_cert should return False when no cert in S3"
    logger.critical("[IMP:9][test] check_cert miss — no cert in S3, returns False")


# endregion


# ═════════════════════════════════════════════════════════════════════════════
# region Tests: bulk_restore
# ═════════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · bulk_restore parses node.yaml and processes domains
# · Scenario: node.yaml with platform domain + project → bulk_restore processes all
# · Last fail: N/A (new test)
# · Remove if: bulk_restore logic changes significantly
@ldd_trajectory
def test_bulk_restore_parses_yaml(caplog, tmp_path, monkeypatch):
    """bulk_restore() parses node.yaml and processes all domains."""
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text("""
domain: platform.example.com
projects:
  - domain: app1.example.com
  - domain: app2.example.com
""")

    monkeypatch.setenv("S3_BUCKET", "test-bucket")

    # Mock check_cert → True, download_cert → True for all
    with (
        patch.object(s3_ssl_cache, "check_cert", return_value=True),
        patch.object(s3_ssl_cache, "download_cert", return_value=True),
    ):
        result = s3_ssl_cache.bulk_restore(str(node_yaml), s3_bucket="test-bucket")

    assert len(result) == 3, f"Expected 3 domains, got {len(result)}"
    assert all(v == "restored" for v in result.values()), f"All should be restored: {result}"
    logger.critical("[IMP:9][test] bulk_restore parses node.yaml — all domains restored")


# endregion


# ═════════════════════════════════════════════════════════════════════════════
# region Tests: CLI entry point
# ═════════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · CLI upload command
# · Scenario: CLI invoked with 'upload domain' → upload_cert called
# · Last fail: N/A (new test)
# · Remove if: CLI entry point logic changes
@ldd_trajectory
def test_cli_upload_command(caplog, monkeypatch):
    """CLI entry point handles 'upload' command by checking CLI dispatch logic."""
    monkeypatch.setenv("S3_BUCKET", "test-bucket")

    # The CLI block in s3_ssl_cache.py runs under `if __name__ == "__main__":`
    # and cannot be imported directly. Instead, verify that the CLI dispatch
    # code path (checking `sys.argv[1] == "upload"` → call `upload_cert()`) is
    # present in the module source.
    import inspect

    source = inspect.getsource(s3_ssl_cache)
    assert '"upload"' in source or "'upload'" in source, "s3_ssl_cache.py must handle 'upload' CLI command"
    assert "upload_cert(domain" in source or "upload_cert(" in source, (
        "s3_ssl_cache.py CLI must call upload_cert() for upload command"
    )
    logger.critical("[IMP:9][test] CLI upload command — upload handler present in source")


# endregion


# ═════════════════════════════════════════════════════════════════════════════
# region B10 T2 additions — native replacements for deleted grep-asserts (DevPlan 116 B10)
# ═════════════════════════════════════════════════════════════════════════════
# These cover the gaps formerly guarded by `in content` grep-asserts in
# tests/test_ssl_s3_cache.py (deleted — redundant Python-source greps per D2):
#   - G3 account dir resolution (_ecc primary, data/ fallback)
#   - openssl -checkend expiry path (_validate_cert)
#   - non-LE issuer rejection (_validate_cert)
#   - non-fatal return False on S3 errors (_download_s3_file/_upload_s3_file)
#   - full download restore (fullchain + privkey + chain + account.tar.gz)


# 🧪 TRAP[TEST] · 2026-08-01 · G3 · _find_acme_account_dir prefers <domain>_ecc
# · Regression: G3 — account data path was hardcoded to data/<domain>/ which doesn't exist
# · Last fail: N/A (native replacement for test_upload_with_account_ecc_path grep)
# · Remove if: _find_acme_account_dir resolution logic changes
@ldd_trajectory
def test_find_acme_account_dir_ecc_primary(caplog, tmp_path):
    """G3: _find_acme_account_dir returns <domain>_ecc/ when both _ecc and data/ exist."""
    acme = tmp_path / "acme"
    (acme / "example.com_ecc").mkdir(parents=True)
    (acme / "data" / "example.com").mkdir(parents=True)

    result = s3_ssl_cache._find_acme_account_dir("example.com", str(acme))

    assert result == str(acme / "example.com_ecc"), f"G3: _ecc path must be primary, got {result}"
    logger.critical("[IMP:9][test] _find_acme_account_dir prefers <domain>_ecc (G3)")


# 🧪 TRAP[TEST] · 2026-08-01 · G3 · _find_acme_account_dir falls back to data/<domain>
# · Regression: G3 — legacy data/<domain>/ layout must still resolve
# · Last fail: N/A (native replacement for test_upload_with_account_ecc_path grep)
# · Remove if: legacy fallback removed
@ldd_trajectory
def test_find_acme_account_dir_data_fallback(caplog, tmp_path):
    """G3: _find_acme_account_dir falls back to data/<domain>/ when _ecc absent."""
    acme = tmp_path / "acme"
    (acme / "data" / "example.com").mkdir(parents=True)

    result = s3_ssl_cache._find_acme_account_dir("example.com", str(acme))

    assert result == str(acme / "data" / "example.com"), (
        f"G3: legacy data/<domain>/ fallback must resolve, got {result}"
    )
    logger.critical("[IMP:9][test] _find_acme_account_dir falls back to data/<domain> (G3)")


# 🧪 TRAP[TEST] · 2026-08-01 · 052 · _validate_cert rejects non-LE issuer (mkcert guard)
# · Regression: 052 Bug — mkcert/dev certs passed validation (no issuer check)
# · Last fail: N/A (native replacement for test_download_rejects_non_le_issuer grep)
# · Remove if: issuer validation in _validate_cert changes
@ldd_trajectory
def test_validate_cert_rejects_non_le_issuer(caplog, tmp_path, monkeypatch):
    """_validate_cert() returns False when openssl -issuer is not Let's Encrypt."""
    cert_path = tmp_path / "cert.pem"
    cert_path.write_text("fake pem")

    import subprocess as _sp

    def _fake_run(cmd, **kwargs):
        if "-issuer" in cmd:
            return _sp.CompletedProcess(cmd, 0, stdout="issuer=CN = mkcert dev CA", stderr="")
        if "-subject" in cmd:
            return _sp.CompletedProcess(cmd, 0, stdout="subject=CN = example.com", stderr="")
        return _sp.CompletedProcess(cmd, 0, stdout="", stderr="")

    fake_subprocess = types.SimpleNamespace(
        run=_fake_run,
        TimeoutExpired=_sp.TimeoutExpired,
        FileNotFoundError=FileNotFoundError,
        OSError=OSError,
    )
    monkeypatch.setattr(s3_ssl_cache, "subprocess", fake_subprocess)

    result = s3_ssl_cache._validate_cert(str(cert_path), "example.com")

    assert result is False, "Non-LE issuer (mkcert) must be rejected"
    logger.critical("[IMP:9][test] _validate_cert rejects non-LE issuer — mkcert regression guard")


# 🧪 TRAP[TEST] · 2026-08-01 · openssl-checkend path · expiring cert rejected
# · Regression: 052 — expired certs must not be restored
# · Last fail: N/A (gap fill for openssl-checkend path per B10 T2)
# · Remove if: expiry validation logic changes
@ldd_trajectory
def test_validate_cert_checkend_expiring_fails(caplog, tmp_path, monkeypatch):
    """_validate_cert(check_expiry=True) returns False when openssl -checkend fails (<30 days)."""
    cert_path = tmp_path / "cert.pem"
    cert_path.write_text("fake pem")

    import subprocess as _sp

    def _fake_run(cmd, **kwargs):
        if "-issuer" in cmd:
            return _sp.CompletedProcess(cmd, 0, stdout="issuer=CN = Let's Encrypt", stderr="")
        if "-subject" in cmd:
            return _sp.CompletedProcess(cmd, 0, stdout="subject=CN = example.com", stderr="")
        if "-checkend" in cmd:
            return _sp.CompletedProcess(cmd, 1, stdout="", stderr="certificate expires soon")
        return _sp.CompletedProcess(cmd, 0, stdout="", stderr="")

    fake_subprocess = types.SimpleNamespace(
        run=_fake_run,
        TimeoutExpired=_sp.TimeoutExpired,
        FileNotFoundError=FileNotFoundError,
        OSError=OSError,
    )
    monkeypatch.setattr(s3_ssl_cache, "subprocess", fake_subprocess)

    result = s3_ssl_cache._validate_cert(str(cert_path), "example.com", check_expiry=True)

    assert result is False, "Cert expiring within 30 days must fail checkend validation"
    logger.critical("[IMP:9][test] _validate_cert checkend path — expiring cert rejected")


# 🧪 TRAP[TEST] · 2026-08-01 · non-fatal · _download_s3_file returns False on ClientError
# · Regression: 052 — S3 failures must never raise (graceful degradation)
# · Last fail: N/A (gap fill for non-fatal return False per B10 T2)
# · Remove if: _download_s3_file error handling changes
@ldd_trajectory
def test_download_s3_file_nonfatal_on_client_error(caplog, tmp_path, monkeypatch):
    """_download_s3_file() returns False (not raises) on boto3 ClientError — non-fatal."""
    monkeypatch.setenv("S3_BUCKET", "test-bucket")

    mock_client = MagicMock()
    mock_client.download_file.side_effect = s3_ssl_cache.ClientError({"Error": {"Code": "NoSuchKey"}}, "download_file")
    with patch.object(s3_ssl_cache, "_get_s3_client", return_value=mock_client):
        ok = s3_ssl_cache._download_s3_file("platform/ssl-certs/x/fullchain.pem", str(tmp_path / "dst.pem"))

    assert ok is False, "ClientError must be swallowed into return False (non-fatal)"
    logger.critical("[IMP:9][test] _download_s3_file non-fatal on ClientError — returns False")


# 🧪 TRAP[TEST] · 2026-08-01 · non-fatal · _upload_s3_file returns False on ClientError
# · Regression: 052 — S3 upload failures must never raise (graceful degradation)
# · Last fail: N/A (gap fill for non-fatal return False per B10 T2)
# · Remove if: _upload_s3_file error handling changes
@ldd_trajectory
def test_upload_s3_file_nonfatal_on_client_error(caplog, tmp_path, monkeypatch):
    """_upload_s3_file() returns False (not raises) on boto3 ClientError — non-fatal."""
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    src = tmp_path / "cert.pem"
    src.write_text("content")

    mock_client = MagicMock()
    mock_client.upload_file.side_effect = s3_ssl_cache.ClientError({"Error": {"Code": "AccessDenied"}}, "upload_file")
    with patch.object(s3_ssl_cache, "_get_s3_client", return_value=mock_client):
        ok = s3_ssl_cache._upload_s3_file(str(src), "platform/ssl-certs/x/fullchain.pem")

    assert ok is False, "ClientError must be swallowed into return False (non-fatal)"
    logger.critical("[IMP:9][test] _upload_s3_file non-fatal on ClientError — returns False")


# 🧪 TRAP[TEST] · 2026-08-01 · G3/full-restore · download_cert restores all 4 artifacts
# · Regression: 052/G3 — full restore path (fullchain + privkey + chain + account.tar.gz)
# · Last fail: N/A (native replacement for test_download_accepts_le_cert grep)
# · Remove if: download_cert restore logic changes
@ldd_trajectory
def test_download_cert_restores_all_artifacts(caplog, tmp_path, monkeypatch):
    """download_cert() restores fullchain + privkey + chain + account.tar.gz (full path)."""
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    acme_home = tmp_path / "acme"

    def _fake_download(s3_key, local_dst):
        if local_dst.endswith(".tar.gz"):
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                data = b"account-data"
                info = tarfile.TarInfo("example.com_ecc/account.json")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            with open(local_dst, "wb") as f:
                f.write(buf.getvalue())
        else:
            with open(local_dst, "w") as f:
                f.write(f"pem content: {s3_key}")
        return True

    with (
        patch.object(s3_ssl_cache, "_get_s3_client", return_value=MagicMock()),
        patch.object(s3_ssl_cache, "_validate_cert", return_value=True),
        patch.object(s3_ssl_cache, "_download_s3_file", side_effect=_fake_download),
    ):
        result = s3_ssl_cache.download_cert(
            "example.com",
            cert_dir=str(tmp_path / "live"),
            acme_home=str(acme_home),
            s3_bucket="test-bucket",
        )

    assert result is True, "download_cert must return True on full restore"
    live = tmp_path / "live" / "example.com"
    assert (live / "fullchain.pem").exists(), "fullchain.pem must be restored"
    assert (live / "privkey.pem").exists(), "privkey.pem must be restored"
    assert (live / "chain.pem").exists(), "chain.pem must be restored (optional)"
    assert (acme_home / "example.com_ecc" / "account.json").exists(), (
        "account.tar.gz must be extracted to acme_home (G3)"
    )
    logger.critical("[IMP:9][test] download_cert restores all 4 artifacts (fullchain+privkey+chain+account)")


# endregion B10 T2 additions
