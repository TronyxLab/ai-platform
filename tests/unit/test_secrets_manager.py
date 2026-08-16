"""
# GREP_SUMMARY: test_secrets_manager, secrets-manager, autogen-secrets, manifest, ensure-secrets, source-secrets-env, fallback-hardcoded, skip-existing, htpasswd-idempotent, salt-extraction, master-credentials-autogen
# STRUCTURE: ▶ tmp_path + monkeypatch + mock subprocess → ◇ source_secrets_env: basic/export-prefix (2x) → ◇ ensure_secrets: manifest/fallback/skip-existing (3x) → ◇ master-credentials autogen: domain/idempotent/node-yaml/not-overwritten (4x) → ◇ _ensure_htpasswd: idempotent salt-extraction (1x) → ⎋ LDD trajectory IMP:7-10 assertions
# region MODULE_CONTRACT
## @purpose  Unit tests for secrets_manager.py — source_secrets_env() parsing, ensure_secrets()
##           generation logic, master-credentials autogen (DevPlan 156 W1), and _ensure_htpasswd()
##           salt-extraction idempotency (DevPlan 102)
## @scope    Tests source_secrets_env, ensure_secrets, and _ensure_htpasswd functions with
##           tmp_path fixtures, monkeypatch for env vars, and mock subprocess.run for system commands.
## @invariants
##   - All subprocess-dependent tests mock subprocess.run to avoid real system calls
##   - File operations use tmp_path exclusively — never /run/platform
##   - Each test validates IMP:9 business logic log presence via @ldd_trajectory
##   - os.environ modifications made by ensure_secrets are cleaned up after each test
##   - Existing manifest-tests patch sm._ensure_master_credentials (DevPlan 156) — они
##     тестируют tier=generated flow, не autogen мастер-кредов
## @changes
##   2026-07-25 · Created
##   2026-07-31 · DevPlan 102 TASK-7 — +test_ensure_htpasswd_idempotent (salt extraction)
##   2026-08-12 · DevPlan 156 W1 — +4 теста master-credentials autogen; существующие
##              ensure_secrets-тесты патчат sm._ensure_master_credentials (изоляция flow)
##   2026-08-16 · DevPlan 176 B.3/B.8 — random-autogen: мастер-пароль secrets.token_urlsafe(32)
##              (H3, инверсия решения 2026-08-12); +_ensure_derived_passwords (M7/B.8 — HERMES/GF/
##              LANGFUSE собственные случайные значения); ensure_secrets-тесты патчат
##              sm._ensure_derived_passwords (изоляция flow, точный line-count идемпотентности)
# endregion MODULE_CONTRACT
"""

import hashlib
import logging
import os
import re
import sys
import time
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

