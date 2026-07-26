#!/usr/bin/env python3
# GREP_SUMMARY: deploy-paths, canonical, deprecated, registry, bootstrap-compose-stub, removal-plan
# STRUCTURE: ▶ CANONICAL_DEPLOY_PATHS (6 paths) → ◇ DEPRECATED_DEPLOY_PATHS (1 stub) → ⊕ function get_canonical_paths() → ⎋
# region MODULE_CONTRACT
## @purpose  Canonical deploy path registry — single source of truth for all code delivery
##           mechanisms. Gate test validates every deploy path in entrypoint-manifest.yaml
##           is registered here. DEPRECATED_DEPLOY_PATHS includes explicit removal plans.
## @scope    Read by tests/gates/test_gate_deploy_paths.py for CI enforcement.
##           No runtime dependencies — pure data module.
## @invariants
##   1. Every deploy-related make_target in entrypoint-manifest.yaml must map to a
##      canonical path defined here. Adding a new deploy mechanism without registering
##      it in CANONICAL_DEPLOY_PATHS blocks CI merge.
##   2. Every DEPRECATED entry must have target_date, removal_mechanism, and verification.
##   3. This module has zero imports from other shared modules — it is Phase A independent.
## @rationale DRIFT-D1 (Brief 077): deploy pipelines are the most critical production domain,
##           yet there was no canonical inventory of deploy paths. CI gate enforcement
##           prevents accidental divergence between manifest, code, and documentation.
## @changes  2026-07-26 | DevPlan 081 Phase A — Created deploy path registry
# endregion MODULE_CONTRACT

from __future__ import annotations

# ── Canonical Deploy Paths ──────────────────────────────────────────────────
# These are the 6 documented code delivery mechanisms. Every deploy-related
# make_target in entrypoint-manifest.yaml must be traceable to one of these paths.

CANONICAL_DEPLOY_PATHS: list[str] = [
    # 1. CI → platform-deliver + deploy.sh
    #    git push → GitHub CI → tar via SSH forced-command → deploy-project.sh
    "CI → platform-deliver + deploy.sh",
    # 2. make deploy-project (direct)
    #    tar + SSH, bypass CI, emergency fallback with DEPLOY-DIRECT audit
    "make deploy-project (direct)",
    # 3. context_deployer.py (Python)
    #    ghcr.io pull (primary) + build-on-node fallback, idempotent health-gate
    "context_deployer.py (Python)",
    # 4. deploy-modules.sh (system modules)
    #    docker compose up for system modules (install.sh path)
    "deploy-modules.sh (system modules)",
    # 5. Core SCP/rsync
    #    CI workflow core-deploy → SCP/rsync core/ to VPS /opt/platform/core/
    "Core SCP/rsync",
    # 6. Context-overlay git
    #    git clone/pull via ensure_context_repo() on VPS
    "Context-overlay git",
]

# ── Deprecated Deploy Paths ────────────────────────────────────────────────
# Each entry must have a removal plan with target_date, removal_mechanism,
# verification, fallback, and rev_date. Gate test enforces this structure.

DEPRECATED_DEPLOY_PATHS: dict[str, dict[str, str]] = {
    "Bootstrap compose stub": {
        "description": (
            "Temporary nginx:alpine container generated during node bootstrap, "
            "replaced by first real project deployment via "
            "context_deployer._deploy_single_project()"
        ),
        "removal_mechanism": ("docker compose up -d на реальный проект заменяет заглушку автоматически"),
        "verification": "docker compose ps --format '{{.Image}}' | grep -c 'nginx:alpine' returns 0",
        "target_date": "2026-08-15",
        "fallback": "docker compose down nginx-stub && docker rm nginx-stub",
        "rev_date": "2026-09-01",
    },
}


# region FUNC_get_canonical_paths
## @purpose — Return immutable list of canonical deploy paths.
## @io — ⇥ None → ⎋ list[str] (6 paths)
## @complexity — O(1)
def get_canonical_paths() -> list[str]:
    """Return the list of canonical deploy paths."""
    return list(CANONICAL_DEPLOY_PATHS)


# endregion FUNC_get_canonical_paths


# region FUNC_get_deprecated_paths
## @purpose — Return immutable dict of deprecated deploy paths with removal plans.
## @io — ⇥ None → ⎋ dict[str, dict[str, str]]
## @complexity — O(1)
def get_deprecated_paths() -> dict[str, dict[str, str]]:
    """Return the dict of deprecated deploy paths with removal plans."""
    return dict(DEPRECATED_DEPLOY_PATHS)


# endregion FUNC_get_deprecated_paths
