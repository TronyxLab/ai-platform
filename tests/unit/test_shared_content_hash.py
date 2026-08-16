# GREP_SUMMARY: test-shared-content-hash content-hash compute-content-hash sha256 cli
# STRUCTURE: ┌tmp_path fixtures┐ → ○ test scenarios: two_files → order_matters → missing_file → empty_list → cli
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/content_hash.py
##           Verifies compute_content_hash() behavior and CLI.
## @scope    Tests: deterministic hash, order sensitivity, missing file tolerance,
##           empty list, and CLI interface.
## @invariants
##   - All tests use tmp_path (no hardcoded paths)
##   - No Docker dependency (pure Python)
##   - LDD: at least one IMP:9 log in each successful scenario
# endregion MODULE_CONTRACT

import logging
import subprocess
import sys
from pathlib import Path

import pytest

from core.internal.shared.content_hash import compute_content_hash

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.static_audit

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_files(tmp_path: Path) -> dict[str, Path]:
    """Create two sample files with known content.

    ## @purpose — Provide deterministic file content for hash verification.
    ## @io — ⇥ tmp_path → ⎋ dict of {name: path}
    """
    f1 = tmp_path / "file_a.txt"
    f2 = tmp_path / "file_b.txt"
    f1.write_text("hello", encoding="utf-8")
    f2.write_text("world", encoding="utf-8")
    return {"a": f1, "b": f2}


# ── Tests ───────────────────────────────────────────────────────────────────


# region FUNC_test_compute_hash_two_files
## @purpose — Verify deterministic hash from two files.
##            AC: two temp files → consistent hash.
## @complexity — O(1)
def test_compute_hash_two_files(sample_files: dict[str, Path], caplog: pytest.LogCaptureFixture) -> None:
    """Two files produce a consistent SHA-256 hash."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: deterministic hash from two files
    # · Last fail: N/A (new test)
    # · Remove if: content_hash algorithm changes

    h1 = compute_content_hash([str(sample_files["a"]), str(sample_files["b"])])
    h2 = compute_content_hash([str(sample_files["a"]), str(sample_files["b"])])

    # LDD trajectory
    found_imp9 = False
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                logger.info("%s", record.message)
            if imp_level >= 9:
                found_imp9 = True
    logger.info("--- END LDD TRAJECTORY ---")

    assert h1 == h2, "Hash must be deterministic for same files"
    assert len(h1) == 64, "SHA-256 hex digest must be 64 chars"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region FUNC_test_compute_hash_order_matters
## @purpose — Verify that file order affects the hash.
##            AC: different order → different hash.
## @complexity — O(1)
def test_compute_hash_order_matters(sample_files: dict[str, Path], caplog: pytest.LogCaptureFixture) -> None:
    """Different file order produces different hash."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: order sensitivity
    # · Last fail: N/A (new test)
    # · Remove if: content_hash adopts order-independent hashing

    h_ab = compute_content_hash([str(sample_files["a"]), str(sample_files["b"])])
    h_ba = compute_content_hash([str(sample_files["b"]), str(sample_files["a"])])

    found_imp9 = False
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                logger.info("%s", record.message)
            if imp_level >= 9:
                found_imp9 = True
    logger.info("--- END LDD TRAJECTORY ---")

    assert h_ab != h_ba, "Hash must change when file order changes"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region FUNC_test_compute_hash_missing_file
## @purpose — Verify missing file is tolerated (WARNING, not fatal).
##            AC: missing file → warning, not fatal.
## @complexity — O(1)
def test_compute_hash_missing_file(sample_files: dict[str, Path], caplog: pytest.LogCaptureFixture) -> None:
    """Missing file logs WARNING and continues."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: missing file tolerance
    # · Last fail: N/A (new test)
    # · Remove if: content_hash changes missing-file behavior

    missing = str(sample_files["a"].parent / "nonexistent.txt")
    result = compute_content_hash([missing, str(sample_files["a"])])

    found_warning = any("File not found" in record.message for record in caplog.records)
    found_imp9 = False
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                logger.info("%s", record.message)
            if imp_level >= 9:
                found_imp9 = True
    logger.info("--- END LDD TRAJECTORY ---")

    assert found_warning, "Missing file should log a WARNING"
    assert len(result) == 64, "Should still produce a valid hash"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region FUNC_test_compute_hash_empty_list
## @purpose — Verify empty file list returns sha256("").
##            AC: empty list → sha256("").
## @complexity — O(1)
def test_compute_hash_empty_list(caplog: pytest.LogCaptureFixture) -> None:
    """Empty file list returns sha256('')."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: empty input
    # · Last fail: N/A (new test)
    # · Remove if: content_hash changes empty-input behavior

    result = compute_content_hash([])
    expected = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    found_imp9 = False
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                logger.info("%s", record.message)
            if imp_level >= 9:
                found_imp9 = True
    logger.info("--- END LDD TRAJECTORY ---")

    assert result == expected, f"Empty list should produce sha256(''): {expected}"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region FUNC_test_cli_compute
## @purpose — Verify CLI interface works.
##            AC: `python3 -m core.internal.shared.content_hash compute --files ...`
## @complexity — O(1)
def test_cli_compute(sample_files: dict[str, Path]) -> None:
    """CLI compute command works via subprocess."""
    # 🧪 TRAP[TEST] · Regression · Scenario: CLI interface
    # · Last fail: N/A (new test)
    # · Remove if: CLI entry point is removed

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "core.internal.shared.content_hash",
            "compute",
            "--files",
            str(sample_files["a"]),
            str(sample_files["b"]),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, f"CLI failed: {result.stderr}"
    assert len(result.stdout.strip()) == 64, "CLI should output 64-char hex hash"


# endregion