pytestmark = pytest.mark.static_audit

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
        "   EMPTY_VALUE=\n",
        encoding="utf-8",
    )

    result = sm.source_secrets_env(str(env_file))

    assert result["LITELLM_MASTER_KEY"] == "sk-generated"
    assert result["LANGFUSE_INIT_ORG_ID"] == "org_test"
    assert result["NEXTAUTH_SECRET"] == "supersecret"
    assert result["SALT"] == "quoted-value"
    assert result["DOUBLE_QUOTED"] == "double-quoted-value"
    assert "MALFORMED_LINE_NO_EQUALS" not in result
    assert not result.get("EMPTY_VALUE", "")
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
        "  export   SPACED_EXPORT=spaced\n",
        encoding="utf-8",
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
        patch.object(sm, "_ensure_master_credentials", return_value=None),
        patch.object(sm, "_ensure_derived_passwords", return_value=None),
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
    env_content = secrets_env_path.read_text(encoding="utf-8")
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
        patch.object(sm, "_ensure_master_credentials", return_value=None),
        patch.object(sm, "_ensure_derived_passwords", return_value=None),
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
    env_content = secrets_env_path.read_text(encoding="utf-8")
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
        patch.object(sm, "_ensure_master_credentials", return_value=None),
        patch.object(sm, "_ensure_derived_passwords", return_value=None),
    ):
        # First call — should generate and write
        generated1 = sm.ensure_secrets(
            manifest_path="/fake/manifest.yaml",
            secrets_env=secrets_env,
            persist_to_sops=False,
        )

    assert len(generated1) == 2
    first_content = secrets_env_path.read_text(encoding="utf-8")
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
        patch.object(sm, "_ensure_master_credentials", return_value=None),
        patch.object(sm, "_ensure_derived_passwords", return_value=None),
    ):
        # Second call — should skip (env vars loaded from file in Step 1)
        generated2 = sm.ensure_secrets(
            manifest_path="/fake/manifest.yaml",
            secrets_env=secrets_env,
            persist_to_sops=False,
        )

    # Second call should generate nothing
    assert len(generated2) == 0, f"Expected 0 generated on second call, got {len(generated2)}"
    second_content = secrets_env_path.read_text(encoding="utf-8")
    assert second_content == first_content, (
        f"File changed on second call!\nFirst:\n{first_content}\nSecond:\n{second_content}"
    )

    # Third call — force-mode simulation (clear os.environ, file still exists)
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    monkeypatch.delenv("NEXTAUTH_SECRET", raising=False)

    with (
        patch.object(sm, "_read_manifest", return_value=manifest_secrets),
        patch.object(sm, "_ensure_htpasswd", return_value=False),
        patch.object(sm, "_ensure_master_credentials", return_value=None),
        patch.object(sm, "_ensure_derived_passwords", return_value=None),
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
        f"File content: {secrets_env_path.read_text(encoding='utf-8')}"
    )
    third_content = secrets_env_path.read_text(encoding="utf-8")
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
        "WEBNAMES_API_KEY=real-api-key-from-sops\nPOSTGRES_PASSWORD=real-pg-pwd\n# This is a comment\n\n",
        encoding="utf-8",
    )

    manifest_secrets = [
        {"name": "LITELLM_MASTER_KEY", "gen_command": "echo sk-test", "tier": "generated"},
    ]

    # Ensure generated secret is NOT in os.environ
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)

    with (
        patch.object(sm, "_read_manifest", return_value=manifest_secrets),
        patch.object(sm, "_ensure_htpasswd", return_value=False),
        patch.object(sm, "_ensure_master_credentials", return_value=None),
        patch.object(sm, "_ensure_derived_passwords", return_value=None),
    ):
        generated = sm.ensure_secrets(
            manifest_path="/fake/manifest.yaml",
            secrets_env=secrets_env,
            persist_to_sops=False,
        )

    assert len(generated) == 1
    assert "LITELLM_MASTER_KEY" in generated

    # Verify file content
    content = secrets_env_path.read_text(encoding="utf-8")
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
    secrets_env_file.write_text(
        "PLATFORM_MASTER_EMAIL=admin@test.local\nPLATFORM_MASTER_PASSWORD=test-password-123\n", encoding="utf-8"
    )
    htpasswd_file = tmp_path / ".htpasswd-platform"

    # Ensure env not pre-seeded — ensure_htpasswd sources from secrets.env
    monkeypatch.delenv("PLATFORM_MASTER_EMAIL", raising=False)
    monkeypatch.delenv("PLATFORM_MASTER_PASSWORD", raising=False)

    assert ensure_htpasswd(str(secrets_env_file), str(htpasswd_file)) is True
    first_md5 = hashlib.md5(htpasswd_file.read_bytes(), usedforsecurity=False).hexdigest()  # S324: тестовая чексумма

    # Clear env so the second call re-sources credentials from secrets.env
    monkeypatch.delenv("PLATFORM_MASTER_EMAIL", raising=False)
    monkeypatch.delenv("PLATFORM_MASTER_PASSWORD", raising=False)

    assert ensure_htpasswd(str(secrets_env_file), str(htpasswd_file)) is True
    second_md5 = hashlib.md5(htpasswd_file.read_bytes(), usedforsecurity=False).hexdigest()  # S324: тестовая чексумма

    assert first_md5 == second_md5, (
        f"htpasswd file changed on second call (md5 {first_md5} → {second_md5}) — salt extraction fix not effective"
    )

    # Sanity: file holds a valid APR1 entry for the given credentials
    content = htpasswd_file.read_text(encoding="utf-8").strip()
    assert "admin@test.local" in content, f"Email not found in htpasswd entry: {content}"
    assert "$apr1$" in content, f"APR1 hash not found in htpasswd entry: {content}"

    logger.critical("[IMP:9][test] htpasswd idempotent across 2 calls (salt extraction) — OK")


