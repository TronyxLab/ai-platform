# GREP_SUMMARY: gates anti-drift CI tests __init__ package
# STRUCTURE: Package marker for tests/gates/ — 123 anti-drift CI gate test files (DevPlan 123 T9: +test_gate_local_path_in_remote)
# region MODULE_CONTRACT
## @purpose  Package init for anti-drift CI gates test suite
## @scope    Tests in tests/gates/ validate: manifest registration, naming conventions,
##           dead code, contract tests, grep-summary headers, manifest parity, compose rules,
##           CI coverage, skip enforcement, test inventory, cross-layer imports, simulators,
##           image naming, linter parity, exception audit
## @invariants
##   - All gate tests are pytest with @pytest.mark.gate
##   - Each gate tests exactly one invariant from entrypoint-manifest.yaml gates: list
##   - Count must match ls tests/gates/test_gate_*.py | wc -l (auto-discovered)
# endregion MODULE_CONTRACT
