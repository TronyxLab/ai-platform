"""
# GREP_SUMMARY: test-secrets-env-cleanup, cleanup-secrets-env, tor-enabled, proxy-removal, secrets-env, atomic-write, noop-missing
# STRUCTURE: ▶ tmp_path fixtures → ◇ cleanup_secrets_env: removes-proxy (1x) / keeps-proxy (1x) / noop-missing (1x) / preserves-other-vars (1x) / unchanged-no-proxy (1x) → ⎋ LDD trajectory IMP:9 assertions
# region MODULE_CONTRACT
## @purpose  Unit tests for secrets_manager.cleanup_secrets_env() — proxy-var stripping
##           from secrets.env with atomic write (DevPlan 102 TASK-8, contract §4.1)
## @scope    Tests cleanup_secrets_env with tmp_path fixtures (fake KEY=value lines only,
##           never real secrets). Native imports — no subprocess.
## @invariants
##   - File operations use tmp_path exclusively
##   - Each test validates IMP:9 business logic log presence via @ldd_trajectory
##   - Fake proxy values only — no real credentials anywhere
## @changes
##   2026-07-31 · Created — DevPlan 102 TASK-8 (5 tests per §7 $TEST_SPEC)
# endregion MODULE_CONTRACT
"""

import logging
import sys
from pathlib import Path

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "lifecycle"
sys.path.insert(0, str(_MODULE_DIR))
import secrets_manager as sm

pytestmark = pytest.mark.static_audit

# Re-export for fixture cleanups
MODULE = sm


# ═══════════════════════════════════════════════════════════════════
# region Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def secrets_env_file(tmp_path) -> Path:
    """Provide a tmp_path-relative secrets.env path for each test."""
    return tmp_path / "secrets.env"


def _write_env(path: Path, lines: list[str]) -> Path:
    """Write fake secrets.env lines to path and return the path."""
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return path


# endregion Fixtures


# ═══════════════════════════════════════════════════════════════════
# region Tests: cleanup_secrets_env — proxy removal (DevPlan 102 §7)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · TOR_ENABLED=false removes proxy vars
# · Scenario: secrets.env with HTTP_PROXY/HTTPS_PROXY + TOR_ENABLED=false → proxy lines
# ·   absent from returned dict AND from file after cleanup
# · Last fail: N/A (new test)
# · Remove if: cleanup_secrets_env proxy-stripping logic changes
@ldd_trajectory
def test_cleanup_removes_proxy_when_tor_disabled(caplog, secrets_env_file):
    """TOR_ENABLED=false → HTTP_PROXY/HTTPS_PROXY removed from secrets.env.

    ## @purpose  Verify the primary contract: when Tor is disabled, proxy vars are
    ##           stripped from secrets.env both in the returned dict and on disk.
    """
    _write_env(
        secrets_env_file,
        [
            "HTTP_PROXY=http://proxy.local:3128",
            "HTTPS_PROXY=http://proxy.local:3128",
            "TOR_ENABLED=false",
            "OTHER=keep-me",
        ],
    )

    result = sm.cleanup_secrets_env(str(secrets_env_file), tor_enabled="false")

    assert "HTTP_PROXY" not in result, f"HTTP_PROXY not removed: {result}"
    assert "HTTPS_PROXY" not in result, f"HTTPS_PROXY not removed: {result}"
    assert result.get("OTHER") == "keep-me", f"Non-proxy var lost: {result}"
    assert result.get("TOR_ENABLED") == "false", f"TOR_ENABLED lost: {result}"

    on_disk = secrets_env_file.read_text(encoding="utf-8")
    assert "HTTP_PROXY=" not in on_disk, f"HTTP_PROXY still on disk:\n{on_disk}"
    assert "HTTPS_PROXY=" not in on_disk, f"HTTPS_PROXY still on disk:\n{on_disk}"

    logger.critical("[IMP:9][test] cleanup removed proxy vars when TOR_ENABLED=false — OK")


# 🧪 TRAP[TEST] · Regression · TOR_ENABLED=true keeps proxy vars
# · Scenario: secrets.env with HTTP_PROXY/HTTPS_PROXY + TOR_ENABLED=true → proxy lines
# ·   preserved both in dict and on disk (file must NOT be rewritten)
# · Last fail: N/A (new test)
# · Remove if: cleanup_secrets_env TOR gating logic changes
@ldd_trajectory
def test_cleanup_keeps_proxy_when_tor_enabled(caplog, secrets_env_file):
    """TOR_ENABLED=true → HTTP_PROXY/HTTPS_PROXY kept in secrets.env.

    ## @purpose  Verify the TOR gating contract: with Tor enabled the proxy vars must
    ##           survive cleanup untouched (byte-identical file, no rewrite).
    """
    original = "HTTP_PROXY=http://proxy.local:3128\nHTTPS_PROXY=http://proxy.local:3128\nTOR_ENABLED=true\n"
    secrets_env_file.write_text(original, encoding="utf-8")

    result = sm.cleanup_secrets_env(str(secrets_env_file), tor_enabled="true")

    assert result.get("HTTP_PROXY") == "http://proxy.local:3128", f"HTTP_PROXY lost: {result}"
    assert result.get("HTTPS_PROXY") == "http://proxy.local:3128", f"HTTPS_PROXY lost: {result}"
    assert secrets_env_file.read_text(encoding="utf-8") == original, "File rewritten despite TOR_ENABLED=true"

    logger.critical("[IMP:9][test] cleanup kept proxy vars when TOR_ENABLED=true — OK")