# endregion Tests: _ensure_htpasswd — salt-extraction idempotency (DevPlan 102)


# ═══════════════════════════════════════════════════════════════════
# region Tests: ensure_secrets — master credentials autogen (DevPlan 156 W1)
# ═══════════════════════════════════════════════════════════════════


def _write_tmp_manifest(tmp_path, entries=("LITELLM_MASTER_KEY",)) -> Path:
    """Write a minimal real secrets-manifest.yaml (strict reader contract)."""
    manifest = tmp_path / "secrets-manifest.yaml"
    secrets = "\n".join(
        f"  - name: {name}\n    tier: generated\n    gen_command: echo {name.lower()}" for name in entries
    )
    manifest.write_text(f"secrets:\n{secrets}\n", encoding="utf-8")
    return manifest


# 🧪 TRAP[TEST] · Regression · DevPlan 156 W1 + 176 B.3 · autogen master credentials при первом bootstrap
# · Scenario: креды отсутствуют (env + файл), PLATFORM_DOMAIN=asiteam.ru → ensure_secrets
# ·   генерирует admin@asiteam.ru + СЛУЧАЙНЫЙ пароль (secrets.token_urlsafe, H3) в secrets.env и os.environ
# · Last fail: 2026-08-12 (asi-team-vps: htpasswd не создан — кредов нет в SOPS); 2026-08-16 (H3:
# ·   детерминированный test-master-password-<дата> заменён на random-autogen, решение 2026-08-15)
# · Remove if: _ensure_master_credentials logic changes
@ldd_trajectory
def test_ensure_secrets_autogenerates_master_credentials(caplog, tmp_path, mock_subprocess_run, monkeypatch):
    """ensure_secrets autogen master credentials: email=admin@<PLATFORM_DOMAIN>, pwd=СЛУЧАЙНЫЙ (H3).

    ## @purpose  AC1-проверка: на свежей ноде φ4 создаёт PLATFORM_MASTER_EMAIL/PASSWORD
    ##           в secrets.env + os.environ (htpasswd-предпосылка, DevPlan 156 W1).
    ##           DevPlan 176 B.3 (H3): пароль СЛУЧАЙНЫЙ (не дата-префикс) + charset-конформен.
    """
    manifest = _write_tmp_manifest(tmp_path)
    secrets_env = tmp_path / "secrets.env"

    monkeypatch.setenv("PLATFORM_DOMAIN", "asiteam.ru")
    monkeypatch.delenv("PLATFORM_MASTER_EMAIL", raising=False)
    monkeypatch.delenv("PLATFORM_MASTER_PASSWORD", raising=False)
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)

    with (
        patch.object(sm, "_ensure_htpasswd", return_value=False),
        patch.object(sm, "_ensure_derived_passwords", return_value=None),
    ):
        generated = sm.ensure_secrets(
            manifest_path=str(manifest),
            secrets_env=str(secrets_env),
            persist_to_sops=False,
        )

    assert "LITELLM_MASTER_KEY" in generated  # tier=generated flow не сломан

    env_content = secrets_env.read_text(encoding="utf-8")
    assert "PLATFORM_MASTER_EMAIL=admin@asiteam.ru" in env_content, f"Email missing:\n{env_content}"
    pwd_line = next((ln for ln in env_content.splitlines() if ln.startswith("PLATFORM_MASTER_PASSWORD=")), "")
    assert pwd_line, f"Password missing:\n{env_content}"
    pwd = pwd_line.split("=", 1)[1]
    # H3: пароль СЛУЧАЙНЫЙ — не детерминированный дата-префикс (≤31 попытка перебора закрыта)
    assert not pwd.startswith("test-master-password"), f"H3 FAIL: детерминированный пароль: {pwd}"
    assert pwd != f"test-master-password-{time.strftime('%d.%m.%Y')}", "H3 FAIL: дата-префикс вернулся"
    assert len(pwd) >= 32, f"H3: пароль подозрительно короткий: {len(pwd)}"
    assert re.fullmatch(r"^[A-Za-z0-9._-]+$", pwd), f"charset violation: {pwd}"
    assert os.environ.get("PLATFORM_MASTER_EMAIL") == "admin@asiteam.ru"
    assert os.environ.get("PLATFORM_MASTER_PASSWORD") == pwd

    monkeypatch.delenv("PLATFORM_MASTER_EMAIL", raising=False)
    monkeypatch.delenv("PLATFORM_MASTER_PASSWORD", raising=False)
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)

    logger.critical("[IMP:9][test] master credentials auto-generated (PLATFORM_DOMAIN, random H3) — OK")


