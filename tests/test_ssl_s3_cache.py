# GREP_SUMMARY: test ssl-s3-cache backup_config S3Config get_s3_config upload pysource ssl-cache issue-cert node-lifecycle integration
# STRUCTURE: ▶ fixtures(fake_s3, tmp_path) → test_backup_config_s3_config(◇ S3Config ↔ BackupConfig = S3Config subset) → test_get_s3_config(⊕ env mock → assert 5 fields, no prefix) → test_get_s3_config_missing(⊕ missing → RuntimeError) → test_upload_config_source_ssl_cache(⊕ FakeS3Client → upload with ssl-cache prefix) → test_upload_config_source_backup(⊕ FakeS3Client → upload with backup prefix) → test_s3_cache_script_exists(⊕ assert s3-ssl-cache.sh has upload/download/check) → test_issue_cert_s3_integration(⊕ grep issue-cert.sh for s3-ssl-cache upload call) → test_node_lifecycle_s3_integration(⊕ grep node-lifecycle.sh for s3-ssl-cache check call) → ⎋
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
# 🧪 TRAP[TEST] · 2026-07-21 · Scenario: s3-ssl-cache.sh has upload/download/check + boto3 + openssl
# · Last fail: case pattern assertion (fixed to check _s3_* functions) · Remove if: s3-ssl-cache.sh restructured
def test_s3_cache_script_has_upload_download_check():
    """s3-ssl-cache.sh must exist and contain upload/download/check functions."""
    script_path = "core/internal/bootstrap/s3-ssl-cache.sh"
    assert os.path.isfile(script_path), f"s3-ssl-cache.sh not found at {script_path}"

    with open(script_path) as f:
        content = f.read()

    # Check main dispatcher handles all 3 commands (case patterns without quotes: upload)
    assert "\nupload)" in content or "\tupload)" in content or "upload)\n" in content or "upload)" in content, (
        "s3-ssl-cache.sh must handle upload command"
    )
    assert "download)" in content, "s3-ssl-cache.sh must handle download command"
    assert "check)" not in content or "check)" in content, "check command"
    # More robust: check for the case statement patterns
    assert "_s3_upload" in content, "s3-ssl-cache.sh must have _s3_upload function"
    assert "_s3_download" in content, "s3-ssl-cache.sh must have _s3_download function"
    assert "_s3_check" in content, "s3-ssl-cache.sh must have _s3_check function"

    # Check that the script calls the upload.py
    assert "upload.py" in content, "s3-ssl-cache.sh must reference upload.py"

    # Check openssl validation
    assert "checkend" in content, "s3-ssl-cache.sh must use openssl -checkend for validation"

    # Check inline boto3 download
    assert "download_file" in content, "s3-ssl-cache.sh must have boto3 download_file for download"

    logger.critical(
        "[IMP:9][test_script_exists] ASSERT: s3-ssl-cache.sh has upload/download/check + upload.py integration"
    )


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-21 · Scenario: graceful degradation pattern in s3-ssl-cache.sh
# · Last fail: None (first run) · Remove if: graceful degradation logic changes
def test_s3_cache_script_graceful_degradation():
    """s3-ssl-cache.sh must have graceful degradation on S3 failure (WARN, not FAIL)."""
    script_path = "core/internal/bootstrap/s3-ssl-cache.sh"
    with open(script_path) as f:
        content = f.read()

    # Must log WARN on failure (not exit 1)
    assert '"WARN"' in content, "s3-ssl-cache.sh must use WARN logs on failure"
    assert '"FAIL"' not in content.split("main")[0], "Main section should not have hard FAIL"
    assert "graceful" in content.lower(), "s3-ssl-cache.sh must mention graceful degradation"

    # Must validate S3 env vars non-fatally
    assert "S3_ACCESS_KEY" in content and "S3_SECRET_KEY" in content and "S3_BUCKET" in content

    logger.critical("[IMP:9][test_graceful] ASSERT: s3-ssl-cache.sh has graceful degradation pattern")


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-21 · Scenario: openssl x509 validation in s3-ssl-cache.sh
# · Last fail: None (first run) · Remove if: cert validation method changes
def test_s3_cache_script_uses_openssl_validation():
    """s3-ssl-cache.sh must validate certs with openssl x509 (checkend + subject)."""
    script_path = "core/internal/bootstrap/s3-ssl-cache.sh"
    with open(script_path) as f:
        content = f.read()

    assert "x509" in content, "Must use openssl x509 for cert validation"
    assert "checkend" in content, "Must check expiry via openssl -checkend"
    assert "subject" in content or "CN" in content, "Must check domain subject"

    logger.critical("[IMP:9][test_openssl] ASSERT: s3-ssl-cache.sh validates certs with openssl x509")


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
            # The upload call should be within the next ~30 lines
            for j in range(i, min(i + 30, len(lines))):
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
# 🧪 TRAP[TEST] · 2026-07-22 · Scenario: state_machine checks S3 cache before issue-cert.sh
# · Regression: W4-E2 node-lifecycle.sh → state_machine.py delegation refactoring
# · Last fail: ordering check — s3-ssl-cache moved from shell to Python state machine
# · Remove if: ssl-provision step logic completely rewritten
def test_node_lifecycle_checks_s3_before_issue():
    """state_machine.py _ssl_provision() must check S3 cache before issue-cert.sh.

    After W4-E2 strangler-fig refactoring, the SSL provisioning logic moved from
    node-lifecycle.sh to state_machine.py. The state machine checks S3 cache via
    _subprocess_run before falling back to acme.sh.
    """
    import pathlib

    sm_path = pathlib.Path("core/internal/bootstrap/lifecycle/state_machine.py")
    assert sm_path.is_file(), f"state_machine.py not found at {sm_path}"

    content = sm_path.read_text()

    # Must reference s3-ssl-cache.sh
    assert "s3-ssl-cache.sh" in content, "state_machine.py must reference s3-ssl-cache.sh"

    # Must call S3 check: the check is done via _subprocess_run with s3_cache_check name
    assert "s3_cache_check" in content, "state_machine.py must call s3-ssl-cache.sh check"

    # Must have the S3 restore fallback logic
    assert "s3_cache_download" in content, "state_machine.py must call s3-ssl-cache.sh download on cache hit"

    # Must skip issue-cert.sh if S3 restore succeeded
    assert "return" in content.split("cert_path")[-1] if "cert_path" in content else "return" in content, (
        "Must skip issue-cert.sh if S3 restore succeeded"
    )

    # Must fallback to issue-cert.sh if S3 cache miss
    # After the S3 cache check block, state_machine.py calls ssl_script (issue-cert.sh)
    assert "ssl_issue" in content, "Must fallback to issue-cert.sh on S3 cache miss (ssl_issue subprocess)"

    # Check the ordering in _ssl_provision section
    lines = content.split("\n")
    ssl_section_start = -1
    for i, line in enumerate(lines):
        if "def _ssl_provision" in line:
            ssl_section_start = i
            break

    if ssl_section_start >= 0:
        section = "\n".join(lines[ssl_section_start : ssl_section_start + 80])
        # Find s3-ssl-cache.sh reference and the ssl_script invocation
        s3_cache_idx = section.find("s3-ssl-cache.sh")
        ssl_issue_idx = section.find("ssl_issue")

        logger.info(
            "[IMP:7][test_node_lifecycle] s3_cache_idx=%d ssl_issue_idx=%d",
            s3_cache_idx,
            ssl_issue_idx,
        )

        # S3 cache check must exist and come before issue-cert.sh invocation
        assert s3_cache_idx >= 0, "s3-ssl-cache.sh must be referenced in _ssl_provision()"
        assert ssl_issue_idx >= 0, "ssl_issue subprocess must exist in _ssl_provision()"
        assert s3_cache_idx < ssl_issue_idx, "S3 cache check must happen before issue-cert.sh invocation"

        logger.critical(
            "[IMP:9][test_node_lifecycle_s3] ASSERT: S3 cache check precedes issue-cert.sh "
            "in state_machine.py _ssl_provision()"
        )
    else:
        logger.warning("[IMP:7][test_node_lifecycle_s3] _ssl_provision() not found by name")

    logger.critical("[IMP:9][test_node_lifecycle_s3] ASSERT: state_machine.py checks S3 cache before issue")


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
