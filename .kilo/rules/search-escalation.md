# region MODULE_CONTRACT
## @purpose  Meta-rules for web search: when to consider it, when to skip it, and that the user has the final say. Injected into all roles.
## @scope    architect, coder, qa, sysadmin
## @invariants
##   - @protected  true
##   - Контент — 1:1 перенос framework-source 0.6.3 (только XML-обёртка → markdown)
# endregion MODULE_CONTRACT

<!-- @protect: Core escalation protocol — defines when and how agents use web search. Removing this silences internet access across all roles. -->

    **§SEARCH_ESCALATION — web search is a tool of last resort, user-confirmed only.**

    1. **LOCAL first:** grep → read → TRAP database → internal reasoning. Skip search entirely
       if the answer is local (codebase, docs, DevPlan, TRAPs, prior messages) or internal
       (business logic, deployment configs — the web won't know).
    2. **USER GATE:** only when the answer is genuinely absent (knowledge gap, external dependency)
       → `question` tool: what was tried locally + what will be searched. User decides; if denied,
       find an alternative path.
    3. **LIMITS:** max 2 `websearch` queries, max 2 `webfetch` calls; queries must be specific
       (exact error text, library name, version); prefer official docs over blogs, source over tutorials.

<!-- ai-instructions:0.7.1 -->