# 🧪 TRAP[TEST] · Regression · DevPlan 156 W1 + 176 B.3 · master credentials autogen ИДЕМПОТЕНТЕН
# · Scenario: secrets.env уже содержит test-master-password-01.01.2020 (легаси-значение эры решения
# ·   2026-08-12 ИЛИ любой persist) → повторный ensure_secrets НЕ перезаписывает (H3-fix не ротирует
# ·   persist-значения — инвариант «persist не перезаписывает»)
# · Last fail: N/A (new test — дата-дрейф риск из риск-листа плана)
# · Remove if: idempotency branch of _ensure_master_credentials changes
@ldd_trajectory
def test_ensure_secrets_master_credentials_idempotent(caplog, tmp_path, mock_subprocess_run, monkeypatch):
    """Existing master creds in secrets.env → NOT overwritten (пароль не «плывёт»; legacy-значения не ротируются).

    ## @purpose  AC1-проверка идемпотентности: повторный bootstrap = no-op.
    ##           Эмуляция «даты завтра» — в secrets.env вписано старое значение.
    """
    manifest = _write_tmp_manifest(tmp_path)
    secrets_env = tmp_path / "secrets.env"
    secrets_env.write_text(
        "PLATFORM_MASTER_EMAIL=admin@old.local\nPLATFORM_MASTER_PASSWORD=test-master-password-01.01.2020\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("PLATFORM_MASTER_EMAIL", raising=False)
    monkeypatch.delenv("PLATFORM_MASTER_PASSWORD", raising=False)
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    monkeypatch.delenv("PLATFORM_DOMAIN", raising=False)

    with (
        patch.object(sm, "_ensure_htpasswd", return_value=False),
        patch.object(sm, "_ensure_derived_passwords", return_value=None),
    ):
        sm.ensure_secrets(
            manifest_path=str(manifest),
            secrets_env=str(secrets_env),
            persist_to_sops=False,
        )

    env_content = secrets_env.read_text(encoding="utf-8")
    assert "PLATFORM_MASTER_EMAIL=admin@old.local" in env_content, f"Email overwritten:\n{env_content}"
    assert "PLATFORM_MASTER_PASSWORD=test-master-password-01.01.2020" in env_content, (
        f"Password overwritten (дрейф даты):\n{env_content}"
    )
    # Пароль привязан к дате ПЕРВОГО bootstrap — сегодняшняя дата не должна появиться
    assert f"test-master-password-{time.strftime('%d.%m.%Y')}" not in env_content, (
        f"Пароль перегенерирован на сегодняшнюю дату:\n{env_content}"
    )
    assert os.environ.get("PLATFORM_MASTER_PASSWORD") == "test-master-password-01.01.2020"

    monkeypatch.delenv("PLATFORM_MASTER_EMAIL", raising=False)
    monkeypatch.delenv("PLATFORM_MASTER_PASSWORD", raising=False)
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)

    logger.critical("[IMP:9][test] master credentials NOT overwritten on re-run (idempotent) — OK")


