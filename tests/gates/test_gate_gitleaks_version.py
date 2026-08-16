# GREP_SUMMARY: gate gitleaks-version single-version composite-action setup-gitleaks version-drift
# STRUCTURE: ▶ parse workflow YAMLs → ◇ extract gitleaks version references → ◇ assert single version + composite usage → ⎋ 2 tests
# region MODULE_CONTRACT
## @purpose — Gate test suite for gitleaks version consistency and composite action usage (Plan 2).
##            Validates that all CI workflows use the same gitleaks version (8.30.1) and that
##            gitleaks-consuming workflows use the setup-gitleaks composite action instead of
##            inline install.
## @scope — Parses .github/workflows/*.yml to verify gitleaks version invariants.
## @invariants
##   - All gitleaks version references in workflows point to version 8.30.1
##   - push-gate, platform-test use setup-gitleaks composite action
##   - No workflow contains inline gitleaks install (curl + tar)
## @rationale — Prevents gitleaks version drift across 2 workflow consumers (P5).
##              Composite action setup-gitleaks is the single source of truth.
# endregion MODULE_CONTRACT

import logging
import pathlib
import re

import pytest
import yaml

from tests.helpers.gate_helpers import load_yaml, repo_root

logger = logging.getLogger(__name__)

_WORKFLOW_DIR: pathlib.Path = repo_root() / ".github" / "workflows"
_SETUP_GITLEAKS_ACTION_DIR: pathlib.Path = repo_root() / ".github" / "actions" / "setup-gitleaks"

# Workflows that should use setup-gitleaks composite action
_GITLEAKS_CONSUMER_WORKFLOWS: set[str] = {
    "push-gate.yml",
    "platform-test.yml",
}


# Read expected gitleaks version from composite action (single source of truth)
def _get_expected_gitleaks_version() -> str:
    """Read gitleaks default version from .github/actions/setup-gitleaks/action.yml.

    ## @purpose — Single source of truth: version is defined in the composite action's
    ##            default input, not hardcoded in test. Eliminates version drift (P5).
    ## @io — ⎋ str: version string (e.g. "8.30.1")
    ## @complexity — O(1)
    ## @invariants
    ##   - Falls back to "8.30.1" if action.yml cannot be read (defensive)
    """
    action_file = _SETUP_GITLEAKS_ACTION_DIR / "action.yml"
    if not action_file.exists():
        logger.warning("[IMP:8][test] setup-gitleaks action.yml not found — falling back to 8.30.1")
        return "8.30.1"
    try:
        data = load_yaml(action_file)
        version = data.get("inputs", {}).get("version", {}).get("default", "8.30.1")
        logger.info("[IMP:8][test] Gitleaks version read from action.yml: %s", version)
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("[IMP:8][test] Failed to parse action.yml: %s — falling back to 8.30.1", exc)
        return "8.30.1"
    else:
        return version


# Pattern to detect inline gitleaks install (curl download + tar extract)
_INLINE_GITLEAKS_INSTALL_PATTERN: re.Pattern = re.compile(r"gitleaks.*releases/download")

# Pattern to detect composite action usage
# Wave 2 (W2-E2): accept either direct setup-gitleaks OR setup-platform (which includes setup-gitleaks internally)
_COMPOSITE_ACTION_PATTERN: re.Pattern = re.compile(r"uses:\s*\./\.github/actions/(setup-gitleaks|setup-platform)(\b|@)")

# Pattern to detect version references
_VERSION_REF_PATTERN: re.Pattern = re.compile(r"gitleaks[_-]?(\d+\.\d+\.\d+)")


@pytest.mark.gate

# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate invariant — first line of defense against drift in platform contracts
# · Last fail: N/A (preventive)
# · Remove if: entire gate category is superseded by a newer mechanism
def test_single_gitleaks_version(caplog):
    """Verify all gitleaks version references across workflows point to version from action.yml (single source)."""
    caplog.set_level(logging.INFO)
    expected_version = _get_expected_gitleaks_version()
    version_violations: list[str] = []

    for wf_file in _WORKFLOW_DIR.glob("*.yml"):
        content = wf_file.read_text()
        # Check for inline gitleaks version references
        versions = _VERSION_REF_PATTERN.findall(content)
        version_violations.extend(
            f"{wf_file.name}: references gitleaks version {ver} (expected {expected_version})"
            for ver in versions
            if ver != expected_version
        )

    # Also check the composite action itself for version consistency
    action_file = _SETUP_GITLEAKS_ACTION_DIR / "action.yml"
    if action_file.exists():
        action_content = action_file.read_text()
        if expected_version not in action_content:
            logger.warning("[IMP:8][test] setup-gitleaks action.yml may have outdated default version")
    else:
        logger.error("[IMP:10][test] setup-gitleaks composite action not found at %s", action_file)
        version_violations.append("setup-gitleaks composite action missing")

    if version_violations:
        for v in version_violations:
            logger.error("[IMP:10][test] Gitleaks version violation: %s", v)
        pytest.fail(f"Gitleaks version violations: {version_violations}")

    logger.info("[IMP:9][test] All workflows reference gitleaks version %s", expected_version)


@pytest.mark.gate
def test_gitleaks_composite_action_used(caplog):
    """Verify gitleaks-consuming workflows use setup-gitleaks composite action (not inline install)."""
    caplog.set_level(logging.INFO)
    violations: list[str] = []

    for wf_name in _GITLEAKS_CONSUMER_WORKFLOWS:
        wf_path = _WORKFLOW_DIR / wf_name
        content = wf_path.read_text()

        # Should use composite action
        if not _COMPOSITE_ACTION_PATTERN.search(content):
            violations.append(f"{wf_name}: does not use setup-gitleaks composite action")

        # Should NOT have inline gitleaks install
        if _INLINE_GITLEAKS_INSTALL_PATTERN.search(content):
            violations.append(f"{wf_name}: contains inline gitleaks install (should use composite action)")

    if violations:
        for v in violations:
            logger.error("[IMP:10][test] Gitleaks composite action violation: %s", v)
        pytest.fail(f"Gitleaks composite action violations: {violations}")

    logger.info("[IMP:9][test] All gitleaks-consuming workflows use setup-gitleaks composite action")
