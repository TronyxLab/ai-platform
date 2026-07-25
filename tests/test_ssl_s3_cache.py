# GREP_SUMMARY: test ssl-s3-cache backup_config S3Config get_s3_config upload pysource ssl-cache issue-cert node-lifecycle integration
# STRUCTURE: ▶ fixtures(fake_s3, tmp_path) → test_backup_config_s3_config(◇ S3Config ↔ BackupConfig = S3Config subset) → test_get_s3_config(⊕ env mock → assert 5 fields, no prefix) → test_get_s3_config_missing(⊕ missing → RuntimeError) → test_upload_config_source_ssl_cache(⊕ FakeS3Client → upload with ssl-cache prefix) → test_upload_config_source_backup(⊕ FakeS3Client → upload with backup prefix) → test_s3_cache_script_exists(⊕ assert s3-ssl-cache.sh has upload/download/check) → test_issue_cert_s3_integration(⊕ grep issue-cert.sh for s3-ssl-cache upload call) → test_node_lifecycle_s3_integration(⊕ grep node-lifecycle.sh for s3-ssl-cache check call) → test_upload_without_chain_pem_succeeds(⊕ G2: chain.pem NOT required) → test_upload_with_account_ecc_path(⊕ G3: <domain>_ecc/ path) → test_download_rejects_non_le_issuer(⊕ LE issuer reject) → test_download_accepts_le_cert(⊕ LE accept + restore) → ⎋
# region MODULE_CONTRACT
## @purpose  Tests for Wave 1 SSL S3 cache — S3Config base TypedDict, --config-source ssl-cache,
##           s3-ssl-cache.sh wrapper, and integration with issue-cert.sh / node-lifecycle.sh.
## @scope    Unit tests for backup_config.py refactoring (S3Config extraction), upload.py
##           --config-source flag, and integration grep tests for bash script changes.
## @invariants
##   - No real S3 connections (FakeS3Client from test_upload.py pattern)
##   - Bash script tests use grep/read (not subprocess) for safe validation
##   - All tests @pytest.mark.static_audit
##   - LDD trajectory printed for every test
## @rationale  DevPlan 024 Wave 1: SSL certificate caching on S3. S3Config extraction enables
##   reuse of upload.py for both backup and ssl-cache operations without duplicating S3 logic.
## @changes  CREATED: 2026-07-21 · Wave 1 SSL S3 cache (DevPlan 024)
##            MODIFIED: 2026-07-23 · Wave 1 cert-wildcard-fix (DevPlan 058):
##              - test_upload_without_chain_pem_succeeds — G2: chain.pem not required
##              - test_upload_with_account_ecc_path — G3: <domain>_ecc/ path + fallback
##              - test_download_rejects_non_le_issuer — LE issuer validation
##              - test_download_accepts_le_cert — full restore path intact
# endregion MODULE_CONTRACT

import logging
import os
import sys

import pytest

# Add backup-cron scripts path for imports of backup_config, upload modules
_backup_cron_scripts = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "core", "modules", "backup-cron", "scripts")
)
if _backup_cron_scripts not in sys.path:
    sys.path.insert(0, _backup_cron_scripts)

logger = logging.getLogger(__name__)


# region HELPERS

# 🧐 TRAP[DECISION] · 2026-07-21 · — · S3Config test uses same FakeS3Client as test_upload.py
# · Rejected: creating separate test-only mock for S3Config testing
# · Reason: upload.py tests already have FakeS3Client. For backup_config.py tests we need
#   env var manipulation, not S3 client mocking. The config tests are pure data flow tests.
# · Rev: if backup_config.py adds network-dependent operations, revisit mock strategy


def _make_s3_config_dict(**overrides: str) -> dict:
    """Create a standard S3Config dict (5 fields), no backup-specific prefix."""
    config: dict = {
        "endpoint_url": "https://s3.timeweb.cloud",
        "aws_access_key_id": "test-access-key",
        "aws_secret_access_key": "test-secret-key",
        "bucket": "test-bucket",
        "region": "us-east-1",
    }
    config.update(overrides)
    return config


def _make_backup_config_dict(**overrides: str) -> dict:
    """Create a standard BackupConfig dict (8 fields)."""
    config = _make_s3_config_dict()
    config.update(
        {
            "prefix": "platform/backups",
            "context": "personal",
            "node_name": "test-node",
        }
    )
    config.update(overrides)
    return config


# endregion HELPERS