# 🧪 TRAP[TEST] · Regression · DevPlan 156 W1 · email использует node.yaml#domain
# · Scenario: PLATFORM_DOMAIN env отсутствует, NODE_YAML=tmp/node.yaml (domain: asiteam.ru)
# ·   → email = admin@asiteam.ru (NODE_YAML → node_resolver 3-path, DevPlan 127 W2)
# · Last fail: N/A (new test — домен ≠ PLATFORM_DOMAIN риск из риск-листа плана)
# · Remove if: _resolve_platform_domain NODE_YAML branch changes
@ldd_trajectory
def test_ensure_secrets_master_email_uses_node_yaml_domain(caplog, tmp_path, mock_subprocess_run, monkeypatch):
    """Without PLATFORM_DOMAIN env — email domain comes from node.yaml#domain via NODE_YAML.

    ## @purpose  Риск-лист плана: «домен платформы ≠ домен ноды» — PLATFORM_DOMAIN env
    ##           приоритетнее node.yaml; здесь проверяется ветка node.yaml (NODE_YAML env).
    """
    manifest = _write_tmp_manifest(tmp_path)
    secrets_env = tmp_path / "secrets.env"
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text("domain: asiteam.ru\n", encoding="utf-8")

    monkeypatch.delenv("PLATFORM_DOMAIN", raising=False)
    monkeypatch.setenv("NODE_YAML", str(node_yaml))
    monkeypatch.delenv("PLATFORM_MASTER_EMAIL", raising=False)
    monkeypatch.delenv("PLATFORM_MASTER_PASSWORD", raising=False)
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)

    with (
        patch.object(sm, "_ensure_htpasswd", return_value=False),
        patch.object(sm, "_ensure_derived_passwords", return_value=None),
    ):
        sm.ensure_secrets(
            manifest_path=str(manifest),
            secrets_env=str(secrets_env),
            persist_to_sops=False,
        )

    assert "PLATFORM_MASTER_EMAIL=admin@asiteam.ru" in secrets_env.read_text(encoding="utf-8")
    assert os.environ.get("PLATFORM_MASTER_EMAIL") == "admin@asiteam.ru"

    monkeypatch.delenv("NODE_YAML", raising=False)
    monkeypatch.delenv("PLATFORM_MASTER_EMAIL", raising=False)
    monkeypatch.delenv("PLATFORM_MASTER_PASSWORD", raising=False)
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)

    logger.critical("[IMP:9][test] master email uses node.yaml#domain — OK")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · DevPlan 156 W1 · явно заданные креды НЕ трогаются
# · Scenario: PLATFORM_MASTER_EMAIL/PASSWORD заданы в env явно → ensure_secrets их
# ·   не перезаписывает и не добавляет в secrets.env (инвариант 2 модуля)
# · Last fail: N/A (new negative test — защита от перезаписи пользовательских кредов)
# · Remove if: skip-existing invariant changes
@ldd_trajectory
def test_ensure_secrets_master_credentials_not_overwritten(caplog, tmp_path, mock_subprocess_run, monkeypatch):
    """Explicitly set master creds (env) are NEVER touched by autogen.

    ## @purpose  R5 negative-тест: явные креды пользователя (env) имеют приоритет над
    ##           autogen-дефолтами — autogen только заполняет отсутствующие значения.
    """
    manifest = _write_tmp_manifest(tmp_path)
    secrets_env = tmp_path / "secrets.env"

    monkeypatch.setenv("PLATFORM_MASTER_EMAIL", "explicit@example.com")
    monkeypatch.setenv("PLATFORM_MASTER_PASSWORD", "explicit-strong-pwd")
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)

    with (
        patch.object(sm, "_ensure_htpasswd", return_value=False),
        patch.object(sm, "_ensure_derived_passwords", return_value=None),
    ):
        generated = sm.ensure_secrets(
            manifest_path=str(manifest),
            secrets_env=str(secrets_env),
            persist_to_sops=False,
        )

    assert os.environ.get("PLATFORM_MASTER_EMAIL") == "explicit@example.com"
    assert os.environ.get("PLATFORM_MASTER_PASSWORD") == "explicit-strong-pwd"
    file_content = secrets_env.read_text(encoding="utf-8") if secrets_env.exists() else ""
    assert "PLATFORM_MASTER_EMAIL=" not in file_content, f"Explicit email leaked into file:\n{file_content}"
    assert "PLATFORM_MASTER_PASSWORD=" not in file_content, f"Explicit password leaked into file:\n{file_content}"
    # master creds НЕ попадают в generated-список (отдельный механизм)
    assert all("PLATFORM_MASTER" not in g for g in generated)

    monkeypatch.delenv("PLATFORM_MASTER_EMAIL", raising=False)
    monkeypatch.delenv("PLATFORM_MASTER_PASSWORD", raising=False)
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)

    logger.critical("[IMP:9][test] explicit master creds NOT overwritten (negative R5) — OK")


