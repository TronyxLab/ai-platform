# region MODULE_CONTRACT
## @purpose  Anti-Loop Protocol — attempt tracking via .test_counter.json, escalation levels 1-5, caplog integration, batched verification (make preflight / make test-summary)
## @scope    architect, coder, qa, docs
## @invariants
##   - @protected  true
##   - Контент — 1:1 перенос framework-source 0.6.3 (только XML-обёртка → markdown)
# endregion MODULE_CONTRACT

<!-- @protect: Agents will loop indefinitely on the same failing strategy — escalation levels 1-5 + .test_counter.json prevent infinite retry. -->
<!-- doc-only, not driver: @requires TESTING section from constitution.xml -->

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

    **Batched verification (anti-serial):**
    - Prefer the project's batched verification command — a single invocation that collects ALL failures in one pass (e.g., `make preflight`, `make test-summary`, `npm test` — whichever the project provides).
    - FORBIDDEN: per-file verification loops — "run file A → fix → run file B → fix". One pass collects the full failure set. Exception: targeted per-task verification during implementation (Coder PER_TASK_VERIFY, before the first full gate) is allowed — the ban applies to fix cycles after a full batched run.
    - Fix cycle operates on the full failure set: fix ALL known errors → re-run the batched command once → repeat. Escalation levels apply per cycle, not per file.
    - The full gate runs exactly once, at the end, when the batched command is clean.


<!-- ai-instructions:0.7.0 -->
