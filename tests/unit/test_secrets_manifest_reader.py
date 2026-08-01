"""
# GREP_SUMMARY: test_secrets_manifest_reader, iter_secrets, strict, tier, consumers, charset, gen-command, tmp_path
# STRUCTURE: ▶ tmp_path manifest fixtures → ◇ iter_secrets 4× (valid/missing/non-dict/non-list) → ◇ helpers 4× → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/secrets_manifest_reader.py — strict
##           secrets-manifest.yaml reader (DevPlan 116 T4, U-33).
## @scope    Tests iter_secrets (valid/missing/malformed) and typed helpers
##           tier/consumers/charset/gen_command with absent-field defaults.
## @invariants
##   - All tests create manifest fixtures under tmp_path (no hardcoded paths)
##   - iter_secrets raises FileNotFoundError on missing, ValueError on non-dict/non-list
##   - Helpers return safe defaults ("" / []) for absent fields
##   - Each test decorated with @ldd_trajectory and asserts IMP:9 log presence
## @rationale DevPlan 116 T4: single strict reader replaces 3 graceful-degradation parsers.
##            Strictness (raise instead of return []) is the core contract under test.
## @changes 2026-07-31 | Created (DevPlan 116 T4)
# endregion MODULE_CONTRACT
"""

import logging
import sys
from pathlib import Path

import yaml

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_SHARED_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "shared"
sys.path.insert(0, str(_SHARED_DIR))
import pytest
import secrets_manifest_reader as smr  # type: ignore[import-untyped]

_MANIFEST_DATA = {
    "secrets": [
        {
            "name": "POSTGRES_PASSWORD",
            "tier": "required",
            "consumers": ["postgres", "pgbouncer"],
            "charset": "^[A-Za-z0-9._-]+$",
        },
        {
            "name": "LITELLM_MASTER_KEY",
            "tier": "generated",
            "consumers": ["litellm"],
            "gen_command": 'echo "sk-$(openssl rand -hex 32)"',
        },
        {
            "name": "SALT",
            "tier": "generated",
            "gen_command": "openssl rand -hex 16",
        },
    ]
}


def _write_manifest(tmp_path: Path, data: dict) -> Path:
    """Write a manifest fixture to tmp_path."""
    path = tmp_path / "secrets-manifest.yaml"
    path.write_text(yaml.dump(data))
    return path


# ═══════════════════════════════════════════════════════════════════
# region Tests: iter_secrets
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · DevPlan 116 T4 · iter_secrets returns all entries
# · Scenario: valid manifest with 3 entries → returns list of 3 dicts (no filtering)
# · Last fail: N/A (new test)
# · Remove if: iter_secrets contract changes
@ldd_trajectory
def test_iter_secrets_valid(caplog, tmp_path):
    """iter_secrets returns ALL manifest entries (filtering via helpers, not here)."""
    path = _write_manifest(tmp_path, _MANIFEST_DATA)

    result = smr.iter_secrets(path)

    assert len(result) == 3, f"Expected 3 entries, got {len(result)}"
    names = {s["name"] for s in result}
    assert names == {"POSTGRES_PASSWORD", "LITELLM_MASTER_KEY", "SALT"}
    logger.critical("[IMP:9][test] iter_secrets loaded %d entries — OK", len(result))


# 🧪 TRAP[TEST] · Regression · DevPlan 116 T4 · missing manifest raises FileNotFoundError
# · Scenario: nonexistent path → FileNotFoundError (STRICT — no return [])
# · Last fail: N/A (new test)
# · Remove if: strictness contract changes
@ldd_trajectory
def test_iter_secrets_missing_raises(caplog, tmp_path):
    """iter_secrets must raise FileNotFoundError for missing manifest (no [] fallback)."""
    with pytest.raises(FileNotFoundError):
        smr.iter_secrets(tmp_path / "nonexistent.yaml")

    logger.critical("[IMP:9][test] iter_secrets missing manifest raises FileNotFoundError — OK")