# endregion Tests: ensure_secrets — master credentials autogen (DevPlan 156 W1)


# ═══════════════════════════════════════════════════════════════════
# region Tests: random-autogen (DevPlan 176 B.3/B.8 — H3/M7)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · DevPlan 176 B.3 (H3) · _random_autogen_password — случайность + charset
# · Scenario: два вызова _random_autogen_password() → РАЗНЫЕ значения; каждое соответствует
# ·   каноническому charset ^[A-Za-z0-9._-]+$ (secret-definitions.yaml)
# · Last fail: 2026-08-16 (H3: детерминированный test-master-password-<дата> — ≤31 попытка перебора)
# · Remove if: _random_autogen_password mechanism changes
@ldd_trajectory
def test_random_autogen_password_random_and_charset(caplog):
    """Randomness + charset of _random_autogen_password (H3/B.8).

    ## @purpose  Два вызова генератора → разные значения (CSPRNG-случайность, не дата-префикс);
    ##           каждое значение входит в канонический charset ^[A-Za-z0-9._-]+$ и имеет
    ##           достаточную длину (≥32 символа — полная энтропия token_urlsafe(32)).
    """
    pwd1 = sm._random_autogen_password()
    pwd2 = sm._random_autogen_password()

    assert pwd1 != pwd2, "Два вызова вернули одинаковое значение — генератор не случайный"
    for pwd in (pwd1, pwd2):
        assert re.fullmatch(r"^[A-Za-z0-9._-]+$", pwd), f"charset violation: {pwd}"
        assert len(pwd) >= 32, f"Пароль подозрительно короткий: {len(pwd)}"
        assert not pwd.startswith("test-master-password"), f"H3 FAIL: дата-префикс: {pwd}"

    logger.critical("[IMP:9][test] random autogen password: 2 вызова различны + charset OK")


