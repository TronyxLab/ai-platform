# region MODULE_CONTRACT
## @purpose  LDD Telemetry in Tests — caplog trajectory output, IMP:7-10 log filtering, Anti-Illusion Rule enforcement, Semantic Trace Verification
## @scope    architect, coder, qa, docs
## @invariants
##   - @protected  true
##   - Контент — 1:1 перенос framework-source 0.6.3 (только XML-обёртка → markdown)
# endregion MODULE_CONTRACT

<!-- @protect: Agents will silently accept false-positive test passes — Anti-Illusion Rule violated (100% PASS without IMP:9-10 == FAIL). -->
<!-- doc-only, not driver: @requires TESTING section from constitution.xml -->

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

<!-- ai-instructions:0.7.0 -->
