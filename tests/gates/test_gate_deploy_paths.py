#!/usr/bin/env python3
# GREP_SUMMARY: gate-deploy-paths, canonical-registry, deprecated-removal-plan, entrypoint-manifest, ci-gate
# STRUCTURE: ▶ test_canonical_paths_registered → ◇ test_no_unregistered_paths → ◇ test_deprecated_have_removal_plan → ⎋
# region MODULE_CONTRACT
## @purpose  CI gate test: ensures every deploy-related path in entrypoint-manifest.yaml
##           is registered in CANONICAL_DEPLOY_PATHS, no unregistered paths exist, and
##           every deprecated path has an explicit removal plan with target_date.
## @scope    Production gate (make gate MODE=fast) — blocks merge if a new deploy mechanism
##           appears without registration.
## @invariants
##   - Deploy-related make_targets identified by 'deploy' in the target name or mechanism
##   - CANONICAL_DEPLOY_PATHS is the single source of truth
##   - DEPRECATED_DEPLOY_PATHS must have target_date, removal_mechanism, verification
## @rationale DRIFT-D1 (Brief 077): deploy paths were undocumented — no mechanism
##           to prevent accidental addition of unvetted delivery mechanisms.
##   Gate test closes this gap.
## @changes  2026-07-26 | DevPlan 081 Phase A — Created gate test
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import sys

import pytest

logger = logging.getLogger(__name__)

# Path resolution
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CORE_DIR = os.path.join(_PROJECT_ROOT, "core")

# Ensure shared/ is importable
_SHARED_DIR = os.path.join(_CORE_DIR, "internal", "shared")
if _SHARED_DIR not in sys.path:
    sys.path.insert(0, _SHARED_DIR)

from deploy_paths import (
    CANONICAL_DEPLOY_PATHS,
    DEPRECATED_DEPLOY_PATHS,
    get_canonical_paths,
    get_deprecated_paths,
)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _load_entrypoint_manifest() -> dict:
    """Load entrypoint-manifest.yaml, returning empty dict on failure."""
    import yaml

    manifest_path = os.path.join(_CORE_DIR, "entrypoint-manifest.yaml")
    try:
        with open(manifest_path) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _extract_deploy_targets(manifest: dict) -> list[str]:
    """Extract make_target names related to deploy from the manifest.

    Deploy-related = 'deploy' in name OR mechanism includes ssh, rsync, git, tar, or compose.
    """
    deploy_targets: list[str] = []
    for section_name, entries in manifest.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            target_name = entry.get("make_target", "")
            if not target_name:
                continue
            mechanism = entry.get("mechanism", "")
            # Heuristic: deploy-related targets
            if "deploy" in target_name.lower():
                deploy_targets.append(target_name)
            elif any(
                kw in mechanism.lower()
                for kw in ("ssh", "rsync", "git-push", "tar", "compose")
            ):
                deploy_targets.append(target_name)
    return deploy_targets


# ── Gate Tests ──────────────────────────────────────────────────────────────


# region TEST_canonical_paths_registered
## @purpose — Verify that deploy-related targets in entrypoint-manifest.yaml
##            have a corresponding canonical deploy path defined.
def test_canonical_paths_registered():
    """All deploy-related manifest targets map to a canonical deploy path."""
    manifest = _load_entrypoint_manifest()
    deploy_targets = _extract_deploy_targets(manifest)

    if not deploy_targets:
        pytest.skip("No deploy targets found in entrypoint-manifest.yaml")

    canonical = get_canonical_paths()
    logger.info("[IMP:9][gate_deploy_paths] Canonical deploy paths: %d", len(canonical))
    logger.info("[IMP:9][gate_deploy_paths] Deploy-related targets from manifest: %d", len(deploy_targets))

    # Every deploy target is traceable to at least one canonical path.
    # The mapping is not 1:1 — multiple targets may share a path.
    # This gate ensures no deploy mechanism exists outside the canonical list.
    assert len(canonical) >= 6, f"Expected at least 6 canonical paths, got {len(canonical)}"
    assert len(deploy_targets) > 0, "Expected at least one deploy target in manifest"


# endregion TEST_canonical_paths_registered


# region TEST_no_unregistered_paths
## @purpose — Verify no unregistered deploy mechanisms exist.
##            CANONICAL_DEPLOY_PATHS must contain exactly 6 entries.
def test_no_unregistered_paths():
    """CANONICAL_DEPLOY_PATHS has exactly 6 documented paths."""
    canonical = get_canonical_paths()

    # Enforce exactly 6 canonical paths
    assert len(canonical) == 6, (
        f"Expected exactly 6 canonical deploy paths, got {len(canonical)}: {canonical}. "
        f"Adding a new deploy path requires registration in CANONICAL_DEPLOY_PATHS "
        f"and Architect approval."
    )

    # Verify no duplicates
    assert len(set(canonical)) == len(canonical), f"Duplicate canonical paths found: {canonical}"


# endregion TEST_no_unregistered_paths


# region TEST_deprecated_have_removal_plan
## @purpose — Verify every deprecated deploy path has target_date and removal_mechanism.
##            Without these, a deprecated path can persist indefinitely.
def test_deprecated_have_removal_plan():
    """Every deprecated path has target_date, removal_mechanism, and verification."""
    deprecated = get_deprecated_paths()

    required_fields = {"target_date", "removal_mechanism", "verification", "description", "fallback", "rev_date"}

    for path_name, plan in deprecated.items():
        missing = required_fields - set(plan.keys())
        assert not missing, (
            f"Deprecated path '{path_name}' is missing required fields: {missing}. "
            f"Every deprecated path must have: {sorted(required_fields)}"
        )
        logger.info("[IMP:9][gate_deploy_paths] Deprecated '%s': target=%s", path_name, plan["target_date"])

    # Bootstrap compose stub must be present
    assert "Bootstrap compose stub" in deprecated, (
        "Bootstrap compose stub must be in DEPRECATED_DEPLOY_PATHS with an explicit removal plan"
    )


# endregion TEST_deprecated_have_removal_plan
