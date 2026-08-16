# GREP_SUMMARY: tools, tests, package, marker
# STRUCTURE: Package marker for tests/tools/
# region MODULE_CONTRACT
## @purpose  Package marker for the tests/tools/ helper package.
## @scope    Test-support tooling (инструменты, НЕ тесты).
## @invariants
##   - Contains no runtime logic — pure package marker
##   - Every module under tests/tools/ carries full §MARKUP
## @rationale  Package import surface for test-support tooling.
# endregion MODULE_CONTRACT
