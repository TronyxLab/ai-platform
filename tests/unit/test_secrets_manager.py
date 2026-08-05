"""
# GREP_SUMMARY: test_secrets_manager, secrets-manager, autogen-secrets, manifest, ensure-secrets, source-secrets-env, fallback-hardcoded, skip-existing, htpasswd-idempotent, salt-extraction
# STRUCTURE: ▶ tmp_path + monkeypatch + mock subprocess → ◇ source_secrets_env: basic/export-prefix (2x) → ◇ ensure_secrets: manifest/fallback/skip-existing (3x) → ◇ _ensure_htpasswd: idempotent salt-extraction (1x) → ⎋ LDD trajectory IMP:7-10 assertions
# region MODULE_CONTRACT
## @purpose  Unit tests for secrets_manager.py — source_secrets_env() parsing, ensure_secrets()
##           generation logic, and _ensure_htpasswd() salt-extraction idempotency (DevPlan 102)
## @scope    Tests source_secrets_env, ensure_secrets, and _ensure_htpasswd functions with
##           tmp_path fixtures, monkeypatch for env vars, and mock subprocess.run for system commands.
## @invariants
##   - All subprocess-dependent tests mock subprocess.run to avoid real system calls
##   - File operations use tmp_path exclusively — never /run/platform
##   - Each test validates IMP:9 business logic log presence via @ldd_trajectory
##   - os.environ modifications made by ensure_secrets are cleaned up after each test
## @changes
##   2026-07-25 · Created
##   2026-07-31 · DevPlan 102 TASK-7 — +test_ensure_htpasswd_idempotent (salt extraction)
# endregion MODULE_CONTRACT
"""

import hashlib
import logging
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Load the LDD trajectory decorator
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "lifecycle"
sys.path.insert(0, str(_MODULE_DIR))
import secrets_manager as sm

# Re-export for fixture cleanups
MODULE = sm

# Публичный htpasswd-контракт (DevPlan 139 W2): sm._ensure_htpasswd — приватный ленивый фасад
# к htpasswd.ensure_htpasswd; тесты идут через ПУБЛИЧНЫЙ путь (top-10 private-доступов закрыты).
from core.internal.bootstrap.lifecycle.htpasswd import ensure_htpasswd

# ═══════════════════════════════════════════════════════════════════
# region Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def secrets_env(tmp_path):
    """Provide a temporary secrets.env path for each test."""
    return str(tmp_path / "secrets.env")


@pytest.fixture
def mock_subprocess_run():
    """Mock subprocess.run to return successful results by default.

    Patches subprocess.run globally so both _generate_secret and
    _ensure_htpasswd use the mock. Returns stdout="generated_value_abc123"
    which _generate_secret treats as a successfully generated secret.
    """
    with patch("subprocess.run") as mock:
        mock.return_value.returncode = 0
        mock.return_value.stdout = "generated_value_abc123\n"
        mock.return_value.stderr = ""
        yield mock


# endregion Fixtures


