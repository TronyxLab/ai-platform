# GREP_SUMMARY: lifecycle-package, state-machine, bootstrap, python-decomposition
# STRUCTURE: ┌state.json┐ → ◇ step_start/done/skip/fail → ⊕ hash-compare → ⎋ checkpoint-resume
# region MODULE_CONTRACT
## @purpose  Python decomposition package for node-lifecycle.sh (W4-E2). Replaces shell state-machine
##           logic with typed Python modules. Each step transition is a typed function with pre/post conditions.
## @scope    core/internal/bootstrap/lifecycle/ — state_machine.py (17 + 7 step transitions), steps.py (implementations)
## @invariants
##   - state_machine.py — единственный entrypoint для lifecycle state machine
##   - steps.py содержит только реализации шагов, не оркестрацию
##   - Все шаги должны иметь pre/post-condition проверки
## @rationale  Replace node-lifecycle.sh state-machine logic with typed, testable Python modules.
# endregion MODULE_CONTRACT

"""
Modules:
  - state_machine.py  — State machine with 17 step_* + 6 update_step_* transitions
  - steps.py          — Step implementation functions (pre/post + subprocess)
"""
