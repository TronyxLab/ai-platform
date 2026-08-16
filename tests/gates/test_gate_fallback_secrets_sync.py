# GREP_SUMMARY: gate fallback-secrets-removed secrets_manager strict-manifest secrets_manifest_reader canonical-import
# STRUCTURE: ◇ test_fallback_secrets_removed → ○ read secrets_manager.py → ◇ _FALLBACK_SECRETS absent? → ◇ canonical shared import present? → ⊕ strict reader (no return []) → ⎋ pass|fail

# region MODULE_CONTRACT
## @purpose  Gate test: verify the hardcoded _FALLBACK_SECRETS list is REMOVED from
##           secrets_manager.py and the module delegates to the canonical shared
##           secrets_manifest_reader (DevPlan 116 T4, U-33). Silent fallback was a
##           drift vector — manifest is always delivered with core/ (fail-visible).
## @scope    Reads secrets_manager.py source (no subprocess). Pure static analysis.
## @invariants
##   - _FALLBACK_SECRETS must NOT exist in secrets_manager.py (removal enforced)
##   - secrets_manager.py must import from core.internal.shared.secrets_manifest_reader
##     (canonical form — gate test_gate_secrets_parser_import covers env parser; this
##     gate covers the manifest reader contract)
##   - _read_manifest must NOT contain a `return []` graceful-degradation fallback
##   - @pytest.mark.gate — registered in CI gate suite
## @rationale DevPlan 116 T4: hardcoded fallback removed — manifest is the single
##            source. This gate prevents re-introduction of the fallback list.
## @changes  REPLACED: 2026-07-31 | was test_fallback_secrets_match_definitions
##           (DevPlan 078 T6) — obsolete: _FALLBACK_SECRETS removed per DevPlan 116 T4
# endregion MODULE_CONTRACT

import logging
import pathlib
import re
from pathlib import Path

import pytest

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
SECRETS_MANAGER_PATH = Path(ROOT_DIR) / "core" / "internal" / "bootstrap" / "lifecycle" / "secrets_manager.py"


# ── Tests ──────────────────────────────────────────────────────────────────────

# region FUNC_test_fallback_secrets_removed
## @purpose — Verify hardcoded fallback list is gone and secrets_manager delegates
##            to the canonical shared secrets_manifest_reader (strict mode).
## @io — ⇥ caplog → ⎋ None (pytest.fail on violation)
## @complexity — O(F) where F = file size
## @invariants
##   - No `_FALLBACK_SECRETS` symbol in secrets_manager.py
##   - Canonical import of secrets_manifest_reader present
##   - No `return []` silent-fallback in _read_manifest


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · fallback list re-introduction (DevPlan 116 T4)
# · Last fail: 2026-07-31 (fallback list removed this wave)
# · Remove if: secrets_manager manifest contract is superseded
def test_fallback_secrets_removed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """_FALLBACK_SECRETS must be removed; secrets_manager uses shared strict reader."""
    with pathlib.Path(SECRETS_MANAGER_PATH).open(encoding="utf-8") as f:
        content = f.read()

    # region BLOCK_Check
    violations: list[str] = []

    if "_FALLBACK_SECRETS" in content:
        violations.append(
            "secrets_manager.py still defines _FALLBACK_SECRETS — hardcoded fallback was removed "
            "(DevPlan 116 T4, U-33). Manifest is always delivered with core/; fallback is a drift vector."
        )

    if "from core.internal.shared.secrets_manifest_reader import" not in content:
        violations.append(
            "secrets_manager.py must import iter_secrets from "
            "core.internal.shared.secrets_manifest_reader (canonical shared module)."
        )

    # Code-level `return []` (graceful degradation) — docstring-упоминания не считаются
    if re.search(r"^\s+return \[\]\s*$", content, re.MULTILINE):
        violations.append(
            "secrets_manager.py contains a code-level `return []` graceful-degradation — "
            "strict mode requires raising on missing/malformed manifest (invariant 7)."
        )
    # endregion BLOCK_Check

    # region BLOCK_Assert
    if violations:
        for v in violations:
            logger.error("[IMP:9][fallback_removed] %s", v)
        pytest.fail(
            "secrets_manager fallback/strictness contract violated:\n" + "\n".join(f"  - {v}" for v in violations)
        )

    logger.info("[IMP:9][fallback_removed] ✅ _FALLBACK_SECRETS removed; secrets_manager uses shared strict reader")
    # endregion BLOCK_Assert


# endregion FUNC_test_fallback_secrets_removed