# ═══════════════════════════════════════════════════════════════════
# region Tests: source_secrets_env
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · source_secrets_env parses key=value file
# · Scenario: Write secrets.env with key=value pairs, comments, blank lines, quoted values
#   → source_secrets_env returns dict with only the valid key=value entries
# · Last fail: N/A (new test)
# · Remove if: source_secrets_env parsing logic changes
@ldd_trajectory
def test_source_secrets_env(caplog, tmp_path):
    """source_secrets_env should parse key=value file into dict.

    ## @purpose  Verify that a standard secrets.env file with comments,
    ##           blank lines, and quoted values is parsed correctly.
    ##           Comments (#) and blank lines are skipped. Surrounding
    ##           quotes are stripped from values.
    """
    env_file = tmp_path / "secrets.env"
    env_file.write_text(
        "LITELLM_MASTER_KEY=sk-generated\n"
        "LANGFUSE_INIT_ORG_ID=org_test\n"
        "# This is a comment — should be skipped\n"
        "\n"
        "NEXTAUTH_SECRET=supersecret\n"
        "SALT='quoted-value'\n"
        'DOUBLE_QUOTED="double-quoted-value"\n'
        "MALFORMED_LINE_NO_EQUALS\n"
        "   EMPTY_VALUE=\n"
    )

    result = sm.source_secrets_env(str(env_file))

    assert result["LITELLM_MASTER_KEY"] == "sk-generated"
    assert result["LANGFUSE_INIT_ORG_ID"] == "org_test"
    assert result["NEXTAUTH_SECRET"] == "supersecret"
    assert result["SALT"] == "quoted-value"
    assert result["DOUBLE_QUOTED"] == "double-quoted-value"
    assert "MALFORMED_LINE_NO_EQUALS" not in result
    assert result.get("EMPTY_VALUE", "") == ""
    assert len(result) == 6

    logger.critical("[IMP:9][test] source_secrets_env parsed key=value file with 6 entries — OK")


# 🧪 TRAP[TEST] · Regression · source_secrets_env handles export prefix
# · Scenario: Lines starting with 'export ' are parsed stripping the prefix
# · Last fail: N/A (new test)
# · Remove if: export prefix handling changes
@ldd_trajectory
def test_source_secrets_export_prefix(caplog, tmp_path):
    """source_secrets_env should strip 'export ' prefix from lines.

    ## @purpose  Verify that lines with the `export VAR=VALUE` shell format
    ##           are correctly parsed, stripping the `export ` prefix while
    ##           preserving plain VAR=VALUE lines.
    """
    env_file = tmp_path / "secrets.env"
    env_file.write_text(
        "export LITELLM_MASTER_KEY=sk-exported\n"
        "export NEXTAUTH_SECRET=hexsecret\n"
        "PLAIN_VAR=plain\n"
        "  export   SPACED_EXPORT=spaced\n"
    )

    result = sm.source_secrets_env(str(env_file))

    assert result["LITELLM_MASTER_KEY"] == "sk-exported"
    assert result["NEXTAUTH_SECRET"] == "hexsecret"
    assert result["PLAIN_VAR"] == "plain"
    assert result["SPACED_EXPORT"] == "spaced"
    assert len(result) == 4

    logger.critical("[IMP:9][test] source_secrets_env parsed export-prefixed lines — OK")


# endregion Tests: source_secrets_env


# ═══════════════════════════════════════════════════════════════════
# region Tests: ensure_secrets
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · ensure_secrets reads manifest, generates missing secrets
# · Scenario: _read_manifest returns valid tier=generated secrets list →
#   ensure_secrets generates values via mock subprocess, sets os.environ,
#   appends to secrets.env, and returns list of generated names
# · Last fail: N/A (new test)
# · Remove if: ensure_secrets manifest processing logic changes
@ldd_trajectory
def test_ensure_secrets_from_manifest(caplog, secrets_env, mock_subprocess_run, monkeypatch):
    """ensure_secrets should read manifest and generate missing secrets via gen_command.

    ## @purpose  Verify end-to-end flow: manifest provides tier=generated secrets
    ##           list → ensure_secrets detects missing env vars → calls
    ##           _generate_secret for each → sets os.environ → persists to file
    ##           → returns generated names. _ensure_htpasswd is mocked out to
    ##           avoid openssl subprocess calls.
    """
    manifest_secrets = [
        {"name": "LITELLM_MASTER_KEY", "gen_command": "echo sk-manifest", "tier": "generated"},
        {"name": "NEXTAUTH_SECRET", "gen_command": "echo hex-manifest", "tier": "generated"},
    ]

    # Ensure these env vars are NOT set before the test
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    monkeypatch.delenv("NEXTAUTH_SECRET", raising=False)

    # Ensure secrets.env file does not pre-exist (fresh tmp_path)
    secrets_env_path = Path(secrets_env)
    if secrets_env_path.exists():
        secrets_env_path.unlink()

    with (
        patch.object(sm, "_read_manifest", return_value=manifest_secrets),
        patch.object(sm, "_ensure_htpasswd", return_value=False),
    ):
        generated = sm.ensure_secrets(
            manifest_path="/fake/manifest.yaml",
            secrets_env=secrets_env,
            persist_to_sops=False,
        )

    # Verify both secrets were generated
    assert "LITELLM_MASTER_KEY" in generated
    assert "NEXTAUTH_SECRET" in generated
    assert len(generated) == 2

    # Verify they were set in os.environ
    assert os.environ.get("LITELLM_MASTER_KEY") == "generated_value_abc123"
    assert os.environ.get("NEXTAUTH_SECRET") == "generated_value_abc123"

    # Verify secrets.env was created with generated values
    assert secrets_env_path.exists()
    env_content = secrets_env_path.read_text()
    assert "LITELLM_MASTER_KEY=generated_value_abc123" in env_content
    assert "NEXTAUTH_SECRET=generated_value_abc123" in env_content

    # Clean up leaked env vars
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    monkeypatch.delenv("NEXTAUTH_SECRET", raising=False)

    logger.critical("[IMP:9][test] ensure_secrets generated %d secrets from manifest — OK", len(generated))


