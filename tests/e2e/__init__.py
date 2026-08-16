# GREP_SUMMARY: e2e-package, requires-node, bootstrap-pipeline, test-vps, e2e-conftest
# STRUCTURE: ┌package marker┐ → ⎋ (conftest.py auto-loaded by pytest for tests/e2e/)
# region MODULE_CONTRACT
## @purpose  Package marker for tests/e2e/ — DevPlan 095. REQUIRED for pytest to load
##           conftest.py fixtures (requires_node, node_ssh, node_state, test_vps_fresh).
## @scope    E2E bootstrap pipeline tests against a recreatable test-VPS (requires_node marker).
## @invariants
##   - Contains NO tests — only package identity
##   - tests/e2e/conftest.py is auto-discovered because this package exists
## @rationale DevPlan 095 §3: "tests/e2e/__init__.py [CREATE] — package marker (обязателен
##            для загрузки conftest.py)"
# endregion MODULE_CONTRACT