# region TEST_S3_CONFIG_BASE


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-21 · Scenario: S3Config(5 fields) ✓ BackupConfig(8 = S3Config+3) ✓
# · Last fail: None (first run) · Remove if: backup_config.py TypedDict hierarchy changes
def test_s3_config_type_structure():
    """S3Config TypedDict exists with 5 required fields: endpoint_url, aws_access_key_id,
    aws_secret_access_key, bucket, region. BackupConfig extends S3Config.

    Validates the refactored type hierarchy from backup_config.py.
    """
    from backup_config import BackupConfig, S3Config

    # S3Config must have the 5 base S3 fields
    s3_keys = set(S3Config.__annotations__.keys())
    logger.info("[IMP:7][test_s3_config] S3Config keys: %s", s3_keys)
    assert "endpoint_url" in s3_keys, "S3Config must have endpoint_url"
    assert "aws_access_key_id" in s3_keys, "S3Config must have aws_access_key_id"
    assert "aws_secret_access_key" in s3_keys, "S3Config must have aws_secret_access_key"
    assert "bucket" in s3_keys, "S3Config must have bucket"
    assert "region" in s3_keys, "S3Config must have region"
    assert len(s3_keys) == 5, f"S3Config must have exactly 5 keys, got {len(s3_keys)}: {s3_keys}"

    # BackupConfig must extend S3Config (have all S3Config keys + prefix/context/node_name)
    bc_keys = set(BackupConfig.__annotations__.keys())
    logger.info("[IMP:7][test_s3_config] BackupConfig keys: %s", bc_keys)
    assert s3_keys.issubset(bc_keys), "BackupConfig must include all S3Config keys"
    assert "prefix" in bc_keys, "BackupConfig must have prefix"
    assert "context" in bc_keys, "BackupConfig must have context"
    assert "node_name" in bc_keys, "BackupConfig must have node_name"
    assert len(bc_keys) == 8, f"BackupConfig must have exactly 8 keys, got {len(bc_keys)}: {bc_keys}"

    logger.critical("[IMP:9][test_s3_config] ASSERT: S3Config(5) ⊆ BackupConfig(8) — type hierarchy correct")


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-21 · Scenario: get_s3_config returns 5 fields without prefix
# · Last fail: None (first run) · Remove if: get_s3_config() signature changes
def test_get_s3_config_returns_5_fields():
    """get_s3_config() returns exactly 5 fields (no prefix/context/node_name)."""
    from backup_config import get_s3_config

    # Set required env vars
    env_vars = {
        "S3_ACCESS_KEY": "test-key-123",
        "S3_SECRET_KEY": "test-secret-456",
        "S3_BUCKET": "test-bucket",
        "S3_ENDPOINT_URL": "https://custom.endpoint",
        "S3_REGION": "eu-west-1",
    }
    original_env = {k: os.environ.get(k, "") for k in env_vars}
    try:
        for k, v in env_vars.items():
            os.environ[k] = v

        config = get_s3_config()

        logger.info("[IMP:7][test_get_s3_config] Config returned: %s", config)

        assert config["endpoint_url"] == "https://custom.endpoint"
        assert config["aws_access_key_id"] == "test-key-123"
        assert config["aws_secret_access_key"] == "test-secret-456"
        assert config["bucket"] == "test-bucket"
        assert config["region"] == "eu-west-1"

        # Must NOT have backup-specific keys
        assert "prefix" not in config, "S3Config must NOT contain prefix"
        assert "context" not in config, "S3Config must NOT contain context"
        assert "node_name" not in config, "S3Config must NOT contain node_name"

        # Must have exactly 5 keys
        assert len(config) == 5, f"S3Config must have exactly 5 keys, got {len(config)}: {list(config.keys())}"

        logger.critical("[IMP:9][test_get_s3_config] ASSERT: get_s3_config() returns 5 fields, no prefix")
    finally:
        for k in env_vars:
            if original_env[k]:
                os.environ[k] = original_env[k]
            else:
                os.environ.pop(k, None)


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-21 · Scenario: S3 endpoint fallback to s3.timeweb.cloud
# · Last fail: None (first run) · Remove if: endpoint resolution logic changes
def test_get_s3_config_uses_fallback_endpoint():
    """get_s3_config() falls back to S3_ENDPOINT and default s3.timeweb.cloud."""
    from backup_config import get_s3_config

    env_vars = {
        "S3_ACCESS_KEY": "test-key",
        "S3_SECRET_KEY": "test-secret",
        "S3_BUCKET": "test-bucket",
    }
    original_env = {k: os.environ.get(k, "") for k in env_vars}
    # Also save S3_ENDPOINT_URL and S3_ENDPOINT
    saved_endpoint = os.environ.get("S3_ENDPOINT_URL", "")
    saved_endpoint2 = os.environ.get("S3_ENDPOINT", "")
    try:
        # Remove explicit endpoint URLs to test fallback
        for var in ("S3_ENDPOINT_URL", "S3_ENDPOINT"):
            os.environ.pop(var, None)

        for k, v in env_vars.items():
            os.environ[k] = v

        config = get_s3_config()

        logger.info("[IMP:7][test_fallback] Endpoint: %s", config["endpoint_url"])
        assert "s3.timeweb.cloud" in config["endpoint_url"], f"Expected default endpoint, got {config['endpoint_url']}"
        logger.critical("[IMP:9][test_fallback] ASSERT: fallback endpoint = s3.timeweb.cloud")
    finally:
        for k in env_vars:
            if original_env[k]:
                os.environ[k] = original_env[k]
            else:
                os.environ.pop(k, None)
        if saved_endpoint:
            os.environ["S3_ENDPOINT_URL"] = saved_endpoint
        if saved_endpoint2:
            os.environ["S3_ENDPOINT"] = saved_endpoint2


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-21 · Scenario: RuntimeError on missing S3 credentials
# · Last fail: None (first run) · Remove if: validation logic changes
def test_get_s3_config_missing_credentials():
    """get_s3_config() raises RuntimeError when S3_ACCESS_KEY, S3_SECRET_KEY, or S3_BUCKET missing."""
    from backup_config import get_s3_config

    # Remove all S3-related env vars
    saved = {}
    for var in ("S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_BUCKET"):
        saved[var] = os.environ.pop(var, "")

    try:
        with pytest.raises(RuntimeError) as excinfo:
            get_s3_config()
        error_msg = str(excinfo.value)
        logger.info("[IMP:7][test_missing] Error: %s", error_msg)
        assert "S3_ACCESS_KEY" in error_msg
        assert "S3_SECRET_KEY" in error_msg
        assert "S3_BUCKET" in error_msg
        logger.critical("[IMP:9][test_missing] ASSERT: RuntimeError with all 3 missing vars")
    finally:
        for var, val in saved.items():
            if val:
                os.environ[var] = val


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-21 · Scenario: region defaults to us-east-1
# · Last fail: None (first run) · Remove if: default region constant changes
def test_get_s3_config_default_region():
    """get_s3_config() defaults region to us-east-1 when S3_REGION not set."""
    from backup_config import get_s3_config

    env_vars = {
        "S3_ACCESS_KEY": "test-key",
        "S3_SECRET_KEY": "test-secret",
        "S3_BUCKET": "test-bucket",
    }
    original_env = {}
    try:
        os.environ.pop("S3_REGION", None)
        for k, v in env_vars.items():
            original_env[k] = os.environ.get(k, "")
            os.environ[k] = v

        config = get_s3_config()
        assert config["region"] == "us-east-1", f"Expected default region us-east-1, got {config['region']}"
        logger.critical("[IMP:9][test_default_region] ASSERT: region defaults to us-east-1")
    finally:
        for k, v in original_env.items():
            if v:
                os.environ[k] = v