# 🧪 TRAP[TEST] · Regression · DevPlan 116 T4 (U-33) · ensure_secrets with missing manifest RAISES
# · Scenario: manifest_path="" → _read_manifest raises FileNotFoundError (hardcoded fallback removed)
# · Last fail: 2026-07-31 (fallback list existed)
# · Remove if: strict manifest reader is superseded
@ldd_trajectory
def test_ensure_secrets_missing_manifest_raises(caplog, secrets_env, monkeypatch):
    """ensure_secrets must raise FileNotFoundError when manifest is unavailable (fail-visible).

    ## @purpose  Verify hardcoded fallback list is GONE: missing manifest now propagates
    ##           a FileNotFoundError instead of silently generating from _FALLBACK_SECRETS.
    ##           Manifest is always delivered with core/ — silent fallback was a drift vector.
    """
    with patch.object(sm, "_ensure_htpasswd", return_value=False), pytest.raises(FileNotFoundError):
        sm.ensure_secrets(
            manifest_path="",
            secrets_env=secrets_env,
            persist_to_sops=False,
        )

    logger.critical("[IMP:9][test] ensure_secrets missing manifest raises FileNotFoundError — OK")


# 🧪 TRAP[TEST] · Regression · ensure_secrets does NOT overwrite existing secrets
# · Scenario: LITELLM_MASTER_KEY already set in os.environ → skipped (not generated)
# · Last fail: N/A (new test)
# · Remove if: skip-existing logic changes
@ldd_trajectory
def test_ensure_secrets_skips_existing(caplog, secrets_env, mock_subprocess_run, monkeypatch):
    """ensure_secrets should NOT overwrite existing secrets in os.environ.

    ## @purpose  Verify invariant: existing secrets (already present in
    ##           os.environ) are NOT overwritten or regenerated. Only
    ##           genuinely missing secrets are generated. The existing
    ##           value must be preserved exactly.
    """
    # Set an env var before calling ensure_secrets
    monkeypatch.setenv("LITELLM_MASTER_KEY", "existing-pre-set-value")

    # Ensure NEXTAUTH_SECRET is NOT set (should be generated)
    monkeypatch.delenv("NEXTAUTH_SECRET", raising=False)

    manifest_secrets = [
        {"name": "LITELLM_MASTER_KEY", "gen_command": "echo sk-new", "tier": "generated"},
        {"name": "NEXTAUTH_SECRET", "gen_command": "echo hex-new", "tier": "generated"},
    ]

    secrets_env_path = Path(secrets_env)
    if secrets_env_path.exists():
        secrets_env_path.unlink()

    with (
        patch.object(sm, "_read_manifest", return_value=manifest_secrets),
        patch.object(sm, "_ensure_htpasswd", return_value=False),
    ):
        generated = sm.ensure_secrets(
            manifest_path="/fake/manifest.yaml",
            secrets_env=secrets_env,
            persist_to_sops=False,
        )

    # LITELLM_MASTER_KEY should NOT be in generated list (already existed)
    assert "LITELLM_MASTER_KEY" not in generated, "Existing secret was regenerated"

    # NEXTAUTH_SECRET should be generated (was missing)
    assert "NEXTAUTH_SECRET" in generated

    # Existing value should NOT be overwritten
    assert os.environ.get("LITELLM_MASTER_KEY") == "existing-pre-set-value", "Existing secret was overwritten"

    # Generated value should be set
    assert os.environ.get("NEXTAUTH_SECRET") == "generated_value_abc123"

    # Verify secrets.env only contains the generated secret, not the existing one
    assert secrets_env_path.exists()
    env_content = secrets_env_path.read_text()
    assert "NEXTAUTH_SECRET=generated_value_abc123" in env_content
    # LITELLM_MASTER_KEY should NOT be in secrets.env (it was already set, not generated)
    assert "LITELLM_MASTER_KEY" not in env_content

    # Clean up leaked env vars
    monkeypatch.delenv("NEXTAUTH_SECRET", raising=False)

    logger.critical("[IMP:9][test] ensure_secrets skipped existing secret — OK")


