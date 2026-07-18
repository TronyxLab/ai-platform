# GREP_SUMMARY: test-gate template-drift check-all resolvability dry-run ci-gate
# STRUCTURE: ┌manifest.yaml┐ → ◇ check_all() → ⊕ assert ok=True → ⎋ diagnostics on fail
# region MODULE_CONTRACT
## @purpose  Gate: verify all templates in template-manifest.yaml render without unresolved placeholders
## @scope    Dry-run check via template_engine.check_all() — no files written
## @invariants
##   - Uses template_engine.check_all() for in-memory dry-run rendering
##   - Standard vars not available in CI use defaults from manifest
##   - Dual-role files (alert-rules.yml) have Prometheus {{ $labels.x }} syntax — strict grammar does NOT match these
## @rationale Ensures templates don't drift from their variable contracts
## @usecases make gate MODE=fast runs this automatically via @pytest.mark.gate
# endregion MODULE_CONTRACT

import logging
import os

import pytest
from conftest import ldd_trajectory

PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.mark.gate
@ldd_trajectory
# region FUNC_test_all_templates_resolvable
## @purpose — Every template renders without unresolved placeholders
## @io — ⎋ PASS/FAIL with diagnostics per unresolved file
## @complexity O(t * n) where t = templates, n = avg file size
## 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Template drift prevention
## · Last fail: N/A (preventive)
## · Remove if: template rendering is replaced by a different mechanism
def test_all_templates_resolvable(caplog):
    """Verify all templates in template-manifest.yaml render without unresolved placeholders."""
    logger = logging.getLogger(__name__)

    try:
        from core.internal.template_engine import check_all
    except ImportError as e:
        pytest.skip(f"template_engine not available: {e}")

    manifest_path = os.path.join(PROJECT_ROOT, "core", "templates", "template-manifest.yaml")
    if not os.path.exists(manifest_path):
        logger.critical("[IMP:9][gate][template-drift] Manifest not found")
        pytest.fail(f"template-manifest.yaml not found at {manifest_path}")

    logger.log(7, "[IMP:7][gate][template-drift] Checking manifest: %s", manifest_path)

    try:
        ok, diagnostics = check_all(manifest_path)
    except Exception as e:
        logger.critical("[IMP:9][gate][template-drift] check_all raised: %s", e)
        pytest.fail(f"check_all failed: {e}")

    # Print diagnostics
    for diag in diagnostics:
        if diag.startswith("OK:"):
            logger.log(8, "[IMP:8][gate][template-drift] %s", diag)
        else:
            logger.log(9, "[IMP:9][gate][template-drift] %s", diag)

    if not ok:
        unresolved = [d for d in diagnostics if d.startswith("UNRESOLVED:")]
        error_msg = "\n".join(unresolved)
        logger.critical("[IMP:9][gate][template-drift] %d template(s) have unresolved placeholders", len(unresolved))
        pytest.fail(f"Unresolved template placeholders:\n{error_msg}")

    logger.critical("[IMP:9][gate][template-drift] All templates resolvable")


# endregion
