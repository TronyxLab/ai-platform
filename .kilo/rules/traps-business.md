<!-- GREP_SUMMARY: business_trap, TRAP, BUSINESS, owner, priority, accent, requirement, decision -->
# region MODULE_CONTRACT
## @purpose  Business Trap (TRAP[BUSINESS]) — captures explicit business priorities and owner accent decisions inline
## @scope    architect
## @invariants
##   - @protected  true
##   - Контент — 1:1 перенос framework-source 0.6.3 (только XML-обёртка → markdown)
# endregion MODULE_CONTRACT

<!-- @protect: Business trap preserves explicit owner accents (reliability > performance) in code for future agent context. -->
<!-- doc-only, not driver: @requires MARKUP section from constitution.xml -->
<!-- §PRINCIPLE: Knowledge locality — Trap is located next to the change site, not in a separate document. grep "TRAP\[" finds all active TRAPs -->

    **Business Trap — TRAP[BUSINESS]**

    When the owner or stakeholder explicitly states a business priority or accent (reliability > performance, duplicates not allowed, audit trail mandatory), add a TRAP[BUSINESS] comment at the relevant architectural decision point. Format (one-line):

    ```
    # 💼 TRAP[BUSINESS] · YYYY-MM-DD · HI · One-liner · Source: owner · Risk: risk-description
    ```

    **Add when:** the owner explicitly stated a priority trade-off, a compliance/regulatory constraint,
    or a stakeholder decision that limits architectural options.
    **Do NOT add for:** requirements already documented in the spec, personal opinions not validated
    with the owner, hypothetical future requirements, undocumented assumptions.

<!-- ai-instructions:0.7.0 -->
