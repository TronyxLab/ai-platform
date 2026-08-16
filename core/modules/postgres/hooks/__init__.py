# GREP_SUMMARY: postgres-hooks package on_project_deploy importable
# STRUCTURE: ┌__init__.py┐ → ⎋ makes core/modules/postgres/hooks a package (F5, DevPlan 118)
# region MODULE_CONTRACT
## @purpose  Package marker for core/modules/postgres/hooks/ — enables the canonical
##           dotted import `from core.modules.postgres.hooks import on_project_deploy`
##           from any CWD (DevPlan 118 F5). Without this marker the hooks dir was an
##           implicit namespace package imported via sys.path.insert in tests — fragile
##           on VPS contexts (PYTHONPATH), worked only from a specific CWD.
## @scope    Empty package init; on_project_deploy.py is the only member.
## @invariants
##   - on_project_deploy.py remains invocable as a standalone script (`python3 .../on_project_deploy.py`)
##     — its own sys.path bootstrap (parents[4] → repo root) is unchanged
##   - Tests import via core.modules.postgres.hooks.on_project_deploy (no sys.path hack)
## @rationale DevPlan 118 F5: replace `sys.path.insert(0, hooks_dir)` in tests with a
##   proper package structure — import resolution no longer depends on process CWD.
## @changes  2026-08-02 | Created (DevPlan 118 F5)
# endregion MODULE_CONTRACT
