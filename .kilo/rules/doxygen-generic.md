# region MODULE_CONTRACT
## @purpose  Universal Inline Documentation Rules — module/function/class contracts, GREP_SUMMARY keyword line, STRUCTURE block diagram, paired region markers, LDD logging, bug fix context
## @scope    architect, coder, qa, docs
## @invariants
##   - @protected  true
##   - Контент — 1:1 перенос framework-source 0.6.3 (только XML-обёртка → markdown)
# endregion MODULE_CONTRACT

<!-- doc-only, not driver: @requires MARKUP section from constitution.xml -->

    **Universal Inline Documentation Rules (Any Language)**

    Regardless of programming language, every source file MUST contain:

    1. **Module contract** describing purpose (why it exists), scope (what it covers), invariants (what always holds), and rationale (why this approach).

    2. **Function/class contracts** describing purpose (goal, not summary), input/output, and complexity.

    3. **GREP_SUMMARY:** A single-line comment with comma-separated keywords for grep-based file discovery by autonomous agents.

    4. **STRUCTURE:** A creative one-line mini block diagram showing algorithm flow using diverse Unicode symbols (▶ ┌┐ ◇ ⊕ ∑ ⟦⟧ ⚡).

    5. **Paired region markers:** Opening and closing markers for module, function, and class boundaries, regardless of whether the language natively supports regions.

    6. **LDD logs:** Structured log format with importance levels (IMP:1-10) and block/function identification, adapted to the language's logging facilities.

    7. **Bug fix context:** When fixing complex bugs, add a comment explaining why the old approach failed and why the new approach was chosen.

    Adapt the specific syntax (// vs # vs -- vs /* */) to the target language while preserving the semantic structure.

<!-- ai-instructions:0.7.1 -->
