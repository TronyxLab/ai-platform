# GREP_SUMMARY: gate fallback-secrets-sync _FALLBACK_SECRETS secret-definitions.yaml sync drift prevention
# STRUCTURE: ◇ test_fallback_secrets_match_definitions → ○ load _FALLBACK_SECRETS → ○ load definitions → ⊕ compare names → ⎋ pass|fail

# region MODULE_CONTRACT
## @purpose  Gate test: verify that _FALLBACK_SECRETS in secrets_manager.py stays
##           in sync with secret-definitions.yaml. Both lists must contain the same
##           autogen secret names. Drift would mean bootstrap generates stale secrets.
## @scope    Reads secrets_manager module directly (no subprocess), reads
##           core/secret-definitions.yaml. Pure static analysis — no Docker daemon.
## @invariants
##   - _FALLBACK_SECRETS names ⊆ secret-definitions.yaml names (for autogen secrets)
##   - Failure means bootstrap would generate secrets not in definitions or vice versa
##   - @pytest.mark.gate — registered in CI gate suite
## @rationale DevPlan 078 T6: _FALLBACK_SECRETS is the hardcoded fallback used when
##            manifest is unavailable during bootstrap. If it diverges from definitions,
##            secrets-init may produce inconsistent state.
## @changes  CREATED: 2026-07-25 | DevPlan 078 Phase A T6
# endregion MODULE_CONTRACT

import logging
import os
import sys

import pytest
import yaml

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SECRET_DEFINITIONS_PATH = os.path.join(ROOT_DIR, "core", "secret-definitions.yaml")


# Helper: import _FALLBACK_SECRETS from secrets_manager
def _get_fallback_secret_names() -> list[str]:
    """Import _FALLBACK_SECRETS from secrets_manager and return secret names."""
    # Ensure the secrets_manager module can be found
    sm_path = os.path.join(ROOT_DIR, "core", "internal", "bootstrap", "lifecycle")
    if sm_path not in sys.path:
        sys.path.insert(0, sm_path)

    from secrets_manager import _FALLBACK_SECRETS  # type: ignore[import-untyped]

    return [s["name"] for s in _FALLBACK_SECRETS]


def _get_definition_autogen_names() -> list[str]:
    """Read secret-definitions.yaml and return names of autogen/generated secrets."""
    logger.info("[IMP:7][fallback_sync] Loading definitions: %s", SECRET_DEFINITIONS_PATH)
    with open(SECRET_DEFINITIONS_PATH) as f:
        defs = yaml.safe_load(f)

    autogen_names: list[str] = []
    for secret in defs.get("secrets", []):
        tier = secret.get("tier", "")
        source = secret.get("source", "")
        # autogen and generated secrets are the ones that appear in _FALLBACK_SECRETS
        if source == "autogen" or tier == "generated":
            autogen_names.append(secret["name"])
    logger.info("[IMP:8][fallback_sync] Found %d autogen/generated definitions", len(autogen_names))
    return sorted(autogen_names)


def _get_definition_ci_default_names() -> list[str]:
    """Read secret-definitions.yaml and return names of secrets WITH ci_default."""
    logger.info("[IMP:7][fallback_sync] Loading definitions for ci_default: %s", SECRET_DEFINITIONS_PATH)
    with open(SECRET_DEFINITIONS_PATH) as f:
        defs = yaml.safe_load(f)

    return sorted(
        secret["name"]
        for secret in defs.get("secrets", [])
        if secret.get("ci_default")
    )


# ── Tests ──────────────────────────────────────────────────────────────────────

# region FUNC_test_fallback_secrets_match_definitions
## @purpose — Verify _FALLBACK_SECRETS names are subset of secret-definitions.yaml
##            generated/autogen secret names. If a fallback secret has no definition,
##            bootstrap would generate a secret unknown to the platform.
## @io — ⇥ caplog → ⎋ None (pytest.fail on mismatch)
## @complexity — O(N log N) — set comparison of two lists
## @invariants
##   - Each _FALLBACK_SECRETS name must appear in definitions (as autogen or generated)
##   - Additional definitions not in fallback are allowed (non-fallback secrets)
##   - @pytest.mark.gate, static check — no CI skip needed


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-25 · REGRESSION · _FALLBACK_SECRETS ÷ definitions drift
# · Last fail: N/A (new gate)
# · Remove if: _FALLBACK_SECRETS is removed and replaced by dynamic manifest-only
def test_fallback_secrets_match_definitions(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """_FALLBACK_SECRETS must be in sync with secret-definitions.yaml."""
    # region BLOCK_Load
    logger.info("[IMP:7][fallback_sync] Loading _FALLBACK_SECRETS from secrets_manager")
    fallback_names = sorted(_get_fallback_secret_names())
    logger.info("[IMP:8][fallback_sync] _FALLBACK_SECRETS names: %s", fallback_names)

    def_names = _get_definition_autogen_names()
    logger.info("[IMP:8][fallback_sync] Definition autogen names: %s", def_names)
    # endregion

    # region BLOCK_Check
    fallback_set = set(fallback_names)
    def_set = set(def_names)

    missing_in_defs = fallback_set - def_set
    logger.info("[IMP:8][fallback_sync] Fallback entries missing from definitions: %s", missing_in_defs)
    # endregion

    # region BLOCK_Assert
    if missing_in_defs:
        logger.error(
            "[IMP:9][fallback_sync] %d _FALLBACK_SECRETS missing from secret-definitions.yaml: %s",
            len(missing_in_defs),
            sorted(missing_in_defs),
        )
        pytest.fail(
            f"_FALLBACK_SECRETS has {len(missing_in_defs)} secret(s) not in secret-definitions.yaml:\n"
            + "\n".join(f"  - {n}" for n in sorted(missing_in_defs))
            + "\nAdd them to secret-definitions.yaml or remove from _FALLBACK_SECRETS."
        )

    logger.info(
        "[IMP:9][fallback_sync] ✅ All %d _FALLBACK_SECRETS matched in definitions",
        len(fallback_names),
    )
    # endregion


# endregion FUNC_test_fallback_secrets_match_definitions