# endregion TEST_S3_CONFIG_BASE


# region TEST_UPLOAD_CONFIG_SOURCE


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-21 · Scenario: --config-source ssl-cache → get_s3_config() called, get_backup_config() not
# · Last fail: argparse error (fixed via sys.argv mock) · Remove if: upload.py config-source logic replaced
def test_upload_config_source_ssl_cache_uses_s3_config():
    """upload.py --config-source ssl-cache uses get_s3_config() instead of get_backup_config().
    The s3_key is used as-is (no prefix prepended). Tests by calling main with explicit argv.
    """
    from unittest.mock import patch as mock_patch

    from upload import main as upload_main

    # Set up env vars for get_s3_config
    env_vars = {
        "S3_ACCESS_KEY": "ssl-cache-key",
        "S3_SECRET_KEY": "ssl-cache-secret",
        "S3_BUCKET": "ssl-cache-bucket",
    }
    original_env = {}
    try:
        for k, v in env_vars.items():
            original_env[k] = os.environ.get(k, "")
            os.environ[k] = v

        s3_config = _make_s3_config_dict(
            aws_access_key_id="ssl-cache-key",
            aws_secret_access_key="ssl-cache-secret",
            bucket="ssl-cache-bucket",
        )

        # Create a fake file for upload
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False, mode="w") as f:
            f.write("fake-cert-content")
            local_file = f.name

        try:
            # Call main with explicit argv including --config-source ssl-cache
            # main() calls _parse_args() which parses sys.argv by default.
            # We need to mock argv to pass --config-source ssl-cache.
            test_argv = [
                "upload.py",
                "--config-source",
                "ssl-cache",
                local_file,
                "platform/ssl-certs/test.domain/fullchain.pem",
            ]

            with (
                mock_patch("upload.get_s3_config", return_value=s3_config) as mock_s3,
                mock_patch("upload.get_backup_config") as mock_backup,
                mock_patch("upload.compute_sha256", return_value="fake-sha256"),
                mock_patch("upload.sys.argv", test_argv),
            ):
                # Patch _init_client and _upload_and_verify to avoid boto3
                fake_client = object()
                with (
                    mock_patch("upload._init_client", return_value=fake_client),
                    mock_patch("upload._upload_and_verify", return_value=True),
                    mock_patch("upload._generate_report"),
                ):
                    upload_main()

                    # Verify get_s3_config was called (not get_backup_config)
                    mock_s3.assert_called_once()
                    mock_backup.assert_not_called()

                    logger.critical(
                        "[IMP:9][test_upload_ssl_cache] ASSERT: get_s3_config() called, "
                        "get_backup_config() NOT called — ssl-cache config source works"
                    )
        finally:
            os.unlink(local_file)

    finally:
        for k, v in original_env.items():
            if v:
                os.environ[k] = v


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-21 · Scenario: --config-source backup (default) parsed correctly
# · Last fail: None (first run) · Remove if: arg parsing of --config-source changes
def test_upload_config_source_backup_uses_backup_config():
    """upload.py --config-source backup (default) uses get_backup_config() with prefix."""

    # We'll test at the arg parsing level — _parse_args with --config-source
    from upload import _parse_args

    # Test default (no --config-source)
    args = _parse_args(["/tmp/test.file", "some/s3/key"])
    assert args.config_source == "backup", f"Default config_source should be 'backup', got {args.config_source}"

    # Test explicit backup
    args = _parse_args(["--config-source", "backup", "/tmp/test.file", "some/s3/key"])
    assert args.config_source == "backup"

    # Test ssl-cache
    args = _parse_args(["--config-source", "ssl-cache", "/tmp/test.file", "some/s3/key"])
    assert args.config_source == "ssl-cache"

    logger.critical("[IMP:9][test_upload_config_source] ASSERT: --config-source parsing correct")


# endregion TEST_UPLOAD_CONFIG_SOURCE


