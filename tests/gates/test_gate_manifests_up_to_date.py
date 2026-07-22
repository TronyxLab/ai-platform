# GREP_SUMMARY: gate manifests-up-to-date check-manifests freshness generated
# STRUCTURE: ▶ make check-manifests → ◇ subprocess.run → ⊕ assert exit 0 → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Freshness gate for generated manifests. Replaces test_gate_secrets_manifest.py.
##            Runs `make check-manifests` which verifies all generated files are in sync
##            with authoritative sources.
## @scope    CI gate — replaces 381 LOC of manual validation
## @invariants
##   - Runs `make check-manifests` as subprocess
##   - Exit code 0 = all generated files up to date
##   - Exit code 1 = drift detected, error message guides to `make generate-manifests`
## @rationale DevPlan 051: freshness checks moved from structural test to dedicated
##            git-diff-based gate. Structural checks remain in test_gate_manifest_integrity.py.
## @changes 2026-07-22 | Created (DevPlan 051 Wave 3)
# endregion MODULE_CONTRACT

import logging
import subprocess

import pytest

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-22 · REGRESSION · Gate invariant — all generated manifests up to date
# · Scenario: Generated files (secrets-manifest.yaml, platform-env.yaml, smoke_env_generated.py,
#   env_defaults_generated.py) must match authoritative sources.
# · Last fail: N/A (new gate)
# · Remove if: make check-manifests mechanism is superseded
def test_manifests_up_to_date(caplog):
    """Verify make check-manifests passes — all generated files up to date.

    Replaces test_gate_secrets_manifest.py (381 LOC) with a single git-diff gate.
    """
    caplog.set_level(logging.INFO)
    logger.info("[IMP:8][test_manifests_up_to_date] Running make check-manifests...")

    result = subprocess.run(
        ["make", "check-manifests"],
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode != 0:
        logger.error(
            "[IMP:10][test_manifests_up_to_date] FAILED: Generated manifests out of date\n%s\n%s",
            result.stdout,
            result.stderr,
        )
        pytest.fail(
            f"Generated manifests are out of date (exit code {result.returncode}).\n"
            f"Run: make generate-manifests\n\n"
            f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
        )

    logger.info("[IMP:9][test_manifests_up_to_date] PASSED — all generated manifests up to date")
