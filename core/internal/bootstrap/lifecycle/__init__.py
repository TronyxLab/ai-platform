# GREP_SUMMARY: lifecycle-package, state-machine, bootstrap, python-decomposition
"""
lifecycle/ — Python decomposition package for node-lifecycle.sh (W4-E2).

Replaces node-lifecycle.sh state-machine logic with typed Python modules.
Each step transition is a typed function with pre/post conditions.

Modules:
  - state_machine.py  — State machine with 17 step_* + 6 update_step_* transitions
  - steps.py          — Step implementation functions (pre/post + subprocess)

# STRUCTURE: ┌state.json┐ → ◇ step_start/done/skip/fail → ⊕ hash-compare → ⎋ checkpoint-resume
"""
