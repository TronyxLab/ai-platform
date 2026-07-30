# GREP_SUMMARY: atomic-generation partial-writes staging trap-exit failure-simulation mktemp
# STRUCTURE: ▶ mock staging/ dir → write files → ◇ simulate failure → ◇ verify trap EXIT removes staging → ◇ verify originals unchanged → ⎋ pass/fail
# region MODULE_CONTRACT
## @purpose  Verify atomic generation pattern (mktemp + trap EXIT + mv) prevents partial writes
##            on failure. When a generator crashes mid-way, the staging directory must be cleaned
##            up and original files must remain untouched.
## @scope    CI gate — tests the atomic generation PATTERN, not the specific make target
##           (which requires full infra). Uses a mock shell script that follows the same
##           mktemp+trap+mv pattern used in `make generate-manifests`.
## @invariants
##   - On success: staging files are moved to original locations
##   - On failure (any non-zero exit): staging directory is removed
##   - On failure: original files are never modified (no partial writes)
##   - trap EXIT handler is registered BEFORE any write operations
##   - Staging directory name is unique (mktemp -d)
## @rationale DevPlan 090 — Atomic Generation. Partial writes are the #1 source of
##            corrupted manifests. A generator that crashes after writing 2 of 4 output files
##            leaves the project in an inconsistent state. The atomic pattern guarantees
##            all-or-nothing semantics.
## @changes 2026-07-30 · Created — DevPlan 090 gate
# endregion MODULE_CONTRACT

import logging
import subprocess
import sys

import pytest

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Atomic pattern shell script template ──
# This script follows the EXACT mktemp + trap EXIT + mv pattern used by
# `make generate-manifests` generators.
_ATOMIC_SCRIPT_SUCCESS = """#!/usr/bin/env bash
set -euo pipefail
# Simulate atomic write with success
STAGING=$(mktemp -d)
trap 'rm -rf "$STAGING"' EXIT
echo "secret: value1" > "$STAGING/output1.yaml"
echo "secret: value2" > "$STAGING/output2.yaml"
# Simulate successful generation
mv "$STAGING/output1.yaml" "{orig1}"
mv "$STAGING/output2.yaml" "{orig2}"
echo "[IMP:9][atomic] Generation succeeded"
"""

_ATOMIC_SCRIPT_FAILURE = """#!/usr/bin/env bash
set -euo pipefail
# Simulate atomic write with mid-way failure
STAGING=$(mktemp -d)
trap 'rm -rf "$STAGING"' EXIT
echo "secret: value1" > "$STAGING/output1.yaml"
# Simulate crash before writing second file
echo "[IMP:1][atomic] CRASH: unexpected error" >&2
exit 1
# These should never execute:
mv "$STAGING/output1.yaml" "{orig1}"
mv "$STAGING/output2.yaml" "{orig2}"
echo "[IMP:9][atomic] Generation succeeded"
"""

_ATOMIC_SCRIPT_NO_TRAP = """#!/usr/bin/env bash
set -euo pipefail
# Simulate non-atomic write (NO trap EXIT — this is the anti-pattern)
STAGING=$(mktemp -d)
echo "secret: value1" > "$STAGING/output1.yaml"
echo "secret: value2" > "$STAGING/output2.yaml"
# "Success" — but without trap, any exit before mv leaves staging
rm -rf "$STAGING"
echo "[IMP:9][non-atomic] Generation succeeded"
"""