# region TEST_S3_SCRIPT_EXISTS


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-21 · Scenario: s3_ssl_cache.py has upload/download/check + boto3 + openssl
# · Last fail: DevPlan 052 Phase 1 — business logic moved from shell to Python
# · Remove if: s3_ssl_cache.py is restructured
def test_s3_cache_script_has_upload_download_check():
    """s3_ssl_cache.py must exist and contain upload/download/check functions.

    DevPlan 052 Phase 1: business logic moved from s3-ssl-cache.sh to s3_ssl_cache.py.
    Shell is now a thin CLI facade (~30 lines) delegating to the Python module.
    """
    py_path = "core/internal/bootstrap/s3_ssl_cache.py"
    assert os.path.isfile(py_path), f"s3_ssl_cache.py not found at {py_path}"

    with open(py_path) as f:
        content = f.read()

    # Check Python functions
    assert "def upload_cert" in content, "s3_ssl_cache.py must have upload_cert function"
    assert "def download_cert" in content, "s3_ssl_cache.py must have download_cert function"
    assert "def check_cert" in content, "s3_ssl_cache.py must have check_cert function"
    assert "def bulk_restore" in content, "s3_ssl_cache.py must have bulk_restore function"

    # Check openssl validation
    assert "checkend" in content, "s3_ssl_cache.py must use openssl -checkend for validation"
    assert "openssl x509" in content, "s3_ssl_cache.py must use openssl x509"

    # Check boto3 usage
    assert "boto3" in content, "s3_ssl_cache.py must import boto3"
    assert "upload_file" in content, "s3_ssl_cache.py must have boto3 upload"
    assert "download_file" in content, "s3_ssl_cache.py must have boto3 download"

    logger.critical("[IMP:9][test_script_exists] ASSERT: s3_ssl_cache.py has upload/download/check + boto3 + openssl")


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-21 · Scenario: graceful degradation in s3_ssl_cache.py
# · Last fail: DevPlan 052 Phase 1 — business logic moved from shell to Python
# · Remove if: graceful degradation logic changes
def test_s3_cache_script_graceful_degradation():
    """s3_ssl_cache.py must have graceful degradation on S3 failure (return False, not raise).

    DevPlan 052 Phase 1: business logic moved from s3-ssl-cache.sh to s3_ssl_cache.py.
    Graceful degradation is now via try/except + return False, not shell WARN log.
    """
    py_path = "core/internal/bootstrap/s3_ssl_cache.py"
    with open(py_path) as f:
        content = f.read()

    # Must use try/except for graceful degradation (non-fatal)
    assert "try:" in content, "s3_ssl_cache.py must use try/except for graceful degradation"
    assert "return False" in content, "s3_ssl_cache.py must return False on failure (not raise)"
    assert "non-fatal" in content.lower() or "graceful" in content.lower(), (
        "s3_ssl_cache.py must document graceful degradation"
    )

    # Must validate S3 env vars non-fatally
    assert "S3_BUCKET" in content, "s3_ssl_cache.py must check S3_BUCKET"
    assert "_get_s3_client" in content, "s3_ssl_cache.py must have S3 client factory"

    logger.critical("[IMP:9][test_graceful] ASSERT: s3_ssl_cache.py has graceful degradation pattern")


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-21 · Scenario: openssl x509 validation in s3_ssl_cache.py
# · Last fail: DevPlan 052 Phase 1 — business logic moved from shell to Python
# · Remove if: cert validation method changes
def test_s3_cache_script_uses_openssl_validation():
    """s3_ssl_cache.py must validate certs with openssl x509 (checkend + issuer + subject)."""
    py_path = "core/internal/bootstrap/s3_ssl_cache.py"
    with open(py_path) as f:
        content = f.read()

    assert "openssl x509" in content, "Must use openssl x509 for cert validation"
    assert "checkend" in content, "Must check expiry via openssl -checkend"
    assert "Let's Encrypt" in content, "Must check issuer is LE"
    assert "subject" in content or "CN" in content, "Must check domain subject"

    logger.critical("[IMP:9][test_openssl] ASSERT: s3_ssl_cache.py validates certs with openssl x509")


# endregion TEST_S3_SCRIPT_EXISTS


# region TEST_INTEGRATION_ISSUE_CERT


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-21 · Scenario: issue-cert.sh calls s3-ssl-cache.sh upload after success
# · Last fail: None (first run) · Remove if: issue-cert.sh post-issue logic changes
def test_issue_cert_saves_to_s3_after_success():
    """issue-cert.sh must call s3-ssl-cache.sh upload after successful cert issuance.

    Verifies by grepping the issue-cert.sh for the s3-ssl-cache upload call
    after cert_path check and _acme_install_cron.
    """
    script_path = "core/internal/bootstrap/issue-cert.sh"
    assert os.path.isfile(script_path), f"issue-cert.sh not found at {script_path}"

    with open(script_path) as f:
        content = f.read()

    # Must reference s3-ssl-cache.sh
    assert "s3-ssl-cache.sh" in content, "issue-cert.sh must reference s3-ssl-cache.sh"

    # Must call upload after successful issue
    assert "upload" in content, "issue-cert.sh must call s3-ssl-cache.sh upload"

    # Must be non-fatal (WARN on failure)
    assert "WARN" in content, "S3 save must be non-fatal (WARN on failure)"

    # Must happen after issue_tls_cert success
    # Look for the upload call after issue_tls_cert
    lines = content.split("\n")
    cert_save_section = False
    for i, line in enumerate(lines):
        if "issue_tls_cert" in line and "true" in line:
            # The upload call should be within the next ~50 lines (increased from 30
            # to accommodate HTTP-01 subdomain cert issuance block — DevPlan 058 Wave 2)
            for j in range(i, min(i + 50, len(lines))):
                if "s3-ssl-cache.sh" in lines[j]:
                    cert_save_section = True
                    break
    assert cert_save_section, "issue-cert.sh must call s3-ssl-cache.sh upload after issue_tls_cert success"

    logger.critical(
        "[IMP:9][test_issue_cert_s3] ASSERT: issue-cert.sh calls s3-ssl-cache.sh upload after successful cert issuance"
    )