# endregion Tests: ensure_secrets


# ═══════════════════════════════════════════════════════════════════
# region Tests: ensure_secrets — atomic overwrite (DevPlan 072)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · ensure_secrets is idempotent on repeated calls
# · Scenario: Call ensure_secrets 3 times with same manifest → file unchanged after first call,
# ·   no duplicate lines, all non-generated secrets preserved
# · Last fail: N/A (new test — validates DevPlan 072 atomic write fix)
# · Remove if: ensure_secrets overwrite logic changes fundamentally
@ldd_trajectory
def test_ensure_secrets_idempotent(caplog, secrets_env, mock_subprocess_run, monkeypatch):
    """ensure_secrets should be idempotent — repeated calls produce identical secrets.env.

    ## @purpose  Verify that calling ensure_secrets multiple times does NOT
    ##           append duplicate lines. After the first call, subsequent calls
    ##           with the same manifest should leave secrets.env unchanged.
    ##           This validates the atomic overwrite fix (DevPlan 072).
    """
    manifest_secrets = [
        {"name": "LITELLM_MASTER_KEY", "gen_command": "echo sk-test", "tier": "generated"},
        {"name": "NEXTAUTH_SECRET", "gen_command": "echo hex-test", "tier": "generated"},
    ]

    # Ensure env vars are NOT set before test
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    monkeypatch.delenv("NEXTAUTH_SECRET", raising=False)

    secrets_env_path = Path(secrets_env)
    if secrets_env_path.exists():
        secrets_env_path.unlink()

    with (
        patch.object(sm, "_read_manifest", return_value=manifest_secrets),
        patch.object(sm, "_ensure_htpasswd", return_value=False),
    ):
        # First call — should generate and write
        generated1 = sm.ensure_secrets(
            manifest_path="/fake/manifest.yaml",
            secrets_env=secrets_env,
            persist_to_sops=False,
        )

    assert len(generated1) == 2
    first_content = secrets_env_path.read_text()
    first_lines = [line for line in first_content.split("\n") if line.strip() and not line.startswith("#")]
    assert len(first_lines) == 2, f"Expected 2 lines, got {len(first_lines)}: {first_lines}"

    # Verify no duplicate keys
    keys_in_file = [line.split("=", 1)[0] for line in first_lines]
    assert len(keys_in_file) == len(set(keys_in_file)), f"Duplicate keys found: {keys_in_file}"

    # Clean env for second call
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    monkeypatch.delenv("NEXTAUTH_SECRET", raising=False)

    with (
        patch.object(sm, "_read_manifest", return_value=manifest_secrets),
        patch.object(sm, "_ensure_htpasswd", return_value=False),
    ):
        # Second call — should skip (env vars loaded from file in Step 1)
        generated2 = sm.ensure_secrets(
            manifest_path="/fake/manifest.yaml",
            secrets_env=secrets_env,
            persist_to_sops=False,
        )

    # Second call should generate nothing
    assert len(generated2) == 0, f"Expected 0 generated on second call, got {len(generated2)}"
    second_content = secrets_env_path.read_text()
    assert second_content == first_content, (
        f"File changed on second call!\nFirst:\n{first_content}\nSecond:\n{second_content}"
    )

    # Third call — force-mode simulation (clear os.environ, file still exists)
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    monkeypatch.delenv("NEXTAUTH_SECRET", raising=False)

    with (
        patch.object(sm, "_read_manifest", return_value=manifest_secrets),
        patch.object(sm, "_ensure_htpasswd", return_value=False),
    ):
        generated3 = sm.ensure_secrets(
            manifest_path="/fake/manifest.yaml",
            secrets_env=secrets_env,
            persist_to_sops=False,
        )

    # Third call: env vars empty, but source_secrets_env reloads from file
    # (Step 1: lines 262-265) → those values go into os.environ → skip generation
    assert len(generated3) == 0, (
        f"Expected 0 generated (values reloaded from file), got {len(generated3)}. "
        f"File content: {secrets_env_path.read_text()}"
    )
    third_content = secrets_env_path.read_text()
    assert third_content == first_content, "File changed on third call!"

    # Clean up
    for g in generated1:
        monkeypatch.delenv(g, raising=False)

    logger.critical("[IMP:9][test] ensure_secrets idempotent after 3 calls — OK")


