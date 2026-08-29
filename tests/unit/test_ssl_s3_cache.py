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

import contextlib
import logging
import os
import pathlib
import sys
from pathlib import Path

import pytest

# Add backup-cron scripts path for imports of backup_config, upload modules
_backup_cron_scripts = str(
    Path(Path(__file__).parent / "../.." / "core" / "modules" / "backup-cron" / "scripts").resolve()
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
    config.update({
        "prefix": "platform/backups",
        "context": "personal",
        "node_name": "test-node",
    })
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
# 🧪 TRAP[TEST] · 2026-07-21 · Scenario: get_s3_config() дефолты — fallback endpoint
# ·   (S3_ENDPOINT_URL unset → s3.timeweb.cloud) и default region (S3_REGION unset → ru-1)
# · Last fail: None (first run) · Remove if: default-resolution logic changes
@pytest.mark.parametrize(
    ("pop_var", "field", "expected", "substring"),
    [
        pytest.param("S3_ENDPOINT_URL", "endpoint_url", "s3.timeweb.cloud", True, id="fallback-endpoint"),
        pytest.param("S3_REGION", "region", "ru-1", False, id="default-region"),
    ],
)
def test_get_s3_config_defaults(pop_var, field, expected, substring):
    """get_s3_config(): fallback endpoint (s3.timeweb.cloud) и default region (ru-1)."""
    from backup_config import get_s3_config

    env_vars = {
        "S3_ACCESS_KEY": "test-key",
        "S3_SECRET_KEY": "test-secret",
        "S3_BUCKET": "test-bucket",
    }
    original_env = {k: os.environ.get(k, "") for k in env_vars}
    saved_popped = os.environ.get(pop_var, "")
    try:
        # Remove the var under test to exercise the default/fallback path
        os.environ.pop(pop_var, None)

        for k, v in env_vars.items():
            os.environ[k] = v

        config = get_s3_config()

        logger.info("[IMP:7][test_get_s3_config_defaults] %s=%s", field, config[field])
        if substring:
            assert expected in config[field], f"Expected default containing {expected!r}, got {config[field]!r}"
        else:
            assert config[field] == expected, f"Expected default {expected!r}, got {config[field]!r}"
        logger.critical("[IMP:9][test_get_s3_config_defaults] ASSERT: %s default=%s", field, config[field])
    finally:
        for k in env_vars:
            if original_env[k]:
                os.environ[k] = original_env[k]
            else:
                os.environ.pop(k, None)
        if saved_popped:
            os.environ[pop_var] = saved_popped


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-21 · Scenario: BackupConfigError on missing S3 credentials
# · Last fail: None (first run) · Remove if: validation logic changes
# · 170 W2-A2 (B3): RuntimeError → BackupConfigError (доменный класс backup_config)
def test_get_s3_config_missing_credentials():
    """get_s3_config() raises BackupConfigError when S3_ACCESS_KEY, S3_SECRET_KEY, or S3_BUCKET missing."""
    from backup_config import BackupConfigError, get_s3_config

    # Remove all S3-related env vars
    saved = {}
    for var in ("S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_BUCKET"):
        saved[var] = os.environ.pop(var, "")

    try:
        with pytest.raises(BackupConfigError) as excinfo:
            get_s3_config()
        error_msg = str(excinfo.value)
        logger.info("[IMP:7][test_missing] Error: %s", error_msg)
        assert "S3_ACCESS_KEY" in error_msg
        assert "S3_SECRET_KEY" in error_msg
        assert "S3_BUCKET" in error_msg
        logger.critical("[IMP:9][test_missing] ASSERT: BackupConfigError with all 3 missing vars")
    finally:
        for var, val in saved.items():
            if val:
                os.environ[var] = val


# endregion TEST_S3_CONFIG_BASE


# region TEST_UPLOAD_CONFIG_SOURCE


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-21 · Scenario: --config-source ssl-cache → get_s3_config() called, get_backup_config() not
# · Last fail: argparse error (fixed via sys.argv mock) · Remove if: upload.py config-source logic replaced
# · 018 W1 (F-22): прежний snapshot `get(k, "")` + finally `if v:` утекал unset S3-ключи
# ·   (тот же класс, что NODE_NAME-утечка из test_get_backup_config_still_works) —
# ·   конвертирован на monkeypatch.setenv (канон 139 W2).
def test_upload_config_source_ssl_cache_uses_s3_config(monkeypatch: pytest.MonkeyPatch):
    """upload.py --config-source ssl-cache uses get_s3_config() instead of get_backup_config().
    The s3_key is used as-is (no prefix prepended). Tests by calling main with explicit argv.
    """
    from unittest.mock import patch as mock_patch

    from upload import main as upload_main

    # Set up env vars for get_s3_config (monkeypatch — авто-undo, 0 утечек)
    env_vars = {
        "S3_ACCESS_KEY": "ssl-cache-key",
        "S3_SECRET_KEY": "ssl-cache-secret",
        "S3_BUCKET": "ssl-cache-bucket",
    }
    for k, v in env_vars.items():
        monkeypatch.setenv(k, v)

    s3_config = _make_s3_config_dict(
        aws_access_key_id="ssl-cache-key",
        aws_secret_access_key="ssl-cache-secret",
        bucket="ssl-cache-bucket",
    )

    # Create a fake file for upload
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".pem", delete=False, mode="w", encoding="utf-8") as f:
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
        # DevPlan 118 E9: upload.py remove_spool_file() уже удалил файл после успеха —
        # cleanup терпим к отсутствию (spool rm merged в Python)
        with contextlib.suppress(FileNotFoundError):
            pathlib.Path(local_file).unlink()


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