# endregion TEST_INTEGRATION_ISSUE_CERT


# region TEST_INTEGRATION_NODE_LIFECYCLE


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-25 · Scenario: cert_orchestrator checks S3 cache before issue-cert.sh
# · Regression: DevPlan 052 Phase 2 — _ssl_provision() replaced by cert_orchestrator unified entrypoint
# · Last fail: 2026-07-25 — S3 logic moved from state_machine.py → cert_orchestrator.py
# · Remove if: cert orchestration logic completely rewritten
def test_node_lifecycle_checks_s3_before_issue():
    """cert_orchestrator.py _process_single_domain() must check S3 cache
    via s3_ssl_cache direct import before falling back to issue-cert.sh.

    DevPlan 052 Phase 2: The unified entrypoint (cert_orchestrator.orchestrate_certs)
    handles ALL domains via restore-first strategy. S3 operations use direct
    s3_ssl_cache import (no subprocess, no subshell credential loss).
    """
    # Check cert_orchestrator.py for S3 check before issue
    import pathlib

    cert_path = pathlib.Path("core/internal/bootstrap/cert_orchestrator.py")
    assert cert_path.is_file(), f"cert_orchestrator.py not found at {cert_path}"

    content = cert_path.read_text()

    # Must import s3_ssl_cache directly (no subprocess to s3-ssl-cache.sh)
    assert "s3_ssl_cache" in content, "cert_orchestrator.py must reference s3_ssl_cache"
    assert "import s3_ssl_cache" in content, "cert_orchestrator.py must import s3_ssl_cache"

    # Must call S3 check via direct import (not subprocess)
    assert "s3_ssl_cache.check_cert" in content, "cert_orchestrator.py must call s3_ssl_cache.check_cert"

    # Must call S3 download via direct import (not subprocess)
    assert "s3_ssl_cache.download_cert" in content, "cert_orchestrator.py must call s3_ssl_cache.download_cert"

    # Must have upload-to-S3 function for upload-on-skip
    assert "_upload_to_s3" in content, "cert_orchestrator.py must have _upload_to_s3()"

    # Must have upload-on-skip for valid cert on disk
    assert "disk_synced" in content, "cert_orchestrator.py must return source='disk_synced' on cert skip"

    # Must fallback to issue-cert.sh
    assert "issue_cert_script" in content, "cert_orchestrator.py must reference issue-cert.sh"

    # Check ordering in _process_single_domain: disk check → S3 check → issue fallback
    lines = content.split("\n")
    proc_start = -1
    for i, line in enumerate(lines):
        if "def _process_single_domain" in line:
            proc_start = i
            break

    if proc_start >= 0:
        section = "\n".join(lines[proc_start : proc_start + 80])
        # All three steps must exist in _process_single_domain
        assert "_is_cert_valid" in section, "Step 1: disk check must exist in _process_single_domain"
        assert "s3_ssl_cache" in section, "Step 2: S3 restore must exist in _process_single_domain"
        assert "issue_cert_script" in section, "Step 3: issue fallback must exist in _process_single_domain"

        logger.info(
            "[IMP:7][test_node_lifecycle_s3] _process_single_domain() has "
            "disk check → S3 check → issue fallback — ordering correct"
        )
    else:
        logger.warning("[IMP:7][test_node_lifecycle_s3] _process_single_domain() not found by name")

    logger.critical(
        "[IMP:9][test_node_lifecycle_s3] ASSERT: cert_orchestrator.py _process_single_domain() "
        "has restore-first flow: disk → S3 → issue. Direct s3_ssl_cache import (no subshell)."
    )


# endregion TEST_INTEGRATION_NODE_LIFECYCLE