# 🧪 TRAP[TEST] · Regression · ensure_secrets preserves non-generated secrets on overwrite
# · Scenario: secrets.env has SOPS-decrypted secrets (WEBNAMES_API_KEY) →
#   ensure_secrets generates only tier=generated → non-generated entries unchanged in output
# · Last fail: N/A (new test — validates DevPlan 072 merge logic)
# · Remove if: merge logic changes
@ldd_trajectory
def test_ensure_secrets_preserves_nongenerated(caplog, secrets_env, mock_subprocess_run, monkeypatch):
    """ensure_secrets should preserve non-generated secrets (from SOPS) on overwrite.

    ## @purpose  Verify that when secrets.env contains non-generated secrets
    ##           (e.g., WEBNAMES_API_KEY from SOPS decryption), the atomic
    ##           overwrite preserves them while still generating missing ones.
    ##           This is the key invariant: overwrite mode must NOT delete
    ##           secrets that ensure_secrets doesn't manage.
    """
    # Pre-populate secrets.env with non-generated secrets (simulating SOPS decrypt)
    secrets_env_path = Path(secrets_env)
    secrets_env_path.write_text(
        "WEBNAMES_API_KEY=real-api-key-from-sops\nPOSTGRES_PASSWORD=real-pg-pwd\n# This is a comment\n\n"
    )

    manifest_secrets = [
        {"name": "LITELLM_MASTER_KEY", "gen_command": "echo sk-test", "tier": "generated"},
    ]

    # Ensure generated secret is NOT in os.environ
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)

    with (
        patch.object(sm, "_read_manifest", return_value=manifest_secrets),
        patch.object(sm, "_ensure_htpasswd", return_value=False),
    ):
        generated = sm.ensure_secrets(
            manifest_path="/fake/manifest.yaml",
            secrets_env=secrets_env,
            persist_to_sops=False,
        )

    assert len(generated) == 1
    assert "LITELLM_MASTER_KEY" in generated

    # Verify file content
    content = secrets_env_path.read_text()
    assert "WEBNAMES_API_KEY=real-api-key-from-sops" in content, (
        f"Non-generated secret was DELETED!\nContent:\n{content}"
    )
    assert "POSTGRES_PASSWORD=real-pg-pwd" in content, "POSTGRES_PASSWORD was DELETED!"
    assert "LITELLM_MASTER_KEY=generated_value_abc123" in content, "Generated secret missing!"

    # Verify no duplicate lines
    file_lines = [line for line in content.split("\n") if line.strip() and not line.startswith("#")]
    keys = [line.split("=", 1)[0] for line in file_lines]
    assert len(keys) == len(set(keys)), f"Duplicate keys: {keys}"

    # Clean up
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)

    logger.critical("[IMP:9][test] Non-generated secrets preserved on atomic overwrite — OK")