# region TEST_INTEGRATION_ISSUE_CERT


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-21 · Scenario: issue_cert.py wires s3_ssl_cache.py upload after success
# · Last fail: None (first run) · Remove if: issue_cert post-issue logic changes
def test_issue_cert_saves_to_s3_after_success():
    """issue_cert.py must call s3_ssl_cache.py upload after successful cert issuance.

    Verifies by grepping issue_cert.py for the s3_ssl_cache.py upload call
    (--reloadcmd wiring after cert install; W3.5-1: issue-cert.sh → issue_cert.py).
    """
    script_path = "core/internal/bootstrap/issue_cert.py"
    assert pathlib.Path(script_path).is_file(), f"issue_cert.py not found at {script_path}"

    with pathlib.Path(script_path).open(encoding="utf-8") as f:
        content = f.read()

    # Must reference s3_ssl_cache.py (Python module — DevPlan 052 replaces s3-ssl-cache.sh)
    assert "s3_ssl_cache.py" in content, "issue_cert.py must reference s3_ssl_cache.py"

    # Must call upload after successful issue
    assert "upload" in content, "issue_cert.py must call s3_ssl_cache.py upload"

    # Must be non-fatal (WARN on failure)
    assert "WARN" in content, "S3 save must be non-fatal (WARN on failure)"

    # Post-issue S3 upload is wired through acme.sh --reloadcmd (runs right after the
    # cert is installed). Invokes `python3 s3_ssl_cache.py upload <domain>`.
    # REF-0008: reloadcmd собирается из переменных (shlex.quote канон) — окно ±2 строки.
    lines = content.split("\n")
    reloadcmd_uploads = [
        i
        for i, line in enumerate(lines)
        if "upload" in line and any("s3_ssl_cache.py" in lines[j] for j in range(max(0, i - 4), min(len(lines), i + 5)))
    ]
    assert reloadcmd_uploads, "issue_cert.py must wire s3_ssl_cache.py upload into reloadcmd"
    for idx in reloadcmd_uploads:
        logger.info("[IMP:8][test_issue_cert_s3] reloadcmd upload at line %d: %s", idx + 1, lines[idx].strip())

    logger.critical(
        "[IMP:9][test_issue_cert_s3] ASSERT: issue_cert.py wires s3_ssl_cache.py upload "
        "via acme.sh reloadcmd after successful cert issuance"
    )


# endregion TEST_INTEGRATION_ISSUE_CERT


# region TEST_BACKWARD_COMPAT


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-21 · Scenario: get_backup_config() backward compatible after refactoring
# · Last fail: 2026-08-27 (F-22, 018 W1) — finally `if v:` НЕ удалял ключи, не установленные
# ·   до теста → NODE_NAME="production-node" утекал в env xdist-воркера → reload app в
# ·   _setup_app_env (test_status_page) подхватывал утечку → node-label "production-node"
# ·   в platform_tls_* сериях → ложный FAIL TestStatusPageMetrics только в составе make check.
# ·   Fix (018 W1): monkeypatch.setenv — авто-undo удаляет ключи, отсутствовавшие до теста
# ·   (канон DevPlan 139 W2: env-мутации ТОЛЬКО через monkeypatch).
# · Remove if: get_backup_config() implementation changes
def test_get_backup_config_still_works(monkeypatch: pytest.MonkeyPatch):
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
    # 018 W1 (F-22): monkeypatch.setenv — авто-undo при teardown (включая удаление ключей,
    # которых не было до теста). Прежний snapshot `get(k, "")` + `if v:` LEAKAL unset-ключи.
    for k, v in env_vars.items():
        monkeypatch.setenv(k, v)

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
    with pathlib.Path(source_path).open(encoding="utf-8") as f:
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