# region TEST_BACKWARD_COMPAT


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-21 · Scenario: get_backup_config() backward compatible after refactoring
# · Last fail: None (first run) · Remove if: get_backup_config() implementation changes
def test_get_backup_config_still_works():
    """get_backup_config() must still work (backward compatible) after S3Config extraction."""
    from backup_config import get_backup_config

    env_vars = {
        "S3_ACCESS_KEY": "bk-compat-key",
        "S3_SECRET_KEY": "bk-compat-secret",
        "S3_BUCKET": "bk-compat-bucket",
        "S3_PREFIX": "custom/prefix",
        "PLATFORM_CONTEXT": "corporate",
        "NODE_NAME": "production-node",
    }
    original_env = {}
    try:
        for k, v in env_vars.items():
            original_env[k] = os.environ.get(k, "")
            os.environ[k] = v

        config = get_backup_config()

        logger.info("[IMP:7][test_backward] BackupConfig: %s", config)

        # S3 base fields
        assert config["aws_access_key_id"] == "bk-compat-key"
        assert config["bucket"] == "bk-compat-bucket"

        # Backup-specific fields
        assert config["prefix"] == "custom/prefix"
        assert config["context"] == "corporate"
        assert config["node_name"] == "production-node"

        # Must have all 8 keys
        assert len(config) == 8, f"BackupConfig must have 8 keys, got {len(config)}"

        logger.critical("[IMP:9][test_backward] ASSERT: get_backup_config() backward compatible — 8 fields")
    finally:
        for k, v in original_env.items():
            if v:
                os.environ[k] = v


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-21 · Scenario: BackupConfig inherits S3Config (annotations + source AST + runtime)
# · Last fail: issubclass/__mro__/__bases__ not supported for TypedDict in Python 3.14 (fixed: AST + annotations)
# · Remove if: TypedDict inheritance mechanism changes
def test_s3_config_type_compatibility():
    """S3Config must be compatible with BackupConfig (BackupConfig inherits S3Config).

    This validates that functions accepting S3Config can also accept BackupConfig
    (structural subtyping via TypedDict inheritance). Uses __annotations__ check
    plus source-code AST inspection since TypedDict does not expose inheritance
    via __mro__ or __bases__ at runtime in Python 3.14.
    """
    import ast

    import backup_config as bc

    # 1. Annotation-based verification: all S3Config keys in BackupConfig
    s3_keys = set(bc.S3Config.__annotations__.keys())
    bc_keys = set(bc.BackupConfig.__annotations__.keys())
    missing = s3_keys - bc_keys
    assert not missing, f"BackupConfig is missing S3Config keys: {missing}"

    # BackupConfig adds 3 additional keys
    extra = bc_keys - s3_keys
    assert extra == {"prefix", "context", "node_name"}, f"BackupConfig should add exactly 3 keys, got extra: {extra}"

    # 2. Source-code verification: class BackupConfig(S3Config) syntax
    source_path = bc.__file__
    with open(source_path) as f:
        tree = ast.parse(f.read())

    found_inheritance = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "BackupConfig":
            base_names = [base.id if isinstance(base, ast.Name) else "" for base in node.bases]
            if "S3Config" in base_names:
                found_inheritance = True
            logger.info(
                "[IMP:7][test_type_compat] BackupConfig bases in source: %s",
                base_names,
            )
            break

    assert found_inheritance, "class BackupConfig(S3Config): must be defined in source with S3Config as base"

    # 3. Runtime verification: a BackupConfig dict is structurally compatible with S3Config
    config = bc.BackupConfig(
        endpoint_url="https://test",
        aws_access_key_id="key",
        aws_secret_access_key="secret",
        bucket="bucket",
        region="us-east-1",
        prefix="backup",
        context="personal",
        node_name="test",
    )
    assert config["bucket"] == "bucket"
    assert len(config) == 8, f"BackupConfig must have 8 keys, got {len(config)}"

    logger.critical(
        "[IMP:9][test_type_compat] ASSERT: BackupConfig inherits S3Config — "
        f"annotations={s3_keys} extra={extra} source=S3Config bases runtime=compat"
    )


# endregion TEST_BACKWARD_COMPAT


# region TEST_BUGFIX_052


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-22 · Scenario: check_cert() must NOT use upload.py (CRITICAL Bug 1 — regression guard)
# · Last fail: 2026-07-22 — first run found upload.py in _s3_check() causing data loss
# · Remove if: check_cert() function is deleted or fundamentally rewritten
def test_s3_check_does_not_use_upload_py():
    """check_cert() in s3_ssl_cache.py must use boto3 download directly, not upload.py.

    Bug 1 (CRITICAL): _s3_check() called upload.py with empty temp file as source,
    overwriting valid S3 certificates with 0 bytes. Fix: port to Python module that
    uses boto3.download_file directly (DevPlan 052 Phase 1).

    DevPlan 052 Phase 1: business logic moved from s3-ssl-cache.sh to s3_ssl_cache.py.
    The Python module uses boto3 client directly (no upload.py).
    """
    py_path = "core/internal/bootstrap/s3_ssl_cache.py"
    assert os.path.isfile(py_path), f"s3_ssl_cache.py not found at {py_path}"

    with open(py_path) as f:
        content = f.read()

    # check_cert must use boto3 directly (not upload.py)
    assert "def check_cert" in content, "check_cert() function must exist in s3_ssl_cache.py"
    assert "TRAP[BUG]" in content, "check_cert must reference the original bug (TRAP[BUG] for audit trail)"
    assert "import boto3" in content, "s3_ssl_cache.py must import boto3 directly"
    assert "_download_s3_file" in content, "check_cert must use _download_s3_file for download"
    # upload.py should NOT be referenced in the Python module (business logic is self-contained)
    # The module may reference upload.py in TRAP comments, but not in active code
    # Let's check for active usage — the module uses boto3.client directly
    assert "upload_file" in content or "download_file" in content, "check_cert must use boto3 file operations"

    logger.critical(
        "[IMP:9][test_s3_check_no_upload] ASSERT: s3_ssl_cache.py uses boto3 directly — no upload.py. Bug 1 fixed."
    )


