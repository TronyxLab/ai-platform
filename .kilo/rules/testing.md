# §TESTING
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
**LDD Telemetry in Tests**

    Tests must NOT be silent. Within the LDD methodology, tests are required to include execution log selection and output to console.

    **Python/pytest implementation:**
    - Use caplog fixture to capture logs
    - Filter IMP:7-10 log lines from caplog.records
    - Print filtered logs BEFORE assertions so agent sees trajectory on failure
    - Assert that at least one IMP:9 log is present in successful scenarios

    **Anti-Illusion Rule:** 100% PASSED without reading IMP:9-10 logs is a failure. The true criterion is Semantic Trace Verification — does the actual execution path (logs) match the design (contracts)?

    **Log output pattern:**
    ```python
    found_log = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_log = True
    print("--- END LDD TRAJECTORY ---")
    assert found_log, "Critical LDD Error: No IMP:9 business logic log found"
    ```

    This demonstrates the real execution context and AI Belief State to QA agents, rather than just a successful assert.
**Anti-Loop Protocol**

    Prevent agents from repeating failed strategies infinitely. Implement attempt tracking mechanism in tests.

    **Architecture:**
    - Use tests/conftest.py for session hook logic (pytest_sessionstart, pytest_sessionfinish)
    - Use .test_counter.json to store failed run counts
    - Counter resets to 0 only at 100% PASS
    - FORBIDDEN to call counter management inside individual test files

    **Escalation levels:**

    **Attempt 1-2 (Checklist):** On failure, output a CHECKLIST of common errors:
    - tmp_path not used — hardcoded paths in tests
    - XML fixture content malformed or missing required sections
    - REQUIRED_SECTIONS mismatch
    - caplog level not set — IMP:7-10 logs not captured
    - File not found — framework/ or granules/ dir missing
    - Version stamp format incorrect
    - merge_sections collision logic not handling duplicates
    - Experience Feedback Loop: add new items to CHECKLIST based on encountered errors

    **Attempt 3 (External Help):** "Use MCP tavily or Context 7 to find a solution online."

    **Attempt 4 (Reflection):** "WARNING: Looping risk! Pause and reflect. Are you repeating a failed strategy? Consider alternatives (Superposition)."

    **Attempt 5+ (Escalation):** "CRITICAL ERROR: Agent looping detected. STOP. Formulate a help request for an operator."

    Always run tests via: python -m pytest tests/ -s -v
**Test Honesty Rules**

    Prevent test suite degradation into unfalsifiable pass-collections. A test that cannot fail is not a test.

    **R1: NO pass-tests**
    Test with no assertions, `assert True`, or `try/except` that swallows all failures → RED (blocks merge).
    Detection: test body contains no `assert` OR all asserts are on constants OR try/except with bare pass.

    **R2: NO unfalsifiable asserts**
    Assertion on a language guarantee or unconditional invariant (e.g., `assert isinstance(x, object)`, `assert len(x) >= 0`) → AMBER (warning, degrades quality score).
    Detection: the asserted property is a language guarantee, mathematical identity, or unconditional invariant of the preceding line.

    **R3: STALE SKIP = RED**
    `@pytest.mark.skip` older than 90 days → RED (blocks merge).
    Detection: `git log --follow -1 --format=%aI <test_file>` for the line that added the skip marker.
    Exception: skip with explicit `reason="awaiting dependency X — ETA YYYY-MM-DD"` where the date is in the future.

    **R4: NO_SERVICE = FAIL, not skip**
    Skip with reason matching "no service", "service not available", "connection refused" → FAIL, not skip.
    Environmental absence is a configuration error — surface it, don't hide it.

    **R5: ANTI-SURVIVORSHIP — negative test for every gate**
    For each gate test referencing a bug/issue ID, a corresponding negative test MUST exist using the exact input that triggered the original bug.
    Detection: for each test whose name/docstring references a bug ID, verify a corresponding test exists with the same ID and `_negative` or `_original_form` suffix.
    Violation → gate test is INCOMPLETE (warning, must be addressed before next release).

    **QA Integration:** QA Phase 4 (Test Quality Deep Audit) checks R1-R5. R1/R3/R4 violation → DEGRADED verdict. R2/R5 violations → documented in report, contribute to test health score.

<!-- ai-instructions:0.5.18 -->
