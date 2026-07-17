# GREP_SUMMARY: gate cross-layer-linter import-layer-isolation modules-internal entrypoint-manifest module-makefile-contract
# STRUCTURE: ▶ import lint_core() from test_cross_layer_imports → ▶ call lint_core() → ◇ violations==0 → ⊕ PASS/FAIL
# region MODULE_CONTRACT
## @purpose  CI gate #8: cross-layer import linter — enforces layer isolation rules from core/AGENTS.md
## @scope    Thin pytest wrapper around test_cross_layer_imports.lint_core(); runs as part of `make gate` / `make gate MODE=fast`
## @invariants
##   - Imports lint_core() from tests/test_cross_layer_imports.py
##   - Function decorated with @pytest.mark.gate
##   - FAIL if any cross-layer violations exist
##   - Reads the same allowlists as the full linter test
## @rationale  Integrates cross-layer linting as a CI gate (gate #8) so violations
##             are caught early in `make gate MODE=fast` before reaching production.
##             Created per TASK-6F Phase 6.
## @usecases  CI gate: cross-layer-linter; pre-commit validation
# endregion MODULE_CONTRACT

import logging

import pytest

logger = logging.getLogger(__name__)


from tests.conftest import ldd_trajectory
from tests.test_cross_layer_imports import lint_core

# region TEST_GATE_CROSS_LAYER


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-09 · gate/cross-layer-linter · wrapper around lint_core()
def test_gate_cross_layer(caplog) -> None:
    """CI gate #8: enforce zero cross-layer import violations in core/.

    ## @purpose — Wraps test_cross_layer_imports.lint_core() as a pytest gate.
    ##            FAIL with readable message if violations are found.
    ## @io — ⎋ None (assert side-effect via pytest.fail on violations)
    ## @complexity — O(n) where n = source files under core/
    ## @usecases  CI gate: cross-layer-linter
    ## @invariants
    ##   - lint_core() returns sorted list of violation strings
    ##   - Empty list → PASS
    ##   - Non-empty list → FAIL with full violation report
    """
    # region FUNC_setup
    logger.info("[IMP:8][gate-cross-layer] Running cross-layer import linter (gate #8)")
    # endregion FUNC_setup

    # region FUNC_lint
    violations = lint_core()
    # endregion FUNC_lint

    # region FUNC_report
    print("\n" + "=" * 70)
    print("  GATE #8: CROSS-LAYER IMPORT LINTER")
    print("=" * 70)

    if not violations:
        print("  ✅ 0 violations — all imports respect layer isolation rules\n")
        logger.info("[IMP:9][gate-cross-layer] PASS — 0 cross-layer import violations")
    else:
        print(f"  ❌ {len(violations)} cross-layer import violation(s) found:\n")
        for v in violations:
            print(v)
        print("\n" + "-" * 70)
        logger.info("[IMP:9][gate-cross-layer] FAIL — %d violation(s)", len(violations))
    print("=" * 70 + "\n")

    # endregion FUNC_report

    # region FUNC_assert
    assert len(violations) == 0, f"Gate #8 FAILED: {len(violations)} cross-layer import violation(s):\n" + "\n".join(
        violations
    )
    # endregion FUNC_assert


# endregion TEST_GATE_CROSS_LAYER