# 🧪 TRAP[TEST] · Regression · DevPlan 116 T4 · non-dict document raises ValueError
# · Scenario: manifest content is a YAML list → ValueError
# · Last fail: N/A (new test)
# · Remove if: strictness contract changes
@ldd_trajectory
def test_iter_secrets_non_dict_raises(caplog, tmp_path):
    """iter_secrets must raise ValueError when document is not a dict."""
    path = _write_manifest(tmp_path, ["not", "a", "dict"])

    with pytest.raises(ValueError):
        smr.iter_secrets(path)

    logger.critical("[IMP:9][test] iter_secrets non-dict manifest raises ValueError — OK")


# 🧪 TRAP[TEST] · Regression · DevPlan 116 T4 · secrets-not-a-list raises ValueError
# · Scenario: dict without 'secrets' list → ValueError
# · Last fail: N/A (new test)
# · Remove if: strictness contract changes
@ldd_trajectory
def test_iter_secrets_secrets_not_list_raises(caplog, tmp_path):
    """iter_secrets must raise ValueError when secrets key is not a list."""
    path = _write_manifest(tmp_path, {"secrets": "not-a-list"})

    with pytest.raises(ValueError):
        smr.iter_secrets(path)

    logger.critical("[IMP:9][test] iter_secrets secrets-not-list raises ValueError — OK")


# 🧪 TRAP[TEST] · Regression · DevPlan 116 T4 · non-dict entries skipped with warning
# · Scenario: list containing a non-dict item → skipped, dict items returned
# · Last fail: N/A (new test)
# · Remove if: defensive skip behavior changes
@ldd_trajectory
def test_iter_secrets_skips_non_dict_entries(caplog, tmp_path):
    """iter_secrets skips non-dict entries defensively (dict access safety)."""
    path = _write_manifest(tmp_path, {"secrets": [{"name": "OK"}, "junk", 42]})

    result = smr.iter_secrets(path)

    assert len(result) == 1, f"Expected 1 dict entry, got {len(result)}"
    assert result[0]["name"] == "OK"
    logger.critical("[IMP:9][test] iter_secrets skipped %d non-dict entries", 2)


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: typed helpers
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · DevPlan 116 T4 · tier helper returns value or ''
# · Scenario: entry with tier + entry without → value / ""
# · Last fail: N/A (new test)
# · Remove if: helper contract changes
@ldd_trajectory
def test_tier_helper(caplog):
    """tier() returns the tier field or '' when absent."""
    assert smr.tier({"tier": "generated"}) == "generated"
    assert smr.tier({"name": "x"}) == ""
    logger.critical("[IMP:9][test] tier helper returns value/'' — OK")


# 🧪 TRAP[TEST] · Regression · DevPlan 116 T4 · consumers helper returns list or []
# · Scenario: entry with consumers + entry without → list / []
# · Last fail: N/A (new test)
# · Remove if: helper contract changes
@ldd_trajectory
def test_consumers_helper(caplog):
    """consumers() returns the consumers list or []."""
    assert smr.consumers({"consumers": ["postgres", "litellm"]}) == ["postgres", "litellm"]
    assert smr.consumers({"name": "x"}) == []
    assert smr.consumers({"consumers": "not-a-list"}) == []
    logger.critical("[IMP:9][test] consumers helper returns list/[] — OK")


# 🧪 TRAP[TEST] · Regression · DevPlan 116 T4 · charset helper returns regex or ''
# · Scenario: entry with charset + entry without → regex / ""
# · Last fail: N/A (new test)
# · Remove if: helper contract changes
@ldd_trajectory
def test_charset_helper(caplog):
    """charset() returns the charset regex or ''."""
    assert smr.charset({"charset": "^[A-Za-z0-9._-]+$"}) == "^[A-Za-z0-9._-]+$"
    assert smr.charset({"name": "x"}) == ""
    logger.critical("[IMP:9][test] charset helper returns regex/'' — OK")


# 🧪 TRAP[TEST] · Regression · DevPlan 116 T4 · gen_command helper returns value or ''
# · Scenario: entry with gen_command + entry without → value / ""
# · Last fail: N/A (new test)
# · Remove if: helper contract changes
@ldd_trajectory
def test_gen_command_helper(caplog):
    """gen_command() returns the generation command or ''."""
    assert smr.gen_command({"gen_command": "openssl rand -hex 16"}) == "openssl rand -hex 16"
    assert smr.gen_command({"name": "x"}) == ""
    logger.critical("[IMP:9][test] gen_command helper returns value/'' — OK")


# endregion