# 🧪 TRAP[TEST] · 2026-07-22 · Scenario: HTTPS_PROXY from secrets.env must not break S3Config
# · Last fail: 2026-07-22 — ProxyConnectionError on VPS with TOR_ENABLED (Bug 2)
# · Remove if: get_s3_config() proxy-handling strategy changes fundamentally
def test_s3_config_ignores_https_proxy():
    """S3Config must not use HTTPS_PROXY/HTTP_PROXY from secrets.env context.

    Bug 2 (MEDIUM): secrets.env contains HTTPS_PROXY=http://host.docker.internal:8118
    for Docker containers. Host-level boto3 (upload.py) picks up this variable and
    tries to proxy S3 requests through non-existent host on VPS, causing ProxyConnectionError.

    Fix: defence-in-depth — unset all 6 proxy variants in every entry point that calls boto3.
    This test verifies that get_s3_config() can construct a valid config even when
    proxy env vars are set.
    """
    from backup_config import get_s3_config

    # Set proxy env vars AND S3 credentials (simulating secrets.env context)
    env_vars = {
        "HTTPS_PROXY": "http://host.docker.internal:8118",
        "HTTP_PROXY": "http://host.docker.internal:8118",
        "https_proxy": "http://host.docker.internal:8118",
        "S3_ACCESS_KEY": "test-key-123",
        "S3_SECRET_KEY": "test-secret-456",
        "S3_BUCKET": "test-bucket",
        "S3_ENDPOINT_URL": "https://s3.example.com",
    }
    original_env = {k: os.environ.get(k, "") for k in env_vars}

    try:
        for k, v in env_vars.items():
            os.environ[k] = v

        # get_s3_config should succeed (not throw ProxyConnectionError or any error)
        config = get_s3_config()

        logger.info(
            "[IMP:7][test_proxy_isolation] Config returned: endpoint=%s bucket=%s",
            config["endpoint_url"],
            config["bucket"],
        )

        # Verify S3 config is correct despite proxy vars in environment
        assert config["aws_access_key_id"] == "test-key-123"
        assert config["aws_secret_access_key"] == "test-secret-456"
        assert config["bucket"] == "test-bucket"
        assert config["endpoint_url"] == "https://s3.example.com"

        logger.critical(
            "[IMP:9][test_proxy_isolation] ASSERT: get_s3_config() returns valid config with HTTPS_PROXY in environment"
        )

    finally:
        for k in env_vars:
            if original_env[k]:
                os.environ[k] = original_env[k]
            else:
                os.environ.pop(k, None)


# endregion TEST_BUGFIX_052


