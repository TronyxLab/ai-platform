# region MODULE_CONTRACT
## @purpose  Fail-Fast Principle — validate inputs/state before output at compiler, code, document, test, and runtime levels; immediate termination on unrecoverable errors
## @scope    architect, coder, qa, sysadmin, docs
## @invariants
##   - @protected  true
##   - Контент — 1:1 перенос framework-source 0.6.3 (только XML-обёртка → markdown)
# endregion MODULE_CONTRACT

<!-- doc-only, not driver: @requires BEHAVIOR section from constitution.xml -->

    **Fail-Fast Principle**

    Validate inputs and state BEFORE producing output. Never write artifacts that are semantically invalid.

    **Compiler-level:** Validation of REQUIRED_SECTIONS happens before any file is written. Missing sections cause immediate termination with error.

    **Code-level:** Validate function inputs at entry. Reject invalid state early with clear error messages.

    **Document-level:** Validate document structure ($DOCUMENT_PLAN completeness, section tag pairing) before expanding sections.

    **Test-level:** Assert preconditions before test logic. Fail immediately on first assertion violation with descriptive message.

    **Runtime-level:** Log critical errors at IMP:10 with full local context. Exit with non-zero code on unrecoverable errors.

    **Batch-level:** After batch mutations (replaceAll, multi-file refactoring), validate with a verification grep. Never assume batch operations succeeded uniformly — non-standard formatting variants may be silently skipped.

<!-- ai-instructions:0.7.1 -->