# 🧪 TRAP[TEST] · Regression · DevPlan 176 B.8 (M7) · per-secret autogen: 4 пароля попарно различны
# · Scenario: свежая secrets.env, manifest без tier=generated (патч _read_manifest=[]) →
# ·   ensure_secrets генерирует мастер + HERMES/GF/LANGFUSE пароли; значения попарно РАЗЛИЧНЫ
# ·   (разрыв unified-auth конвенции — единый пароль = blast radius), каждый charset-конформен
# · Last fail: 2026-08-16 (M7: .env:6,29,92,102,145 — единый пароль для master/langfuse/hermes/grafana)
# · Remove if: per-secret derived autogen logic changes
@ldd_trajectory
def test_ensure_secrets_derived_passwords_per_secret_random(caplog, tmp_path, monkeypatch):
    """B.8: значения 4 сервис-паролей попарно различны (per-secret random-autogen).

    ## @purpose  Верификация DevPlan 176 B.8: PLATFORM_MASTER_PASSWORD + 3 производных
    ##           (HERMES_DASHBOARD_PASSWORD, GF_SECURITY_ADMIN_PASSWORD, LANGFUSE_INIT_USER_PASSWORD)
    ##           получают СОБСТВЕННЫЕ случайные значения — попарно различны и charset-конформны.
    """
    manifest = _write_tmp_manifest(tmp_path)
    secrets_env = tmp_path / "secrets.env"
    # В проде файл создаётся Step 3.5 (manifest tier=generated) ДО autogen-персиста — симулируем
    secrets_env.write_text("# empty secrets.env\n", encoding="utf-8")

    monkeypatch.setenv("PLATFORM_DOMAIN", "asiteam.ru")
    for v in (
        "PLATFORM_MASTER_EMAIL",
        "PLATFORM_MASTER_PASSWORD",
        "HERMES_DASHBOARD_PASSWORD",
        "GF_SECURITY_ADMIN_PASSWORD",
        "LANGFUSE_INIT_USER_PASSWORD",
    ):
        monkeypatch.delenv(v, raising=False)

    # Manifest без tier=generated → derived-механизм генерирует ВСЕ 3 производных
    # (LANGFUSE в проде обычно приходит из manifest gen_command — здесь проверяется
    #  локальный derived-путь, что и есть B.8-механика для HERMES/GF).
    with (
        patch.object(sm, "_read_manifest", return_value=[]),
        patch.object(sm, "_ensure_htpasswd", return_value=False),
    ):
        sm.ensure_secrets(
            manifest_path=str(manifest),
            secrets_env=str(secrets_env),
            persist_to_sops=False,
        )

    parsed = sm.source_secrets_env(str(secrets_env))
    pwd_names = (
        "PLATFORM_MASTER_PASSWORD",
        "HERMES_DASHBOARD_PASSWORD",
        "GF_SECURITY_ADMIN_PASSWORD",
        "LANGFUSE_INIT_USER_PASSWORD",
    )
    for name in pwd_names:
        assert name in parsed, f"{name} missing in secrets.env:\n{parsed}"
        value = parsed[name]
        assert re.fullmatch(r"^[A-Za-z0-9._-]+$", value), f"charset violation for {name}: {value}"
        assert len(value) >= 32, f"{name} подозрительно короткий: {len(value)}"
        assert not value.startswith("test-master-password"), f"{name} получил дата-префикс: {value}"

    values = [parsed[n] for n in pwd_names]
    assert len(set(values)) == len(values), f"M7 FAIL: дубликаты паролей (unified-auth не разорван): {values}"

    for v in pwd_names:
        monkeypatch.delenv(v, raising=False)

    logger.critical("[IMP:9][test] 4 сервис-пароля попарно различны (per-secret random, B.8) — OK")