# endregion Tests: ensure_secrets — atomic overwrite (DevPlan 072)


# ═══════════════════════════════════════════════════════════════════
# region Tests: _ensure_htpasswd — salt-extraction idempotency (DevPlan 102)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · htpasswd salt-extraction idempotency (TRAP[BUG] 2026-07-31)
# · Scenario: two calls to ensure_htpasswd (public контракт) with identical credentials →
# ·   identical md5sum (2nd call extracts $apr1$ salt from existing file, recomputes entry with fixed salt)
# · Last fail: N/A (new test — validates DevPlan 102 TASK-1 fix)
# · Remove if: salt-extraction logic in write_htpasswd_file changes fundamentally
@ldd_trajectory
def test_ensure_htpasswd_idempotent(caplog, tmp_path, monkeypatch):
    """Two calls to ensure_htpasswd with same creds → identical md5sum (salt extraction).

    ## @purpose  Verify DevPlan 102 TASK-1 fix: random salt broke idempotency —
    ##           each call regenerated a different $apr1$ hash and rewrote the file.
    ##           Now the existing file's salt is extracted and reused, so the second
    ##           call produces the identical entry (md5-stable file).
    ##           DevPlan 139 W2: через публичный htpasswd.ensure_htpasswd (не sm._ensure_htpasswd).
    """
    secrets_env_file = tmp_path / "secrets.env"
    secrets_env_file.write_text("PLATFORM_MASTER_EMAIL=admin@test.local\nPLATFORM_MASTER_PASSWORD=test-password-123\n")
    htpasswd_file = tmp_path / ".htpasswd-platform"

    # Ensure env not pre-seeded — ensure_htpasswd sources from secrets.env
    monkeypatch.delenv("PLATFORM_MASTER_EMAIL", raising=False)
    monkeypatch.delenv("PLATFORM_MASTER_PASSWORD", raising=False)

    assert ensure_htpasswd(str(secrets_env_file), str(htpasswd_file)) is True
    first_md5 = hashlib.md5(htpasswd_file.read_bytes()).hexdigest()

    # Clear env so the second call re-sources credentials from secrets.env
    monkeypatch.delenv("PLATFORM_MASTER_EMAIL", raising=False)
    monkeypatch.delenv("PLATFORM_MASTER_PASSWORD", raising=False)

    assert ensure_htpasswd(str(secrets_env_file), str(htpasswd_file)) is True
    second_md5 = hashlib.md5(htpasswd_file.read_bytes()).hexdigest()

    assert first_md5 == second_md5, (
        f"htpasswd file changed on second call (md5 {first_md5} → {second_md5}) — salt extraction fix not effective"
    )

    # Sanity: file holds a valid APR1 entry for the given credentials
    content = htpasswd_file.read_text().strip()
    assert "admin@test.local" in content, f"Email not found in htpasswd entry: {content}"
    assert "$apr1$" in content, f"APR1 hash not found in htpasswd entry: {content}"

    logger.critical("[IMP:9][test] htpasswd idempotent across 2 calls (salt extraction) — OK")


# endregion Tests: _ensure_htpasswd — salt-extraction idempotency (DevPlan 102)
