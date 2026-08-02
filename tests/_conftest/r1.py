# GREP_SUMMARY: r1-delegates, exemption-decorator, per-function-assert, R1-detector, test-honesty
# STRUCTURE: ┌r1_delegates(fn)┐ → ◇ passthrough (no-op runtime) → ⎋ decorator marks documented delegation
# region MODULE_CONTRACT
## @purpose  Exemption decorator for the R1 per-function detector (test_gate_r1_no_pass_tests.py):
##           marks a test function whose falsifiability is DELEGATED to a helper/fixture call
##           that raises on failure (e.g. `module_graph` fixture raises RuntimeError on cycle,
##           `state.precondition_check` raises on precondition violation).
## @scope    Used ONLY for tests where the assertion mechanism lives in a called helper/fixture —
##           not for tests that merely log a PASS with no fail mechanism at all (those are RED).
## @invariants
##   - No-op at runtime: returns the function unchanged (decoration is documentation + AST signal)
##   - The R1 detector recognizes the decorator by name and skips the function body scan
##   - MISUSE (decorating a genuine pass-test) is a RED gate violation — the decorator name
##     must be accompanied by a TRAP[TEST] comment explaining the delegation
## @rationale DevPlan 118 F1: per-function R1 scan (AST assert in function body) creates false
##   positives on delegating tests whose fail mechanism is a helper/fixture raise. The sanctioned
##   escape hatch is a named decorator (AC-F1: "исключения через декораторы").
## @changes  2026-08-02 | Created (DevPlan 118 F1)
# endregion MODULE_CONTRACT


def r1_delegates(func):
    """Mark a test function as delegating its fail mechanism to a helper/fixture call.

    ## @purpose  Document + exempt: the test's falsifiability is provided by a raised exception
    ##            in called code (helper/fixture), not by an in-body assert.
    ## @io        ⇥ func: callable → ⎋ callable (unchanged)
    ## @complexity O(1) — identity passthrough
    ## @invariants
    ##   - Pure marker: no behavior change
    ##   - R1 detector scans decorator names; 'r1_delegates' exempts the function
    """
    return func
