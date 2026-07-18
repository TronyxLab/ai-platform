# GREP_SUMMARY: gate cross-layer-linter import-layer-isolation modules-internal entrypoint-manifest module-makefile-contract data-flow extended-registry shellcheck
# STRUCTURE: ▶ import lint_core() from test_cross_layer_imports → ▶ call lint_core() → ◇ violations==0 → ⊕ PASS/FAIL
# region MODULE_CONTRACT
## @purpose  CI gate #8: cross-layer import linter — enforces layer isolation rules from core/AGENTS.md
## @scope    Thin pytest wrapper around test_cross_layer_imports.lint_core(); runs as part of `make gate` / `make gate MODE=fast`
##           Enhanced with Extended Variable Registry (auto-collect from paths.sh),
##           local variable tracking (_trace_variable_assignment), and ShellCheck data-flow analysis.
## @invariants
##   - Imports lint_core() from tests/test_cross_layer_imports.py
##   - Function decorated with @pytest.mark.gate
##   - FAIL if any cross-layer violations exist
##   - Reads the same allowlists as the full linter test
##   - ShellCheck integration: graceful degradation if ShellCheck not installed
##   - Extended Registry: auto-collected from paths.sh at import time
## @rationale  Integrates cross-layer linting as a CI gate (gate #8) so violations
##             are caught early in `make gate MODE=fast` before reaching production.
## @changes   2026-07-18 | DataFlow DevPlan: Extended Registry + ShellCheck + make -C/docker compose -f patterns
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

# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate invariant — typed contract enforcement
# · Last fail: N/A (preventive)
# · Remove if: entire gate category is superseded by a newer mechanism
def test_gate_cross_layer(caplog) -> None:
    """CI gate #8 v2: enforce cross-layer typed contract.

    v2 changes:
    - internal/ → modules/ is ALLOWED through typed contract (invoke_module_interface)
    - Phase 1: direct module calls from internal/ WITHOUT invoke_module_interface → violation
    - Phase 2: invoke_module_interface with unregistered interface in module.yaml → violation

    ## @purpose — Wraps test_cross_layer_imports.lint_core() as a pytest gate.
    ##            v2: enforces typed contract instead of blanket prohibition.
    ## @io — ⎋ None (assert side-effect via pytest.fail on violations)
    ## @complexity — O(n) where n = source files under core/
    ## @usecases  CI gate: cross-layer-linter
    ## @invariants
    ##   - lint_core() returns sorted list of violation strings
    ##   - Empty list → PASS
    ##   - Non-empty list → FAIL with full violation report
    ##   - internal→modules via invoke_module_interface + interfaces registration is OK
    ##   - internal→modules via direct bash/source/.. without invoke is violation
    ##   - invoke_module_interface with unregistered interface is violation
    """
    # region FUNC_setup
    logger.info("[IMP:8][gate-cross-layer] Running cross-layer import linter (gate #8 v2 — typed contract)")
    # endregion FUNC_setup

    # region FUNC_lint
    violations = lint_core()
    # endregion FUNC_lint

    # region FUNC_report
    print("\n" + "=" * 70)
    print("  GATE #8 v2: CROSS-LAYER TYPED CONTRACT ENFORCEMENT")
    print("=" * 70)

    if not violations:
        print("  ✅ 0 violations — all cross-layer calls use typed contract\n")
        logger.info("[IMP:9][gate-cross-layer] PASS — 0 cross-layer violations")
    else:
        print(f"  ❌ {len(violations)} cross-layer violation(s) found:\n")
        for v in violations:
            print(v)
        print("\n" + "-" * 70)
        print("  LEGEND:")
        print("    [internal→modules·direct]  — direct bash/source/. call without invoke_module_interface")
        print("    [internal→modules·invoke]  — invoke_module_interface with unregistered interface")
        print("    [layer→layer]              — classic cross-layer import violation")
        logger.info("[IMP:9][gate-cross-layer] FAIL — %d violation(s)", len(violations))
    print("=" * 70 + "\n")

    # endregion FUNC_report

    # region FUNC_assert
    assert len(violations) == 0, f"Gate #8 v2 FAILED: {len(violations)} cross-layer violation(s):\n" + "\n".join(
        violations
    )
    # endregion FUNC_assert


# endregion TEST_GATE_CROSS_LAYER
