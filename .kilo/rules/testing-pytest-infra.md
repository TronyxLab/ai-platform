# region MODULE_CONTRACT
## @purpose  pytest Testing Infrastructure — tmp_path fixture, zero hardcode, native imports, headless UI testing, plugin architecture tests, DI over mocks, invariant testing
## @scope    architect, coder, qa, docs
## @invariants
##   - @protected  true
##   - Контент — 1:1 перенос framework-source 0.6.3 (только XML-обёртка → markdown)
# endregion MODULE_CONTRACT

<!-- doc-only, not driver: @requires TESTING section from constitution.xml -->

    **pytest Testing Infrastructure**

    All tests MUST reside in a single root tests/ directory at the project level. Test files named accordingly (e.g., tests/test_module.py).

    **Core rules:**
    - Use native imports only. STRICTLY FORBIDDEN: subprocess.run for business logic testing
    - Zero Hardcode Rule: never use hardcoded paths or sys.path.append. Always use tmp_path fixture
    - Backend tests call functions directly (Native Pytest)
    - Test Atomicity: create atomic tests for individual functional elements
    - Integration Test: include a full-scenario pass test

    **UI Testing (Headless):**
    - Emulate controller calls without starting the server
    - Call handler functions directly with test arguments
    - Verify return types (DataFrame, Plotly Figure, etc.)
    - FORBIDDEN: launching servers inside tests, browser emulators

    **Plugin Architecture Tests:**
    - Read-Only vs Ephemeral Data: reference files in tests/test_data/, isolated DBs via plugin calls
    - Dependency Injection (DI) > Mocks: avoid unittest.mock.patch for internal state, pass paths explicitly
    - Invariant Testing (ETL): verify logical invariants
    - SWE Heuristics: isolate parsing logic, test with static Data-Driven Fixtures

<!-- ai-instructions:0.7.1 -->
