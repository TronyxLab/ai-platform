# region MODULE_CONTRACT
## @purpose  Test Honesty Rules — prevents degradation of test suites into unfalsifiable pass-collections
## @scope    coder, qa
## @invariants
##   - @protected  true
##   - Контент — 1:1 перенос framework-source 0.6.3 (только XML-обёртка → markdown)
# endregion MODULE_CONTRACT

<!-- @protect: Test honesty is the last line of defense against false confidence. Without these rules, test suites degrade into pass-collections that prove nothing. -->

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

<!-- ai-instructions:0.7.1 -->