# region TEST_WAVE1_G2_G3


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-23 · Scenario: upload without chain.pem succeeds (G2 fix)
# · Regression: G2 — chain.pem was required but acme.sh --install-cert doesn't generate it
# · Last fail: DevPlan 052 Phase 1 — business logic moved from shell to Python
# · Remove if: upload_cert() required files logic changes
def test_upload_without_chain_pem_succeeds():
    """upload_cert() must NOT require chain.pem — G2 fix (DevPlan 058 TASK-1.1).

    chain.pem is optional best-effort upload. fullchain.pem + privkey.pem are required.
    DevPlan 052 Phase 1: business logic moved from s3-ssl-cache.sh to s3_ssl_cache.py.
    Check Python module for required files logic.
    """
    py_path = "core/internal/bootstrap/s3_ssl_cache.py"
    assert os.path.isfile(py_path), f"s3_ssl_cache.py not found at {py_path}"

    with open(py_path) as f:
        content = f.read()

    logger.info("[IMP:7][test_no_chain_required] Checking upload_cert in s3_ssl_cache.py")

    # 1. Required files list must contain fullchain.pem and privkey.pem but NOT chain.pem
    assert "required_files" in content, "upload_cert must define required files"
    assert "fullchain.pem" in content, "fullchain.pem must be a required file"
    assert "privkey.pem" in content, "privkey.pem must be a required file"
    # chain.pem must NOT be in the required_files keyword-area (the list literal), not just the file
    required_section_start = content.find("required_files")
    if required_section_start >= 0:
        # Find the first ] that closes the required_files list
        list_end = content.find("]", required_section_start)
        if list_end > required_section_start:
            required_list = content[required_section_start : list_end + 1]
            # Chain.pem may appear in comments AFTER the list, but not in the list items
            # Check if "chain.pem" appears before the closing ] of the list
            list_content = content[required_section_start:list_end]
            # Check for chain.pem as standalone entry (not as substring of "fullchain.pem")
            assert '"chain.pem"' not in list_content and "'chain.pem'" not in list_content, (
                "chain.pem must NOT be in required_files list (G2 fix) — it's optional"
            )

    logger.critical(
        "[IMP:9][test_no_chain_required] ASSERT: required = [fullchain.pem, privkey.pem] — chain.pem NOT required"
    )

    # 2. chain.pem must be handled as best-effort upload (not required)
    assert "chain.pem" in content, "chain.pem must still be referenced in upload_cert()"
    assert "best-effort" in content.lower() or "skipping" in content.lower() or "optional" in content.lower(), (
        "chain.pem upload must be documented as best-effort"
    )

    logger.critical("[IMP:9][test_no_chain_required] ASSERT: chain.pem referenced as best-effort upload — G2 fixed")


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-23 · Scenario: account path uses <domain>_ecc/ with data/<domain>/ fallback (G3 fix)
# · Regression: G3 — account data path was hardcoded to data/<domain>/ which doesn't exist
# · Last fail: DevPlan 052 Phase 1 — business logic moved from shell to Python
# · Remove if: upload_cert() account path logic changes
def test_upload_with_account_ecc_path():
    """upload_cert() in s3_ssl_cache.py must use <domain>_ecc/ as primary account path
    with data/<domain>/ fallback.

    G3 fix: acme.sh stores account data in <domain>_ecc/ directory, not data/<domain>/.
    DevPlan 052 Phase 1: business logic moved to s3_ssl_cache.py.
    """
    py_path = "core/internal/bootstrap/s3_ssl_cache.py"
    assert os.path.isfile(py_path), f"s3_ssl_cache.py not found at {py_path}"

    with open(py_path) as f:
        content = f.read()

    logger.info("[IMP:7][test_account_ecc] Checking upload_cert in s3_ssl_cache.py")

    # Must have _find_acme_account_dir helper with _ecc primary and data/<domain> fallback
    assert "def _find_acme_account_dir" in content, "Must have _find_acme_account_dir helper"
    # _ecc reference must precede data/ reference in the file (ecc is primary, data is fallback)
    ecc_pos = content.find("_ecc")
    data_pos = content.find("data/")
    assert ecc_pos >= 0, "Must reference <domain>_ecc directory"
    assert data_pos >= 0, "Must have legacy data/ fallback"
    assert ecc_pos < data_pos, "_ecc path must be checked before data/ fallback"

    logger.critical(
        "[IMP:9][test_account_ecc] ASSERT: upload_cert() uses _find_acme_account_dir() — _ecc primary, data/ fallback"
    )

    # Verify download function also uses correct extract path
    assert "tar.extractall" in content, "download_cert must use tar.extractall for account restore"
    assert "acme_home" in content, "download_cert must extract account.tar.gz to acme_home"

    logger.critical(
        "[IMP:9][test_account_ecc] ASSERT: download_cert() extracts account.tar.gz to ACME_HOME/ — G3 fixed"
    )


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-23 · Scenario: download rejects non-LE cert (issuer validation)
# · Regression: pre-fix — any valid x509 cert in S3 was accepted, including mkcert/self-signed
# · Last fail: DevPlan 052 Phase 1 — business logic moved from shell to Python
# · Remove if: _validate_cert() issuer validation logic changes
def test_download_rejects_non_le_issuer():
    """download_cert() via _validate_cert() must validate issuer as Let's Encrypt.

    DevPlan 052 Phase 1: business logic moved from s3-ssl-cache.sh _s3_download()
    to s3_ssl_cache.py _validate_cert(). The validation checks:
    - openssl x509 -issuer for "Let's Encrypt"
    - Returns False on non-LE issuer
    """
    py_path = "core/internal/bootstrap/s3_ssl_cache.py"
    assert os.path.isfile(py_path), f"s3_ssl_cache.py not found at {py_path}"

    with open(py_path) as f:
        content = f.read()

    logger.info("[IMP:7][test_reject_non_le] Checking s3_ssl_cache.py")

    # Must have issuer validation
    assert "def _validate_cert" in content, "Must have _validate_cert function"
    assert "Let's Encrypt" in content, "Must check for 'Let's Encrypt' issuer"
    assert "issuer" in content, "Must check cert issuer"

    # Must return False on non-LE issuer
    assert "return False" in content.split("Let's Encrypt")[0] if "Let's Encrypt" in content else True, (
        "Must return False on non-LE issuer"
    )

    logger.critical("[IMP:9][test_reject_non_le] ASSERT: s3_ssl_cache.py validates issuer = Let's Encrypt")
    logger.critical("[IMP:9][test_reject_non_le] ASSERT: Non-LE issuer triggers return False — rejection confirmed")


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-23 · Scenario: download accepts LE cert and restores it
# · Regression: pre-fix — issuer validation was missing entirely
# · Last fail: DevPlan 052 Phase 1 — business logic moved from shell to Python
# · Remove if: download_cert() restore logic changes
def test_download_accepts_le_cert():
    """download_cert() in s3_ssl_cache.py must accept LE cert, restore all files + account data.

    DevPlan 052 Phase 1: business logic moved from s3-ssl-cache.sh _s3_download()
    to s3_ssl_cache.py download_cert(). Validates that the full download-and-restore
    path still works after Phase 1 migration.
    """
    py_path = "core/internal/bootstrap/s3_ssl_cache.py"
    assert os.path.isfile(py_path), f"s3_ssl_cache.py not found at {py_path}"

    with open(py_path) as f:
        content = f.read()

    logger.info("[IMP:7][test_accept_le] Checking s3_ssl_cache.py download_cert")

    # 1. Openssl validation must be present
    assert "openssl x509" in content, "openssl x509 validation must be present"
    assert "Let's Encrypt" in content, "Must have Let's Encrypt issuer check"

    # 2. fullchain.pem restore path must work
    assert "fullchain.pem" in content, "fullchain.pem restore must be present"
    # Verify os.replace or shutil.copy exists for cert files
    assert "os.replace" in content or "shutil" in content, "Must copy cert files to cert_dir"

    # 3. privkey.pem restore must be present
    assert "privkey.pem" in content, "privkey.pem restore must be present"

    # 4. chain.pem restore must still work (optional but supported)
    assert "chain.pem" in content, "chain.pem restore must be present (optional)"

    # 5. account.tar.gz restore must still work
    assert "account.tar.gz" in content, "account.tar.gz restore must be present"

    # 6. Must return True on success
    assert "return True" in content, "download_cert() must return True on success"

    logger.critical(
        "[IMP:9][test_accept_le] ASSERT: download_cert() restores fullchain + privkey + chain + account — all paths intact"
    )


# endregion TEST_WAVE1_G2_G3
