# GREP_SUMMARY: converge-package, reconciler, desired-state, python-decomposition
# STRUCTURE: ┌reconciler: R1-R9 reconcile methods┐ → ⊕ aggregate exit_code → ⎋ JSON report
# region MODULE_CONTRACT
## @purpose  Python decomposition package for converge.sh (W4-E3). Replaces shell reconcile logic
##           with typed Python reconciler.py.
## @scope    core/internal/bootstrap/converge/ — reconciler.py (9 R-units: perms, audit_log, projects, networks,
##           hosts_drift, vhosts, volumes, sudoers, runtime)
## @invariants
##   - reconciler.py — единственный entrypoint для converge reconciliation
##   - Каждый R-unit автономен и независим от других
##   - JSON report обязателен для всех режимов (--dry-run, --reconcile)
## @rationale  Replace converge.sh reconcile logic with typed, testable Python module.
# endregion MODULE_CONTRACT

"""
Converge reconciliation package.

Modules:
  - reconciler.py  — 9 R-units (R1-R9): perms, audit_log, projects, networks, hosts_drift, vhosts, volumes, sudoers, runtime
"""