# 🧪 TRAP[TEST] · Regression · DevPlan 176 B.8 (M7) · derived autogen ИДЕМПОТЕНТЕН (persist не перезаписывает)
# · Scenario: secrets.env содержит HERMES/GF, отсутствует LANGFUSE → _ensure_derived_passwords
# ·   сохраняет существующие, генерирует ТОЛЬКО отсутствующий; повторный вызов = no-op (файл байт-идентичен)
# · Last fail: N/A (new test — persist-идемпотентность derived)
# · Remove if: _ensure_derived_passwords idempotency branch changes
@ldd_trajectory
def test_ensure_derived_passwords_idempotent_and_generates_missing(caplog, tmp_path, monkeypatch):
    """Existing derived passwords preserved; missing one generated; repeat call = no-op.

    ## @purpose  Инвариант 2 модуля («existing secrets NOT overwritten») на derived-механике:
    ##           существующие HERMES/GF не перегенерируются; отсутствующий LANGFUSE дополняется;
    ##           повторный вызов не меняет файл (merge + atomic write, генерация ОДНОКРАТНАЯ).
    """
    secrets_env = tmp_path / "secrets.env"
    secrets_env.write_text(
        "HERMES_DASHBOARD_PASSWORD=hermes-existing-1\nGF_SECURITY_ADMIN_PASSWORD=grafana-existing-2\n",
        encoding="utf-8",
    )
    # Симуляция ensure_secrets Step 1 (source file → os.environ) — канонический вызовной контекст
    for k, v in sm.source_secrets_env(str(secrets_env)).items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("LANGFUSE_INIT_USER_PASSWORD", raising=False)

    sm._ensure_derived_passwords(str(secrets_env))

    env_content = secrets_env.read_text(encoding="utf-8")
    assert "HERMES_DASHBOARD_PASSWORD=hermes-existing-1" in env_content, "HERMES overwritten!"
    assert "GF_SECURITY_ADMIN_PASSWORD=grafana-existing-2" in env_content, "GF overwritten!"
    parsed = sm.source_secrets_env(str(secrets_env))
    assert "LANGFUSE_INIT_USER_PASSWORD" in parsed, f"LANGFUSE not generated:\n{env_content}"
    lanfuse_pwd = parsed["LANGFUSE_INIT_USER_PASSWORD"]
    assert lanfuse_pwd != "hermes-existing-1", "LANGFUSE скопирован с HERMES (не per-secret)"
    assert re.fullmatch(r"^[A-Za-z0-9._-]+$", lanfuse_pwd), f"charset violation: {lanfuse_pwd}"
    assert len(lanfuse_pwd) >= 32

    # Повторный вызов — LANGFUSE уже в env → no-op, файл байт-идентичен
    content_before = secrets_env.read_text(encoding="utf-8")
    sm._ensure_derived_passwords(str(secrets_env))
    assert secrets_env.read_text(encoding="utf-8") == content_before, "Второй вызов изменил файл!"

    monkeypatch.delenv("LANGFUSE_INIT_USER_PASSWORD", raising=False)

    logger.critical("[IMP:9][test] derived: existing preserved + missing generated (idempotent) — OK")


# endregion Tests: random-autogen (DevPlan 176 B.3/B.8 — H3/M7)


# region Tests: standalone CLI (TRAP[DEBT] 2026-08-12 — bare-импорт deploy_paths)
# 🧪 TRAP[TEST] · NEGATIVE (R5) · standalone CLI — secrets_manager.py работает без PYTHONPATH
# · Scenario: `python3 core/internal/bootstrap/lifecycle/secrets_manager.py cleanup --secrets-env <missing>`
# ·   изолированным subprocess БЕЗ PYTHONPATH (sys.path[0] = script dir, core недостижим).
# ·   Ожидание: файл не найден → exit 1 + "SKIP" (НЕ ModuleNotFoundError).
# · Last fail: 2026-08-12 (make dev-metrics htpasswd CLI — helpers.mk:81; DevPlan 142 W2 bdaa3f6d
# ·   добавил bare-импорт deploy_paths ДО sys.path-bootstrap → ModuleNotFoundError)
# · Remove if: импортная схема secrets_manager.py меняется (try/except + _PLATFORM_ROOT bootstrap)
def test_cli_standalone_without_pythonpath(tmp_path):
    """Standalone CLI survives: no PYTHONPATH → no ModuleNotFoundError (TRAP[DEBT] 2026-08-12)."""
    import subprocess

    script = (
        Path(__file__).resolve().parent.parent.parent
        / "core"
        / "internal"
        / "bootstrap"
        / "lifecycle"
        / "secrets_manager.py"
    )
    missing_env = tmp_path / "does-not-exist.env"
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}

    result = subprocess.run(
        [sys.executable, str(script), "cleanup", "--secrets-env", str(missing_env)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )

    assert result.returncode == 1, (
        f"Standalone CLI failed unexpectedly: rc={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "ModuleNotFoundError" not in result.stderr, f"Standalone CLI crashed on import:\n{result.stderr}"
    assert "SKIP" in result.stderr, f"Expected SKIP for missing file:\nstderr={result.stderr}\nstdout={result.stdout}"
    logger.critical("[IMP:9][test] standalone CLI runs without PYTHONPATH (R5 negative) — OK")


# endregion Tests: standalone CLI (TRAP[DEBT] 2026-08-12)