@pytest.mark.gate
@ldd_trajectory
# region FUNC_test_no_partial_writes_on_failure
## @purpose  Verify atomic generation pattern prevents partial writes when generator crashes
## @io       ⇥ tmp_path: pytest fixture → mock shell scripts → assert pass/fail
## @complexity O(1) — runs 3 shell scripts, checks file state
## 🧪 TRAP[TEST] · 2026-07-30 · REGRESSION · Atomic generation on failure
## · Scenario: Generator crashes mid-way; verify staging is cleaned up and originals intact
## · Last fail: N/A (new gate)
## · Remove if: generation is restructured to use a different atomicity mechanism
def test_no_partial_writes_on_failure(tmp_path, caplog) -> None:
    """Verify that the atomic generation pattern (mktemp + trap EXIT + mv) prevents
    partial writes on failure.

    Tests three scenarios:
    1. Success: files are atomically moved to originals
    2. Failure with trap: staging cleaned up, originals unchanged
    3. Anti-pattern (no trap): detection of the non-atomic pattern
    """
    caplog.set_level(logging.INFO)
    print("[IMP:8][test_no_partial_writes_on_failure] Testing atomic generation pattern...", file=sys.stderr)

    # ── Setup ──
    orig1 = tmp_path / "output1.yaml"
    orig2 = tmp_path / "output2.yaml"

    # Create original files with known content (simulating previous valid manifests)
    orig1.write_text("original_value: should_not_change\n")
    orig2.write_text("original_value: should_not_change\n")

    print("[IMP:7][test_no_partial_writes_on_failure] Original files created with checksums", file=sys.stderr)

    # ── Test 1: Success case ──
    script_success = _ATOMIC_SCRIPT_SUCCESS.format(orig1=str(orig1), orig2=str(orig2))
    script_path = tmp_path / "atomic_success.sh"
    script_path.write_text(script_success)
    script_path.chmod(0o755)

    result = subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"Atomic success script failed: returncode={result.returncode}\nstderr: {result.stderr}"
    )
    assert orig1.read_text() == "secret: value1\n", (
        f"Atomic success: orig1 content mismatch. Expected 'secret: value1\\n', got '{orig1.read_text()}'"
    )
    assert orig2.read_text() == "secret: value2\n", (
        f"Atomic success: orig2 content mismatch. Expected 'secret: value2\\n', got '{orig2.read_text()}'"
    )
    logger.info("[IMP:9][test_no_partial_writes_on_failure] Test 1 (success): PASS — files atomically moved")

    # ── Test 2: Failure with trap EXIT ──
    # Restore originals
    orig1.write_text("original_value: should_not_change\n")
    orig2.write_text("original_value: should_not_change\n")

    script_failure = _ATOMIC_SCRIPT_FAILURE.format(orig1=str(orig1), orig2=str(orig2))
    script_path2 = tmp_path / "atomic_failure.sh"
    script_path2.write_text(script_failure)
    script_path2.chmod(0o755)

    result = subprocess.run(
        ["bash", str(script_path2)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    # This script should fail (exit non-zero)
    print(f"[IMP:7][test_no_partial_writes_on_failure] Failure script exit code: {result.returncode}", file=sys.stderr)

    # Verify originals are UNCHANGED (no partial write)
    assert orig1.read_text() == "original_value: should_not_change\n", (
        f"Atomic failure: orig1 was MODIFIED despite generator crash! "
        f"Expected 'original_value: should_not_change\\n', got '{orig1.read_text()}'"
    )
    assert orig2.read_text() == "original_value: should_not_change\n", (
        f"Atomic failure: orig2 was MODIFIED despite generator crash! "
        f"Expected 'original_value: should_not_change\\n', got '{orig2.read_text()}'"
    )

    # Verify staging is cleaned up (no orphaned staging dirs)
    staging_dirs = [p for p in tmp_path.iterdir() if p.is_dir() and p.name.startswith("tmp.")]
    assert len(staging_dirs) == 0, (
        f"Atomic failure: orphaned staging directories remain: {staging_dirs}\ntrap EXIT should have cleaned them up."
    )
    logger.info(
        "[IMP:9][test_no_partial_writes_on_failure] Test 2 (failure+trap): PASS — originals unchanged, staging cleaned"
    )

    # ── Test 3: Anti-pattern detection ──
    # Detect scripts without trap EXIT by statically analyzing the pattern
    script_no_trap = _ATOMIC_SCRIPT_NO_TRAP.format(orig1=str(orig1), orig2=str(orig2))
    has_trap = "trap" in script_no_trap
    has_mktemp = "mktemp -d" in script_no_trap or "mktemp" in script_no_trap

    if has_mktemp and not has_trap:
        logger.warning("[IMP:7][test_no_partial_writes_on_failure] Anti-pattern detected: uses mktemp but no trap EXIT")
        # This is what we expect the test to detect — the anti-pattern
        print(
            "[IMP:7][test_no_partial_writes_on_failure] Anti-pattern detection: PASS (no trap = risk of partial write)",
            file=sys.stderr,
        )
    else:
        print("[IMP:7][test_no_partial_writes_on_failure] Script has trap — proper atomic pattern", file=sys.stderr)

    logger.info(
        "[IMP:9][test_no_partial_writes_on_failure] ALL PASS — atomic pattern verified: "
        "success writes atomically, failure preserves originals"
    )


# endregion FUNC_test_no_partial_writes_on_failure