# 🧪 TRAP[TEST] · Regression · missing file → no-op empty dict
# · Scenario: cleanup on non-existent path → returns {} without raising, no file created
# · Last fail: N/A (new test)
# · Remove if: cleanup_secrets_env missing-file handling changes
@ldd_trajectory
def test_cleanup_noop_on_missing_file(caplog, tmp_path):
    """cleanup on a non-existent file → returns {} without error (no-op).

    ## @purpose  Verify the no-op contract: missing secrets.env must not raise and must
    ##           not create any file — returns empty dict (DevPlan 102 §4.1).
    """
    missing = tmp_path / "does-not-exist.env"
    assert not missing.exists()

    result = sm.cleanup_secrets_env(str(missing), tor_enabled="false")

    assert result == {}, f"Expected empty dict for missing file, got: {result}"
    assert not missing.exists(), "cleanup must not create the missing file"

    logger.critical("[IMP:9][test] cleanup no-op on missing file returned {} — OK")


# 🧪 TRAP[TEST] · Regression · atomic write preserves non-proxy vars (0o600)
# · Scenario: secrets.env with 10 vars (incl. proxy) → after cleanup all 8 non-proxy
# ·   vars preserved, file parseable, permissions 0o600 (atomic write contract)
# · Last fail: N/A (new test)
# · Remove if: cleanup_secrets_env atomic-write behavior changes
@ldd_trajectory
def test_cleanup_atomic_write_preserves_other_vars(caplog, secrets_env_file):
    """After cleanup, all non-proxy vars survive and file mode is 0o600.

    ## @purpose  Verify the atomic-write contract: the rewritten file must contain all
    ##           non-proxy variables, be valid key=value syntax, and carry 0o600
    ##           permissions (secrets must not be world-readable).
    """
    all_vars = {
        "HTTP_PROXY": "http://proxy.local:3128",
        "HTTPS_PROXY": "http://proxy.local:3128",
        "TOR_ENABLED": "false",
        "LITELLM_MASTER_KEY": "sk-test-1",
        "NEXTAUTH_SECRET": "hex-test-2",
        "LANGFUSE_PUBLIC_KEY": "pk-lf-test-3",
        "LANGFUSE_SECRET_KEY": "sk-lf-test-4",
        "POSTGRES_PASSWORD": "pg-test-5",
        "WEBNAMES_API_KEY": "wn-test-6",
        "SALT": "salt-test-7",
    }
    _write_env(secrets_env_file, [f"{k}={v}" for k, v in all_vars.items()])

    result = sm.cleanup_secrets_env(str(secrets_env_file), tor_enabled="false")

    # 8 non-proxy vars preserved, 2 proxy vars removed
    expected_kept = {k: v for k, v in all_vars.items() if k not in {"HTTP_PROXY", "HTTPS_PROXY"}}
    assert result == expected_kept, f"Mismatch after cleanup:\nresult={result}\nexpected={expected_kept}"

    on_disk = secrets_env_file.read_text(encoding="utf-8")
    for k, v in expected_kept.items():
        assert f"{k}={v}" in on_disk, f"{k} lost from file:\n{on_disk}"
    assert "HTTP_PROXY=" not in on_disk and "HTTPS_PROXY=" not in on_disk

    # Atomic write mode: 0o600
    mode = secrets_env_file.stat().st_mode & 0o777
    assert mode == 0o600, f"Expected 0o600 permissions, got 0o{mode:o}"

    logger.critical("[IMP:9][test] cleanup atomic write preserved %d non-proxy vars (0o600) — OK", len(result))


# 🧪 TRAP[TEST] · Regression · no proxy vars → byte-identical file
# · Scenario: secrets.env WITHOUT proxy lines → cleanup does not touch the file at all
# ·   (no rewrite — byte-identical preservation)
# · Last fail: N/A (new test)
# · Remove if: cleanup_secrets_env no-change short-circuit changes
@ldd_trajectory
def test_cleanup_no_proxy_vars_unchanged(caplog, secrets_env_file):
    """secrets.env without proxy vars → file byte-identical after cleanup.

    ## @purpose  Verify the no-change short-circuit: when there is nothing to remove,
    ##           cleanup must NOT rewrite the file — the bytes stay identical
    ##           (avoids needless mtime churn and preserves comments/formatting).
    """
    original = (
        "TOR_ENABLED=false\n"
        "LITELLM_MASTER_KEY=sk-test-1\n"
        "# inline comment preserved when no rewrite happens\n"
        "NEXTAUTH_SECRET=hex-test-2\n"
    )
    secrets_env_file.write_text(original, encoding="utf-8")

    result = sm.cleanup_secrets_env(str(secrets_env_file), tor_enabled="false")

    assert "HTTP_PROXY" not in result and "HTTPS_PROXY" not in result
    assert secrets_env_file.read_text(encoding="utf-8") == original, "File changed despite no proxy vars present"

    logger.critical("[IMP:9][test] cleanup left proxy-free file byte-identical — OK")


# endregion Tests: cleanup_secrets_env — proxy removal (DevPlan 102 §7)
