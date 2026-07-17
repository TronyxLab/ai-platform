# GREP_SUMMARY: tools tests package sync-inventory helper __init__ marker
# STRUCTURE: Package marker for tests/tools/ — inventory regeneration helper(s)
# region MODULE_CONTRACT
## @purpose  Package init for the tests/tools/ helper package (sync_inventory.py).
## @scope    Import surface for test-support tooling invoked by `make test-inventory-sync`
##           and consumed natively by tests/unit/test_sync_inventory.py.
## @invariants
##   - Contains no runtime logic — pure package marker
##   - Every module under tests/tools/ carries full §MARKUP (GREP_SUMMARY, MODULE_CONTRACT, STRUCTURE)
## @rationale  tests/tools is imported as a package so unit tests can call the collection
##             logic directly (native imports) instead of via subprocess.
# endregion MODULE_CONTRACT
