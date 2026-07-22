# GREP_SUMMARY: converge-package, reconciler, desired-state, python-decomposition
"""
converge/ — Python decomposition package for converge.sh (W4-E3).

Replaces converge.sh reconcile logic with typed Python reconciler.py.

Modules:
  - reconciler.py  — 6 reconcile_* methods + JSON report output

# STRUCTURE: ┌reconciler: R1-R6 reconcile methods┐ → ⊕ aggregate exit_code → ⎋ JSON report
"""
